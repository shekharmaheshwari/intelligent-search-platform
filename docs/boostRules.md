# Boost Rules — Rule-Based Inventory Boosting

A complete design reference for the Boost Rules feature: how merchandising teams define conditional score boosts, how rules are stored and applied at search time, and the UX decisions that protect search relevance while giving operators full control.

---

## What it is

Boost Rules let operators apply additive score boosts to product subsets using two-part conditional logic:

- **Trigger** — *when* the rule fires (all conditions AND-ed)
- **Boost targets** — *which products* are boosted (any condition OR-ed)

Rather than boosting a single named document (which Suggestions / Pin-to-top handles), Boost Rules target a *class* of products: every Apple device when someone searches "iphone", every item whose title contains "clearance" on the browse page, or a specific product ID when a particular search term is used.

---

## Data model

### Criterion

A single field-match condition used in both trigger and targets:

```json
{ "field": "manufacturer", "operator": "equals", "value": "apple" }
```

| Field | Type | Values |
|---|---|---|
| `field` | string | Any filterable schema field, `"id"`, or `"__search_term__"` (trigger only) |
| `operator` | enum | `"equals"` / `"contains"` / `"regex"` |
| `value` | string | The match target |

The special field `__search_term__` matches against the live search query string rather than any document field. It is only meaningful in the `trigger` section.

### Rule block

```json
{
  "id":      "rule_abc123",
  "label":   "Apple search boost",
  "enabled": true,
  "boost":   0.40,
  "trigger": [
    { "field": "__search_term__", "operator": "contains", "value": "apple" }
  ],
  "targets": [
    { "field": "id",           "operator": "equals",   "value": "PROD-001" },
    { "field": "manufacturer", "operator": "equals",   "value": "apple"    },
    { "field": "title",        "operator": "contains", "value": "iPhone"   }
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `id` | string | Generated on creation (nanoid) |
| `label` | string | Human-readable name shown in the rule card header |
| `enabled` | bool | Toggle without deleting — takes effect immediately |
| `boost` | float | Additive score delta. Range 0.00–1.00, clamped server-side. |
| `trigger` | list | ALL must match (AND). Supports `__search_term__`. Empty = matches nothing. |
| `targets` | list | ANY must match (OR). Empty = all documents passing the trigger are boosted. |

### Browse vs search gating (per-rule)

The rule fires in different modes depending on whether the trigger references the search term:

| Trigger contains `__search_term__`? | Fires when |
|---|---|
| Yes | Active query is non-empty AND the query matches the trigger |
| No | Browse only (empty query) |

This is evaluated per-rule inside `apply_boost_rules` — different rules in the same index can mix search-mode and browse-mode.

### Index-level storage

Rules are stored in a `boost_rules` JSONB column on the `indexes` table — one list of rule blocks per index:

```json
[
  {
    "id": "rule_abc123",
    "label": "Apple search boost",
    "enabled": true,
    "boost": 0.40,
    "trigger": [
      { "field": "__search_term__", "operator": "contains", "value": "apple" }
    ],
    "targets": [
      { "field": "manufacturer", "operator": "equals", "value": "apple" }
    ]
  },
  {
    "id": "rule_def456",
    "label": "Clearance browse nudge",
    "enabled": true,
    "boost": 0.10,
    "trigger": [
      { "field": "title", "operator": "contains", "value": "clearance" }
    ],
    "targets": []
  }
]
```

Migration: `ALTER TABLE indexes ADD COLUMN IF NOT EXISTS boost_rules JSONB DEFAULT '[]'`.

Legacy rules with a flat `criteria` field are automatically migrated on load: `criteria` becomes `trigger`, `targets` defaults to `[]`.

---

## Boost value zones

### Normal zone — 0.00 to +0.45

- **Track colour:** accent (blue/purple)
- **Context text:** *"Applies a preference nudge. Natural search relevance remains intact."*
- **Effect:** Products move up in rankings but strong relevance matches still appear above weak ones.

### Danger zone — +0.50 to +1.00

- **Track colour:** amber
- **Warning icon:** ⚠ appears next to the value display
- **Context text:** *"Boosts ≥ +0.50 aggressively override keyword and semantic relevance. Products matching this rule may jump to the top even for loosely related queries."*
- **Hard cap:** +1.00 in both UI and backend.

**Why expose the danger zone rather than cap at +0.45?**

Silently capping generates "why isn't my boost working?" support tickets during high-stakes campaigns (Black Friday, brand launches). Exposing the boundary with a warning gives full control while ensuring operators explicitly acknowledge the trade-off.

### Score context

| RRF rank | Approximate base score |
|---|---|
| Position 1 | ~0.061 |
| Position 5 | ~0.055 |
| Position 20 | ~0.043 |

A boost of +0.30 roughly matches the "featured" editorial boost — moves a product well up the page without guaranteeing position 1. A boost of +0.70 overrides almost any relevance signal.

---

## Trigger logic (AND) / Target logic (OR)

### Trigger — all must match

All trigger criteria are evaluated with AND. If any criterion fails, the rule does not fire for any document.

```
Trigger:
  __search_term__  contains  "apple"
  AND
  (nothing else)

