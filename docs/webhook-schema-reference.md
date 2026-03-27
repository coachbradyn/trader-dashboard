# Webhook Schema Reference

Base URL: `https://trader-dashboard-production-02bd.up.railway.app`

---

## 1. Strategy Trade Webhook

**Endpoint:** `POST /api/webhook`

Used by Pine Script **strategies** (entry/exit signals). Creates or closes trades in the database.

### Required Fields

| Field    | Type   | Description                          | Example            |
|----------|--------|--------------------------------------|--------------------|
| `key`    | string | Your API key (from Settings page)    | `"abc123..."`      |
| `trader` | string | Trader slug (from Settings page)     | `"henry-v36"`      |
| `signal` | string | `"entry"` or `"exit"`                | `"entry"`          |
| `dir`    | string | `"long"` or `"short"`                | `"long"`           |
| `ticker` | string | Ticker symbol                        | `"NVDA"`           |
| `price`  | number | Entry or exit price (NO quotes)      | `188.07`           |

### Optional Fields

| Field            | Type        | Description                              | Example         |
|------------------|-------------|------------------------------------------|-----------------|
| `qty`            | number      | Share quantity                           | `391.58`        |
| `sig`            | number      | Signal strength (0-100)                  | `85.5`          |
| `adx`            | number      | ADX value                                | `32.1`          |
| `atr`            | number      | ATR value                                | `0.24`          |
| `stop`           | number      | Stop loss price                          | `8.27`          |
| `exit_reason`    | string      | Why trade was closed (exit only)         | `"Slope Flat"`  |
| `pnl_pct`        | number      | P&L percentage (exit only)               | `4.07`          |
| `bars_in_trade`  | number (int)| Bars held (exit only)                    | `12`            |
| `tf`             | string      | Timeframe                                | `"240"`         |
| `time`           | number      | Unix timestamp in ms (can be string)     | `1773768600000` |
| `profile`        | string      | Strategy profile name                    | `"aggressive"`  |

> **Extra fields are silently ignored.** You can include `win_pct`, `total_trades`, `profit_factor`, etc. in your Pine Script alert — they won't cause errors.

### Entry Alert Template (copy into TradingView)

```json
{"key":"YOUR_API_KEY","trader":"YOUR_TRADER_SLUG","signal":"entry","dir":"{{strategy.order.action}}","ticker":"{{ticker}}","price":{{close}},"qty":{{strategy.order.contracts}},"sig":0,"adx":0,"atr":0,"stop":0,"tf":"{{interval}}","time":{{timenow}}}
```

### Exit Alert Template (copy into TradingView)

```json
{"key":"YOUR_API_KEY","trader":"YOUR_TRADER_SLUG","signal":"exit","dir":"{{strategy.order.action}}","ticker":"{{ticker}}","price":{{close}},"qty":{{strategy.order.contracts}},"exit_reason":"strategy_exit","pnl_pct":0,"bars_in_trade":0,"tf":"{{interval}}","time":{{timenow}}}
```

### Combined Entry + Exit Template (single alert)

If your Pine Script uses `alert()` or `alertcondition()` with conditional logic:

```json
{"key":"YOUR_API_KEY","trader":"YOUR_TRADER_SLUG","signal":"{{strategy.order.action}}","dir":"{{strategy.order.action}}","ticker":"{{ticker}}","price":{{close}},"qty":{{strategy.order.contracts}},"tf":"{{interval}}","time":{{timenow}}}
```

> **Note on `signal` field:** The backend expects `"entry"` or `"exit"`. If your strategy uses `strategy.entry()` and `strategy.close()`, `{{strategy.order.action}}` resolves to `"buy"`/`"sell"` — NOT `"entry"`/`"exit"`. You may need to hardcode `"signal":"entry"` and `"signal":"exit"` in separate alert messages, or handle the mapping in your Pine Script.

---

## 2. Screener/Indicator Webhook

**Endpoint:** `POST /api/screener/webhook`

Used by Pine Script **indicators** (alert signals for the screener heatmap). Does NOT create trades — adds indicator alerts for Henry to analyze.

### Required Fields

| Field       | Type   | Description                          | Example              |
|-------------|--------|--------------------------------------|----------------------|
| `key`       | string | Your API key (from Settings page)    | `"abc123..."`        |
| `ticker`    | string | Ticker symbol                        | `"NVDA"`             |

That's it. Only `key` and `ticker` are truly required. Everything else has sensible defaults.

### Optional Fields

| Field       | Type           | Default     | Description                                  | Example              |
|-------------|----------------|-------------|----------------------------------------------|----------------------|
| `indicator` | string         | `"UNKNOWN"` | Indicator name (your label)                  | `"KALMAN_BREAKOUT"`  |
| `value`     | number or null | `null`      | Indicator value — can be number, string, or omitted | `225.5`        |
| `signal`    | string         | `"neutral"` | Signal direction (see normalization below)   | `"bullish"`          |
| `tf`        | string         | `null`      | Timeframe — auto-normalized (see below)      | `"240"` → `"4h"`    |
| `time`      | number/string  | `null`      | Unix timestamp ms — accepts string or number | `1773768600000`      |
| `metadata`  | object         | `null`      | Any extra data (freeform JSON)               | `{"source":"TV"}`    |

> **Extra fields are silently ignored.** Send anything from Pine Script — it won't break.

### Auto-Normalization (you don't have to worry about formatting)

**Ticker:** `"NASDAQ:NVDA"` → `"NVDA"`. Exchange prefixes are automatically stripped.

