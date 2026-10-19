import asyncio
import json
import logging
from contextlib import contextmanager
from types import SimpleNamespace

import app.main as main
from app.generation import BUSY_MESSAGE, ModelError, _gemini_stream


class _Result:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class _Conn:
    def __init__(self, *, fail: bool = False, documents: int = 10, chunks: int = 20):
        self.fail = fail
        self.documents = documents
        self.chunks = chunks
        self.queries: list[str] = []

    def execute(self, sql, _params=None):
        self.queries.append(sql)
        if self.fail:
            raise RuntimeError("postgres://srmap:password@db.internal/srmap_rag")
        if "COUNT(*)" in sql and "documents" in sql:
            return _Result(self.documents)
        if "COUNT(*)" in sql and "chunks" in sql:
            return _Result(self.chunks)
        return _Result(1)


def _connect(conn: _Conn):
    @contextmanager
    def _manager():
        yield conn

    return _manager


def _patch_chat(monkeypatch, generate):
    monkeypatch.setattr(main, "rewrite_query", lambda *_a, **_k: ("rewritten", "fees"))
    monkeypatch.setattr(main, "hybrid_search", lambda *_a, **_k: [])
    monkeypatch.setattr(main, "generate_answer", generate)
    monkeypatch.setattr(main, "get_conn", _connect(_Conn()))


def _ok_generate(message, chunks, **kwargs):
    if kwargs.get("stream"):

        def _pieces():
            yield "Answer about fees."

        return _pieces()
    return "Answer about fees.", []


def _access_payloads(caplog) -> list[dict]:
    payloads = []
    for record in caplog.records:
        if record.name != "app.access":
            continue
        payloads.append(json.loads(record.message))
    return payloads


def test_health_does_not_count_on_every_probe(client, monkeypatch):
    conn = _Conn(documents=3635, chunks=21466)
    monkeypatch.setattr(main, "get_conn", _connect(conn))
    first = client.get("/health")
    second = client.get("/health")
    live = client.get("/health/live")
    assert first.status_code == 200
    assert first.json()["documents"] == 3635
    assert second.json()["chunks"] == 21466
    assert live.json()["status"] == "ok"
    assert live.json()["details"]["probe"] == "live"
    count_queries = [sql for sql in conn.queries if "COUNT(*)" in sql]
    assert len(count_queries) == 2
    assert all("SELECT 1" not in sql for sql in conn.queries)


def test_ready_uses_select_one_and_hides_database_errors(client, monkeypatch):
    conn = _Conn(fail=True)
    monkeypatch.setattr(main, "get_conn", _connect(conn))
    ready = client.get("/health/ready")
    assert ready.status_code == 503
    body = ready.json()
    assert body["status"] == "degraded"
    assert body["details"]["error_category"] == "database"
    assert "password" not in ready.text
    assert any("SELECT 1" in sql for sql in conn.queries)


