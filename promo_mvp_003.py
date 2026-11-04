"""One-off promo removal probe for Luo003; no price-changing endpoints.

Run on the deployment host with its private config.json. Every API request and
response is saved to a private directory outside the source tree before output.
Inspect first, then use one of the narrowly scoped operations below.
"""

import argparse
import collections
import datetime as dt
import json
import os
from pathlib import Path
import urllib.error
import urllib.request


ACTION_IDS = (1977747, 4400847)
ROOT = Path("/opt/ozon-push-monitor")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect", "remove-pending", "test-current", "remove-current"))
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.report_dir.is_absolute() or ROOT in args.report_dir.parents:
        parser.error("Reports must be outside the deployment source tree")
    os.umask(0o077)
    args.report_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    shops = json.loads((ROOT / "config.json").read_text())["shops"]
    matches = [s for s in shops if s["id"] == "shop03" and s["name"] == "Luo003" and s.get("enabled", True)]
    if len(matches) != 1:
        raise RuntimeError("Expected exactly one enabled Luo003/shop03")
    shop = matches[0]
    sequence = 0

    def call(path, payload=None):
        nonlocal sequence
        # This tool deliberately has no activate/update/import-price operation.
        allowed = {"/v1/actions", "/v2/actions/products", "/v2/actions/auto-add/products/list"}
        if args.operation == "remove-pending":
            allowed.add("/v2/actions/auto-add/products/delete")
        if args.operation in ("test-current", "remove-current"):
            allowed.add("/v2/actions/products/deactivate")
        if path not in allowed or (payload and payload.get("action_id") not in ACTION_IDS):
            raise RuntimeError("Request outside the authorized probe scope")
        sequence += 1
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        report = args.report_dir / f"{stamp}-{sequence:03}.json"
        event = {"shop": shop["name"], "path": path, "payload": payload, "status": "request-prepared"}
        report.write_text(json.dumps(event, ensure_ascii=False, indent=2))
        request = urllib.request.Request(
            "https://api-seller.ozon.ru" + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Client-Id": str(shop["client_id"]), "Api-Key": shop["api_key"], "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                event.update(status=response.status, response=json.load(response))
        except urllib.error.HTTPError as error:
            event.update(status=error.code, response=error.read().decode())
        except Exception as error:
            event.update(status="outcome-unknown", error=type(error).__name__)
            report.write_text(json.dumps(event, ensure_ascii=False, indent=2))
            raise RuntimeError("Request outcome unknown: inspect saved request and read back; do not retry blindly") from error
        report.write_text(json.dumps(event, ensure_ascii=False, indent=2))
        if event["status"] != 200:
            print(json.dumps(event, ensure_ascii=False), flush=True)
            raise RuntimeError("API rejected request; stopped without any price change")
        if path.endswith("/delete") or path.endswith("/deactivate"):
            print(json.dumps({"path": path, "response": event["response"]}, ensure_ascii=False), flush=True)
        return event["response"]

    def participants(action_id):
        result, cursor, seen = [], "", set()
        for _ in range(100):
            response = call("/v2/actions/products", {"action_id": action_id, "limit": 100, "last_id": cursor})
            page = response["products"]
            result.extend(page)
            if len(result) >= response["total"]:
                if len({p["id"] for p in result}) != len(result):
                    raise RuntimeError("Repeated product IDs")
                return result
            cursor = response.get("last_id")
            if not page or not cursor or cursor in seen:
                raise RuntimeError("Incomplete participants pagination")
            seen.add(cursor)
        raise RuntimeError("Too many participants pages")

    actions = {a["id"]: a for a in call("/v1/actions")["result"] if a["id"] in ACTION_IDS}
    if set(actions) != set(ACTION_IDS):
        raise RuntimeError("Both screenshot activities must still exist")
    for action_id in ACTION_IDS:
        current = participants(action_id)
        print(json.dumps({"action_id": action_id, "current": len(current), "add_modes": dict(collections.Counter(p.get("add_mode", "UNKNOWN") for p in current))}, ensure_ascii=False), flush=True)
        if args.operation in ("test-current", "remove-current") and current:
            ids = [p["id"] for p in current]
            if args.operation == "test-current":
                ids = ids[:1]
            for start in range(0, len(ids), 1000):
                call("/v2/actions/products/deactivate", {"action_id": action_id, "product_ids": ids[start:start + 1000]})
            print(json.dumps({"action_id": action_id, "current_after": len(participants(action_id))}), flush=True)
        if args.operation in ("inspect", "remove-pending"):
            for date in actions[action_id].get("auto_add_dates", []):
                pending, offset = [], 0
                for _ in range(100):
                    response = call("/v2/actions/auto-add/products/list", {"action_id": action_id, "auto_add_date": date, "limit": 100, "offset": offset})
                    pending.extend(response["products"])
                    if len(pending) >= response["total"]:
                        break
                    if not response["products"]:
                        raise RuntimeError("Incomplete pending pagination")
                    offset += len(response["products"])
                else:
                    raise RuntimeError("Too many pending pages")
                ids = [p["product_id"] for p in pending]
                if len(set(ids)) != len(ids):
                    raise RuntimeError("Repeated pending product IDs")
                print(json.dumps({"action_id": action_id, "pending_date": date, "pending": len(ids)}), flush=True)
                if args.operation == "remove-pending" and ids:
                    for start in range(0, len(ids), 1000):
                        call("/v2/actions/auto-add/products/delete", {"action_id": action_id, "auto_add_date": date, "product_ids": ids[start:start + 1000]})
                    after = call("/v2/actions/auto-add/products/list", {"action_id": action_id, "auto_add_date": date, "limit": 100, "offset": 0})
                    print(json.dumps({"action_id": action_id, "pending_after": after["total"]}), flush=True)
                    if after["total"]:
                        raise RuntimeError("Pending items remain; inspect saved response")
    print("Reports:", args.report_dir, flush=True)


if __name__ == "__main__":
    main()
