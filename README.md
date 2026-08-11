# pagewatch

Watch a web page. Get alerted **only when the thing you care about actually changes**.

```bash
pip install -r requirements.txt
python -m pagewatch --demo
```

No config, no network, no signup. The demo shows the two behaviours that make
this harder than `diff`.

## Why this isn't just diffing a page

Diff two fetches of any real page and they differ **every single time**:
timestamps, view counters, session ids, CSRF tokens, ad slots, cache-busting
query strings. A monitor built on a whole-page diff alerts constantly, and
within a week it's muted and useless.

Then there's the failure that ruins monitoring specifically: **a block page
looks exactly like a change.** The site answers with a WAF challenge, returns
`200 OK`, the content is completely different from last time — and a naive
monitor wakes you at 4am to report that the page changed. It didn't. You got
blocked.

pagewatch handles both.

## The demo, verbatim

```
── fetch v1: baseline
  ○ first        now watching — baseline captured (2 node(s))

── fetch v2: same price, timestamp and view counter moved
  · unchanged

── fetch v3: price changed 249.99 -> 199.99
  ● changed      [249.99 EUR In stock — 12] → [199.99 EUR In stock — 3]

── fetch blocked: site returns a WAF challenge
  ▲ blocked      cloudflare — comparison skipped
```

Run 2 is the important line. The page genuinely changed — new timestamp, new
view count, new session token. The **watched value** did not, so nothing fired.

Run 4 is the other one. A naive monitor reports a change. This reports a
problem, and refuses to overwrite the baseline with a block page.

## Configuration

```yaml
notify:
  console: true
  webhook: https://hooks.slack.com/services/...    # Slack, Discord, n8n, Make
  telegram:
    token: "123456:ABC..."
    chat_id: "987654321"

watches:
  - name: widget-price
    url: https://example.com/products/widget
    selector: ".product-price, .stock-status"      # watch this, not the page

  - name: order-status
    url: https://example.com/orders
    selector: "#order-table"
    ignore:
      - 'Order #\d+'                               # your own volatile content
    alert_on_block_after: 5                        # this site rate-limits a lot
```

```bash
python -m pagewatch --config watches/example.yaml            # one pass
python -m pagewatch --config watches/example.yaml --loop 600 # every 10 minutes
```

Or drive it from cron and let the state file handle continuity:

```
*/10 * * * * cd /opt/pagewatch && .venv/bin/python -m pagewatch -c watches/live.yaml
```

## Decisions worth arguing about

**The selector is the feature.** Watching `.product-price` instead of the page
is what makes alerts meaningful. If you don't set one, you're diffing the whole
page and you will regret it.

**Noise filters run inside the selected region too**, because even a price block
often carries a "last updated" line. Timestamps, clock times, hashes, session
ids, view counters and relative times ("3 minutes ago") are normalised to
placeholders before hashing.

**A block page never overwrites the baseline.** If it did, the next *real* fetch
would look like a change and you'd get a second false alert on top of the first.

**One block is silence, a streak is an alert.** A single 429 is noise. Five in a
row means the watch is dead, and a dead watch that stays quiet is indistinguishable
from a page that never changes. `alert_on_block_after` controls the line.

**The length floor is deliberately low and overridable.** A fixed "pages are at
least N bytes" rule false-positives on legitimately small pages — status
endpoints, minimal sites. Block *markers* are the strong signal; length only
catches obvious stubs. Set `min_body` per watch when you know better.

**State writes are atomic** (temp file then rename), so a crash mid-write can't
truncate the file and silently reset every baseline.

**One broken watch never stops the others.** Exceptions are caught per watch.

## Layout

```
pagewatch/
  fetch.py     HTTP + block classification
  extract.py   CSS selection, noise normalisation, diff summaries
  state.py     atomic JSON persistence
  notify.py    console / webhook / telegram
  monitor.py   orchestration and alert decisions
  cli.py       command line and the offline demo
watches/       YAML watch definitions
fixtures/      the four pages the demo walks through
tests/         29 tests
```

## Tests

```bash
python -m pytest tests/ -q
```

```
.............................    [100%]
29 passed in 0.31s
```

They cover the places where being wrong costs you a 4am phone call: that noise
is filtered but real changes survive it, that a block page is never reported as
a change, that sustained blocking *does* eventually alert, that a blocked fetch
doesn't overwrite the baseline, and that one broken watch doesn't stop the rest.

## Related

- [stealth-scrape](https://github.com/dngr2/stealth-scrape) — the scraping
  framework this borrows its block-classification approach from
- [resilient-telegram-bot](https://github.com/dngr2/resilient-telegram-bot) —
  pairs with the Telegram channel here

## License

MIT
