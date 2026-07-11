export function getApiBase(): string {
  return typeof window !== 'undefined'
    ? `${window.location.protocol}//${window.location.hostname}:8001/v1`
    : 'http://localhost:8001/v1';
}

export interface ApiCurls {
  publicSearch: string;
  publicDocs:   string;
  hybrid:       string;
  lexical:      string;
  docs:         string;
}

export function buildApiCurls(base: string, indexId: string): ApiCurls {
  return {
    publicSearch: `curl -X POST "${base}/indexes/${indexId}/search/public" \\\n  -H "Content-Type: application/json" \\\n  -d '{"q": "your query", "mode": "hybrid", "page": 1, "per_page": 10}'`,
    publicDocs:   `curl "${base}/indexes/${indexId}/documents/public?page=1&per_page=20"`,
    hybrid:       `curl -X POST "${base}/indexes/${indexId}/search" \\\n  -H "Content-Type: application/json" \\\n  -d '{"q": "your query", "mode": "hybrid", "page": 1, "per_page": 10}'`,
    lexical:      `curl -X POST "${base}/indexes/${indexId}/search" \\\n  -H "Content-Type: application/json" \\\n  -d '{"q": "your query", "mode": "lexical", "page": 1, "per_page": 10}'`,
    docs:         `curl "${base}/indexes/${indexId}/documents?page=1&per_page=20"`,
  };
}
