"""Command-line interface that dispatches through canonical RAPP brainstem."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Sequence

from .brainstem import invoke

ACTIONS = (
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
    "upload",
)
BUTTONS = ("a", "b", "start", "select", "up", "down", "left", "right")
DEFAULT_RUNTIME_DIR = Path.home() / ".rapp" / "rapp-play-pokemon"


def load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read config {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError("Config must contain one JSON object")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Pokemon cartridge through canonical RAPP brainstem"
    )
    parser.add_argument("action", nargs="?", choices=ACTIONS, default="start")
    parser.add_argument("button", nargs="?", choices=BUTTONS)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--rom", dest="rom_path")
    parser.add_argument(
        "--runtime-dir",
        type=Path,
        help="Private state directory (default: ~/.rapp/rapp-play-pokemon)",
    )
    parser.add_argument("--port", type=int)
    parser.add_argument("--setup-port", type=int, default=8764)
    parser.add_argument("--clip-minutes", type=float)
    parser.add_argument("--model")
    parser.add_argument("--decision-timeout", type=int)
    parser.add_argument("--startup-timeout", type=float)
    parser.add_argument("--max-clips", type=int)
    parser.add_argument("--max-states", type=int)
    parser.add_argument("--max-storage-gb", type=float)
    parser.add_argument("--min-free-gb", type=float)
    parser.add_argument("--visible", action="store_true", default=None)
    parser.add_argument("--no-open-viewer", action="store_false", dest="open_viewer")
    parser.add_argument("--no-open-browser", action="store_false", dest="open_browser")
    parser.add_argument("--no-resume", action="store_false", dest="resume")
    parser.set_defaults(open_viewer=None, open_browser=True, resume=None)
    return parser


def _configured(
    args: argparse.Namespace,
    config: dict[str, Any],
    name: str,
    default: Any = None,
) -> Any:
    argument = getattr(args, name, None)
    if argument is not None:
        return argument
    return config.get(name, default)


def agent_kwargs(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    runtime = _configured(args, config, "runtime_dir", str(DEFAULT_RUNTIME_DIR))
    kwargs: dict[str, Any] = {
        "action": args.action,
        "runtime_dir": str(Path(str(runtime)).expanduser()),
    }
    if args.action == "press":
        if not args.button:
            raise RuntimeError("press requires a button")
        kwargs["button"] = args.button
    if args.action not in {"start", "upload"}:
        return kwargs

    values = {
        "rom_path": _configured(
            args,
            config,
            "rom_path",
            os.environ.get("RAPP_PLAY_POKEMON_ROM"),
        ),
        "port": _configured(args, config, "port", 8765),
        "clip_minutes": _configured(args, config, "clip_minutes", 10),
        "model": _configured(args, config, "model", "gpt-5.6-sol"),
        "decision_timeout": _configured(args, config, "decision_timeout", 180),
        "startup_timeout": _configured(args, config, "startup_timeout", 180),
        "max_clips": _configured(args, config, "max_clips", 200),
        "max_states": _configured(args, config, "max_states", 256),
        "max_storage_gb": _configured(args, config, "max_storage_gb", 20),
        "min_free_gb": _configured(args, config, "min_free_gb", 2),
        "visible": _configured(args, config, "visible", False),
        "open_viewer": _configured(args, config, "open_viewer", True),
        "resume": _configured(args, config, "resume", True),
    }
    kwargs.update({key: value for key, value in values.items() if value is not None})
    return kwargs


def run(argv: Sequence[str] | None = None) -> tuple[int, dict[str, Any]]:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        kwargs = agent_kwargs(args, config)
        if args.action == "upload":
            raise RuntimeError("upload is a server action; use the CLI entry point")
        raw = invoke(kwargs)
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise RuntimeError("Pokemon cartridge returned a non-object response")
    except (ImportError, json.JSONDecodeError, OSError, RuntimeError) as error:
        result = {"status": "error", "message": str(error)}
    return (0 if result.get("status") == "success" else 1), result


def main(argv: Sequence[str] | None = None) -> int:
    parsed = build_parser().parse_args(argv)
    if parsed.action == "upload":
        try:
            config = load_config(parsed.config)
            kwargs = agent_kwargs(parsed, config)
            kwargs["action"] = "start"
            runtime_dir = Path(str(kwargs["runtime_dir"])).expanduser()
            from .upload import serve_upload

            return serve_upload(
                runtime_dir=runtime_dir,
                port=parsed.setup_port,
                open_browser=parsed.open_browser,
                start_callback=lambda rom: json.loads(
                    invoke({**kwargs, "rom_path": str(rom)})
                ),
            )
        except (OSError, RuntimeError, ValueError) as error:
            print(json.dumps({"status": "error", "message": str(error)}, indent=2))
            return 1

    exit_code, result = run(argv)
    print(json.dumps(result, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
