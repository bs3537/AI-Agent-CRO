# Hermes VPS Backend — Update Instructions (June 8 2026)

These instructions are for the Codex agent making code edits to the Hermes VPS
backend. This file supersedes `HERMES_INSTRUCTIONS_JUNE7.MD` and covers all
changes made through June 8 2026. Pull the repo and work through every section.

---

## 1. Pull the latest repo code

```bash
git pull origin main
```

All changes described in this document are already committed to `main`.

---

## 2. What changed in this repo (do not re-implement — already done)

### 2a. `src/sma_monitor/news/fmp_client.py` — `fetch_quotes()` return type

The function now returns `dict[str, dict]` instead of `dict[str, float]`.

**Old shape:**
```python
{"AAPL": 195.0, "MSFT": 420.5}
```

**New shape:**
```python
{
  "AAPL": {"price": 195.0, "change_pct": -0.83},
  "MSFT": {"price": 420.5, "change_pct": 1.24}
}
```

`change_pct` is today's percentage move (e.g. `-0.83` means −0.83%), sourced
from FMP's `changesPercentage` field.

### 2b. `src/sma_monitor/api/routes/quotes.py` — full rewrite (Yahoo primary)

`GET /api/quotes` now uses **Yahoo Finance as the primary source**. FMP is
called in parallel as a cross-verification layer only when `FMP_API_KEY` is
set. Yahoo is attempted unconditionally — no FMP key required for quotes to work.

**Source fallback logic:**

| Condition | `source` value | `verified` |
|-----------|---------------|-----------|
| Yahoo ✓ + FMP ✓, prices agree ≤1% | `"yahoo+fmp"` | `true` |
| Yahoo ✓ + FMP ✓, prices diverge >1% | `"yahoo+fmp"` | `false` |
| Yahoo ✓, FMP key absent or unreachable | `"yahoo_only"` | `false` |
| Yahoo ✗, FMP ✓ | `"fmp_only"` | `false` |
| Yahoo ✗ + FMP ✗ (or both error) | `"error"` | — (empty quotes) |
| No holdings in DB | `"no_holdings"` | — (empty quotes) |

**Note:** `"no_key"` is no longer a possible `source` value. Yahoo runs
regardless of whether `FMP_API_KEY` is set. Any VPS code that checks for
`source == "no_key"` should treat `"error"` as the fully-degraded fallback instead.

**HTTP response shape:**
```json
{
  "quotes": {
    "AAPL": {"price": 195.0, "change_pct": -0.83, "verified": true},
    "MSFT": {"price": 420.5, "change_pct": 1.24, "verified": false}
  },
  "is_market_open": true,
  "source": "yahoo+fmp"
}
```

`change_pct` comes from Yahoo's `regularMarketChangePercent` when Yahoo is
available, or FMP's `changesPercentage` when Yahoo fails and FMP is the
fallback.

### 2c. Frontend — MV chip removed

The market-value (MV) chip has been removed from each holding tile. Only the
`+X.XX% today` daily-change chip remains as the intraday overlay. No backend
change is required for this.

---

## 3. VPS-specific code — required updates

Search for any code in the VPS that is **outside** this repo and touches quotes.
Use these grep patterns as your starting point:

```bash
grep -r "fetch_quotes"       /path/to/vps/code   # Python callers
grep -r "/api/quotes"        /path/to/vps/code   # HTTP clients hitting the endpoint
grep -r "quotes\["           /path/to/vps/code   # dict accesses that may assume float
grep -r '"no_key"'           /path/to/vps/code   # stale source checks to remove
grep -r '"source"'           /path/to/vps/code   # any other source string checks
```

### 3a. Python code that calls `fetch_quotes()` directly

Every call site that reads the result as a plain float must be updated:

