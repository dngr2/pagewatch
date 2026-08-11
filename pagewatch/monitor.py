"""Orchestration: fetch, extract, compare, decide whether that deserves an alert."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import notify
from .extract import diff_summary, extract
from .fetch import fetch
from .notify import Alert
from .state import Store

log = logging.getLogger("pagewatch.monitor")


@dataclass
class Watch:
    name: str
    url: str
    selector: str | None = None
    ignore: list[str] | None = None
    alert_on_block_after: int = 3   # consecutive blocks before telling a human
    min_body: int = 200            # below this a response is treated as a stub

    @classmethod
    def from_dict(cls, d: dict) -> "Watch":
        return cls(
            name=d["name"], url=d["url"],
            selector=d.get("selector"),
            ignore=d.get("ignore") or [],
            alert_on_block_after=int(d.get("alert_on_block_after", 3)),
            min_body=int(d.get("min_body", 200)),
        )


@dataclass
class Result:
    watch: str
    status: str          # changed | unchanged | first | blocked | error
    summary: str = ""
    digest: str = ""
    stripped: list[str] | None = None


def load_config(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def check(w: Watch, store: Store, channels: list[notify.Channel] | None = None) -> Result:
    """Run one watch and alert if it warrants one."""
    channels = channels or []
    resp = fetch(w.url, min_body=w.min_body)

    if not resp.ok and resp.error:
        log.error("fetch failed", extra={"watch": w.name, "error": resp.error})
        return Result(w.name, "error", resp.error)

    if resp.blocked:
        streak = store.record_block(w.name)
        log.warning("blocked — comparison skipped",
                    extra={"watch": w.name, "kind": resp.blocked, "streak": streak})
        # One block is noise; a sustained streak means the watch is dead and
        # silence would be mistaken for "nothing changed".
        if streak >= w.alert_on_block_after:
            notify.dispatch(channels, Alert(
                w.name, w.url,
                f"blocked {streak}× in a row ({resp.blocked}) — monitoring is not working",
                kind="blocked"))
        return Result(w.name, "blocked", resp.blocked)

    ex = extract(resp.html, w.selector, w.ignore)
    if ex.empty:
        log.warning("selector matched nothing",
                    extra={"watch": w.name, "selector": w.selector})
        return Result(w.name, "error", f"selector matched nothing: {w.selector}")

    prev = store.get(w.name)
    first = not prev.digest
    changed = (not first) and ex.digest != prev.digest

    if first:
        store.record(w.name, ex.digest, ex.text, changed=False)
        notify.dispatch(channels, Alert(
            w.name, w.url, f"now watching — baseline captured ({ex.matched} node(s))",
            kind="first"))
        return Result(w.name, "first", ex.text[:160], ex.digest, ex.stripped)

    if changed:
        summary = diff_summary(prev.text, ex.text)
        store.record(w.name, ex.digest, ex.text, changed=True)
        notify.dispatch(channels, Alert(w.name, w.url, summary, kind="change"))
        return Result(w.name, "changed", summary, ex.digest, ex.stripped)

    store.record(w.name, ex.digest, ex.text, changed=False)
    return Result(w.name, "unchanged", "", ex.digest, ex.stripped)


def run(config: dict, store: Store,
        channels: list[notify.Channel] | None = None) -> list[Result]:
    channels = channels if channels is not None else notify.build(config)
    results = []
    for raw in config.get("watches", []):
        w = Watch.from_dict(raw)
        try:
            results.append(check(w, store, channels))
        except Exception as exc:                    # noqa: BLE001
            # One broken watch must never stop the others.
            log.exception("watch raised", extra={"watch": w.name})
            results.append(Result(w.name, "error", str(exc)))
    store.save()
    return results
