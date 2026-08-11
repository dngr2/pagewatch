"""Noise filtering is what separates this from `diff`. It gets the tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pagewatch.extract import denoise, diff_summary, extract  # noqa: E402

PAGE = """
<html><body>
  <div class="price">249.99 EUR</div>
  <div class="stock">In stock — 12 available</div>
  <p class="meta">Updated 2026-08-11 04:31:07 · 1843 views</p>
  <script>var t = Date.now();</script>
</body></html>
"""


def test_selector_narrows_to_the_watched_region():
    ex = extract(PAGE, ".price")
    assert "249.99" in ex.text
    assert "In stock" not in ex.text
    assert ex.matched == 1


def test_multiple_selectors_are_combined():
    ex = extract(PAGE, ".price, .stock")
    assert "249.99" in ex.text and "In stock" in ex.text
    assert ex.matched == 2


def test_scripts_are_dropped():
    """Inline JS changes on every render and is never what you're watching."""
    assert "Date.now" not in extract(PAGE).text


def test_timestamps_are_normalised_away():
    a, _ = denoise("Updated 2026-08-11 04:31:07")
    b, _ = denoise("Updated 2026-08-11 06:12:55")
    assert a == b


def test_view_counters_are_normalised_away():
    a, _ = denoise("1843 views")
    b, _ = denoise("2201 views")
    assert a == b


def test_session_tokens_are_normalised_away():
    a, _ = denoise("session c9f2a71b4e8d0a3f6b5c2e91d7a48f30")
    b, _ = denoise("session 4d1e8a90c7b23f5e6a0d9c81b3f7e254")
    assert a == b


def test_relative_times_are_normalised_away():
    a, _ = denoise("posted 3 minutes ago")
    b, _ = denoise("posted 47 minutes ago")
    assert a == b


def test_whitespace_reflow_is_not_a_change():
    assert denoise("a   b\n\nc")[0] == denoise("a b c")[0]


def test_real_change_survives_denoising():
    """The filter must not be so aggressive it hides the thing you're watching."""
    a, _ = denoise("249.99 EUR at 2026-08-11 04:31:07")
    b, _ = denoise("199.99 EUR at 2026-08-11 06:12:55")
    assert a != b


def test_digest_is_stable_for_equivalent_pages():
    p1 = PAGE
    p2 = PAGE.replace("04:31:07", "09:55:01").replace("1843", "9021")
    assert extract(p1, ".price, .stock").digest == extract(p2, ".price, .stock").digest


def test_custom_ignore_patterns_apply():
    ex1 = extract('<div class="p">Order #1001 ready</div>', ".p", ignore=[r"#\d+"])
    ex2 = extract('<div class="p">Order #2417 ready</div>', ".p", ignore=[r"#\d+"])
    assert ex1.digest == ex2.digest


def test_missing_selector_yields_empty():
    assert extract(PAGE, ".does-not-exist").empty


def test_diff_summary_points_at_what_moved():
    s = diff_summary("249.99 EUR In stock 12", "199.99 EUR In stock 3")
    assert "249.99" in s and "199.99" in s and "→" in s


def test_diff_summary_handles_first_observation():
    assert "first observation" in diff_summary("", "anything")
