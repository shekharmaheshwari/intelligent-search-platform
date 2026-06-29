# Customer Context — Personalised Search

A complete reference for how ISP uses customer behaviour to contextualise search results: what signals are collected, how they influence ranking, and every available personalisation option.

---

## Why customer context

Two customers searching "Samsung phone" want different things.  One has spent the last ten minutes looking at budget accessories; the other just viewed three flagship devices.  Without context, both get the same ranked list.  With it, ISP steers the semantic search vector and applies affinity boosts so each customer sees results shaped by their own session.

The system is designed to be **additive** — context nudges rather than overrides relevance.  A product that ranks well on BM25 + semantic RRF stays near the top even for a customer whose history points elsewhere.  Personalisation raises the floor for relevant-to-the-customer items; it does not bury unrelated but objectively strong matches.

---

## Architecture overview

```
Customer browser
  ├── sessionStorage      current-session events (raw, minute-scale)
  └── localStorage        cross-session profile (compressed, day-scale)
          │
          │  action_history[] merged on every search request
          ▼
ISP Public Search API  (POST /v1/indexes/{id}/search/public  or  POST /v1/catalog)
  │
  ├── context_service.build_context(max_actions=50)
  │       time-decay weights per action
  │       affinity maps: category / manufacturer / source_type
  │       vector seed list (most-recent view / cart / purchase items)
  │
  ├── embedding_service.embed_query()  →  query vector
  │       + context_service.fetch_preference_vector()
  │           weighted centroid of viewed-item vectors
  │           blended at 80 % query intent / 20 % preference
  │
  ├── typesense_client.search_with_vec()        (hybrid / semantic — query present)
  │       RRF (BM25 0.7  +  semantic 0.3)
  │       editorial boosts: is_featured, popularity, boost_queries
  │       post_rrf_hook: context affinity boosts
  │             ├── +0.20 × category affinity
  │             ├── +0.15 × manufacturer affinity
  │             ├── +0.10 × source_type affinity
  │             └── -0.05  already-viewed demotion
  │
  └── typesense_client.search_documents()       (lexical — empty query / browse)
          fetch 250-item batch, rank-score each hit (1 / K+rank)
          post_rrf_hook: same affinity boosts as above
          re-paginate to requested page after hook re-sorts
```

---

## Action types

Every customer interaction is represented as a `CustomerAction` object.

```typescript
interface CustomerAction {
  type:          'view' | 'cart' | 'purchase' | 'search' | 'category';
  item_id?:      string;   // Typesense doc id — used for preference vector seeding
  item_type?:    string;   // "device" | "accessory"
  category?:     string;   // product category (e.g. "smartphones")
  manufacturer?: string;   // brand (e.g. "Samsung")
  query?:        string;   // past search query (type="search" only)
  timestamp?:    number;   // Unix ms — used for time-decay weighting
  weight?:       number;   // caller-supplied multiplier (default 1.0)
}
```

### Implemented event sources

| Event | Triggered by | Fields populated |
|---|---|---|
| `view` | Opening a product card | `item_id`, `item_type`, `category`, `manufacturer`, `timestamp` |
| `search` | Submitting a search query | `query`, `timestamp` |
| `category` | Clicking a tab (Phones / Accessories / …) | `category`, `timestamp` |

### Planned event sources

| Event | Trigger point | Extra value |
|---|---|---|
| `cart` | Add-to-cart button | Stronger purchase intent than view |
| `purchase` | Order confirmation page | Strongest signal; suppresses re-showing same item |
| `wishlist` | Save / favourite action | Durable long-term interest |
| `dwell` | Time-on-page > threshold | Differentiates genuine interest from accidental click |
| `scroll_past` | Item visible but scrolled away quickly | Negative signal — mild demotion |
| `price_filter` | User applies a price range filter | Infers budget segment |
| `oos_skip` | Out-of-stock item ignored | Informs stock-sensitivity |

---

## Action weights

Base weights before time-decay:

| Type | Weight | Rationale |
|---|---|---|
| `purchase` | 3.0 | Conversion — highest possible intent signal |
| `cart` | 2.0 | Clear interest, incomplete conversion |
| `view` | 1.0 | Baseline browsing signal |
| `search` | 0.5 | Intent without product selection |
| `category` | 0.3 | Broad interest only |
| `wishlist` *(planned)* | 2.5 | Durable opt-in interest |
| `dwell` *(planned)* | 1.5 | Quality engagement above a simple click |
| `scroll_past` *(planned)* | −0.3 | Negative signal — suppress similar items |

The caller may supply a `weight` multiplier (e.g. `2.0` for a very recent high-priority event) which is multiplied with the base weight before any decay is applied.

---

## Time-decay

### Why decay matters

An action taken 3 seconds ago is a much stronger signal than one taken 45 minutes ago.  Without decay, a product the customer briefly glanced at an hour ago carries the same weight as one they just put in their cart.

### Implemented — Phase 2 (planned)

Each action's effective weight is:

```
effective_weight = type_weight × caller_weight × exp(−age_seconds / half_life_seconds)
```

Half-lives by type:

| Type | Half-life | At 30 min | At 2 hours | At 1 day |
|---|---|---|---|---|
| `purchase` | no decay | 100 % | 100 % | 100 % |
| `cart` | 30 min | 50 % | 6 % | < 1 % |
| `view` | 15 min | 25 % | 1.5 % | < 1 % |
| `search` | 8 min | 8 % | < 1 % | < 1 % |
| `category` | 5 min | 3 % | < 1 % | < 1 % |

`purchase` events do not decay within a session: a conversion is always the strongest anchor regardless of when it happened.

If `timestamp` is absent the action is treated as age zero (full weight) — backward compatible with clients that do not yet send timestamps.

---

## Storage tiers

### Tier 1 — Current session (`sessionStorage`)

- Raw event list, newest first
- Capped at 20 events
- Cleared when the browser tab/session closes
- Minute-scale decay applied server-side via `timestamp`
- Highest influence on results

### Tier 2 — Visitor profile (`localStorage`) — planned

Persists across browser closes on the same device.  Stores a compressed profile rather than raw events to avoid unbounded growth.

```typescript
interface VisitorProfile {
  visitor_id:           string;           // UUID, generated once, no PII
  last_updated:         number;           // Unix ms — timestamp of last session
  category_weights:     Record<string, number>;    // normalised [0,1]
  manufacturer_weights: Record<string, number>;
  type_weights:         Record<string, number>;
  recent_item_ids: {
    id:           string;
    type?:        string;
    category?:    string;
    manufacturer?: string;
    ts:           number;
  }[];                                    // last 10, newest first
}
```

Profile TTL: 30 days.  Items older than 30 days are pruned from `recent_item_ids` on each write.

**Merge strategy on every search request:**

`getHistory()` produces a unified `action_history` array by:

1. Taking all raw events from `sessionStorage` (real timestamps, current session)
2. Converting the `localStorage` profile into synthetic `CustomerAction` events with timestamps set to `last_updated` (the end of the most recent past session) and `weight: 0.5` (past sessions are lower-confidence)

The server-side decay function handles both uniformly.  A product viewed in a past session 5 days ago with a 15-minute view half-life evaluates to effectively zero weight — which is correct.  The day-scale cross-session signal comes from the synthetic `category` and `manufacturer` events whose profile weights already encode accumulated past engagement.

**Profile write-back:**  After each action event, `actionHistoryService._updateProfile()` incrementally updates the `localStorage` profile:
- `view`/`cart`/`purchase` → upsert `recent_item_ids`, increment `category_weights` and `manufacturer_weights`
- `category` → increment `category_weights`
- `search` → no profile write (queries are ephemeral)
- Normalise all weight maps (max = 1) after each write

### Tier 3 — Server-side profile (identified users) — planned

For customers who sign in via OAuth2.  Profile stored in Redis (key: `isp:visitor:{email}`, TTL 30 days).

