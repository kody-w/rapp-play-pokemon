"""Resolve and invoke the canonical RAPP brainstem cartridge loader."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Sequence

CARTRIDGE_FILENAME = "rapp_play_pokemon_agent.py"
CANONICAL_KERNEL_RELATIVE = Path(
    "cave/rapplications/rapp-installer/kernel"
)


def repository_root() -> Path:
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "agent.py").is_file():
        return source_root
    agent_spec = importlib.util.find_spec("agent")
    if agent_spec and agent_spec.origin:
        return Path(agent_spec.origin).resolve().parent
    return source_root


def default_brainstem_dir() -> Path:
    return repository_root() / ".rapp" / "RAPP" / CANONICAL_KERNEL_RELATIVE


def resolve_brainstem_dir(value: Path | None = None) -> Path:
    configured = value or (
        Path(os.environ["RAPP_BRAINSTEM_DIR"])
        if os.environ.get("RAPP_BRAINSTEM_DIR")
        else default_brainstem_dir()
    )
    directory = configured.expanduser().resolve()
    required = (
        directory / "brainstem.py",
        directory / "agents" / "basic_agent.py",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(
            "Canonical RAPP brainstem is not installed; run ./bootstrap.sh "
            f"(missing: {', '.join(missing)})"
        )
    return directory


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_cartridge(
    source: Path | None = None,
    brainstem_dir: Path | None = None,
) -> Path:
    source = (source or repository_root() / "agent.py").expanduser().resolve()
    if not source.is_file() or source.name != "agent.py" or source.is_symlink():
        raise RuntimeError(f"RAPP cartridge source is invalid: {source}")
    destination_dir = resolve_brainstem_dir(brainstem_dir) / "agents"
    destination = destination_dir / CARTRIDGE_FILENAME
    if destination.is_symlink():
        raise RuntimeError(f"Refusing symlink cartridge destination: {destination}")
    if destination.exists() and sha256(destination) == sha256(source):
        return destination

    temporary = destination.with_name(f".{CARTRIDGE_FILENAME}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with source.open("rb") as source_handle, os.fdopen(
            descriptor, "wb"
        ) as output:
            descriptor = -1
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    return destination


def load_brainstem(brainstem_dir: Path | None = None) -> ModuleType:
    directory = resolve_brainstem_dir(brainstem_dir)
    agents_dir = directory / "agents"
    os.environ["AGENTS_PATH"] = str(agents_dir)
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    module_path = directory / "brainstem.py"
    module_suffix = hashlib.sha256(str(module_path).encode()).hexdigest()[:16]
    module_name = f"_rapp_canonical_brainstem_{module_suffix}"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load canonical brainstem: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def discover_cartridge(
    source: Path | None = None,
    brainstem_dir: Path | None = None,
) -> Any:
    register_cartridge(source, brainstem_dir)
    brainstem = load_brainstem(brainstem_dir)
    agents = brainstem.load_agents()
    cartridge = agents.get("Pokemon")
    if cartridge is None:
        raise RuntimeError(
            "Canonical RAPP brainstem did not discover the Pokemon cartridge"
        )
    metadata = getattr(cartridge, "metadata", {})
    if metadata.get("name") != "Pokemon" or not callable(
        getattr(cartridge, "perform", None)
    ):
        raise RuntimeError("Discovered Pokemon cartridge violates the RAPP contract")
    return cartridge


def invoke(
    kwargs: dict[str, Any],
    source: Path | None = None,
    brainstem_dir: Path | None = None,
) -> str:
    return discover_cartridge(source, brainstem_dir).perform(**kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Register and verify agent.py with canonical RAPP brainstem"
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument("--brainstem-dir", type=Path)
    parser.add_argument("--smoke-runtime-dir", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cartridge = discover_cartridge(args.source, args.brainstem_dir)
        if args.smoke_runtime_dir:
            import json

            result = json.loads(
                cartridge.perform(
                    action="status",
                    runtime_dir=str(args.smoke_runtime_dir),
                )
            )
            if result.get("status") != "success":
                raise RuntimeError(f"Cartridge smoke invocation failed: {result}")
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}")
        return 1
    print("Canonical RAPP discovered and invoked cartridge: Pokemon")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
