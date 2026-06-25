# Authentication Approaches for ISP

> **Current state:** **Approach 3 (OAuth2 / OIDC SSO) is implemented and live.** Auth is off by default (`AUTH_ENABLED=false`). Set `AUTH_ENABLED=true` in `backend/.env` to activate it. See the [Enabling auth](#enabling-auth) section for full setup steps.

---

## Approach 3 — OAuth2 / OIDC SSO ✅ IMPLEMENTED

> Auth is **off by default**. Existing behaviour (all routes open) is unchanged until `AUTH_ENABLED=true`.

### Design: fully decoupled

Auth is a Starlette `BaseHTTPMiddleware` registered in `main.py` only when `AUTH_ENABLED=true`. No existing router, handler, or test was modified.

### Route split

```
┌──────────────────────────────────────────────────────────────────┐
│                          ISP Backend                              │
│                                                                   │
│  OPTIONS (preflight)                       → always open          │
│  /health, /auth/*, /docs, /openapi.json    → always open          │
│                                                                   │
│  /v1/catalog/*, /v1/analytics/*,           → open (no auth)       │
│  /v1/intent/*, /v1/indexes/*/search/public   (PLP-facing routes)  │
│                                                                   │
│  All other /v1/*                           → OAuth2 JWT required  │
│  (indexes, rebuild, extensions, settings)    (Bearer header)      │
└──────────────────────────────────────────────────────────────────┘
```

> **Future:** PLP-facing routes will be protected by per-index API keys (`X-Api-Key` header). The `api_keys` table and management endpoints are already built — enforcement will be added once the API key management UI is complete.

### Login flow

```
1. User opens ISP → LoginGate.svelte checks localStorage for valid JWT
2. No token → "Sign in" button → GET /auth/login
3. Backend sets CSRF state cookie → redirects to provider consent screen
4. Provider redirects to GET /auth/callback?code=...&state=...
5. Backend verifies state, exchanges code, fetches email + name from provider
6. Backend issues HS256 JWT (8h TTL) → redirects to frontend?isp_token=<jwt>
7. LoginGate.svelte reads ?isp_token=, stores in localStorage, cleans URL
8. All api.ts calls attach Authorization: Bearer <token>
9. On 401 → localStorage cleared → redirect to /auth/login
```

### New files

| File | Purpose |
|---|---|
| `backend/src/isp/auth/__init__.py` | Package marker |
| `backend/src/isp/auth/jwt_utils.py` | HS256 `create_token` / `verify_token` (PyJWT) |
| `backend/src/isp/auth/provider.py` | Google, GitHub & Microsoft OAuth2: auth URL, code exchange, userinfo |
| `backend/src/isp/auth/middleware.py` | `AuthMiddleware` — open / JWT per route pattern |
| `backend/src/isp/routers/auth.py` | `/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/me` |
| `backend/src/isp/routers/api_keys.py` | CRUD for per-index API keys |
| `frontend/src/lib/stores/auth-store.ts` | JWT store (localStorage, expiry-aware) |
| `frontend/src/components/auth/LoginGate.svelte` | Transparent when `AUTH_ENABLED=false`; login screen otherwise |

### Existing files changed (minimal)

| File | Change |
|---|---|
| `config.py` | 9 new auth settings, all with safe defaults (includes `oauth_tenant_id` for Microsoft) |
| `database.py` | `api_keys` table added to `init_db()` |
| `secrets.py` | `OAUTH_CLIENT_SECRET`, `JWT_SECRET` added to bootstrap list |
| `main.py` | One `if settings.auth_enabled` block; auth/api_keys routers registered |
| `pyproject.toml` | `PyJWT>=2.8.0` added |
| `api.ts` | `req()` injects Bearer token; handles 401 redirect |
| `index.astro` | `<App>` wrapped in `<LoginGate>` |

### Enabling auth

#### Step 1 — Create OAuth credentials

**Google:** [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials) → Create OAuth 2.0 Client ID → Web application → add `http://localhost:8001/auth/callback` as redirect URI.

**GitHub:** [Settings → Developer settings → OAuth Apps](https://github.com/settings/developers) → set callback to `http://localhost:8001/auth/callback`.

**Microsoft:** [Azure portal → App registrations](https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade) → New registration → add `http://localhost:8001/auth/callback` as a redirect URI (Web platform). Copy the Application (client) ID and Directory (tenant) ID from the overview page. Under *Certificates & secrets*, create a new client secret.

#### Step 2 — backend/.env

```env
AUTH_ENABLED=true
OAUTH_PROVIDER=google              # google | github | microsoft
OAUTH_CLIENT_ID=<from console>
OAUTH_CLIENT_SECRET=<from console>
OAUTH_REDIRECT_URI=http://localhost:8001/auth/callback
OAUTH_FRONTEND_URL=http://localhost:3001
OAUTH_ALLOWED_DOMAIN=yourcompany.com   # optional — restricts to one domain
JWT_SECRET=<random 32+ char string>    # python3 -c "import secrets; print(secrets.token_hex(32))"

# Microsoft only
OAUTH_TENANT_ID=common             # common | organizations | <your-tenant-id>
```

#### Step 3 — frontend/.env

```env
PUBLIC_AUTH_ENABLED=true
PUBLIC_OAUTH_PROVIDER=google       # google | github | microsoft
```

#### Step 4 — restart

```bash
./deploy-api.sh
```

### Disabling auth for local development

`AUTH_ENABLED=false` is the default. When false, the `AuthMiddleware` is never registered, `LoginGate.svelte` renders the app directly, and `api.ts` sends no auth headers — behaviour is identical to before auth was added.

To disable explicitly:

```env
# backend/.env
AUTH_ENABLED=false

# frontend/.env
PUBLIC_AUTH_ENABLED=false
```

> **Tip:** Keep a `backend/.env.local` (auth off) and a `backend/.env.prod` (auth on). Neither should be committed to git.

### API keys — infrastructure ready, enforcement coming

PLP-facing routes (`/search/public`, `/catalog`, `/analytics`, `/intent`) are currently open without any key. Enforcement will be added once the API key management UI is built.

**What's already built:**
- `api_keys` PostgreSQL table (SHA-256 hashes, never plaintext)
- Management endpoints (require admin JWT):

```bash
# Create a key
curl -X POST http://localhost:8001/v1/indexes/<id>/api-keys \
  -H "Authorization: Bearer <admin-jwt>" \
  -d '{"label": "PLP production"}'
# → { "id": "...", "key": "isp_<48 hex chars>" }  ← returned once only

# Revoke a key
curl -X DELETE http://localhost:8001/v1/indexes/<id>/api-keys/<key-id> \
  -H "Authorization: Bearer <admin-jwt>"
```

**What's coming:**
- API key management panel in the ISP admin UI
- `X-Api-Key` header enforcement on `/search/public` and optionally catalog/analytics routes

---

## Future: securing public routes

Currently `/v1/catalog/*`, `/v1/analytics/*`, `/v1/intent/*`, and `/v1/indexes/*/search/public` are open without any authentication. Two options are planned:

### Option A — Per-index API keys (recommended)

Infrastructure already built (`api_keys` table + management endpoints). Needs a UI panel and one-line middleware change to enforce.

```
Consumer → X-Api-Key: isp_<key> → /v1/indexes/{id}/search/public
```

**Work remaining:** key management UI in admin panel + re-enable `X-Api-Key` check in `middleware.py`.

### Option B — Shared read-only JWT

Issue a single long-lived service JWT (not tied to a Google account) that all consumers share.

```
POST /auth/service-token  { "label": "PLP service", "ttl_days": 365 }
→ { "token": "eyJ..." }

Consumer → Authorization: Bearer eyJ...
```

**Work remaining:** `POST /auth/service-token` endpoint + middleware update.

### Planned route split (after implementation)

```
┌──────────────────────────────────────────────────────────────────┐
│  OPTIONS, /health, /auth/*, /docs        → always open           │
│  /v1/catalog/*, /v1/analytics/*,         → X-Api-Key required    │
│  /v1/intent/*, /v1/indexes/*/search/public (per-index key)       │
│  All other /v1/*                         → OAuth2 JWT required   │
└──────────────────────────────────────────────────────────────────┘
```

**Recommendation:** Option A. The hard part (DB schema, hash storage, management API) is already done.

---

## Decision

**Approach 3 (OAuth2 / OIDC SSO + per-index API keys)** was implemented because:
- No passwords to manage
- Supports Google, GitHub, and Microsoft — switch via `OAUTH_PROVIDER` with no code changes
- Restricts admin access to a single provider account or domain (`OAUTH_ALLOWED_DOMAIN`)
- Public search endpoints remain simple header-key auth for PLP and other consumers
- Fully decoupled — `AUTH_ENABLED=false` (default) preserves existing behaviour
- Naturally upgrades to SAML if enterprise customers need it later
