from __future__ import annotations

import asyncio
import base64
import json
import stat
import threading
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent as cartridge
from rapp_play_pokemon import cli
from rapp_play_pokemon.brainstem import (
    CARTRIDGE_FILENAME,
    register_cartridge,
    sha256,
)
from rapp_play_pokemon.upload import (
    UploadServer,
    validate_pokemon_red_bytes,
)

ROOT = Path(__file__).resolve().parents[1]


def synthetic_red_rom(size_code: int = 0x00) -> bytes:
    sizes = {
        0x00: 32 * 1024,
        0x01: 64 * 1024,
        0x02: 128 * 1024,
        0x03: 256 * 1024,
        0x04: 512 * 1024,
        0x05: 1024 * 1024,
        0x06: 2 * 1024 * 1024,
    }
    data = bytearray(sizes[size_code])
    data[0x134 : 0x134 + len(b"POKEMON RED")] = b"POKEMON RED"
    data[0x147] = 0x13
    data[0x148] = size_code
    checksum = 0
    for value in data[0x134:0x14D]:
        checksum = (checksum - value - 1) & 0xFF
    data[0x14D] = checksum
    return bytes(data)


def test_single_file_is_native_rapp_cartridge():
    assert cartridge.CARTRIDGE_MANIFEST["entry_class"] == "PokemonAgent"
    assert cartridge.CARTRIDGE_MANIFEST["contract"] == (
        "BasicAgent.perform(**kwargs) -> str"
    )
    assert cartridge.CARTRIDGE_MANIFEST["rom_included"] is False
    assert cartridge.PokemonAgent().metadata["name"] == "Pokemon"


def test_registers_exact_cartridge_with_canonical_layout(tmp_path):
    kernel = tmp_path / "kernel"
    agents = kernel / "agents"
    agents.mkdir(parents=True)
    (kernel / "brainstem.py").write_text("# canonical test boundary\n")
    (agents / "basic_agent.py").write_text("# canonical contract location\n")

    installed = register_cartridge(ROOT / "agent.py", kernel)

    assert installed == agents / CARTRIDGE_FILENAME
    assert sha256(installed) == sha256(ROOT / "agent.py")
    assert not installed.is_symlink()
    assert list(agents.glob("*.tmp")) == []


