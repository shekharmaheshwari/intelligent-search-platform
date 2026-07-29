"""
Claude analyst: sends a JSON schema + sample to Claude and returns structured
artifacts (3 JS extensions + field_config) ready for ISP to execute.
"""
from __future__ import annotations
import json
import logging
import re

log = logging.getLogger(__name__)

_OBJ_OBJ_RE = re.compile(r'\[object Object\]')

_SYSTEM = """
You are an ISP (Intelligent Search Platform) configuration expert.

Given a JSON data sample and its schema, you produce four artifacts that
configure a complete, production-ready ISP search index:

1. **normalizer_js** — A JavaScript transform extension that flattens the raw
   JSON document into ISP standard fields. Must handle nested objects, arrays,
   and missing/null values gracefully.

2. **chat_content_js** — A JavaScript transform extension that builds a
   ``chatContent`` string on each document. This is a structured, labelled
   summary used by the RAG chat pipeline. Format: "FieldLabel: value. Label2: value2."
   Put the most discriminating attribute first (e.g. brand, type, price).

3. **suggestion_js** — A JavaScript extension that provides autocomplete phrases.
   Must define TWO functions:
   - ``extractSuggestions(doc)`` — returns an array of ``{query, category}`` objects.
     Return at least one entry from doc.title. Optionally add brand + category combo.
   - ``transform(doc)`` — must exist as a no-op: ``function transform(doc) { return doc; }``

   Example:
   ```
   function extractSuggestions(doc) {
     var results = [];
     var title = doc.title || "";
     if (title) { results.push({query: title, category: doc.intent_label || ""}); }
     var brand = doc.brand || "";
     var cat = doc.category_name || doc.category || "";
     if (brand && cat) { results.push({query: brand + " " + cat, category: doc.intent_label || ""}); }
     return results;
   }
   function transform(doc) { return doc; }
   ```

4. **field_config** — A JSON array of field config objects for Typesense.
   Each object: { "name": "...", "type": "string|float|int|bool", "searchable": true/false, "facet": true/false }
   Include only fields that exist after normalization. Mark string fields that
   users might search as searchable=true. Mark fields useful for filtering as facet=true.

CRITICAL JS RULES (the extension runner uses ES5 — Duktape):
- Use ONLY ES5: var declarations, regular functions, no arrow functions,
  no const/let, no template literals (use string concatenation), no destructuring,
  no spread operator, no default parameters.
- normalizer_js and chat_content_js: ``function transform(doc) { ... return doc; }``
- suggestion_js: must define BOTH ``extractSuggestions(doc)`` (returns array of {query,category} objects) AND ``function transform(doc) { return doc; }``
- All extensions must handle missing/null fields with fallbacks like:
  ``var price = doc.price || 0;``  ``var name = doc.name || doc.title || "";``
- For nested fields (e.g. doc.category.name): always guard:
  ``var cat = (doc.category || {}).name || "";``
- For array fields (e.g. doc.images[0]): always guard:
  ``var img = (doc.images && doc.images.length) ? doc.images[0] : "";``

NORMALIZER RULES:
- Set doc.title from the best human-readable name field.
- Set doc.content from the best description/body field (strip HTML tags with .replace(/<[^>]*>/g, " ")).
- Set doc.url from url/link/slug (or construct from sku/id if none exists).
- Set doc.image: if images is an array of objects, extract the first item's src/url/href property:
  ``var img = (doc.images && doc.images.length && doc.images[0]) ? (doc.images[0].src || doc.images[0].url || "") : (doc.image || "");``
- Set doc.price as a number (parseFloat, default 0). If price is in a variants array, take variants[0].price.
- Flatten nested objects to flat fields using underscore naming: category.name → doc.category_name
- ARRAYS OF OBJECTS must never be left as-is. Always flatten them:
  - If the array has a meaningful single value (e.g. price), take the first item's field.
  - If the array is a list of items (e.g. variants, options), join their key property with ", ":
    ``var variants = (doc.variants || []).map(function(v){ return v.title || v.name || ""; }).filter(Boolean).join(", ");``
  - For image arrays: extract src/url from each and join or take first.
  - Never set a field to an array of objects — always reduce to string, number, or bool.
- Set doc.in_stock to true/false boolean.
- Keep all extra domain-specific fields as flat string/number/bool fields.

FIELD_CONFIG RULES:
- Always include: title (string, searchable), content (string, searchable),
  chatContent (string, searchable), url (string), image (string).
- price should be type "float", not "string".
- Boolean fields (in_stock, is_featured): type "bool", facet true.
- Category/brand/type string fields: facet true.
- Do NOT include vec, indexed_at, source_type, id — ISP manages these.

Return ONLY a raw JSON object with these exact keys (no markdown, no explanation):
{
  "normalizer_js": "...",
  "chat_content_js": "...",
  "suggestion_js": "...",
  "field_config": [...],
  "root_key": "...",
  "title_field": "...",
  "content_field": "...",
  "image_field": "...",
  "url_field": "...",
  "id_field": "...",
  "intent_label": "..."
}

intent_label should be one short word describing the product type
(e.g. "device", "plant", "accessory", "plan", "furniture", "book").
"""


