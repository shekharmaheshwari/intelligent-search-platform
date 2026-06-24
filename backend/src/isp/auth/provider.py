"""
OAuth2 provider abstraction — Google and GitHub.

All network I/O uses the ``httpx`` async client already in the dependency tree.
Switch providers by setting ``OAUTH_PROVIDER=github`` (default: ``google``).
"""
from __future__ import annotations
import urllib.parse
import httpx
from isp.config import settings

_GOOGLE = {
    "auth_url":     "https://accounts.google.com/o/oauth2/v2/auth",
    "token_url":    "https://oauth2.googleapis.com/token",
    "userinfo_url": "https://www.googleapis.com/oauth2/v3/userinfo",
    "scope":        "openid email profile",
}

_GITHUB = {
    "auth_url":     "https://github.com/login/oauth/authorize",
    "token_url":    "https://github.com/login/oauth/access_token",
    "userinfo_url": "https://api.github.com/user",
    "scope":        "read:user user:email",
}


def _cfg() -> dict:
    return _GITHUB if settings.oauth_provider == "github" else _GOOGLE


def get_authorization_url(state: str) -> str:
    cfg = _cfg()
    params = {
        "client_id":     settings.oauth_client_id,
        "redirect_uri":  settings.oauth_redirect_uri,
        "response_type": "code",
        "scope":         cfg["scope"],
        "state":         state,
    }
    if settings.oauth_provider == "google":
        params["access_type"] = "online"
        params["prompt"] = "select_account"
    return cfg["auth_url"] + "?" + urllib.parse.urlencode(params)


async def exchange_code(code: str) -> str:
    """Exchange an authorization code for an access token. Returns the token string."""
    cfg = _cfg()
    data = {
        "client_id":     settings.oauth_client_id,
        "client_secret": settings.oauth_client_secret,
        "code":          code,
        "redirect_uri":  settings.oauth_redirect_uri,
        "grant_type":    "authorization_code",
    }
    headers = {"Accept": "application/json"}
    async with httpx.AsyncClient() as client:
        resp = await client.post(cfg["token_url"], data=data, headers=headers, timeout=10)
        resp.raise_for_status()
        body = resp.json()
    return body.get("access_token", "")


async def get_userinfo(access_token: str) -> dict:
    """Return ``{email, name}`` for the authenticated user."""
    cfg = _cfg()
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        resp = await client.get(cfg["userinfo_url"], headers=headers, timeout=10)
        resp.raise_for_status()
        info = resp.json()

    if settings.oauth_provider == "github":
        email = info.get("email") or ""
        if not email:
            # GitHub may hide primary email — fetch explicitly
            emails_resp = await _github_emails(access_token)
            primary = next((e for e in emails_resp if e.get("primary")), None)
            email = primary["email"] if primary else ""
        return {"email": email, "name": info.get("name") or info.get("login", "")}

    # Google OIDC userinfo
    return {"email": info.get("email", ""), "name": info.get("name", "")}


async def _github_emails(access_token: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://api.github.com/user/emails", headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()
