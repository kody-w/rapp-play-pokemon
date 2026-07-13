#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
RAPP_CHECKOUT="$ROOT/.rapp/RAPP"
RAPP_REF="${RAPP_REF:-32f1932f4213ed92dd867325b410f59be535ba19}"
KERNEL_RELATIVE="cave/rapplications/rapp-installer/kernel"
KERNEL="$RAPP_CHECKOUT/$KERNEL_RELATIVE"
ROM=""
MODE="upload"
DOWNLOAD_COPILOT=1
LAUNCH_ARGS=()

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --rom)
      (($# >= 2)) || fail "--rom requires a path"
      ROM="$2"
      MODE="rom"
      shift 2
      ;;
    --upload)
      MODE="upload"
      shift
      ;;
    --setup-only)
      MODE="setup"
      shift
      ;;
    --skip-copilot-runtime)
      DOWNLOAD_COPILOT=0
      shift
      ;;
    --)
      shift
      LAUNCH_ARGS+=("$@")
      break
      ;;
    *)
      LAUNCH_ARGS+=("$1")
      shift
      ;;
  esac
done

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) fail "the autonomous runtime currently supports macOS and Linux" ;;
esac
[[ "$RAPP_REF" =~ ^[0-9a-f]{40}$ ]] ||
  fail "RAPP_REF must be a full lowercase 40-character commit SHA"

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
    "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done
[[ -n "$PYTHON" ]] || fail "Python 3.11+ is required"
command -v git >/dev/null 2>&1 || fail "git is required"
command -v ffmpeg >/dev/null 2>&1 ||
  fail "ffmpeg is required (macOS: brew install ffmpeg)"

mkdir -p "$ROOT/.rapp"
chmod 700 "$ROOT/.rapp"
if [[ -e "$RAPP_CHECKOUT" && ! -d "$RAPP_CHECKOUT/.git" ]]; then
  fail "$RAPP_CHECKOUT exists but is not a Git checkout"
fi
if [[ ! -d "$RAPP_CHECKOUT/.git" ]]; then
  git clone --filter=blob:none --no-checkout \
    https://github.com/kody-w/RAPP.git "$RAPP_CHECKOUT"
  git -C "$RAPP_CHECKOUT" sparse-checkout init --cone
  git -C "$RAPP_CHECKOUT" sparse-checkout set "$KERNEL_RELATIVE"
else
  ORIGIN="$(git -C "$RAPP_CHECKOUT" remote get-url origin)"
  case "$ORIGIN" in
    https://github.com/kody-w/RAPP|https://github.com/kody-w/RAPP.git|git@github.com:kody-w/RAPP.git) ;;
    *) fail "refusing unexpected canonical RAPP origin: $ORIGIN" ;;
  esac
fi

rm -f -- "$KERNEL/agents/rapp_play_pokemon_agent.py"
git -C "$RAPP_CHECKOUT" fetch --depth 1 origin "$RAPP_REF"
git -C "$RAPP_CHECKOUT" checkout --detach FETCH_HEAD
[[ "$(git -C "$RAPP_CHECKOUT" rev-parse HEAD)" == "$RAPP_REF" ]] ||
  fail "canonical RAPP checkout did not resolve the pinned commit"
[[ -f "$KERNEL/brainstem.py" && -f "$KERNEL/agents/basic_agent.py" ]] ||
  fail "pinned canonical RAPP checkout does not contain the brainstem contract"

if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check --quiet \
  --upgrade "pip>=24,<27"
"$VENV/bin/python" -m pip install --disable-pip-version-check --quiet \
  -r "$ROOT/requirements-rapp.txt" -e "$ROOT[runtime]"

export RAPP_BRAINSTEM_DIR="$KERNEL"
SMOKE_DIR="$ROOT/.work-smoke"
rm -rf -- "$SMOKE_DIR"
mkdir -p "$SMOKE_DIR"
chmod 700 "$SMOKE_DIR"
"$VENV/bin/python" -m rapp_play_pokemon.brainstem \
  --source "$ROOT/agent.py" \
  --brainstem-dir "$KERNEL" \
  --smoke-runtime-dir "$SMOKE_DIR"
rm -rf -- "$SMOKE_DIR"

if ((DOWNLOAD_COPILOT)); then
  "$VENV/bin/python" -m copilot download-runtime
fi

if [[ "$MODE" == "setup" ]]; then
  printf 'Setup complete. Use ./launch.sh upload or ./launch.sh start --rom "/path/to/Pokemon Red.gb"\n'
  exit 0
fi
if [[ "$MODE" == "rom" ]]; then
  exec "$ROOT/launch.sh" start --rom "$ROM" "${LAUNCH_ARGS[@]}"
fi
exec "$ROOT/launch.sh" upload "${LAUNCH_ARGS[@]}"