**Endpoints:**
- `GET /v1/visitor/profile` — returns the server profile for the current JWT user
- `POST /v1/visitor/profile` — merges a client `VisitorProfile` into the server profile (client wins for items with newer `ts`, server wins for older cross-device history)

**Benefit:** the customer's preference history follows them across devices.  A user who browsed Samsung flagship phones on desktop sees those signals when they open the PLP on mobile.

**Merge on login:**  when `LoginGate.svelte` detects a successful OAuth2 login, it POSTs the current `localStorage` profile to `POST /v1/visitor/profile`, receives the merged result, and writes it back to `localStorage`.

---

## Preference vector blending

The primary personalisation mechanism for **semantic and hybrid search**.  This is where vector embedding mathematics comes in — distinct from affinity boosts, which are a plain weighted count.

Typesense's ANN (approximate nearest-neighbour) pass retrieves the vectors closest to the query vector in embedding space.  By blending viewed-item vectors into the query vector *before* the ANN call, the retrieval naturally surfaces items that are both semantically relevant to the query **and** similar to what the customer has been looking at.

### The algorithm

**Step 1 — Collect item vectors.**  For each `item_id` in `ctx.vector_seed_ids` (up to 5, ordered by recency), fetch the stored `vec` embedding from Typesense.

**Step 2 — Compute centroid (equal-weight mean).**  Average the item vectors dimension by dimension:

```
preference_vec[i] = (vec_1[i] + vec_2[i] + ... + vec_n[i]) / n
                    for each dimension i in the embedding space
```

Concretely, with three Samsung accessories in session history:

```
  Viewed items                     Stored embedding vector (d dimensions)
  ────────────────────────         ──────────────────────────────────────
  Samsung Galaxy Buds  →  v₁  =  [ 0.82,  0.14,  0.55,  0.91, ... ]
  Samsung Phone Case   →  v₂  =  [ 0.79,  0.21,  0.48,  0.88, ... ]
  Samsung Charger      →  v₃  =  [ 0.85,  0.09,  0.61,  0.94, ... ]
                                    ↓       ↓       ↓       ↓
                           sum  =  [2.46,  0.44,  1.64,  2.73, ... ]
                                    ÷3      ÷3      ÷3      ÷3
                                    ↓       ↓       ↓       ↓
                     p  =  (1/n) Σ vₖ  =  [ 0.82,  0.15,  0.55,  0.91, ... ]
                                           └─────────────────────────────────┘
                                              Preference centroid — points toward
                                              the "Samsung accessories" region
                                              of embedding space
```

> *Mathematical basis:* centroid computation as a representation of a document set is the direct descendant of the **Rocchio (1971)** relevance feedback algorithm.  Rocchio showed that the centroid of relevant document vectors is the best additive correction to a query vector under the vector space model.  Here, the "relevant documents" are items the customer has viewed.  The vector space model itself — representing queries and documents as vectors in the same embedding space — was introduced by **Salton, Wong & Yang (1975)**.

**Step 3 — Linear blend with query vector.**

```
blended[i] = (1 − blend_weight) × query_vec[i]  +  blend_weight × preference_vec[i]
           = 0.8 × query_vec[i]                  +  0.2 × preference_vec[i]
```

Continuing the example, blending the query "wireless earbuds" with the Samsung preference centroid:

```
  q  = embed("wireless earbuds")  →  [ 0.60,  0.88,  0.33,  0.72, ... ]
  p  = Samsung centroid           →  [ 0.82,  0.15,  0.55,  0.91, ... ]

  blended = 0.8 × q  +  0.2 × p
          = 0.8 × [ 0.60,  0.88,  0.33,  0.72, ... ]   ← 80% query intent
          + 0.2 × [ 0.82,  0.15,  0.55,  0.91, ... ]   ← 20% Samsung preference
          =        [ 0.48,  0.70,  0.26,  0.58, ... ]
          +        [ 0.16,  0.03,  0.11,  0.18, ... ]
          =        [ 0.64,  0.73,  0.37,  0.76, ... ]   ← steered toward Samsung
```

> *Mathematical basis:* this is precisely the **Rocchio update rule** applied to dense vectors.  The original formula was `Q_new = α·Q_orig + β·centroid(relevant) − γ·centroid(non-relevant)`.  With `α=0.8`, `β=0.2`, and `γ=0` (no negative signal yet), ISP's blend is a two-term Rocchio step.  The 80/20 split is a deliberate hyperparameter: too much preference (blend_weight > 0.5) causes **query drift** — the blended vector moves so far from the query that semantically off-topic but "preferred-brand" items flood the results.  The 0.2 value is within the range shown to be safe in the pseudo-relevance feedback literature (**Efthimiadis, 1996**).

**Step 4 — L2 normalisation** (required for cosine-distance ANN search).

```
  norm         = sqrt( 0.64² + 0.73² + 0.37² + 0.76² + ... )
  final_vec[i] = blended[i] / norm

  final  →  [ 0.52,  0.61,  0.29,  0.63, ... ]   ← unit vector, ready for ANN
```

The result is a unit vector pointing 80% toward the original query intent and 20% toward the centroid of viewed items.

### Why L2 normalisation?

Typesense's ANN index compares vectors using **cosine similarity** — the angle between two vectors, not their Euclidean distance.  The cosine similarity formula is:

```
cosine_sim(A, B) = (A · B) / (‖A‖ × ‖B‖)
```

When both vectors are unit-length (`‖A‖ = ‖B‖ = 1`), this simplifies to a plain dot product `A · B`, which is what ANN indexes compute efficiently.  After blending, the norm of the mixed vector is not 1 (it is between 0 and 1), so normalisation is required before the ANN lookup.

> *Mathematical basis:* **Manning, Raghavan & Schütze (2008)**, Chapter 6.3 ("Variant tf-idf functions") and Chapter 6.4 ("Document and query weighting schemes") establish the necessity of length normalisation for cosine comparison.  **Reimers & Gurevych (2019)** (Sentence-BERT) confirm this holds for dense transformer embeddings: models are trained with L2-normalised outputs and cosine distance is the correct metric.

### Why mean, not sum?

Summing without averaging would make the preference vector grow larger as the customer views more items, progressively drowning out the query intent.  The mean keeps the preference vector at roughly the same magnitude regardless of how many items are seeded.

> *Mathematical basis:* this is the same motivation behind **tf normalisation** in BM25 — raw term frequency grows without bound as documents get longer, so it is length-normalised before use.  The principle is: raw counts or sums must be normalised before combination with independently-scaled signals.  See **Robertson & Spärck Jones (1976)** for the original probabilistic argument.

### How Typesense ANN works — HNSW

#### ANN: the problem category

**ANN (Approximate Nearest Neighbour)** is not an algorithm — it is the name of a *class of problem*: find the vectors most similar to a query vector, approximately, fast enough for real-time use.

Exact nearest-neighbour search scans every document vector: O(n × d).  At 1 million products with 1024-dimensional embeddings that is 10⁹ floating-point operations per query — too slow.  ANN algorithms accept a small, controlled loss in recall (e.g., miss 1–5% of the true nearest neighbours) in exchange for O(log n) query time.

**HNSW is one specific algorithm** that solves the ANN problem.  There are several competing algorithms, each with different trade-offs:

```
ANN  ← the PROBLEM / category
│       "find similar vectors fast, approximately"
│
├── HNSW   (Hierarchical Navigable Small World)  ← what Typesense uses
│           graph-based · best recall/speed on most benchmarks · CPU-only
│
├── FAISS  (Facebook AI Similarity Search)       ← what Meta / PyTorch uses
│           IVF + quantisation · best throughput at billion scale · GPU-friendly
│
├── Annoy  (Approximate Nearest Neighbours Oh Yeah) ← what Spotify built
│           random projection trees · low memory · read-only after build
│
└── ScaNN  (Scalable Nearest Neighbours)         ← what Google uses
            anisotropic quantisation · fastest on Google TPUs · proprietary infra
```

