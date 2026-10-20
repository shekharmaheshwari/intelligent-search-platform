# Chat Persona — Per-Index Configurable Chat Wording

> **Status: Implemented.**
> Built as designed: `chat_persona_config` JSONB column on `indexes`, GET/PUT
> `/v1/indexes/{id}/chat-persona`, `_system_prompt()` templated on `{item_noun}`
> with the comparison instruction and additional instructions wired through, and
> a new "Chat Persona" tab in the admin console. Verified end-to-end live in the
> browser (dark/light themes, save/reload persistence, and a real chat request
> against the Plants index reflecting `item_noun="plant"`, comparison disabled,
> and a custom instruction in the model's actual answer) — see the session that
> built this for the full verification trail. One real bug was found and fixed
> during that browser testing: the tab initially rendered stuck on defaults
> instead of the saved config, caused by a race between the component's
> reactive sync and the store's async load; fixed to match the
> `await load(); read once` pattern already used in `BoostRulesTab.svelte`.

## Problem

The chat system prompt (`_system_prompt()` in `backend/src/isp/services/infra/chat/service.py`)
is one template shared by every index in the deployment. Its instructional rules — "use only
the data below," "don't drop items from a list," "compare all of them" — are written in
deliberately generic terms (`products`, `attributes`, `matches`), following the same
domain-agnostic principle already used for the follow-up-detection heuristics (`_is_follow_up_heuristic`
derives from shown-product-title tokens, not a hardcoded brand/category list).

One place breaks that genericness: the vague-preference note (added to handle queries like
"cheap phones" or "fast phone" with no concrete number — see `_is_vague_preference_query`)
explicitly invites the user to share **"a budget"**. That's correct for a price-bearing
catalog and wrong for any index without a `price` field — a documentation index, a listings
index, anything non-commerce.

Investigating that one bug widened into a broader question: how much of the chat prompt
should actually vary per index, and how should that variance be surfaced to an operator?

## Two different kinds of variation — not one problem

Walking through every string in `prompts.json`, the variation splits cleanly into two
categories that need different treatment:

1. **Facts ISP already knows from the schema** — does this index have a `price` field? This
   isn't a judgment call; it's answerable by the same field-introspection ISP already uses to
   build `ts_query_by` and facet fields dynamically (`get_all_fields` / `field_config`). No
   operator input needed — the prompt should just check the schema and adjust automatically.

2. **Things no schema can answer** — is this catalog conceptually "products" at all, or
   articles, listings, cases? Does "compare all of them" even make sense for this domain? A
   schema can't tell you that; it's a real choice, and it's the one place per-index operator
   configuration actually earns its complexity.

This document covers **(2)**. Category (1) — the price/budget mismatch specifically — is a
small, separate, self-contained fix (check whether `price` is in the index's field list before
including "a budget" in the vague-preference note) and doesn't need a UI at all.

## What NOT to build

Two options were considered and rejected before landing on the design below:

- **A raw JSON/free-text override of the whole prompt.** Every other ISP config surface
  (Boost Rules, Synonyms, Fields) is a structured form, not a text editor — and here a
  structured form is also the safer choice: free-text editing risks an operator deleting the
  "answer only from the data below" grounding rule while trying to change the tone, silently
  reopening the hallucination issues this session spent most of its time fixing.
- **A single global override.** Rejected specifically because of the price/budget case above
  — there's no one global value of "how should I phrase this" that's correct across
  fundamentally different domains. (Category-1 facts, by contrast, *are* fine to handle
  globally, because they're derived per-request from the schema, not configured at all.)
- **Reusing the existing "Personalisation" tab.** Checked the actual code
  (`AffinityDimensionsPanel.svelte`) — that tab is customer-behavior-based search **ranking**
  personalization (`context_service.py`, `affinity_dimensions`, `context_boost`), a completely
  different system from chat wording. Same English word, two unrelated concepts; reusing the
  tab or its name would conflate them. The new feature gets its own, differently-named tab.

## What to build

A small, structured, per-index config — three fields, not a full prompt rewrite:

| Field | Type | Default | Effect |
|---|---|---|---|
| Item noun | text | `"product"` | Fills the generic template: "helpful **product** expert assistant," "matching **products**." Swapping to "plant," "listing," "article" makes most of the existing generic phrasing correct for that domain without touching anything else. |
| Enable comparison language | toggle | on | Gates the "compare all of them, don't silently pick a subset" instruction. Off for domains where comparison doesn't make sense (e.g. documentation). |
| Additional instructions | text, bounded length | empty | Free text, but always **appended after** the fixed grounding rules — never replaces them. This is where genuinely open-ended tone/persona customization lives, scoped so it can't remove the safety-critical instructions. |

Unset fields fall through to the current `prompts.json` defaults — an index that never visits
this tab behaves exactly as today.

## Backend

- New per-index column, same pattern as `field_config` / `boost_rules`:
  `indexes.chat_persona_config JSONB` — `{"item_noun": "plant", "enable_comparison": false, "additional_instructions": "..."}`.
- New endpoints mirroring the Synonyms feature's shape:
  ```
  GET  /v1/indexes/{idx_id}/chat-persona   → current config (defaults if unset)
  PUT  /v1/indexes/{idx_id}/chat-persona   → upsert
  ```
- `_system_prompt()` gains parameters for the three fields (or a single `persona_config: dict`),
  substituted into the template in place of the hardcoded `"product"` noun and the
  always-on comparison instruction.
- `chat_stream(query, messages, index_id, ...)` already receives `index_id` — it fetches the
  per-index config before building `system`, same place `field_config`/`boost_rules` are
  already read per-request today.
- The separate schema-derived price/budget fix (category 1, above) is independent of this —
  it reads `field_config` directly to decide whether to mention "a budget" at all, with no
  relation to `chat_persona_config`.

## Frontend

Same file layout as the Synonyms feature (`docs/synonyms.md`), adapted for a 3-field form
instead of per-item CRUD:

- `frontend/src/lib/types/index.types.ts` — `ChatPersonaConfig { item_noun, enable_comparison, additional_instructions }`
- `frontend/src/lib/api.ts` — `getChatPersona`, `updateChatPersona`
- `frontend/src/lib/stores/chat-persona-store.ts` — `load`, `save(config)`
- `frontend/src/components/sources/ChatPersonaTab.svelte` — item-noun text input, comparison
  toggle, additional-instructions textarea, single "Save" action (batch, like Boost Rules —
  not per-item CRUD like Synonyms, since this is one config object per index, not a list)
- `frontend/src/components/sources/IndexDetail.svelte` — new tab, distinctly named ("Chat
  Persona," not "Personalisation"), registered alongside Boost Rules / Synonyms

## Decisions summary

- **Structured form, not raw text/JSON** — matches every other ISP config surface, and avoids
  an operator being able to break the grounding rules that prevent hallucination.
- **Per-index, not global** — the one thing this covers (domain framing) has no sensible
  single global value; category-1 facts (price exists?) are handled separately, automatically,
  with no config surface at all.
- **Additive, not replacing** — "additional instructions" is appended after the fixed rules,
  never a substitute for them.
- **New tab, distinct name from "Personalisation"** — that name is already taken by an
  unrelated feature (customer-affinity search ranking); verified against the actual component
  before reusing it.
