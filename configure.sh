#!/bin/sh
set -eu
cd /opt/ozon-push-monitor
exec /usr/bin/python3.11 /opt/ozon-push-monitor/configure.py
