#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 result.json 转成脱敏的 Markdown 运行摘要（白名单字段，绝不输出令牌）。"""

import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ALLOWED = ("status", "action", "message", "checked_at",
           "token_expire_at", "token_days_left", "rotated")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "result.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        print("- 无可用签到结果（任务在生成结果前失败）")
        return 0
    print("| 字段 | 值 |")
    print("| --- | --- |")
    for key in ALLOWED:
        value = data.get(key)
        if value is None or value == "":
            continue
        print("| %s | %s |" % (key, str(value).replace("|", "\\|").replace("\n", " ")))
    grant = ((data.get("detail") or {}).get("grant") or {})
    for key in ("created_at", "grant_at", "expire_at", "points", "valid_hours"):
        if grant.get(key) is not None:
            print("| %s | %s |" % (key, str(grant[key]).replace("|", "\\|")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
