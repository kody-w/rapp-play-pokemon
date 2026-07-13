#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
RAPP_LOCAL="$ROOT/.rapp"
RUNTIME_DIR="${HOME}/.rapp/rapp-play-pokemon"
PURGE=0

while (($#)); do
  case "$1" in
    --purge-data)
      PURGE=1
      shift
      ;;
    --runtime-dir)
      (($# >= 2)) || {
        printf 'error: --runtime-dir requires a path\n' >&2
        exit 1
      }
      RUNTIME_DIR="$2"
      shift 2
      ;;
    *)
      printf 'error: unknown option: %s\n' "$1" >&2
      exit 1
      ;;
  esac
done

if [[ -x "$VENV/bin/python" ]]; then
  RAPP_BRAINSTEM_DIR="$RAPP_LOCAL/RAPP/cave/rapplications/rapp-installer/kernel" \
    "$VENV/bin/python" -m rapp_play_pokemon.cli \
    stop --runtime-dir "$RUNTIME_DIR" >/dev/null 2>&1 || true
fi

if ((PURGE)); then
  PYTHON="$(command -v python3 || true)"
  [[ -n "$PYTHON" ]] || {
    printf 'error: Python is required to validate the purge path\n' >&2
    exit 1
  }
  RUNTIME_DIR="$("$PYTHON" -c \
    'from pathlib import Path; import sys; print(Path(sys.argv[1]).expanduser().resolve())' \
    "$RUNTIME_DIR")"
  case "$RUNTIME_DIR" in
    "/"|"${HOME}"|"$ROOT")
      printf 'error: refusing unsafe runtime directory: %s\n' "$RUNTIME_DIR" >&2
      exit 1
      ;;
  esac
  if [[ -d "$RUNTIME_DIR" ]]; then
    "$PYTHON" - "$RUNTIME_DIR/runtime-owner.json" <<'PY'
import json
import sys
from pathlib import Path

marker = Path(sys.argv[1])
try:
    value = json.loads(marker.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(
        "error: refusing to purge a directory without a valid runtime-owner.json"
    )
if value.get("product") != "rapp-play-pokemon":
    raise SystemExit("error: runtime ownership marker does not match this project")
PY
  fi
fi

[[ "$VENV" == "$ROOT/.venv" ]] && rm -rf -- "$VENV"
[[ "$RAPP_LOCAL" == "$ROOT/.rapp" ]] && rm -rf -- "$RAPP_LOCAL"

if ((PURGE)); then
  [[ -d "$RUNTIME_DIR" ]] && rm -rf -- "$RUNTIME_DIR"
  printf 'Removed local installation and explicitly requested Pokemon data.\n'
else
  printf 'Removed local installation. Preserved saves and recordings in %s\n' \
    "$RUNTIME_DIR"
fi
