"""Fail when tracked/public files contain game artifacts or likely credentials."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROHIBITED_SUFFIXES = {
    ".avi",
    ".gb",
    ".gbc",
    ".jpeg",
    ".jpg",
    ".mkv",
    ".mov",
    ".mp4",
    ".png",
    ".ram",
    ".rom",
    ".rtc",
    ".sav",
    ".srm",
    ".state",
    ".webp",
}
PROHIBITED_NAMES = {
    ".copilot_session",
    ".copilot_token",
    ".env",
    "brain.json",
    "config.json",
    "control.jsonl",
    "desired.json",
    "runtime-owner.json",
    "setup-auth.json",
    "status.json",
    "supervisor.json",
    "viewer-auth.json",
}
SECRET_PATTERNS = {
    "GitHub token": re.compile(
        rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"
    ),
    "private key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "AWS access key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "personal macOS path": re.compile(rb"/Users/[A-Za-z0-9._-]+/"),
    "personal Windows path": re.compile(
        rb"[A-Za-z]:\\\\Users\\\\[A-Za-z0-9._ -]+\\\\"
    ),
}


def public_files() -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout:
        return [
            ROOT / item.decode("utf-8")
            for item in result.stdout.split(b"\0")
            if item
        ]
    ignored = {
        ".git",
        ".rapp",
        ".rapp-ci",
        ".references",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".venv-dev",
        ".work-smoke",
        "__pycache__",
        "build",
        "dist",
    }
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and not any(
            part in ignored or part.endswith(".egg-info") for part in path.parts
        )
    ]


def main() -> int:
    failures: list[str] = []
    for path in public_files():
        relative = path.relative_to(ROOT)
        lower_name = path.name.lower()
        if path.suffix.lower() in PROHIBITED_SUFFIXES:
            failures.append(f"prohibited artifact extension: {relative}")
            continue
        if lower_name in PROHIBITED_NAMES:
            failures.append(f"prohibited runtime/secret filename: {relative}")
            continue
        if path.is_symlink():
            failures.append(f"tracked symlink is not allowed: {relative}")
            continue
        try:
            data = path.read_bytes()
        except OSError as error:
            failures.append(f"cannot scan {relative}: {error}")
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                failures.append(f"possible {label}: {relative}")
    if failures:
        print("\n".join(sorted(failures)))
        return 1
    print(f"Public artifact scan passed ({len(public_files())} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