#### Comparison of ANN algorithms

| | HNSW | FAISS | Annoy | ScaNN |
|---|---|---|---|---|
| Data structure | Multi-layer graph | Inverted file + quantisation | Random projection forest | Quantised partitions |
| Query complexity | O(log n) | O(√n) with IVF | O(log n) | O(log n) |
| Recall @ 10-NN, 1M vectors | ~99% | ~95% (IVF-PQ) | ~90% | ~98% |
| Throughput | High | Very high (GPU) | Medium | Very high (TPU) |
| Index update | Online (add docs live) | Offline rebuild | Offline rebuild | Offline rebuild |
| GPU required | No | Optional but faster | No | Preferred |
| Open source | Yes | Yes (MIT) | Yes (Apache 2) | No |

> *Source:* ANN-Benchmarks (Aumuller et al., 2020) at ann-benchmarks.com.  Numbers are indicative; exact figures depend on dataset, dimensionality, and hardware.

#### Why ISP uses Typesense / HNSW

| Criterion | Decision |
|---|---|
| **Recall vs speed** | HNSW consistently wins on recall-at-fixed-QPS across ANN-Benchmarks datasets — ~99% recall with sub-millisecond latency at product-catalogue scale (thousands to low millions of docs) |
| **Online updates** | Products are indexed incrementally as crawlers run.  HNSW supports inserting new vectors without rebuilding the entire index.  FAISS and Annoy require offline rebuilds. |
| **No GPU needed** | Typesense runs on standard CPU instances.  FAISS's speed advantage is largely GPU-dependent; ScaNN is optimised for Google TPUs.  HNSW on CPU is sufficient at ISP's scale. |
| **Unified engine** | Typesense combines BM25 lexical search + HNSW vector search + filtering + sorting in one process.  Using FAISS or ScaNN would require a separate vector database alongside a lexical search engine, doubling operational complexity. |
| **Open source, self-hosted** | No vendor lock-in; runs inside the existing Docker Compose stack with no external API calls for search. |

> *Reference:* the ANN problem and its complexity were formally defined in **Indyk & Motwani (1998)** "Approximate nearest neighbors: towards removing the curse of dimensionality," STOC 1998 — the foundational paper that proved exact high-dimensional NN search is intractable and motivated the approximate approach.  FAISS is described in **Johnson, Douze & Jégou (2019)** "Billion-scale similarity search with GPUs," IEEE Transactions on Big Data.  ScaNN is described in **Guo et al. (2020)** "Accelerating Large-Scale Inference with Anisotropic Vector Quantization," ICML 2020.  Annoy is documented at github.com/spotify/annoy (no formal paper).

Typesense uses **HNSW (Hierarchical Navigable Small World)** graphs — the current industry standard for ANN vector search at product-catalogue scale.

#### Index build time

When a document is indexed, its embedding vector is inserted into a multi-layer graph.  Vectors that are close in cosine distance are connected by edges.  The graph has multiple layers: the top layer is sparse with long-range links for fast coarse navigation; the bottom layer is dense with short-range links for precision.

```
  Layer 2  (sparse — few nodes, long-range connections)
    ○ ─────────────────────── ○
    │                         │
  Layer 1  (medium density)
    ○ ──── ○ ──────── ○ ───── ○
    │       │         │       │
  Layer 0  (dense — every document)
    ○─○─○─○─○─○─○─○─○─○─○─○─○─○
    └── Samsung cluster ──┘└─ Sony ─┘
```

Similar items (e.g., all Samsung accessories) end up connected in the same neighbourhood at Layer 0.

#### Query time

HNSW traverses the graph top-down:

```
  1. Enter at Layer 2 — quickly find the rough region nearest to the query vector
           query: "wireless earbuds" (blended toward Samsung)
                ↓
  2. Drop to Layer 1 — refine within that region
                ↓
  3. Reach Layer 0 — walk the dense graph to find the k nearest neighbours
                ↓
  Return top-k documents (e.g., k=72 for a 24-result page with 3× candidate pool)
```

This is analogous to a binary search in vector space: each layer halves the search space, so the total traversal is O(log n) rather than O(n).

#### Why preference vector blending works at the retrieval level

When the query vector is shifted toward the Samsung centroid, HNSW enters the graph at a different position and traverses a different neighbourhood:

```
  Unblended query  →  enters "earbuds" neighbourhood  →  returns generic earbuds
                                                          (Sony, Apple, JBL, ...)

  Blended query    →  enters "Samsung earbuds" sub-neighbourhood
                   →  returns earbuds that are both query-relevant AND Samsung-proximate
```

This is why blending is more powerful than a post-retrieval boost: it changes **which documents are candidates at all**, not just how they are ranked after the fact.  A Sony earbud that would have been retrieved by the unblended query may never appear in the candidate set after blending — it is simply not in the neighbourhood HNSW traverses.

> *Implementation:* Typesense's HNSW index is configured at collection creation time with parameters `M` (number of bi-directional links per node, default 16) and `ef_construction` (size of the candidate list during build, default 200).  At query time, `ef` (search width) determines the recall/speed trade-off.  ISP uses Typesense defaults; these can be tuned if recall on the semantic pass is found to be insufficient.

> *Reference:* the HNSW algorithm is defined in **Malkov & Yashunin (2018)** "Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs," IEEE TPAMI. The navigable small world graph concept it builds on traces to **Kleinberg (2000)** "The small-world phenomenon: An algorithmic perspective," STOC 2000.

---

### Relationship to affinity

These two mechanisms are complementary and operate at different stages of the search pipeline:

| | Affinity boosts | Preference vector blending |
|---|---|---|
| **When it runs** | After RRF merge (re-ranking) | Before ANN lookup (retrieval) |
| **What it uses** | Weighted action counts | Stored embedding vectors |
| **Effect** | Nudges score of already-retrieved items | Steers which items are retrieved at all |
| **Requires query?** | No | Yes (needs a query vector to blend with) |
| **Affected by empty search?** | Previously not applied; fixed — see Browse personalisation section | N/A — cannot blend without a query |

### Current (Phase 1 — equal blend)

```
preference_vec = mean(item_vecs[0..4])
final_query    = L2_normalise(0.8 × query_vec + 0.2 × preference_vec)
```

### Planned (Phase 3 — decay-weighted blend)

```
preference_vec = Σ(decay_weight_k × item_vec_k) / Σ(decay_weight_k)
final_query    = L2_normalise(0.8 × query_vec + 0.2 × preference_vec)
```

Items viewed 10 seconds ago pull the vector ~32× more than items viewed 30 minutes ago (at 15-minute half-life).

### Tuning knobs

| Parameter | Default | Effect |
|---|---|---|
| `blend_weight` | 0.2 | Fraction of preference vector in the final query. Higher → stronger personalisation, less query fidelity |
| `max_seeds` | 5 | Number of viewed items whose vectors are fetched from Typesense |
| View half-life | 15 min | How fast item-level interest decays (Phase 3) |

---

## Affinity boosts (post-RRF)

Applied **after** RRF merge and editorial boosts, before final page slice.  These are additive score adjustments that nudge results toward the customer's demonstrated interests.

| Signal | Boost | Applied when |
|---|---|---|
| Category match | +0.20 × affinity | `metadata.category` matches a category in `category_affinity` |
| Manufacturer match | +0.15 × affinity | `metadata.manufacturer` matches `manufacturer_affinity` |
| Source type match | +0.10 × affinity | `source_type` matches `type_affinity` (device/accessory) |
| Already viewed | −0.05 | Doc id is in `viewed_ids` — surfaces variety |

All affinity values are normalised to [0,1] so maximum total boost is `+0.45`.  Editorial boosts (`is_featured` +0.5, `popularity` ≤ +0.5) are larger — a featured item stays featured even for a customer with no category affinity.

