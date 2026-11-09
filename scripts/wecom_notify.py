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
        lines.append("处理：打开小浣熊客户端登录该账号，双击「一键更新.bat」")
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
    return "app:%s" % (r.get("errmsg") or r.get("errcode"))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "result.json"
    result = load_result(path)
    title = decide(result)
    if not title:
        print("本次无需推送（签到成功且令牌未临期）")
        return 0
    text = build_text(result, title)
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
    print("[%s] %s" % (title, " | ".join(channels)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
