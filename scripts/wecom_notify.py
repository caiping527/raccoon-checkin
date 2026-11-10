#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取 result.json，按策略推送企业微信。

策略与参考系统一致：默认「成功不打扰」，仅以下情形推送
  1) 签到失败 / 自检失败
  2) 登录令牌剩余 <= WARN_DAYS 天（默认 7 天），提前提醒更新，避免断签
  3) SUCCESS_NOTIFY=1 时每次运行都推

支持两种通道（可同时配置）：
  WECOM_WEBHOOK  群机器人完整 webhook（或仅 key）
  WECOM_APP      "corpid,corpsecret,touser,agentid"（touser 可为 @all）
"""

import json
import os
import sys
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QYAPI = "https://qyapi.weixin.qq.com/cgi-bin"
WARN_DAYS = float(os.environ.get("RACCOON_WARN_DAYS") or "7")


def truthy(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def load_result(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "error", "account": os.environ.get("RACCOON_ACCOUNT_LABEL") or "未命名账号",
                "message": "未找到签到结果文件（任务可能在签到前就失败了）",
                "action": "unknown"}


def decide(result):
    if result.get("status") != "ok":
        return "❌ 签到失败"
    days = result.get("token_days_left")
    if isinstance(days, (int, float)) and days <= WARN_DAYS:
        return "⚠️ 令牌临期"
    if truthy(os.environ.get("SUCCESS_NOTIFY")):
        return "✅ 签到成功"
    return None


def build_text(result, title):
    lines = [title,
             "账号：%s" % (result.get("account") or "未命名账号"),
             "状态：%s" % (result.get("message") or "无详情"),
             "时间：%s" % (result.get("checked_at") or "-")]
    if result.get("token_expire_at"):
        lines.append("令牌到期：%s（剩余 %s 天）" % (
            result["token_expire_at"], result.get("token_days_left")))
    if title.startswith("⚠️"):
        lines.append("处理：双击「密码登录.bat」重新登记该账号（不用打开客户端）")
    run_url = os.environ.get("RACCOON_RUN_URL")
    if run_url:
        lines.append("运行详情：%s" % run_url)
    return "\n".join(str(x) for x in lines)


def post_json(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def push_webhook(value, text):
    url = value if value.startswith("http") else (
        QYAPI + "/webhook/send?key=" + value)
    r = post_json(url, {"msgtype": "text", "text": {"content": text}})
    return "webhook:%s" % (r.get("errmsg") or r.get("errcode"))


def push_app(value, text):
    parts = [p.strip() for p in value.split(",")]
    if len(parts) < 4:
        return "app:配置格式错误（应为 corpid,corpsecret,touser,agentid）"
    corpid, secret, touser, agentid = parts[0], parts[1], parts[2] or "@all", parts[3]
    token_url = "%s/gettoken?corpid=%s&corpsecret=%s" % (
        QYAPI, urllib.parse.quote(corpid, safe=""), urllib.parse.quote(secret, safe=""))
    with urllib.request.urlopen(token_url, timeout=15) as resp:
        token_data = json.loads(resp.read().decode("utf-8", "replace"))
    if token_data.get("errcode") != 0:
        return "app:gettoken失败 %s" % token_data.get("errmsg")
    send_url = "%s/message/send?access_token=%s" % (QYAPI, token_data["access_token"])
    r = post_json(send_url, {
        "touser": touser, "msgtype": "text", "agentid": agentid,
        "text": {"content": text}, "safe": 0,
    })
    # 企微 message/send 在「无人可见」时 errcode 仍为 0，只回 invaliduser；
    # 把完整投递结果打进日志，避免出现「app:ok 但手机没收到」的假阳性。
    bits = ["errcode=%s" % r.get("errcode"),
            "errmsg=%s" % (r.get("errmsg") or "-")]
    if r.get("invaliduser"):
        bits.append("invaliduser=%s" % r["invaliduser"])
    if r.get("msgid"):
        bits.append("msgid=%s" % r["msgid"])
    return "app:" + " ".join(bits)


def push_all(text):
    channels = []
    webhook = (os.environ.get("WECOM_WEBHOOK") or "").strip()
    app = (os.environ.get("WECOM_APP") or "").strip()
    if webhook:
        try:
            channels.append(push_webhook(webhook, text))
        except Exception as e:
            channels.append("webhook异常:%s" % type(e).__name__)
    if app:
        try:
            channels.append(push_app(app, text))
        except Exception as e:
            channels.append("app异常:%s" % type(e).__name__)
    if not channels:
        channels.append("未配置通知通道，仅打印：\n" + text)
    return channels


def recommend_rows(root):
    """读取各账号 result.json，按「临期且未用积分」从多到少排序。"""
    import glob
    rows = []
    for path in sorted(glob.glob(os.path.join(root, "**", "result.json"), recursive=True)):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        expiry = (data.get("detail") or {}).get("expiry") or {}
        if not expiry:
            continue
        # nearest_remaining 才是「还没花掉的」最早一批；账本里已花光的
        # 发放批次（nearest_expire_date）不能拿来当待用积分的到期日
        nearest = expiry.get("nearest_remaining") or {}
        try:
            soon = float(expiry.get("soon_remaining") or 0)
        except (TypeError, ValueError):
            soon = 0.0
        balance = (data.get("detail") or {}).get("balance") or {}
        try:
            total = float(balance.get("available_points") or 0)
        except (TypeError, ValueError):
            total = 0.0
        try:
            daily = float(expiry.get("held_daily_points")
                          if expiry.get("held_daily_points") is not None
                          else balance.get("daily_points") or 0)
        except (TypeError, ValueError):
            daily = 0.0
        try:
            # 榜单展示客户端里的真实奖励余额；校正用的「有到期日部分」
            # 留在 expiry.held_reward_points，不拿来当总余额显示
            reward = float(balance.get("reward_points") or 0)
        except (TypeError, ValueError):
            reward = 0.0
        rows.append({
            "account": str(data.get("account") or "未命名账号"),
            "soon": round(soon, 2),
            "held": round(total, 2),        # 客户端显示的总可用积分
            "daily": round(daily, 2),       # 其中会过期的每日签到积分
            "reward": round(reward, 2),
            "warn": expiry.get("warn_days"),
            "same_day": round(float((expiry.get("activation") or {}).get("same_day_points") or 0), 2),
            "policy": (expiry.get("activation") or {}).get("policy") or "未知",
            "nearest_date": nearest.get("expire_date"),
            "nearest_days": nearest.get("days_left"),
        })
    # 临期积分多的排最前；临期相同的按手里可用积分多的排前，
    # 让「积分多、快过期」的号永远在榜首，余额为 0 的号沉底
    # 今晚就清零的 300 分优先级最高，其次才是常规临期积分
    rows.sort(key=lambda r: (-r.get("same_day", 0), -r["soon"], -r["held"],
                             r["nearest_days"] if isinstance(r["nearest_days"], (int, float))
                             else 999,
                             r["account"]))
    return rows


def build_report(rows):
    """Markdown 榜单，写入 Actions Summary。"""
    if not rows:
        return "#### 今日积分优先用号\n\n- 本次没有可用的积分账本数据。\n"
    warn = rows[0].get("warn") or 2
    lines = ["#### 今日积分优先用号（按临期未用积分从多到少排序）", "",
             "| 排名 | 账号 | %g 天内待过期且未用 | 最近到期 | 总可用积分 | 签到积分 | 奖励积分 | 今日300分 |" % warn,
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for i, r in enumerate(rows, 1):
        nearest = ("%s（剩 %s 天）" % (r["nearest_date"], r["nearest_days"])
                   if r["nearest_date"] and r["nearest_days"] is not None else "暂无待用批次")
        lines.append("| %d | %s | **%g 分** | %s | %g | %g | %g | %s |" % (
            i, r["account"], r["soon"], nearest, r["held"], r["daily"], r["reward"],
            r.get("policy") or "-"))
    lines.append("")
    lines.append("> 总可用积分 = 签到积分 + 奖励积分。到期日来自 expire_at 字段（每日签到）"
                 "与 event_name 文本（部分奖励，如「2027-10-19 到期」）；"
                 "未标注到期日的奖励积分暂无法判断过期。")
    lines.append("")
    bad = [r for r in rows if r.get("same_day", 0) > 0]
    if bad:
        lines.append("**⚠️ 未登录激活**：%s 今日 %g 分今晚 23:59 清零，请尽快登录/跑代码用掉。"
                     % ("、".join(r["account"] for r in bad), sum(r["same_day"] for r in bad)))
        lines.append("")
    if rows[0]["soon"] > 0:
        lines.append("**建议：今天优先用 %s 跑代码**，可先消耗 %g 分（%s 天后到期）。"
                     % (rows[0]["account"], rows[0]["soon"], rows[0]["nearest_days"]))
        if sum(1 for r in rows if r["soon"] > 0) > 1:
            lines.append("")
            lines.append("多个号都有临期积分：按上表顺序依次使用，积分多的先用。")
    else:
        lines.append("当前没有账号有临期签到积分，无需特意切换账号。")
    return "\n".join(lines) + "\n"


def build_recommend_text(rows, run_url=None):
    """企微纯文本榜单（不用 Markdown 符号，手机上看得清）。"""
    lines = ["【今日积分优先用号】按快过期的积分从多到少："]
    for i, r in enumerate(rows, 1):
        nearest = ("%s（剩 %s 天）" % (r["nearest_date"], r["nearest_days"])
                   if r["nearest_date"] and r["nearest_days"] is not None else "暂无待用批次")
        lines.append("%d. %s：临期待用 %g 分，%s；总可用 %g 分；今日300分 %s" % (
            i, r["account"], r["soon"], nearest, r["held"], r.get("policy") or "-"))
    bad = [r for r in rows if r.get("same_day", 0) > 0]
    if bad:
        lines.append("⚠️ %s 今日 300 分未激活，今晚 23:59 清零，尽快登录用掉！"
                     % "、".join(r["account"] for r in bad))
    if rows[0]["soon"] > 0:
        lines.append("建议：今天优先用 %s 跑代码，积分多的先用，别浪费。" % rows[0]["account"])
    else:
        lines.append("今天没有临期积分，不用特意切号。")
    lines.append("注：签到积分有明确到期日；部分奖励积分的到期日写在发放说明里。")
    if run_url:
        lines.append("运行详情：%s" % run_url)
    return "\n".join(lines)


def recommend_main(root):
    rows = recommend_rows(root)
    print(build_report(rows))
    policy = str(os.environ.get("RECOMMEND_NOTIFY") or "auto").strip().lower()
    has_soon = any(r["soon"] > 0 or r.get("same_day", 0) > 0 for r in rows)
    if not rows or policy in ("0", "off", "false", "no", "none", "disabled"):
        print("推荐通知：已跳过（无数据或已关闭）")
        return 0
    if policy not in ("1", "true", "yes", "on", "always") and not has_soon:
        print("推荐通知：本次无临期积分，按「不打扰」策略不推送")
        return 0
    text = build_recommend_text(rows, os.environ.get("RACCOON_RUN_URL"))
    print("推荐通知：%s" % " | ".join(push_all(text)))
    return 0


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--recommend":
        return recommend_main(sys.argv[2])
    path = sys.argv[1] if len(sys.argv) > 1 else "result.json"
    result = load_result(path)
    title = decide(result)
    if not title:
        print("本次无需推送（签到成功且令牌未临期）")
        return 0
    print("[%s] %s" % (title, " | ".join(push_all(build_text(result, title)))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
