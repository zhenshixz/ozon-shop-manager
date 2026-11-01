#!/usr/bin/env python3
"""Send two clearly marked mock notifications, without calling or modifying Ozon."""
import argparse
import json
from pathlib import Path
import tempfile
import time

import monitor


def main():
    parser = argparse.ArgumentParser(description="模拟一次上新和一次出单，发送到已配置的 pushplus 群组")
    parser.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    args = parser.parse_args()
    cfg = monitor.load_config(args.config)
    if not cfg["pushplus"]["topic"]:
        raise monitor.MonitorError("未配置接收群组，不发送模拟消息")
    live = monitor.Store(cfg["data_dir"])
    push = monitor.Pushplus(cfg["pushplus"], monitor.Transport(cfg["http_timeout_seconds"]))
    shop = {**cfg["shops"][0], "name": cfg["shops"][0]["name"] + "（模拟）"}
    receipts = []
    # Isolate all fake goods, orders and batches from the production monitoring DB.
    with tempfile.TemporaryDirectory(prefix="simulation-", dir=cfg["data_dir"]) as directory:
        for kind, label in (("product", "模拟上新"), ("order", "模拟出单")):
            sandbox = monitor.Store(Path(directory) / kind)
            now = time.time()
            if kind == "product":
                sandbox.products_snapshot(shop, {}, {}, now - 1)
                item = {"SIM-PRODUCT-001": {"offer_id": "SIM-新品-001"}}
                sandbox.products_snapshot(shop, item, item, now)
            else:
                sandbox.orders_snapshot(shop, "fbs", [{
                    "order_id": "SIM-ORDER-001", "order_number": "SIM-ORDER-001",
                    "posting_number": "SIM-ORDER-001-1", "created_at": monitor.stamp(now),
                }], now, now - 2)
            batch = sandbox.make_batch(now)
            if batch is None:
                raise monitor.MonitorError("模拟事件没有生成通知，已停止发送")
            title = "【" + label + "】" + batch["title"]
            body = ("【模拟测试，非真实商品或订单】\n"
                    "以下数据仅测试通知样式，无需发货或处理，真实后台不会出现这些编号。\n\n"
                    + batch["content"].replace("详情请到 Ozon 或妙手 PC 后台查看。", "本条为模拟消息。"))
            deadline = time.monotonic() + 60
            while not live.reserve_send(cfg["pushplus"], time.time()):
                if time.monotonic() >= deadline:
                    raise monitor.MonitorError("未取得发送额度，可能已达今日预算；未继续发送")
                time.sleep(1)
            receipt = push.send(title, body)
            receipts.append({"kind": kind, "title": title, "receipt": receipt, "submitted_at": monitor.stamp()})
            # Store the audit after every accepted message, even if a later submission fails.
            audit = Path(cfg["data_dir"]) / "last-simulation.json"
            audit.write_text(json.dumps(receipts, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{label}：pushplus 已受理，流水号 {receipt}", flush=True)
    print("共两条模拟提醒已提交；请在两部手机上确认接收。未修改 Ozon 或正式商品/订单记录。", flush=True)


if __name__ == "__main__":
    try:
        main()
    except monitor.MonitorError as error:
        raise SystemExit(str(error))
