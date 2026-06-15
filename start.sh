#!/usr/bin/env bash
# ====================================================================
#  Ausleihe-Ausgabe - Server starten (macOS / Linux)
#  Erststart: vorher  uv sync  &&  uv run playwright install chromium
# ====================================================================
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "[FEHLER] 'uv' nicht gefunden — https://docs.astral.sh/uv/" >&2
  exit 1
fi
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    echo "  .env aus Vorlage erstellt — bitte ISERV_* und LEITSTAND_PASSWORD eintragen."
    exit 1
  fi
  echo "[FEHLER] .env fehlt." >&2
  exit 1
fi

echo "Starte Ausleihe-Ausgabe-Server (HTTPS, Port aus .env, Default 3443) ..."
echo "Leitstand: https://localhost:3443/leitstand.html  (Beenden: Strg+C)"
exec uv run python -m server.main
