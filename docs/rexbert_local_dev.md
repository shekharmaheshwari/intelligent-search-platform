# Running RexBERT Locally for ISP Development

RexBERT is a BERT-based embedding model family supported by ISP for per-index vector search.
In production it runs on an internal inference server. This guide shows how to run a
compatible local server for development and learning using `sentence-transformers`.

---

## What the local server does

- Exposes a single endpoint: `POST /v1/embeddings` (OpenAI-compatible format)
- Uses `all-MiniLM-L6-v2` from HuggingFace (~80 MB, CPU-only, no GPU required)
- Truncates and normalises output to match ISP's expected dimension per variant:

| ISP model name  | Vector dimension |
|-----------------|-----------------|
| rexbert-micro   | 128-d           |
| rexbert-mini    | 256-d           |
| rexbert-base    | 384-d           |
| rexbert-large   | 384-d           |

> **Note:** In production, real RexBERT variants produce different dimensions
> (micro=128, mini=256, base=768, large=1024). The local server caps at 384-d
> because `all-MiniLM-L6-v2` produces 384-d vectors. This is fine for local testing.

---

## Prerequisites

Python 3.10+ with pip. No GPU required.

```bash
pip install fastapi uvicorn "sentence-transformers<3.0" numpy
```

> `sentence-transformers<3.0` is required because PyTorch 2.2 (bundled with Anaconda
> on macOS) is not compatible with sentence-transformers 3+, which requires PyTorch >= 2.4.

---

## Starting the server

The server script lives at `tools/rexbert_local.py` in the repo root.

```bash
# From the repo root
python3 tools/rexbert_local.py
```

Optional flags:

```bash
python3 tools/rexbert_local.py --port 8010   # default port
python3 tools/rexbert_local.py --port 9000   # custom port
```

Expected output on first run (model downloads ~80 MB):

```
INFO  Starting RexBERT local server on 0.0.0.0:8010
INFO  Supported variants: ['rexbert-micro', 'rexbert-mini', 'rexbert-base', 'rexbert-large']
INFO  Base model: all-MiniLM-L6-v2 (downloads ~80 MB on first run)
INFO  In ISP Settings → RexBERT Server:
INFO    Endpoint: http://localhost:8010
INFO    API key:  (leave blank)
INFO  Application startup complete.
```

Subsequent runs skip the download and start in ~2 seconds.

### Verify it is working

```bash
curl -s -X POST http://localhost:8010/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model": "rexbert-mini", "input": ["Samsung Galaxy Tab S10"]}' | python3 -c "
import sys, json
d = json.load(sys.stdin)
vec = d['data'][0]['embedding']
print(f'model:     {d[\"model\"]}')
print(f'dimension: {len(vec)}')
print(f'first 5:   {vec[:5]}')
"
```

Expected output:

```
model:     rexbert-mini
dimension: 256
first 5:   [-0.119, 0.002, 0.025, -0.134, 0.021]
```

---

## ISP configuration

### Step 1 — Save the RexBERT endpoint in ISP Settings

The ISP backend runs inside Docker and cannot reach `localhost` on your Mac directly.
Use `host.docker.internal` instead, which Docker resolves to the Mac host.

**Via the ISP UI:**

1. Open the ISP UI → **Settings**
2. Scroll to the **RexBERT Server** section (always visible, separate from the global model)
3. Enter the endpoint: `http://host.docker.internal:8010/v1/embeddings`
4. Leave **API key** blank
5. Click **Save RexBERT config**

**Via curl (alternative):**

```bash
curl -s -X PUT http://localhost:8001/v1/settings/rexbert-config \
  -H "Content-Type: application/json" \
  -d '{"endpoint": "http://host.docker.internal:8010/v1/embeddings"}'
```

This saves the endpoint to the database and loads it into the running backend immediately —
no container restart required.

### Step 2 — Create an index with a per-index RexBERT model

When creating a new index via the **Add Source** wizard:

1. Complete steps 1–4 (name, connector, field mapping, extensions) as normal
2. On **Step 5 — Embedding model**, select **RexBERT-mini** (256-d)
3. Click **Start Ingestion**

ISP stores the model choice on the index only (`embed_model = "rexbert-mini"`).
The global model (e.g. voyage-3) is not changed. Other indexes are unaffected.

### Step 3 — Verify vectors were stored

After ingest completes, open the index → **Documents** tab → expand any document.
You should see a `vec` field containing an array of 256 floats:

```
vec   [ 0.027, -0.033, 0.009, -0.084, ... ]   (256 values)
```

If `vec` is missing, check that the local server is running and the endpoint was saved
correctly (see Troubleshooting below).

---

## How ISP routes embedding calls

Each index stores an `embed_model` field in PostgreSQL:

```
embed_model = ""            → use the global default (e.g. voyage-3)
embed_model = "rexbert-mini" → call the RexBERT endpoint for this index only
```

At ingest and search time the backend resolves:

```python
effective_model = meta.embed_model or embedding_service.get_current_model()
```

Query embedding at search time follows the same logic — a search on a rexbert-mini
index calls your local server to embed the query before hitting Typesense.

### Searching across indexes with different models

Each ISP index is its own Typesense collection with its own fixed vector dimension.
Voyage-3 indexes use 1024-d collections; rexbert-mini indexes use 256-d collections.

- **Per-index search** (`POST /v1/indexes/{id}/search`) — always correct: ISP embeds
  the query with the index's own model.
- **Cross-index / "search everything"** — scores from different embedding spaces are
  not directly comparable. Indexes you want to search together should use the same model.

---

## Changing the model on an existing index

Use the `PUT /v1/indexes/{id}/embed-model` endpoint:

```bash
# Switch Tablets index to rexbert-mini
curl -s -X PUT http://localhost:8001/v1/indexes/<index-id>/embed-model \
  -H "Content-Type: application/json" \
  -d '{"model_id": "rexbert-mini"}'

# Revert to global default
curl -s -X PUT http://localhost:8001/v1/indexes/<index-id>/embed-model \
  -H "Content-Type: application/json" \
  -d '{"model_id": ""}'
```

After changing the model, **rebuild the index** so all vectors are re-generated with the
new model. Mixing vectors from different models in the same Typesense collection will
produce incorrect semantic search results.

---

## Stopping the server

```bash
# If started in the foreground: Ctrl+C
# If running as a background process:
pkill -f rexbert_local.py
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `vec` missing after ingest | RexBERT endpoint not reachable from Docker | Use `host.docker.internal:8010`, not `localhost:8010` |
| `vec` missing after ingest | Endpoint not saved in ISP | Run the `PUT /v1/settings/rexbert-config` curl command |
| `address already in use` on start | Server already running on port 8010 | It is already running — no action needed |
| Model download hangs | First-run download of `all-MiniLM-L6-v2` | Wait; it downloads ~80 MB once and caches locally |
| `ModuleNotFoundError: sentence_transformers` | Dependencies not installed | Run `pip install fastapi uvicorn "sentence-transformers<3.0" numpy` |
| Dimension mismatch warning in Settings | Selecting a rexbert model as the *global* model | Do not change the global model; use the RexBERT Server section to save credentials only |
