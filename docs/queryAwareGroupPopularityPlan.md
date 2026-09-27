# Query-Aware Popularity Ranking Plan

## Goal

Use learned popularity to improve ordering without allowing a popular but weakly
related product to outrank a strong query match.

Expected behavior:

- `iphone 17` returns matching iPhones first.
- Samsung and other alternatives may remain visible lower in the results.
- Broad queries such as `samsung` may use popularity more strongly among their
  relevant results.
- Empty-query browse remains popularity-driven.
- Human merchandising controls may intentionally cross relevance boundaries;
  learned popularity may not.

## Current Limitation

For non-empty queries, the current implementation normalizes popularity against
the most-popular document in the retrieved candidate set:

```text
pop_norm = document_popularity / max_popularity_in_result_set
final_score = relevance_score * (1 + 0.10 * pop_norm)
```

The multiplicative bound is safer than the former additive boost, but it has two
limitations:

1. A coefficient alone cannot guarantee that a popular alternative stays below
   a strong lexical match.
2. `max_popularity_in_result_set` is page- and candidate-dependent. The same
   document's adjustment can change when `per_page`, filters, or retrieval recall
   changes.

The first release should solve the ranking guarantee with two relevance tiers.
Query-group inference and stable offline cohort percentiles are follow-up
refinements that must earn their complexity through evaluation results.

> **Shipped early (2026-09) — the "exact match" slice of Tier 1a.** Before the
> full tier work, a narrower version of the guarantee is live in
> `apply_editorial_boosts()`: a candidate whose normalized title *or* any alias
> **equals the query** is given `pop_norm = 1.0`, so a more-popular sibling
> variant can't out-boost it (`"iphone 17"` keeps "iPhone 17" above "iPhone 17
> Pro Max"). It is *not* a hard tier — base relevance still decides between an
> exact and a partial match — but it covers the most common failure without the
> lexical-evidence spike or the candidate-structure refactor. The full two-tier
> design below still stands for partial-match / semantic-only protection.

## V1: Resolver-Free Two-Tier Ranking

### Blocking spike: validate the Tier 1 signal

Do not implement tiering until a focused spike establishes a trustworthy
full-query-match signal on the pinned Typesense server version (`0.25.2` in
`docker-compose.yml` and `docker-compose.prod.yml`). In that version,
`text_match_info.tokens_matched` may describe the best single matching field
rather than matches aggregated across every `query_by` field. For example, a
document could match `iphone` in `title` and `17` in `aliases` while reporting
only one token for its best field.

The spike must run real Typesense searches and compare these possible rules:

1. `tokens_matched == query_token_count`;
2. `num_tokens_dropped == 0`;
3. aggregation across `text_match_info.fields_matched`, if the returned metadata
   contains enough per-field evidence;
4. a conservative combination of these signals.

Cover split-field matches, exact matches, aliases, stop words, punctuation,
prefix behavior, and typo-tolerant queries. V1 should count an accepted typo as
a full match—for example, `ifone 17` may enter Tier 1 when Typesense matches both
tokens under the configured `num_typos: 2` policy—but the spike must verify and
encode that decision explicitly.

Also verify that `text_match_info` survives each `multi_search` sub-response in
`catalog._rrf_search`, including requests that set `exclude_fields`. Capture the
observed response fixtures in tests so future Typesense upgrades expose semantic
changes.

The output of this spike is the final `is_full_lexical_match(hit, query)` rule.
If none of the available metadata supports a reliable rule, stop and redesign
the tier signal rather than approximating it with a fragile threshold.

### Ranking flow

```text
query
  -> retrieve lexical and semantic candidates
  -> preserve lexical-match evidence
  -> assign candidates to Tier 1 or Tier 2
  -> apply popularity only within each tier
  -> sort by tier first, adjusted score second
```

Do not hard-filter by manufacturer. Alternatives should remain eligible.

### Concrete Tier 1 rule

Request `text_match_info` from Typesense and retain it for every lexical hit.
Normalize the user query into the same token form used for matching. The initial
hypothesis to validate in the blocking spike is:

```text
Tier 1 = appeared in lexical pass
         AND text_match_info.tokens_matched == query_token_count

Tier 2 = every other retrieved candidate
```

Do not ship this exact expression until the spike determines whether
`num_tokens_dropped`, field aggregation, or a combined predicate is required.
Use the resulting metadata-based predicate rather than a hard-coded score
threshold.

Examples:

```text
Query: iphone 17

Tier 1: documents whose lexical hit matched both "iphone" and "17"
Tier 2: partial lexical matches and semantic-only alternatives, including Samsung
```

