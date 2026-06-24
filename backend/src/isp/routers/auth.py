"""
/auth — OAuth2 login / callback / logout / me.

These routes are always public (no auth middleware applied to /auth/*).
The callback issues a short-lived JWT and redirects the browser to the
frontend URL with the token as a query param so the SPA can store it.
"""
from __future__ import annotations
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from isp.auth import jwt_utils, provider
from isp.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login():
    """Redirect the browser to the OAuth2 provider's consent screen."""
    state = secrets.token_hex(16)
    url = provider.get_authorization_url(state)
    response = RedirectResponse(url, status_code=302)
    # httpOnly state cookie — verified on callback to prevent CSRF
    response.set_cookie(
        "oauth_state", state,
        httponly=True, samesite="lax", max_age=300,
    )
    return response


@router.get("/callback")
async def callback(code: str, state: str, request: Request):
    """
    Exchange the authorization code for an ISP JWT and redirect to the frontend.

    The JWT is passed as ``?isp_token=<token>`` on the redirect URL so the
    SPA can read it from the URL, store in localStorage, and remove it from
    the address bar — avoiding httpOnly / cross-port cookie issues in local dev.
    """
    stored_state = request.cookies.get("oauth_state")
    if not stored_state or stored_state != state:
        raise HTTPException(400, "Invalid OAuth state — possible CSRF attempt")

    try:
        access_token = await provider.exchange_code(code)
        userinfo = await provider.get_userinfo(access_token)
    except Exception as exc:
        raise HTTPException(502, f"OAuth provider error: {exc}") from exc

    email = userinfo.get("email", "")
    if not email:
        raise HTTPException(502, "OAuth provider did not return an email address")

    if settings.oauth_allowed_domain:
        domain = email.split("@")[-1]
        if domain != settings.oauth_allowed_domain:
            raise HTTPException(403, f"Email domain @{domain} is not allowed")

    token = jwt_utils.create_token(email, userinfo.get("name", ""))

    # Redirect to frontend; SPA reads ?isp_token= and stores in localStorage
    redirect_url = f"{settings.oauth_frontend_url}?isp_token={token}"
    response = RedirectResponse(redirect_url, status_code=302)
    response.delete_cookie("oauth_state")
    return response


@router.post("/logout")
async def logout():
    """Clear the auth cookie (if used) and return 200."""
    response = JSONResponse({"ok": True})
    response.delete_cookie("isp-auth-token")
    return response


@router.get("/me")
async def me(request: Request):
    """Return the current user's email and name decoded from the JWT."""
    from isp.auth.middleware import _extract_token
    token = _extract_token(request)
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt_utils.verify_token(token)
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    return {"email": payload["sub"], "name": payload.get("name", "")}
