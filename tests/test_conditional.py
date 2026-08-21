"""Tests for ETag/conditional-request support and the parallel run pipeline."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pagewatch import monitor  # noqa: E402
from pagewatch.fetch import fetch  # noqa: E402
from pagewatch.state import Store  # noqa: E402


class FakeResp:
    def __init__(self, status, text="", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}


class FakeSession:
    """Records the headers sent and returns a canned response."""
    def __init__(self, resp):
        self.resp = resp
        self.sent_headers = None

    def get(self, url, timeout=None, headers=None):
        self.sent_headers = headers
        return self.resp


def test_fetch_sends_conditional_headers_when_validators_present():
    s = FakeSession(FakeResp(304, headers={"ETag": 'W/"abc"'}))
    r = fetch("https://example.com/x", session=s, etag='W/"abc"', last_modified="Mon")
    assert s.sent_headers["If-None-Match"] == 'W/"abc"'
    assert s.sent_headers["If-Modified-Since"] == "Mon"
    assert r.not_modified is True
    assert r.ok is True
    assert r.status == 304


def test_fetch_returns_validators_for_next_time():
    s = FakeSession(FakeResp(200, text="x" * 500, headers={"ETag": 'W/"v2"', "Last-Modified": "Tue"}))
    r = fetch("https://example.com/x", session=s)
    assert r.etag == 'W/"v2"'
    assert r.last_modified == "Tue"


def test_not_modified_is_treated_as_unchanged(tmp_path):
    store = Store(path=tmp_path / "s.json")
    w = monitor.Watch(name="q", url="https://example.com/q")
    # seed a baseline the 304 should preserve
    store.record("q", "digest123", "prior text", changed=False, etag='W/"e1"', last_modified="Mon")

    class Resp304:
        ok = True
        not_modified = True
        etag = 'W/"e1"'
        last_modified = "Mon"

    res = monitor._process(w, Resp304(), store, [])
    assert res.status == "unchanged"
    assert store.get("q").digest == "digest123"  # baseline untouched


def test_run_parallel_processes_all_watches(tmp_path):
    # three file:// watches; run() must fetch in parallel and process all serially
    fx = Path(__file__).resolve().parent.parent / "fixtures" / "v1.html"
    config = {"watches": [
        {"name": f"w{i}", "url": f"file://{fx}", "min_body": 1} for i in range(3)
    ]}
    store = Store(path=tmp_path / "s.json")
    results = monitor.run(config, store, channels=[])
    assert len(results) == 3
    assert {r.status for r in results} == {"first"}
