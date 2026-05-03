from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_PATH = Path("_codex_state.json")
MAX_ENTRIES = 200


def _load_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def append_codex_state(
    cli: str, args: dict[str, Any], outputs: Iterable[str], metrics: dict[str, Any]
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "cli": cli,
        "args": args,
        "outputs": list(outputs),
        "metrics": metrics,
    }
    entries = _load_entries(STATE_PATH)
    entries.append(entry)
    if len(entries) > MAX_ENTRIES:
        entries = entries[-MAX_ENTRIES:]
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open("w", encoding="utf-8") as handle:
        for item in entries:
            handle.write(json.dumps(item, separators=(",", ":")) + "\n")


__all__ = ["append_codex_state"]
