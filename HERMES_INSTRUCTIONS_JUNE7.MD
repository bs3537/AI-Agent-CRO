# Hermes VPS Backend — Update Instructions (June 7 2026)

These instructions are for the Codex agent making code edits to the Hermes VPS
backend. The Replit repo (this repo) has been updated; some of those changes land
automatically when you `git pull`, but the VPS may have additional backend code
**outside** this repo that needs matching updates. Work through every section below.

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

`change_pct` is today's percentage move (e.g. `-0.83` means −0.83%).
It comes from FMP's `changesPercentage` field in the `/stable/quote` response.

### 2b. `src/sma_monitor/api/routes/quotes.py` — rewritten

`GET /api/quotes` now:
1. Calls FMP `/stable/quote` for all held tickers → gets `price` + `change_pct`.
2. Calls Yahoo Finance v7 API (no key, ~15-20 min delayed) for the same tickers
   to cross-verify prices.
3. If FMP and Yahoo agree within 1% → `verified: true`.
4. If they diverge by >1% → Yahoo price wins, `verified: false`, warning logged.
5. If Yahoo is unreachable → falls back to FMP-only, `verified: false`.

**New HTTP response shape:**
```json
{
  "quotes": {
    "AAPL": {"price": 195.0, "change_pct": -0.83, "verified": true},
    "MSFT": {"price": 420.5, "change_pct": 1.24, "verified": false}
  },
  "is_market_open": true,
  "source": "fmp+yahoo"
}
```

Possible `source` values: `"fmp+yahoo"`, `"fmp_only"`, `"no_key"`,
`"no_holdings"`, `"fmp_error"`, `"error"`.

---

## 3. VPS-specific code — required updates

Search for any code in the VPS that is **outside** this repo and touches quotes.
Use these grep patterns as your starting point:

```bash
grep -r "fetch_quotes"       /path/to/vps/code   # Python callers
grep -r "/api/quotes"        /path/to/vps/code   # HTTP clients hitting the endpoint
grep -r "quotes\["           /path/to/vps/code   # dict accesses that may assume float
grep -r "changesPercentage"  /path/to/vps/code   # any existing FMP field reads
```

### 3a. Python code that calls `fetch_quotes()` directly

Every call site that reads the result as a plain float must be updated:

```python
# OLD — breaks after the update
price = fetch_quotes(tickers, api_key=key)["AAPL"]          # was float, now dict

# NEW
quote = fetch_quotes(tickers, api_key=key)["AAPL"]
price      = quote["price"]
change_pct = quote["change_pct"]   # today's % move; use if needed
```

If the call site only needs the price and doesn't care about `change_pct`:

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
verified   = data["quotes"]["AAPL"]["verified"]  # True = FMP + Yahoo agree
```

### 3c. Any code that checks `source == "fmp"`

The success source string changed. Update equality checks:

```python
# OLD
if data["source"] == "fmp":

# NEW — source is now "fmp+yahoo" when Yahoo succeeds, "fmp_only" when it fails
if data["source"] in ("fmp+yahoo", "fmp_only"):
```

---

## 4. No changes needed for

- The FMP API key — same key, same env var (`FMP_API_KEY`).
- Polling interval — still 30 min on the frontend; the backend route is stateless.
- The nightly financials/price-history pipeline — it uses separate FMP endpoints
  (`refresh_for_holdings`, `refresh_prices_for_holdings`), not `fetch_quotes`.
- Any code reading `pos.open_pnl` or `pos.market_value` — those are EOD stored
  values and are unaffected.

---

## 5. Restart the backend after pulling

```bash
git pull origin main
# install any new deps if requirements changed (none expected for this update)
.venv/bin/pip install -r requirements.txt -e .
# restart however the VPS service is managed, e.g.:
sudo systemctl restart sma-monitor
```

Verify with:
```bash
curl http://localhost:8000/api/quotes | python3 -m json.tool
```

Expected output when market is closed (no quotes, but new source strings):
```json
{"quotes": {}, "is_market_open": false, "source": "fmp_only"}
```

Expected output during market hours with holdings:
```json
{
  "quotes": {
    "AAPL": {"price": 195.0, "change_pct": -0.83, "verified": true}
  },
  "is_market_open": true,
  "source": "fmp+yahoo"
}
```

If `source` is `"fmp+yahoo"` and all `verified` are `true`, everything is working
correctly. If `source` is `"fmp_only"`, Yahoo Finance is unreachable from the VPS
(unlikely but possible — check outbound HTTP to `query1.finance.yahoo.com`).

---

## 6. Summary of interface contract

| Item | Before | After |
|------|--------|-------|
| `fetch_quotes()` return | `dict[str, float]` | `dict[str, dict]` with `price`, `change_pct` |
| `/api/quotes` quotes value | `float` | `{price, change_pct, verified}` |
| `/api/quotes` source (success) | `"fmp"` | `"fmp+yahoo"` or `"fmp_only"` |
| Yahoo verification | not present | cross-checks FMP, overrides on >1% divergence |
| Daily change % | not available | `change_pct` field on every quote |
