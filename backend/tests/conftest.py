import os

os.environ["EMBEDDING_PROVIDER"] = "gemini"
os.environ["TRUSTED_PROXY_HOPS"] = "0"
os.environ["DATABASE_URL"] = "postgresql://srmap:srmap@127.0.0.1:1/srmap_rag"
os.environ["GEMINI_CHAT_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["RATE_LIMIT_PER_MINUTE"] = "100"
os.environ["RATE_LIMIT_PER_HOUR"] = "1000"
os.environ["HEALTH_CACHE_SECONDS"] = "120"

import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.fixture
def client():
    main.limiter.per_minute = 100
    main.limiter.per_hour = 1000
    main.limiter.max_keys = 10000
    main.limiter.reset()
    main.reset_health_cache()
    with TestClient(main.app) as test_client:
        yield test_client
