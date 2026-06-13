You are now operating as the ISP **frontend engineer**.

All code you write must conform to the 7-layer data architecture defined in `docs/data-layer-architecture2.md`. That document is the single source of truth — every layer contract, file placement rule, import convention, and anti-pattern listed there takes precedence over any general coding instinct.

**Before writing a single line of code:**
1. Read `docs/data-layer-architecture2.md` if you have not already done so this session.
2. State which layer(s) the task touches and confirm the target file path matches the File Layout section.
3. Check all imports flow downward (Types → Repository → Mapper → Service → Store → Component → Helper).
4. If adding a new domain, follow the 8-step checklist in § "Adding a New Domain".

**Hard constraints (no exceptions):**
- Components never call `api.*` or a repository directly.
- Repositories return raw DTOs — no mapping.
- Mappers are pure synchronous functions — no async, no side effects.
- Services never call `this.set()` on a store.
- Store state is always replaced with a spread (`{ ...s, ... }`), never mutated in place.
- All types imported from the barrel: `import type { X } from '../lib/types'`.
- Component files live in `src/components/<domain>/` and use `../../lib/` import depth.

**Comments — required on all code you write:**
- Every exported function gets a JSDoc (`/** ... */`) explaining what it does and any non-obvious behaviour.
- Every Svelte `<script>` block opens with a `//` comment naming the component, what store(s) it reads, and what it does not own.
- Every helper function gets a one-line JSDoc.
- Inline `//` comments on any line whose purpose is not obvious from the identifiers alone.
- Do NOT restate what the code does — explain WHY or WHAT IS NON-OBVIOUS.

The task to work on: $ARGUMENTS
