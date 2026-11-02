"""BGE-M3 dense embeddings through Ollama's native API."""

from __future__ import annotations

from typing import Any

import httpx

TIMEOUT_SECONDS = 120.0


class EmbedError(RuntimeError):
    """Raised when Ollama cannot return the embeddings asked for."""


class OllamaEmbedder:
    """Dense embedding client. Long inputs fail instead of being silently truncated."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        batch_size: int,
        dense_size: int,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self._batch_size = batch_size
        self._dense_size = dense_size
        self._client = client or httpx.Client(base_url=base_url, timeout=TIMEOUT_SECONDS)

    @classmethod
    def from_config(cls, config: dict[str, Any], client: httpx.Client | None = None) -> OllamaEmbedder:
        embedder = config["models"]["embedder"]
        return cls(
            base_url=embedder["base_url"],
            model=embedder["name"],
            batch_size=embedder["batch_size"],
            dense_size=config["qdrant"]["dense_size"],
            client=client,
        )

    def other_loaded_models(self) -> list[str]:
        """Return the models Ollama holds in memory besides the embedder."""
        data = self._request("GET", "/api/ps")
        return [m["name"] for m in data.get("models") or [] if m["name"] != self.model]

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in batches, in order."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            data = self._request(
                "POST", "/api/embed", json={"model": self.model, "input": batch, "truncate": False}
            )
            embeddings = data.get("embeddings") or []
            if len(embeddings) != len(batch):
                raise EmbedError(f"expected {len(batch)} embeddings, got {len(embeddings)}")
            for vector in embeddings:
                if len(vector) != self._dense_size:
                    raise EmbedError(f"expected size {self._dense_size}, got {len(vector)}")
            vectors.extend(embeddings)
        return vectors

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as err:
            raise EmbedError(f"Ollama {path} failed: {err}") from err
