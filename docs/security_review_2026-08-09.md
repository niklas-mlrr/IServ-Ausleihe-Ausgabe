# Security review — Modus B and public routes (2026-08-09)

## Scope and method

Read-only source review of the Modus-B student-session lifecycle, HTTP and
WebSocket route protection, static-file mount, and FastAPI's default routes.
No production write, booking, or Playwright test was performed.

The focused offline suites `tests/test_api_guards.py`, `tests/test_queue_flow.py`,
and `tests/test_print_queue.py` passed.

## Result: student data access

No cross-student data-access path was found for a Modus-B student.

- The four-digit pairing code is only a human-facing pairing identifier; it
  grants no data access. The actual credential is a 256-bit random
  `session_token`.
- A pending session receives only its pairing code. Student identity and book
  data are sent only after host pairing.
- A paired student session receives only the assigned student's identity,
  payment indicator, book list, and (by design) that student's own
  student-loan-slip PDF.
- Completion/abort/timeout removes the token from `AppState.student_sessions`,
  releases the worker and closes the WebSocket. A reconnect with the former
  token gets no data.
- The student's own loan-slip download is pushed before completion and can be
  retained on their device afterwards. This is an intended disclosure of their
  own data, not a route that remains usable after completion.

Relevant implementation: `server/routes/ws.py::ws_student`,
`server/sessions.py::load_and_push_paired_student`,
`server/sessions.py::invalidate_session`, and `server/hub.py::broadcast_host`.

## Public routes

The following are intentionally reachable without a host-login cookie. None
directly returns the host snapshot or arbitrary student data.

- Static pages/assets, including `/host`, `/scan`, `/student`, `/teacher` and
  `/qr-display`: source/UI only; subsequent API/WS access remains protected.
- `/api/student/join`: requires the current 128-bit join secret and is
  per-IP rate-limited.
- `/api/student/helper-join`: requires a one-time high-entropy helper QR
  secret.
- `/ws/student/{token}`, `/ws/scanner/{token}` and `/ws/teacher?token=...`:
  capability-token authenticated. The token is the credential, not an
  additional unprotected route.
- Display endpoints/WebSockets: show only a registration code before host
  authorization; they receive data only after pairing.
- `/api/logout`: deliberately accepts a host cookie without a prior route
  guard. It exposes no data but is susceptible to a logout-only CSRF/DoS.
- FastAPI defaults `/docs`, `/redoc`, and `/openapi.json`: publicly reveal the
  API contract but do not bypass endpoint authorization. Disable them for the
  deployed app if the policy requires no publicly discoverable API surface.

All host data routes use `require_host`; `/ws/host` validates the same session
cookie before accepting.

## Follow-up findings (workflow integrity, not data disclosure)

1. A paired student can manually send the `finish` WebSocket frame before the
   expected books, print, or signature workflow is complete. The server then
   marks that student `done` and revokes their session.
2. A paired student can manually send `print_request`; the server enforces the
   configured trigger and printer rules, but not that all expected books have
   been completed.

Recommended hardening: enforce completion of all expected books before both
messages, and enforce the applicable print/signature state before `finish`.
These are integrity/process controls; neither enables access to another
student's data.
