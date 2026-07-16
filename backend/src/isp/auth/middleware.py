"""
Auth middleware — active only when ``AUTH_ENABLED=true``.

Route protection rules (evaluated in order):
  1. OPTIONS (preflight)                             → always open (CORS handles it)
  2. /health, /auth/*, /docs, /openapi.json, /redoc → always open
  3. /v1/catalog/*, /v1/analytics/*, /v1/intent/*, /v1/suggest/* → open (PLP-facing, no auth)
  4. /v1/indexes/{id}/search/public                  → open (external search, no auth)
     /v1/indexes/{id}/facets                         → open (PLP-facing facet counts, no auth)
     Future: enforce X-Api-Key here when API key management UI is built.
  5. /v1/*                                           → JWT required (Bearer header)
  6. Everything else                                 → open

No existing router or handler is modified.  This middleware is registered in
``main.py`` only when ``settings.auth_enabled`` is True.
"""
from __future__ import annotations
import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from isp.auth.jwt_utils import verify_token

_PUBLIC_PREFIXES = ("/health", "/auth/", "/docs", "/openapi.json", "/redoc")

# Routes called by PLP and external consumers — no admin JWT required
_PUBLIC_API_PREFIXES = (
    "/v1/catalog",       # facets + catalog search
    "/v1/analytics",     # impression/click logging
    "/v1/intent",        # tab intent
    "/v1/suggest",       # typeahead suggestions
)
_SEARCH_PUBLIC  = re.compile(r"^/v1/indexes/[^/]+/search/public$")
_FACETS_PUBLIC  = re.compile(r"^/v1/indexes/[^/]+/facets$")
_CHAT_PUBLIC    = re.compile(r"^/v1/indexes/[^/]+/chat$")   # PLP-facing, called by end users


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get("isp-auth-token")



class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Always pass OPTIONS through — CORS middleware handles preflight
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path

        # Always open
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        # PLP-facing public routes — open without auth
        if any(path.startswith(p) for p in _PUBLIC_API_PREFIXES):
            return await call_next(request)

        # Public search, facets, and chat endpoints — open without auth
        if _SEARCH_PUBLIC.match(path) or _FACETS_PUBLIC.match(path) or _CHAT_PUBLIC.match(path):
            return await call_next(request)

        # Admin routes — JWT
        if path.startswith("/v1/"):
            token = _extract_token(request)
            if not token:
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
            try:
                verify_token(token)
            except Exception:
                return JSONResponse({"detail": "Invalid or expired token"}, status_code=401)
            return await call_next(request)

        return await call_next(request)
