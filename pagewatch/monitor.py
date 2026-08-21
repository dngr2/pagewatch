"""Orchestration: fetch, extract, compare, decide whether that deserves an alert."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
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
    """Run one watch and alert if it warrants one (fetch + process)."""
    channels = channels or []
    prev0 = store.get(w.name)
    resp = fetch(w.url, min_body=w.min_body, etag=prev0.etag, last_modified=prev0.last_modified)
    return _process(w, resp, store, channels)


def _process(w: Watch, resp, store: Store, channels: list[notify.Channel]) -> Result:
    """Post-fetch handling: classify, compare, persist, alert. Touches the Store,
    so callers must run this serially (the Store is not thread-safe)."""
    if resp.not_modified:
        # server confirmed nothing changed (304) — cheap, no body to compare
        prev = store.get(w.name)
        store.record(w.name, prev.digest, prev.text, changed=False,
                     etag=resp.etag, last_modified=resp.last_modified)
        return Result(w.name, "unchanged", "", prev.digest)

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
        channels: list[notify.Channel] | None = None,
        max_workers: int = 8) -> list[Result]:
    """Run all watches. Fetches run concurrently (the slow, I/O-bound part), then
    results are processed serially so the non-thread-safe Store and alert delivery
    stay single-threaded. Total wall time collapses from the sum of the fetch
    latencies to roughly the slowest single fetch."""
    channels = channels if channels is not None else notify.build(config)
    watches = [Watch.from_dict(raw) for raw in config.get("watches", [])]
    if not watches:
        store.save()
        return []

    # Snapshot prior validators SERIALLY before fanning out (Store is not thread-safe).
    prior = {w.name: (store.get(w.name).etag, store.get(w.name).last_modified) for w in watches}

    # Group by host so watches on the SAME site run serially (respecting that site's
    # rate limit / politeness), while DIFFERENT hosts are probed in parallel — the
    # latency win without hammering any single host.
    from urllib.parse import urlsplit
    groups: dict[str, list[Watch]] = {}
    for w in watches:
        host = (urlsplit(w.url).hostname or w.url).lower()
        groups.setdefault(host, []).append(w)

    def _probe_group(group: list[Watch]):
        out = []
        for w in group:
            et, lm = prior[w.name]
            try:
                out.append((w, fetch(w.url, min_body=w.min_body, etag=et, last_modified=lm), None))
            except Exception as exc:                # noqa: BLE001
                out.append((w, None, exc))
        return out

    workers = max(1, min(max_workers, len(groups)))
    fetched = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for group_result in pool.map(_probe_group, groups.values()):
            fetched.extend(group_result)

    # Process serially: extract/compare/persist/alert all touch shared state.
    results = []
    for w, resp, exc in fetched:
        if exc is not None:
            log.exception("fetch raised", extra={"watch": w.name})
            results.append(Result(w.name, "error", str(exc)))
            continue
        try:
            results.append(_process(w, resp, store, channels))
        except Exception as exc:                    # noqa: BLE001
            # One broken watch must never stop the others.
            log.exception("watch raised", extra={"watch": w.name})
            results.append(Result(w.name, "error", str(exc)))
    store.save()
    return results
