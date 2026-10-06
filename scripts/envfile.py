"""Minimal .env reader/writer shared by the scripts (no dependencies).

Keeps comments and order when updating, so .env stays readable for people and agents.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV = REPO / ".env"
EXAMPLE = REPO / ".env.example"


def read(path: Path = ENV) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def settings() -> dict[str, str]:
    """.env.example defaults overlaid with .env."""
    return {**read(EXAMPLE), **read(ENV)}


def update(changes: dict[str, str], path: Path = ENV) -> None:
    """Set keys in .env (created from .env.example if missing), keeping everything else."""
    if not path.is_file():
        path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    lines = path.read_text(encoding="utf-8").splitlines()
    pending = dict(changes)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in pending:
            lines[i] = f"{key}={pending.pop(key)}"
    lines += [f"{k}={v}" for k, v in pending.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
