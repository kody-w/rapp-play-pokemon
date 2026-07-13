"""RAPP cartridge for a local Copilot-powered Pokemon Red playthrough.

Users must explicitly provide their own legally obtained ROM. The ROM is never
copied, attached to Copilot, served by the viewer, or included in this project.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import http.server
import importlib.util
import io
import json
import logging
import os
import queue
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
import webbrowser
from collections import deque
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Optional

try:
    from agents.basic_agent import BasicAgent
except ModuleNotFoundError:
    try:
        from basic_agent import BasicAgent
    except ModuleNotFoundError:
        from openrappter.agents.basic_agent import BasicAgent

try:
    import fcntl
except (
    ImportError
):  # pragma: no cover - the supported runtime targets Unix/macOS
    fcntl = None


CARTRIDGE_FILE = Path(__file__).resolve()
CARTRIDGE_MANIFEST = {
    "schema_version": 1,
    "id": "rapp-play-pokemon",
    "name": "RAPP Plays Pokemon",
    "entry_class": "PokemonAgent",
    "contract": "BasicAgent.perform(**kwargs) -> str",
    "source": "https://github.com/kody-w/rapp-play-pokemon",
    "canonical_brainstem": "https://github.com/kody-w/RAPP",
    "runtime": "local",
    "rom_included": False,
}
LOGGER = logging.getLogger("rapp.play_pokemon")
VALID_BUTTONS = ("a", "b", "start", "select", "up", "down", "left", "right")
BADGE_NAMES = (
    "Boulder",
    "Cascade",
    "Thunder",
    "Rainbow",
    "Soul",
    "Marsh",
    "Volcano",
    "Earth",
)
DEFAULT_RUNTIME_DIR = Path.home() / ".rapp" / "rapp-play-pokemon"
DEFAULT_PORT = 8765
MAX_BUTTONS_PER_DECISION = 16
MAX_DECISIONS_PER_SESSION = 24
COPILOT_START_TIMEOUT_SECONDS = 90
COPILOT_STOP_TIMEOUT_SECONDS = 15
COPILOT_THREAD_SHUTDOWN_TIMEOUT_SECONDS = COPILOT_STOP_TIMEOUT_SECONDS * 3 + 5
COPILOT_DOWNLOAD_TIMEOUT_SECONDS = 180
RECORDER_QUEUE_SECONDS = 2
RECORDER_WRITER_TIMEOUT_SECONDS = 5
SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS = COPILOT_THREAD_SHUTDOWN_TIMEOUT_SECONDS + 40
DEFAULT_MAX_CLIPS = 200
DEFAULT_MAX_STATES = 256
DEFAULT_MAX_STORAGE_GB = 20.0
DEFAULT_MIN_FREE_GB = 2.0
STATUS_HEARTBEAT_FRESH_SECONDS = 15
SUPERVISOR_HEARTBEAT_TIMEOUT_SECONDS = 45
SUPERVISOR_STARTUP_TIMEOUT_SECONDS = 180
SUPERVISOR_STALE_HEARTBEAT_EXIT = 75
RESTART_REQUEST_NAME = "restart-request.json"
GENERATED_CLIP_RE = re.compile(r"^clip-\d{4,}-\d{8}-\d{6}(?:-\d{6})?\.mp4$")
GENERATED_STATE_RE = re.compile(r"^state-\d{8}-\d{6}-\d{6}\.state$")
GENERATED_PARTIAL_RE = re.compile(
    r"^\.clip-\d{4,}-\d{8}-\d{6}(?:-\d{6})?\.mp4\.partial\.mp4$"
)


MAP_NAMES = {
    0x00: "Pallet Town",
    0x01: "Viridian City",
    0x02: "Pewter City",
    0x03: "Cerulean City",
    0x04: "Lavender Town",
    0x05: "Vermilion City",
    0x06: "Celadon City",
    0x07: "Fuchsia City",
    0x08: "Cinnabar Island",
    0x09: "Indigo Plateau",
    0x0A: "Saffron City",
    0x0C: "Route 1",
    0x0D: "Route 2",
    0x0E: "Route 3",
    0x0F: "Route 4",
    0x10: "Route 5",
    0x11: "Route 6",
    0x12: "Route 7",
    0x13: "Route 8",
    0x14: "Route 9",
    0x15: "Route 10",
    0x16: "Route 11",
    0x17: "Route 12",
    0x18: "Route 13",
    0x19: "Route 14",
    0x1A: "Route 15",
    0x1B: "Route 16",
    0x1C: "Route 17",
    0x1D: "Route 18",
    0x1E: "Route 19",
    0x1F: "Route 20",
    0x20: "Route 21",
    0x21: "Route 22",
    0x22: "Route 23",
    0x23: "Route 24",
    0x24: "Route 25",
    0x25: "Player's House 1F",
    0x26: "Player's House 2F",
    0x27: "Rival's House",
    0x28: "Oak's Lab",
    0x29: "Viridian Pokemon Center",
    0x2A: "Viridian Mart",
    0x2D: "Viridian Gym",
    0x33: "Viridian Forest",
    0x36: "Pewter Gym",
    0x3A: "Pewter Pokemon Center",
    0x3B: "Mt. Moon 1F",
    0x3C: "Mt. Moon B1F",
    0x3D: "Mt. Moon B2F",
    0x40: "Cerulean Pokemon Center",
    0x41: "Cerulean Gym",
    0x44: "Mt. Moon Pokemon Center",
    0x52: "Rock Tunnel 1F",
    0x53: "Power Plant",
    0x58: "Bill's House",
    0x59: "Vermilion Pokemon Center",
    0x5C: "Vermilion Gym",
    0x5E: "Vermilion Dock",
    0x5F: "S.S. Anne 1F",
    0x60: "S.S. Anne 2F",
    0x61: "S.S. Anne 3F",
    0x62: "S.S. Anne B1F",
    0x65: "S.S. Anne Captain's Room",
    0x6C: "Victory Road 1F",
    0x71: "Lance",
    0x76: "Hall of Fame",
    0x78: "Champion's Room",
    0x85: "Celadon Pokemon Center",
    0x86: "Celadon Gym",
    0x87: "Game Corner",
    0x8D: "Lavender Pokemon Center",
    0x8E: "Pokemon Tower 1F",
    0x8F: "Pokemon Tower 2F",
    0x90: "Pokemon Tower 3F",
    0x91: "Pokemon Tower 4F",
    0x92: "Pokemon Tower 5F",
    0x93: "Pokemon Tower 6F",
    0x94: "Pokemon Tower 7F",
    0x9A: "Fuchsia Pokemon Center",
    0x9C: "Safari Zone Entrance",
    0x9D: "Fuchsia Gym",
    0xA5: "Pokemon Mansion 1F",
    0xA6: "Cinnabar Gym",
    0xAB: "Cinnabar Pokemon Center",
    0xAE: "Indigo Plateau Lobby",
    0xB2: "Saffron Gym",
    0xB5: "Silph Co. 1F",
    0xB6: "Saffron Pokemon Center",
    0xC0: "Seafoam Islands 1F",
    0xC2: "Victory Road 2F",
    0xC5: "Diglett's Cave",
    0xC6: "Victory Road 3F",
    0xC7: "Rocket Hideout B1F",
    0xC8: "Rocket Hideout B2F",
    0xC9: "Rocket Hideout B3F",
    0xCA: "Rocket Hideout B4F",
    0xCF: "Silph Co. 2F",
    0xD0: "Silph Co. 3F",
    0xD1: "Silph Co. 4F",
    0xD2: "Silph Co. 5F",
    0xD3: "Silph Co. 6F",
    0xD4: "Silph Co. 7F",
    0xD5: "Silph Co. 8F",
    0xD9: "Safari Zone East",
    0xDA: "Safari Zone North",
    0xDB: "Safari Zone West",
    0xDC: "Safari Zone Center",
    0xE8: "Rock Tunnel B1F",
    0xE9: "Silph Co. 9F",
    0xEA: "Silph Co. 10F",
    0xEB: "Silph Co. 11F",
    0xF5: "Lorelei",
    0xF6: "Bruno",
    0xF7: "Agatha",
}


VIEWER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Copilot Plays Pokemon Red</title>
<link rel="stylesheet" href="/viewer.css">
</head>
<body>
<main>
<h1>Copilot Plays Pokemon Red</h1>
<p>Live local emulator feed. Manual buttons temporarily take priority.</p>
<div class="grid">
  <section class="card">
    <img id="game" alt="Pokemon Red live frame">
    <div>
      <button data-action="manual">Take Over</button>
      <button data-action="autonomy">Return to AI</button>
      <button class="alt" data-action="pause">Pause</button>
      <button class="alt" data-action="resume">Resume</button>
      <button data-action="checkpoint">Save + New Clip</button>
      <button class="warn" data-action="stop">Stop</button>
    </div>
  </section>
  <section class="card">
    <div class="dpad">
      <span></span><button data-button="up">Up</button><span></span>
      <button data-button="left">Left</button><button data-button="a">A</button><button data-button="right">Right</button>
      <button data-button="b">B</button><button data-button="down">Down</button><button data-button="start">Start</button>
    </div>
    <div class="center"><button data-button="select">Select</button></div>
    <pre id="status">Connecting...</pre>
  </section>
</div>
<section class="card spaced clips">
  <h2>Saved clips</h2>
  <div id="clips"></div>
</section>
</main>
<script src="/viewer.js" defer></script>
</body>
</html>
"""


VIEWER_CSS = """
:root { color-scheme: dark; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
body { margin: 0; background: #10141f; color: #eef3ff; }
main { max-width: 1050px; margin: auto; padding: 20px; }
h1 { color: #ffdf4d; margin: 0 0 8px; }
.grid { display: grid; grid-template-columns: minmax(320px, 2fr) minmax(280px, 1fr); gap: 18px; }
.card { background: #192235; border: 1px solid #34445f; border-radius: 12px; padding: 16px; }
#game { width: 100%; max-width: 640px; image-rendering: pixelated; background: black; }
#status { white-space: pre-wrap; overflow-wrap: anywhere; min-height: 250px; }
button { background: #2c6bed; color: white; border: 0; border-radius: 6px; padding: 10px 14px; margin: 3px; cursor: pointer; }
button.alt { background: #59677f; }
button.warn { background: #bd3d45; }
.dpad { display: grid; grid-template-columns: repeat(3, 55px); justify-content: center; }
.clips a { color: #8fc5ff; display: block; margin: 6px 0; }
.center { text-align: center; }
.spaced { margin-top: 18px; }
@media (max-width: 760px) { .grid { grid-template-columns: 1fr; } }
"""


