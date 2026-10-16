from __future__ import annotations

from functools import lru_cache

from app.config import Settings, get_settings

LOCAL_BGE_DIMS = 384
BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class GeminiEmbedder:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        api_key = self.settings.embedding_api_key
        if not api_key:
            raise RuntimeError(
                "No Gemini embedding key. Set GEMINI_EMBEDDING_API_KEY "
                "(or GEMINI_API_KEY as fallback)."
            )
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self.model_id = self.settings.gemini_embedding_model
        self.model = self.model_id
        self.dims = int(self.settings.embedding_dims)

    def embed_query(self, text: str) -> list[float]:
        from google.genai import types

        result = self._client.models.embed_content(
            model=self.model,
            contents=text,
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=self.dims,
            ),
        )
        return list(result.embeddings[0].values)


class LocalBGEEmbedder:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model_id = self.settings.local_embedding_model
        self.dims = LOCAL_BGE_DIMS
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_id, device="cpu")

    def embed_query(self, text: str) -> list[float]:
        prepared = f"{BGE_QUERY_INSTRUCTION}{text}"
        arr = self._model.encode([prepared], normalize_embeddings=True)
        return [float(x) for x in arr[0]]


@lru_cache
def get_embedder():
    settings = get_settings()
    provider = (settings.embedding_provider or "gemini").strip().lower()
    if provider == "local":
        return LocalBGEEmbedder(settings)
    if provider == "gemini":
        return GeminiEmbedder(settings)
    raise RuntimeError(f"Unknown EMBEDDING_PROVIDER={provider!r}")


def active_model_id(settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    provider = (settings.embedding_provider or "gemini").strip().lower()
    if provider == "local":
        return settings.local_embedding_model
    return settings.gemini_embedding_model
