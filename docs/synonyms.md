# Synonyms — Vocabulary Equivalence for Search

## What it is

The **Synonyms** tab lets an operator declare that different words should be treated as
equivalent during search — e.g. `wire = cable = cord`. It's a thin UI + API wrapper around
Typesense's own native per-collection synonym feature; ISP doesn't reimplement synonym
matching itself, it just exposes Typesense's existing mechanism per index.

Changes apply immediately at query time — **no rebuild required**, since synonyms are
resolved by Typesense during query parsing, not baked into indexed documents.

## Why

While testing chat retrieval on the Accessories index, "iphone charging wire" returned
nothing useful — every product in the catalog says "Cable," never "wire." The chat
retrieval pipeline (see `chat-rag.md`) weights its three passes lex-facets 0.6 / lex-all 0.3
/ semantic 0.1. A word that appears in zero product text gets zero signal from the two
lexical passes (90% of the ranking weight), leaving only the weak 10%-weighted semantic
pass to carry the entire query — not reliable enough to consistently rank the right product
above semantically-adjacent-but-wrong ones.

No synonym infrastructure existed anywhere in the codebase before this feature. The fix is
Typesense's own synonym feature, not a bespoke query-rewriting layer — it's already there,
already applied natively by the search engine, and generalizes to any future vocabulary gap
(not just wire/cable) without further code changes.

## Two synonym modes

Typesense supports both, and the Synonyms tab exposes both:

- **Multi-way** (no `root`): every listed word is mutually interchangeable.
  `{"synonyms": ["wire", "cable", "cord"]}` — searching any one of the three matches
  documents containing any of the others.
- **One-way** (`root` set): searching the root also matches the synonyms, but not the
  reverse. `{"root": "tv", "synonyms": ["television", "telly"]}` — useful when blanket
  bidirectional equivalence would be too broad (e.g. you want "tv" to also find
  "television," but you don't necessarily want every unrelated use of "television" to
  surface "tv"-tagged content).

## Backend

Three thin wrapper functions in `backend/src/isp/services/infra/typesense_client.py`,
proxying directly to Typesense's native `collections[name].synonyms` API (same logging
conventions as the rest of the file — errors are logged with `exc_info=True` rather than
silently swallowed):

- `list_synonyms(index_id) -> list[dict]`
- `upsert_synonym(index_id, synonym_id, synonyms, root=None) -> dict`
- `delete_synonym(index_id, synonym_id) -> bool`

**Note on Typesense server version:** this stack runs Typesense **0.25.2**
(`docker-compose.yml`), which pre-dates Typesense's newer top-level `synonym_sets` API
(introduced in later server versions). The installed Python client (`typesense==2.0.0`)
marks the per-collection `synonyms` API "deprecated" in favor of `synonym_sets`, but that
newer API isn't available on this server version — the per-collection API is the correct
and only viable choice for this deployment, not a legacy shortcut. Verified directly against
the running server before building anything (created a real `{"synonyms": ["wire","cable","cord"]}`
set, confirmed it changed search results correctly, then deleted the test set).

## API endpoints

```
GET    /v1/indexes/{idx_id}/synonyms                → { "synonyms": [...] }
PUT    /v1/indexes/{idx_id}/synonyms/{synonym_id}    → create or update one set
DELETE /v1/indexes/{idx_id}/synonyms/{synonym_id}    → 204 on success
```

`PUT` body: `{"synonyms": ["wire", "cable", "cord"]}` (multi-way) or
`{"synonyms": ["television", "telly"], "root": "tv"}` (one-way). Requires at least 2 words
in `synonyms`.

Unlike the Fields endpoints, these are **not restricted for external indexes** — synonyms
are a pure Typesense-collection feature and apply the same way regardless of whether ISP
considers the index "managed" or "external."

## Frontend

Mirrors the **Boost Rules** tab's file layout (`frontend/src/components/sources/BoostRulesTab.svelte`,
`frontend/src/lib/stores/boost-rules-store.ts`), adapted for **per-item CRUD** instead of
Boost Rules' "edit the whole array, Save once" model — Typesense's synonym API is inherently
per-ID, so each add/delete is its own API call, not a batch save.

- `frontend/src/lib/types/index.types.ts` — `Synonym { id, synonyms, root? }`
- `frontend/src/lib/api.ts` — `getSynonyms`, `upsertSynonym`, `deleteSynonym`
- `frontend/src/lib/stores/synonyms-store.ts` — `load`, `upsert(id, synonyms, root?)`, `remove(id)`
- `frontend/src/components/sources/SynonymsTab.svelte` — list of existing sets (comma-joined
  words, or `root → synonyms` for one-way) with a delete button, plus an "Add synonym set"
  form (comma-separated word input, a "One-way" checkbox that reveals a root-word input)
- `frontend/src/components/sources/IndexDetail.svelte` — tab registered next to Boost Rules,
  since both are search-tuning tools

## Decisions summary

- **Typesense-native, not a custom query-rewrite layer** — reuses an existing, correct
  mechanism instead of building a parallel one.
- **Per-collection API, not `synonym_sets`** — matches the actual Typesense server version
  running in this stack; verified directly rather than assumed from the client library.
- **Per-item CRUD, not batch-save** — matches Typesense's own per-ID synonym model, unlike
  Boost Rules' Postgres-JSONB-backed batch model.
- **No rebuild required** — synonyms are query-time, not document-time, so changes are live
  immediately.