```text
Query: samsung

Tier 1: Samsung documents whose lexical hit matched "samsung"
Tier 2: semantic-only alternatives
```

This example depends on indexed aliases. Phone titles such as
`Galaxy Z Fold8 Ultra` do not contain `samsung`; `catalog_mapper.js` supplies an
alias such as `samsung galaxy z fold8 ultra`, and `aliases` is included in
`query_by`. Collections created before the aliases field was introduced can hit
the existing retry path that removes `aliases`, causing every Samsung result to
fall into Tier 2. The spike must verify the live phones schema and alias coverage.
A rebuild or explicit fallback policy is required before using brand queries as
an acceptance test.

If Tier 1 is empty, Tier 2 naturally supplies the complete result list. This is a
ranking boundary, not a retrieval filter.

### Tier precedence

Popularity may reorder candidates within a tier but may not move a document
between tiers:

```python
rank_key = (effective_tier, pinned_position, -adjusted_score)
```

Use `effective_tier = 0` as a pseudo-tier for pinned results and query-fired
merchandising overrides. Ordinary learned-ranking candidates remain in Tier 1 or
Tier 2. Pinned position takes precedence inside Tier 0; configured merchandising
strength and adjusted score order the remaining override candidates.

This boundary—not coefficient tuning—guarantees that a maximum-popularity
Samsung cannot rank above a full-token lexical match for `iphone 17`.

### V1 popularity normalization

For the first increment, normalize popularity independently within each tier:

```text
tier_pop_norm = document_popularity / max_popularity_in_tier
adjusted_score = relevance_score * (1 + 0.10 * tier_pop_norm)
```

This is intentionally an intermediate design. It is inexpensive and the tier is
already query-relevant, so it approximates cohort-relative normalization. It
remains dependent on the retrieved candidates and does not yet satisfy the
long-term page-independence goal.

Do not increase the coefficient as part of the initial implementation. First
verify the tier boundary, then compare `0.10`, `0.20`, and `0.25` using the
query regression set.

### Browse behavior

Empty queries have no query-token evidence and do not use relevance tiers. Keep
the existing browse policy:

```text
popularity_bonus = min(popularity, 1_000_000) / 2_000_000
```

Typesense may continue sorting the full browse collection by editorial fields so
pagination remains correct.

Preserve the two existing browse mechanisms deliberately:

- RRF-backed browse paths (`search_with_vec` and `catalog._rrf_search`) can apply
  the additive `popularity / 2_000_000` score.
- Single-index `search_documents` browse sorts the full collection natively in
  Typesense with `is_featured:desc,popularity:desc,_text_match:desc`, avoiding
  page-local reranking.

## Merchandising Precedence

Separate operator decisions from learned popularity.

The following are human merchandising controls and may intentionally cross tier
boundaries:

- pinned results;
- `is_featured`;
- `boost_queries_equal`;
- `boost_queries_contains`;
- per-index `boost_rules`, including rules such as an Apple field boost.

The current `boost_rules` implementation adds as much as `+1.0` to an
RRF-scale score. That is a separate additive-scale concern and should be
evaluated independently. For this plan, document the intended precedence:

`boost_rules` are mode-conditional. For a non-empty query, only rules containing
a `__search_term__` trigger fire. Field-only rules, such as a manufacturer-based
Apple boost, fire only during browse and do not participate in an `iphone 17`
query. Therefore, only the search-triggered subset can enter the Tier 0 override
path during query ranking.

```text
pinned/query-fired operator override (Tier 0)
  -> relevance tier
  -> popularity-adjusted within-tier score
```

If product requirements do not intend a particular merchandising control to
override relevance tiers, classify it accordingly before implementation. Do not
let hook execution order decide the policy accidentally.

## V1 Implementation Steps

### 1. Preserve lexical evidence

In the hybrid RRF path, retain for each candidate:

- whether it appeared in the lexical pass;
- lexical rank;
- semantic rank;
- `text_match_info`, including `tokens_matched`;
- base RRF score before editorial or context adjustments.

Use a small internal candidate structure instead of reducing retrieval evidence
immediately to `scores` and `all_docs`. Keep public API responses backward
compatible.

The existing `post_rrf_hook(scores, all_docs)` signature is a cross-module
contract used by customer-context scoring and `apply_boost_rules`. Preserve that
interface by adapting the richer candidate structure at the hook boundary. If a
signature change becomes necessary, update every hook producer and caller in the
same change and add integration coverage; personalization and boost rules must
not silently stop running.