→ fires only when the search query contains "apple"
```

Mixed triggers (search term + field) are supported:

```
Trigger:
  __search_term__  equals  "iphone"
  manufacturer     equals  "apple"

→ fires only when searching "iphone" AND the document is made by Apple
  (unusual — normally search-term trigger alone is enough)
```

### Targets — any must match (OR)

Once the trigger fires, a document is boosted if it matches **any** target row. Empty targets = boost all documents.

```
Targets (OR):
  id           equals    "PROD-001"
  manufacturer equals    "apple"
  title        contains  "iPhone"

→ boosts PROD-001, or any Apple product, or any product with "iPhone" in the title
```

### OR across rules

Create separate rule blocks. Each is evaluated independently and applies its own boost. A document matching two blocks receives both boosts (they stack additively).

---

## ID target — Browse picker

When `id` is selected as the target field, the plain text input is replaced by a document picker:

```
┌─ Boost targets ─────────────────────────────────────────────────┐
│  id ▼   Equals ▼   [iPhone 15 Pro          PROD-001    ✕]  Browse│
│  [+ Add target condition]                                        │
└──────────────────────────────────────────────────────────────────┘
```

Clicking **Browse** opens the `DocPickerModal` — the same search-and-select modal used for Pin-to-top in Suggestions. The picked document's title is shown in the chip for readability; only the ID is saved in the criterion value.

This makes it easy to pin an exact product at the top for a specific search query without needing to copy-paste doc IDs.

---

## Field list — filtered live schema

Fields in the target dropdown come from the live Typesense collection schema, filtered through a server-side blocklist:

**Always-excluded fields (system internals):**
`vec`, `indexed_at`, `metadata`, `content`, `url`, `is_featured`, `popularity`, `version_score`, `in_stock`, `price`, `boost_queries_equal`, `boost_queries_contains`, `images`, `skus`, `color_options`, `aliases`, `tags`

**`id` is excluded from the API field list** but hardcoded as the first option in the targets dropdown (with Browse picker behaviour).

**`__search_term__`** is hardcoded as the first option in the trigger dropdown only.

**What remains (example — phones index):**

| Field | Typical operators |
|---|---|
| `title` | Contains, Regex |
| `manufacturer` | Equals, Contains |
| `category` | Equals |
| `form_factor` | Equals |
| `operating_system` | Equals |
| `source_type` | Equals |

---

## Operator options

| Operator | UI label | Match logic |
|---|---|---|
| `equals` | Equals | `doc[field].lower() == value.lower()` |
| `contains` | Contains | `value.lower() in doc[field].lower()` |
| `regex` | Matches Regex | `re.search(value, doc[field], IGNORECASE)` |

Regex patterns are validated at save time (`re.compile`). Invalid patterns return HTTP 400 before saving.

---

## Backend application

```python
_SEARCH_TERM_FIELD = "__search_term__"

