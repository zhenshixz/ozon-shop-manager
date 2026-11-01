#!/usr/bin/env python3
"""Interactive, server-local setup: secrets never need to enter a chat or shell history."""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import warnings

import monitor

ROOT = Path(__file__).resolve().parent
SERVICE = "ozon-push-monitor.service"


def secret(prompt):
    while True:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                value = getpass.getpass(prompt + "（输入隐藏，粘贴后回车）：").strip()
        except getpass.GetPassWarning:
            raise monitor.MonitorError("当前终端无法隐藏输入，请使用宝塔交互终端，不要把密钥写进命令") from None
        if value:
            return value
        print("这一项不能为空。")


def collect():
    print("\n先在 pushplus 创建私密群组，你和家人都加入，再输入下面的信息。")
    print("公众号账号登录、加入群组和密钥提供需要由你本人完成。")
    token = secret("pushplus Token")
    topic = ""
    while not topic:
        topic = input("pushplus 私密群组编码 topic：").strip()
    member = input("发送方的 pushplus 付费会员目前有效吗？[y/N]：").strip().lower() == "y"
    cfg = {"poll_seconds": 10, "workers": 10, "http_timeout_seconds": 8,
           "order_lookback_hours": 48, "data_dir": "data",
           "pushplus": {"token": token, "topic": topic, "secret_key": "",
                        "min_send_interval_seconds": 3 if member else 13,
                        "daily_request_budget": 1900 if member else 180}, "shops": []}
    print("\n最多填写 10 家店，可以先填一家；后续 Client ID 留空即结束。")
    clients = set()
    for index in range(1, 11):
        while True:
            client = input(f"第 {index} 家店 Client ID：").strip()
            if not client and cfg["shops"]:
                return cfg
            if client.isdigit() and client not in clients:
                break
            print("请填写未重复的数字 Client ID；至少需要一家店。")
        clients.add(client)
        name = input(f"店铺名称（回车使用 店铺{index}）：").strip() or f"店铺{index}"
        key = secret(f"{name} API Key")
        cfg["shops"].append({"id": f"shop{index:02}", "name": name, "enabled": True,
                             "client_id": client, "api_key": key})
    return cfg


def save_config(cfg, path):
    """Validate before atomic replacement; never leave a world-readable credential file."""
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(cfg, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        monitor.load_config(temporary)
        if os.name != "nt":
            import grp
            os.chown(temporary, 0, grp.getgrnam("ozon-monitor").gr_gid)
            os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    if os.name != "posix" or os.geteuid() != 0:
        print("请在服务器的宝塔 root 终端执行此向导。")
        return 2
    if subprocess.run(["systemctl", "is-active", "--quiet", SERVICE]).returncode == 0:
        print("监控服务已经运行，本向导未修改配置。")
        print("如需更换账号，请先联系维护者停止服务并备份现有配置，避免改变店铺去重标识。")
        return 0
    path = ROOT / "config.json"
    print("Ozon 微信提醒配置向导\n密钥只保存在这台服务器，不会发送到聊天或打印到日志。")
    reuse = path.exists() and input("发现已有配置，直接继续检查和启动？[Y/n]：").strip().lower() != "n"
    if not reuse:
        if path.exists():
            print("修改已有店铺时请保留原店铺顺序，id 与历史去重记录对应。")
        save_config(collect(), path)
    cfg = monitor.load_config(path)
    if not cfg["pushplus"]["topic"]:
        raise monitor.MonitorError("群组编码 topic 为空，尚不能同时通知两人；请重新配置")
    print(f"\n已准备 {len(cfg['shops'])} 家店。现在自动检查 Ozon 接口，不修改店铺数据。")
    runtime_command = ["runuser", "-u", "ozon-monitor", "--", sys.executable,
                       str(ROOT / "monitor.py"), "--config", str(path)]
    if subprocess.run(runtime_command + ["probe"], cwd=str(ROOT)).returncode != 0:
        print("\n有接口未通过，配置已保存，监控尚未启动。请把上面的报错发给维护者。")
        return 1
    print("\n现在发送一条测试提醒给私密群组。")
    # Run tests as the service account too: otherwise root would create a DB and
    # log file the long-running unprivileged service cannot subsequently write.
    if subprocess.run(runtime_command + ["test-push"], cwd=str(ROOT)).returncode != 0:
        print("\n测试提交失败，监控尚未启动。配置已保存，可稍后重新运行向导。")
        return 1
    print("\n请在你和家人的两部手机上查看 pushplus 通知。")
    print("如果等待后仍没收到，输入 n，配置会保留，不会误报已经完成。")
    if input("两个人都收到了吗？[y/N]：").strip().lower() != "y":
        print("配置已保存，服务尚未启动；请告诉维护者哪部手机未收到。")
        return 1
    subprocess.run(["systemctl", "enable", SERVICE], check=True)
    subprocess.run(["systemctl", "start", SERVICE], check=True)
    import time
    time.sleep(2)
    if subprocess.run(["systemctl", "is-active", "--quiet", SERVICE]).returncode != 0:
        print("服务启动异常，请执行 journalctl -u ozon-push-monitor -n 30 --no-pager 查看。")
        return 1
    print("\n已启动并设置开机自启，现在可以关闭终端。")
    print("首次同步会记录现有商品；之后新发现且可售的商品、新订单才会提醒。")
    print("默认每 10 秒查询；30 秒为目标，实际到达仍受平台和网络影响。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (monitor.MonitorError, OSError, subprocess.CalledProcessError) as exc:
        message = str(exc) if isinstance(exc, monitor.MonitorError) else type(exc).__name__
        print("配置未完成：" + message, file=sys.stderr)
        sys.exit(2)
    except (KeyboardInterrupt, EOFError):
        print("\n已退出配置向导，未保存的输入不会生效。")
        sys.exit(130)
