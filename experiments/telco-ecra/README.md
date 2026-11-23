# TELCO-ECRA: experiment data and code

Experiment archive for the paper *TELCO-ECRA: Reducing LLM Hallucinations in Telecom Support with Rule-Derived
Programs* (S. Maheshwari, IEEE ICCA 2026). Paper details are in
[`docs/papers/telco-ecra/`](../../docs/papers/telco-ecra/).

Every case, account, phone, plan and offer in this archive is synthetic. The 1,000-case benchmark and both retrieval
worlds use invented catalogues; no customer data or real product catalogue is included.

## Contents

| Folder | What it holds | Paper |
|---|---|---|
| `data/ops/` | 200 practice and 800 test cases (seeds 20261011, 20261012), oracles, preregistration, every saved model answer (`runs/*.jsonl`), and the scored summary | Tables I–III, Fig. 2 |
| `code/frozen/` | The six programs GPT-5.4-mini wrote from the round-1 (`v2_*`) and round-2 (`v3_*`) rules, frozen before the cases were generated | Table III (A3, A6) |
| `code/shared/` | Hand-written checker (`verifier.py`), scoring, the round-1 and round-2 prompts and model wrappers, and the case generator | all |
| `code/ops/` | Generator and runner of the 1,000-case benchmark, and the Fig. 2 plot | Tables I–III |
| `data/retrieval/` | Retrieval world (seed 20261013): 800 test cases among 10,000 distractor accounts (`world.json`), and every per-step output: customer messages written by GPT-5.4-mini and by Claude Sonnet 5.5, parsed mentions, offer selections, direct answers and summaries | Tables IV, V, VII |
| `code/retrieval/` | Retrieval pipeline: world generator, SQLite and Typesense loaders, the four-step pipeline, the phone + offer query, the message-class rule, the ask-when-tied step and the hybrid search | Tables IV, V, VII |
| `data/retrieval_earlier/` | The earlier 200-case world, the 30 author-typed messages (`author_messages.json`) and their results | Table VI |
| `code/retrieval_earlier/` | Pipeline code of the earlier world | Table VI |
| `data/earlier160/` | Aggregate scores on the earlier set of 160 test cases | Table III, last column |

## Reproduce

Python 3.9 or later; the replay uses only the standard library.

    cd experiments/telco-ecra
    python code/replay.py

`replay.py` re-scores every saved model answer, re-executes the six frozen programs on the practice and test sets,
re-applies the program-selection rule, runs the hand-written checker with each group of checks removed, and checks
every count against `data/ops/summary_1000.json`. It reproduces Tables I, II and III (800 test cases) and ends with
`MATCHES saved summary`.

The retrieval tables (IV, V, VII) are in `data/retrieval/runs/summary.json`, `posthoc_summary.json`,
`claude_summary.json`, `ambiguity_summary.json`, `clarify_summary.json` and `hybrid_summary.json`, next to the
per-step outputs they were computed from. Table VI is in `data/retrieval_earlier/runs/sheet_author_summary.json`.

Running the models again needs `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (customer messages by the second model) and
`VOYAGE_API_KEY` (hybrid search), and the retrieval pipeline needs a local Typesense server (`docker-compose.yml` in
`code/retrieval_earlier/`; set `TELCO_TS_API_KEY` and `TELCO_TS_PORT`). The SQLite systems of record are rebuilt from
`world.json` by `load_sqlite.py`. Model outputs will differ between runs, which is why every saved answer is included.

## Not included

- **The four worked examples** in the round-2 medium-effort prompt (A5) and in the program-writing prompt. They come
  from the earlier 160-case set, which reused fields of a real product catalogue, and are not redistributed. Their
  SHA-256 is recorded in `data/ops/preregistration.json` (`sha256.examples`). Scoring and replay do not need them.
- **The earlier 160-case set itself**, for the same reason. Only its aggregate scores are included.
- **Embedding caches** of the hybrid search (about 130 MB of Voyage-3 vectors) and the Typesense index files; the
  hybrid-search results are in `hybrid_summary.json`.
- **API keys.** The original scripts read keys from a private configuration file; this copy reads them from the
  environment.

## Notes

- Scripts in `code/ops/`, `code/shared/` and `code/retrieval*/` are the versions that produced the results. They
  load data through the paths of the original repository and are included as the record of what ran; `replay.py`
  is the entry point for this archive.
- The OpenAI run stopped once when provider credits ran out and was resumed with the same code; cached complete
  calls were reused and only the A5 arm was re-sent in full (`data/ops/resume_note.json`).
- The class rule for customer messages, the ask-when-tied step and the hybrid search were fixed on the earlier world
  before the 1,000-case world was generated; the hybrid search is a post hoc arm (`hybrid_note.json`).
