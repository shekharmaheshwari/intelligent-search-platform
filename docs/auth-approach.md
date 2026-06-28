# ISP Authentication

ISP uses OAuth2 / OIDC SSO for admin access. Auth is **off by default** — all routes behave as before until `AUTH_ENABLED=true` is set.

- No passwords to manage
- Supports Google, GitHub, and Microsoft — switch via `OAUTH_PROVIDER` with no code changes
- Restricts admin access to a single provider account or domain (`OAUTH_ALLOWED_DOMAIN`)
- Public search endpoints remain open for PLP and other consumers (no auth required)
- Fully decoupled — `AUTH_ENABLED=false` (default) preserves existing behaviour
- Naturally upgrades to SAML if enterprise customers need it later

---

## How it works

Auth is implemented as a fully decoupled Starlette middleware (`isp/auth/middleware.py`) that is only registered when `AUTH_ENABLED=true`. Route protection follows a fixed priority order — no existing router, handler, or test was modified.

## Route protection

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
│  All other /v1/*                           → JWT required         │
│  (indexes, rebuild, extensions, settings)    (Bearer header)      │
└──────────────────────────────────────────────────────────────────┘
```

---

## Login flow

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

---

## Enabling auth

### Step 1 — Create OAuth credentials

**Google:** [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials) → Create OAuth 2.0 Client ID → Web application → add `http://localhost:8001/auth/callback` as redirect URI.

**GitHub:** [Settings → Developer settings → OAuth Apps](https://github.com/settings/developers) → set callback to `http://localhost:8001/auth/callback`.

**Microsoft:** [Azure portal → App registrations](https://portal.azure.com/#blade/Microsoft_AAD_RegisteredApps/ApplicationsListBlade) → New registration → add `http://localhost:8001/auth/callback` as redirect URI (Web platform). Copy the Application (client) ID and Directory (tenant) ID from the overview page. Under *Certificates & secrets*, create a client secret.

### Step 2 — backend/.env

```env
AUTH_ENABLED=true
OAUTH_PROVIDER=google              # google | github | microsoft
OAUTH_CLIENT_ID=<from console>
OAUTH_CLIENT_SECRET=<from console>
OAUTH_REDIRECT_URI=http://localhost:8001/auth/callback
OAUTH_FRONTEND_URL=http://localhost:3001
OAUTH_ALLOWED_DOMAIN=yourcompany.com   # optional — restricts login to one domain
JWT_SECRET=<random 32+ char string>    # python3 -c "import secrets; print(secrets.token_hex(32))"

# Microsoft only
OAUTH_TENANT_ID=common             # common | organizations | <your-tenant-id>
```

> `OAUTH_CLIENT_SECRET` and `JWT_SECRET` are secrets — store them in Vault or AWS Secrets Manager in non-local environments. `OAUTH_CLIENT_ID`, `OAUTH_TENANT_ID`, `OAUTH_PROVIDER`, and `OAUTH_REDIRECT_URI` are plain config and do not need to be in the secrets store.

### Step 3 — frontend/.env

```env
PUBLIC_AUTH_ENABLED=true
PUBLIC_OAUTH_PROVIDER=google       # google | github | microsoft
```

### Step 4 — restart

```bash
./deploy-api.sh
```

On next load the UI will show the login screen. The first successful sign-in stores an 8-hour JWT in `localStorage`.

> **JWT_SECRET** must be at least 32 random characters and must never be committed to git. Generate one with: `python3 -c "import secrets; print(secrets.token_hex(32))"`

---

## Disabling auth locally

`AUTH_ENABLED=false` is the default. When it is false:

- The `AuthMiddleware` is never registered — every route is open, identical to pre-auth behaviour
- `LoginGate.svelte` renders the app directly with no token checks or login screen
- `api.ts` does not attach an `Authorization` header and ignores 401 responses

To turn auth off, either omit the variable entirely or set it explicitly in both files:

```env
# backend/.env
AUTH_ENABLED=false

# frontend/.env
PUBLIC_AUTH_ENABLED=false
```

Then restart: `./deploy-api.sh`. No code changes needed — the toggle is purely config.

> Keep a `backend/.env.local` (auth off) and a `backend/.env.prod` (auth on). Neither should be committed to git.

---

## API keys

Per-index API keys are built but not yet enforced. The infrastructure is in place:

- `api_keys` table in PostgreSQL (SHA-256 hashes, never plaintext)
- Management endpoints protected by admin JWT:

```bash
# Create a key
curl -X POST http://localhost:8001/v1/indexes/<id>/api-keys \
  -H "Authorization: Bearer <admin-jwt>" \
  -d '{"label": "PLP production"}'
# → { "id": "...", "key": "isp_<48 hex chars>" }  ← returned once only

# List keys
curl http://localhost:8001/v1/indexes/<id>/api-keys \
  -H "Authorization: Bearer <admin-jwt>"

# Revoke a key
curl -X DELETE http://localhost:8001/v1/indexes/<id>/api-keys/<key-id> \
  -H "Authorization: Bearer <admin-jwt>"
```

PLP-facing routes (`/search/public`, `/catalog`, `/analytics`, `/intent`) are currently open without a key. Enforcement will be added once the key management UI is complete.

---

## Supporting files

| File | Role |
|---|---|
| `backend/src/isp/auth/jwt_utils.py` | Signs and verifies HS256 JWTs |
| `backend/src/isp/auth/provider.py` | Handles OAuth2 flow for Google, GitHub, and Microsoft |
| `backend/src/isp/auth/middleware.py` | Enforces route protection rules on every request |
| `backend/src/isp/routers/auth.py` | `/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/me` endpoints |
| `backend/src/isp/routers/api_keys.py` | API key create / list / revoke endpoints |
| `backend/src/isp/config.py` | All auth settings (`AUTH_ENABLED`, `OAUTH_*`, `JWT_*`) |
| `frontend/src/lib/stores/auth-store.ts` | Reads and writes the JWT in localStorage |
| `frontend/src/components/auth/LoginGate.svelte` | Guards the UI; transparent when auth is disabled |


---

## References

> Citations below are sourced from training knowledge (not live browsing). DOI and arXiv links are provided where known.

1. **Hardt, D. (Ed.)** (2012). "The OAuth 2.0 Authorization Framework." *RFC 6749*, Internet Engineering Task Force (IETF). DOI: [10.17487/RFC6749](https://doi.org/10.17487/RFC6749) — Defines the Authorization Code grant flow used by ISP: `GET /auth/login` → provider consent → `GET /auth/callback?code=...` → token exchange.

2. **Jones, M., Bradley, J., & Sakimura, N.** (2015). "JSON Web Token (JWT)." *RFC 7519*, IETF. DOI: [10.17487/RFC7519](https://doi.org/10.17487/RFC7519) — Specifies the JWT format ISP uses for 8-hour admin sessions; HS256 signing with `JWT_SECRET` matches the HMAC-SHA256 algorithm defined here.

3. **Sakimura, N., Bradley, J., Jones, M., de Medeiros, B., & Mortimore, C.** (2014). "OpenID Connect Core 1.0." *OpenID Foundation Specification*. URL: [openid.net/connect](https://openid.net/connect/) — OIDC extends OAuth 2.0 with identity claims (`email`, `name`); ISP uses these claims from the provider's UserInfo endpoint to populate the JWT and display the logged-in user.

4. **Barth, A., Jackson, C., & Mitchell, J. C.** (2008). "Robust Defenses for Cross-Site Request Forgery." *Proceedings of CCS 2008*, pp. 75–88. DOI: [10.1145/1455770.1455782](https://doi.org/10.1145/1455770.1455782) — Establishes the state-parameter CSRF defense used in ISP's OAuth flow: the backend generates a random `state` cookie before the redirect and verifies it on callback.

5. **Hunt, T.** (2018). "Have I Been Pwned: Passwords." *HIBP Project*. URL: [haveibeenpwned.com/Passwords](https://haveibeenpwned.com/Passwords) — Motivates ISP's decision to use SSO instead of passwords: password-based auth requires secure storage, rotation, and breach monitoring; delegating to OAuth providers eliminates all three.

6. **OWASP Foundation.** (2023). *OWASP Authentication Cheat Sheet*. URL: [cheatsheetseries.owasp.org](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html) — Security checklist; ISP's CSRF state cookie, short JWT TTL (8h), localStorage storage with 401→logout, and domain restriction (`OAUTH_ALLOWED_DOMAIN`) all align with OWASP guidance.

7. **Sakimura, N., & Bradley, J.** (2014). "OAuth 2.0 Multiple Response Type Encoding Practices." *OpenID Foundation*. — Background on authorization code vs. implicit flows; ISP uses Authorization Code (most secure) not Implicit Flow, consistent with current best practices.