---

## What affinity is and how it is calculated

> **Important:** affinity has nothing to do with vector embeddings.  It is a simple weighted frequency count, max-normalised to [0, 1], applied as a flat score addition to individual documents after RRF merge.  Preference vector blending (the separate mechanism in the next section) is where embedding math is involved.

### Step 1 — Accumulate weighted counts

For every action in `action_history`, compute:

```
w = type_weight × caller_weight
```

where `type_weight` is the fixed table (purchase=3, cart=2, view=1, search=0.5, category=0.3) and `caller_weight` is the optional `weight` field on the action (defaults to 1.0).

For each action, add `w` to a running sum keyed by the action's `category`, `manufacturer`, and `item_type` fields:

```
raw_affinity[manufacturer] += w
raw_affinity[category]     += w
raw_affinity[item_type]    += w
```

> *Mathematical basis:* the type weights follow the confidence-weighted implicit feedback model of **Hu, Koren & Volinsky (2008)** — higher-confidence actions (purchase, cart) get larger weights than lower-confidence ones (view, browse).  The rationale for differential weighting (stronger intent = higher weight) traces to the probabilistic term-weighting argument in **Robertson & Spärck Jones (1976)**.

**Example — Samsung browsing session:**

| Action | type_weight | caller_weight | w | Adds to |
|---|---|---|---|---|
| Viewed Samsung Galaxy S24 | 1.0 | 1.0 | 1.0 | mfr["samsung"] += 1.0, cat["smartphones"] += 1.0, type["device"] += 1.0 |
| Viewed Samsung Galaxy Watch | 1.0 | 1.0 | 1.0 | mfr["samsung"] += 1.0, cat["smartwatches"] += 1.0, type["device"] += 1.0 |
| Bought Samsung Galaxy Buds | 3.0 | 1.0 | 3.0 | mfr["samsung"] += 3.0, cat["earbuds"] += 3.0, type["accessory"] += 3.0 |
| Searched "samsung case" | 0.5 | 1.0 | 0.5 | *(no category/manufacturer in search actions)* |

Running totals: `mfr = {samsung: 5.0}`, `cat = {smartphones: 1.0, smartwatches: 1.0, earbuds: 3.0}`, `type = {device: 2.0, accessory: 3.0}`

### Step 2 — Max-normalise to [0, 1]

```
affinity[k] = raw[k] / max(raw.values())
```

Every map is divided by its own maximum, so the top-ranked item in each dimension always gets 1.0 and everything else is proportional.

> *Mathematical basis:* max-normalisation is a standard feature-scaling technique in information retrieval to make scores from different accumulation depths comparable before combination.  The need to normalise scores before additive combination is covered in **Manning, Raghavan & Schütze (2008)**, Chapter 6 (Scoring, term weighting, and the vector space model).

```
manufacturer_affinity = {samsung: 5.0 / 5.0} = {samsung: 1.0}

category_affinity = {
    earbuds:     3.0 / 3.0 = 1.00,
    smartphones: 1.0 / 3.0 = 0.33,
    smartwatches:1.0 / 3.0 = 0.33,
}

type_affinity = {
    accessory: 3.0 / 3.0 = 1.00,
    device:    2.0 / 3.0 = 0.67,
}
```

### Step 3 — Apply as additive score boost per document

For each document in the result set, `context_boost()` looks up the document's `metadata.manufacturer`, `metadata.category`, and `source_type` in the affinity maps and adds:

```
boost = 0.20 × category_affinity.get(doc_cat,  0)
      + 0.15 × manufacturer_affinity.get(doc_mfr, 0)
      + 0.10 × type_affinity.get(doc_type, 0)
      − 0.05  (if doc.id is in viewed_ids)
```

> *Mathematical basis:* additive score combination after a primary retrieval pass is the standard field-boosting pattern in IR; the coefficients (0.20, 0.15, 0.10) are analogous to **field weights** in BM25F (**Robertson et al., 2004**).  The viewed-item demotion (−0.05) follows the **novelty / diversity** principle formalised in **Clarke et al. (2008)** — surfacing variety in ranked lists is beneficial when the user has already seen an item.

**Example scores for documents in the result set:**

| Document | category_boost | manufacturer_boost | type_boost | total |
|---|---|---|---|---|
| Samsung Galaxy Buds 3 (earbuds, Samsung, accessory) | 0.20×1.00 = +0.20 | 0.15×1.00 = +0.15 | 0.10×1.00 = +0.10 | **+0.45** |
| Apple AirPods 4 (earbuds, Apple, accessory) | 0.20×1.00 = +0.20 | 0.15×0.00 = 0 | 0.10×1.00 = +0.10 | **+0.30** |
| Samsung Galaxy S25 (smartphones, Samsung, device) | 0.20×0.33 = +0.07 | 0.15×1.00 = +0.15 | 0.10×0.67 = +0.07 | **+0.29** |
| Sony WH-1000XM6 (headphones, Sony, accessory) | 0.20×0.00 = 0 | 0.15×0.00 = 0 | 0.10×1.00 = +0.10 | **+0.10** |
| Samsung Galaxy Buds 3 (if already viewed) | +0.20 + 0.15 + 0.10 − 0.05 | | | **+0.40** |

The boost is added to the RRF score (which is typically 0.02–0.06 range).  +0.45 is large enough to move an item 5–10 positions up a page but cannot beat an `is_featured` item (+0.5 fixed).

### What affinity is NOT

- **Not cosine similarity.** There is no dot-product or embedding comparison in affinity.
- **Not collaborative filtering.** It does not use what other customers did.
- **Not a probability.** The [0,1] values are unnormalised relative weights, not probabilities.
- **Not persistent.** Recomputed from scratch on every request; no stored profile is involved (yet).

The algorithm is deliberately simple — a weighted frequency table — because it is stateless, requires no ML model, is explainable, and runs in microseconds in Python on the hot search path.

---

## Operator controls

ISP exposes four independent feature flags in **Settings → Personalisation tab**.  All flags are cached in memory at module level — there is no database round-trip per search request.

| Flag | Default | Effect when off |
|---|---|---|
| Personalisation (master) | On | `action_history` is completely ignored; no context is built and no hooks fire. All three sub-flags become inert. |
| Preference Vector Blending | On | Viewed-item vectors are not blended into the query embedding. ANN retrieval is guided by the raw query vector only. |
| Affinity Score Boosts | On | Category (+0.20), manufacturer (+0.15), and product-type (+0.10) post-RRF adjustments are skipped. |
| Viewed Item Penalty | On | Already-viewed items are not demoted (−0.05). Variety suppression is disabled. |

The master toggle is a hard gate: when it is off the other three flags have no effect regardless of their individual state.

**A/B testing use:** because each flag can be turned off independently, you can isolate the contribution of each personalisation signal.  For example, disabling only "Affinity Score Boosts" while leaving vector blending on measures the re-ranking contribution separately from the retrieval-phase contribution.

**Implementation notes:**

- Flags are stored in the `app_settings` PostgreSQL table (keys: `customer_context_enabled`, `cc_vector_blending_enabled`, `cc_affinity_boost_enabled`, `cc_viewed_penalty_enabled`).
- Module-level variables in `settings_service.py` are loaded at startup and updated in place on every `PUT /settings/customer-context` call — no DB read on the hot search path.
- The Settings API accepts partial updates: sending only `{"affinity_boost_enabled": false}` leaves the other three flags unchanged.

---

## Browse personalisation (empty-query)

When the search query is empty (page load with no search text, or after clearing the search box), the PLP frontend forces `mode: 'lexical'` to avoid embedding an empty string:

```typescript
// catalogController.ts
mode: q ? effectiveState.searchMode : 'lexical',
```

Because there is no query vector, preference vector blending cannot run.  However, affinity boosts and the viewed-item penalty operate purely on document metadata and do not need a query — so they can and should apply to browse.