VIEWER_JS = """
const game = document.getElementById('game');
async function control(action, button) {
  const response = await fetch('/api/control', {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify({action, button})
  });
  if (!response.ok) throw new Error(`control failed: ${response.status}`);
}
function press(button) { return control('press', button); }
document.querySelectorAll('[data-action]').forEach(
  element => element.addEventListener('click', () => control(element.dataset.action))
);
document.querySelectorAll('[data-button]').forEach(
  element => element.addEventListener('click', () => press(element.dataset.button))
);
async function refresh() {
  game.src = '/frame.png?t=' + Date.now();
  try {
    const data = await (await fetch('/api/status?t=' + Date.now())).json();
    const view = {
      lifecycle: data.lifecycle, running: data.running, control: data.control_mode,
      paused: data.paused, brain: data.brain_status,
      location: data.game_state && data.game_state.location,
      coordinates: data.game_state && data.game_state.coordinates,
      badges: data.game_state && data.game_state.badges,
      party: data.game_state && data.game_state.party,
      phase: data.phase, objective: data.objective, observation: data.observation,
      reason: data.reason,
      last_action: data.last_action, model_calls: data.model_calls,
      current_clip: data.current_clip, last_error: data.last_error
    };
    document.getElementById('status').textContent = JSON.stringify(view, null, 2);
    const clips = document.getElementById('clips');
    clips.replaceChildren();
    for (const clip of data.clips || []) {
      const link = document.createElement('a');
      link.href = '/clips/' + encodeURIComponent(clip.name);
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = `${clip.name} (${clip.megabytes} MB)`;
      clips.appendChild(link);
    }
    if (!clips.childElementCount) clips.textContent = 'No completed clips yet.';
  } catch (error) {
    document.getElementById('status').textContent = 'Viewer disconnected: ' + error;
  }
}
setInterval(refresh, 125);
refresh();
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(value, indent=2, sort_keys=True).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    os.chmod(path, 0o600)
    fsync_directory(path.parent)


def read_json(path: Path, default: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (
        FileNotFoundError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        OSError,
    ):
        return dict(default or {})
    return value if isinstance(value, dict) else dict(default or {})


def process_is_alive(pid: Any) -> bool:
    try:
        numeric_pid = int(pid)
        if numeric_pid <= 0:
            return False
        os.kill(numeric_pid, 0)
        return True
    except (TypeError, ValueError, ProcessLookupError, PermissionError, OSError):
        return False


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_copilot_runtime(
    timeout_seconds: int = COPILOT_DOWNLOAD_TIMEOUT_SECONDS,
) -> None:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "copilot", "download-runtime"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            f"Copilot SDK runtime download exceeded {timeout_seconds} seconds"
        ) from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            "Copilot SDK runtime preparation failed"
            + (f": {detail[-1000:]}" if detail else "")
        )


def rom_title(path: Path) -> str:
    if is_cloud_placeholder(path):
        return ""
    try:
        with path.open("rb") as handle:
            handle.seek(0x134)
            title = handle.read(16).split(b"\x00", 1)[0]
        return title.decode("ascii", errors="ignore").strip()
    except OSError:
        return ""


def is_cloud_placeholder(path: Path) -> bool:
    try:
        flags = path.stat().st_flags
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(flags & getattr(stat, "SF_DATALESS", 0x40000000))


def is_pokemon_red_rom(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() != ".gb" or is_cloud_placeholder(path):
        return False
    title = rom_title(path).upper()
    return "POKEMON RED" in title


def discover_pokemon_red_rom(
    explicit: Optional[str] = None,
    runtime_dir: Optional[Path] = None,
) -> Path:
    if explicit:
        resolved = Path(explicit).expanduser().resolve()
        if is_cloud_placeholder(resolved):
            raise FileNotFoundError(
                f"Pokemon Red ROM is still a cloud placeholder: {resolved}"
            )
        if is_pokemon_red_rom(resolved):
            return resolved
        raise FileNotFoundError(f"Not a readable Pokemon Red Game Boy ROM: {explicit}")

    candidates: list[Path] = []

    configured = os.environ.get("RAPP_PLAY_POKEMON_ROM")
    if configured:
        candidates.append(Path(configured).expanduser())

    configured_runtime = (
        Path(runtime_dir).expanduser()
        if runtime_dir is not None
        else DEFAULT_RUNTIME_DIR
    )
    runtime_config = read_json(configured_runtime / "config.json")
    if runtime_config.get("rom_path"):
        candidates.append(Path(str(runtime_config["rom_path"])).expanduser())

    seen: set[Path] = set()
    placeholders: list[Path] = []
    resolved_candidates = sorted(
        (candidate.expanduser().resolve() for candidate in candidates),
        key=lambda path: (is_cloud_placeholder(path), str(path)),
    )
    for resolved in resolved_candidates:
        if resolved in seen:
            continue
        seen.add(resolved)
        if is_cloud_placeholder(resolved):
            placeholders.append(resolved)
            continue
        if is_pokemon_red_rom(resolved):
            return resolved

    if placeholders:
        raise FileNotFoundError(
            "Pokemon Red was found, but every copy is still a cloud placeholder: "
            + ", ".join(str(path) for path in placeholders)
        )
    raise FileNotFoundError(
        "No Pokemon Red ROM was configured. Pass rom_path or set "
        "RAPP_PLAY_POKEMON_ROM to your own legally obtained .gb file."
    )


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, character in enumerate(cleaned):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("Copilot response did not contain a JSON object")


def normalize_brain_decision(text: str) -> dict[str, Any]:
    raw = extract_json_object(text)
    raw_buttons = raw.get("buttons", [])
    if isinstance(raw_buttons, str):
        raw_buttons = [raw_buttons]
    if not isinstance(raw_buttons, list):
        raise ValueError("Copilot response field 'buttons' must be an array")

    buttons = [
        str(button).lower()
        for button in raw_buttons
        if str(button).lower() in VALID_BUTTONS
    ][:MAX_BUTTONS_PER_DECISION]
    if not buttons:
        raise ValueError("Copilot response did not contain a valid button")

    return {
        "phase": str(raw.get("phase", "unknown"))[:80],
        "observation": str(raw.get("observation", ""))[:500],
        "objective": str(raw.get("objective", ""))[:500],
        "reason": str(raw.get("reason", ""))[:500],
        "buttons": buttons,
        "checkpoint": raw.get("checkpoint") is True,
    }


def parse_agent_action(query: str) -> tuple[str, Optional[str]]:
    normalized = query.strip().lower()
    button_match = re.search(
        r"\bpress\s+(a|b|start|select|up|down|left|right)\b", normalized
    )
    if button_match:
        return "press", button_match.group(1)
    if any(word in normalized for word in ("checkpoint", "new clip", "save state")):
        return "checkpoint", None
    if "pause" in normalized:
        return "pause", None
    if any(
        phrase in normalized for phrase in ("take over", "manual control", "my control")
    ):
        return "manual", None
    if any(
        phrase in normalized
        for phrase in (
            "return to ai",
            "return to copilot",
            "autonomy",
            "continue playing",
        )
    ):
        return "autonomy", None
    if "resume" in normalized:
        return "resume", None
    if any(word in normalized for word in ("stop", "quit", "end session")):
        return "stop", None
    if any(word in normalized for word in ("watch", "viewer", "open window")):
        return "view", None
    if "status" in normalized or "progress" in normalized:
        return "status", None
    return "start", None


def set_desired_running(runtime_dir: Path, running: bool) -> None:
    desired_path = runtime_dir / "desired.json"
    if not desired_path.exists():
        return
    desired = read_json(desired_path)
    desired["running"] = running
    desired["updated_at"] = utc_now()
    atomic_write_json(desired_path, desired)


def seed_legacy_ram_provenance(runtime_dir: Path) -> None:
    legacy = runtime_dir / "pokemon-red.ram"
    provenance_path = runtime_dir / "legacy-ram-provenance.json"
    if not legacy.exists() or provenance_path.exists():
        return
    previous_config = read_json(runtime_dir / "config.json")
    previous_status = read_json(runtime_dir / "status.json")
    atomic_write_json(
        provenance_path,
        {
            "rom_path": previous_config.get("rom_path"),
            "rom_sha256": (
                previous_config.get("rom_sha256")
                or previous_status.get("rom_sha256")
            ),
            "recorded_at": utc_now(),
        },
    )


def append_control(runtime_dir: Path, command: dict[str, Any]) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runtime_dir, 0o700)
    command = {**command, "timestamp": utc_now()}
    if command.get("action") == "stop":
        set_desired_running(runtime_dir, False)
    control_path = runtime_dir / "control.jsonl"
    descriptor = os.open(control_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(command, separators=(",", ":")) + "\n")


def list_clips(runtime_dir: Path) -> list[dict[str, Any]]:
    clips = []
    for clip in sorted((runtime_dir / "clips").glob("*.mp4"), reverse=True):
        if (
            clip.name.startswith(".")
            or clip.is_symlink()
            or not GENERATED_CLIP_RE.fullmatch(clip.name)
        ):
            continue
        try:
            size = clip.stat().st_size
        except OSError:
            continue
        manifest = read_json(clip.with_suffix(".json"))
        if not (
            manifest.get("schema_version") == 1
            and manifest.get("name") == clip.name
            and manifest.get("sha256")
        ):
            continue
        clips.append(
            {
                "name": clip.name,
                "megabytes": round(size / 1_048_576, 2),
                "duration_seconds": manifest.get("duration_seconds"),
                "reason": manifest.get("reason"),
                "location": manifest.get("game_state", {}).get("location")
                if isinstance(manifest.get("game_state"), dict)
                else None,
            }
        )
    return clips[:100]


def heartbeat_age_seconds(status: dict[str, Any]) -> Optional[float]:
    heartbeat = (
        status.get("heartbeat_at")
        or status.get("updated_at")
        or status.get("started_at")
    )
    if not heartbeat:
        return None
    try:
        updated = datetime.fromisoformat(str(heartbeat))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(
        0.0,
        (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds(),
    )


def runtime_status(runtime_dir: Path = DEFAULT_RUNTIME_DIR) -> dict[str, Any]:
    status = read_json(runtime_dir / "status.json", {"running": False})
    lifecycle = status.get("lifecycle")
    alive = process_is_alive(status.get("pid"))
    heartbeat_age = heartbeat_age_seconds(status)
    fresh_heartbeat = bool(
        heartbeat_age is not None and heartbeat_age < STATUS_HEARTBEAT_FRESH_SECONDS
    )
    initializing = bool(
        lifecycle == "initializing"
        and heartbeat_age is not None
        and heartbeat_age < SUPERVISOR_STARTUP_TIMEOUT_SECONDS
    )
    status["running"] = bool(
        alive
        and status.get("running", True)
        and (
            initializing
            or (lifecycle == "ready" and fresh_heartbeat)
            or lifecycle is None
        )
    )
    status["clips"] = list_clips(runtime_dir)
    status["viewer_url"] = f"http://127.0.0.1:{status.get('port', DEFAULT_PORT)}"
    return status


def heartbeat_is_stale(status: dict[str, Any], threshold_seconds: int = 30) -> bool:
    age = heartbeat_age_seconds(status)
    return age is None or age > threshold_seconds


def authenticated_viewer_url(
    runtime_dir: Path,
    status: Optional[dict[str, Any]] = None,
) -> str:
    current = status or runtime_status(runtime_dir)
    base = f"http://127.0.0.1:{current.get('port', DEFAULT_PORT)}"
    auth = read_json(runtime_dir / "viewer-auth.json")
    token = auth.get("token")
    if not isinstance(token, str) or not token:
        return base
    return f"{base}/?token={urllib.parse.quote(token, safe='')}"


def public_runtime_status(runtime_dir: Path = DEFAULT_RUNTIME_DIR) -> dict[str, Any]:
    status = runtime_status(runtime_dir)
    for key in (
        "pid",
        "rom_path",
        "rom_sha256",
        "runtime_dir",
        "loaded_state",
        "instance_id",
    ):
        status.pop(key, None)
    current_clip = status.get("current_clip")
    if current_clip:
        status["current_clip"] = Path(str(current_clip)).name
    checkpoint = status.get("last_checkpoint")
    if isinstance(checkpoint, dict):
        status["last_checkpoint"] = {
            key: value for key, value in checkpoint.items() if key != "path"
        }
    return status


def acquire_runtime_lock(
    runtime_dir: Path,
    instance_id: str,
    lock_name: str = "player.lock",
) -> Any:
    if fcntl is None:
        raise RuntimeError("Pokemon runtime locking is unavailable on this platform")
    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runtime_dir, 0o700)
    lock_path = runtime_dir / lock_name
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    handle = os.fdopen(descriptor, "r+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise RuntimeError(
            "Another Pokemon player owns this runtime directory"
        ) from error
    handle.seek(0)
    handle.truncate()
    handle.write(instance_id)
    handle.flush()
    os.fsync(handle.fileno())
    return handle


class PokemonAgent(BasicAgent):
    def __init__(self):
        self.name = "Pokemon"
        self.metadata = {
            "name": self.name,
            "description": (
                "Let GitHub Copilot play a local Pokemon Red ROM through PyBoy. "
                "Starts a live localhost viewer, records rotating MP4 clips, and "
                "supports status, pause, resume, checkpoint, manual button, view, "
                "and stop actions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "start",
                            "status",
                            "manual",
                            "autonomy",
                            "pause",
                            "resume",
                            "checkpoint",
                            "press",
                            "view",
                            "stop",
                        ],
                        "description": "Player lifecycle or control action",
                    },
                    "query": {
                        "type": "string",
                        "description": "Natural-language player command",
                    },
                    "rom_path": {
                        "type": "string",
                        "description": "Optional local path to a Pokemon Red .gb ROM",
                    },
                    "button": {
                        "type": "string",
                        "enum": list(VALID_BUTTONS),
                        "description": "Button for the press action",
                    },
                    "visible": {
                        "type": "boolean",
                        "description": "Show the native PyBoy window in addition to the browser viewer",
                    },
                    "clip_minutes": {
                        "type": "number",
                        "description": "Automatic clip duration in minutes",
                    },
                    "port": {
                        "type": "integer",
                        "description": "Local viewer port",
                    },
                    "open_viewer": {
                        "type": "boolean",
                        "description": "Open the authenticated loopback viewer after startup",
                    },
                    "resume": {
                        "type": "boolean",
                        "description": "Resume the newest valid checkpoint for this ROM",
                    },
                    "model": {
                        "type": "string",
                        "description": "Copilot model (defaults to gpt-5.6-sol)",
                    },
                    "decision_timeout": {
                        "type": "integer",
                        "description": "Maximum seconds for one Copilot decision",
                    },
                    "startup_timeout": {
                        "type": "number",
                        "description": "Seconds to wait for emulator, recorder, brain, and viewer readiness",
                    },
                    "max_clips": {
                        "type": "integer",
                        "description": "Maximum generated clips retained locally",
                    },
                    "max_states": {
                        "type": "integer",
                        "description": "Maximum generated save states retained locally",
                    },
                    "max_storage_gb": {
                        "type": "number",
                        "description": "Maximum storage used by generated Pokemon artifacts",
                    },
                    "min_free_gb": {
                        "type": "number",
                        "description": "Minimum free disk space preserved",
                    },
                },
                "required": [],
            },
        }
        super().__init__(name=self.name, metadata=self.metadata)

    def perform(self, **kwargs):
        runtime_dir = Path(
            str(kwargs.get("runtime_dir", DEFAULT_RUNTIME_DIR))
        ).expanduser()
        query = str(kwargs.get("query", "start"))
        inferred_action, inferred_button = parse_agent_action(query)
        action = str(kwargs.get("action") or inferred_action).lower()

        if action == "status":
            return json.dumps({"status": "success", **runtime_status(runtime_dir)})

        if action == "view":
            status = runtime_status(runtime_dir)
            if not status["running"]:
                return json.dumps(
                    {"status": "error", "message": "Pokemon player is not running"}
                )
            viewer_url = authenticated_viewer_url(runtime_dir, status)
            webbrowser.open(viewer_url)
            return json.dumps(
                {
                    "status": "success",
                    "message": "Opened the local Pokemon viewer",
                    "viewer_url": viewer_url,
                }
            )

        if action in {
            "manual",
            "autonomy",
            "pause",
            "resume",
            "checkpoint",
            "stop",
            "press",
        }:
            status = runtime_status(runtime_dir)
            if action == "stop" and not status["running"]:
                desired = read_json(runtime_dir / "desired.json")
                supervisor = read_json(runtime_dir / "supervisor.json")
                supervisor_alive = process_is_alive(supervisor.get("pid"))
                if desired.get("running") or supervisor_alive:
                    set_desired_running(runtime_dir, False)
                    return json.dumps(
                        {
                            "status": "success",
                            "message": "Pokemon supervisor accepted stop",
                            "viewer_url": authenticated_viewer_url(runtime_dir, status),
                        }
                    )
            if not status["running"]:
                return json.dumps(
                    {"status": "error", "message": "Pokemon player is not running"}
                )
            button = str(kwargs.get("button") or inferred_button or "").lower()
            if action == "press" and button not in VALID_BUTTONS:
                return json.dumps(
                    {
                        "status": "error",
                        "message": f"button must be one of: {', '.join(VALID_BUTTONS)}",
                    }
                )
            append_control(runtime_dir, {"action": action, "button": button or None})
            return json.dumps(
                {
                    "status": "success",
                    "message": f"Pokemon player accepted {action}",
                    "viewer_url": authenticated_viewer_url(runtime_dir, status),
                }
            )

        if action != "start":
            return json.dumps(
                {"status": "error", "message": f"Unknown Pokemon action: {action}"}
            )

        existing = runtime_status(runtime_dir)
        if existing["running"]:
            return json.dumps(
                {
                    "status": "success",
                    "message": "Pokemon player is already running",
                    **existing,
                    "viewer_url": authenticated_viewer_url(runtime_dir, existing),
                }
            )

        try:
            rom = discover_pokemon_red_rom(kwargs.get("rom_path"), runtime_dir)
        except (FileNotFoundError, OSError) as error:
            return json.dumps({"status": "error", "message": str(error)})

        if importlib.util.find_spec("pyboy") is None:
            return json.dumps(
                {
                    "status": "error",
                    "message": (
                        "PyBoy is not installed in this Python environment. "
                        'Install runtime dependencies with pip install -e ".[runtime]".'
                    ),
                }
            )
        if sys.version_info < (3, 11) or importlib.util.find_spec("copilot") is None:
            return json.dumps(
                {
                    "status": "error",
                    "message": (
                        "The Pokemon player requires Python 3.11+ and the "
                        "github-copilot-sdk package"
                    ),
                }
            )
        if not shutil.which("ffmpeg"):
            return json.dumps({"status": "error", "message": "ffmpeg is not installed"})
        try:
            ensure_copilot_runtime()
        except RuntimeError as error:
            return json.dumps({"status": "error", "message": str(error)})

        try:
            port = int(kwargs.get("port", DEFAULT_PORT))
            clip_minutes = float(kwargs.get("clip_minutes", 10))
            startup_timeout_requested = float(
                kwargs.get("startup_timeout", SUPERVISOR_STARTUP_TIMEOUT_SECONDS)
            )
            max_clips = int(kwargs.get("max_clips", DEFAULT_MAX_CLIPS))
            max_states = int(kwargs.get("max_states", DEFAULT_MAX_STATES))
            max_storage_gb = float(kwargs.get("max_storage_gb", DEFAULT_MAX_STORAGE_GB))
            min_free_gb = float(kwargs.get("min_free_gb", DEFAULT_MIN_FREE_GB))
            decision_timeout = int(kwargs.get("decision_timeout", 180))
        except (TypeError, ValueError) as error:
            return json.dumps(
                {
                    "status": "error",
                    "message": f"Invalid Pokemon configuration: {error}",
                }
            )
        if not 1 <= port <= 65535:
            return json.dumps({"status": "error", "message": "port must be 1-65535"})
        if (
            clip_minutes <= 0
            or max_clips < 1
            or max_states < 2
            or max_storage_gb <= 0
            or min_free_gb < 0
            or decision_timeout < 10
        ):
            return json.dumps(
                {
                    "status": "error",
                    "message": "Retention, timing, and storage values must be positive",
                }
            )

        runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(runtime_dir, 0o700)
        atomic_write_json(
            runtime_dir / "runtime-owner.json",
            {
                "product": "rapp-play-pokemon",
                "created_at": utc_now(),
            },
        )
        previous_config = read_json(runtime_dir / "config.json")
        seed_legacy_ram_provenance(runtime_dir)
        atomic_write_json(
            runtime_dir / "config.json",
            {
                "rom_path": str(rom),
                "rom_sha256": file_sha256(rom),
                "previous_rom_path": previous_config.get("rom_path"),
                "previous_rom_sha256": previous_config.get("rom_sha256"),
                "updated_at": utc_now(),
            },
        )
        log_descriptor = os.open(
            runtime_dir / "player.log",
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        log_handle = os.fdopen(log_descriptor, "a", encoding="utf-8")
        instance_id = uuid.uuid4().hex
        command = [
            sys.executable,
            str(CARTRIDGE_FILE),
            "supervise",
            "--rom",
            str(rom),
            "--runtime-dir",
            str(runtime_dir),
            "--port",
            str(port),
            "--clip-minutes",
            str(clip_minutes),
            "--model",
            str(kwargs.get("model", "gpt-5.6-sol")),
            "--decision-timeout",
            str(decision_timeout),
            "--instance-id",
            instance_id,
            "--max-clips",
            str(max_clips),
            "--max-states",
            str(max_states),
            "--max-storage-gb",
            str(max_storage_gb),
            "--min-free-gb",
            str(min_free_gb),
        ]
        if kwargs.get("open_viewer", True):
            command.append("--open-viewer")
        if kwargs.get("visible", False):
            command.append("--visible")
        if kwargs.get("resume", True) is False:
            command.append("--no-resume")
        process = subprocess.Popen(
            command,
            cwd=str(runtime_dir),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log_handle.close()
        minimum_startup_timeout = (
            COPILOT_START_TIMEOUT_SECONDS + COPILOT_STOP_TIMEOUT_SECONDS * 2 + 10
        )
        startup_timeout = max(
            startup_timeout_requested,
            minimum_startup_timeout,
        )
        deadline = time.monotonic() + startup_timeout
        viewer_url = f"http://127.0.0.1:{port}"
        while time.monotonic() < deadline:
            child_status = read_json(runtime_dir / "status.json")
            child_instance = child_status.get("instance_id")
            lifecycle = child_status.get("lifecycle")
            if child_instance == instance_id and lifecycle == "ready":
                viewer_url = authenticated_viewer_url(runtime_dir, child_status)
                return json.dumps(
                    {
                        "status": "success",
                        "message": "Copilot Plays Pokemon Red is ready",
                        "pid": child_status.get("pid"),
                        "supervisor_pid": process.pid,
                        "rom_title": rom_title(rom),
                        "viewer_url": viewer_url,
                        "recordings": str(runtime_dir / "clips"),
                        "brain_backend": child_status.get("brain_backend"),
                    }
                )
            if (
                child_instance
                and child_instance != instance_id
                and lifecycle == "ready"
                and process_is_alive(child_status.get("pid"))
            ):
                viewer_url = authenticated_viewer_url(runtime_dir, child_status)
                return json.dumps(
                    {
                        "status": "success",
                        "message": "Pokemon player is already running",
                        "viewer_url": viewer_url,
                    }
                )
            if process.poll() is not None:
                return json.dumps(
                    {
                        "status": "error",
                        "message": child_status.get(
                            "last_error",
                            f"Pokemon player exited with code {process.returncode}",
                        ),
                    }
                )
            time.sleep(0.2)
        process.terminate()
        try:
            process.wait(timeout=SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        return json.dumps(
            {
                "status": "error",
                "message": "Pokemon player did not become ready before the startup timeout",
            }
        )


class PokemonMemoryReader:
    def __init__(self, memory: Any):
        self.memory = memory

    def _read(self, address: int, default: int = 0) -> int:
        try:
            return int(self.memory[address])
        except (IndexError, TypeError, ValueError):
            return default

    def _text(self, start: int, length: int) -> str:
        output = []
        for address in range(start, start + length):
            value = self._read(address)
            if value == 0x50:
                break
            if 0x80 <= value <= 0x99:
                output.append(chr(value - 0x80 + ord("A")))
            elif 0xA0 <= value <= 0xB9:
                output.append(chr(value - 0xA0 + ord("a")))
            elif 0xF6 <= value <= 0xFF:
                output.append(str(value - 0xF6))
            elif value == 0x7F:
                output.append(" ")
            elif value in (0xE8, 0xE9):
                output.append(".")
            elif value == 0xE6:
                output.append("?")
            elif value == 0xE7:
                output.append("!")
            elif value == 0xF4:
                output.append(",")
            elif value == 0xE3:
                output.append("-")
        return "".join(output).strip()

    def _screen_text(self) -> str:
        lines: list[str] = []
        current: list[str] = []
        spaces = 0
        for address in range(0xC3A0, 0xC507):
            value = self._read(address)
            character = ""
            if 0x80 <= value <= 0x99:
                character = chr(value - 0x80 + ord("A"))
            elif 0xA0 <= value <= 0xB9:
                character = chr(value - 0xA0 + ord("a"))
            elif 0xF6 <= value <= 0xFF:
                character = str(value - 0xF6)
            elif value == 0x7F:
                character = " "
            elif value in (0xE8, 0xE9):
                character = "."
            elif value == 0xE6:
                character = "?"
            elif value == 0xE7:
                character = "!"
            elif value == 0xF4:
                character = ","
            elif value == 0xE3:
                character = "-"
            elif value in (0x4E, 0x7C):
                if "".join(current).strip():
                    lines.append("".join(current).strip())
                current = []
                spaces = 0
                continue

            if character:
                current.append(character)
                spaces = spaces + 1 if character == " " else 0
                if spaces > 10:
                    if "".join(current).strip():
                        lines.append("".join(current).strip())
                    current = []
                    spaces = 0
        if "".join(current).strip():
            lines.append("".join(current).strip())
        deduplicated = list(dict.fromkeys(line for line in lines if len(line) > 1))
        return " | ".join(deduplicated[-8:])[:600]

    def snapshot(self) -> dict[str, Any]:
        map_id = self._read(0xD35E)
        badge_byte = self._read(0xD356)
        party_count = min(self._read(0xD163), 6)
        party = []
        bases = (0xD16B, 0xD197, 0xD1C3, 0xD1EF, 0xD21B, 0xD247)
        nicknames = (0xD2B5, 0xD2C0, 0xD2CB, 0xD2D6, 0xD2E1, 0xD2EC)
        for index in range(party_count):
            base = bases[index]
            party.append(
                {
                    "nickname": self._text(nicknames[index], 11),
                    "species_id": self._read(base),
                    "level": self._read(base + 0x21),
                    "hp": (self._read(base + 1) << 8) + self._read(base + 2),
                    "max_hp": (self._read(base + 0x22) << 8) + self._read(base + 0x23),
                }
            )
        return {
            "map_id": map_id,
            "location": MAP_NAMES.get(map_id, f"Map 0x{map_id:02X}"),
            "coordinates": {
                "x": self._read(0xD362),
                "y": self._read(0xD361),
            },
            "player_name": self._text(0xD158, 11),
            "rival_name": self._text(0xD34A, 8),
            "badges": [
                name for bit, name in enumerate(BADGE_NAMES) if badge_byte & (1 << bit)
            ],
            "badge_bits": badge_byte,
            "party_count": party_count,
            "party": party,
            "screen_text": self._screen_text(),
            "hall_of_fame": map_id == 0x76,
        }


def collision_ascii(pyboy: Any) -> Optional[str]:
    try:
        collision = pyboy.game_wrapper.game_area_collision()
        rows, columns = collision.shape
        if rows < 9 or columns < 10:
            return None
        row_step = max(1, rows // 9)
        column_step = max(1, columns // 10)
        output = []
        for row in range(9):
            characters = []
            for column in range(10):
                if row == 4 and column == 4:
                    characters.append("P")
                    continue
                values = collision[
                    row * row_step : min(rows, (row + 1) * row_step),
                    column * column_step : min(columns, (column + 1) * column_step),
                ]
                characters.append("." if bool(values.any()) else "#")
            output.append("".join(characters))
        return "\n".join(output)
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def rock_tunnel_route_guidance(game_state: dict[str, Any]) -> Optional[str]:
    map_id = game_state.get("map_id")
    coordinates = game_state.get("coordinates")
    if not isinstance(coordinates, dict):
        return None
    x = coordinates.get("x")
    y = coordinates.get("y")
    if not isinstance(x, int) or not isinstance(y, int):
        return None
    if map_id == 232:
        if x >= 24 or y >= 20:
            return (
                "Authoritative route stage: navigate to B1F ladder (27,3). "
                "Do not use Dig and do not route toward (3,33)."
            )
        return (
            "Authoritative route stage: navigate to B1F ladder (3,3), "
            "then take it to Rock Tunnel 1F. Never use Dig."
        )
    if map_id == 82:
        if x <= 10:
            return "Authoritative route stage: navigate to 1F ladder (17,11)."
        if x >= 30 and y >= 12:
            return (
                "Authoritative final stage: navigate to the south exit at "
                "(15,33) or (15,35), then continue south toward Lavender Town."
            )
        if y <= 8:
            return "Authoritative route stage: navigate to 1F ladder (37,3)."
        return (
            "Authoritative final stage: navigate to the south exit at "
            "(15,33) or (15,35)."
        )
    return None


def celadon_route_guidance(game_state: dict[str, Any]) -> Optional[str]:
    badges = game_state.get("badges")
    if isinstance(badges, list) and "Rainbow" in badges:
        return None
    map_id = game_state.get("map_id")
    if map_id == 6:
        return (
            "Authoritative Celadon objective: the Gym entrance warp is exactly "
            "at (12,27). Navigate to (12,27); if the hedge immediately north "
            "blocks entry, use Cut once and step south into the warp."
        )
    if map_id == 134:
        return (
            "Authoritative Celadon Gym objective: reach Erika at (4,3), "
            "defeat her, and obtain the Rainbow Badge."
        )
    return None


class ClipRecorder:
    def __init__(self, runtime_dir: Path, fps: int = 30):
        self.clips_dir = runtime_dir / "clips"
        self.clips_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.clips_dir, 0o700)
        self.fps = fps
        self.process: Optional[subprocess.Popen[bytes]] = None
        self.stderr_handle: Optional[Any] = None
        self.partial_path: Optional[Path] = None
        self.final_path: Optional[Path] = None
        self.frames_written = 0
        self.frames_dropped = 0
        self.started_at: Optional[str] = None
        self.frame_queue: "queue.Queue[Optional[bytes]]" = queue.Queue(
            maxsize=max(1, fps * RECORDER_QUEUE_SECONDS)
        )
        self.writer_thread: Optional[threading.Thread] = None
        self.writer_error: Optional[Exception] = None

    def _next_index(self) -> int:
        indexes = []
        for path in self.clips_dir.glob("clip-*.mp4"):
            match = re.match(r"clip-(\d+)-", path.name)
            manifest = read_json(path.with_suffix(".json"))
            if (
                match
                and GENERATED_CLIP_RE.fullmatch(path.name)
                and manifest.get("schema_version") == 1
                and manifest.get("name") == path.name
                and manifest.get("sha256")
            ):
                indexes.append(int(match.group(1)))
        for path in self.clips_dir.glob(".clip-*.partial.mp4"):
            match = re.match(r"\.clip-(\d+)-", path.name)
            if match and GENERATED_PARTIAL_RE.fullmatch(path.name):
                indexes.append(int(match.group(1)))
        return max(indexes, default=0) + 1

    def start(self) -> Path:
        if self.process:
            raise RuntimeError("Recorder is already running")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("ffmpeg is required for Pokemon recording")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        index = self._next_index()
        while True:
            self.final_path = self.clips_dir / f"clip-{index:04d}-{timestamp}.mp4"
            self.partial_path = self.clips_dir / f".{self.final_path.name}.partial.mp4"
            if not self.final_path.exists() and not self.partial_path.exists():
                break
            index += 1
        error_path = self.clips_dir / f".{self.final_path.name}.ffmpeg.log"
        self.stderr_handle = error_path.open("ab")
        self.process = subprocess.Popen(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                "160x144",
                "-r",
                str(self.fps),
                "-i",
                "pipe:0",
                "-an",
                "-vf",
                "scale=640:576:flags=neighbor",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(self.partial_path),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self.stderr_handle,
            bufsize=0,
        )
        self.frames_written = 0
        self.frames_dropped = 0
        self.started_at = utc_now()
        self.frame_queue = queue.Queue(
            maxsize=max(1, self.fps * RECORDER_QUEUE_SECONDS)
        )
        self.writer_error = None
        self.writer_thread = threading.Thread(
            target=self._writer_loop,
            name="pokemon-ffmpeg-writer",
            daemon=False,
        )
        self.writer_thread.start()
        return self.final_path

    def _writer_loop(self) -> None:
        process = self.process
        if not process or not process.stdin:
            self.writer_error = RuntimeError("ffmpeg writer started without stdin")
            return
        try:
            while True:
                payload = self.frame_queue.get()
                if payload is None:
                    return
                remaining = memoryview(payload)
                while remaining:
                    written = process.stdin.write(remaining)
                    if not written:
                        raise BrokenPipeError("ffmpeg accepted zero frame bytes")
                    remaining = remaining[written:]
        except (BrokenPipeError, OSError) as error:
            self.writer_error = error

    def write(self, image: Any) -> bool:
        if not self.process or not self.process.stdin:
            raise RuntimeError("Recorder has not started")
        if self.writer_error:
            raise RuntimeError("ffmpeg frame writer failed") from self.writer_error
        exit_code = self.process.poll()
        if exit_code is not None:
            raise RuntimeError(f"ffmpeg exited unexpectedly with code {exit_code}")
        try:
            self.frame_queue.put_nowait(image.convert("RGB").tobytes())
        except queue.Full:
            self.frames_dropped += 1
            return False
        self.frames_written += 1
        return True

    def _stop_writer(self, process: subprocess.Popen[bytes]) -> None:
        while True:
            try:
                self.frame_queue.put_nowait(None)
                break
            except queue.Full:
                try:
                    self.frame_queue.get_nowait()
                    self.frames_dropped += 1
                except queue.Empty:
                    continue
        if self.writer_thread:
            self.writer_thread.join(timeout=RECORDER_WRITER_TIMEOUT_SECONDS)
            if self.writer_thread.is_alive():
                process.terminate()
                try:
                    process.wait(timeout=RECORDER_WRITER_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=RECORDER_WRITER_TIMEOUT_SECONDS)
                self.writer_thread.join(timeout=RECORDER_WRITER_TIMEOUT_SECONDS)
            if self.writer_thread.is_alive():
                raise RuntimeError(
                    "ffmpeg writer did not stop after process termination"
                )

    def finish(self) -> Optional[Path]:
        process = self.process
        partial_path = self.partial_path
        final_path = self.final_path
        if not process:
            return None
        self._stop_writer(process)
        if process.stdin and not process.stdin.closed:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        try:
            exit_code = process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                exit_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                exit_code = process.wait(timeout=5)
        if self.stderr_handle:
            self.stderr_handle.close()
        self.process = None
        self.stderr_handle = None
        self.partial_path = None
        self.final_path = None
        self.writer_thread = None
        if exit_code != 0:
            raise RuntimeError(f"ffmpeg failed to finalize clip with code {exit_code}")
        if self.writer_error:
            raise RuntimeError("ffmpeg frame writer failed") from self.writer_error
        if not partial_path or not final_path or self.frames_written == 0:
            if partial_path:
                partial_path.unlink(missing_ok=True)
            return None
        os.replace(partial_path, final_path)
        os.chmod(final_path, 0o600)
        fsync_directory(final_path.parent)
        return final_path


GAME_SYSTEM_PROMPT = f"""You are the autonomous player in Copilot Plays Pokemon Red.
Your long-term goal is to finish Pokemon Red and enter the Hall of Fame.
Each user message includes the current game state and a PNG screenshot.
Never use tools. Return only the requested JSON object.

