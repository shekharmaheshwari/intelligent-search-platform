# Copyright 2026 Shekhar Maheshwari
# SPDX-License-Identifier: Apache-2.0

"""
Local RexBERT-compatible embedding server for development and learning.

Mimics the RexBERT endpoint that ISP calls for per-index embedding overrides.
Uses sentence-transformers under the hood (no GPU required) and truncates
vectors to match the dimension ISP expects for each rexbert-* model variant.

Usage
-----
  pip install fastapi uvicorn sentence-transformers numpy
  python tools/rexbert_local.py

Then in ISP Settings → RexBERT Server:
  Endpoint URL: http://localhost:8010
  API key:      (leave blank)

The server accepts the same OpenAI-compatible request shape ISP sends:
  POST /v1/embeddings
  {"model": "rexbert-mini", "input": ["text 1", "text 2"]}

Note: this is for local learning only.  The real RexBERT model would be a
proprietary fine-tuned BERT deployed on your company's internal infrastructure.
The sentence-transformers model used here is semantically reasonable but not
the same as the production model.
"""
import argparse
import logging
import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rexbert-local")

# One shared base model — small, CPU-friendly, downloads ~80 MB on first run.
# Produces 384-d vectors; we truncate to ISP's expected dim per variant.
BASE_MODEL_ID = "all-MiniLM-L6-v2"

# ISP rexbert-* variant → target vector dimension.
# Must match DIM_BY_MODEL in backend/src/isp/services/domain/settings_service.py.
DIM_BY_VARIANT: dict[str, int] = {
    "rexbert-micro": 128,
    "rexbert-mini":  256,
    "rexbert-base":  384,   # full 384-d, no truncation needed
    "rexbert-large": 384,   # same; real large would be 768+ but we cap at base
}

app = FastAPI(title="RexBERT Local Dev Server")

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    """Lazily load the sentence-transformers model on first request."""
    global _model
    if _model is None:
        log.info("Loading %s (first request — may take a moment)…", BASE_MODEL_ID)
        _model = SentenceTransformer(BASE_MODEL_ID)
        log.info("Model loaded.  Base dimension: %d", _model.get_sentence_embedding_dimension())
    return _model


def truncate_and_normalise(vecs: np.ndarray, dim: int) -> np.ndarray:
    """
    Truncate to ``dim`` dimensions then L2-normalise each vector.

    Truncation preserves the first N principal directions of the embedding
    space and is semantically reasonable for short-text similarity tasks.
    Normalisation ensures cosine-equivalent dot-product scores in Typesense.
    """
    truncated = vecs[:, :dim].astype(np.float32)
    norms = np.linalg.norm(truncated, axis=1, keepdims=True)
    # avoid divide-by-zero for zero vectors
    norms = np.where(norms == 0, 1.0, norms)
    return truncated / norms


class EmbedRequest(BaseModel):
    model: str
    input: list[str]


class EmbedResponse(BaseModel):
    object: str = "list"
    model: str
    data: list[dict]
    usage: dict


@app.post("/v1/embeddings", response_model=EmbedResponse)
def embed(req: EmbedRequest) -> EmbedResponse:
    """
    OpenAI-compatible embeddings endpoint.

    Accepts any ``rexbert-*`` model name; all variants use the same base model
    with the output truncated to the variant's target dimension.
    Unknown model names fall back to rexbert-mini (256-d).
    """
    variant = req.model if req.model in DIM_BY_VARIANT else "rexbert-mini"
    if req.model not in DIM_BY_VARIANT:
        log.warning("Unknown model '%s', falling back to rexbert-mini", req.model)

    if not req.input:
        raise HTTPException(status_code=400, detail="input list must not be empty")

    target_dim = DIM_BY_VARIANT[variant]
    model = get_model()

    raw: np.ndarray = model.encode(req.input, convert_to_numpy=True)
    vecs = truncate_and_normalise(raw, target_dim)

    log.info("Embedded %d text(s) with %s → %d-d", len(req.input), variant, target_dim)

    return EmbedResponse(
        model=req.model,
        data=[
            {"object": "embedding", "index": i, "embedding": vec.tolist()}
            for i, vec in enumerate(vecs)
        ],
        usage={
            "prompt_tokens": sum(len(t.split()) for t in req.input),
            "total_tokens":  sum(len(t.split()) for t in req.input),
        },
    )


@app.get("/health")
def health() -> dict:
    """Simple health check — returns model load status."""
    return {"status": "ok", "model_loaded": _model is not None, "base_model": BASE_MODEL_ID}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RexBERT local dev embedding server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8010, help="Port (default: 8010)")
    args = parser.parse_args()

    log.info("Starting RexBERT local server on %s:%d", args.host, args.port)
    log.info("Supported variants: %s", list(DIM_BY_VARIANT))
    log.info("Base model: %s (downloads ~80 MB on first run)", BASE_MODEL_ID)
    log.info("")
    log.info("In ISP Settings → RexBERT Server:")
    log.info("  Endpoint: http://localhost:%d", args.port)
    log.info("  API key:  (leave blank)")

    uvicorn.run(app, host=args.host, port=args.port)
