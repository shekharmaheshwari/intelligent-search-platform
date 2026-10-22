// Copyright 2026 Shekhar Maheshwari
// SPDX-License-Identifier: Apache-2.0

export function getApiBase(): string {
  return typeof window !== 'undefined'
    ? `${window.location.protocol}//${window.location.hostname}:8001/v1`
    : 'http://localhost:8001/v1';
}

export interface ApiCurls {
  publicSearch: string;
  publicDocs:   string;
  suggest:      string;
  hybrid:       string;
  lexical:      string;
  docs:         string;
  chat:         string;
  chatTurn:     string;
}

export function buildApiCurls(base: string, indexId: string): ApiCurls {
  return {
    publicSearch: `curl -X POST "${base}/indexes/${indexId}/search/public" \\\n  -H "Content-Type: application/json" \\\n  -d '{"q": "your query", "mode": "hybrid", "page": 1, "per_page": 10}'`,
    publicDocs:   `curl "${base}/indexes/${indexId}/documents/public?page=1&per_page=20"`,
    suggest:      `curl "${base}/suggest?q=your+query&index_id=${indexId}&limit=8"`,
    hybrid:       `curl -X POST "${base}/indexes/${indexId}/search" \\\n  -H "Content-Type: application/json" \\\n  -d '{"q": "your query", "mode": "hybrid", "page": 1, "per_page": 10}'`,
    lexical:      `curl -X POST "${base}/indexes/${indexId}/search" \\\n  -H "Content-Type: application/json" \\\n  -d '{"q": "your query", "mode": "lexical", "page": 1, "per_page": 10}'`,
    docs:         `curl "${base}/indexes/${indexId}/documents?page=1&per_page=20"`,
    chat:         `curl -X POST "${base}/indexes/${indexId}/chat" \\\n  -H "Content-Type: application/json" \\\n  -d '{"query": "your question", "messages": []}'`,
    chatTurn:     `curl -X POST "${base}/indexes/${indexId}/chat" \\\n  -H "Content-Type: application/json" \\\n  -d '{\n    "query": "follow-up question",\n    "messages": [\n      {"role": "user",      "content": "first question"},\n      {"role": "assistant", "content": "...", "products": [...]}\n    ]\n  }'`,
  };
}
