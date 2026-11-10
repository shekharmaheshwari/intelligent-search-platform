# Synonyms — Vocabulary Equivalence for Search

## What it is

The **Synonyms** tab lets an operator declare that different words should be treated as
equivalent during search — e.g. `wire = cable = cord`. It's a thin UI + API wrapper around
Typesense's own native synonym feature (synonym sets, Typesense ≥ 30); ISP doesn't
reimplement synonym matching itself, it just exposes Typesense's existing mechanism per index.

Changes apply immediately at query time — **no rebuild required**, since synonyms are
resolved by Typesense during query parsing, not baked into indexed documents.

## Why

While testing chat retrieval on the Accessories index, "iphone charging wire" returned
nothing useful — every product in the catalog says "Cable," never "wire." (The chat
retrieval pipeline's RRF weights — see `chat-rag.md` — have since been rebalanced toward
semantic, which happens to help this exact case more than it used to; the underlying gap
this feature closes still applies whenever a word appears in zero product text at all.) A
word with zero lexical signal leaves ranking entirely to whatever a general-purpose
embedding happens to consider "wire" close to — not reliable enough to consistently rank
the right product above semantically-adjacent-but-wrong ones.

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

Three thin wrapper functions in `backend/src/isp/services/infra/typesense_client.py`, on
Typesense's `synonym_sets` API (same logging conventions as the rest of the file — errors
are logged with `exc_info=True` rather than silently swallowed):

- `list_synonyms(index_id) -> list[dict]`
- `upsert_synonym(index_id, synonym_id, synonyms, root=None) -> dict`
- `delete_synonym(index_id, synonym_id) -> bool`

### How rules are stored (Typesense ≥ 30)

Typesense 30 removed per-collection synonyms (`/collections/{c}/synonyms`). Rules now live
in top-level **synonym sets**, and a collection uses a set by listing it in its
`synonym_sets` field; search then applies every linked set automatically.

- Each index gets one set, **`isp_<index_id>_synonyms`** (`synonym_set_name()`), created
  and linked to the collection on the first save. Other sets already linked are kept.
- **List** returns the rules of every set linked to the collection, so sets Typesense
  migrated from pre-30 synonyms on upgrade still show up.
- **Save** updates a rule in place in whichever linked set already holds its id; a new id
  goes into the index's own set.
- **Delete** removes the rule from whichever linked set holds it.
- Responses keep the `{id, synonyms, root?}` shape: Typesense 30 returns `root: ""` for
  multi-way rules, which is dropped.

The Python client must be `typesense>=2.1.0` (`pyproject.toml`) for `client.synonym_sets`.

**History:** the feature was first built on Typesense 0.25.2, where only the per-collection
API existed. Moving the stack to 30.2 (`docker-compose.yml`) broke it: every list returned
`[]` and every save was a 500, until it was moved to synonym sets.

Tests: `backend/tests/services/infra/test_synonyms.py`. Unit tests use a fake client; one
live test (skipped unless `TYPESENSE_HOST` is set) does the real round trip and checks that
search applies a rule without a rebuild.

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
- **One synonym set per index, linked to its collection** — the Typesense 30 model; rules
  from any other linked set (e.g. migrated ones) stay visible and editable.
- **Per-item CRUD, not batch-save** — matches Typesense's own per-ID synonym model, unlike
  Boost Rules' Postgres-JSONB-backed batch model.
- **No rebuild required** — synonyms are query-time, not document-time, so changes are live
  immediately.
