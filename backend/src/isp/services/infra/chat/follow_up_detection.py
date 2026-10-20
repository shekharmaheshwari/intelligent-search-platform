"""
Follow-up detection — pure classification of whether a message continues the
prior turn (heuristic path only; used when smart_followup is OFF, see
chat/service.py's _resolve_retrieval_params). No LLM calls in this module —
those (query rewriting for smart mode) stay in chat/service.py alongside the
provider-dispatch code they depend on; see that file's module docstring.

  Tier 1 — unambiguous pronoun / intent phrases ("these", "tell me more").
  Tier 2 — anchored refinement phrases ("something else", "not those").
  Tier 3 — context-aware short-query guard: every meaningful word in a short
           query already appeared in the titles just shown — domain-agnostic,
           no hardcoded brand/category list, works for any catalog.
"""
from __future__ import annotations
import re

# ── Tier 1 (unambiguous pronoun / intent phrases) ────────────────────────────

_T1_RE = re.compile(
    r'\b('
    r'these|those|them|they|the ones?|that one|this one'
    r'|the first one|the second one|the third one|the last one'
    r'|number (one|two|three)|option \d|#\d'
    r'|give me details?|more details?|tell me more|more info|show me more'
    r'|what else|anything else|say more|tell me about (it|them)'
    r'|side by side|pros and cons|help me decide|help me choose'
    r'|which one should i (get|buy)|which do you recommend|what would you recommend'
    r'|which is (better|best|cheaper|more expensive)|which one is (better|best)'
    r'|compare|versus|vs\b|the best value'
    r'|does it have|does it support|compatible with'
    r'|what colou?rs does it|what size does it|what comes in the box|what.s included'
    r'|is it waterproof|is it wireless|is it compatible'
    r'|is it in stock|is it available|back in stock|when will it be back'
    r'|how much is it|how much does it cost|how much are they'
    r'|and the price|and the cost'
    r"|i'll take it|i want that one|how do i order|how do i buy"
    r'|what you showed|what you mentioned|the ones you listed'
    r'|the results|the options above|the items above|listed above|mentioned above'
    r')\b',
    re.IGNORECASE,
)


def _is_follow_up_tier1(query: str) -> bool:
    return bool(_T1_RE.search(query))


# ── Tier 2 (anchored refinement phrases) ──────────────────────────────────────

_T2_RE = re.compile(
    r'\b('
    r"i('m| am) interested in|i prefer|not that one|not those"
    r'|something else|other options?|show me alternatives?'
    r'|do you have it in|any in|from .+ brand|by .+ brand'
    r')\b',
    re.IGNORECASE,
)


def _is_follow_up_tier2(query: str) -> bool:
    return bool(_T2_RE.search(query))


# ── Tier 3 (context-aware short-query guard) ─────────────────────────────────
#
# Domain-agnostic: no hardcoded brand/category lists.
# Checks whether query tokens already appeared in previously shown product titles.
# Works identically for phones, plants, furniture, cars — any catalog.

def _shown_product_tokens(messages: list[dict]) -> set[str]:
    """
    Words (>2 chars) from the titles of the products shown in the MOST RECENT
    assistant turn only — not accumulated across the entire conversation.
    Matches the same "most recent turn only" scoping retrieval.py's
    pinned_cards and query_filters.py's _ids_from_history already use.

    Accumulating across all history let unrelated, non-adjacent topics bleed
    into this guard: "iphone adapter" was wrongly classified as a follow-up
    and ID-pinned to a *different* prior turn's Samsung adapters, because
    "iphone" appeared in an even-earlier "iPhone cases" turn and "adapter"
    appeared in the (unrelated) Samsung-adapter turn — neither together, and
    neither in the actually-relevant most recent turn, but the accumulated
    set couldn't tell the difference.
    """
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("products"):
            return {
                w.lower()
                for p in m["products"]
                for w in (p.get("title") or "").split()
                if len(w) > 2
            }
    return set()


def _is_follow_up_short_query(query: str, messages: list[dict]) -> bool:
    words = query.strip().split()
    if len(words) > 5 or not messages:
        return False
    shown = _shown_product_tokens(messages)
    # A token is "content" if it's long enough to be meaningful on its own, OR
    # contains a digit — model/generation numbers ("16" vs "17", "S24" vs "S25",
    # "4G" vs "5G") are almost never filler, and are often exactly the token
    # that distinguishes a genuinely new query from the previous turn's topic,
    # even though they're short. Dropping them let e.g. "iphone 17 case" be
    # misread as a follow-up on an already-shown iPhone 16 case, because "17"
    # never got compared against what was actually shown.
    content_words = [w.lower() for w in words if len(w) > 3 or any(c.isdigit() for c in w)]
    if not content_words:
        return True  # "ok" / "yes" / "and?" — almost certainly a follow-up
    new_terms = [w for w in content_words if w not in shown]
    return len(new_terms) == 0  # every meaningful word is familiar → follow-up


def is_follow_up_heuristic(query: str, messages: list[dict]) -> bool:
    return (
        _is_follow_up_tier1(query)
        or _is_follow_up_tier2(query)
        or _is_follow_up_short_query(query, messages)
    )


def scoped_history(messages: list[dict]) -> list[dict]:
    """
    Trim history to the current follow-up chain, so the LLM doesn't see turns
    from an earlier, unrelated topic.

    Walks backward from the most recent turn. A turn stays in scope as long as
    it was itself a follow-up relative to what preceded it — re-testing each
    historical query with the same heuristic used for the live query. The walk
    stops at (and keeps) the first turn, going backward, that was NOT a
    follow-up: that's the turn that actually started the current topic, so
    everything before it belongs to a different conversation and is dropped.
    """
    user_idxs = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if not user_idxs:
        return messages
    boundary = user_idxs[0]  # no topic boundary found → the whole thing is one chain
    for i in reversed(user_idxs):
        prior = messages[:i]
        if not is_follow_up_heuristic(messages[i].get("content", ""), prior):
            boundary = i
            break
    return messages[boundary:]