Make progress deliberately:
- Advance title screens and dialogue with A or Start.
- Prefer Squirtle if choosing a starter, but adapt to existing progress.
- In battle, read the screen before choosing Fight, a move, an item, or Run.
- In the overworld, use the local collision grid and coordinates to avoid loops.
- Never use Dig outside battle while traversing a cave; it returns to the
  entrance and discards traversal progress.
- Follow Rock Tunnel's verified warp sequence from the north entrance:
  1F ladder (37,3) -> B1F ladder (27,3) -> 1F ladder (17,11) ->
  B1F ladder (3,3) -> 1F south exit (15,33) or (15,35).
  B1F (3,33) is not an exit; do not route there.
- Do not issue more than {MAX_BUTTONS_PER_DECISION} buttons. Repeated directions
  are allowed, but avoid long blind walks.
- Set checkpoint true after a badge, major story event, new important location,
  or other moment worth ending the current recording clip.

Valid buttons are: {", ".join(VALID_BUTTONS)}."""


class CopilotBrain:
    def __init__(
        self,
        model: str,
        runtime_dir: Path,
        timeout_seconds: int = 180,
        max_decisions_per_session: int = MAX_DECISIONS_PER_SESSION,
    ):
        self.model = model
        self.runtime_dir = runtime_dir
        self.timeout_seconds = timeout_seconds
        self.max_decisions_per_session = max_decisions_per_session
        if sys.version_info < (3, 11) or importlib.util.find_spec("copilot") is None:
            raise RuntimeError(
                "Copilot Plays Pokemon requires Python 3.11+ and github-copilot-sdk"
            )
        self.backend = "sdk"
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.client: Any = None
        self.session: Any = None
        self.session_decisions = 0
        self.current_task: Optional[asyncio.Task[Any]] = None

    def _run_operation(self, operation: Any, timeout: float) -> Any:
        if not self.loop:
            raise RuntimeError("Copilot SDK event loop is not running")
        task = self.loop.create_task(operation)
        self.current_task = task
        try:
            return self.loop.run_until_complete(asyncio.wait_for(task, timeout=timeout))
        finally:
            self.current_task = None

    def cancel(self) -> None:
        if self.loop and self.current_task and not self.current_task.done():
            self.loop.call_soon_threadsafe(self.current_task.cancel)

    def start(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self._run_operation(
                self._start_sdk(),
                timeout=COPILOT_START_TIMEOUT_SECONDS,
            )
        except (Exception, asyncio.CancelledError) as error:
            try:
                if self.client:
                    self._run_operation(
                        self.client.force_stop(),
                        timeout=COPILOT_STOP_TIMEOUT_SECONDS,
                    )
            except (Exception, asyncio.CancelledError) as cleanup_error:
                LOGGER.error(
                    "Failed to clean up partial Copilot SDK client: %s",
                    cleanup_error,
                )
            self.client = None
            self.session = None
            self.loop.close()
            self.loop = None
            raise RuntimeError(f"Copilot SDK startup failed: {error}") from error

    async def _start_sdk(self) -> None:
        from copilot import CopilotClient

        copilot_home = self.runtime_dir / "copilot"
        copilot_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(copilot_home, 0o700)
        self.client = CopilotClient(
            mode="empty",
            base_directory=str(copilot_home),
            working_directory=str(self.runtime_dir),
            log_level="error",
            session_idle_timeout_seconds=0,
        )
        await self.client.start()
        await self._create_sdk_session()

    async def _create_sdk_session(self) -> None:
        self.session = await self.client.create_session(
            model=self.model,
            reasoning_effort="max",
            system_message={"mode": "replace", "content": GAME_SYSTEM_PROMPT},
            available_tools=[],
            skip_custom_instructions=True,
            enable_session_store=False,
            enable_session_telemetry=False,
            enable_config_discovery=False,
            enable_on_demand_instruction_discovery=False,
            enable_skills=False,
            infinite_sessions={"enabled": False},
            memory={"enabled": False},
        )
        self.session_decisions = 0

    def decide(
        self,
        screenshot: Path,
        game_state: dict[str, Any],
        collision_map: Optional[str],
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        prompt = self._prompt(game_state, collision_map, history)
        if not self.loop or not self.session:
            raise RuntimeError("Copilot SDK session is not running")
        return self._run_operation(
            self._decide_sdk(screenshot, prompt),
            timeout=float(self.timeout_seconds + COPILOT_STOP_TIMEOUT_SECONDS),
        )

    async def _decide_sdk(self, screenshot: Path, prompt: str) -> dict[str, Any]:
        if self.session_decisions >= self.max_decisions_per_session:
            await self.session.disconnect()
            await self._create_sdk_session()

        attachment = {
            "type": "blob",
            "data": base64.b64encode(screenshot.read_bytes()).decode("ascii"),
            "mimeType": "image/png",
        }
        response = await self.session.send_and_wait(
            prompt,
            attachments=[attachment],
            timeout=float(self.timeout_seconds),
        )
        if response is None or not hasattr(response.data, "content"):
            raise RuntimeError("Copilot SDK returned no assistant message")
        self.session_decisions += 1
        return normalize_brain_decision(response.data.content)

    def close(self) -> None:
        if not self.loop:
            return
        cleanup_failed = False
        try:
            if self.session:
                try:
                    self._run_operation(
                        self.session.disconnect(),
                        timeout=COPILOT_STOP_TIMEOUT_SECONDS,
                    )
                except (Exception, asyncio.CancelledError):
                    cleanup_failed = True
                    LOGGER.exception("Copilot session disconnect failed")
            if self.client:
                try:
                    self._run_operation(
                        self.client.stop(),
                        timeout=COPILOT_STOP_TIMEOUT_SECONDS,
                    )
                except (Exception, asyncio.CancelledError):
                    cleanup_failed = True
                    LOGGER.exception("Copilot client stop failed")
                if cleanup_failed:
                    try:
                        self._run_operation(
                            self.client.force_stop(),
                            timeout=COPILOT_STOP_TIMEOUT_SECONDS,
                        )
                    except (Exception, asyncio.CancelledError):
                        LOGGER.exception("Copilot client force-stop failed")
        finally:
            self.session = None
            self.client = None
            if self.current_task and not self.current_task.done():
                self.current_task.cancel()
            self.current_task = None
            if self.loop.is_running():
                self.loop.stop()
            if not self.loop.is_closed():
                self.loop.close()
            self.loop = None

    def recover(self) -> None:
        self.close()
        self.start()

    @staticmethod
    def _prompt(
        game_state: dict[str, Any],
        collision_map: Optional[str],
        history: list[dict[str, Any]],
    ) -> str:
        history_json = json.dumps(history[-8:], separators=(",", ":"))
        state_json = json.dumps(game_state, separators=(",", ":"))
        collision = collision_map or "Unavailable; rely on the screenshot."
        return f"""The attached image is the current 160x144 game screen.

