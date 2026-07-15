# Chat with RAG — Complete Design & Implementation

End-to-end documentation for the ISP conversational chat feature: why it exists, how it works, full UI design, architecture, data flow, and implementation details.

---

## Table of Contents

1. [The Problem with Plain LLM Chat](#1-the-problem-with-plain-llm-chat)
2. [What is RAG?](#2-what-is-rag)
3. [The Three Steps of RAG](#3-the-three-steps-of-rag)
4. [Real Examples](#4-real-examples)
5. [How Vector Embeddings Power Chat Retrieval](#5-how-vector-embeddings-power-chat-retrieval)
6. [How Semantic Search Makes Retrieval Smarter](#6-how-semantic-search-makes-retrieval-smarter)
7. [Complete System Architecture](#7-complete-system-architecture)
8. [UI Design](#8-ui-design)
9. [Data Flow — Request to Response](#9-data-flow--request-to-response)
10. [Backend Design](#10-backend-design)
11. [Frontend Design](#11-frontend-design)
12. [LLM Provider Design](#12-llm-provider-design)
13. [Settings Panel Design](#13-settings-panel-design)
14. [What the LLM Does and Does Not Do](#14-what-the-llm-does-and-does-not-do)
15. [Why This Matters for Your Store](#15-why-this-matters-for-your-store)

---

## 1. The Problem with Plain LLM Chat

A large language model (LLM) like ChatGPT, Claude, or Gemini is trained on a massive snapshot of the internet — text from websites, books, and articles up to a certain date. That training gives it general knowledge, but it has no idea what your store sells, what your current prices are, or which products are in stock today.

**Without RAG:**

```
Customer asks:  "What plants do you have that are safe for cats under $30?"

LLM says:       "I'm not sure what plants your store carries.
                 Generally, spider plants and Boston ferns are
                 considered pet-safe and are often affordable."
```

Two problems:
- The LLM is guessing from general knowledge, not your actual inventory
- If it guesses wrong, the customer may look for a product you do not carry, or miss one you do

**The LLM only knows what it was trained on. It has no live connection to your store.**

---

## 2. What is RAG?

**RAG = Retrieval Augmented Generation**

| Word | Plain English |
|------|---------------|
| **Retrieval** | Look up relevant products from your database |
| **Augmented** | Add those products to the AI's prompt as context |
| **Generation** | The AI writes its answer using that real data |

The simplest analogy: **a well-trained employee with a product catalog.**

A new employee on their first day does not know every product in the store. But if you hand them a catalog and tell them "look it up before you answer," they can give accurate answers to any customer question.

- **Without RAG** — the AI answers from memory (may be wrong or outdated)
- **With RAG** — the AI looks up the answer in your product index first, then answers

Your Typesense index is the catalog. The LLM is the employee. RAG is the act of handing them the catalog before they speak.

---

## 3. The Three Steps of RAG

Every chat message goes through three steps before the customer sees a response:

```
Customer: "What plants are safe for cats under $30?"
                    │
                    ▼
        ┌───────────────────────────┐
        │     Step 1: RETRIEVE      │
        │                           │
        │  Convert question into a  │
        │  1024-d vector via        │
        │  Voyage AI embedding      │
        │                           │
        │  Run hybrid search on     │
        │  Typesense index:         │
        │  - keyword match on       │
        │    title + content        │
        │  - vector similarity on   │
        │    vec field              │
        │                           │
        │  Return top 5 hits        │
        └───────────┬───────────────┘
                    │
                    │  Retrieved products:
                    │  1. Spider Plant   – $18 – pet safe ✓
                    │  2. Boston Fern    – $22 – pet safe ✓
                    │  3. Calathea       – $28 – pet safe ✓
                    │  4. Areca Palm     – $29 – pet safe ✓
                    │  5. Peperomia      – $15 – pet safe ✓
                    │
                    ▼
        ┌───────────────────────────┐
        │     Step 2: AUGMENT       │
        │                           │
        │  Build a system prompt    │
        │  that includes:           │
        │  - Role instruction       │
        │  - The 5 retrieved        │
        │    products as context    │
        │  - The customer's         │
        │    question               │
        │                           │
        │  Send full prompt to LLM  │
        └───────────┬───────────────┘
                    │
                    ▼
        ┌───────────────────────────┐
        │     Step 3: GENERATE      │
        │                           │
        │  LLM reads the real       │
        │  product data and writes  │
        │  a helpful, accurate,     │
        │  conversational answer    │
        │                           │
        │  Streams tokens back to   │
        │  the browser as SSE       │
        └───────────┬───────────────┘
                    │
                    ▼
        "Great news — we have several pet-safe
         plants under $30! The Spider Plant ($18)
         and Peperomia ($15) are perfect beginner
         options. Boston Fern ($22) and Calathea
         ($28) are more dramatic choices."

         [Spider Plant $18] [Peperomia $15]
         [Boston Fern $22]  [Calathea $28]
```

The LLM never guesses. It only uses the products you handed it.

---

## 4. Real Examples

### Example A — Plant Store (Pet Safety Question)

**Without RAG:**
```
Customer:  "Do you have any trailing plants that are easy to care for?"

LLM:       "Pothos and Tradescantia are popular trailing plants
            that are generally easy to care for."
            
            ← Generic internet advice. The LLM has no idea what
              the store carries or whether those plants are in stock.
```

**With RAG:**
```
Customer:  "Do you have any trailing plants that are easy to care for?"

Step 1 — Typesense retrieves:
  • Pothos Golden     – $14 – growth_style: trailing – difficulty: beginner
  • Hoya Carnosa      – $24 – growth_style: trailing – difficulty: easy
  • String of Pearls  – $19 – growth_style: trailing – difficulty: moderate

Step 2 — These products are added to the prompt

Step 3 — LLM answers:
  "Yes! We have three great trailing options.
   Pothos Golden ($14) is nearly impossible to kill
   — perfect for beginners. Hoya Carnosa ($24) is
   easy to care for and produces beautiful waxy
   blooms. String of Pearls ($19) is a showstopper
   but needs a little more attention."

  [Pothos Golden $14] [Hoya Carnosa $24] [String of Pearls $19]
```

---

### Example B — Plant Store (No Match Scenario)

```
Customer:  "Do you have any bonsai trees?"

Step 1 — Typesense finds 0 matching products
          (store does not carry bonsai)

Step 2 — Prompt tells LLM: no products found

Step 3 — LLM answers:
  "We don't currently carry bonsai trees. If you're
   looking for a compact, sculptural plant, I could
   suggest some alternatives — would you like to see
   those?"

  (No product cards shown — nothing to display)
```

Without RAG, the LLM might invent a bonsai product that does not exist.

---

### Example C — Phone Store (Budget + Use Case)

**Without RAG:**
```
Customer:  "What's a good Samsung phone for gaming under $800?"

LLM:       "The Samsung Galaxy S23 is a solid gaming phone."
            
            ← Training data from last year. Store might not carry S23.
              Price might be wrong. Newer model might be available.
```

**With RAG:**
```
Customer:  "What's a good Samsung phone for gaming under $800?"

Step 1 — Typesense retrieves:
  • Galaxy S24+   – $799 – Samsung – snapdragon 8 Gen 3, 120Hz display
  • Galaxy S24 FE – $649 – Samsung – gaming optimised, 6.7" display
  • Galaxy A55 5G – $449 – Samsung – gaming mode, 5000mAh battery

Step 2 — Products added to prompt

Step 3 — LLM answers:
  "For gaming under $800 we have three great Samsung
   options. The S24+ ($799) is the most powerful —
   top-tier chipset, brilliant 120Hz display, ideal
   for demanding games. The S24 FE ($649) is a great
   mid-range pick with solid performance. If budget
   is the priority, the A55 ($449) has a dedicated
   gaming mode and impressive battery life."

  [Galaxy S24+ $799] [Galaxy S24 FE $649] [Galaxy A55 $449]
```

---

### Example D — Follow-up Conversation (Multi-turn)

```
Turn 1:
  Customer:   "Show me pet safe plants"
  Assistant:  "Here are our pet-safe options..."
              [Spider Plant] [Boston Fern] [Calathea]

Turn 2:
  Customer:   "Which of those is cheapest?"
  
  → The full conversation history is sent with Turn 2
  → LLM knows what "those" refers to (the plants from Turn 1)
  
  Assistant:  "The Spider Plant at $18 is the most affordable
               of the three I mentioned."
```

The chat history travels with every request so the LLM can handle follow-up questions naturally.

---

## 5. How Vector Embeddings Power Chat Retrieval

This section answers: **why does RAG chat use the same Voyage AI embeddings as search, and what exactly are those embeddings doing?**

### What is a vector embedding?

A vector embedding is a list of numbers that represents the *meaning* of a piece of text. Two sentences that mean the same thing — even if worded completely differently — produce similar lists of numbers. Two sentences with unrelated meanings produce very different numbers.

```
"safe for pets"          → [0.23, -0.14,  0.87, 0.11, -0.63, ...]   ← 1024 numbers
"non-toxic to animals"   → [0.21, -0.13,  0.85, 0.10, -0.61, ...]   ← almost identical
"best gaming phone"      → [-0.55, 0.67, -0.21, 0.90,  0.14, ...]   ← completely different
```

The model that produces these numbers is **Voyage AI** (specifically `voyage-3` or `voyage-3-lite`). It was trained on billions of sentences to learn that "safe for pets" and "non-toxic to animals" share meaning, even though they share only one word ("for").

### How embeddings are created at index time

When you crawl or ingest products into ISP, each product document goes through Voyage AI before it is stored in Typesense:

```
Spider Plant product document
│
│  title:   "Spider Plant"
│  content: "Air-purifying trailing plant. Safe for cats
│            and dogs. Thrives in indirect light. Great
│            for beginners."
│
▼  Voyage AI embed_document()
│
│  vec: [0.23, -0.14, 0.87, 0.11, -0.63, 0.40, ...]   ← 1024 numbers
│
▼  Stored in Typesense
│
│  {
│    "id":     "plant-001",
│    "title":  "Spider Plant",
│    "content": "Air-purifying trailing...",
│    "vec":    [0.23, -0.14, 0.87, ...]         ← the vector lives here
│  }
```

Every product in the index has its own 1024-number vector. The vector captures the semantic meaning of the combined title and content.

### How embeddings are used at chat time

When a customer types a question in the chat, the **exact same Voyage AI model** converts their question into a vector:

```
Customer types: "What plants won't hurt my cat?"

Voyage AI embed_query()
    │
    ▼
[0.21, -0.13, 0.85, 0.09, -0.60, 0.39, ...]   ← 1024 numbers

This vector is compared against every product vector in Typesense.
```

The comparison finds products whose vectors are numerically closest to the question vector. Because "won't hurt my cat" and "safe for cats and dogs" were embedded by the same model, their vectors are close — and Spider Plant is retrieved.

### The math behind vector similarity

Typesense uses **HNSW** (Hierarchical Navigable Small World) — a graph-based index that makes approximate nearest-neighbor search fast even with millions of products.

The similarity score between two vectors is computed as **cosine similarity**:

```
cosine_similarity(query_vec, product_vec) =
    (query_vec · product_vec) / (|query_vec| × |product_vec|)

Range: -1 (opposite meaning) to +1 (identical meaning)
```

For chat RAG, Typesense returns the top 5 products by this score.

### Hybrid search: vector + keyword combined

Chat retrieval does not rely on vector search alone. It combines both signals using **Reciprocal Rank Fusion (RRF)**:

```
Customer asks: "monstera thai constellation price"

Keyword search ranks:
  1. Monstera Thai Constellation  ← exact title match
  2. Monstera Deliciosa
  3. Monstera Adansonii

Vector search ranks:
  1. Monstera Thai Constellation  ← semantic match
  2. Monstera Albo Variegata      ← visually similar plant
  3. Monstera Deliciosa

RRF combines both:
  Monstera Thai Constellation  → score 0.98  ← appears #1 in both lists
  Monstera Deliciosa           → score 0.71  ← appears in both, lower
  Monstera Adansonii           → score 0.54  ← keyword only
  Monstera Albo Variegata      → score 0.48  ← vector only
```

This matters for chat because customers use natural language (good for vector search) but sometimes also type exact product names or SKUs (good for keyword search). Combining both gives the best retrieval for all query types.

### Why the same embeddings work for both search and chat

ISP uses Voyage AI for two separate features:

| Feature | Where embedding is used |
|---------|------------------------|
| Semantic search bar | Embed the search query → find visually/semantically matching products |
| RAG chat retrieval | Embed the chat question → find contextually matching products to give to the LLM |

Because the same model embeds both products (at index time) and queries (at request time), the vector space is consistent. A product embedded with `voyage-3` at index time will always be compared against a question embedded with `voyage-3` at chat time — both live in the same 1024-dimensional space.

If you change the embedding model, you would need to re-index all products with the new model. The LLM provider can be swapped freely without touching the embeddings.

### Why the LLM never sees the vectors

```
Step 1 — Retrieval (Typesense + Voyage AI):
  [0.21, -0.13, 0.85, ...]  ← vector is used here, then discarded

Step 2 — Context building:
  "1. Spider Plant — $18 — Safe for cats and dogs. Trailing. Beginner-friendly."
                        ← plain text extracted from the product document

Step 3 — LLM receives plain text only:
  [system prompt with product text]
  [customer's question in plain text]
```

Vectors are a retrieval tool, not a representation the LLM uses. The LLM receives the same plain text description a human would read on the product page. The vector's job is finished once Typesense identifies which products are relevant.

### End-to-end embedding flow

```
INDEX TIME (once per product)
─────────────────────────────
Product title + content
        │
        ▼  Voyage AI embed_document()
        │
   [1024-d vector]
        │
        ▼  stored in Typesense vec field


CHAT TIME (every message)
──────────────────────────
Customer message: "plants safe for cats under $30"
        │
        ▼  Voyage AI embed_query()
        │
   [1024-d vector]
        │
        ▼  Typesense hybrid search
        │   - HNSW ANN for vector similarity
        │   - BM25 for keyword match
        │   - RRF combines both
        │
   Top 5 matching products (plain text, not vectors)
        │
        ▼  Built into system prompt
        │
   LLM writes answer using product text
        │
        ▼  Streams tokens back via SSE
        │
   Browser renders product cards + text
```

Standard keyword search breaks when customers describe what they want in their own words rather than typing the exact product name. Semantic search understands meaning, not just words.

### Keyword search vs. semantic search in retrieval

| Customer types | Keyword match | Semantic match |
|----------------|--------------|----------------|
| "plants that won't hurt my cat" | Fails — "won't hurt" not in any title | Finds `pet_safe: true` plants |
| "something easy for my first plant" | Fails — "first plant" not in titles | Finds `care_difficulty: beginner` |
| "trailing green thing for a shelf" | Fails — too vague | Finds `growth_style: trailing` plants |
| "low maintenance office plant" | Partial | Finds beginner plants + low light plants |
| "budget phone good for photos" | Partial | Finds camera-spec phones sorted by price |
| "phone that lasts all day" | Fails — no product says this | Finds high battery capacity phones |
| "something like an iPhone but cheaper" | Fails | Finds premium Android alternatives |

Semantic search means the retrieval step finds the right products even when the customer's words do not exactly match the product description. The LLM then gets accurate context and writes a helpful answer.

### How this works technically

When a product is indexed, Voyage AI converts the title and description into a 1024-dimension vector — a mathematical representation of its meaning. When a customer types a question, the same Voyage AI model converts that question into a vector. Typesense then finds products whose vectors are closest to the question's vector in 1024-dimensional space.

```
"pet safe trailing plant"
        │
        ▼  Voyage AI embed_query()
        │
  [0.23, -0.14, 0.87, 0.11, ...]  ← 1024 numbers representing meaning
        │
        ▼  Typesense ANN search (HNSW index)
        │
  Finds vectors closest in meaning:
    Spider Plant vec:   similarity 0.94  ✓
    Pothos vec:         similarity 0.91  ✓
    Snake Plant vec:    similarity 0.88  ✓
    iPhone case vec:    similarity 0.02  ✗  (different domain entirely)
```

---

## 6. How Semantic Search Makes Retrieval Smarter

Standard keyword search breaks when customers describe what they want in their own words rather than typing the exact product name. Semantic search understands meaning, not just words.

### Keyword search vs. semantic search in retrieval

| Customer types | Keyword match | Semantic match |
|----------------|--------------|----------------|
| "plants that won't hurt my cat" | Fails — "won't hurt" not in any title | Finds `pet_safe: true` plants |
| "something easy for my first plant" | Fails — "first plant" not in titles | Finds `care_difficulty: beginner` |
| "trailing green thing for a shelf" | Fails — too vague | Finds `growth_style: trailing` plants |
| "low maintenance office plant" | Partial | Finds beginner plants + low light plants |
| "budget phone good for photos" | Partial | Finds camera-spec phones sorted by price |
| "phone that lasts all day" | Fails — no product says this | Finds high battery capacity phones |
| "something like an iPhone but cheaper" | Fails | Finds premium Android alternatives |

Semantic search means the retrieval step finds the right products even when the customer's words do not exactly match the product description. The LLM then gets accurate context and writes a helpful answer.

---

## 7. Complete System Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                          BROWSER (PLP)                                 │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  PlantChat.svelte                                                │  │
│  │                                                                  │  │
│  │  chatStore.ts ──────── message history, loading state           │  │
│  │  chatController.ts ─── SSE stream reader, store updater         │  │
│  └──────────────────────────┬───────────────────────────────────────┘  │
│                             │  POST /api/v1/indexes/{id}/chat          │
│                             │  { query, messages[] }                   │
└─────────────────────────────┼──────────────────────────────────────────┘
                              │  SSE stream
                              │  data: {"type":"products","products":[]}
                              │  data: {"type":"text","content":"Great"}
                              │  data: [DONE]
                              │
┌─────────────────────────────┼──────────────────────────────────────────┐
│                    ISP BACKEND (FastAPI)                                │
│                             │                                          │
│  routers/chat.py ───────────┘                                          │
│       │  POST /v1/indexes/{index_id}/chat                              │
│       │  StreamingResponse (text/event-stream)                         │
│       │                                                                │
│       ▼                                                                │
│  services/infra/chat/service.py                                        │
│       │                                                                │
│       ├── Step 1: _search_context(query, index_id)                     │
│       │       │                                                        │
│       │       ├── embed_query(query)  ──────────────► Voyage AI API   │
│       │       │   returns 1024-d vector                ◄──────────────│
│       │       │                                                        │
│       │       └── typesense.search()  ─────────────►  Typesense       │
│       │           hybrid: keyword + vector             ◄──────────────│
│       │           returns top 5 hits                                   │
│       │                                                                │
│       ├── Step 2: _system_prompt(context)                              │
│       │       Assembles: role + product list + question                │
│       │                                                                │
│       └── Step 3: route to provider                                    │
│               │                                                        │
│               ├── anthropic_chat.py  ──────────────►  Anthropic API   │
│               ├── google_chat.py     ──────────────►  Gemini API      │
│               └── openai_compat.py   ──────────────►  OpenAI / Groq   │
│                                          OR local ►  Ollama / vLLM    │
│                                                                        │
│  services/domain/settings_service.py                                   │
│       chat_provider, chat_model, chat_api_key, chat_endpoint           │
│       stored in app_settings (Postgres), cached in-process             │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────┼──────────────────────────────────────────┐
│                    ISP ADMIN UI                                         │
│                                                                        │
│  Settings → Chat Model tab                                             │
│       GET/PUT /v1/settings/chat                                        │
│       Configure provider, model, API key, endpoint                     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 8. UI Design

### 7.1 Floating Chat Button (Closed State)

The chat entry point is a floating action button in the bottom-right corner of the PLP page. It does not disrupt the existing search, filter, or product grid layout.

```
┌─────────────────────────────────────────────────────────────┐
│  [Search bar                                    ]           │
│                                                             │
│  [Filter]  [Sort]                                           │
│                                                             │
│  ┌───────┐  ┌───────┐  ┌───────┐  ┌───────┐               │
│  │       │  │       │  │       │  │       │                │
│  │ Plant │  │ Plant │  │ Plant │  │ Plant │                │
│  │ Card  │  │ Card  │  │ Card  │  │ Card  │                │
│  └───────┘  └───────┘  └───────┘  └───────┘               │
│                                                             │
│  ┌───────┐  ┌───────┐  ┌───────┐  ┌───────┐               │
│  │       │  │       │  │       │  │       │                │
│  │ Plant │  │ Plant │  │ Plant │  │ Plant │                │
│  │ Card  │  │ Card  │  │ Card  │  │ Card  │                │
│  └───────┘  └───────┘  └───────┘  └───────┘               │
│                                                             │
│                                          ┌───────────────┐ │
│                                          │ 🤖  Ask AI    │ │
│                                          └───────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

- Background: dark green `#2d6a4f`
- Text: white, `Ask AI` label
- On hover: darkens, lifts 2px
- On click: opens chat panel, button changes to `✕`

---

### 7.2 Chat Panel (Open State — Empty)

```
                              ┌──────────────────────────────┐
                              │ 🌿 Plant Expert      [🗑] [✕] │
                              │ Ask anything about our plants │
                              ├──────────────────────────────┤
                              │                              │
                              │  Hi! I can help you find     │
                              │  the perfect plant.          │
                              │                              │
                              │  Try one of these:           │
                              │                              │
                              │ ┌──────────────────────────┐ │
                              │ │ What plants are safe      │ │
                              │ │ for cats?                 │ │
                              │ └──────────────────────────┘ │
                              │ ┌──────────────────────────┐ │
                              │ │ Show me low-light options │ │
                              │ └──────────────────────────┘ │
                              │ ┌──────────────────────────┐ │
                              │ │ Easy plants for beginners │ │
                              │ └──────────────────────────┘ │
                              │                              │
                              ├──────────────────────────────┤
                              │ [ Ask about plants…  ]  [↑] │
                              └──────────────────────────────┘
                                              ▲
                                       [✕  Ask AI]
```

**Panel dimensions:** 380px wide × 560px max height  
**Position:** fixed, bottom-right, above the FAB button  
**Animation:** slides up + fades in (0.18s)

---

### 7.3 Chat Panel — During Conversation

```
                              ┌──────────────────────────────┐
                              │ 🌿 Plant Expert      [🗑] [✕] │
                              │ Ask anything about our plants │
                              ├──────────────────────────────┤
                              │                              │
                              │              What plants are  │
                              │              safe for cats? 👤│
                              │                              │
                              │ 🌿                           │
                              │  ┌────────┐┌────────┐┌──────┐│
                              │  │[image] ││[image] ││[img] ││
                              │  │Spider  ││Boston  ││Calat ││
                              │  │Plant   ││Fern    ││hea   ││
                              │  │ $18    ││ $22    ││ $28  ││
                              │  └────────┘└────────┘└──────┘│
                              │  ┌────────────────────────┐  │
                              │  │ Great news — we have   │  │
                              │  │ several pet-safe plants │  │
                              │  │ under $30. Spider Plant │  │
                              │  │ ($18) is perfect for   │  │
                              │  │ beginners...           │  │
                              │  └────────────────────────┘  │
                              │                              │
                              │              Which is        │
                              │              cheapest? 👤    │
                              │                              │
                              │ 🌿                           │
                              │  ┌────────────────────────┐  │
                              │  │ The Spider Plant at    │  │
                              │  │ $18 is the most        │  │
                              │  │ affordable option.     │  │
                              │  └────────────────────────┘  │
                              │                              │
                              ├──────────────────────────────┤
                              │ [ Ask about plants…  ]  [↑] │
                              └──────────────────────────────┘
```

---

### 7.4 Chat Panel — Typing / Loading State

```
                              ├──────────────────────────────┤
                              │              Do you have any  │
                              │              monstera? 👤     │
                              │                              │
                              │ 🌿                           │
                              │  ┌────────┐┌────────┐        │
                              │  │[image] ││[image] │        │
                              │  │Monstera││Monstera│        │
                              │  │Delicio ││Thai    │        │
                              │  │ $34    ││ $89    │        │
                              │  └────────┘└────────┘        │
                              │  ┌────────────────────────┐  │
                              │  │  ●  ●  ●               │  │  ← typing indicator
                              │  └────────────────────────┘  │    (animated dots)
                              │                              │
                              ├──────────────────────────────┤
                              │ [ Ask about plants…  ] [⟳]  │  ← spinner on send button
                              └──────────────────────────────┘
```

The product cards render **immediately** when the first SSE chunk arrives (type: `products`), before the LLM has finished writing. The customer can start reading and clicking product cards while the text streams in.

---

### 7.5 Product Card Design (Inside Chat)

```
┌──────────────┐
│              │   ← 100×100px image (aspect-ratio: 1:1)
│   [image]    │      object-fit: cover
│              │      background: #f4f8f5 placeholder
│              │
├──────────────┤
│ Spider Plant │   ← title, 10px, 2-line clamp
│ $18.00       │   ← price, 10px bold, green
└──────────────┘
```

- Cards scroll horizontally if more than 3 fit
- Each card is a link to the product page (`target="_blank"`)
- Hover: lifts 1px, adds shadow

---

### 7.6 Message Bubble Design

```
User bubble (right-aligned):
                    ┌─────────────────────┐
                    │  What plants are    │  background: #2d6a4f (dark green)
                    │  safe for cats?     │  color: white
                    └─────────────────────┘ 👤
                              border-bottom-right-radius: 4px (chat tail)

Assistant bubble (left-aligned):
🌿 ┌─────────────────────┐
   │  Great news — we   │  background: #f2fbf5 (light green)
   │  have several...   │  border: 1px solid #d8f3dc
   └─────────────────────┘
   border-bottom-left-radius: 4px (chat tail)
```

---

### 7.7 Mobile Layout

On screens narrower than 480px the panel expands to fill the full width:

```
┌───────────────────────────┐
│ 🌿 Plant Expert   [🗑][✕] │
│ Ask anything...           │
├───────────────────────────┤
│                           │
│         messages          │
│                           │
├───────────────────────────┤
│ [ Ask about plants… ] [↑] │
└───────────────────────────┘
▲
[✕ Ask AI]  (FAB at bottom right, smaller padding)
```

---

## 9. Data Flow — Request to Response

### Complete sequence diagram

```
Browser                 ISP Backend              Voyage AI        Typesense          LLM
   │                        │                        │                │               │
   │  POST /chat            │                        │                │               │
   │  {query, messages}     │                        │                │               │
   │───────────────────────►│                        │                │               │
   │                        │                        │                │               │
   │                        │  embed_query(query)    │                │               │
   │                        │───────────────────────►│                │               │
   │                        │  1024-d vector         │                │               │
   │                        │◄───────────────────────│                │               │
   │                        │                        │                │               │
   │                        │  hybrid_search(vec, q) │                │               │
   │                        │────────────────────────────────────────►│               │
   │                        │  top 5 hits            │                │               │
   │                        │◄────────────────────────────────────────│               │
   │                        │                        │                │               │
   │                        │  build_system_prompt() │                │               │
   │                        │  (products → context)  │                │               │
   │                        │                        │                │               │
   │  SSE: products[]       │  stream(system,        │                │               │
   │◄───────────────────────│         messages)      │                │               │
   │  (cards render now)    │───────────────────────────────────────────────────────► │
   │                        │                        │                │               │
   │  SSE: text chunk 1     │  token 1               │                │               │
   │◄───────────────────────│◄─────────────────────────────────────────────────────── │
   │  SSE: text chunk 2     │  token 2               │                │               │
   │◄───────────────────────│◄─────────────────────────────────────────────────────── │
   │  SSE: text chunk n     │  token n               │                │               │
   │◄───────────────────────│◄─────────────────────────────────────────────────────── │
   │                        │  [stream ends]         │                │               │
   │  SSE: [DONE]           │                        │                │               │
   │◄───────────────────────│                        │                │               │
   │                        │                        │                │               │
```

### SSE event types

Every server-sent event is a JSON object on a `data:` line:

```
data: {"type": "products", "products": [
         {"id":"plant-001","title":"Spider Plant","price":"18","url":"...","image":"..."},
         {"id":"plant-002","title":"Boston Fern", "price":"22","url":"...","image":"..."}
       ]}

data: {"type": "text", "content": "Great news"}
data: {"type": "text", "content": " — we have"}
data: {"type": "text", "content": " several pet-safe"}
data: {"type": "text", "content": " plants..."}

data: [DONE]
```

The browser renders product cards as soon as the first `products` event arrives, and appends text to the assistant bubble as each `text` event arrives. This creates the "typing" streaming effect.

---

## 10. Backend Design

### File structure

```
backend/src/isp/
│
├── routers/
│   ├── chat.py                  ← POST /v1/indexes/{id}/chat (SSE endpoint)
│   └── settings.py              ← GET/PUT /v1/settings/chat (added)
│
├── services/
│   ├── domain/
│   │   └── settings_service.py  ← AVAILABLE_CHAT_MODELS, get/set/load_chat_config()
│   │
│   └── infra/
│       └── chat/
│           ├── __init__.py
│           ├── service.py           ← dispatcher + RAG pipeline
│           └── providers/
│               ├── __init__.py
│               ├── anthropic_chat.py    ← Anthropic SDK (~35 lines)
│               ├── google_chat.py       ← Google Generative AI SDK (~40 lines)
│               └── openai_compat.py     ← OpenAI SDK — handles OpenAI, Groq,
│                                          and all self-hosted servers (~30 lines)
│
└── auth/
    └── middleware.py            ← /v1/indexes/{id}/chat added to public routes
```

### Why providers are split into separate files

Each paid provider uses a completely different SDK with different authentication, streaming format, and message structure:

| Provider file | SDK | Unique aspects |
|---------------|-----|----------------|
| `anthropic_chat.py` | `anthropic` | `system` is a top-level field, not a message. Uses `client.messages.stream()`. Text arrives via `stream.text_stream`. |
| `google_chat.py` | `google-generativeai` | Message format uses `parts` not `content`. `role: "model"` not `"assistant"`. No native async streaming — wrapped in `asyncio.to_thread`. |
| `openai_compat.py` | `openai` | Handles OpenAI, Groq, and all self-hosted servers (Ollama, vLLM, LM Studio, LocalAI). Only the `base_url` differs. |

Self-hosted servers all expose an OpenAI-compatible API, so they share one file:

```
openai_compat.py
  └── stream(system, messages, model, api_key, base_url=None)
        │
        ├── provider="openai"      → base_url=None    (default: api.openai.com)
        ├── provider="groq"        → base_url="https://api.groq.com/openai/v1"
        └── provider="self_hosted" → base_url=<user endpoint>
```

### service.py — the dispatcher

```python
# Decision tree in service.py chat_stream():

if provider == "none":
    yield error message
    return

cards, context = await _search_context(query, index_id)
yield {"type": "products", "products": cards}           # ← product cards first

system = _system_prompt(context)

if   provider == "anthropic":   gen = anthropic_chat.stream(...)
elif provider == "google":      gen = google_chat.stream(...)
elif provider == "groq":        gen = openai_compat.stream(..., base_url=GROQ_URL)
elif provider == "self_hosted": gen = openai_compat.stream(..., base_url=user_url)
else:                           gen = openai_compat.stream(...)   # openai

async for chunk in gen:
    yield {"type": "text", "content": chunk}
```

### The system prompt

```
You are a helpful product expert assistant.
Answer questions using only the products listed below.
Be concise — 2 to 3 sentences maximum.
If no product matches the question, say so honestly.

Products:
1. Spider Plant
   A low-maintenance trailing plant safe for pets.
   Great for beginners.

2. Boston Fern
   Lush, elegant fern. Thrives in humidity.
   Pet safe, needs indirect light.

3. Calathea Ornata
   Striking leaf patterns. Non-toxic to pets.
   ...
```

The LLM is explicitly told to use **only** the provided products. It cannot invent products not in the list.

### Why multi_search is used for retrieval

Typesense limits GET request query strings to 4000 characters. A 1024-float vector serialised as `vec:([0.23,-0.14,...], k:5)` exceeds that limit, causing a 400 error. The chat retrieval uses `client.multi_search.perform()` (POST body) instead of `documents.search()` (GET query string) to avoid this. The two passes (lexical + semantic) are sent in a single POST body and merged via RRF.

### API endpoint

```
POST /v1/indexes/{index_id}/chat
Content-Type: application/json

Request body:
{
  "query":    "What plants are safe for cats?",
  "messages": [                              ← prior conversation turns
    {"role": "user",      "content": "Show me trailing plants"},
    {"role": "assistant", "content": "Here are some trailing options..."}
  ]
}

Response:
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no

data: {"type": "products", "products": [...]}
data: {"type": "text",     "content": "Great"}
data: {"type": "text",     "content": " news"}
data: [DONE]
```

Auth: **public** — no JWT required. This endpoint is called by PLP shoppers who do not have admin tokens.

---

## 11. Frontend Design

### File structure

```
plp/src/
│
├── lib/
│   ├── stores/
│   │   └── chatStore.ts          ← open/closed, message history, loading state
│   │
│   └── controller/
│       └── chatController.ts     ← SSE stream reader, updates store
│
└── components/
    └── plants/
        └── PlantChat.svelte      ← full chat UI (FAB + panel)
```

### chatStore.ts — state shape

```typescript
interface ProductCard {
  id:    string;
  title: string;
  price: string;
  url:   string;
  image: string;
}

interface ChatMessage {
  role:      'user' | 'assistant';
  content:   string;
  products?: ProductCard[];      // only on assistant messages, from RAG
}

chatOpenStore     = writable<boolean>(false)       // panel open/closed
chatMessagesStore = writable<ChatMessage[]>([])    // full conversation
chatLoadingStore  = writable<boolean>(false)       // streaming in progress
```

### chatController.ts — stream handling

```typescript
async function sendChatMessage(query: string): Promise<void> {
  // 1. Snapshot history for request
  const history = get(chatMessagesStore);

  // 2. Add user message + empty assistant placeholder immediately
  chatMessagesStore.update(msgs => [
    ...msgs,
    { role: 'user',      content: query },
    { role: 'assistant', content: '', products: [] },
  ]);
  chatLoadingStore.set(true);

  // 3. Open SSE stream
  const res = await fetch(`/api/v1/indexes/${indexId}/chat`, {  // indexId passed as prop per page
    method: 'POST',
    body:   JSON.stringify({ query, messages: history }),
  });

  const reader = res.body.getReader();

  // 4. Read and parse events
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    for (const line of decode(value)) {
      const chunk = JSON.parse(line.slice(6));   // "data: {...}"

      if (chunk.type === 'products')
        // renders product cards above the text bubble
        updateLastMessage(last => ({ ...last, products: chunk.products }));

      if (chunk.type === 'text')
        // appends tokens to the text bubble as they stream in
        updateLastMessage(last => ({ ...last, content: last.content + chunk.content }));
    }
  }
}
```

### Component responsibilities

There are two chat components — one generic, one plant-specific:

```
src/components/
│
├── Chat.svelte                  ← generic, used by all device pages
│   Props: indexId, title, subtitle, icon, accent, placeholder, starters
│   Each category page passes its own ISP index ID and branding.
│
│   Page → index mapping:
│     /phones       → 22ffabfef4d1  (pink #e20074, 📱)
│     /accessories  → 847bf1f24c72  (navy,        🎧)
│     /smartwatch   → 8bf92948e950  (blue,         ⌚)
│     /tablets      → 9ed56100f220  (green,        💻)
│
└── plants/
    └── PlantChat.svelte         ← plants-specific (green theme, 🌿)
          indexId: 2d921f78d54b

chatController.ts
  sendChatMessage(query, indexId)  ← indexId is a parameter, not hardcoded
  clearChat()

chatStore.ts
  chatOpenStore     — panel open/closed
  chatMessagesStore — full conversation history
  chatLoadingStore  — streaming in progress
```

---

## 12. LLM Provider Design

### Two categories — one UI decision

```
Settings → Chat Model
│
├── Disabled     → chat feature hidden entirely
│
├── Self-hosted  → one code path for all self-hosted tools
│   │              (all expose OpenAI-compatible API)
│   │
│   │  User provides:
│   │    Endpoint: http://your-server:11434/v1
│   │    Model:    llama3.1:8b  (optional)
│   │
│   └── Compatible software:
│         Ollama      — brew install ollama && ollama pull llama3.1:8b
│         LM Studio   — desktop app, one-click model download
│         vLLM        — Python server, GPU required for speed
│         LocalAI     — docker run localai/localai:latest
│
└── Paid API     → four providers, all cloud, user provides API key
    │
    ├── Google Gemini   → api key from aistudio.google.com (free quota available)
    ├── Anthropic       → api key from console.anthropic.com
    ├── OpenAI          → api key from platform.openai.com
    └── Groq            → api key from console.groq.com (free tier available)
```

### Why URL is only needed for self-hosted

| Provider | URL in settings? | Why |
|----------|-----------------|-----|
| Anthropic | No | `api.anthropic.com` — hardcoded |
| Google | No | `generativelanguage.googleapis.com` — hardcoded |
| OpenAI | No | `api.openai.com` — hardcoded |
| Groq | No | `api.groq.com/openai/v1` — hardcoded |
| Self-hosted | **Yes** | You decide where to run it — could be localhost, a home server, or a cloud GPU |

### Cost comparison (per 1,000 chats, ~500 tokens each)

| Provider | Model | Cost |
|----------|-------|------|
| Self-hosted (Ollama) | Llama 3.1 8B | **$0** (server cost only) |
| Groq | Llama 3.3 70B | **$0** (free tier — 14,400 req/day, no card needed) |
| Groq | Llama 3.1 8B | **$0** (free tier — faster, slightly weaker) |
| Google | Gemini 2.0 Flash | **~$0.01** (billing required) |
| Google | Gemini 2.5 Flash | **~$0.02** (billing required) |
| Google | Gemini 2.5 Pro | **~$0.50** (billing required) |
| OpenAI | GPT-4o mini | **~$0.08** |
| OpenAI | GPT-4o | **~$2.00** |
| Anthropic | Claude Haiku 4.5 | **~$0.14** |
| Anthropic | Claude Sonnet 4.6 | **~$1.50** |

**Recommended starting point:** Groq / Llama 3.3 70B — genuinely free (no credit card), 14,400 requests/day, fast streaming. Sign up at console.groq.com. For production with higher volume, Gemini 2.0 Flash is the best cost-to-quality ratio once billing is enabled.

> **Note:** Gemini 1.5 models (Flash and Pro) are deprecated and will return a 404. Use Gemini 2.0 Flash or newer.

### How vector embedding and LLM are independent

```
Voyage AI  →  converts query to 1024-d vector  →  Typesense finds products
                        ↓
                    (product list)
                        ↓
LLM        →  reads products, writes answer

These two systems share no state. You can:
  - Change the LLM provider → search quality unchanged
  - Change the embedding model → LLM quality unchanged
  - Use self-hosted LLM + cloud embeddings → fully valid combination
  - Use cloud LLM + self-hosted embeddings → also valid
```

---

## 13. Settings Panel Design

The Chat Model tab in the ISP admin panel (Settings → Chat Model):

### State: Disabled (default)

```
┌ Embedding Model │ Customer Personalisation │ Chat Model ───────────────┐
│                                                                         │
│  Chat Model                                                             │
│  Configure the LLM used for conversational search (RAG chat on PLP).   │
│  Applied immediately — no restart required.                             │
│                                                                         │
│  Provider type                                                          │
│  ┌────────────┐  ┌─────────────────┐  ┌──────────────┐                 │
│  │  Disabled  │  │  Self-hosted    │  │   Paid API   │                 │
│  │  (active)  │  │                 │  │              │                 │
│  └────────────┘  └─────────────────┘  └──────────────┘                 │
│                                                                         │
│  [Save]                                                                 │
│                                                                         │
│  ● Chat is disabled. Select a provider above to enable it.              │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### State: Self-hosted selected

```
│  Provider type                                                          │
│  ┌────────────┐  ┌─────────────────┐  ┌──────────────┐                 │
│  │  Disabled  │  │  Self-hosted    │  │   Paid API   │                 │
│  │            │  │   (active) ✓   │  │              │                 │
│  └────────────┘  └─────────────────┘  └──────────────┘                 │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  Endpoint URL *                                                   │  │
│  │  Ollama, LM Studio, vLLM, LocalAI, or any OpenAI-compatible      │  │
│  │  ┌─────────────────────────────────────────────────────────────┐ │  │
│  │  │ http://localhost:11434/v1                                   │ │  │
│  │  └─────────────────────────────────────────────────────────────┘ │  │
│  │                                                                   │  │
│  │  Model name  (optional — leave blank if server has only 1 model) │  │
│  │  ┌─────────────────────────────────────────────────────────────┐ │  │
│  │  │ llama3.1:8b                                                 │ │  │
│  │  └─────────────────────────────────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  [Save]                                                                 │
│                                                                         │
│  ● Self-hosted at http://localhost:11434/v1 · model llama3.1:8b         │
│                                                                         │
```

### State: Paid API selected (Google Gemini)

```
│  Provider type                                                          │
│  ┌────────────┐  ┌─────────────────┐  ┌──────────────┐                 │
│  │  Disabled  │  │  Self-hosted    │  │   Paid API   │                 │
│  │            │  │                 │  │  (active) ✓  │                 │
│  └────────────┘  └─────────────────┘  └──────────────┘                 │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  Provider                                                         │  │
│  │  ┌─────────────────────────────────────────────────────────────┐ │  │
│  │  │ Google Gemini                                             ▼ │ │  │
│  │  └─────────────────────────────────────────────────────────────┘ │  │
│  │                                                                   │  │
│  │  Model                                                            │  │
│  │  ┌─────────────────────────────────────────────────────────────┐ │  │
│  │  │ Gemini 2.0 Flash                                          ▼ │ │  │
│  │  └─────────────────────────────────────────────────────────────┘ │  │
│  │                                                                   │  │
│  │  API key  (stored — leave blank to keep)                         │  │
│  │  ┌─────────────────────────────────────────────────────────────┐ │  │
│  │  │ ••••••••••••••••••••••••                                    │ │  │
│  │  └─────────────────────────────────────────────────────────────┘ │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  [Save]                                                                 │
│                                                                         │
│  ● google · gemini-2.0-flash · API key stored ✓                         │
│                                                                         │
```

---

## 14. What the LLM Does and Does Not Do

| The LLM does | The LLM does not |
|-------------|-----------------|
| Read the retrieved products and write a natural answer | Search your database — Typesense does that |
| Format the response conversationally | Know your prices or stock from training data |
| Handle follow-up questions using conversation history | Access the internet or live external systems |
| Say "I don't know" when no products match | Invent products not in the system prompt |
| Summarise, compare, and recommend from the given list | Remember anything between separate sessions |
| Stream tokens back to the browser as they are generated | Make decisions about which products to retrieve |

The LLM is a writer, not a searcher. Typesense does the searching. The LLM does the writing.

---

## 15. Why This Matters for Your Store

### Problems RAG solves

**Hallucination** — a plain LLM will confidently make up products, prices, and features. RAG grounds every answer in real data from your index. If a product is not indexed, the LLM cannot mention it.

**Staleness** — LLM training data has a cutoff date. Your store inventory changes daily. RAG always queries the live index, so answers reflect current products and pricing.

**Specificity** — generic AI chat can only give generic plant or phone advice. RAG chat gives advice specific to what you actually carry, using your descriptions, your categories, and your prices.

**Trust** — customers can see the product cards alongside the chat answer. The product cards are the source. If the LLM says "Spider Plant for $18" and the card shows the same thing, the customer trusts the answer.

### What improves chat quality

The chat is only as good as what is indexed. Richer product data → better retrieval → more accurate answers:

| Field | Why it matters for chat |
|-------|------------------------|
| `title` | Primary search signal — always include the full plant or product name |
| `content` | Description used in retrieval — more detail means better semantic matching |
| `metadata.pet_safe` | Enables "safe for cats/dogs" questions |
| `metadata.care_difficulty` | Enables "easy for beginners" questions |
| `metadata.price` | Enables budget-based questions |
| `metadata.plant_family` | Enables "any Hoyas?" or "show me Aroids" questions |
| `metadata.growth_style` | Enables "trailing" or "upright" preference questions |
| `metadata.care_light` | Enables "low light" or "bright indirect" questions |