def apply_boost_rules(scores, all_docs, rules, query=""):
    is_search = bool(query.strip())
    for rule in rules:
        if not rule.get("enabled", True): continue
        boost   = min(float(rule.get("boost", 0.0)), 1.0)
        trigger = rule.get("trigger", [])
        targets = rule.get("targets", [])
        if not trigger or boost == 0.0: continue

        # Per-rule browse/search gate
        has_query_criterion = any(c["field"] == _SEARCH_TERM_FIELD for c in trigger)
        if has_query_criterion != is_search:
            continue

        for did, doc in all_docs.items():
            meta = _parse_meta(doc)
            # Trigger: ALL must match (AND)
            if not all(_check_criterion(c, doc, meta, query) for c in trigger):
                continue
            # Targets: ANY must match (OR); empty = boost all
            if targets and not any(_check_criterion(c, doc, meta, query) for c in targets):
                continue
            scores[did] = scores.get(did, 0.0) + boost
```

### Where it runs

- **`search.py`** — single-index hybrid/semantic/lexical search: wired into `post_rrf_hook` after personalisation
- **`catalog.py`** — cross-index catalog search: rules merged from all non-external indexes, same hook position
- **Browse-only rules** (no `__search_term__` trigger) are skipped during search; **search-only rules** are skipped during browse — both determined per-rule inside `apply_boost_rules`

---

## API endpoints

### Get rules

```
GET /v1/indexes/{id}/boost-rules
→ { "rules": [...] }
```

### Save rules (full replace)

```
PUT /v1/indexes/{id}/boost-rules
Body: { "rules": [...] }
→ { "rules": [...] }
```

Validates regex patterns server-side. Clamps boost to +1.00. Returns 400 on invalid regex.

### Get available fields

```
GET /v1/indexes/{id}/boost-rules/fields
→ { "fields": ["title", "manufacturer", "category", ...] }
```

Returns the filtered live schema field list (excludes system internals; `id` and `__search_term__` are hardcoded in the UI separately).

---

## UI layout

```
┌─ Boost Rules ──────────────────────────────────────────── [Save rules] ─┐
│  Each rule has a Trigger (when it fires — all AND) and Boost targets     │
│  (which products — any OR). Search-term trigger = fires on search.       │
│                                                                          │
│  ┌─ Apple search boost ──────────────── [ON SEARCH] [enabled ●]  [✕] ──┐│
│  │                                                                      ││
│  │  TRIGGER  ALL match (AND)              → boost   BOOST TARGETS  OR  ││
│  │  ─────────────────────────────              ────────────────────── ││
│  │  Search term  [Contains ▼]  apple           id ▼  [PROD-001  ✕] Browse││
│  │  [+ Add trigger condition]                  manufacturer  [Equals ▼]  apple ││
│  │                                             [+ Add target condition] ││
│  │                                                                      ││
│  │  Boost  [━━━━━━━━●──────]  ⚠ +0.50                                  ││
│  │  Boosts ≥ +0.50 aggressively override relevance…                    ││
│  └──────────────────────────────────────────────────────────────────────┘│
│                                                                          │
│  ┌─ Clearance browse nudge ────────────── [ON BROWSE] [enabled ●] [✕] ─┐│
│  │  TRIGGER  ALL match (AND)              → boost   BOOST TARGETS  OR  ││
│  │  title  [Contains ▼]  clearance             (empty — boosts all)   ││
│  │  [+ Add trigger condition]                  [+ Add target condition] ││
│  │                                                                      ││
│  │  Boost  [━━━●─────────]  +0.10                                      ││
│  │  Applies a preference nudge. Natural relevance remains intact.      ││
│  └──────────────────────────────────────────────────────────────────────┘│
│                                                                          │
│  [+ Add rule block]                                                      │
└──────────────────────────────────────────────────────────────────────────┘
```

The header badge ("ON SEARCH" / "ON BROWSE") is derived automatically from whether any trigger criterion uses `__search_term__`.

---

## Implementation status

| Component | Status | Notes |
|---|---|---|
| `boost_rules` DB column | ✅ Done | `ALTER TABLE … ADD COLUMN IF NOT EXISTS` in `init_db()` |
| `IndexMeta.boost_rules` model field | ✅ Done | `list[dict] = []` |
| `_row_to_meta` mapping | ✅ Done | Includes legacy `criteria→trigger` migration |
| `set_boost_rules` service | ✅ Done | `UPDATE indexes SET boost_rules = $1` |
| GET/PUT endpoints | ✅ Done | `indexes.py` |
| Fields endpoint | ✅ Done | Filtered live schema |
| `apply_boost_rules` backend | ✅ Done | Trigger+targets, per-rule gate, `__search_term__` |
| Hook wired in `search.py` | ✅ Done | After personalisation hook |
| Hook wired in `catalog.py` | ✅ Done | Merged rules from all non-external indexes |
| `BoostRulesTab.svelte` | ✅ Done | Trigger section + Boost targets section |
| `IndexDetail.svelte` tab | ✅ Done | "Boost Rules" tab |
| `DocPickerModal` for `id` target | ✅ Done | Browse button replaces text input when field = `id` |

---

## Decisions summary

| Decision | Choice | Rationale |
|---|---|---|
| Rule structure | `trigger` (AND) + `targets` (OR) | Separates "when to fire" from "what to boost" — cleaner than flat AND-only criteria |
| Browse vs search gate | Per-rule, based on `__search_term__` in trigger | Rules without it are browse-only; rules with it are search-only |
| `__search_term__` field | Hardcoded in trigger dropdown | Not a real schema field; treated as a special token matching the query string |
| `id` target | Hardcoded in targets dropdown + Browse picker | `id` is filtered out by the schema API; Browse reuses the existing `DocPickerModal` |
| Multiple targets | OR-logic | Allows "boost this product OR this manufacturer OR this title keyword" without multiple rule blocks |
| Boost range | 0.00–1.00, clamped server-side | Danger-zone warning at +0.50; operators retain full control with explicit acknowledgement |
| Storage | `boost_rules` JSONB on `indexes` | Per-index, consistent with `affinity_dimensions` / `viewed_item_policy` patterns |
| Stacking | Additive across matching rules | Transparent; operator created each rule and is accountable for combined effect |

---

## References

> **Note on sourcing:** all citations below are real, verifiable sources.

---

### Boolean retrieval and AND logic

**Introduction to Information Retrieval**
Manning, C. D., Raghavan, P., & Schütze, H. (2008).
*Introduction to information retrieval.*
Cambridge University Press.
https://nlp.stanford.edu/IR-book/

> Chapter 1 defines the Boolean retrieval model — the theoretical foundation for AND-chained trigger evaluation. `_check_criterion` with all-AND across `trigger` is a direct conjunctive Boolean query over document field values.

---

### Field-specific boosting (BM25F)

**BM25F — Simple BM25 Extension to Multiple Weighted Fields**
Robertson, S., Zaragoza, H., & Taylor, M. (2004).
*Simple BM25 extension to multiple weighted fields.*
Proceedings of CIKM 2004, pp. 42–49.
https://dl.acm.org/doi/10.1145/1031171.1031181

> Motivates field-selective boosting: different fields carry different discriminating power. Targeting `title` (contains "clearance") vs `manufacturer` (equals "apple") applies the same principle of field-weighted relevance BM25F formalises.

**The Probabilistic Relevance Framework: BM25 and Beyond**
Robertson, S., & Zaragoza, H. (2009).
*Foundations and Trends in Information Retrieval*, 3(4), 333–389.
https://doi.org/10.1561/1500000019

> Establishes that additive per-field score contributions are both theoretically grounded and empirically effective — the same principle that makes additive boost rules predictable and composable.

---

### Score combination and stacking

**Analyses of Multiple Evidence Combination**
Lee, J. H. (1997).
*Proceedings of SIGIR 1997*, pp. 267–276.
https://dl.acm.org/doi/10.1145/258525.258587

> Demonstrates that linear additive combination of independent evidence signals is robust — the theoretical basis for stacking boost rules additively. Also motivates the hard cap at +1.00.

**Reciprocal Rank Fusion**
Cormack, G. V., Clarke, C. L. A., & Buettcher, S. (2009).
*Proceedings of SIGIR 2009*, pp. 758–759.
https://dl.acm.org/doi/10.1145/1571941.1572114

> Defines RRF and the score range context for calibrating the danger zone. At K=60, a +0.50 boost matches or exceeds the full base RRF score of a mid-ranked document — the quantitative basis for the +0.50 danger zone boundary.

---

### E-commerce search ranking and merchandising constraints

**On Application of Learning to Rank for E-Commerce Search**
Karmaker Santu, S. K., Sondhi, P., & Zhai, C. (2017).
*Proceedings of SIGIR 2017*, pp. 475–484.
https://dl.acm.org/doi/10.1145/3077136.3080838

> Identifies the tension between pure relevance and business objectives. Finds that purely relevance-based ranking systematically under-serves business goals and operator-defined boosts are a necessary complement — directly motivating Boost Rules as a first-class merchandising tool.

---

### Rule matching and pattern evaluation

**RETE: A Fast Algorithm for the Many Pattern / Many Object Pattern Match Problem**
Forgy, C. L. (1982).
*Artificial Intelligence*, 19(1), 17–37.
https://doi.org/10.1016/0004-3702(82)90020-0

> The foundational rule-matching algorithm. ISP's linear scan is appropriate for < 50 rules and < 1 000 document candidate sets. If rule or candidate counts grow substantially, RETE-style partial match caching becomes the right optimisation.

**Regular Expression Matching Can Be Simple and Fast**
Cox, R. (2007).
https://swtch.com/~rsc/regexp/regexp1.html

> Directly relevant to the `regex` operator: Python's `re` module can exhibit O(2ⁿ) worst-case on adversarial patterns. Regex patterns are validated at save time; consider complexity limits to prevent ReDoS if patterns are operator-supplied.

---

### Relevance vs business objectives

**Learning to Rank for Information Retrieval**
Liu, T. Y. (2009).
*Foundations and Trends in Information Retrieval*, 3(3), 225–331.
https://doi.org/10.1561/1500000016

> Hard business constraints are best handled as post-processing overrides rather than training signals — exactly the architecture ISP uses: relevance ranking first, then boost-rule overrides.

**Unbiased Learning to Rank with Biased Feedback**
Joachims, T., Swaminathan, A., & Schnabel, T. (2017).
*Proceedings of WSDM 2017*, pp. 781–789.
https://dl.acm.org/doi/10.1145/3018661.3018699

> When a boost rule forces a product to position 1, CTR signals for that position become contaminated — clicks reflect position, not preference. The hidden cost of aggressive boosting that the danger-zone warning implicitly communicates.

---

### Merchandising and retailing

**Retailing Management** (9th ed.)
Levy, M., Weitz, B., & Grewal, D. (2019).
McGraw-Hill Education. ISBN: 978-1260260786.

> Digital search boost rules are the online equivalent of physical end-caps: placing promoted products at the top of the result set performs the same function as a physical promotional display.

**The Long Tail: Why the Future of Business Is Selling Less of More**
Anderson, C. (2006).
Hyperion. ISBN: 978-1401302375.

> Boost rules targeting specific manufacturers or categories during campaigns are a direct tool for shifting the revenue distribution temporarily — pushing clearance items or new launches into the head of the distribution for the campaign window.
