"""Fetching, with the check that stops false alerts.

A block page is the failure mode that ruins monitoring specifically. The site
answers a WAF challenge or a rate-limit interstitial, it returns 200 OK, the
content is completely different from last time — and a naive monitor reports
"the page changed!" at 4am. It didn't. You got blocked.

So every response is classified before it is ever compared.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("pagewatch.fetch")

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36")

BLOCK_MARKERS: list[tuple[str, str]] = [
    ("waf",        r"awswaf|aws-waf|_incapsula_|perimeterx|datadome"),
    ("cloudflare", r"attention required!\s*\|\s*cloudflare|cf-challenge|checking your browser"),
    ("captcha",    r"g-recaptcha|hcaptcha\.com/captcha|verify you are human|i'm not a robot"),
    ("rate_limit", r"too many requests|rate limit exceeded|unusual traffic"),
    ("denied",     r"access denied|request blocked|you have been blocked"),
]
_COMPILED = [(k, re.compile(p, re.I)) for k, p in BLOCK_MARKERS]

# A length floor is a weak signal on its own — plenty of legitimate pages are
# small (status pages, minimal sites, API-ish endpoints). This is set low enough
# to catch only obvious error stubs, and is overridable per watch. The strong
# signal is the marker list above.
MIN_BODY = 200


@dataclass
class Response:
    ok: bool
    html: str = ""
    status: int = 0
    blocked: str = ""        # marker name, empty when clean
    error: str = ""
    elapsed: float = 0.0
    not_modified: bool = False   # server answered 304 to a conditional request
    etag: str = ""               # validators to send back next time
    last_modified: str = ""

    @property
    def usable(self) -> bool:
        """Only a clean, non-blocked response may be compared against history."""
        return self.ok and not self.blocked


def classify(html: str, min_body: int = MIN_BODY) -> str:
    """Return the block kind, or '' if the page looks like real content."""
    if not html:
        return "empty"
    for name, rx in _COMPILED:
        if rx.search(html):
            return name
    if len(html) < min_body:
        return "too_short"
    return ""


def fetch(url: str, timeout: float = 25.0, session=None,
          min_body: int = MIN_BODY, etag: str = "", last_modified: str = "") -> Response:
    """HTTP fetch. `file://` paths are supported so the demo runs offline.

    If `etag`/`last_modified` from a prior fetch are supplied, they are sent as
    conditional headers (If-None-Match / If-Modified-Since). A 304 Not Modified
    is returned cheaply as `not_modified=True` with no body — on rate-limited
    hosts (e.g. the GitHub bounty queues) a 304 does not count against the limit,
    so a poll that hasn't changed is nearly free.
    """
    t0 = time.time()

    if url.startswith("file://"):
        p = Path(url[7:])
        try:
            html = p.read_text(encoding="utf-8")
        except OSError as exc:
            return Response(ok=False, error=str(exc), elapsed=time.time() - t0)
        return Response(ok=True, html=html, status=200,
                        blocked=classify(html, min_body),
                        elapsed=time.time() - t0)

    try:
        import requests
    except ImportError:
        return Response(ok=False, error="requests not installed")

    headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    s = session or requests.Session()
    try:
        r = s.get(url, timeout=timeout, headers=headers)
    except Exception as exc:                       # noqa: BLE001
        return Response(ok=False, error=f"{type(exc).__name__}: {exc}",
                        elapsed=time.time() - t0)

    new_etag = r.headers.get("ETag", "") or etag
    new_lastmod = r.headers.get("Last-Modified", "") or last_modified

    if r.status_code == 304:
        # unchanged since last poll — nothing to classify or compare
        return Response(ok=True, status=304, not_modified=True,
                        etag=new_etag, last_modified=new_lastmod,
                        elapsed=time.time() - t0)

    blocked = classify(r.text, min_body)
    if r.status_code in (403, 429) and not blocked:
        blocked = "rate_limit" if r.status_code == 429 else "denied"

    if blocked:
        log.warning("blocked response, not comparing",
                    extra={"url": url, "kind": blocked, "status": r.status_code})

    return Response(ok=r.status_code < 400, html=r.text, status=r.status_code,
                    blocked=blocked, etag=new_etag, last_modified=new_lastmod,
                    elapsed=time.time() - t0)
