"""Block detection and alert decisions — where a wrong call wakes someone at 4am."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pagewatch.fetch import classify, fetch  # noqa: E402
from pagewatch.monitor import Watch, check, run  # noqa: E402
from pagewatch.notify import Alert  # noqa: E402
from pagewatch.state import Store  # noqa: E402

FIX = Path(__file__).resolve().parent.parent / "fixtures"


class Recorder:
    """Captures alerts instead of sending them."""
    def __init__(self):
        self.alerts: list[Alert] = []

    def send(self, alert: Alert) -> bool:
        self.alerts.append(alert)
        return True


def watch(name="w", fixture="v1.html", **kw):
    return Watch(name=name, url=f"file://{FIX / fixture}",
                 selector=".product-price, .stock", **kw)


def store(tmp_path):
    return Store.load(tmp_path / "state.json")


# ---------- block detection ----------

def test_cloudflare_challenge_is_detected():
    assert classify((FIX / "blocked.html").read_text()) == "cloudflare"


def test_normal_page_is_not_flagged():
    assert classify((FIX / "v1.html").read_text()) == ""


def test_waf_marker_detected():
    assert classify("<html>" + "x" * 400 + "awswaf</html>") == "waf"


def test_empty_response_flagged():
    assert classify("") == "empty"


def test_small_page_is_allowed_when_threshold_lowered():
    """A fixed length floor would false-positive on legitimately small pages."""
    tiny = "<html><body><div class='p'>9.99</div></body></html>"
    assert classify(tiny, min_body=1000) == "too_short"
    assert classify(tiny, min_body=10) == ""


# ---------- alert decisions ----------

def test_first_run_captures_baseline_without_a_change_alert(tmp_path):
    rec = Recorder()
    r = check(watch(), store(tmp_path), [rec])
    assert r.status == "first"
    assert [a.kind for a in rec.alerts] == ["first"]


def test_noise_only_difference_does_not_alert(tmp_path):
    """v2 moves the timestamp, view counter and session id. Nothing else."""
    st = store(tmp_path)
    check(watch(fixture="v1.html"), st, [])
    rec = Recorder()
    r = check(watch(fixture="v2.html"), st, [rec])
    assert r.status == "unchanged"
    assert rec.alerts == []


def test_real_change_alerts(tmp_path):
    st = store(tmp_path)
    check(watch(fixture="v1.html"), st, [])
    rec = Recorder()
    r = check(watch(fixture="v3.html"), st, [rec])
    assert r.status == "changed"
    assert len(rec.alerts) == 1
    assert "199.99" in rec.alerts[0].summary


def test_block_page_is_never_reported_as_a_change(tmp_path):
    """The bug this whole module exists to prevent: a WAF page looks utterly
    different from the real one, and a naive monitor calls that a change."""
    st = store(tmp_path)
    check(watch(fixture="v1.html"), st, [])
    rec = Recorder()
    r = check(watch(fixture="blocked.html", alert_on_block_after=99), st, [rec])
    assert r.status == "blocked"
    assert rec.alerts == []


def test_sustained_blocking_does_eventually_alert(tmp_path):
    """Silence must not be mistaken for 'nothing changed'."""
    st = store(tmp_path)
    check(watch(fixture="v1.html"), st, [])
    rec = Recorder()
    w = watch(fixture="blocked.html", alert_on_block_after=2)
    check(w, st, [rec])
    assert rec.alerts == []
    check(w, st, [rec])
    assert [a.kind for a in rec.alerts] == ["blocked"]


def test_blocked_response_does_not_overwrite_baseline(tmp_path):
    st = store(tmp_path)
    check(watch(fixture="v1.html"), st, [])
    before = st.get("w").digest
    check(watch(fixture="blocked.html", alert_on_block_after=99), st, [])
    assert st.get("w").digest == before


def test_missing_selector_reports_error_not_change(tmp_path):
    w = Watch(name="w", url=f"file://{FIX / 'v1.html'}", selector=".nope")
    r = check(w, store(tmp_path), [])
    assert r.status == "error"


def test_one_broken_watch_does_not_stop_the_others(tmp_path):
    cfg = {"watches": [
        {"name": "bad", "url": "file:///nonexistent/page.html"},
        {"name": "good", "url": f"file://{FIX / 'v1.html'}",
         "selector": ".product-price"},
    ]}
    results = run(cfg, store(tmp_path), [])
    assert {r.watch: r.status for r in results}["good"] == "first"
    assert len(results) == 2


def test_state_survives_a_reload(tmp_path):
    p = tmp_path / "state.json"
    st = Store.load(p)
    check(watch(fixture="v1.html"), st, [])
    st.save()
    again = Store.load(p)
    assert again.get("w").digest
    rec = Recorder()
    assert check(watch(fixture="v2.html"), again, [rec]).status == "unchanged"


def test_missing_file_is_an_error_not_a_crash():
    assert fetch("file:///definitely/not/here.html").ok is False
