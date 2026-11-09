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
    detail = data.get("detail") or {}
    grant = detail.get("grant") or {}
    for key in ("created_at", "grant_at", "expire_at", "points", "valid_hours"):
        if grant.get(key) is not None:
            print("| %s | %s |" % (key, str(grant[key]).replace("|", "\\|")))
    balance = detail.get("balance") or {}
    for key in ("available_points", "daily_points", "reward_points", "monthly_points"):
        if balance.get(key) is not None:
            print("| balance.%s | %s |" % (key, str(balance[key]).replace("|", "\\|")))
    expiry = detail.get("expiry") or {}
    if expiry:
        print("")
        print("#### 积分过期分布（逐笔入账）")
        print("")
        print("| 到期日 | 积分 | 笔数 | 剩余天数 |")
        print("| --- | --- | --- | --- |")
        for bucket in expiry.get("buckets") or []:
            print("| %s | %s | %s | %s |" % (
                bucket.get("expire_date"), bucket.get("points"),
                bucket.get("count"), bucket.get("days_left")))
        print("")
        print("- 近 %s 天内到期发放 %s 分；**扣掉已花掉的后真正待过期：%s 分**" % (
            expiry.get("warn_days"), expiry.get("soon_points"),
            expiry.get("soon_remaining")))
        print("- 当前每日积分余额：%s（账本未过期发放合计 %s 分）" % (
            expiry.get("held_daily_points"), expiry.get("granted_active_points")))
        for row in expiry.get("distribution") or []:
            if row.get("remaining", 0) > 0:
                print("- %s 到期：发放 %s 分 / **未用 %s 分**（剩 %s 天）" % (
                    row["expire_date"], row["granted"], row["remaining"], row["days_left"]))
        if expiry.get("unknown_expiry"):
            print("- 另有 %s 笔入账无过期时间字段，未纳入统计" % expiry["unknown_expiry"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
