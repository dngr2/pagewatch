"""Command line.

    python -m pagewatch --demo                 # offline, shows both behaviours
    python -m pagewatch --config watches/example.yaml
    python -m pagewatch --config w.yaml --loop 300
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from . import notify
from .monitor import Store, load_config, run

log = logging.getLogger("pagewatch")

ICON = {"changed": "●", "unchanged": "·", "first": "○", "blocked": "▲", "error": "✕"}


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )


def demo() -> int:
    """Offline demonstration of the two things that make this non-trivial.

    Run 1 vs 2: only the timestamp moved. A naive diff would alert. This does not.
    Run 2 vs 3: the price moved. That is a real change, and it alerts.
    """
    root = Path(__file__).resolve().parent.parent
    fixtures = root / "fixtures"
    state = root / ".demo-state.json"
    state.unlink(missing_ok=True)

    store = Store.load(state)
    channels = [notify.ConsoleChannel()]
    stages = [
        ("v1", "baseline"),
        ("v2", "same price, timestamp and view counter moved"),
        ("v3", "price changed 249.99 -> 199.99"),
        ("blocked", "site returns a WAF challenge"),
    ]

    for name, note in stages:
        cfg = {"watches": [{
            "name": "demo-product",
            "url": f"file://{fixtures / (name + '.html')}",
            "selector": ".product-price, .stock",
            "alert_on_block_after": 1,
        }]}
        print(f"\n\033[38;5;244m── fetch {name}: {note}\033[0m")
        for r in run(cfg, store, channels):
            icon = ICON.get(r.status, "?")
            extra = f"   stripped: {', '.join(r.stripped)}" if r.stripped else ""
            print(f"  {icon} {r.status}{extra}")
        time.sleep(0.4)

    state.unlink(missing_ok=True)
    print("\n\033[38;5;244mNote what did NOT alert: run 2. The page changed, "
          "the watched value did not.\033[0m")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pagewatch",
                                description="Alert only when a page actually changes")
    p.add_argument("--config", "-c", help="YAML file of watches")
    p.add_argument("--state", default="state.json", help="where last-seen state lives")
    p.add_argument("--demo", action="store_true", help="offline demonstration")
    p.add_argument("--loop", type=int, metavar="SECONDS",
                   help="keep running, checking every N seconds")
    p.add_argument("--once", action="store_true", help="single pass (default)")
    p.add_argument("--log-level", default="WARNING")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)

    if args.demo:
        return demo()

    if not args.config:
        print("need --config FILE or --demo", file=sys.stderr)
        return 2

    cfg = load_config(args.config)
    store = Store.load(args.state)
    channels = notify.build(cfg)

    def pass_once() -> None:
        for r in run(cfg, store, channels):
            print(f"  {ICON.get(r.status,'?')} {r.watch:<24} {r.status}"
                  f"{'  ' + r.summary[:70] if r.summary else ''}")

    if args.loop:
        print(f"watching {len(cfg.get('watches', []))} page(s) every {args.loop}s — ctrl-c to stop")
        try:
            while True:
                pass_once()
                time.sleep(args.loop)
        except KeyboardInterrupt:
            print("\nstopped")
    else:
        pass_once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