Pokemon RAM snapshot:
{state_json}

Local collision grid (# blocked, . walkable, P player at center):
{collision}

Recent decisions:
{history_json}

Return only one JSON object with exactly this shape:
{{"phase":"intro|menu|dialogue|overworld|battle|other",
"observation":"what is visibly happening",
"objective":"the immediate goal and relevant longer-term plan",
"reason":"brief reason for this input sequence",
"buttons":["a","up"],
"checkpoint":false}}"""


class ActionPlayer:
    def __init__(self):
        self.pending: deque[str] = deque()
        self.current: Optional[str] = None
        self.hold_frames = 0
        self.gap_frames = 0

    @property
    def idle(self) -> bool:
        return not self.pending and self.current is None and self.gap_frames == 0

    def replace(self, buttons: list[str]) -> None:
        self.pending.clear()
        self.pending.extend(button for button in buttons if button in VALID_BUTTONS)

    def append(self, button: str) -> None:
        if button in VALID_BUTTONS:
            self.pending.append(button)

    def tick(self, pyboy: Any) -> Optional[str]:
        if self.current:
            self.hold_frames -= 1
            if self.hold_frames <= 0:
                pyboy.button_release(self.current)
                completed = self.current
                self.current = None
                self.gap_frames = 18
                return completed
            return None
        if self.gap_frames > 0:
            self.gap_frames -= 1
            return None
        if self.pending:
            self.current = self.pending.popleft()
            pyboy.button_press(self.current)
            self.hold_frames = 8
        return None

    def release(self, pyboy: Any) -> None:
        if self.current:
            pyboy.button_release(self.current)
            self.current = None
        self.pending.clear()
        self.gap_frames = 0

    def release_and_flush(self, pyboy: Any, require_running: bool = True) -> None:
        self.pending.clear()
        self.current = None
        self.gap_frames = 0
        for button in VALID_BUTTONS:
            pyboy.button_release(button)
        if not pyboy.tick() and require_running:
            raise RuntimeError("PyBoy stopped while normalizing controller input")


class ViewerServer:
    def __init__(
        self,
        runtime_dir: Path,
        port: int,
        controls: "queue.Queue[dict[str, Any]]",
    ):
        self.runtime_dir = runtime_dir
        self.port = port
        self.controls = controls
        self.token = secrets.token_urlsafe(32)
        self.server: Optional[http.server.ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        runtime_dir = self.runtime_dir
        controls = self.controls
        token = self.token

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format_string: str, *args: Any) -> None:
                LOGGER.debug("viewer: " + format_string, *args)

            def _security_headers(self) -> None:
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; img-src 'self' data:; "
                    "media-src 'self'; style-src 'self'; script-src 'self'; "
                    "connect-src 'self'; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'none'",
                )
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")

            def _allowed_origins(self) -> set[str]:
                port = self.server.server_address[1]
                return {
                    f"http://127.0.0.1:{port}",
                    f"http://localhost:{port}",
                }

            def _host_allowed(self) -> bool:
                port = self.server.server_address[1]
                return self.headers.get("Host", "") in {
                    f"127.0.0.1:{port}",
                    f"localhost:{port}",
                }

            def _authenticated(self) -> bool:
                if not self._host_allowed():
                    return False
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get("Cookie", ""))
                except ValueError:
                    return False
                supplied = cookie.get("rapp_play_pokemon")
                return bool(supplied and secrets.compare_digest(supplied.value, token))

            def _forbidden(self) -> None:
                payload = b'{"status":"error","message":"forbidden"}'
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self._security_headers()
                self.end_headers()
                self.wfile.write(payload)

            def _json(self, status_code: int, value: dict[str, Any]) -> None:
                payload = json.dumps(value).encode("utf-8")
                self._bytes(status_code, payload, "application/json")

            def _bytes(
                self,
                status_code: int,
                payload: bytes,
                content_type: str,
            ) -> None:
                self.send_response(status_code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self._security_headers()
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/":
                    if not self._host_allowed():
                        self._forbidden()
                        return
                    if not self._authenticated():
                        supplied = urllib.parse.parse_qs(
                            parsed.query,
                            keep_blank_values=True,
                        ).get("token", [""])[0]
                        if not secrets.compare_digest(supplied, token):
                            self._forbidden()
                            return
                        self.send_response(303)
                        self.send_header("Location", "/")
                        self.send_header("Content-Length", "0")
                        self.send_header(
                            "Set-Cookie",
                            "rapp_play_pokemon="
                            f"{token}; Path=/; HttpOnly; SameSite=Strict",
                        )
                        self.send_header("Cache-Control", "no-store")
                        self._security_headers()
                        self.end_headers()
                        return
                    self._bytes(
                        200,
                        VIEWER_HTML.encode("utf-8"),
                        "text/html; charset=utf-8",
                    )
                    return
                if not self._authenticated():
                    self._forbidden()
                    return
                if parsed.path == "/viewer.css":
                    self._bytes(
                        200,
                        VIEWER_CSS.encode("utf-8"),
                        "text/css; charset=utf-8",
                    )
                    return
                if parsed.path == "/viewer.js":
                    self._bytes(
                        200,
                        VIEWER_JS.encode("utf-8"),
                        "text/javascript; charset=utf-8",
                    )
                    return
                if parsed.path == "/api/status":
                    self._json(200, public_runtime_status(runtime_dir))
                    return
                if parsed.path == "/frame.png":
                    self._send_file(runtime_dir / "latest.png", "image/png")
                    return
                if parsed.path.startswith("/clips/"):
                    name = urllib.parse.unquote(parsed.path.removeprefix("/clips/"))
                    if Path(name).name != name or not GENERATED_CLIP_RE.fullmatch(name):
                        self.send_error(400)
                        return
                    self._send_file(runtime_dir / "clips" / name, "video/mp4")
                    return
                self.send_error(404)

            def _send_file(self, path: Path, content_type: str) -> None:
                try:
                    if path.is_symlink():
                        raise OSError("symlinks are not served")
                    size = path.stat().st_size
                except OSError:
                    self.send_error(404)
                    return
                start = 0
                end = size - 1
                range_header = self.headers.get("Range")
                if range_header:
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
                    if not match or not any(match.groups()):
                        self.send_error(416)
                        return
                    if match.group(1):
                        start = int(match.group(1))
                    if match.group(2):
                        end = min(end, int(match.group(2)))
                if start < 0 or end < start or start >= size:
                    self.send_error(416)
                    return
                length = end - start + 1
                self.send_response(206 if range_header else 200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                if range_header:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Cache-Control", "no-store")
                self._security_headers()
                self.end_headers()
                try:
                    with path.open("rb") as handle:
                        handle.seek(start)
                        remaining = length
                        while remaining:
                            chunk = handle.read(min(64 * 1024, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return

            def do_POST(self) -> None:
                if urllib.parse.urlparse(self.path).path != "/api/control":
                    self.send_error(404)
                    return
                if not self._authenticated():
                    self._forbidden()
                    return
                if self.headers.get("Origin") not in self._allowed_origins():
                    self._forbidden()
                    return
                content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
                if content_type.strip().lower() != "application/json":
                    self._forbidden()
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= length <= 4096:
                        raise ValueError("invalid request length")
                    value = json.loads(self.rfile.read(length).decode("utf-8"))
                except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
                    self._json(400, {"status": "error", "message": "Invalid JSON"})
                    return
                if not isinstance(value, dict):
                    self._json(400, {"status": "error", "message": "Expected object"})
                    return
                action = str(value.get("action", "")).lower()
                button = str(value.get("button", "")).lower()
                if action not in {
                    "manual",
                    "autonomy",
                    "pause",
                    "resume",
                    "checkpoint",
                    "stop",
                    "press",
                }:
                    self._json(400, {"status": "error", "message": "Invalid action"})
                    return
                if action == "press" and button not in VALID_BUTTONS:
                    self._json(400, {"status": "error", "message": "Invalid button"})
                    return
                if action == "stop":
                    set_desired_running(runtime_dir, False)
                controls.put({"action": action, "button": button or None})
                self._json(200, {"status": "success", "action": action})

        class LoopbackServer(http.server.ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = LoopbackServer(("127.0.0.1", self.port), Handler)
        self.port = int(self.server.server_address[1])
        atomic_write_json(
            runtime_dir / "viewer-auth.json",
            {
                "token": token,
                "port": self.port,
                "created_at": utc_now(),
            },
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="pokemon-viewer",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread:
            self.thread.join(timeout=3)
            self.thread = None
        (self.runtime_dir / "viewer-auth.json").unlink(missing_ok=True)


class PokemonRunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.run_id = uuid.uuid4().hex[:12]
        self.rom = Path(args.rom).expanduser().resolve()
        self.runtime_dir = Path(args.runtime_dir).expanduser().resolve()
        self.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.runtime_dir, 0o700)
        self.states_dir = self.runtime_dir / "states"
        self.screens_dir = self.runtime_dir / "screens"
        self.states_dir.mkdir(exist_ok=True, mode=0o700)
        self.screens_dir.mkdir(exist_ok=True, mode=0o700)
        os.chmod(self.states_dir, 0o700)
        os.chmod(self.screens_dir, 0o700)
        self.status_path = self.runtime_dir / "status.json"
        self.control_path = self.runtime_dir / "control.jsonl"
        self.control_path.write_text("", encoding="utf-8")
        os.chmod(self.control_path, 0o600)
        self.controls: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self.brain_requests: "queue.Queue[Optional[dict[str, Any]]]" = queue.Queue(
            maxsize=1
        )
        self.brain_results: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self.stop_event = threading.Event()
        self.brain_ready = threading.Event()
        self.brain_available = threading.Event()
        self.brain: Optional[CopilotBrain] = None
        self.brain_thread: Optional[threading.Thread] = None
        self.paused = False
        self.emulator_pause_requested = False
        self.control_mode = "ai"
        self.resume_mode = "ai"
        self.control_generation = 0
        self.decision_sequence = 0
        self.pending_decision_id: Optional[int] = None
        self.player = ActionPlayer()
        brain_data = read_json(self.runtime_dir / "brain.json", {"history": []})
        self.history: list[dict[str, Any]] = brain_data.get("history", [])
        if not isinstance(self.history, list):
            self.history = []
        self.total_decisions = int(brain_data.get("total_decisions", len(self.history)))
        self.rom_sha256 = self._rom_sha256()
        self.ram_path = (
            self.runtime_dir
            / f"pokemon-red-{self.rom_sha256[:16]}.ram"
        )
        self.status: dict[str, Any] = {
            "running": True,
            "pid": os.getpid(),
            "instance_id": args.instance_id,
            "port": args.port,
            "lifecycle": "initializing",
            "started_at": utc_now(),
            "updated_at": utc_now(),
            "rom_title": rom_title(self.rom),
            "rom_sha256": self.rom_sha256,
            "rom_path": str(self.rom),
            "runtime_dir": str(self.runtime_dir),
            "paused": False,
            "control_mode": "ai",
            "brain_status": "starting",
            "model": args.model,
            "model_calls": 0,
            "actions_taken": 0,
            "observation": "",
            "phase": "other",
            "reason": "",
            "objective": "Start Pokemon Red and work toward the Hall of Fame",
            "last_action": [],
            "last_error": None,
            "game_state": {},
            "current_clip": None,
            "last_checkpoint": None,
            "completed": False,
            "clips": [],
        }
        self.pyboy: Any = None
        self.recorder = ClipRecorder(self.runtime_dir)
        self.viewer = ViewerServer(self.runtime_dir, args.port, self.controls)
        self.last_badges = 0
        self.last_decision_requested = 0.0
        self.last_decision_finished = 0.0
        self.decision_pending = False
        self.clip_started = 0.0
        self.next_record_at = 0.0
        self.frames = 0
        self.control_offset = 0
        self.max_clips = max(1, int(args.max_clips))
        self.max_states = max(2, int(args.max_states))
        self.max_storage_bytes = max(
            1,
            int(float(args.max_storage_gb) * 1024**3),
        )
        self.min_free_bytes = max(
            0,
            int(float(args.min_free_gb) * 1024**3),
        )
        self.status.update(
            {
                "storage_max_bytes": self.max_storage_bytes,
                "storage_reserve_bytes": self.min_free_bytes,
            }
        )

    def _rom_sha256(self) -> str:
        digest = hashlib.sha256()
        with self.rom.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _write_status(self) -> None:
        self.status.update(
            {
                "updated_at": utc_now(),
                "heartbeat_at": utc_now(),
                "running": not self.stop_event.is_set(),
                "paused": self.paused,
                "control_mode": self.control_mode,
                "clips": list_clips(self.runtime_dir),
            }
        )
        atomic_write_json(self.status_path, self.status)

    @staticmethod
    def _milestone_artifact(path: Path) -> bool:
        manifest = read_json(path.with_suffix(".json"))
        reason = str(manifest.get("reason", "")).lower()
        return any(
            marker in reason
            for marker in ("badge", "hall of fame", "champion", "elite four")
        )

    @staticmethod
    def _verified_generated_clip(path: Path) -> bool:
        if not GENERATED_CLIP_RE.fullmatch(path.name):
            return False
        manifest = read_json(path.with_suffix(".json"))
        return bool(
            manifest.get("schema_version") == 1
            and manifest.get("name") == path.name
            and manifest.get("sha256")
        )

    def _verified_generated_state(self, path: Path) -> bool:
        if not GENERATED_STATE_RE.fullmatch(path.name):
            return False
        manifest = read_json(path.with_suffix(".json"))
        metadata_valid = bool(
            manifest.get("schema_version") == 1
            and manifest.get("sha256")
            and manifest.get("rom_sha256") == self.status.get("rom_sha256")
        )
        if not metadata_valid:
            return False
        try:
            return file_sha256(path) == manifest["sha256"]
        except OSError:
            return False

    def _recover_orphaned_states(self) -> None:
        for state in self.states_dir.glob("state-*.state"):
            if not GENERATED_STATE_RE.fullmatch(state.name):
                continue
            manifest_path = state.with_suffix(".json")
            if manifest_path.exists():
                state.with_suffix(".pending.json").unlink(missing_ok=True)
                manifest = read_json(manifest_path)
                if (
                    manifest.get("schema_version") == 1
                    and manifest.get("rom_sha256")
                    == self.status.get("rom_sha256")
                    and manifest.get("sha256")
                ):
                    try:
                        valid_hash = file_sha256(state) == manifest["sha256"]
                    except OSError:
                        valid_hash = False
                    if not valid_hash:
                        self._quarantine_state(
                            state,
                            state.with_suffix(".pending.json"),
                            "Checkpoint checksum validation failed",
                        )
                continue
            pending_path = state.with_suffix(".pending.json")
            pending = read_json(pending_path)
            recoverable = bool(
                pending.get("schema_version") == 1
                and pending.get("state_name") == state.name
                and pending.get("rom_sha256") == self.status.get("rom_sha256")
                and pending.get("created_at")
            )
            if not recoverable:
                self._quarantine_state(
                    state,
                    pending_path,
                    "Checkpoint provenance is missing or belongs to another ROM",
                )
                continue
            try:
                metadata = state.stat()
                atomic_write_json(
                    manifest_path,
                    {
                        "schema_version": 1,
                        "created_at": pending["created_at"],
                        "reason": pending.get(
                            "reason",
                            "recovered after interrupted checkpoint",
                        ),
                        "rom_sha256": pending["rom_sha256"],
                        "sha256": file_sha256(state),
                        "bytes": metadata.st_size,
                        "game_state": {},
                        "recovered": True,
                    },
                )
                pending_path.unlink(missing_ok=True)
            except OSError as error:
                LOGGER.warning("Could not recover interrupted checkpoint: %s", error)

    def _quarantine_state(
        self,
        state: Path,
        pending_path: Path,
        reason: str,
    ) -> None:
        quarantine_dir = self.runtime_dir / "quarantine"
        quarantine_dir.mkdir(exist_ok=True, mode=0o700)
        os.chmod(quarantine_dir, 0o700)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = quarantine_dir / f"{state.name}.{timestamp}.orphan"
        os.replace(state, destination)
        if pending_path.exists():
            os.replace(
                pending_path,
                quarantine_dir / f"{pending_path.name}.{timestamp}.orphan",
            )
        atomic_write_json(
            quarantine_dir / f"{destination.name}.reason.json",
            {"reason": reason, "quarantined_at": utc_now()},
        )

    def _remove_generated_clip(self, clip: Path) -> None:
        clip.unlink(missing_ok=True)
        clip.with_suffix(".json").unlink(missing_ok=True)
        (clip.parent / f".{clip.name}.ffmpeg.log").unlink(missing_ok=True)

    def _remove_generated_state(self, state: Path) -> None:
        state.unlink(missing_ok=True)
        state.with_suffix(".json").unlink(missing_ok=True)

    def _generated_artifact_bytes(self) -> int:
        total = 0
        generated_paths: list[Path] = []
        for clip in self.recorder.clips_dir.glob("clip-*.mp4"):
            if self._verified_generated_clip(clip):
                generated_paths.extend([clip, clip.with_suffix(".json")])
                generated_paths.append(clip.parent / f".{clip.name}.ffmpeg.log")
        for state in self.states_dir.glob("state-*.state"):
            if self._verified_generated_state(state):
                generated_paths.extend([state, state.with_suffix(".json")])
        for partial in self.recorder.clips_dir.glob(".clip-*.partial.mp4"):
            if GENERATED_PARTIAL_RE.fullmatch(partial.name):
                generated_paths.append(partial)
        generated_paths.extend(self.screens_dir.glob("decision-*.png"))
        for path in generated_paths:
            try:
                if path.is_file():
                    total += path.stat().st_size
            except FileNotFoundError:
                continue
        return total

    def _prune_stale_partials(self) -> None:
        now = time.time()
        current = self.recorder.partial_path
        for partial in self.recorder.clips_dir.glob(".clip-*.partial.mp4"):
            if partial == current or not GENERATED_PARTIAL_RE.fullmatch(partial.name):
                continue
            try:
                if now - partial.stat().st_mtime < 3600:
                    continue
            except FileNotFoundError:
                continue
            partial.unlink(missing_ok=True)
            log_name = partial.name.removeprefix(".").removesuffix(".partial.mp4")
            (partial.parent / f".{log_name}.ffmpeg.log").unlink(missing_ok=True)

    def _enforce_retention(self) -> None:
        self._prune_stale_partials()
        clips = sorted(
            (
                path
                for path in self.recorder.clips_dir.glob("clip-*.mp4")
                if self._verified_generated_clip(path)
            ),
            key=lambda path: path.stat().st_mtime_ns,
        )
        states = sorted(
            (
                path
                for path in self.states_dir.glob("state-*.state")
                if self._verified_generated_state(path)
            ),
            key=lambda path: path.stat().st_mtime_ns,
        )

        while len(clips) > self.max_clips:
            removable = next(
                (clip for clip in clips if not self._milestone_artifact(clip)),
                None,
            )
            if removable is None:
                break
            self._remove_generated_clip(removable)
            clips.remove(removable)

        newest_state = states[-1] if states else None
        while len(states) > self.max_states:
            removable = next(
                (
                    state
                    for state in states
                    if state != newest_state and not self._milestone_artifact(state)
                ),
                None,
            )
            if removable is None:
                break
            self._remove_generated_state(removable)
            states.remove(removable)

        artifact_bytes = self._generated_artifact_bytes()
        free_bytes = shutil.disk_usage(self.runtime_dir).free
        while (
            artifact_bytes > self.max_storage_bytes or free_bytes < self.min_free_bytes
        ):
            candidates = [
                path
                for path in [*clips, *states]
                if path != newest_state and not self._milestone_artifact(path)
            ]
            if not candidates:
                break
            oldest = min(candidates, key=lambda path: path.stat().st_mtime_ns)
            if oldest.suffix == ".mp4":
                self._remove_generated_clip(oldest)
                clips.remove(oldest)
            else:
                self._remove_generated_state(oldest)
                states.remove(oldest)
            artifact_bytes = self._generated_artifact_bytes()
            free_bytes = shutil.disk_usage(self.runtime_dir).free

        self.status.update(
            {
                "storage_artifact_bytes": artifact_bytes,
                "storage_free_bytes": free_bytes,
                "retained_clips": len(clips),
                "retained_states": len(states),
                "recording_suspended": (
                    artifact_bytes > self.max_storage_bytes
                    or free_bytes < self.min_free_bytes
                ),
            }
        )

    def _brain_loop(self) -> None:
        try:
            brain = CopilotBrain(
                self.args.model,
                self.runtime_dir,
                self.args.decision_timeout,
            )
            self.brain = brain
            brain.start()
        except RuntimeError as error:
            self.status["brain_status"] = "error"
            self.status["last_error"] = str(error)
            self.brain_ready.set()
            return
        self.status["brain_backend"] = brain.backend
        self.status["brain_status"] = "idle"
        self.brain_available.set()
        self.brain_ready.set()
        try:
            while not self.stop_event.is_set():
                request = self.brain_requests.get()
                if request is None:
                    break
                try:
                    decision = brain.decide(
                        screenshot=Path(request["screenshot"]),
                        game_state=request["game_state"],
                        collision_map=request.get("collision_map"),
                        history=request["history"],
                    )
                    self.brain_results.put(
                        {
                            "decision": decision,
                            "decision_id": request["decision_id"],
                            "generation": request["generation"],
                        }
                    )
                except ValueError as error:
                    self.brain_results.put(
                        {
                            "error": str(error),
                            "decision_id": request["decision_id"],
                            "generation": request["generation"],
                        }
                    )
                except Exception as error:
                    # The SDK emits base Exception for session.error notifications.
                    LOGGER.exception("Copilot worker failure")
                    self.status["brain_failure_count"] = (
                        int(self.status.get("brain_failure_count", 0)) + 1
                    )
                    self.brain_results.put(
                        {
                            "error": str(error),
                            "decision_id": request["decision_id"],
                            "generation": request["generation"],
                        }
                    )
                    self.brain_available.clear()
                    self.status["brain_status"] = "recovering"
                    try:
                        brain.recover()
                        self.status["brain_backend"] = brain.backend
                        self.status["brain_status"] = "idle"
                        self.brain_available.set()
                    except Exception as recovery_error:
                        LOGGER.exception("Copilot worker recovery failed")
                        self.status["brain_status"] = "failed"
                        self.status["last_error"] = (
                            f"Copilot recovery failed: {recovery_error}"
                        )
                        self.status["lifecycle"] = "failed"
                        self.stop_event.set()
                        break
        finally:
            try:
                brain.close()
            except Exception:
                LOGGER.exception("Copilot worker shutdown failed")
            self.brain = None

    def _stop_brain_worker(self) -> None:
        self.stop_event.set()
        if self.brain:
            self.brain.cancel()
        try:
            self.brain_requests.put_nowait(None)
        except queue.Full:
            try:
                self.brain_requests.get_nowait()
            except queue.Empty:
                pass
            self.brain_requests.put_nowait(None)
        if not self.brain_thread:
            return
        self.brain_thread.join(timeout=COPILOT_THREAD_SHUTDOWN_TIMEOUT_SECONDS)
        if self.brain_thread.is_alive():
            self.status["last_error"] = (
                "Copilot worker exceeded its bounded shutdown window"
            )
            self.status["lifecycle"] = "failed"
            LOGGER.error("%s", self.status["last_error"])
            try:
                self._write_status()
            except OSError:
                LOGGER.exception("Failed to publish Copilot shutdown failure")
            self.brain_thread.join()

    def _load_latest_state(self) -> Optional[Path]:
        if not self.args.resume:
            return None
        state_errors: tuple[type[BaseException], ...] = (
            OSError,
            EOFError,
            RuntimeError,
            ValueError,
        )
        try:
            from pyboy.utils import PyBoyException
        except ImportError:
            pass
        else:
            state_errors += (PyBoyException,)

        self._recover_orphaned_states()

        def checkpoint_timestamp(path: Path) -> float:
            created_at = read_json(path.with_suffix(".json")).get("created_at")
            if created_at:
                try:
                    parsed = datetime.fromisoformat(str(created_at))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    return parsed.timestamp()
                except ValueError:
                    pass
            return path.stat().st_mtime

        states = sorted(
            (
                path
                for path in self.states_dir.glob("*.state")
                if self._verified_generated_state(path)
            ),
            key=checkpoint_timestamp,
            reverse=True,
        )
        if not states:
            return None
        baseline = io.BytesIO()
        self.pyboy.save_state(baseline)
        baseline_bytes = baseline.getvalue()
        for state_path in states:
            manifest = read_json(state_path.with_suffix(".json"))
            if manifest.get("rom_sha256") not in (None, self.status["rom_sha256"]):
                LOGGER.warning("Skipping checkpoint for another ROM: %s", state_path)
                continue
            expected_hash = manifest.get("sha256")
            try:
                if expected_hash and file_sha256(state_path) != expected_hash:
                    raise ValueError("checkpoint hash mismatch")
                with state_path.open("rb") as handle:
                    self.pyboy.load_state(handle)
            except state_errors as error:
                LOGGER.warning("Skipping invalid checkpoint %s: %s", state_path, error)
                try:
                    self._quarantine_state(
                        state_path,
                        state_path.with_suffix(".pending.json"),
                        f"Checkpoint validation failed: {type(error).__name__}",
                    )
                except OSError:
                    LOGGER.exception("Could not quarantine invalid checkpoint")
                self.pyboy.load_state(io.BytesIO(baseline_bytes))
                continue
            self.player.release_and_flush(self.pyboy)
            return state_path
        return None

    def _save_checkpoint(self, reason: str, allow_stopped: bool = False) -> Path:
        self.player.release_and_flush(
            self.pyboy,
            require_running=not allow_stopped,
        )
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        state_path = self.states_dir / f"state-{timestamp}.state"
        temporary = self.states_dir / f".{state_path.name}.tmp"
        created_at = utc_now()
        pending_path = state_path.with_suffix(".pending.json")
        atomic_write_json(
            pending_path,
            {
                "schema_version": 1,
                "state_name": state_path.name,
                "created_at": created_at,
                "reason": reason,
                "rom_sha256": self.status["rom_sha256"],
            },
        )
        try:
            with temporary.open("wb") as handle:
                self.pyboy.save_state(handle)
                handle.flush()
                os.fsync(handle.fileno())
            if temporary.stat().st_size == 0:
                raise RuntimeError("PyBoy produced an empty checkpoint")
            os.replace(temporary, state_path)
            os.chmod(state_path, 0o600)
            fsync_directory(self.states_dir)
        except Exception:
            temporary.unlink(missing_ok=True)
            if not state_path.exists():
                pending_path.unlink(missing_ok=True)
            raise
        game_state = PokemonMemoryReader(self.pyboy.memory).snapshot()
        manifest = {
            "schema_version": 1,
            "created_at": created_at,
            "reason": reason,
            "rom_sha256": self.status["rom_sha256"],
            "sha256": file_sha256(state_path),
            "bytes": state_path.stat().st_size,
            "game_state": game_state,
        }
        atomic_write_json(state_path.with_suffix(".json"), manifest)
        pending_path.unlink(missing_ok=True)
        self.status["last_checkpoint"] = {
            "path": str(state_path),
            "reason": reason,
            "timestamp": manifest["created_at"],
            "sha256": manifest["sha256"],
        }
        return state_path

    def _write_clip_manifest(
        self,
        clip: Path,
        reason: str,
        frames_written: int,
        started_at: Optional[str],
    ) -> None:
        game_state = (
            self.status.get("game_state")
            if isinstance(self.status.get("game_state"), dict)
            else {}
        )
        manifest = {
            "schema_version": 1,
            "name": clip.name,
            "started_at": started_at,
            "completed_at": utc_now(),
            "reason": reason,
            "frames": frames_written,
            "fps": self.recorder.fps,
            "duration_seconds": round(frames_written / self.recorder.fps, 3),
            "bytes": clip.stat().st_size,
            "sha256": file_sha256(clip),
            "game_state": game_state,
        }
        atomic_write_json(clip.with_suffix(".json"), manifest)

    def _rotate_clip(self, reason: str) -> None:
        frames_written = self.recorder.frames_written
        started_at = self.recorder.started_at
        self._save_checkpoint(reason)
        finished = self.recorder.finish()
        if finished:
            self._write_clip_manifest(finished, reason, frames_written, started_at)
        self.status["last_completed_clip"] = str(finished) if finished else None
        next_clip = self.recorder.start()
        self.status["current_clip"] = str(next_clip)
        self.clip_started = time.monotonic()
        self.next_record_at = self.clip_started
        try:
            self._enforce_retention()
        except OSError as error:
            self.status["last_error"] = f"Artifact retention failed: {error}"
            LOGGER.exception("Artifact retention failed")
        self._write_status()

    def _record_due_frames(self, image: Any) -> None:
        now = time.monotonic()
        interval = 1 / self.recorder.fps
        if self.status.get("recording_suspended"):
            self.next_record_at = now + interval
            return
        if self.next_record_at <= 0:
            self.next_record_at = now
        if now < self.next_record_at:
            return
        due = int((now - self.next_record_at) / interval) + 1
        max_catchup = self.recorder.fps * 2
        write_count = min(due, max_catchup)
        for _ in range(write_count):
            if not self.recorder.write(image):
                self.status["recording_frames_dropped"] = (
                    int(self.status.get("recording_frames_dropped", 0)) + 1
                )
        if due > max_catchup:
            self.status["recording_frames_skipped"] = (
                int(self.status.get("recording_frames_skipped", 0)) + due - max_catchup
            )
            self.next_record_at = now + interval
        else:
            self.next_record_at += due * interval

    def _ram_pair_valid(self, ram_path: Path, manifest_path: Path) -> bool:
        if not ram_path.exists() or not manifest_path.exists():
            return False
        manifest = read_json(manifest_path)
        if not (
            manifest.get("schema_version") == 1
            and manifest.get("rom_sha256") == self.rom_sha256
            and manifest.get("sha256")
        ):
            return False
        try:
            return file_sha256(ram_path) == manifest["sha256"]
        except OSError:
            return False

    def _ram_backup_paths(self) -> tuple[Path, Path]:
        return (
            self.ram_path.with_name(f".{self.ram_path.name}.backup"),
            self.ram_path.with_name(
                f".{self.ram_path.with_suffix('.json').name}.backup"
            ),
        )

    def _ram_pending_path(self) -> Path:
        return self.ram_path.with_name(f".{self.ram_path.name}.pending.json")

    def _quarantine_ram_pair(
        self,
        ram_path: Path,
        manifest_path: Path,
        reason: str,
    ) -> None:
        if not ram_path.exists() and not manifest_path.exists():
            return
        quarantine_dir = self.runtime_dir / "quarantine"
        quarantine_dir.mkdir(exist_ok=True, mode=0o700)
        os.chmod(quarantine_dir, 0o700)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = quarantine_dir / f"{ram_path.name}.{timestamp}.invalid"
        if ram_path.exists():
            os.replace(ram_path, destination)
        if manifest_path.exists():
            os.replace(
                manifest_path,
                quarantine_dir / f"{manifest_path.name}.{timestamp}.invalid",
            )
        atomic_write_json(
            quarantine_dir / f"{destination.name}.reason.json",
            {"reason": reason, "quarantined_at": utc_now()},
        )
        self.status["ram_warning"] = reason

    def _quarantine_ram(self, reason: str) -> None:
        self._quarantine_ram_pair(
            self.ram_path,
            self.ram_path.with_suffix(".json"),
            reason,
        )

    def _recover_ram_transaction(self) -> None:
        backup_ram, backup_manifest = self._ram_backup_paths()
        current_manifest = self.ram_path.with_suffix(".json")
        pending_path = self._ram_pending_path()
        temporary = self.runtime_dir / f".{self.ram_path.name}.tmp"

        def clear_transaction_files() -> None:
            pending_path.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)

        if self._ram_pair_valid(self.ram_path, current_manifest):
            backup_ram.unlink(missing_ok=True)
            backup_manifest.unlink(missing_ok=True)
            clear_transaction_files()
            fsync_directory(self.runtime_dir)
            return
        if self._ram_pair_valid(self.ram_path, pending_path):
            atomic_write_json(current_manifest, read_json(pending_path))
            backup_ram.unlink(missing_ok=True)
            backup_manifest.unlink(missing_ok=True)
            clear_transaction_files()
            fsync_directory(self.runtime_dir)
            return
        if self._ram_pair_valid(temporary, pending_path):
            self._quarantine_ram("Discarded incomplete RAM transaction")
            os.replace(temporary, self.ram_path)
            os.chmod(self.ram_path, 0o600)
            fsync_directory(self.runtime_dir)
            atomic_write_json(current_manifest, read_json(pending_path))
            backup_ram.unlink(missing_ok=True)
            backup_manifest.unlink(missing_ok=True)
            clear_transaction_files()
            fsync_directory(self.runtime_dir)
            return
        if self._ram_pair_valid(backup_ram, current_manifest):
            if self.ram_path.exists():
                self._quarantine_ram_pair(
                    self.ram_path,
                    self.runtime_dir / ".no-ram-manifest",
                    "Discarded incomplete RAM transaction",
                )
            os.replace(backup_ram, self.ram_path)
            backup_manifest.unlink(missing_ok=True)
            clear_transaction_files()
            fsync_directory(self.runtime_dir)
            return
        if self._ram_pair_valid(self.ram_path, backup_manifest):
            current_manifest.unlink(missing_ok=True)
            os.replace(backup_manifest, current_manifest)
            self._quarantine_ram_pair(
                backup_ram,
                self.runtime_dir / ".no-ram-manifest",
                "Preserved incomplete RAM transaction data",
            )
            clear_transaction_files()
            fsync_directory(self.runtime_dir)
            return
        if self._ram_pair_valid(backup_ram, backup_manifest):
            self._quarantine_ram("Recovered previous RAM after interrupted commit")
            os.replace(backup_ram, self.ram_path)
            os.replace(backup_manifest, current_manifest)
            clear_transaction_files()
            fsync_directory(self.runtime_dir)
            return
        if backup_ram.exists() or backup_manifest.exists():
            self._quarantine_ram_pair(
                backup_ram,
                backup_manifest,
                "Invalid interrupted RAM backup",
            )
        if self.ram_path.exists() or current_manifest.exists():
            self._quarantine_ram("Ignored incomplete RAM transaction")
        clear_transaction_files()
        fsync_directory(self.runtime_dir)

    def _adopt_legacy_ram(self) -> None:
        if self.ram_path.exists():
            return
        legacy = self.runtime_dir / "pokemon-red.ram"
        if not legacy.exists():
            return
        legacy_manifest_path = legacy.with_suffix(".json")
        legacy_manifest = read_json(legacy_manifest_path)
        try:
            manifest_matches = bool(
                legacy_manifest.get("rom_sha256") == self.rom_sha256
                and legacy_manifest.get("sha256")
                and file_sha256(legacy) == legacy_manifest["sha256"]
            )
        except OSError:
            manifest_matches = False
        provenance = read_json(
            self.runtime_dir / "legacy-ram-provenance.json"
        )
        previous_hash = provenance.get("rom_sha256")
        format_matches = bool(
            not legacy_manifest_path.exists()
            and previous_hash == self.rom_sha256
            and self.status.get("rom_title", "").upper().startswith("POKEMON RED")
            and legacy.stat().st_size == 32768
        )
        if not (manifest_matches or format_matches):
            self.status["ram_warning"] = (
                "Preserved legacy cartridge RAM because ROM provenance did not match"
            )
            return
        manifest = {
            "schema_version": 1,
            "created_at": utc_now(),
            "rom_sha256": self.rom_sha256,
            "sha256": file_sha256(legacy),
            "bytes": legacy.stat().st_size,
            "migrated_from": legacy.name,
        }
        pending_path = self._ram_pending_path()
        try:
            atomic_write_json(pending_path, manifest)
            os.replace(legacy, self.ram_path)
            os.chmod(self.ram_path, 0o600)
            fsync_directory(self.runtime_dir)
            atomic_write_json(self.ram_path.with_suffix(".json"), manifest)
            legacy_manifest_path.unlink(missing_ok=True)
            pending_path.unlink(missing_ok=True)
            fsync_directory(self.runtime_dir)
        except Exception:
            if self.ram_path.exists() and not legacy.exists():
                os.replace(self.ram_path, legacy)
            self.ram_path.with_suffix(".json").unlink(missing_ok=True)
            pending_path.unlink(missing_ok=True)
            fsync_directory(self.runtime_dir)
            raise

    def _validated_ram_path(self) -> Optional[Path]:
        self._recover_ram_transaction()
        if not any(path.exists() for path in self._ram_backup_paths()):
            self._adopt_legacy_ram()
        if not self.ram_path.exists():
            return None
        if not self._ram_pair_valid(
            self.ram_path,
            self.ram_path.with_suffix(".json"),
        ):
            self._quarantine_ram("Ignored invalid or unverified cartridge RAM")
            return None
        return self.ram_path

    def _save_ram_and_stop(self) -> None:
        temporary = self.runtime_dir / f".{self.ram_path.name}.tmp"
        manifest_path = self.ram_path.with_suffix(".json")
        backup_ram, backup_manifest = self._ram_backup_paths()
        pending_path = self._ram_pending_path()
        self._recover_ram_transaction()
        backed_up = self._ram_pair_valid(self.ram_path, manifest_path)
        backup_started = False
        backup_installed = False
        try:
            with temporary.open("w+b") as ram_output:
                self.pyboy.stop(save=True, ram_file=ram_output)
                ram_output.flush()
                os.fsync(ram_output.fileno())
            manifest = {
                "schema_version": 1,
                "created_at": utc_now(),
                "rom_sha256": self.rom_sha256,
                "sha256": file_sha256(temporary),
                "bytes": temporary.stat().st_size,
            }
            atomic_write_json(pending_path, manifest)
            if backed_up:
                backup_started = True
                os.replace(self.ram_path, backup_ram)
                os.replace(manifest_path, backup_manifest)
                backup_installed = True
                fsync_directory(self.runtime_dir)
            os.replace(temporary, self.ram_path)
            os.chmod(self.ram_path, 0o600)
            fsync_directory(self.runtime_dir)
            atomic_write_json(manifest_path, manifest)
        except Exception:
            if backup_installed:
                failed_path = self.ram_path.with_name(
                    f".{self.ram_path.name}.{uuid.uuid4().hex}.failed"
                )
                if self.ram_path.exists():
                    os.replace(self.ram_path, failed_path)
                manifest_path.unlink(missing_ok=True)
                os.replace(backup_ram, self.ram_path)
                os.replace(backup_manifest, manifest_path)
                pending_path.unlink(missing_ok=True)
                temporary.unlink(missing_ok=True)
                fsync_directory(self.runtime_dir)
            elif backup_started:
                try:
                    self._recover_ram_transaction()
                except Exception:
                    LOGGER.exception("Could not recover partial RAM backup")
            elif backed_up:
                pending_path.unlink(missing_ok=True)
                temporary.unlink(missing_ok=True)
                fsync_directory(self.runtime_dir)
            elif not pending_path.exists():
                temporary.unlink(missing_ok=True)
            raise
        else:
            backup_ram.unlink(missing_ok=True)
            backup_manifest.unlink(missing_ok=True)
            pending_path.unlink(missing_ok=True)
            fsync_directory(self.runtime_dir)

    def _read_external_controls(self) -> None:
        try:
            with self.control_path.open("r", encoding="utf-8") as handle:
                handle.seek(self.control_offset)
                for line in handle:
                    try:
                        command = json.loads(line)
                    except json.JSONDecodeError:
                        LOGGER.warning("Ignored malformed control line")
                        continue
                    if isinstance(command, dict):
                        self.controls.put(command)
                self.control_offset = handle.tell()
        except OSError as error:
            self.status["last_error"] = f"Control queue error: {error}"

    def _set_control_mode(self, mode: str) -> None:
        if mode not in {"ai", "manual", "paused"}:
            raise ValueError(f"Unknown control mode: {mode}")
        if mode == "paused":
            if self.control_mode != "paused":
                self.resume_mode = self.control_mode
        else:
            self.resume_mode = mode
        self.control_generation += 1
        self.control_mode = mode
        self.paused = mode == "paused"
        self._sync_emulator_pause()
        self.player.release(self.pyboy)
        if mode == "ai":
            self.last_decision_finished = 0
            self.status["brain_status"] = (
                "thinking" if self.decision_pending else "idle"
            )
        elif mode == "manual":
            self.status["brain_status"] = "manual"
        else:
            self.status["brain_status"] = "paused"

    def _set_emulator_paused(self, paused: bool) -> None:
        if paused == self.emulator_pause_requested:
            return
        from pyboy.utils import WindowEvent

        self.pyboy.send_input(WindowEvent.PAUSE if paused else WindowEvent.UNPAUSE)
        self.emulator_pause_requested = paused

    def _sync_emulator_pause(self) -> None:
        should_pause = self.control_mode == "paused" or (
            self.control_mode == "ai" and self.decision_pending
        )
        self._set_emulator_paused(should_pause)

    def _process_controls(self) -> None:
        while True:
            try:
                command = self.controls.get_nowait()
            except queue.Empty:
                break
            action = str(command.get("action", "")).lower()
            if action == "pause":
                self._set_control_mode("paused")
            elif action == "resume":
                self._set_control_mode(self.resume_mode)
            elif action == "manual":
                self._set_control_mode("manual")
            elif action == "autonomy":
                self._set_control_mode("ai")
            elif action == "checkpoint":
                self._rotate_clip("manual checkpoint")
            elif action == "press":
                button = str(command.get("button", "")).lower()
                if button in VALID_BUTTONS:
                    self._set_control_mode("manual")
                    self.player.append(button)
            elif action == "stop":
                self.stop_event.set()

    def _save_latest_frame(self, image: Any) -> None:
        temporary = self.runtime_dir / ".latest.png.tmp"
        image.save(temporary, format="PNG")
        os.replace(temporary, self.runtime_dir / "latest.png")

    def _request_decision(
        self, image: Any, game_state: dict[str, Any], collision_map: Optional[str]
    ) -> None:
        screenshot = (
            self.screens_dir
            / f"decision-{self.run_id}-{self.decision_sequence + 1:08d}.png"
        )
        image.save(screenshot, format="PNG")
        decision_state = dict(game_state)
        route_guidance = (
            rock_tunnel_route_guidance(game_state)
            or celadon_route_guidance(game_state)
        )
        if route_guidance:
            decision_state["route_guidance"] = route_guidance
        request = {
            "decision_id": self.decision_sequence + 1,
            "generation": self.control_generation,
            "screenshot": str(screenshot),
            "game_state": decision_state,
            "collision_map": collision_map,
            "history": list(self.history[-8:]),
        }
        self.decision_sequence += 1
        self.pending_decision_id = request["decision_id"]
        self.decision_pending = True
        self._sync_emulator_pause()
        try:
            self.brain_requests.put_nowait(request)
        except queue.Full as error:
            self.decision_pending = False
            self.pending_decision_id = None
            self._sync_emulator_pause()
            raise RuntimeError("Copilot request queue is unexpectedly full") from error
        self.last_decision_requested = time.monotonic()
        self.status["brain_status"] = "thinking"
        self.status["model_calls"] += 1

        self._prune_decision_screenshots(screenshot)

    def _prune_decision_screenshots(
        self,
        current_screenshot: Path,
        keep: int = 30,
    ) -> None:
        candidates = [
            path
            for path in self.screens_dir.glob("decision-*.png")
            if path != current_screenshot
        ]
        candidates.sort(key=lambda path: path.stat().st_mtime_ns)
        retained_old_count = max(0, keep - 1)
        stale = candidates[:-retained_old_count] if retained_old_count else candidates
        for old_screenshot in stale:
            old_screenshot.unlink(missing_ok=True)

    def _apply_brain_result(self) -> None:
        try:
            result = self.brain_results.get_nowait()
        except queue.Empty:
            return
        decision_id = result.get("decision_id")
        generation = result.get("generation")
        if decision_id == self.pending_decision_id:
            self.decision_pending = False
            self.pending_decision_id = None
            self._sync_emulator_pause()
        self.last_decision_finished = time.monotonic()
        if generation != self.control_generation or self.control_mode != "ai":
            self.status["last_discarded_decision"] = {
                "decision_id": decision_id,
                "reason": "control ownership changed",
                "timestamp": utc_now(),
            }
            self.status["brain_status"] = (
                "manual" if self.control_mode == "manual" else self.control_mode
            )
            return
        if result.get("error"):
            self.status["brain_status"] = "error"
            self.status["last_error"] = str(result["error"])
            LOGGER.error("Copilot decision error: %s", result["error"])
            return

        decision = result["decision"]
        self.status["brain_status"] = "acting"
        self.status["last_error"] = None
        self.status["observation"] = decision["observation"]
        self.status["phase"] = decision["phase"]
        self.status["objective"] = decision["objective"]
        self.status["reason"] = decision["reason"]
        self.status["last_action"] = decision["buttons"]
        self.player.replace(decision["buttons"])
        history_item = {
            "timestamp": utc_now(),
            "location": self.status.get("game_state", {}).get("location"),
            **decision,
        }
        self.history.append(history_item)
        self.history = self.history[-50:]
        self.total_decisions += 1
        atomic_write_json(
            self.runtime_dir / "brain.json",
            {
                "history": self.history,
                "total_decisions": self.total_decisions,
                "updated_at": utc_now(),
            },
        )
        if decision["checkpoint"]:
            self._rotate_clip(f"Copilot checkpoint: {decision['objective'][:120]}")

    def _maybe_milestone(self, game_state: dict[str, Any]) -> None:
        badge_count = len(game_state.get("badges", []))
        if badge_count > self.last_badges:
            self.last_badges = badge_count
            self._rotate_clip(f"Badge milestone: {game_state['badges'][-1]}")
        if game_state.get("hall_of_fame") and not self.status["completed"]:
            self.status["completed"] = True
            self._rotate_clip("Pokemon Red completed: Hall of Fame")
            self._set_control_mode("paused")

    def _tick_emulator(self) -> bool:
        should_apply_input = self.control_mode != "paused" and not (
            self.control_mode == "ai" and self.decision_pending
        )
        if should_apply_input:
            self.player.tick(self.pyboy)
        return bool(self.pyboy.tick())

    def _shutdown_runtime(self, reason: str) -> None:
        cleanup_errors: list[str] = []
        self.stop_event.set()
        try:
            self.player.release(self.pyboy)
        except Exception as error:
            cleanup_errors.append(f"controller release: {error}")
            LOGGER.exception("Controller release failed during shutdown")

        try:
            self._save_checkpoint(reason, allow_stopped=True)
        except Exception as error:
            cleanup_errors.append(f"checkpoint: {error}")
            LOGGER.exception("Checkpoint shutdown failed")

        try:
            frames_written = self.recorder.frames_written
            started_at = self.recorder.started_at
            finished = self.recorder.finish()
            if finished:
                self._write_clip_manifest(
                    finished,
                    reason,
                    frames_written,
                    started_at,
                )
            self.status["last_completed_clip"] = (
                str(finished) if finished else self.status.get("last_completed_clip")
            )
        except Exception as error:
            cleanup_errors.append(f"recorder: {error}")
            LOGGER.exception("Recorder shutdown failed")

        try:
            self._stop_brain_worker()
        except Exception as error:
            cleanup_errors.append(f"Copilot worker: {error}")
            LOGGER.exception("Copilot worker shutdown failed")

        try:
            self.viewer.stop()
        except Exception as error:
            cleanup_errors.append(f"viewer: {error}")
            LOGGER.exception("Viewer shutdown failed")

        try:
            self._save_ram_and_stop()
        except Exception as error:
            cleanup_errors.append(f"cartridge RAM: {error}")
            LOGGER.exception("Cartridge RAM shutdown failed")

        try:
            self._enforce_retention()
        except Exception as error:
            cleanup_errors.append(f"retention: {error}")
            LOGGER.exception("Artifact retention failed during shutdown")

        self.status["current_clip"] = None
        self.status["running"] = False
        self.status["stopped_at"] = utc_now()
        if cleanup_errors:
            previous_error = self.status.get("last_error")
            errors = ([str(previous_error)] if previous_error else []) + cleanup_errors
            self.status["last_error"] = "; ".join(errors)
            self.status["lifecycle"] = "failed"
        elif self.status.get("lifecycle") != "failed":
            self.status["lifecycle"] = "stopped"
        try:
            self._write_status()
        except OSError:
            LOGGER.exception("Failed to publish terminal Pokemon status")
        (self.runtime_dir / "pid").unlink(missing_ok=True)

    def run(self) -> None:
        if not is_pokemon_red_rom(self.rom):
            raise RuntimeError(f"Not a Pokemon Red ROM: {self.rom}")
        existing = read_json(self.status_path)
        if (
            process_is_alive(existing.get("pid"))
            and int(existing["pid"]) != os.getpid()
        ):
            raise RuntimeError(
                f"Pokemon player is already running as PID {existing['pid']}"
            )

        from pyboy import PyBoy

        emulator_errors: tuple[type[BaseException], ...] = (OSError, ValueError)
        try:
            from pyboy.utils import PyBoyException
        except ImportError:
            pass
        else:
            emulator_errors += (PyBoyException,)

        window = "SDL2" if self.args.visible else "null"

        def create_emulator(ram_path: Optional[Path]) -> Any:
            ram_input = ram_path.open("rb") if ram_path else None
            try:
                with self.rom.open("rb") as rom_input:
                    return PyBoy(
                        rom_input,
                        window=window,
                        scale=4,
                        sound_volume=0,
                        sound_emulated=False,
                        ram_file=ram_input,
                    )
            finally:
                if ram_input:
                    ram_input.close()

        validated_ram = self._validated_ram_path()
        try:
            self.pyboy = create_emulator(validated_ram)
        except emulator_errors as ram_error:
            if validated_ram is None:
                raise
            try:
                self.pyboy = create_emulator(None)
            except emulator_errors as retry_error:
                raise retry_error from ram_error
            self._quarantine_ram(f"PyBoy rejected cartridge RAM: {ram_error}")
        self.pyboy.set_emulation_speed(1)
        for _ in range(90):
            if not self.pyboy.tick():
                raise RuntimeError("PyBoy stopped during startup")

        loaded_state = self._load_latest_state()
        self.status["loaded_state"] = str(loaded_state) if loaded_state else None
        reader = PokemonMemoryReader(self.pyboy.memory)
        initial_state = reader.snapshot()
        self.last_badges = len(initial_state.get("badges", []))
        self.status["game_state"] = initial_state
        initial_image = self.pyboy.screen.image.copy()
        self._save_latest_frame(initial_image)
        self.viewer.start()

        self.brain_thread = threading.Thread(
            target=self._brain_loop,
            name="pokemon-copilot-brain",
            daemon=False,
        )
        try:
            self.brain_thread.start()
            startup_wait = (
                COPILOT_START_TIMEOUT_SECONDS + COPILOT_STOP_TIMEOUT_SECONDS + 5
            )
            if not self.brain_ready.wait(timeout=startup_wait):
                raise RuntimeError(
                    f"Copilot brain did not initialize within {startup_wait} seconds"
                )
            if self.stop_event.is_set():
                raise RuntimeError("Pokemon startup was cancelled")
            if self.status.get("brain_status") == "error":
                raise RuntimeError(
                    str(self.status.get("last_error", "Copilot brain failed"))
                )
            clip = self.recorder.start()
            self.status["current_clip"] = str(clip)
            self.clip_started = time.monotonic()
            self.next_record_at = self.clip_started
            self._record_due_frames(initial_image)
            self._enforce_retention()
            self.status["lifecycle"] = "ready"
            self._write_status()

            if self.args.open_viewer:
                viewer_url = authenticated_viewer_url(
                    self.runtime_dir,
                    {"port": self.viewer.port},
                )
                threading.Timer(
                    1.0,
                    lambda: webbrowser.open(viewer_url),
                ).start()
        except Exception:
            self._shutdown_runtime("startup failed")
            raise

        last_status_write = 0.0
        last_retention_check = 0.0
        try:
            while not self.stop_event.is_set():
                self._read_external_controls()
                self._process_controls()
                self._apply_brain_result()

                if not self._tick_emulator():
                    self.stop_event.set()
                    break

                self.frames += 1
                image = self.pyboy.screen.image.copy()
                self._record_due_frames(image)
                if self.frames % 6 == 0:
                    self._save_latest_frame(image)

                game_state = reader.snapshot()
                if self.frames % 30 == 0:
                    self.status["game_state"] = game_state
                    self._maybe_milestone(game_state)

                if (
                    self.control_mode == "ai"
                    and self.brain_available.is_set()
                    and not self.decision_pending
                    and self.player.idle
                    and time.monotonic() - self.last_decision_finished >= 1.0
                ):
                    self._request_decision(
                        image, game_state, collision_ascii(self.pyboy)
                    )

                if time.monotonic() - self.clip_started >= self.args.clip_minutes * 60:
                    self._rotate_clip("automatic clip boundary")

                if time.monotonic() - last_retention_check >= 30:
                    try:
                        self._enforce_retention()
                    except OSError as error:
                        self.status["last_error"] = (
                            f"Artifact retention failed: {error}"
                        )
                        LOGGER.exception("Artifact retention failed")
                    last_retention_check = time.monotonic()

                if (
                    self.frames % 30 == 0
                    and time.monotonic() - last_status_write >= 0.5
                ):
                    self.status["actions_taken"] = self.total_decisions
                    self._write_status()
                    last_status_write = time.monotonic()
        finally:
            self._shutdown_runtime("session stopped")


def add_runtime_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_supervised: bool = False,
) -> None:
    parser.add_argument("--rom", required=True)
    parser.add_argument("--runtime-dir", default=str(DEFAULT_RUNTIME_DIR), type=Path)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--clip-minutes", type=float, default=10)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--decision-timeout", type=int, default=180)
    parser.add_argument("--instance-id", default=None)
    parser.add_argument("--max-clips", type=int, default=DEFAULT_MAX_CLIPS)
    parser.add_argument("--max-states", type=int, default=DEFAULT_MAX_STATES)
    parser.add_argument(
        "--max-storage-gb",
        type=float,
        default=DEFAULT_MAX_STORAGE_GB,
    )
    parser.add_argument("--min-free-gb", type=float, default=DEFAULT_MIN_FREE_GB)
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--open-viewer", action="store_true")
    parser.add_argument(
        "--no-resume", action="store_false", dest="resume", default=True
    )
    if include_supervised:
        parser.add_argument("--supervised", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copilot Plays Pokemon Red")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run the persistent player")
    add_runtime_arguments(run_parser, include_supervised=True)
    supervisor_parser = subparsers.add_parser(
        "supervise",
        help="Run and restart the player after unexpected child failures",
    )
    add_runtime_arguments(supervisor_parser)
    return parser


def runtime_command(args: argparse.Namespace, *, open_viewer: bool) -> list[str]:
    command = [
        sys.executable,
        str(CARTRIDGE_FILE),
        "run",
        "--rom",
        str(args.rom),
        "--runtime-dir",
        str(args.runtime_dir),
        "--port",
        str(args.port),
        "--clip-minutes",
        str(args.clip_minutes),
        "--model",
        str(args.model),
        "--decision-timeout",
        str(args.decision_timeout),
        "--instance-id",
        str(args.instance_id),
        "--max-clips",
        str(args.max_clips),
        "--max-states",
        str(args.max_states),
        "--max-storage-gb",
        str(args.max_storage_gb),
        "--min-free-gb",
        str(args.min_free_gb),
        "--supervised",
    ]
    if args.visible:
        command.append("--visible")
    if open_viewer and args.open_viewer:
        command.append("--open-viewer")
    if not args.resume:
        command.append("--no-resume")
    return command


def terminate_supervised_child(child: subprocess.Popen[Any]) -> int:
    returncode = child.poll()
    if returncode is not None:
        return int(returncode)
    child.terminate()
    try:
        return child.wait(timeout=SUPERVISOR_SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        child.kill()
        return child.wait(timeout=10)


def wait_for_supervised_child(
    child: subprocess.Popen[Any],
    stop_requested: threading.Event,
    runtime_dir: Optional[Path] = None,
) -> tuple[int, bool]:
    while True:
        try:
            return child.wait(timeout=1), False
        except subprocess.TimeoutExpired:
            if runtime_dir:
                desired_running = read_json(
                    runtime_dir / "desired.json",
                    {"running": True},
                ).get("running", True)
                if not desired_running:
                    stop_requested.set()
                status = read_json(runtime_dir / "status.json")
                status_matches_child = status.get("pid") == child.pid
                failed = status_matches_child and status.get("lifecycle") == "failed"
                stale = status_matches_child and (
                    (
                        status.get("lifecycle") == "ready"
                        and heartbeat_is_stale(
                            status,
                            SUPERVISOR_HEARTBEAT_TIMEOUT_SECONDS,
                        )
                    )
                    or (
                        status.get("lifecycle") == "initializing"
                        and heartbeat_is_stale(
                            status,
                            SUPERVISOR_STARTUP_TIMEOUT_SECONDS,
                        )
                    )
                )
                if (failed or stale) and not stop_requested.is_set():
                    reason = "failed" if failed else "stale heartbeat"
                    LOGGER.error(
                        "Supervisor terminating Pokemon child after %s",
                        reason,
                    )
                    atomic_write_json(
                        runtime_dir / RESTART_REQUEST_NAME,
                        {
                            "child_pid": child.pid,
                            "reason": reason,
                            "requested_at": utc_now(),
                        },
                    )
                    return terminate_supervised_child(child), True
            if not stop_requested.is_set():
                continue
            return terminate_supervised_child(child), False


def supervisor_main(args: argparse.Namespace) -> int:
    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runtime_dir, 0o700)
    if not args.instance_id:
        args.instance_id = uuid.uuid4().hex
    try:
        lock_handle = acquire_runtime_lock(
            runtime_dir,
            f"supervisor-{args.instance_id}",
            "supervisor.lock",
        )
    except RuntimeError:
        LOGGER.error("Another Pokemon supervisor owns this runtime directory")
        return 2

    desired = {
        "running": True,
        "instance_id": args.instance_id,
        "updated_at": utc_now(),
        "config": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
            if key != "command"
        },
    }
    atomic_write_json(runtime_dir / "desired.json", desired)
    stop_requested = threading.Event()
    child: Optional[subprocess.Popen[Any]] = None

    def request_stop(signum: int, frame: Any) -> None:
        del signum, frame
        stop_requested.set()
        set_desired_running(runtime_dir, False)
        if child and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    restart_times: deque[float] = deque()
    first_launch = True
    try:
        while not stop_requested.is_set():
            (runtime_dir / RESTART_REQUEST_NAME).unlink(missing_ok=True)
            child = subprocess.Popen(
                runtime_command(args, open_viewer=first_launch),
                stdin=subprocess.DEVNULL,
            )
            first_launch = False
            atomic_write_json(
                runtime_dir / "supervisor.json",
                {
                    "running": True,
                    "pid": os.getpid(),
                    "child_pid": child.pid,
                    "instance_id": args.instance_id,
                    "restart_timestamps": [
                        datetime.fromtimestamp(value, timezone.utc).isoformat()
                        for value in restart_times
                    ],
                    "updated_at": utc_now(),
                },
            )
            exit_code, restart_required = wait_for_supervised_child(
                child,
                stop_requested,
                runtime_dir,
            )
            child = None
            if not read_json(
                runtime_dir / "desired.json",
                {"running": True},
            ).get("running", True):
                stop_requested.set()
            if stop_requested.is_set() or (exit_code == 0 and not restart_required):
                break

            now = time.time()
            restart_times.append(now)
            while restart_times and now - restart_times[0] > 600:
                restart_times.popleft()
            if len(restart_times) > 10:
                LOGGER.error("Pokemon supervisor restart circuit opened")
                return 1
            time.sleep(min(30, 2 ** min(len(restart_times), 4)))
    finally:
        if child and child.poll() is None:
            terminate_supervised_child(child)
        desired["running"] = False
        desired["updated_at"] = utc_now()
        atomic_write_json(runtime_dir / "desired.json", desired)
        atomic_write_json(
            runtime_dir / "supervisor.json",
            {
                "running": False,
                "pid": os.getpid(),
                "instance_id": args.instance_id,
                "stopped_at": utc_now(),
            },
        )
        (runtime_dir / RESTART_REQUEST_NAME).unlink(missing_ok=True)
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()
    return 0


def runner_main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    os.umask(0o077)
    if args.command == "supervise":
        return supervisor_main(args)
    if not args.instance_id:
        args.instance_id = uuid.uuid4().hex
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    try:
        lock_handle = acquire_runtime_lock(runtime_dir, args.instance_id)
    except RuntimeError as error:
        LOGGER.error("%s", error)
        return 2

    runner: Optional[PokemonRunner] = None
    try:
        runner = PokemonRunner(args)

        def request_stop(signum: int, frame: Any) -> None:
            del signum, frame
            if runner:
                runner.stop_event.set()
                runner.brain_ready.set()
                if runner.brain:
                    runner.brain.cancel()

        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)
        atomic_write_json(
            runtime_dir / "status.json",
            {
                "running": True,
                "pid": os.getpid(),
                "instance_id": args.instance_id,
                "port": args.port,
                "lifecycle": "initializing",
                "brain_status": "starting",
                "started_at": utc_now(),
            },
        )
        pid_descriptor = os.open(
            runtime_dir / "pid",
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(pid_descriptor, "w", encoding="ascii") as pid_handle:
            pid_handle.write(str(os.getpid()))
        runner.run()
        return 1 if runner.status.get("lifecycle") == "failed" else 0
    except Exception as error:
        LOGGER.exception("Pokemon player failed")
        status = runtime_status(runtime_dir)
        status.update(
            {
                "running": False,
                "pid": os.getpid(),
                "instance_id": args.instance_id,
                "lifecycle": "failed",
                "last_error": str(error),
                "stopped_at": utc_now(),
            }
        )
        atomic_write_json(runtime_dir / "status.json", status)
        return 1
    finally:
        (runtime_dir / "pid").unlink(missing_ok=True)
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(runner_main())
