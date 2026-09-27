# GLiNER Query Understanding — Design

Status: **proposed, not implemented, not yet recommended for adoption.** §6's
benchmark is the load-bearing section — accuracy on our own control cases
has not yet cleared the bar this document sets before writing any real
integration code.

## 1. Objective

[[multiIntentConjunctionSearchDesign.md]] and
[[deviceCompatibilityMatchingDesign.md]] both already build a **shared
vocabulary at index time and reuse it at chat time**:

- `category` — extracted at index time (`catalog_mapper.js`), embedded and
  reused at chat time (`_category_vectors_for_index`) as the classification
  vocabulary for multi-intent splitting.
- `compatible_devices` — extracted at index time
  (`device_compatibility_accessories.js`), reused at chat time
  (`_resolve_device_filter`) as the device-matching vocabulary.

This document evaluates whether **GLiNER** (a zero-shot NER model) belongs
anywhere in that picture — specifically, as a chat-time query-parsing step
that consumes the vocabulary already built at index time, not as a
replacement for building it.

**What this document is not**: a proposal to build a new "common language"
layer from scratch. That layer already exists (the two bullets above). Every
bug traced in this session's chat sessions lived inside that existing
pattern — bad data going into it, or no confidence check when consuming it —
not from lacking one. GLiNER is evaluated here as a candidate for one
specific step inside the existing architecture: replacing
`chat_intent_split.py`'s `split_segments` + `classify_segments` pair.

## 2. What GLiNER actually is

An open-source, zero-shot NER model (`fastino/gliner2.5-base-v1` tested
here, 194M params, Apache-2.0, self-hosted via the `gliner2` PyPI package).
"Zero-shot" means the caller supplies arbitrary label strings at inference
time — no fine-tuning per label set — and the model returns text spans
matched against those labels in one forward pass.