Primary locations:

- `backend/src/isp/services/infra/typesense_client.py`
- `backend/src/isp/routers/catalog.py`

### 2. Assign two tiers

For non-empty queries:

1. Count normalized query tokens.
2. Put full-token lexical matches in Tier 1.
3. Put partial lexical and semantic-only candidates in Tier 2.
4. Fall back to Tier 2-only ordering when Tier 1 is empty.

Keep this rule resolver-free. Manufacturer, OS, form-factor, price-intent, and
model-family inference are not prerequisites for v1.

### 3. Apply popularity within each tier

1. Compute the maximum popularity separately for Tier 1 and Tier 2.
2. Apply the existing `0.10` multiplicative adjustment using the tier maximum.
3. Sort within each tier by adjusted score.
4. Concatenate Tier 1 before Tier 2.
5. Apply documented operator overrides at their explicit precedence level.

### 4. Share the behavior across search paths

Put tier assignment and within-tier popularity in one shared reranker used by:

- lexical search;
- hybrid/semantic RRF search;
- multi-index catalog search.

Keep empty-query browse on its separate policy.

### 5. Add observability

In debug output or structured logs, record:

- lexical membership and rank;
- semantic membership and rank;
- query-token count and `tokens_matched`;
- assigned tier and reason;
- base relevance score;
- tier popularity denominator and normalized popularity;
- adjusted score;
- applied merchandising rules.

Roll out behind a feature flag such as:

```text
query_relevance_tiers_enabled
```

Persist and expose this flag through `app_settings` in `settings_service.py` and
the settings router, following the existing customer-context and `cc_*` toggle
pattern.

Use offline replay, shadow evaluation, and then a limited A/B test.

## V1 Test Plan

### Unit and integration tests

Update the existing assertions in
`backend/tests/services/infra/test_editorial_boosts.py`, which currently assume a
single candidate-set `max_pop`. In particular, revise the capped-lift and
tie-breaking cases to exercise per-tier denominators, then add the tier-specific
tests below.

1. A lexical hit matching all tokens in `iphone 17` enters Tier 1.
2. A partial lexical hit enters Tier 2.
3. A semantic-only Samsung enters Tier 2 and remains visible.
4. A maximum-popularity Tier 2 Samsung cannot outrank a Tier 1 iPhone.
5. Popularity reorders equally relevant documents within Tier 1.
6. Popularity reorders documents within Tier 2 without crossing the boundary.
7. `samsung` places full-token Samsung lexical hits in Tier 1.
8. A query with no full-token lexical hit falls back safely to Tier 2.
9. Empty-query browse retains strong popularity ordering.
10. Pinned results and every configured human merchandising control follow their
    documented precedence.
11. Lexical, hybrid, and catalog paths apply the same two-tier policy.
12. Token counting behaves correctly for punctuation, stop words, aliases, and
    typo-tolerant matches.

### Query regression set

```text
Specific:
  iphone 17
  galaxy s25 ultra
  pixel 11 pro

Broad brand:
  samsung
  apple
  motorola

Attribute/category:
  foldable phone
  android phone
  budget phone
  premium phone

Accessories and ambiguous intent:
  iphone 17 case
  galaxy case
  pro phone
  newest device
  good camera
```

For each query, record:

- expected Tier 1 documents;
- unexpected documents admitted to Tier 1;
- wrong-brand products in the top three;
- popularity-driven movements within tiers;
- cross-tier movements, which must be zero unless caused by a documented human
  override;
- result diversity below Tier 1.

V1 does not have a labeled relevance corpus, so its hard offline gate is zero
learned-popularity cross-tier movements plus manual review of the regression
queries. Building a small judgment set (approximately 20 queries with the top 10
results graded) and calculating NDCG@10 is an evidence-gated follow-up.

## V1 Acceptance Criteria

1. `iphone 17` and other specific queries place full-token lexical matches above
   partial or semantic-only alternatives.
2. Other-brand alternatives remain available below Tier 1.
3. Learned popularity causes zero cross-tier movements.
4. Empty-query browse remains popularity-driven.
5. Lexical, hybrid, and catalog endpoints behave consistently.
6. Debug information explains every tier and popularity adjustment.
7. Every human merchandising mechanism has an explicit, tested precedence rule.

When Tier 1 is empty, `found` and facet counts must continue describing the full
retrieved result set; only hit ordering changes. The same invariant applies when
Tier 1 is non-empty.

Page-independent popularity normalization is a long-term acceptance criterion,
not a v1 blocker. Track it explicitly while v1 uses within-tier candidate
normalization.