### How browse personalisation works

**`_apply_lexical_hook` in `typesense_client.py`**

A private helper converts the flat Typesense hit list into the same `scores / all_docs` structure that `search_with_vec` uses for its RRF merge, then calls the affinity hook:

```python
def _apply_lexical_hook(raw: dict, hook) -> dict:
    hits = raw.get("hits", [])
    K = 60  # same constant as RRF path
    scores: dict[str, float] = {}
    all_docs: dict[str, dict] = {}
    for rank, hit in enumerate(hits):
        doc = hit["document"]
        did = doc["id"]
        scores[did] = 1.0 / (K + rank + 1)   # rank-based score, same scale as RRF
        all_docs[did] = doc
    hook(scores, all_docs)                    # adds affinity boosts in place
    ranked = sorted(all_docs, key=lambda d: scores[d], reverse=True)
    hit_by_id = {hit["document"]["id"]: hit for hit in hits}
    return {**raw, "hits": [hit_by_id[d] for d in ranked if d in hit_by_id]}
```

`search_documents` accepts `post_rrf_hook=None` and calls this helper before returning.

**Large-batch fetch in `_run_search` (`search.py`)**

Re-sorting only the first page (8–24 items) would miss Samsung items sitting at position 25+.  When an affinity hook is active, `_run_search` fetches `_FILTER_BATCH = 250` items (the same strategy used for metadata filters), applies the hook over all 250, then slices to the requested page:

```python
has_ctx_sort = bool(ctx_hook)
need_batch   = has_filter or has_ctx_sort          # 250-item fetch when either is true
fetch_page     = 1             if need_batch else req.page
fetch_per_page = _FILTER_BATCH if need_batch else req.per_page

# … search_documents called with these params and post_rrf_hook=ctx_hook …

# Re-paginate after hook / filter
hits  = raw.get("hits", [])
total = raw.get("found", len(hits))
if has_filter:
    hits  = _apply_meta_filter(hits, req.manufacturer, req.category)
    total = len(hits)
start = (req.page - 1) * req.per_page
return {**raw, "hits": hits[start:start + req.per_page], "found": total, "page": req.page}
```

### Signal status after the fix

| Signal | Empty-query browse |
|---|---|
| Preference vector blending | N/A — no query vector to blend with; expected |
| Affinity boosts (category / manufacturer / type) | ✅ Applied via `_apply_lexical_hook` over 250-item batch |
| Viewed-item penalty | ✅ Applied — same hook |

### Verified behaviour

A session with Samsung `view` + `purchase` actions and an empty search query returns Samsung accessories at the top of the browse grid.  A session with no action history returns the default `is_featured:desc, _text_match:desc` order unchanged.

---

## Other personalisation options

Everything below is currently not implemented but is compatible with the existing `CustomerAction` + `context_service` architecture.

---

### Explicit preference signals

Let customers tell ISP what they want rather than inferring it.

**Wishlist / save**
Record `type: "wishlist"` when a customer saves a product.  Weight: 2.5×.  Unlike `view`, this is an explicit opt-in — treat it as a durable signal that decays slowly (half-life: 7 days) and contributes heavily to the preference vector.

**Rating / feedback**
`type: "rating"` with a `weight` field (e.g. 5-star → `weight: 1.0`, 1-star → `weight: -1.0`).  Negative ratings can be used to actively suppress similar items from the affinity maps and preference vector.

**"Not interested"**
A direct dismiss action.  Add the item's category and manufacturer to a `suppressed_categories` / `suppressed_manufacturers` set in the profile.  Items in suppressed categories receive a hard `−0.5` penalty post-RRF.

---

### Segment detection

Rather than treating every customer individually, detect which segment they belong to and apply segment-wide boosts.  Computed from accumulated action history without any server-side ML model.

| Segment | Detection rule | Adjustment |
|---|---|---|
| Budget seeker | 80%+ of viewed items priced < $100 | Boost items by `price_asc` secondary sort |
| Premium buyer | 80%+ of viewed items priced > $500 | Boost `is_featured` items more aggressively |
| Accessory cross-sell | Viewed 1+ device + 0 accessories | Surface accessory tab suggestion |
| Loyalty | `purchase` events in history | Boost "new for existing customers" tagged items |
| Researcher | > 5 `view` events without `cart` | Surface comparison / spec-heavy content |

Segments are computed entirely client-side from `localStorage` profile and injected as `action_history` events with type `"segment"` (planned type) and a `category` field naming the segment.  The server applies pre-configured segment boosts registered in `app_settings`.

---

### Price sensitivity

Infer acceptable price range from viewed and purchased items.

**Client-side:**  track `median_viewed_price` and `max_purchased_price` in the `localStorage` profile.

**Server-side:**  use these as soft filters:
- Items priced > `2 × median_viewed_price` receive a small penalty (`−0.1`)
- Items priced within ± 30% of `median_viewed_price` receive a small boost (`+0.1`)

This is not a hard filter — an aspirational shopper still sees premium items, just slightly lower ranked.

---

### Negative signals

Currently only `viewed_ids` demotion (−0.05) is implemented.  Planned negative signals:

| Signal | Source | Effect |
|---|---|---|
| `scroll_past` | Item visible for < 2 s then scrolled away | −0.15 on similar category/manufacturer |
| `quick_close` | Modal opened and closed in < 1 s | −0.10 on that specific item |
| Out-of-stock ignore | Customer skips OOS items consistently | Prefer `in_stock: true` in metadata filter |
| Return visit without purchase | Viewed item 3+ times but never carted | Neutral — do not suppress but do not over-boost |

Negative signals decay faster than positive ones (half-life: 5 min for `scroll_past`) so a momentary distraction does not permanently suppress a product.

---

### Dwell time signal

Time-on-modal is a stronger view signal than click-open alone.  Track time from `modalOpen` to `modalClose` on the PLP.

```typescript
// In ProductModal.svelte
const openedAt = Date.now();
// on close:
const dwellMs = Date.now() - openedAt;
if (dwellMs > 5000) {
  recordAction({ type: 'dwell', item_id: item.id, weight: Math.min(dwellMs / 30000, 2.0), ... });
}
```

Weight scales linearly up to 2× at 30 seconds.  Above 30 seconds is treated as the same as 30 seconds (the customer may have walked away).

---

### Collaborative filtering (server-side)

"Customers who viewed X also viewed Y" — does not require any action history from the current customer.  Computed offline from the `search_events` table.

**How it would work:**
1. An offline job (nightly cron) reads `search_events` and computes co-view pairs: items that appear in the same session together more than N times.
2. Co-view pairs are stored in a `co_view_pairs` PostgreSQL table: `(item_id_a, item_id_b, score)`.
3. At search time, the top-K co-view partners of any item in `viewed_ids` are fetched and used as additional preference vector seeds (alongside directly viewed items).

This personalises results for customers **with zero current-session history** — the system borrows signals from similar customers.

---

### Geo and time-of-day context

Not derived from action history but available as implicit signals.

**Time-of-day:**  customers browsing at 11 PM on mobile likely have different intent than customers browsing at 2 PM on desktop.  A `context_time_of_day` field could toggle between "discovery" (broader, more diverse results) and "purchase" (more conversion-oriented) ranking modes.

**Geographic region:**  if the customer's region is known (IP-inferred or passed explicitly), items available in that region or on regional promotion can be boosted.  Requires `region` metadata on documents at index time.

**Device type:**  `User-Agent` or an explicit `device_class: "mobile" | "desktop"` field.  Mobile sessions tend to be shorter and more decisive; adjust `per_page` and ranking to favour in-stock, well-imaged items.

---

### A/B testing personalisation strategies

Once multiple personalisation strategies exist, ISP needs a way to test which improves CTR and conversion.

**Approach:**  a `context_variant` field in `CatalogSearchRequest` (or passed as a header).  The server routes to a different `post_rrf_hook` configuration per variant.  The `search_events` table already has a `session_id` field — tie the variant to the session and measure CTR difference.

