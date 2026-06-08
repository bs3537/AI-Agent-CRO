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

### 2b. `src/sma_monitor/api/routes/quotes.py` — rewritten (Yahoo primary)

`GET /api/quotes` now uses **Yahoo Finance as the primary source** with FMP
as a cross-verification fallback. Source fallback order:

| Condition | `source` value |
|-----------|---------------|
| Yahoo succeeds + FMP succeeds (prices agree ≤1%) | `"yahoo+fmp"`, `verified: true` |
| Yahoo succeeds + FMP succeeds (prices diverge >1%) | `"yahoo+fmp"`, `verified: false` |
| Yahoo succeeds, FMP unreachable | `"yahoo_only"`, `verified: false` |
| Yahoo fails, FMP succeeds | `"fmp_only"`, `verified: false` |
| Both fail | `"error"`, empty quotes |

Yahoo Finance supplies both `price` (`regularMarketPrice`) and `change_pct`
(`regularMarketChangePercent`) directly. FMP is only used to cross-check the
price; if it agrees, `verified` is set to `true`.

**HTTP response shape (unchanged from previous update):**
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

Possible `source` values: `"yahoo+fmp"`, `"yahoo_only"`, `"fmp_only"`,
`"no_holdings"`, `"error"`. Note: `"no_key"` is no longer returned — Yahoo
is now attempted even when the FMP key is absent.

### 2c. Frontend — MV chip removed

The market-value chip has been removed from each holding tile. Only the
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
grep -r "changesPercentage"  /path/to/vps/code   # any existing FMP field reads
grep -r '"source"'           /path/to/vps/code   # source string checks
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
verified   = data["quotes"]["AAPL"]["verified"]  # True = Yahoo + FMP agree within 1%
```

### 3c. Any code that checks the `source` field

Source strings have changed. Update any equality checks:

```python
# OLD
if data["source"] == "fmp":
if data["source"] == "fmp+yahoo":
if data["source"] == "fmp_only":

# NEW — Yahoo is now primary; check for any successful source:
if data["source"] in ("yahoo+fmp", "yahoo_only", "fmp_only"):
    # quotes are available
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

Expected output when market is closed:
```json
{"quotes": {}, "is_market_open": false, "source": "yahoo_only"}
```
(Yahoo still returns quotes when market is closed; the frontend ignores them
because `is_market_open` is false. If Yahoo is also unavailable after hours,
`source` will be `"error"` with empty quotes — that is also fine.)

Expected output during market hours with holdings:
```json
{
  "quotes": {
    "AAPL": {"price": 195.0, "change_pct": -0.83, "verified": true}
  },
  "is_market_open": true,
  "source": "yahoo+fmp"
}
```

If `source` is `"yahoo+fmp"` and most `verified` are `true`, everything is
working correctly. If `source` is `"yahoo_only"`, FMP is unreachable (check
the FMP API key). If `source` is `"fmp_only"`, Yahoo is being rate-limited
(rare at this polling frequency — retry in a few minutes).

---

## 6. Summary of interface contract

| Item | Before (original) | After (this update) |
|------|------------------|---------------------|
| `fetch_quotes()` return | `dict[str, float]` | `dict[str, dict]` with `price`, `change_pct` |
| `/api/quotes` quotes value | `float` | `{price, change_pct, verified}` |
| Primary quote source | FMP | Yahoo Finance |
| `/api/quotes` source (success) | `"fmp"` | `"yahoo+fmp"`, `"yahoo_only"`, or `"fmp_only"` |
| `change_pct` origin | FMP `changesPercentage` | Yahoo `regularMarketChangePercent` (FMP fallback) |
| MV chip on tiles | present | removed |
