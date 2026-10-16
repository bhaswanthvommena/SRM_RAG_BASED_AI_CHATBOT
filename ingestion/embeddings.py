from __future__ import annotations

import asyncio
import time
from typing import Literal, Protocol

from rich.console import Console
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ingestion.config import Settings, get_settings

console = Console()

TaskType = Literal["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]

# Canonical model ids stored in chunks.embed_model
GEMINI_EMBED_MODEL_ID = "gemini-embedding-001"
LOCAL_BGE_MODEL_ID = "BAAI/bge-small-en-v1.5"
LOCAL_BGE_DIMS = 384
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


def is_rate_limit_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "429" in text or "resource_exhausted" in text or "rate" in text or "quota" in text


def is_quota_exhausted(exc: BaseException) -> bool:
    text = str(exc).lower()
    return (
        "resource_exhausted" in text
        or "exceeded your current quota" in text
        or "quota exceeded" in text
        or ("429" in text and "quota" in text)
    )


def _is_rate_limit(exc: BaseException) -> bool:
    return is_rate_limit_error(exc)


class Embedder(Protocol):
    model_id: str
    dims: int

    def embed_one(self, text: str, *, task_type: TaskType = "RETRIEVAL_DOCUMENT") -> list[float]: ...

    def embed_batch(
        self,
        texts: list[str],
        *,
        task_type: TaskType = "RETRIEVAL_DOCUMENT",
        batch_size: int = 8,
    ) -> list[list[float]]: ...


class GeminiEmbedder:
    """Hosted Google Gemini embeddings (768-d in this project)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        api_key = self.settings.embedding_api_key
        if not api_key:
            raise RuntimeError(
                "No Gemini embedding key. Set GEMINI_EMBEDDING_API_KEY "
                "(or GEMINI_API_KEY as fallback) in .env."
            )
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self.model_id = self.settings.gemini_embedding_model or GEMINI_EMBED_MODEL_ID
        self.model = self.model_id
        self.dims = int(self.settings.embedding_dims)
        self._min_interval = 0.7
        self._last_call = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()

    @retry(
        retry=retry_if_exception(_is_rate_limit),
        wait=wait_exponential(multiplier=2, min=15, max=90),
        stop=stop_after_attempt(8),
        reraise=True,
    )
    def embed_one(self, text: str, *, task_type: TaskType = "RETRIEVAL_DOCUMENT") -> list[float]:
        from google.genai import types

        self._throttle()
        result = self._client.models.embed_content(
            model=self.model,
            contents=text,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self.dims,
            ),
        )
        values = result.embeddings[0].values
        return list(values)

    def embed_batch(
        self,
        texts: list[str],
        *,
        task_type: TaskType = "RETRIEVAL_DOCUMENT",
        batch_size: int = 8,
    ) -> list[list[float]]:
        vectors: list[list[float]] = []
        for i, text in enumerate(texts, start=1):
            vectors.append(self.embed_one(text, task_type=task_type))
            if i % 10 == 0 or i == len(texts):
                console.print(f"  embedded {i}/{len(texts)}")
        return vectors

    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_one, text, task_type="RETRIEVAL_QUERY")


class LocalBGEEmbedder:
    """Local CPU embeddings via sentence-transformers + BAAI/bge-small-en-v1.5 (384-d)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model_id = self.settings.local_embedding_model or LOCAL_BGE_MODEL_ID
        self.dims = LOCAL_BGE_DIMS
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers"
            ) from exc

        console.print(f"[cyan]Loading local embedder[/] {self.model_id} (CPU)…")
        self._model = SentenceTransformer(self.model_id, device="cpu")
        # Verify output dim once
        probe = self._model.encode(["dim check"], normalize_embeddings=True)
        actual = int(probe.shape[1])
        if actual != self.dims:
            raise RuntimeError(f"Expected {self.dims}-d embeddings, model returned {actual}-d")
        console.print(f"[green]Local embedder ready[/] dims={self.dims}")

    def _prepare(self, texts: list[str], *, task_type: TaskType) -> list[str]:
        if task_type == "RETRIEVAL_QUERY":
            return [f"{BGE_QUERY_INSTRUCTION}{t}" for t in texts]
        return list(texts)

    def embed_one(self, text: str, *, task_type: TaskType = "RETRIEVAL_DOCUMENT") -> list[float]:
        return self.embed_batch([text], task_type=task_type, batch_size=1)[0]

    def embed_batch(
        self,
        texts: list[str],
        *,
        task_type: TaskType = "RETRIEVAL_DOCUMENT",
        batch_size: int = 8,
    ) -> list[list[float]]:
        prepared = self._prepare(texts, task_type=task_type)
        vectors: list[list[float]] = []
        bs = max(1, batch_size)
        for i in range(0, len(prepared), bs):
            chunk = prepared[i : i + bs]
            arr = self._model.encode(
                chunk,
                batch_size=bs,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            for row in arr:
                vectors.append([float(x) for x in row])
            console.print(f"  embedded {min(i + bs, len(prepared))}/{len(prepared)}")
        return vectors

    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_one, text, task_type="RETRIEVAL_QUERY")


def get_embedder(settings: Settings | None = None) -> Embedder:
    settings = settings or get_settings()
    provider = (settings.embedding_provider or "gemini").strip().lower()
    if provider == "local":
        return LocalBGEEmbedder(settings)
    if provider == "gemini":
        return GeminiEmbedder(settings)
    raise RuntimeError(
        f"Unknown EMBEDDING_PROVIDER={provider!r}. Use 'gemini' or 'local'."
    )


def active_model_id(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    provider = (settings.embedding_provider or "gemini").strip().lower()
    if provider == "local":
        return settings.local_embedding_model or LOCAL_BGE_MODEL_ID
    return settings.gemini_embedding_model or GEMINI_EMBED_MODEL_ID


def active_dims(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    provider = (settings.embedding_provider or "gemini").strip().lower()
    if provider == "local":
        return LOCAL_BGE_DIMS
    return int(settings.embedding_dims)