def test_live_does_not_touch_the_database(client, monkeypatch):
    def explode():
        raise AssertionError("live probe queried the database")

    monkeypatch.setattr(main, "get_conn", explode)
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_rate_limit_and_oversized_requests(client, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.access")
    _patch_chat(monkeypatch, _ok_generate)
    main.limiter.per_minute = 2
    main.limiter.reset()
    secret = "question-should-not-be-logged-xyz"
    payload = {"message": secret, "stream": False}
    assert client.post("/chat", json=payload).status_code == 200
    assert client.post("/chat", json=payload).status_code == 200
    blocked = client.post("/chat", json=payload)
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == "Too many requests. Please wait and try again."

    huge = "x" * 4001
    invalid = client.post("/chat", json={"message": huge, "stream": False})
    assert invalid.status_code == 422
    assert invalid.json()["detail"] == "Request is invalid."
    assert huge not in invalid.text

    history = [{"role": "user", "content": "earlier"} for _ in range(41)]
    too_much_history = client.post("/chat", json={"message": "fee", "history": history, "stream": False})
    assert too_much_history.status_code == 422

    logged = " ".join(record.message for record in caplog.records if record.name == "app.access")
    assert secret not in logged
    assert huge not in logged
    payloads = _access_payloads(caplog)
    assert payloads
    assert "request_id" not in payloads[-1]["labels"]
    assert "ip_hash" not in payloads[-1]["labels"]


def test_history_is_trimmed(client, monkeypatch):
    seen: dict[str, int] = {}

    def rewrite(question, history, settings):
        seen["count"] = len(history)
        return question, "fees"

    _patch_chat(monkeypatch, _ok_generate)
    monkeypatch.setattr(main, "rewrite_query", rewrite)
    history = [{"role": "user", "content": f"note {i}"} for i in range(10)]
    response = client.post("/chat", json={"message": "fees", "history": history, "stream": False})
    assert response.status_code == 200
    assert seen["count"] == main.settings.max_history_messages


def test_retrieval_error_is_generic(client, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.access")

    def boom(*_a, **_k):
        raise RuntimeError("postgres://srmap:password@db.internal/srmap_rag")

    monkeypatch.setattr(main, "rewrite_query", lambda *_a, **_k: ("q", "fees"))
    monkeypatch.setattr(main, "hybrid_search", boom)
    response = client.post("/chat/stream", json={"message": "hidden-question-abc", "stream": True})
    assert response.status_code == 503
    assert response.json()["detail"] == "Retrieval is temporarily unavailable."
    assert "password" not in response.text
    assert "hidden-question-abc" not in " ".join(
        record.message for record in caplog.records if record.name == "app.access"
    )


def test_database_persist_failure_still_returns_the_answer(client, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.access")
    _patch_chat(monkeypatch, _ok_generate)
    monkeypatch.setattr(main, "get_conn", _connect(_Conn(fail=True)))
    response = client.post("/chat/stream", json={"message": "private-persist-question", "stream": True})
    assert response.status_code == 200
    assert "event: done" in response.text
    assert "Answer about fees." in response.text
    payloads = _access_payloads(caplog)
    assert any(item["labels"]["error_category"] == "persistence" for item in payloads)
    assert all("private-persist-question" not in json.dumps(item) for item in payloads)


def test_gemini_failure_before_stream_is_http_error(client, monkeypatch):
    def fail(*_a, **_k):
        raise ModelError("model_unavailable", BUSY_MESSAGE, status_code=503)

    _patch_chat(monkeypatch, fail)
    response = client.post("/chat", json={"message": "fees", "stream": False})
    assert response.status_code == 503
    assert response.json()["detail"] == BUSY_MESSAGE
    assert "api key" not in response.text.lower()


def test_mid_stream_gemini_error_event(client, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.access")

    def fail(*_a, **_k):
        def _pieces():
            yield "Partial answer"
            raise ModelError("model_unavailable", BUSY_MESSAGE, status_code=503)

        return _pieces()

    _patch_chat(monkeypatch, fail)
    response = client.post("/chat/stream", json={"message": "do-not-log-this-question", "stream": True})
    assert response.status_code == 200
    assert "event: error" in response.text
    assert "event: done" not in response.text
    assert "Partial answer" in response.text
    assert BUSY_MESSAGE in response.text
    error_payload = _sse_data(response.text, "error")
    assert error_payload["error"] == "model_unavailable"
    assert error_payload["request_id"]
    logged = " ".join(json.dumps(item) for item in _access_payloads(caplog))
    assert "do-not-log-this-question" not in logged
    assert any(item["labels"]["error_category"] == "model_unavailable" for item in _access_payloads(caplog))


def test_client_disconnect_stops_the_stream(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.access")

    def pieces(*_a, **_k):
        def _pieces():
            yield "one"
            yield "two"

        return _pieces()

    monkeypatch.setattr(main, "generate_answer", pieces)
    monkeypatch.setattr(main, "get_conn", _connect(_Conn()))
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="req-disconnect", timings={}, ip_hash="abc"),
    )
    request.is_disconnected = _disconnect_after_first()

    async def collect():
        events = []
        async for event in main.chat_event_stream(
            request,
            request_id="req-disconnect",
            conversation_id="conv",
            message="disconnect-question",
            history=[],
            intent="fees",
            rewritten="fees",
            chunks=[],
            citations=[],
            started=0,
        ):
            events.append(event)
        return events

    events = asyncio.run(collect())
    names = [event["event"] for event in events]
    assert names == ["meta", "token"]
    assert any(item["labels"]["error_category"] == "client_disconnect" for item in _access_payloads(caplog))
    assert all("disconnect-question" not in json.dumps(item) for item in _access_payloads(caplog))


def test_gemini_stream_raises_without_leaking_into_the_answer(caplog):
    caplog.set_level(logging.INFO, logger="app.access")

    class _Models:
        def generate_content_stream(self, **_kwargs):
            raise RuntimeError("secret-prompt-echo")

        def generate_content(self, **_kwargs):
            raise RuntimeError("secret-prompt-echo")

    client = SimpleNamespace(models=_Models())
    try:
        list(_gemini_stream(client, "gemini-test", "prompt-should-stay-out", {}))
        raised = False
    except ModelError as exc:
        raised = True
        assert exc.category == "generation_failed"
        assert exc.public_message == BUSY_MESSAGE
    assert raised
    logged = " ".join(record.message for record in caplog.records if record.name == "app.access")
    assert "secret-prompt-echo" not in logged
    assert "prompt-should-stay-out" not in logged


def _disconnect_after_first():
    state = {"calls": 0}

    async def _check():
        state["calls"] += 1
        return state["calls"] > 1

    return _check


def _sse_data(body: str, event_name: str) -> dict:
    current = None
    for line in body.splitlines():
        if line.startswith("event:"):
            current = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and current == event_name:
            return json.loads(line.split(":", 1)[1].strip())
    raise AssertionError(f"{event_name} event not found")
