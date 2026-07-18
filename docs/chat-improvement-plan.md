# Chat Improvement Plan — ISP Stress-Test & Optimization

> **Source:** `/Users/shekharmaheshwari/Desktop/chat-improvement`  
> **Goal:** Stress-test ISP chat's prompt routing, intent recognition, and data sourcing across five product pillars: Smartwatches, Accessories, Phones, Tablets, and Plans. Deliver a concrete optimization roadmap.

---

## Part 1 — Role & Objective

**Role:** QA Engineer + UX Researcher specializing in e-commerce conversational AI.

**Task:** Simulate diverse customer personas to evaluate:

1. **Intent Matching** — Did the bot accurately identify what the user wanted?
2. **Data Sourcing** — Did it pull from the correct catalog/API (e.g., plans database vs. phone specs)?
3. **Error Handling** — How did it handle vague inputs, typos, or out-of-stock requests?
4. **Tone & Friction** — Was the response helpful, or did it force the user to over-explain?

Real customers rarely use perfect grammar — they use slang, look for the cheapest options, try to compare completely different items, or type vague fragments. Test cases must reflect this.

---

## Part 2 — Query Test Matrix

### 2.1 Phones & Tablets

| Query Type | Real Customer Query | Intended Source / Action |
|:---|:---|:---|
| Direct search | "Need a new iPhone, what do you have?" | Phone Catalog API |
| Cheapest / budget | "lowest price tablet available right now" | Sort: Price (Low to High) |
| Feature-specific | "which phones have the best battery life for travel?" | Spec filtering (battery capacity + reviews) |
| Comparison | "should i get the s24 ultra or pixel 9 pro xl?" | Side-by-side spec comparison engine |
| Cross-category capability | "can i run photoshop on a regular ipad or do i need a pro?" | Tablet capability / App requirements matrix |

### 2.2 Smartwatches & Accessories

| Query Type | Real Customer Query | Intended Source / Action |
|:---|:---|:---|
| Compatibility | "will this watch strap fit an apple watch series 9 45mm?" | Accessory compatibility database |
| Use-case driven | "best smartwatch for running that doesn't need a phone near it" | Smartwatch filter: GPS + Cellular capability |
| Bundle inquiry | "any deals if i buy a phone case and screen protector together?" | Promotional / Discount engine |
| Vague hardware | "i need a charger cable for my phone" | Context gathering (ask: what phone model?) |

### 2.3 Plans & Cross-Source (Service + Hardware Combinations)

| Query Type | Real Customer Query | Intended Source / Action |
|:---|:---|:---|
| Price hunting | "what's the cheapest plan for 2 lines?" | Plans Catalog (sort by price) |
| Hardware + plan | "if i switch to the unlimited premium plan do i get a free phone?" | Promotion Matrix (plan + device matching) |
| International / feature | "going to europe next month, which plan covers data there?" | Plan features / Add-ons documentation |
| Data / usage math | "is 10gb data enough for streaming music daily?" | Plan advisory / Usage calculator logic |

---

## Part 3 — Recommended Changes & Optimization Plan

### 3.1 Intent Matrix & Friction Audit

For each failed or friction-filled test case, log:
- The raw user query
- The intent the bot matched
- The correct intent
- The source/API it hit vs. the correct one
- Whether the response resolved the user's need or created more work

Friction points to watch for:
- "cheapest phone" triggering a plan result instead of hardware
- Comparison queries returning two separate search results instead of a structured comparison
- Vague accessory queries ("charger") returning a product list instead of a clarifying question

---

### 3.2 System Prompt Modifications

**Rule A — Vague Inputs:**  
If a user asks for an "accessory" or "charger" without specifying a device, the system prompt must instruct the model to pull device history from context or ask **one** clear clarifying question before rendering products. Never return an unfiltered list.

**Rule B — Comparisons:**  
When comparing two devices, limit technical jargon. Use a simplified Markdown table highlighting: Price, Battery, and Main Differentiator only.

**Rule C — Mixed Intent:**  
When a user asks about both a tablet and a plan simultaneously, prioritize hardware selection first, then recommend the matching data line add-on.

**Rule D — Out-of-Stock:**  
When a user queries a "cheapest phone" that is backordered, the response must be:  
> "The lowest priced in-stock phone is [X], but we also have [Y] coming back next week. Would you like to pre-order?"

---

### 3.3 Data Source Routing Corrections

| Detected Query Type | Current Behavior (Flaw) | Recommended Routing |
|:---|:---|:---|
| "Free phone plans" | Shows phone list instead of plan deals | Route to Promo Database filtered by Plan constraints |
| Comparison queries | Returns two separate product cards | Trigger side-by-side spec comparison engine |
| Vague accessory query | Returns unfiltered accessory list | Ask one clarifying question (device model) first |
| Budget + hardware | Mixes plan upsells into hardware results | Separate pass: hardware results first, plan add-ons second |

---

### 3.4 Edge Case & Error Handling Upgrades

| Scenario | Required Behavior |
|:---|:---|
| Out of stock (cheapest query) | Surface next available + pre-order CTA |
| Typos / misspellings | Fuzzy match with confirmation ("Did you mean Galaxy S24?") |
| Cross-category (tablet + plan) | Hardware first → plan recommendation inline |
| Compatibility query | Must resolve device model before returning accessories |
| Usage math | Give a concrete yes/no first, then context ("10GB is tight for daily streaming — the 30GB plan covers ~8hrs/day") |
| No results | Never return an empty list — offer closest alternatives or escalate to human agent |

---

## Part 4 — ISP Implementation Notes

These improvements map to ISP chat system components as follows:

| Improvement | ISP Layer | Files |
|:---|:---|:---|
| System prompt rules (A–D) | `_system_prompt()` in service.py | `backend/src/isp/services/infra/chat/service.py` |
| Data source routing | Facet-pass `facet_query_by` + `query_by_weights` | `backend/src/isp/routers/chat.py` |
| Intent classification | Query pre-processing before `_search_context` | `backend/src/isp/services/infra/chat/service.py` |
| Comparison queries | New structured response type in stream | `backend/src/isp/services/infra/chat/service.py` |
| Clarifying questions | Multi-turn context in `messages` history | Existing — ensure context is passed correctly |
| Out-of-stock handling | Requires stock field in Typesense + filter logic | `backend/src/isp/services/infra/typesense_client.py` |
