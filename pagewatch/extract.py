"""Turn a page into the small string you actually care about.

This module is the whole reason naive monitoring fails. Diff two fetches of any
real page and it will differ every single time: timestamps, CSRF tokens, ad
slots, view counters, session ids, cache-busting query strings. A monitor built
on a whole-page diff fires constantly, and within a week the alerts are muted
and the tool is useless.

So: select the region that matters, then normalise the noise inside it.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

# Patterns that change on their own, without anything meaningful happening.
NOISE_PATTERNS: list[tuple[str, str]] = [
    (r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?\b", "<TS>"),      # ISO timestamps
    (r"\b\d{1,2}:\d{2}(:\d{2})?\s?(AM|PM|am|pm)?\b", "<TIME>"),      # clock times
    (r"\b[0-9a-f]{32,}\b", "<HASH>"),                                # tokens, etags
    (r"\bcsrf[_-]?token[\"'=:\s]+[\w-]+", "<CSRF>"),
    (r"\bsessionid[\"'=:\s]+[\w-]+", "<SESSION>"),
    (r"[?&](v|cb|_|ts|cache)=\d+", "<CACHEBUST>"),
    (r"\b\d+\s+(views?|visitors?|online|watching)\b", "<COUNTER>"),
    (r"\b(just now|\d+\s+(seconds?|minutes?|hours?|days?)\s+ago)\b", "<RELTIME>"),
]

_COMPILED = [(re.compile(p, re.I), r) for p, r in NOISE_PATTERNS]


@dataclass
class Extraction:
    text: str
    digest: str
    matched: int                    # how many nodes the selector hit
    stripped: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.text.strip()


def denoise(text: str, extra: list[str] | None = None) -> tuple[str, list[str]]:
    """Replace self-changing content with stable placeholders.

    Returns the cleaned text and a note of which rules actually fired, so the
    CLI can show *why* two visually different fetches were treated as equal.
    """
    fired: list[str] = []
    out = text
    rules = list(_COMPILED)
    for p in extra or []:
        rules.append((re.compile(p, re.I), "<CUSTOM>"))
    for rx, repl in rules:
        out, n = rx.subn(repl, out)
        if n:
            fired.append(f"{repl}×{n}")
    # collapse whitespace last: reflowed HTML is not a change
    out = re.sub(r"\s+", " ", out).strip()
    return out, fired


def extract(html: str, selector: str | None = None,
            ignore: list[str] | None = None) -> Extraction:
    """Pull the watched region out of a page and normalise it."""
    soup = BeautifulSoup(html, "html.parser")

    # Script and style content changes constantly and is never what you're watching.
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    if selector:
        nodes = soup.select(selector)
        raw = "\n".join(n.get_text(" ", strip=True) for n in nodes)
        matched = len(nodes)
    else:
        raw = soup.get_text(" ", strip=True)
        matched = 1

    text, fired = denoise(raw, ignore)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return Extraction(text=text, digest=digest, matched=matched, stripped=fired)


def diff_summary(old: str, new: str, width: int = 220) -> str:
    """A short, human-readable description of what moved.

    Deliberately not a unified diff — an alert that arrives on a phone needs to
    be readable in one glance, not scrolled.
    """
    if old == new:
        return "no change"
    if not old:
        return f"first observation: {new[:width]}"

    # find the first and last differing character to bound the changed region
    start = 0
    limit = min(len(old), len(new))
    while start < limit and old[start] == new[start]:
        start += 1
    end_o, end_n = len(old), len(new)
    while end_o > start and end_n > start and old[end_o - 1] == new[end_n - 1]:
        end_o -= 1
        end_n -= 1

    before = old[start:end_o].strip() or "(nothing)"
    after = new[start:end_n].strip() or "(nothing)"
    ctx = new[max(0, start - 40):start].strip()
    lead = f"…{ctx} " if ctx else ""
    return f"{lead}[{before[:width]}] → [{after[:width]}]"
