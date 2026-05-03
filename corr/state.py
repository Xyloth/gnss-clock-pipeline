from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

MAX_STATE_LINES = 200


@dataclass
class YearSummary:
    ts: datetime
    system: str
    year: int
    eps_total: int
    s3: int
    s2: int
    s1: int
    s0: int
    blue_included: bool
    src_days: int
    skipped_days: int
    duration_s: float
    version: str = "v31"

    def to_json_line(self) -> str:
        payload = {
            "ts": self.ts.isoformat(),
            "sys": self.system,
            "year": self.year,
            "eps_total": self.eps_total,
            "s3": self.s3,
            "s2": self.s2,
            "s1": self.s1,
            "s0": self.s0,
            "blue_included": self.blue_included,
            "src_days": self.src_days,
            "skipped_days": self.skipped_days,
            "duration_s": self.duration_s,
            "version": self.version,
        }
        return json.dumps(payload, separators=(",", ":"))


def append_state(path: Path, summary: YearSummary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = summary.to_json_line()
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    _truncate_if_needed(path)


def _truncate_if_needed(path: Path) -> None:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        lines = fh.readlines()
    if len(lines) <= MAX_STATE_LINES:
        return
    trimmed = lines[-MAX_STATE_LINES:]
    with path.open("w", encoding="utf-8") as fh:
        fh.writelines(trimmed)