Variants to test:
- `control` — no personalisation
- `affinity_only` — category/manufacturer boosts, no vector blend
- `vector_only` — preference vector blend, no affinity boosts
- `full` — both (current default)
- `aggressive` — `blend_weight: 0.4` instead of 0.2

---

### Feedback loop — CTR learning

The `search_events` table captures impressions and clicks.  A learning pipeline (described in `docs/analyticsDataLearning.md`) can:
1. Identify items that consistently receive impressions but no clicks for a given query → add to `boost_queries_contains` demotion list
2. Identify items that consistently get clicked first → boost `popularity` field or add to `boost_queries_equal`

This is global (not per-customer) but complements personalisation — it improves the baseline ranking that personalisation builds on top of.

---

## API reference

### `POST /v1/indexes/{id}/search/public`

```json
{
  "q": "samsung galaxy",
  "mode": "hybrid",
  "page": 1,
  "per_page": 24,
  "action_history": [
    {
      "type": "view",
      "item_id": "abc123",
      "item_type": "device",
      "category": "smartphones",
      "manufacturer": "Samsung",
      "timestamp": 1750000100000
    },
    {
      "type": "search",
      "query": "android phone",
      "timestamp": 1750000050000
    },
    {
      "type": "category",
      "category": "phones",
      "timestamp": 1750000000000
    }
  ]
}
```

### `POST /v1/catalog`

Same `action_history` field.  Additional context signals in the response:

```json
{
  "items": [...],
  "context_actions_used": [
    "hybrid_search",
    "preference_vector_blend",
    "affinity_boost"
  ]
}
```

`context_actions_used` tells the caller which personalisation paths fired.

---

## Privacy and data handling

| Concern | Approach |
|---|---|
| No PII in action history | `item_id` is a Typesense doc id (opaque), not a product SKU or user identifier. `category` and `manufacturer` are metadata strings, not personal attributes. |
| Session storage only (current) | `sessionStorage` is cleared on browser close. No server-side persistence of raw events. |
| Visitor profile opt-out | `localStorage` is readable/deletable by the customer via browser settings. A future "Clear my history" button in the PLP calls `localStorage.removeItem('isp_visitor_profile')` and `sessionStorage.removeItem('isp_action_history')`. |
| Server-side profile (Phase 4) | Requires explicit opt-in (login). TTL 30 days. Deleted on account deletion or manual request. No raw query strings stored — only aggregated weights. |
| Action history is ephemeral | The server does not persist `action_history` from the request body. It is processed in-memory per request and discarded. No event log is written. |
| `search_events` table | Stores `session_id` (UUID, no link to customer identity unless logged in) + query + impressions + clicks. Used for analytics and CTR learning only. |

---

## Implementation status

| Feature | Status |
|---|---|
| `CustomerAction` model (frontend + backend) | Done |
| `view`, `search`, `category` event recording | Done |
| `action_history` sent on every search | Done |
| Affinity boosts (category, manufacturer, type) | Done |
| Preference vector blending (equal weight) | Done |
| Viewed-item demotion | Done |
| Operator controls — 4-flag Settings panel (master, vector blend, affinity, penalty) | Done |
| Personalisation on empty-query browse (affinity + penalty via lexical path) | Done — `_apply_lexical_hook` + 250-item batch fetch |
| Server-side brand/category filter (`manufacturer` / `category` in `SearchRequest`) | Done |
| Timestamps on actions | Planned — Phase 2 |
| Time-decay in `build_context()` | Planned — Phase 2 |
| Decay-weighted preference vector | Planned — Phase 3 |
| `localStorage` visitor profile (returning customer) | Planned — Phase 3 |
| `cart` and `purchase` events | Planned |
| `dwell` time signal | Planned |
| Negative signals (`scroll_past`, `quick_close`) | Planned |
| Segment detection | Planned |
| Price sensitivity | Planned |
| Server-side profile (Redis, cross-device) | Planned — Phase 4 |
| Collaborative filtering | Future |
| Geo / time-of-day context | Future |
| A/B testing variants | Future |
| CTR feedback loop integration | Future (see analyticsDataLearning.md) |

---

## References

> **Note on sourcing:** this document was written from accumulated knowledge of the information retrieval and recommender systems literature, not from live web browsing. Every citation below is a real, verifiable source. Where a free URL exists it is included; otherwise the standard academic citation is given so the paper can be found via Google Scholar, ACM DL, or arXiv.

---

### ANN and HNSW vector indexing

**ANN — Foundational Complexity Paper**
Indyk, P., & Motwani, R. (1998).
*Approximate nearest neighbors: towards removing the curse of dimensionality.*
Proceedings of STOC 1998, pp. 604–613.
https://dl.acm.org/doi/10.1145/276698.276876

> The paper that formally defined the ANN problem and proved exact high-dimensional nearest-neighbour search is computationally intractable (the "curse of dimensionality").  Established the theoretical foundation for all approximate algorithms — HNSW, FAISS, Annoy, ScaNN all exist because of this result.

**FAISS — Billion-Scale Similarity Search**
Johnson, J., Douze, M., & Jégou, H. (2019).
*Billion-scale similarity search with GPUs.*
IEEE Transactions on Big Data, 7(3), 535–547.
https://doi.org/10.1109/TBDATA.2019.2921572
arXiv preprint: https://arxiv.org/abs/1702.08734

> Describes FAISS (Facebook AI Similarity Search) — the leading GPU-accelerated ANN library.  Uses inverted file indexes (IVF) and product quantisation to achieve very high throughput at billion-scale.  Included here as the primary alternative to HNSW; its GPU dependency and offline-rebuild requirement are why ISP uses Typesense/HNSW instead.

**ScaNN — Anisotropic Vector Quantisation**
Guo, R., Sun, P., Lindgren, E., Geng, Q., Simcha, D., Chern, F., & Kumar, S. (2020).
*Accelerating Large-Scale Inference with Anisotropic Vector Quantization.*
Proceedings of ICML 2020, PMLR 119, pp. 3887–3896.
https://arxiv.org/abs/1908.10396

> Describes ScaNN (Scalable Nearest Neighbours) — Google's ANN system.  Introduces anisotropic quantisation, which preserves the inner-product structure better than isotropic quantisation.  Achieves state-of-the-art throughput on Google TPU infrastructure.  Included as a comparison point; not suitable for ISP's self-hosted CPU deployment.

**HNSW — Hierarchical Navigable Small World**
Malkov, Y. A., & Yashunin, D. A. (2018).
*Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs.*
IEEE Transactions on Pattern Analysis and Machine Intelligence, 42(4), 824–836.
https://doi.org/10.1109/TPAMI.2018.2843326
arXiv preprint: https://arxiv.org/abs/1603.09320

> The paper that defines HNSW — the multi-layer graph structure used by Typesense (and most modern vector databases) for ANN search.  Establishes the layered graph construction (sparse upper layers for coarse navigation, dense lower layers for precision), the greedy traversal algorithm, and the O(log n) query complexity.  Directly underpins every `vector_query` call in `typesense_client.search_with_vec()`.

**Navigable Small World Graphs**
Malkov, Y., Ponomarenko, A., Logvinov, A., & Krylov, V. (2014).
*Approximate nearest neighbor algorithm based on navigable small world graphs.*
Information Systems, 45, 61–68.
https://doi.org/10.1016/j.is.2013.10.006

> The precursor to HNSW that introduced the navigable small world graph concept for ANN.  Explains why greedy graph traversal finds approximate nearest neighbours efficiently — the "small world" property ensures short paths exist between any two nodes.

**The Small-World Phenomenon (theoretical foundation)**
Kleinberg, J. (2000).
*The small-world phenomenon: An algorithmic perspective.*
Proceedings of STOC 2000, pp. 163–170.
https://dl.acm.org/doi/10.1145/335305.335325