def test_cli_dispatches_through_brainstem(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def fake_invoke(kwargs):
        captured.update(kwargs)
        return '{"status":"success","message":"canonical loader invoked"}'

    monkeypatch.setattr(cli, "invoke", fake_invoke)
    code, result = cli.run(
        [
            "status",
            "--runtime-dir",
            str(tmp_path / "private-runtime"),
        ]
    )

    assert code == 0
    assert result["message"] == "canonical loader invoked"
    assert captured["action"] == "status"


def test_copilot_session_is_tool_free():
    captured: dict[str, object] = {}

    class Client:
        async def create_session(self, **kwargs):
            captured.update(kwargs)
            return object()

    brain = cartridge.CopilotBrain.__new__(cartridge.CopilotBrain)
    brain.model = "gpt-5.6-sol"
    brain.client = Client()
    brain.session = None
    brain.session_decisions = 99

    asyncio.run(brain._create_sdk_session())

    assert captured["model"] == "gpt-5.6-sol"
    assert captured["reasoning_effort"] == "max"
    assert captured["available_tools"] == []
    assert captured["skip_custom_instructions"] is True
    assert captured["enable_config_discovery"] is False
    assert captured["enable_on_demand_instruction_discovery"] is False
    assert captured["enable_skills"] is False
    assert captured["enable_session_store"] is False
    assert captured["enable_session_telemetry"] is False
    assert captured["memory"] == {"enabled": False}


def test_copilot_receives_only_current_screenshot(tmp_path):
    screenshot = tmp_path / "frame.bin"
    screenshot.write_bytes(b"synthetic-image")
    captured: dict[str, object] = {}

    class Session:
        async def send_and_wait(self, prompt, attachments, timeout):
            captured.update(
                prompt=prompt,
                attachments=attachments,
                timeout=timeout,
            )
            return SimpleNamespace(
                data=SimpleNamespace(
                    content='{"buttons":["a"],"checkpoint":false}'
                )
            )

    brain = cartridge.CopilotBrain.__new__(cartridge.CopilotBrain)
    brain.session = Session()
    brain.session_decisions = 0
    brain.max_decisions_per_session = 24
    brain.timeout_seconds = 30

    result = asyncio.run(brain._decide_sdk(screenshot, "structured state"))

    assert result["buttons"] == ["a"]
    assert captured["prompt"] == "structured state"
    assert captured["attachments"] == [
        {
            "type": "blob",
            "data": base64.b64encode(b"synthetic-image").decode("ascii"),
            "mimeType": "image/png",
        }
    ]


def test_rom_free_runner_smoke_uses_mock_pyboy(monkeypatch, tmp_path):
    class MockRunner:
        def __init__(self, args):
            del args
            self.status = {"lifecycle": "stopped"}
            self.stop_event = threading.Event()
            self.brain_ready = threading.Event()
            self.brain = None

        def run(self):
            return None

    monkeypatch.setattr(cartridge, "PokemonRunner", MockRunner)
    monkeypatch.setattr(cartridge.signal, "signal", lambda *args: None)

    assert (
        cartridge.runner_main(
            [
                "run",
                "--rom",
                str(tmp_path / "not-opened.bin"),
                "--runtime-dir",
                str(tmp_path),
                "--instance-id",
                "rom-free-smoke",
            ]
        )
        == 0
    )


def _request(
    opener,
    url: str,
    *,
    data: bytes | None = None,
    content_type: str | None = None,
    origin: str | None = None,
    name: str | None = None,
):
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type
    if origin:
        headers["Origin"] = origin
    if name:
        headers["X-ROM-Name"] = name
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    return opener.open(request, timeout=5)


def test_upload_rejects_invalid_header():
    data = bytearray(synthetic_red_rom())
    data[0x14D] ^= 0xFF
    with pytest.raises(ValueError, match="checksum"):
        validate_pokemon_red_bytes(bytes(data))


def test_upload_server_is_authenticated_same_origin_and_private(tmp_path):
    launched: list[Path] = []
    server = UploadServer(
        tmp_path / "runtime",
        0,
        lambda path: launched.append(path)
        or {
            "status": "success",
            "message": "mocked launch",
            "viewer_url": "http://127.0.0.1:8765/?token=private",
        },
    )
    server.start()
    origin = f"http://127.0.0.1:{server.port}"
    bare = urllib.request.build_opener()
    authenticated = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar())
    )
    try:
        with pytest.raises(urllib.error.HTTPError) as denied:
            bare.open(f"{origin}/api/status", timeout=5)
        assert denied.value.code == 403

        authenticated.open(server.bootstrap_url, timeout=5).read()
        rom = synthetic_red_rom()
        with pytest.raises(urllib.error.HTTPError) as cross_origin:
            _request(
                authenticated,
                f"{origin}/api/upload",
                data=rom,
                content_type="application/octet-stream",
                origin="https://attacker.example",
                name="owned.gb",
            )
        assert cross_origin.value.code == 403

        response = _request(
            authenticated,
            f"{origin}/api/upload",
            data=rom,
            content_type="application/octet-stream",
            origin=origin,
            name="owned.gb",
        )
        assert json.load(response)["status"] == "success"
        stored = tmp_path / "runtime" / "rom" / "pokemon-red-upload.gb"
        assert stored.read_bytes() == rom
        assert stat.S_IMODE((tmp_path / "runtime").stat().st_mode) == 0o700
        assert stat.S_IMODE((tmp_path / "runtime" / "rom").stat().st_mode) == 0o700
        assert stat.S_IMODE(stored.stat().st_mode) == 0o600

        response = _request(
            authenticated,
            f"{origin}/api/start",
            data=b"{}",
            content_type="application/json",
            origin=origin,
        )
        assert response.status == 202
        deadline = time.monotonic() + 5
        status = {}
        while time.monotonic() < deadline:
            status = json.load(
                authenticated.open(f"{origin}/api/status", timeout=5)
            )
            if status.get("result"):
                break
            time.sleep(0.02)
        assert status["result"]["status"] == "success"
        assert launched == [stored]
        assert "private" in status["result"]["viewer_url"]
    finally:
        server.stop()


def test_runtime_marker_and_upload_do_not_disclose_rom_path(tmp_path):
    server = UploadServer(
        tmp_path / "runtime",
        0,
        lambda path: {"status": "success", "message": path.name},
    )
    marker = tmp_path / "runtime" / "runtime-owner.json"
    assert json.loads(marker.read_text()) == {"product": "rapp-play-pokemon"}
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert server._public_status() == {
        "selected": False,
        "launching": False,
        "result": None,
    }


def test_upload_runtime_rejects_symlink_leaf(tmp_path):
    real_runtime = tmp_path / "real-runtime"
    real_runtime.mkdir()
    linked_runtime = tmp_path / "linked-runtime"
    linked_runtime.symlink_to(real_runtime, target_is_directory=True)

    with pytest.raises(RuntimeError, match="unsafe"):
        UploadServer(
            linked_runtime,
            0,
            lambda path: {"status": "success", "message": path.name},
        )