This is a genuinely different tool from Duckling (see the price-phrase
stripping discussion in this session's chat log): Duckling deterministically
parses a fixed grammar of quantities/dates/comparators; GLiNER is a learned
model that can be pointed at whatever label vocabulary a caller defines,
including a catalog's own category names.

## 3. Where it would fit — and where it explicitly would not

| Pipeline step | Candidate for GLiNER? | Why |
|---|---|---|
| `split_segments` + `classify_segments` (§5/§6 of the multi-intent doc) | **Yes, candidate** | Its per-call custom labels map naturally onto "this index's real category values" — the same live-schema philosophy `_category_vectors_for_index` already uses. Could plausibly do splitting *and* classification in one call. |
| `_resolve_device_filter` (device-name matching against `compatible_devices`) | **No** | GLiNER would recognize "iPhone 17" as a device-*shaped* string from general knowledge, but has no idea what canonical value *this catalog* uses. The Typesense lookup is still required regardless — GLiNER would at best hand it a candidate, not replace it. Same lesson as [[project_schema_resolution]]. |
| Index-time extraction (`device_compatibility_accessories.js`, `catalog_mapper.js`) | **No** | Different problem entirely — deterministic structured-field extraction from catalog titles, not free-text query parsing. Nothing about today's fix to that extension's Tier 1 pattern would be better served by a model. |
| `_extract_price_filter` / price-phrase stripping | **No** — see the Duckling discussion earlier in this session. Comparator+number extraction is a solved, narrow, deterministic problem; GLiNER adds a model dependency for something three regexes already handle correctly. |

## 4. The data supplied for this document, and why it's treated as a claim, not a finding

The following was supplied as the basis for this document (verbatim, from
an external source — not run in this session):

> If you pass `"airpods, iphone 17 case and screen protector"` into
> GLiNER... Option A `labels = ["product", "accessory"]`:
> ```json
> [{"start":0,"end":7,"text":"airpods","label":"product"},
>  {"start":9,"end":18,"text":"iphone 17","label":"product"},
>  {"start":19,"end":23,"text":"case","label":"accessory"},
>  {"start":28,"end":44,"text":"screen protector","label":"accessory"}]
> ```
> Option B `labels = ["brand", "device", "model", "accessory"]`:
> ```json
> [{"start":0,"end":7,"text":"airpods","label":"device"},
>  {"start":9,"end":15,"text":"iphone","label":"brand"},
>  {"start":16,"end":18,"text":"17","label":"model"},
>  {"start":19,"end":23,"text":"case","label":"accessory"},
>  {"start":28,"end":44,"text":"screen protector","label":"accessory"}]
> ```
> "No Information Loss: unlike Duckling... GLiNER extracts every meaningful
> word because it reads the contextual relations across the entire
> sentence."

**This is presented as a hypothetical ("the output depends on...", "yields:"),
not a verified run.** §6 below is what was actually measured, in this
session, against the real model. It does not match the above, and the
mismatch is itself part of this document's evidence.

## 5. What was actually run

Setup: isolated venv (`pip install "gliner2[local]"`), no changes to the
real project's dependencies. Model: `fastino/gliner2.5-base-v1`. Disk:
747MB downloaded. Cold load: 28s (once per process). Warm inference:
~100–200ms per call on CPU, after an initial ~1s first-call overhead.

### 5.1 The showcase query, with real category labels

```
query:  "apple airpods , iphone 17 case and screen protector"
labels: ["headphones", "cases and covers", "screen protectors", "chargers and adapters"]
→ headphones: [apple airpods], cases and covers: [iphone 17 case],
  screen protectors: [screen protector], chargers and adapters: []
```
Genuinely good: one 168ms call did both splitting and classification
correctly, including keeping `"iphone 17 case"` intact — the exact case the
embedding pipeline needed the `strip_phrases` fix for.

### 5.2 The generic labels from §4's supplied example, actually run

```
query:  "apple airpods , iphone 17 case and screen protector"
labels: ["brand", "product", "model_number", "accessory"]
→ {'brand': ['apple'], 'product': ['apple airpods', 'iphone'],
   'model_number': ['17'], 'accessory': ['screen protector']}
```
Compare against §4's claimed output: **"case" is missing entirely**, and
`"iphone 17"` is split into two separate spans/labels instead of the one
clean span the supplied example shows. The "no information loss" claim did
not hold under an actual run.

### 5.3 This project's own control cases (from [[multiIntentConjunctionSearchDesign.md]] §12), with real category labels

```
labels: ["headphones", "cases and covers", "screen protectors", "chargers and adapters"]

"wireless and durable case"        (Control C) -> everything empty — "case" not tagged at all
"case between $20 and $50"         (Control B) -> everything empty — same miss
"durable case for iphone 17"       (Control A) -> headphones:[iphone 17], screen protectors:[iphone 17]
                                                   "case" missed; device name mis-tagged into two
                                                   unrelated categories
```

**3 of 4 tested cases failed**, including two of our own named regression
controls. The failure mode isn't random noise — it's sensitive to word
order and phrasing in a way the deterministic `split_segments` +
`classify_segments` pipeline is not.

## 6. Decision

**Not adopted.** The showcase query (§5.1) is a genuine result and worth
keeping on file, but §5.2 and §5.3 show the model is not yet reliable
enough to replace a pipeline that currently passes 38/38 of its own tests
(including the exact Controls A/B/C it just failed here). Adopting it now
would trade a deterministic, fully-tested mechanism for a probabilistic one
with a known, reproduced failure mode on cases we already have named
regression tests for.

This is not a rejection of GLiNER in principle — §5.1 is real evidence it
can work — it's a statement that the bar for replacing already-tested,
already-shipped logic is "at least as reliable," and that bar isn't cleared
yet.

## 7. What would need to be true before reconsidering

- **Pass every existing control** in
  [[multiIntentConjunctionSearchDesign.md]] §12 (Controls A–G), run for
  real against `fastino/gliner2.5-base-v1` or a later checkpoint — not
  spot-checked, the full suite, ported to run against GLiNER's output shape.
- **Understand the phrasing sensitivity.** §5.3's Control A
  (`"durable case for iphone 17"`) failed while the near-identical §5.1
  query (`"...iphone 17 case..."`) succeeded — same words, different order,
  opposite outcome. That needs a root cause, not a shrug, before trusting it
  in production. (This is the same shape of bug this whole session has been
  chasing in the existing pipeline — a mechanism that's reliable on the
  query it was demoed with and not obviously reliable elsewhere.)
- **A specific, reproducible label-design methodology** — if per-label
  descriptions (not just bare strings) are supported by GLiNER2's
  schema-conditioned API and materially improve the Control A/B/C results,
  that changes this document's conclusion. Not yet tried.
- **A cost/latency comparison at realistic concurrency** — 100–200ms
  per call was measured single-threaded, cold-cache-excluded, on one
  machine. Not measured: behavior under the concurrent `asyncio.gather`
  load `_try_multi_intent_context` already runs (§11 of the multi-intent
  doc), or CPU contention with the rest of `isp-api` under real traffic.

## 8. If it does clear that bar — integration shape (not built)

Sketched here so a future pass has a starting point, not because it's
approved:

- **Decoupling boundary preserved.** `chat_intent_split.py` stays pure/
  injectable (per its own stated design). GLiNER inference is real I/O
  (loads a model, runs inference) and belongs in `chat/service.py`, injected
  in as a new callable — same pattern as `embed`/`confirm`/`resolve_span`
  today. It would **replace** the `embed` + `classify_segments` combination
  for indexes that opt in, not sit alongside them.
- **Labels are always this index's real category values** — never a
  generic label set like §5.2's. §5.1 is the only tested configuration that
  worked; §5.2 is the one the supplied reference material recommended and
  it's the one that failed. This document explicitly rejects using generic
  labels, based on measured evidence, regardless of what looks appealing in
  the abstract.
- **Fallback, not a hard dependency.** Given the demonstrated
  unreliability, any integration must fall back to the existing embedding
  classification path on a low-confidence or empty GLiNER result — never a
  single point of failure for the whole split.
- **New infra**: `gliner2[local]` (`torch` + `transformers`), a ~750MB
  model artifact baked into the image or fetched at startup into a cached
  volume, and container memory/CPU sizing adjusted for a resident
  transformer model — this is the *first* self-hosted model in a stack that
  today only calls external embedding/LLM APIs. That's a real operational
  step change, independent of whether the accuracy bar is cleared.

## 9. Shadow-mode rollout (if it ever ships)

Same pattern already specified for the multi-intent feature itself
([[multiIntentConjunctionSearchDesign.md]] §13) and used for the reverted
[[project_schema_resolution]] work before that: run GLiNER's classification
in parallel with the existing pipeline, log agreement/disagreement per
request, and let it affect zero live answers until the disagreement rate
and failure modes are understood on real traffic — not just the four
queries tested in §5.

## 10. Testing (if implementation proceeds)

- Every control in [[multiIntentConjunctionSearchDesign.md]] §12 (A
  through G), run against the real model — not a fake/mocked GLiNER
  response, since the point of this evaluation is the model's actual
  behavior, which has already proven surprising twice in this session.
- A fallback-path test: GLiNER unavailable, times out, or returns
  low-confidence — pipeline must fall back to the embedding classifier
  cleanly, with no user-visible degradation beyond what the embedding path
  already produces today.
- A regression test locking in §5.2's finding — generic labels are not an
  acceptable configuration — so a future change can't silently reintroduce
  the pattern this document rejected.

## 11. Files expected to change (only once §7's bar is cleared — none yet)

| File | Change |
|---|---|
| `backend/pyproject.toml` | New `gliner2[local]` dependency (pulls in `torch`, `transformers`) |
| `backend/Dockerfile` | Model artifact caching/baking strategy; image size and build time both increase materially |
| `backend/src/isp/services/domain/chat_intent_split.py` | New injected parameter (e.g. `classify_fn`) as an alternative to `embed` + `classify_segments`, gated per index |
| `backend/src/isp/services/infra/chat/service.py` | GLiNER model load/lifecycle, the injected classify function, fallback-to-embedding logic |
| Tests | Full Control A–G suite ported to run against real GLiNER output; fallback-path tests |

## 12. Open questions

- Does GLiNER2's schema-conditioned API support per-label descriptions
  (not just bare label strings), and does that close the Control A/B/C gap?
  Not tried in this session.
- Is the phrasing sensitivity in §5.3 specific to the `base` checkpoint
  tested, or does it persist in the `multi` (287M) or a future larger
  checkpoint?
- What's the actual latency and failure behavior under this deployment's
  real concurrency pattern, not a single-threaded benchmark script?
