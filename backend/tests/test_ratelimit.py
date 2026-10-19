import threading

from app.ratelimit import RateLimiter


def test_minute_and_hour_windows():
    limiter = RateLimiter(per_minute=2, per_hour=3, max_keys=10)
    assert limiter.allow("10.0.0.1", now=1_000)
    assert limiter.allow("10.0.0.1", now=1_010)
    assert not limiter.allow("10.0.0.1", now=1_020)
    assert limiter.allow("10.0.0.2", now=1_020)
    assert limiter.allow("10.0.0.1", now=1_070)
    assert not limiter.allow("10.0.0.1", now=1_080)


def test_key_cap_drops_oldest():
    limiter = RateLimiter(per_minute=5, per_hour=5, max_keys=2)
    assert limiter.allow("a", now=10)
    assert limiter.allow("b", now=11)
    assert limiter.allow("c", now=12)
    assert len(limiter) == 2
    assert limiter.allow("a", now=13)
    assert len(limiter) == 2


def test_allow_is_safe_under_threads():
    limiter = RateLimiter(per_minute=100_000, per_hour=100_000, max_keys=32)
    errors: list[BaseException] = []

    def hammer(worker: int) -> None:
        try:
            for n in range(40):
                limiter.allow(f"ip-{worker}-{n % 20}")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(limiter) <= 32