async def analyse(
    sample: list[dict],
    schema: dict[str, str],
    index_name: str,
    api_key: str,
    feedback: str = "",
) -> dict:
    """
    Call Claude with the schema + sample and return the structured artifacts dict.

    Raises ``ValueError`` if Claude's response cannot be parsed as JSON.
    """
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=api_key)

    user_content = (
        f"Index name: {index_name}\n\n"
        f"Schema (field path → type):\n{json.dumps(schema, indent=2)}\n\n"
        f"Sample items ({len(sample)} items):\n{json.dumps(sample, indent=2)}"
    )

    if feedback.strip():
        user_content += (
            f"\n\n## Feedback from previous attempt — you MUST address all of these issues\n\n"
            f"{feedback.strip()}"
        )

    msg = await client.messages.create(
        model="claude-opus-4-8",
        max_tokens=4096,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )

    raw = msg.content[0].text.strip()

    # Strip markdown code fences if Claude wrapped the response
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude returned non-JSON response: {e}\n\nRaw:\n{raw[:500]}")


def _find_bad_fields(doc: dict) -> list[str]:
    """
    Inspect a normalizer output doc and return human-readable descriptions of
    any fields that still hold nested structures or bad JS stringification.
    """
    bad = []
    for key, val in doc.items():
        if isinstance(val, dict):
            bad.append(f"'{key}' is still a nested object — flatten it to a scalar string")
        elif isinstance(val, list) and val and isinstance(val[0], dict):
            bad.append(
                f"'{key}' is still an array of objects — use .map() to extract a "
                f"scalar property then .join(\", \")"
            )
        elif isinstance(val, str) and _OBJ_OBJ_RE.search(val):
            bad.append(
                f"'{key}' became \"[object Object]\" — you called .join() on an array "
                f"of objects without .map() first; use "
                f"(doc.{key} || []).map(function(x){{ return x.title || x.name || x.src || \"\"; }}).join(\", \")"
            )
    return bad


async def analyse_with_correction(
    sample: list[dict],
    schema: dict[str, str],
    index_name: str,
    api_key: str,
    feedback: str = "",
    max_retries: int = 2,
) -> dict:
    """
    Call ``analyse()`` then test the generated normalizer_js against sample[0].
    If any field still holds nested objects or \"[object Object]\" strings,
    automatically re-prompt Claude with precise field-level feedback and retry.
    Returns artifacts dict with an added ``_auto_corrections`` list (may be empty).
    """
    import asyncio
    from isp.services.infra.extension_runner import _run_sync

    artifacts = await analyse(sample, schema, index_name, api_key, feedback=feedback)
    corrections: list[str] = []

    for attempt in range(max_retries):
        normalizer_code = artifacts.get("normalizer_js", "function transform(doc){return doc;}")
        try:
            ext = [{
                "id": "selftest", "name": "selftest",
                "code": normalizer_code, "type": "transform", "enabled": True,
            }]
            result = await asyncio.to_thread(_run_sync, [dict(sample[0])], ext)
            test_doc = result[0] if result else {}
        except Exception:
            # JS error — accept current artifacts; _repair_doc will catch it later
            log.debug("analyse_with_correction: normalizer self-test failed on attempt %d, "
                     "accepting current artifacts", attempt + 1, exc_info=True)
            break

        bad = _find_bad_fields(test_doc)
        if not bad:
            break  # normalizer output is clean

        correction_note = (
            f"Auto-correction attempt {attempt + 1}: normalizer_js still produces "
            f"bad output for these fields after running against a real sample doc:\n"
            + "\n".join(f"  - {b}" for b in bad)
            + "\n\nFix ONLY normalizer_js for these fields. All other artifacts are fine."
        )
        corrections.append(correction_note)
        combined = (feedback + "\n\n" + correction_note).strip() if feedback else correction_note
        artifacts = await analyse(sample, schema, index_name, api_key, feedback=combined)

    artifacts["_auto_corrections"] = corrections
    return artifacts