```python
# OLD — breaks after the update
price = fetch_quotes(tickers, api_key=key)["AAPL"]          # was float, now dict

# NEW
quote  = fetch_quotes(tickers, api_key=key)["AAPL"]
price      = quote["price"]
change_pct = quote["change_pct"]   # today's % move; use if needed
```

If the call site only needs the price:

```python
prices = {t: v["price"] for t, v in fetch_quotes(tickers, api_key=key).items()}
```

### 3b. Code that calls `GET /api/quotes` over HTTP

Any VPS-side service that calls the `/api/quotes` endpoint and reads
`response["quotes"]["TICKER"]` as a number must be updated:

```python
# OLD
price = data["quotes"]["AAPL"]                   # was float

# NEW
price      = data["quotes"]["AAPL"]["price"]
change_pct = data["quotes"]["AAPL"]["change_pct"]
verified   = data["quotes"]["AAPL"]["verified"]  # True = Yahoo + FMP agree within 1%
```

### 3c. Any code that checks the `source` field

```python
# OLD — these are all stale:
if data["source"] == "fmp":
if data["source"] == "fmp+yahoo":
if data["source"] == "no_key":

# NEW — check for any successful quote-serving source:
if data["source"] in ("yahoo+fmp", "yahoo_only", "fmp_only"):
    # quotes dict is populated and usable

# Fully degraded (no quotes available):
if data["source"] in ("error", "no_holdings"):
    # quotes dict is empty — fall back to EOD data
```

---

## 4. No changes needed for

- `FMP_API_KEY` env var — still used, now only for FMP cross-verification.
  Quotes will still work (via Yahoo) if the key is absent.
- Polling interval — still 30 min on the frontend; the backend route is stateless.
- The nightly financials/price-history pipeline — uses separate FMP endpoints
  (`refresh_for_holdings`, `refresh_prices_for_holdings`), not `fetch_quotes`.
- Any code reading `pos.open_pnl` or `pos.market_value` — EOD stored values,
  unaffected by this change.

---

## 5. Restart the backend after pulling

```bash
git pull origin main
.venv/bin/pip install -r requirements.txt -e .   # no new deps expected
sudo systemctl restart sma-monitor               # or however the service is managed
```

Verify:
```bash
curl http://localhost:8000/api/quotes | python3 -m json.tool
```

**Expected outside market hours** (Yahoo returns stale/last-close prices,
`is_market_open` is false so the frontend ignores them):
```json
{"quotes": {}, "is_market_open": false, "source": "yahoo_only"}
```

**Expected during market hours with holdings:**
```json
{
  "quotes": {
    "AAPL": {"price": 195.0, "change_pct": -0.83, "verified": true}
  },
  "is_market_open": true,
  "source": "yahoo+fmp"
}
```

If `source` is still `"fmp"` (old flat-float format), the pull did not take —
retry `git pull` and check for merge conflicts.

If `source` is `"yahoo_only"`, FMP is unreachable or key is missing — quotes
still work, just unverified.

If `source` is `"fmp_only"`, Yahoo is being rate-limited (rare at 30-min
cadence) — FMP fallback is serving quotes correctly.

---

## 6. Full interface contract summary

| Item | Before (original) | After (June 8) |
|------|------------------|----------------|
| `fetch_quotes()` return | `dict[str, float]` | `dict[str, dict]` with `price`, `change_pct` |
| `/api/quotes` quotes value | `float` | `{price, change_pct, verified}` |
| Primary quote source | FMP | Yahoo Finance (`regularMarketPrice`) |
| `change_pct` origin | FMP `changesPercentage` | Yahoo `regularMarketChangePercent` (FMP fallback) |
| FMP key required for quotes | Yes | No — Yahoo runs unconditionally |
| `source` success values | `"fmp"`, `"fmp+yahoo"` | `"yahoo+fmp"`, `"yahoo_only"`, `"fmp_only"` |
| `source` degraded value | `"no_key"`, `"fmp_error"` | `"error"` |
| MV chip on tiles | present | removed (frontend only) |
