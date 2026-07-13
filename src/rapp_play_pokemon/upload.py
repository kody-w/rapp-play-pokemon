"""Authenticated loopback-only ROM selection and upload page."""

from __future__ import annotations

import http.server
import json
import os
import secrets
import stat
import threading
import urllib.parse
import webbrowser
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Callable

MAX_ROM_BYTES = 2 * 1024 * 1024
MIN_ROM_BYTES = 32 * 1024
HEADER_END = 0x150
ROM_SIZE_BYTES = {
    0x00: 32 * 1024,
    0x01: 64 * 1024,
    0x02: 128 * 1024,
    0x03: 256 * 1024,
    0x04: 512 * 1024,
    0x05: 1024 * 1024,
    0x06: 2 * 1024 * 1024,
    0x52: 1152 * 1024,
    0x53: 1280 * 1024,
    0x54: 1536 * 1024,
}

SETUP_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RAPP Plays Pokemon setup</title>
<link rel="stylesheet" href="/setup.css">
</head>
<body>
<main>
<h1>RAPP Plays Pokemon</h1>
<p>Select your own legally obtained Pokemon Red Game Boy ROM. Nothing leaves
this computer; uploaded bytes are stored only in the private local runtime.</p>
<section>
<h2>Upload in this browser</h2>
<input id="rom" type="file" accept=".gb,application/octet-stream">
<button id="upload">Validate and select upload</button>
</section>
<section>
<h2>Or select an existing local path</h2>
<input id="path" type="text" autocomplete="off"
  placeholder="/absolute/path/to/Pokemon Red.gb">
