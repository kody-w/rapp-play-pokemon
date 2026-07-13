#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
KERNEL="$ROOT/.rapp/RAPP/cave/rapplications/rapp-installer/kernel"

if [[ ! -x "$VENV/bin/python" || ! -f "$KERNEL/brainstem.py" ]]; then
  printf 'error: run ./bootstrap.sh --setup-only first\n' >&2
  exit 1
fi

export RAPP_BRAINSTEM_DIR="$KERNEL"
exec "$VENV/bin/python" -m rapp_play_pokemon.cli "$@"
