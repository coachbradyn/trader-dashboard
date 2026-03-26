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
| `indicator` | string | Indicator name (your label)          | `"KALMAN_BREAKOUT"`  |
| `value`     | number | Indicator value (NO quotes)          | `225.5`              |

### Optional Fields

| Field      | Type   | Description                          | Example              |
|------------|--------|--------------------------------------|----------------------|
| `signal`   | string | `"bullish"`, `"bearish"`, `"neutral"`| `"bullish"`          |
| `tf`       | string | Timeframe                            | `"240"`              |
| `time`     | number | Unix timestamp in ms                 | `1773768600000`      |
| `metadata` | object | Any extra data (freeform JSON)       | `{"source":"TV"}`    |

> **Extra fields are silently ignored.**

### Indicator Alert Template (copy into TradingView)

```json
{"key":"YOUR_API_KEY","ticker":"{{ticker}}","indicator":"YOUR_INDICATOR_NAME","value":{{close}},"signal":"bullish","tf":"{{interval}}","time":{{timenow}}}
```

### Example with metadata

```json
{"key":"YOUR_API_KEY","ticker":"{{ticker}}","indicator":"LMA_MOMENTUM","value":{{close}},"signal":"bullish","tf":"{{interval}}","time":{{timenow}},"metadata":{"source":"TradingView","condition":"LMA crossover confirmed"}}
```

---

## Common Gotchas

### 1. Unquoted numbers
`{{close}}`, `{{timenow}}`, and `{{strategy.order.contracts}}` resolve to **numbers**. Do NOT wrap them in quotes:

```
CORRECT:  "price":{{close}}
WRONG:    "price":"{{close}}"
```

### 2. Quoted strings
`{{ticker}}` and `{{interval}}` resolve to **strings**. They MUST be inside quotes:

```
CORRECT:  "ticker":"{{ticker}}"
WRONG:    "ticker":{{ticker}}
```

### 3. `{{interval}}` returns minutes
TradingView returns the interval in minutes as a string. 4h = `"240"`, 1h = `"60"`, 1D = `"1D"`.

### 4. `{{timenow}}` is milliseconds
Returns Unix timestamp in milliseconds (e.g., `1773768600000`). The backend accepts both number and string format.

### 5. Indicators vs Strategies — available placeholders
Both indicators and strategies support: `{{ticker}}`, `{{close}}`, `{{open}}`, `{{high}}`, `{{low}}`, `{{volume}}`, `{{interval}}`, `{{timenow}}`, `{{exchange}}`.

**Only strategies** support: `{{strategy.order.action}}`, `{{strategy.order.contracts}}`, `{{strategy.order.price}}`, `{{strategy.position_size}}`.

### 6. Extra fields are OK
Both endpoints use `extra = "ignore"`. Send whatever you want from Pine Script — unknown fields are silently dropped. You will NOT get a 422 for extra fields.

### 7. Two different endpoints
- Strategy trades → `POST /api/webhook`
- Indicator alerts → `POST /api/screener/webhook`

Do NOT send indicator alerts to `/api/webhook` or strategy trades to `/api/screener/webhook`. The schemas are different and you'll get a 422.

---

## Quick Test (curl)

### Test strategy webhook
```bash
curl -X POST https://trader-dashboard-production-02bd.up.railway.app/api/webhook \
  -H "Content-Type: application/json" \
  -d '{"key":"YOUR_KEY","trader":"YOUR_SLUG","signal":"entry","dir":"long","ticker":"AAPL","price":225.50,"tf":"240","time":1773768600000}'
```

### Test screener webhook
```bash
curl -X POST https://trader-dashboard-production-02bd.up.railway.app/api/screener/webhook \
  -H "Content-Type: application/json" \
  -d '{"key":"YOUR_KEY","ticker":"AAPL","indicator":"TEST","value":225.50,"signal":"bullish","tf":"240"}'
```

Both should return a JSON response with `"status":"ok"`. If you get a 422, check that required fields are present and number fields are not quoted.