**Signal:** These all work — the backend normalizes them:
| You Send | Backend Stores |
|----------|---------------|
| `"bullish"`, `"bull"`, `"long"`, `"buy"`, `"up"`, `"1"`, `"true"` | `"bullish"` |
| `"bearish"`, `"bear"`, `"short"`, `"sell"`, `"down"`, `"-1"`, `"false"` | `"bearish"` |
| anything else / omitted | `"neutral"` |

**Timeframe:** TradingView sends minutes as strings. Auto-mapped:
| You Send | Backend Stores |
|----------|---------------|
| `"1"` | `"1m"` |
| `"5"` | `"5m"` |
| `"15"` | `"15m"` |
| `"60"` | `"1h"` |
| `"240"` | `"4h"` |
| `"D"` or `"1D"` | `"1D"` |
| anything else | kept as-is |

**Value:** Can be a number, a string (`"225.5"`), empty string, `null`, or completely omitted. All are fine.

**Time:** Can be a number (`1773768600000`) or string (`"1773768600000"`). Both work.

### Minimal Alert Template (just the basics)

```json
{"key":"YOUR_API_KEY","ticker":"{{ticker}}","signal":"bullish"}
```

### Standard Alert Template (recommended)

```json
{"key":"YOUR_API_KEY","ticker":"{{ticker}}","indicator":"YOUR_INDICATOR_NAME","value":{{close}},"signal":"bullish","tf":"{{interval}}","time":{{timenow}}}
```

### Full Alert Template (with metadata)

```json
{"key":"YOUR_API_KEY","ticker":"{{ticker}}","indicator":"LMA_MOMENTUM","value":{{close}},"signal":"bullish","tf":"{{interval}}","time":{{timenow}},"metadata":{"source":"TradingView","condition":"LMA crossover confirmed"}}
```

### Pine Script `alert()` Example

```pinescript
// In your indicator code:
if bullishCondition
    alert('{"key":"YOUR_API_KEY","ticker":"' + syminfo.ticker + '","indicator":"MY_INDICATOR","value":' + str.tostring(close) + ',"signal":"bullish","tf":"' + timeframe.period + '"}', alert.freq_once_per_bar)
```

### Pine Script `alertcondition()` Example

```pinescript
// Define the condition:
alertcondition(bullishCondition, title="Bullish Signal", message='{"key":"YOUR_API_KEY","ticker":"{{ticker}}","indicator":"MY_INDICATOR","value":{{close}},"signal":"bullish","tf":"{{interval}}","time":{{timenow}}}')
```

Then in TradingView: create alert → select the condition → check "Webhook URL" → paste your Railway URL → the message auto-fills from `alertcondition()`.

---

## Common Gotchas

### 1. Numbers vs strings — be relaxed
The backend now coerces types automatically. `"value":"225.5"` and `"value":225.5` both work. `"time":"1773768600000"` and `"time":1773768600000` both work. Don't stress about quoting.

### 2. `{{ticker}}` may include exchange
TradingView's `{{ticker}}` resolves to `"NVDA"` but `syminfo.tickerid` includes the exchange like `"NASDAQ:NVDA"`. **Both work** — the backend strips the exchange prefix automatically.

### 3. `{{interval}}` returns minutes
TradingView returns the interval in minutes as a string. 4h = `"240"`, 1h = `"60"`, 1D = `"1D"`. The backend auto-converts to human-readable: `"240"` → `"4h"`.

### 4. `{{timenow}}` is milliseconds
Returns Unix timestamp in milliseconds (e.g., `1773768600000`). Accepts both number and string.

### 5. Indicators vs Strategies — available placeholders
Both indicators and strategies support: `{{ticker}}`, `{{close}}`, `{{open}}`, `{{high}}`, `{{low}}`, `{{volume}}`, `{{interval}}`, `{{timenow}}`, `{{exchange}}`.

**Only strategies** support: `{{strategy.order.action}}`, `{{strategy.order.contracts}}`, `{{strategy.order.price}}`, `{{strategy.position_size}}`.

### 6. Extra fields are OK
Both endpoints use `extra = "ignore"`. Send `win_pct`, `total_trades`, `profit_factor`, or any other fields from Pine Script — they're silently dropped. No 422 errors.

### 7. Two different endpoints
- Strategy trades → `POST /api/webhook`
- Indicator alerts → `POST /api/screener/webhook`

Do NOT send indicator alerts to `/api/webhook` or strategy trades to `/api/screener/webhook`.

---

## Quick Test (curl)

### Test strategy webhook
```bash
curl -X POST https://trader-dashboard-production-02bd.up.railway.app/api/webhook \
  -H "Content-Type: application/json" \
  -d '{"key":"YOUR_KEY","trader":"YOUR_SLUG","signal":"entry","dir":"long","ticker":"AAPL","price":225.50,"tf":"240","time":1773768600000}'
```

### Test screener webhook (minimal)
```bash
curl -X POST https://trader-dashboard-production-02bd.up.railway.app/api/screener/webhook \
  -H "Content-Type: application/json" \
  -d '{"key":"YOUR_KEY","ticker":"AAPL","signal":"bullish"}'
```

### Test screener webhook (full)
```bash
curl -X POST https://trader-dashboard-production-02bd.up.railway.app/api/screener/webhook \
  -H "Content-Type: application/json" \
  -d '{"key":"YOUR_KEY","ticker":"NASDAQ:AAPL","indicator":"TEST","value":"225.50","signal":"bull","tf":"240"}'
```

All should return `{"status":"ok",...}`. The screener webhook now only requires `key` + `ticker` — everything else is optional or auto-normalized.