## Evidence-Gated Follow-Ups

The following work is not required for v1. Implement it only when evaluation or
production diagnostics demonstrate the need.

### Follow-up A: Deterministic query-group resolver

Potentially recognize affinity values such as manufacturer, operating system,
form factor, category, and price tier. Treat all inferred groups as advisory. A
low-confidence inference must never create a hard relevance tier.

This is a mini-NLU system with significant ambiguity:

- shared tokens such as `pro`, `ultra`, and `plus`;
- `galaxy` as a brand/family signal;
- accessories such as `iphone 17 case`, where Apple describes compatibility but
  not the accessory manufacturer;
- synonyms and values shared across catalogs.

Evaluate the resolver independently with labeled queries before allowing it to
influence tier membership. Initially use it only to select a popularity cohort
or coefficient.

If justified, specificity classes may become:

| Specificity | Popularity policy |
|---|---|
| Product/model | Popularity only inside Tier 1; off outside Tier 1 |
| Attribute/brand | Moderate popularity within matched groups |
| Browse | Popularity is a primary signal |

### Follow-up B: Third relevance tier

Add a third tier only if evaluation shows a useful, reliable distinction between
related alternatives and weak semantic matches. This likely depends on a
validated resolver or calibrated lexical evidence.

Until then:

```text
Tier 1 = full-token lexical match
Tier 2 = everything else retrieved
```

### Follow-up C: Stable offline cohort percentiles

If candidate-dependent normalization causes measurable instability, compute
offline popularity percentiles for useful cohorts:

```text
manufacturer=samsung
operating_system=android
form_factor=fold
manufacturer=samsung + form_factor=fold
```

Prefer the most specific cohort with sufficient observations and fall back:

```text
manufacturer + form_factor
-> manufacturer
-> category
-> global
```

Possible storage designs include a compact side cache or promoted document
fields. Avoid adding one Typesense field per dimension before measuring the
benefit and operational cost. Require a minimum cohort size and version the
statistics.

This follow-up removes the current page-dependence wart: a document's popularity
percentile becomes stable regardless of candidate count, pagination, or filters.

### Follow-up D: Query-specific coefficients

After tier protection is proven, compare `0.10`, `0.20`, and `0.25` for broad
queries. Do not assume a larger coefficient improves quality; use relevance
judgments and online metrics.

### Follow-up E: Labeled relevance evaluation

Create a compact judgment corpus of roughly 20 representative queries with the
top 10 candidates graded for relevance. Use it to compare NDCG@10 across the
baseline, two-tier ranking, later query-group inference, and alternative
coefficients. Until that corpus exists, do not present NDCG as a v1 gate.

## Risks and Mitigations

### Incorrect full-token classification

Validate Typesense token semantics and log classification reasons. Fall back to
Tier 2 when evidence is incomplete instead of guessing.

### Overly large Tier 1

Measure Tier 1 size by query class. A broad query may legitimately produce a
large Tier 1; popularity can then reorder within that relevant set.

### Popularity feedback loops

Continue correcting click data for position bias. Monitor whether promoted
products accumulate popularity because the system already promoted them.

### Endpoint behavior drift

Keep tier assignment and popularity reranking in a shared component rather than
duplicating logic across endpoints.

### Resolver scope expansion

Keep the resolver out of v1. If added later, require labeled-query evaluation and
use low-confidence output only as advisory metadata.

## Recommended First Increment

Ship only this scope initially:

1. Run the blocking Typesense `0.25.2` spike and lock the selected full-match
   predicate into response-fixture tests.
2. Verify the phones alias schema/data and the catalog multi-search metadata.
3. Preserve lexical membership, rank, and `text_match_info` during RRF while
   maintaining the `post_rrf_hook(scores, all_docs)` contract.
4. Create two tiers using the spike-validated full-token lexical-match rule.
5. Normalize popularity within each tier and apply the existing `0.10`
   multiplicative adjustment.
6. Make operator-override precedence explicit, including per-index
   `boost_rules`.
7. Share the reranker across lexical, hybrid, and catalog paths.
8. Update existing editorial-boost tests and add regressions for `iphone 17`,
   `samsung`, empty browse, hook compatibility, and merchandising precedence.
9. Add the feature flag through the existing `app_settings` pattern.
10. Release behind the flag and measure before building a resolver, third tier,
    offline cohort infrastructure, or judgment corpus.

This increment delivers the required guarantee: alternatives remain visible,
but learned popularity cannot place them above products that fully match the
query.
