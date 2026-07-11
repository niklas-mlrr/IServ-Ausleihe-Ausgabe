"""State-Endpoint (`/api/state`) und Dev-/Host-Toggles.

`/api/settings/{key}` bündelt die drei strukturell gleichen Bool-Toggles;
`/api/force-tailscale-ip` bleibt bewusst eigenständig (baut zusätzlich den
Modus-B-QR neu, braucht `Request`).
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from ..hub import get_hub
from ..sessions import broadcast_displays, make_qr_data_url
from ..state import get_state
from ._deps import BoolToggleRequest, SettingsToggleRequest, _base_url, host_router

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@host_router.get("/api/state")
async def get_state_endpoint() -> dict:
    return get_state().state_snapshot()


@host_router.post("/api/force-tailscale-ip")
async def set_force_tailscale_ip(body: BoolToggleRequest, request: Request) -> dict:
    """Header-Toggle „Tailscale-IP": Auto-Auswahl (LAN-first) ↔ erzwungene Tailscale-IP.

    Beeinflusst alle QR-/Join-URLs (Helfer-, Schüler-Join-, iPad-Display-QR).
    Die On-Demand-QRs übernehmen den Modus beim nächsten Abruf automatisch; den
    bei `/modus-b/open` eingefrorenen Schüler-Join-QR bauen wir hier neu, wenn
    die Ausgabe gerade offen ist.
    """
    state = get_state()
    state.settings.force_tailscale_ip = body.enabled

    if state.modus_b_open and state.modus_b_join_secret:
        state.modus_b_join_url = f"{_base_url(request)}/student?j={state.modus_b_join_secret}"
        state.modus_b_join_qr = make_qr_data_url(state.modus_b_join_url)
        await broadcast_displays(state)

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "force_tailscale_ip": state.settings.force_tailscale_ip}


# Whitelist für POST /api/settings/{key} — bündelt die drei strukturell
# gleichen Bool-Toggles (setzen genau ein Attribut im Serverstate, broadcasten
# an alle Hosts). `force-tailscale-ip` (baut zusätzlich den Modus-B-QR neu,
# braucht `Request`) und `printer` (String-Wert, andere Semantik: leer =
# .env-Default) bleiben bewusst eigenständige Endpunkte — reinquetschen würde
# den gemeinsamen Rumpf nur mit Sonderfällen vollstopfen, ohne echte
# Duplikation zu sparen.
# key -> (state-Attribut, Body-Feldname auf `SettingsToggleRequest`)
_BOOL_SETTINGS: dict[str, tuple[str, str]] = {
    "save-pdf-locally": ("save_pdf_locally", "enabled"),
    "fix-class-on-slip": ("fix_class_on_slip", "enabled"),
    "slip-default": ("slip_second_page_default", "second_page"),
}


@host_router.post("/api/settings/{key}")
async def set_bool_setting(key: str, body: SettingsToggleRequest) -> dict:
    """Einfache Bool-Entwickler-/Host-Toggles gegen eine Whitelist
    (`_BOOL_SETTINGS`) gebündelt: `save-pdf-locally` (Entwickler-Toggle „PDF
    lokal speichern"), `fix-class-on-slip` (experimenteller Entwickler-Toggle
    „Klasse auf Leihschein korrigieren") und `slip-default` (Host-Toggle
    „Schüler-Leihschein" als Druck-Dialog-Default für die Helfer). Alle drei:
    rein In-Memory, kein IServ-/DB-Zugriff.
    """
    entry = _BOOL_SETTINGS.get(key)
    if entry is None:
        raise HTTPException(404, f"Unbekannte Einstellung: {key}")
    attr, field = entry
    value = bool(getattr(body, field))
    state = get_state()
    setattr(state.settings, attr, value)
    hub = get_hub()
    if key == "slip-default":
        # `slip_second_page_default` ist zusätzlich für die Helfer relevant
        # (Druck-Dialog-Vorauswahl) — anders als die beiden reinen
        # Entwickler-Toggles auch an sie broadcasten.
        await hub.broadcast_settings(state)
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, attr: value}
