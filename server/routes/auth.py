"""Login / Logout (öffentliche Routen, ohne Host-Auth)."""

from __future__ import annotations

import secrets
import uuid

from fastapi import Cookie, HTTPException, Request, Response

from ..config import get_config
from ..ratelimit import login_limiter
from ..state import get_state
from ._deps import LoginRequest, router


@router.post("/api/login")
async def login(body: LoginRequest, response: Response, request: Request = None) -> dict:
    cfg = get_config()
    # Login-Rate-Limit (pro-IP) — Brute-Force-Anläufe auf das Host-Passwort
    # drosseln. `request` hat bewusst KEINE Union-Annotation (Request | None):
    # FastAPI erkennt `Request` nur als Special-Parameter (Injektion ohne
    # Pydantic-Feld), wenn die Annotation genau `Request` ist — bei einer Union
    # versucht FastAPI ein Pydantic-Feld draus zu bauen und stirbt. Der Default
    # None greift nur beim Direktaufruf im Unit-Test (Tests übergeben kein
    # request-Objekt); in Produktion injiziert FastAPI das echte Request.
    if request is not None:
        if request.client is None:
            raise HTTPException(400, "Client-Info nicht verfügbar")
        if not login_limiter.hit(request.client.host):
            raise HTTPException(429, "Zu viele Login-Versuche — bitte kurz warten")
    # Konstantzeit-Vergleich — kein Short-Circuit-Timing-Leak wie bei `!=`.
    # compare_digest verlangt gleichartige Typen; None/non-str über str() abgesichert.
    if not secrets.compare_digest(str(body.password or ""), str(cfg.host_password or "")):
        raise HTTPException(403, "Falsches Passwort")
    sid = str(uuid.uuid4())
    get_state().add_host_session(sid)
    # secure=True: Cookie nur über HTTPS (der Server läuft ausschließlich über TLS).
    response.set_cookie("session_id", sid, httponly=True, samesite="lax", secure=True)
    return {"ok": True}


@router.post("/api/logout")
async def logout(response: Response, session_id: str | None = Cookie(default=None)) -> dict:
    if session_id:
        get_state().remove_host_session(session_id)
    response.delete_cookie("session_id")
    return {"ok": True}
