"""Where the last-seen state lives.

JSON on disk, one file for all watches. A database would be overkill for
something a person runs on a small VPS, and a file you can open in an editor
is a feature when you are debugging why an alert did or did not fire.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path("state.json")


@dataclass
class Seen:
    digest: str = ""
    text: str = ""
    checked_at: str = ""
    changed_at: str = ""
    checks: int = 0
    changes: int = 0
    blocked_streak: int = 0
    etag: str = ""              # HTTP validators for conditional requests
    last_modified: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Store:
    path: Path = field(default=DEFAULT_PATH)
    data: dict[str, Seen] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "Store":
        p = Path(path)
        if not p.exists():
            return cls(path=p)
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt state file must not stop monitoring; worst case is one
            # "first observation" alert per watch.
            return cls(path=p)
        fields = set(Seen().to_dict())
        # tolerate state written by a different version: ignore unknown keys
        return cls(path=p, data={k: Seen(**{kk: vv for kk, vv in v.items() if kk in fields}) for k, v in raw.items()})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: v.to_dict() for k, v in self.data.items()},
                                  indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)      # atomic: a crash mid-write cannot truncate state

    def get(self, name: str) -> Seen:
        return self.data.setdefault(name, Seen())

    def record(self, name: str, digest: str, text: str, changed: bool,
               etag: str | None = None, last_modified: str | None = None) -> Seen:
        s = self.get(name)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        s.checks += 1
        s.checked_at = now
        s.blocked_streak = 0
        if changed:
            s.changes += 1
            s.changed_at = now
        s.digest = digest
        s.text = text[:4000]        # enough to diff against, not enough to bloat
        if etag is not None:
            s.etag = etag
        if last_modified is not None:
            s.last_modified = last_modified
        return s

    def record_block(self, name: str) -> int:
        s = self.get(name)
        s.blocked_streak += 1
        s.checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return s.blocked_streak
