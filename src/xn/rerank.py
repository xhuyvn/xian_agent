"""bge-reranker-v2-m3 cross-encoder, fp16 on the GPU, loaded on first use."""

from __future__ import annotations

from typing import Any, Callable


class RerankError(RuntimeError):
    """Raised when the reranker cannot load or score."""


def _load_cross_encoder(name: str, device: str, max_length: int) -> Any:
    """Load the model in fp16; imported here so the CLI starts without torch."""
    import torch
    from sentence_transformers import CrossEncoder

    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RerankError(f"reranker device {device} asked for, but CUDA is not available")
    return CrossEncoder(
        name,
        device=device,
        max_length=max_length,
        activation_fn=torch.nn.Sigmoid(),
        model_kwargs={"dtype": torch.float16},
    )


class CrossEncoderReranker:
    """Scores query-passage pairs in [0, 1], the scale the gate threshold uses."""

    def __init__(
        self,
        *,
        model: str,
        device: str,
        max_length: int,
        batch_size: int,
        loader: Callable[[str, str, int], Any] = _load_cross_encoder,
    ) -> None:
        self.model = model
        self._device = device
        self._max_length = max_length
        self._batch_size = batch_size
        self._loader = loader
        self._encoder: Any = None

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> CrossEncoderReranker:
        reranker = config["models"]["reranker"]
        return cls(
            model=reranker["name"],
            device=reranker["device"],
            max_length=reranker["max_length"],
            batch_size=reranker["batch_size"],
        )

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Return one score per passage, in order."""
        if not passages:
            return []
        try:
            if self._encoder is None:
                self._encoder = self._loader(self.model, self._device, self._max_length)
            scores = self._encoder.predict(
                [(query, passage) for passage in passages],
                batch_size=self._batch_size,
                show_progress_bar=False,
            )
        except RerankError:
            raise
        except Exception as err:
            raise RerankError(f"reranker {self.model} failed: {err}") from err
        result = [float(s) for s in scores]
        if len(result) != len(passages):
            raise RerankError(f"expected {len(passages)} scores, got {len(result)}")
        return result