<button id="select">Validate and select path</button>
</section>
<button id="start" disabled>Start autonomous playthrough</button>
<pre id="status">Waiting for a ROM.</pre>
<a id="viewer" rel="noreferrer noopener">Open authenticated live viewer</a>
</main>
<script src="/setup.js" defer></script>
</body>
</html>
"""

SETUP_CSS = """
:root { color-scheme: dark; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
body { margin: 0; background: #10141f; color: #eef3ff; }
main { max-width: 760px; margin: auto; padding: 24px; }
h1 { color: #ffdf4d; }
section { background: #192235; border: 1px solid #34445f; border-radius: 12px;
  margin: 16px 0; padding: 16px; }
input[type="text"] { box-sizing: border-box; width: 100%; }
input, button { font: inherit; margin: 6px 0; padding: 10px; }
button { background: #2c6bed; border: 0; border-radius: 6px; color: white;
  cursor: pointer; }
button:disabled { cursor: not-allowed; opacity: .5; }
pre { overflow-wrap: anywhere; white-space: pre-wrap; }
#viewer { display: none; color: #8fc5ff; }
"""

SETUP_JS = """
const statusNode = document.getElementById('status');
const startButton = document.getElementById('start');
const viewer = document.getElementById('viewer');
function show(value) {
  statusNode.textContent = typeof value === 'string'
    ? value : JSON.stringify(value, null, 2);
}
async function jsonPost(path, value) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {'content-type': 'application/json'},
    body: JSON.stringify(value)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || `request failed: ${response.status}`);
  return result;
}
document.getElementById('upload').addEventListener('click', async () => {
  try {
    const file = document.getElementById('rom').files[0];
    if (!file) throw new Error('Choose a .gb file first.');
    show('Validating local upload...');
    const response = await fetch('/api/upload', {
      method: 'POST',
      headers: {
        'content-type': 'application/octet-stream',
        'x-rom-name': encodeURIComponent(file.name)
      },
      body: file
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || 'upload rejected');
    startButton.disabled = false;
    show(result);
  } catch (error) {
    show(String(error));
  }
});
document.getElementById('select').addEventListener('click', async () => {
  try {
    const path = document.getElementById('path').value;
    const result = await jsonPost('/api/select', {path});
    startButton.disabled = false;
    show(result);
  } catch (error) {
    show(String(error));
  }
});
startButton.addEventListener('click', async () => {
  try {
    startButton.disabled = true;
    show(await jsonPost('/api/start', {}));
  } catch (error) {
    startButton.disabled = false;
    show(String(error));
  }
});
async function poll() {
  try {
    const response = await fetch('/api/status', {cache: 'no-store'});
    if (!response.ok) return;
    const result = await response.json();
    if (result.launching || result.result) show(result);
    if (result.result && result.result.viewer_url) {
      viewer.href = result.result.viewer_url;
      viewer.style.display = 'inline';
    }
  } catch (_) {
    return;
  }
}
setInterval(poll, 1000);
"""


def _header_checksum(header: bytes) -> int:
    checksum = 0
    for value in header[0x134:0x14D]:
        checksum = (checksum - value - 1) & 0xFF
    return checksum


def validate_pokemon_red_bytes(data: bytes) -> None:
    if not MIN_ROM_BYTES <= len(data) <= MAX_ROM_BYTES:
        raise ValueError(
            f"ROM size must be between {MIN_ROM_BYTES} and {MAX_ROM_BYTES} bytes"
        )
    if len(data) < HEADER_END:
        raise ValueError("File is too short to contain a Game Boy header")
    title = data[0x134:0x144].split(b"\0", 1)[0].decode("ascii", "ignore").strip()
    if not title.upper().startswith("POKEMON RED"):
        raise ValueError("Game Boy header title is not POKEMON RED")
    if data[0x147] != 0x13:
        raise ValueError("Unexpected cartridge type for Pokemon Red")
    declared_size = ROM_SIZE_BYTES.get(data[0x148])
    if declared_size is None or declared_size != len(data):
        raise ValueError("Game Boy header ROM size does not match the file")
    if data[0x14D] != _header_checksum(data):
        raise ValueError("Game Boy header checksum is invalid")


def validate_pokemon_red_path(path_value: str | Path) -> Path:
    unresolved = Path(path_value).expanduser()
    if unresolved.suffix.lower() != ".gb":
        raise ValueError("ROM path must end in .gb")
    try:
        path = unresolved.resolve(strict=True)
        info = path.stat()
    except OSError as error:
        raise ValueError(f"Cannot read ROM path: {error}") from error
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("ROM path must identify a regular file")
    if not MIN_ROM_BYTES <= info.st_size <= MAX_ROM_BYTES:
        raise ValueError("ROM size is outside the accepted local limit")
    try:
        data = path.read_bytes()
    except OSError as error:
        raise ValueError(f"Cannot read ROM path: {error}") from error
    validate_pokemon_red_bytes(data)
    return path


def _private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"Private runtime path is unsafe: {path}")
    os.chmod(path, 0o700)
    return path


def _ensure_runtime_owner(runtime_dir: Path) -> None:
    marker = runtime_dir / "runtime-owner.json"
    if marker.is_symlink():
        raise RuntimeError("Refusing a symlinked runtime ownership marker")
    if marker.exists():
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError("Runtime ownership marker is invalid") from error
        if value.get("product") != "rapp-play-pokemon":
            raise RuntimeError("Runtime directory belongs to another product")
        return
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"product": "rapp-play-pokemon"}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(marker, 0o600)


def store_private_rom(runtime_dir: Path, data: bytes) -> Path:
    validate_pokemon_red_bytes(data)
    rom_dir = _private_directory(runtime_dir / "rom")
    destination = rom_dir / "pokemon-red-upload.gb"
    if destination.is_symlink():
        raise RuntimeError("Refusing a symlinked upload destination")
    temporary = rom_dir / f".upload-{secrets.token_hex(8)}.partial"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        directory_descriptor = os.open(rom_dir, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    return destination


class UploadServer:
    def __init__(
        self,
        runtime_dir: Path,
        port: int,
        start_callback: Callable[[Path], dict[str, Any]],
    ):
        self.runtime_dir = _private_directory(runtime_dir.expanduser()).resolve()
        _ensure_runtime_owner(self.runtime_dir)
        self.port = port
        self.start_callback = start_callback
        self.token = secrets.token_urlsafe(32)
        self.selected_path: Path | None = None
        self.launching = False
        self.result: dict[str, Any] | None = None
        self.lock = threading.Lock()
        self.server: http.server.ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def bootstrap_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?token={urllib.parse.quote(self.token)}"

    def _public_status(self) -> dict[str, Any]:
        with self.lock:
            result = None
            if self.result:
                allowed = ("status", "message", "viewer_url", "brain_backend")
                result = {key: self.result[key] for key in allowed if key in self.result}
            return {
                "selected": self.selected_path is not None,
                "launching": self.launching,
                "result": result,
            }

    def _launch(self) -> None:
        with self.lock:
            selected = self.selected_path
        if selected is None:
            return
        try:
            result = self.start_callback(selected)
            if not isinstance(result, dict):
                raise RuntimeError("Cartridge returned a non-object result")
        except Exception as error:
            result = {"status": "error", "message": str(error)}
        with self.lock:
            self.result = result
            self.launching = False

    def start(self) -> None:
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format_string: str, *args: Any) -> None:
                del format_string, args

            def _security_headers(self) -> None:
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; img-src 'none'; media-src 'none'; "
                    "style-src 'self'; script-src 'self'; connect-src 'self'; "
                    "frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
                )
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("Cache-Control", "no-store")

            def _host_allowed(self) -> bool:
                return self.headers.get("Host", "") == (
                    f"127.0.0.1:{self.server.server_address[1]}"
                )

            def _authenticated(self) -> bool:
                if not self._host_allowed():
                    return False
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get("Cookie", ""))
                except ValueError:
                    return False
                supplied = cookie.get("rapp_play_pokemon_setup")
                return bool(
                    supplied
                    and secrets.compare_digest(supplied.value, owner.token)
                )

            def _same_origin(self) -> bool:
                expected = f"http://127.0.0.1:{self.server.server_address[1]}"
                return self.headers.get("Origin") == expected

            def _bytes(
                self, status_code: int, payload: bytes, content_type: str
            ) -> None:
                self.send_response(status_code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self._security_headers()
                self.end_headers()
                self.wfile.write(payload)

            def _json(self, status_code: int, value: dict[str, Any]) -> None:
                self._bytes(
                    status_code,
                    json.dumps(value).encode("utf-8"),
                    "application/json",
                )

            def _forbidden(self) -> None:
                self._json(403, {"status": "error", "message": "forbidden"})

            def do_GET(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/" and self._host_allowed():
                    if not self._authenticated():
                        supplied = urllib.parse.parse_qs(
                            parsed.query, keep_blank_values=True
                        ).get("token", [""])[0]
                        if not secrets.compare_digest(supplied, owner.token):
                            self._forbidden()
                            return
                        self.send_response(303)
                        self.send_header("Location", "/")
                        self.send_header("Content-Length", "0")
                        self.send_header(
                            "Set-Cookie",
                            "rapp_play_pokemon_setup="
                            f"{owner.token}; Path=/; HttpOnly; SameSite=Strict",
                        )
                        self._security_headers()
                        self.end_headers()
                        return
                    self._bytes(
                        200,
                        SETUP_HTML.encode("utf-8"),
                        "text/html; charset=utf-8",
                    )
                    return
                if not self._authenticated():
                    self._forbidden()
                    return
                if parsed.path == "/setup.css":
                    self._bytes(
                        200,
                        SETUP_CSS.encode("utf-8"),
                        "text/css; charset=utf-8",
                    )
                elif parsed.path == "/setup.js":
                    self._bytes(
                        200,
                        SETUP_JS.encode("utf-8"),
                        "text/javascript; charset=utf-8",
                    )
                elif parsed.path == "/api/status":
                    self._json(200, owner._public_status())
                else:
                    self._json(404, {"status": "error", "message": "not found"})

            def _read_json(self) -> dict[str, Any]:
                if (
                    self.headers.get("Content-Type", "").split(";", 1)[0].strip()
                    != "application/json"
                ):
                    raise ValueError("Content-Type must be application/json")
                length = int(self.headers.get("Content-Length", "0"))
                if not 2 <= length <= 4096:
                    raise ValueError("Invalid JSON request size")
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("Expected a JSON object")
                return value

            def do_POST(self) -> None:
                parsed = urllib.parse.urlparse(self.path)
                if not self._authenticated() or not self._same_origin():
                    self._forbidden()
                    return
                try:
                    if parsed.path == "/api/upload":
                        self._upload()
                    elif parsed.path == "/api/select":
                        value = self._read_json()
                        path_value = value.get("path")
                        if not isinstance(path_value, str) or not path_value.strip():
                            raise ValueError("A local ROM path is required")
                        selected = validate_pokemon_red_path(path_value)
                        with owner.lock:
                            owner.selected_path = selected
                            owner.result = None
                        self._json(
                            200,
                            {
                                "status": "success",
                                "message": "Local Pokemon Red ROM selected",
                            },
                        )
                    elif parsed.path == "/api/start":
                        self._read_json()
                        with owner.lock:
                            if owner.selected_path is None:
                                raise ValueError("Select a ROM before starting")
                            if owner.launching:
                                raise ValueError("Launch is already in progress")
                            owner.launching = True
                            owner.result = None
                        threading.Thread(target=owner._launch, daemon=True).start()
                        self._json(
                            202,
                            {
                                "status": "success",
                                "message": "RAPP cartridge launch started",
                            },
                        )
                    else:
                        self._json(
                            404, {"status": "error", "message": "not found"}
                        )
                except (
                    json.JSONDecodeError,
                    OSError,
                    UnicodeDecodeError,
                    ValueError,
                ) as error:
                    self._json(400, {"status": "error", "message": str(error)})

            def _upload(self) -> None:
                content_type = (
                    self.headers.get("Content-Type", "").split(";", 1)[0].strip()
                )
                if content_type != "application/octet-stream":
                    raise ValueError(
                        "Content-Type must be application/octet-stream"
                    )
                if self.headers.get("Transfer-Encoding"):
                    raise ValueError("Chunked uploads are not accepted")
                encoded_name = self.headers.get("X-ROM-Name", "")
                name = urllib.parse.unquote(encoded_name)
                if (
                    not name
                    or len(name) > 255
                    or Path(name).name != name
                    or Path(name).suffix.lower() != ".gb"
                ):
                    raise ValueError("Upload must be a single .gb file")
                length = int(self.headers.get("Content-Length", "0"))
                if not MIN_ROM_BYTES <= length <= MAX_ROM_BYTES:
                    raise ValueError("Upload size is outside the accepted limit")
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError("Upload ended before Content-Length")
                selected = store_private_rom(owner.runtime_dir, data)
                with owner.lock:
                    owner.selected_path = selected
                    owner.result = None
                self._json(
                    200,
                    {
                        "status": "success",
                        "message": "Pokemon Red ROM validated and stored privately",
                    },
                )

        class LoopbackServer(http.server.ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        self.server = LoopbackServer(("127.0.0.1", self.port), Handler)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=5)


def serve_upload(
    runtime_dir: Path,
    port: int,
    open_browser: bool,
    start_callback: Callable[[Path], dict[str, Any]],
) -> int:
    server = UploadServer(runtime_dir, port, start_callback)
    server.start()
    url = server.bootstrap_url
    print(f"Private local setup: {url}")
    print("Keep this URL private; press Ctrl+C to close setup.")
    if open_browser:
        webbrowser.open(url)
    try:
        assert server.thread is not None
        while server.thread.is_alive():
            server.thread.join(timeout=1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0