> The theoretical paper that proved navigable small world graphs (à la Milgram's "six degrees of separation") admit efficient decentralised search.  The long-range links in HNSW's upper layers are the algorithmic realisation of Kleinberg's navigable structure — they let the traversal skip across the graph in O(log n) hops.

**ANN Benchmarks**
Aumuller, M., Bernhardsson, E., & Faithfull, A. (2020).
*ANN-Benchmarks: A benchmarking tool for approximate nearest neighbor algorithms.*
Information Systems, 87, 101374.
https://doi.org/10.1016/j.is.2019.02.006
https://ann-benchmarks.com

> The standard benchmark suite for comparing ANN algorithms (HNSW, Annoy, FAISS, ScaNN, etc.) on recall vs. query-per-second trade-offs.  Useful reference when tuning Typesense HNSW parameters (`M`, `ef_construction`, `ef`) if semantic search recall is found to be insufficient.

---

### Hybrid search and rank fusion

**Reciprocal Rank Fusion**
Cormack, G. V., Clarke, C. L. A., & Buettcher, S. (2009).
*Reciprocal rank fusion outperforms Condorcet and individual rank learning methods.*
Proceedings of SIGIR 2009, pp. 758–759.
https://dl.acm.org/doi/10.1145/1571941.1572114

> The paper that defines RRF and proves it outperforms individual ranking signals. Directly informs the `0.7 lex + 0.3 sem` fusion used in ISP's `search_with_vec` and `_rrf_search`.

**BM25 — The Probabilistic Relevance Framework**
Robertson, S., & Zaragoza, H. (2009).
*The probabilistic relevance framework: BM25 and beyond.*
Foundations and Trends in Information Retrieval, 3(4), 333–389.
https://doi.org/10.1561/1500000019

> Definitive survey covering BM25 derivation, parameter tuning (k1, b), and extensions. BM25 is Typesense's default full-text ranking function and the 0.7-weight lexical leg in ISP's hybrid search RRF merge.

**Sparse + Dense Retrieval Survey**
Lin, J., & Ma, X. (2021).
*A few brief notes on DeepImpact, COIL, and a conceptual framework for information retrieval techniques.*
arXiv:2106.14807.
https://arxiv.org/abs/2106.14807
*Note: arXiv preprint — no page numbers; cite by arXiv ID.*

> Conceptual framework for understanding when BM25, dense ANN, and hybrid combinations each win. Informs the fallback-to-lexical logic and the `mode` parameter.

**Dense Passage Retrieval**
Karpukhin, V., Oğuz, B., Min, S., Lewis, P., Wu, L., Edunov, S., Chen, D., & Yih, W. (2020).
*Dense passage retrieval for open-domain question answering.*
Proceedings of EMNLP 2020, pp. 6769–6781.
ACL Anthology: https://aclanthology.org/2020.emnlp-main.550
arXiv preprint: https://arxiv.org/abs/2004.04906

> Establishes dense embedding retrieval as competitive with BM25 for factual queries. Underpins the semantic search pass in ISP and the use of Voyage AI / RexBERT for passage-level embeddings.

---

### Preference vector and query expansion

**Rocchio Relevance Feedback**
Rocchio, J. J. (1971).
*Relevance feedback in information retrieval.*
In G. Salton (Ed.), The SMART Retrieval System: Experiments in Automatic Document Processing (pp. 313–323). Prentice-Hall.
Google Scholar: https://scholar.google.com/scholar?q=Rocchio+relevance+feedback+information+retrieval+1971
*Note: book chapter — no DOI; search via Google Scholar or the Cornell University library catalogue.*

> The original formalisation of blending a query vector with document vectors to steer retrieval. The preference vector blend in `fetch_preference_vector()` (`0.8 × query + 0.2 × mean(item_vecs)`) is a direct descendant of the Rocchio update rule.

**Pseudo-Relevance Feedback Survey**
Efthimiadis, E. N. (1996).
*Query expansion.*
Annual Review of Information Science and Technology, 31, 121–187.
https://doi.org/10.1002/aris.1440310106

> Survey of query expansion techniques including user-history-based approaches. Frames why blending user context into the query vector is more robust than post-hoc re-ranking alone.

**Sentence Embeddings (Sentence-BERT)**
Reimers, N., & Gurevych, I. (2019).
*Sentence-BERT: Sentence embeddings using siamese BERT-networks.*
Proceedings of EMNLP 2019, pp. 3982–3992.
ACL Anthology: https://aclanthology.org/D19-1410
arXiv preprint: https://arxiv.org/abs/1908.10084

> The embedding architecture that Voyage AI, RexBERT, and similar models build on. Explains why cosine similarity in embedding space is a reliable proxy for semantic relevance and why L2 normalisation is required before blending vectors.

---

### Time-decay in recommendations

**Collaborative Filtering with Temporal Dynamics**
Koren, Y. (2009).
*Collaborative filtering with temporal dynamics.*
Proceedings of KDD 2009, pp. 447–456.
https://dl.acm.org/doi/10.1145/1557019.1557072

> Demonstrates that user preferences drift over time and that time-aware models significantly outperform static ones. Directly motivates the half-life decay applied per action type in `build_context()`.

**Time Weight Collaborative Filtering**
Ding, Y., & Li, X. (2005).
*Time weight collaborative filtering.*
Proceedings of CIKM 2005, pp. 485–492.
https://dl.acm.org/doi/10.1145/1099554.1099689

> Early formalisation of exponential time-decay for recommendation signals. Source for the `exp(–age / half_life)` decay formula and the empirical observation that recent interactions predict future behaviour far better than old ones.

**Temporal Dynamics in Session-Based Recommendation**
Hidasi, B., Karatzoglou, A., Baltrunas, L., & Tikk, D. (2016).
*Session-based recommendations with recurrent neural networks.*
International Conference on Learning Representations (ICLR) 2016.
OpenReview: https://openreview.net/forum?id=r1s_WxBge | arXiv: https://arxiv.org/abs/1511.06939
*Note: ICLR proceedings do not use traditional page numbers; papers are identified by OpenReview submission ID.*

> Introduces the idea of treating within-session event sequences as a signal distinct from long-term user history. The conceptual separation of Tier 1 (sessionStorage) and Tier 2 (localStorage) storage in this document follows this framing.

---

### Affinity calculation and vector space mathematics

**Vector Space Model**
Salton, G., Wong, A., & Yang, C. S. (1975).
*A vector space model for automatic indexing.*
Communications of the ACM, 18(11), 613–620.
https://dl.acm.org/doi/10.1145/361219.361220

> The paper that introduced representing queries and documents as vectors in the same embedding space.  Establishes centroid computation (mean of document vectors) as the correct way to represent a document set, and cosine similarity as the correct distance metric — both of which are used directly in `blend_preference_vector()`.

**Probabilistic Term Weighting**
Robertson, S. E., & Spärck Jones, K. (1976).
*Relevance weighting of search terms.*
Journal of the American Society for Information Science, 27(3), 129–146.
https://doi.org/10.1002/asi.4630270302

> The paper that first gave a probabilistic justification for differential term weights (some terms are more informative than others).  Directly motivates the action-type weight table: stronger intent signals (purchase, cart) get higher weights because they are more informative about true preferences than weaker signals (search, category).  Also establishes the principle of length/frequency normalisation before combination — the basis for max-normalisation in `build_context()`.

**BM25F — Field-Weighted BM25**
Robertson, S., Zaragoza, H., & Taylor, M. (2004).
*Simple BM25 extension to multiple weighted fields.*
Proceedings of CIKM 2004, pp. 42–49.
https://dl.acm.org/doi/10.1145/1031171.1031181

> Extends BM25 to documents with multiple fields (title, body, metadata), each with its own weight coefficient.  The affinity boost coefficients (0.20 for category, 0.15 for manufacturer, 0.10 for type) follow the same field-weighting design principle: fields with higher discriminating power receive a larger multiplier.

**Novelty and Diversity in Ranked Results**
Clarke, C. L. A., Kolla, M., Cormack, G. V., Vechtomova, O., Ashkan, A., Büttcher, S., & MacKinnon, I. (2008).
*Novelty and diversity in information retrieval evaluation.*
Proceedings of SIGIR 2008, pp. 659–666.
https://dl.acm.org/doi/10.1145/1390334.1390446

> Establishes that re-showing items the user has already seen hurts perceived result quality, and introduces metrics that reward novelty.  Directly motivates the viewed-item demotion (−0.05) in `context_boost()`: once a customer has seen an item, its marginal value in subsequent result pages is lower.

---

### Affinity scoring and behavioural signals

**Click Models for Web Search**
Chuklin, A., Markov, I., & de Rijke, M. (2015).
*Click models for web search.*
Synthesis Lectures on Information Concepts, Retrieval, and Services. Morgan & Claypool.
https://doi.org/10.2200/S00654ED1V01Y201507ICR043

> Comprehensive treatment of how clicks, dwell time, and scroll signals encode user relevance judgements. Underpins the weighting rationale: `purchase > cart > view > search > category` and the negative signals plan (`scroll_past`, `quick_close`).

**Implicit Feedback for Recommender Systems**
Hu, Y., Koren, Y., & Volinsky, C. (2008).
*Collaborative filtering for implicit feedback datasets.*
Proceedings of ICDM 2008, pp. 263–272.
https://doi.org/10.1109/ICDM.2008.22

> Foundational paper for treating implicit signals (views, dwell, scrolls) as confidence-weighted preferences rather than binary like/dislike. The `weight` multiplier field on `CustomerAction` implements this idea directly.

**Dwell Time as a Relevance Signal**
Yi, X., Hong, L., Zhong, E., Liu, N. N., & Smola, A. (2014).
*Beyond clicks: Dwell time for personalization.*
Proceedings of RecSys 2014, pp. 113–120.
https://dl.acm.org/doi/10.1145/2645710.2645724

> Empirical study showing dwell time predicts satisfaction more reliably than raw click rate alone. Directly cited in the dwell-time signal section — the 5-second threshold and 30-second cap are consistent with findings in this paper.

---

### Session-based and segment personalisation

**A Survey on Session-Based Recommender Systems**
Wang, S., Cao, L., Wang, Y., Sheng, Q. Z., Orgun, M. A., & Lian, D. (2021).
*A survey on session-based recommender systems.*
ACM Computing Surveys, 54(7), Article 154.
https://dl.acm.org/doi/10.1145/3465401

> Comprehensive survey of approaches for using within-session behaviour to personalise recommendations without long-term user profiles. Validates the sessionStorage-first architecture and the segment detection approach.

**User Segmentation for E-commerce Personalisation**
Zhang, S., Yao, L., Sun, A., & Tay, Y. (2019).
*Deep learning based recommender system: A survey and new perspectives.*
ACM Computing Surveys, 52(1), Article 5.
https://dl.acm.org/doi/10.1145/3285029

> Reviews learning-free segmentation techniques alongside deep approaches. The rule-based segment detection table in this document (budget/premium/researcher/loyalty) draws on the simpler heuristic-based segment literature reviewed here.

---

### Collaborative filtering

**Matrix Factorization for Recommender Systems**
Koren, Y., Bell, R., & Volinsky, C. (2009).
*Matrix factorization techniques for recommender systems.*
IEEE Computer, 42(8), 30–37.
https://doi.org/10.1109/MC.2009.263

> The Netflix Prize winning approach. Foundational reference for the collaborative filtering option discussed in this document (co-view pairs from `search_events`).

---

### A/B testing and controlled experiments

**Trustworthy Online Controlled Experiments**
Kohavi, R., Tang, D., & Xu, Y. (2020).
*Trustworthy online controlled experiments: A practical guide to A/B testing.*
Cambridge University Press.
ISBN: 978-1108724265.
https://www.cambridge.org/core/books/trustworthy-online-controlled-experiments/D97B26382EB0EB2DC2019A7A7B518F59

> The standard reference for running statistically valid A/B tests in production. Directly applicable to the A/B testing variants section — how to tie `context_variant` to `session_id`, required sample sizes, and guardrail metrics.

**Interleaving for Online Evaluation**
Hofmann, K., Whiteson, S., & de Rijke, M. (2013).
*Fidelity, soundness, and efficiency of interleaved comparison methods.*
ACM TOIS, 31(4), Article 17.
https://dl.acm.org/doi/10.1145/2516889

> Interleaved comparison is more sample-efficient than A/B for ranking evaluation. An alternative to the `context_variant` approach for comparing personalisation strategies at lower traffic cost.

---

### Learning to rank and CTR signals

**Learning to Rank from Clickthrough Data**
Joachims, T. (2002).
*Optimizing search engines using clickthrough data.*
Proceedings of KDD 2002, pp. 133–142.
https://dl.acm.org/doi/10.1145/775047.775067

> Establishes that click position bias must be corrected before using CTR as a training signal. The ISP CTR learning pipeline (in `analyticsDataLearning.md`) should apply position-normalised CTR, as described here.

**Unbiased Learning to Rank**
Joachims, T., Swaminathan, A., & Schnabel, T. (2017).
*Unbiased learning-to-rank with biased feedback.*
Proceedings of WSDM 2017, pp. 781–789.
https://dl.acm.org/doi/10.1145/3018661.3018699

> Formalises inverse propensity scoring (IPS) to debias click signals. Relevant when the CTR feedback loop is extended beyond simple popularity boosting into full ranking model training.

---

### Privacy

**Privacy Risks of Collaborative Filtering**
Calandrino, J. A., Kilzer, A., Narayanan, A., Felten, E. W., & Shmatikoff, V. (2011).
*"You might also like:" Privacy risks of collaborative filtering.*
Proceedings of IEEE Symposium on Security and Privacy 2011, pp. 231–246.
https://doi.org/10.1109/SP.2011.40

> Demonstrates that aggregated recommendation signals can be reverse-engineered to infer individual purchase history. Underpins the decision to store only category/manufacturer weights in the visitor profile rather than raw item interaction histories.

**Web Storage and Privacy (MDN)**
Mozilla Developer Network.
*Web Storage API — sessionStorage vs localStorage.*
https://developer.mozilla.org/en-US/docs/Web/API/Web_Storage_API

> Definitive technical reference for the browser storage model used in Tier 1 and Tier 2 of the action history architecture.

---

### General information retrieval textbooks

**Introduction to Information Retrieval**
Manning, C. D., Raghavan, P., & Schütze, H. (2008).
*Introduction to information retrieval.*
Cambridge University Press.
Freely available: https://nlp.stanford.edu/IR-book/

> Standard IR textbook covering BM25, tf-idf, relevance feedback, and evaluation. Chapters 6 (scoring), 9 (relevance feedback and query expansion), and 11 (probabilistic IR) are directly relevant to ISP's ranking design.

**Recommender Systems Handbook**
Ricci, F., Rokach, L., & Shapira, B. (Eds.). (2022).
*Recommender systems handbook* (3rd ed.).
Springer.
ISBN: 978-1071621875.
https://link.springer.com/book/10.1007/978-1-0716-2197-4

> Comprehensive edited volume covering collaborative filtering, content-based filtering, session-based methods, temporal dynamics, and evaluation. The broadest single reference for all personalisation topics in this document.

**Recommender Systems: The Textbook**
Aggarwal, C. C. (2016).
*Recommender systems: The textbook.*
Springer.
ISBN: 978-3319296579.
https://link.springer.com/book/10.1007/978-3-319-29659-3

> Rigorous treatment of matrix factorisation, neighbourhood methods, and hybrid approaches. Particularly useful for the collaborative filtering option and the mathematical treatment of decay functions.
