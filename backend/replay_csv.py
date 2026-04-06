"""Replay failed webhooks from TradingView CSV alert log.

Usage:
    python replay_csv.py [--dry-run] [--api-url http://localhost:8000]

Parses the CSV, extracts payloads from failed rows, and POSTs them to
the appropriate endpoint (screener or trade webhook).
"""

import csv
import json
import sys
import httpx

CSV_PATH = "../TradingView_Alerts_Log_2026-04-06.csv"
API_URL = "http://localhost:8000/api"

DRY_RUN = "--dry-run" in sys.argv
for arg in sys.argv[1:]:
    if arg.startswith("--api-url"):
        API_URL = arg.split("=", 1)[1] if "=" in arg else sys.argv[sys.argv.index(arg) + 1]


def main():
    failed = []
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = row.get("Webhook status", "")
            if "422" in status or "timed out" in status:
                desc = row.get("Description", "")
                # Extract JSON payload from the Description field
                # The payload is between the first { and the last }
                start = desc.find("{")
                end = desc.rfind("}")
                if start >= 0 and end > start:
                    raw = desc[start:end + 1]
                    # Un-escape doubled quotes from CSV
                    raw = raw.replace('""', '"')
                    try:
                        payload = json.loads(raw)
                        failed.append({
                            "alert_id": row.get("Alert ID", ""),
                            "ticker": row.get("Ticker", ""),
                            "time": row.get("Time", ""),
                            "status": status.strip(),
                            "payload": payload,
                        })
                    except json.JSONDecodeError as e:
                        print(f"  SKIP (bad JSON): {row.get('Ticker', '?')} — {e}")

    print(f"\nFound {len(failed)} failed webhooks to replay")
    if DRY_RUN:
        print("DRY RUN — not sending anything\n")

    scanner = [f for f in failed if "indicator" in f["payload"]]
    trades = [f for f in failed if "indicator" not in f["payload"]]
    print(f"  Scanner alerts: {scanner and len(scanner) or 0}")
    print(f"  Trade signals:  {trades and len(trades) or 0}")
    print()

    replayed = 0
    errors = 0

    with httpx.Client(timeout=30) as client:
        for item in failed:
            payload = item["payload"]
            is_scanner = "indicator" in payload

            # All go to /webhook — the auto-router will forward scanner payloads
            url = f"{API_URL}/webhook"
            label = f"{item['ticker']} ({payload.get('indicator', payload.get('signal', '?'))})"

            if DRY_RUN:
                print(f"  [DRY] Would POST {label} to {url}")
                continue

            try:
                resp = client.post(url, json=payload)
                if resp.status_code < 300:
                    data = resp.json()
                    print(f"  ✓ {label} — {data.get('status', 'ok')}")
                    replayed += 1
                else:
                    print(f"  ✗ {label} — HTTP {resp.status_code}: {resp.text[:200]}")
                    errors += 1
            except Exception as e:
                print(f"  ✗ {label} — {e}")
                errors += 1

    if not DRY_RUN:
        print(f"\nDone: {replayed} replayed, {errors} errors")


if __name__ == "__main__":
    main()
