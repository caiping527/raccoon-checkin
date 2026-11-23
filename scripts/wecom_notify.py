#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取 result.json，按策略推送企业微信。

两条互相独立的通道：
  A) 单账号告警（checkin job，main 入口）：默认「成功不打扰」，仅以下情形推送
       1) 签到失败 / 自检失败
       2) 登录令牌剩余 <= WARN_DAYS 天（默认 7 天），提前提醒更新，避免断签
       3) SUCCESS_NOTIFY=1 时每次运行都推
  B) 每日积分播报（recommend job，--recommend 入口）：每天签到完成后必推一条，
     逐个账号列出「当前积分」「最早开始过期的日子」，并给出「到期前一天
     用掉」的具体日期。RECOMMEND_NOTIFY=0 可关闭。

支持两种通道（可同时配置）：
  WECOM_WEBHOOK  群机器人完整 webhook（或仅 key）
  WECOM_APP      "corpid,corpsecret,touser,agentid"（touser 可为 @all）

排版：纯文本 + emoji 色块，两个通道发同一份内容。
  为什么要这样：微信端只渲染 text / image —— markdown 提示「暂不支持此消息类型」，
  textcard 的 div 配色在微信插件里全部变灰（实测），而 image 会缩成缩略图，
  非要点开才能看清。真正在微信里带颜色的只有 emoji 自带的色块（🟩🟥🟨…），
  所以每行行首放一个 emoji 当「色标」，正文保持纯文本。
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QYAPI = "https://qyapi.weixin.qq.com/cgi-bin"
WARN_DAYS = float(os.environ.get("RACCOON_WARN_DAYS") or "7")
BEIJING = timezone(timedelta(hours=8))

TEXT_BYTE_LIMIT = 1900        # 企微 text 上限 2048 字节，留余量给截断提示


def fit_text(text, limit=TEXT_BYTE_LIMIT):
    """超长按行截断：整条超限企微会直接拒收，比截断更糟。"""
    if len(text.encode("utf-8")) <= limit:
        return text
    out, size = [], 0
    for line in text.split("\n"):
        n = len(line.encode("utf-8")) + 1
        if size + n > limit - 80:
            break
        out.append(line)
        size += n
    out.append("…内容过长已截断，完整数据见运行详情")
    return "\n".join(out)


def post_json(url, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def push_webhook(value, content):
    url = value if value.startswith("http") else (
        QYAPI + "/webhook/send?key=" + value)
    r = post_json(url, {"msgtype": "text", "text": {"content": content}})
    return "webhook:%s" % (r.get("errmsg") or r.get("errcode") or "ok")


def push_app(value, content):
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
        "text": {"content": content}, "safe": 0,
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
    """单账号告警：一行一个 emoji 色标，关键信息（到期/剩余天数）单独成行。"""
    days = result.get("token_days_left")
    failed = not str(title).startswith("✅")
    # decide() 的标题自带 emoji（❌/⚠️/✅），这里去掉，避免与行首色标重复
    plain_title = str(title).replace("❌", "").replace("⚠️", "").replace("✅", "").strip()
    lines = ["📌 小浣熊签到 · %s" % plain_title,
             "🟦 账号：%s" % (result.get("account") or "未命名账号"),
             "%s 状态：%s" % ("🟥" if failed else "✅", result.get("message") or "无详情")]
    if result.get("token_expire_at"):
        lines.append("📅 令牌到期：%s，剩 %s 天" % (
            result["token_expire_at"], days if days is not None else "?"))
    if title.startswith("⚠️"):
        lines.append("❗ 处理：双击「单个账号登录（备用）.bat」重新登记（不用打开客户端）")
    lines.append("⏰ 时间：%s" % (result.get("checked_at") or "-"))
    run_url = os.environ.get("RACCOON_RUN_URL")
    if run_url:
        lines.append("🔗 运行详情：%s" % run_url)
    return fit_text("\n".join(str(x) for x in lines))


def _num(value):
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _day_before(day_text):
    """「开始过期日」的前一天，即建议把积分用掉的最后一天。"""
    try:
        return (date.fromisoformat(str(day_text)[:10]) - timedelta(days=1)).isoformat()
    except (TypeError, ValueError):
        return None


def recommend_rows(root):
    """读取各账号 result.json，每个账号一行，按「临期未用积分」从多到少排序。

    与旧版区别：即使某个账号没有积分账本（签到失败 / 接口未返回），
    也要作为一行出现在每日播报里，避免「少了一个号却没人发现」。
    """
    import glob
    rows = []
    for path in sorted(glob.glob(os.path.join(root, "**", "result.json"), recursive=True)):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        detail = data.get("detail") or {}
        expiry = detail.get("expiry") or {}
        balance = detail.get("balance") or {}
        # nearest_remaining 才是「还没花掉的」最早一批；账本里已花光的
        # 发放批次（nearest_expire_date）不能拿来当待用积分的到期日
        nearest = expiry.get("nearest_remaining") or {}
        # 真实数据里 nearest_remaining 只会取「还没花掉且 >0」的最早一批；
        # 这里再兜一层，避免异常数据冒出「这批 0 分，建议提前用掉」的误导句
        if _num(nearest.get("remaining")) <= 0:
            nearest = {}
        activation = expiry.get("activation") or {}
        nearest_date = nearest.get("expire_date")
        nearest_days = nearest.get("days_left")
        rows.append({
            "account": str(data.get("account") or "未命名账号"),
            "status": str(data.get("status") or "unknown"),
            "message": str(data.get("message") or ""),
            "held": _num(balance.get("available_points")),   # 客户端显示的当前总积分
            "daily": _num(expiry.get("held_daily_points")
                          if expiry.get("held_daily_points") is not None
                          else balance.get("daily_points")),  # 其中会过期的签到积分
            "reward": _num(balance.get("reward_points")),
            "soon": _num(expiry.get("soon_remaining")),       # 临期且未用
            "warn": expiry.get("warn_days"),
            "same_day": _num(activation.get("same_day_points")),
            "policy": activation.get("policy") or "未知",
            "nearest_date": nearest_date,                    # 积分开始过期的日子
            "nearest_days": nearest_days,
            "nearest_points": _num(nearest.get("remaining")),
            "use_by": _day_before(nearest_date),             # 提前一天用掉
            # 签到失败时云端根本不会查余额，此时账本里的 0 不是真实余额，
            # 不能显示成「0 分 / 长期有效」，必须明确标成未取到
            "has_data": (str(data.get("status") or "unknown") == "ok"
                         and (bool(expiry) or balance.get("available_points") is not None)),
        })
    # 临期积分多的排最前；今晚就清零的 300 分优先级最高，余额为 0 的沉底
    rows.sort(key=lambda r: (-r["same_day"], -r["soon"], -r["held"],
                             r["nearest_days"] if isinstance(r["nearest_days"], (int, float))
                             else 999,
                             r["account"]))
    return rows


def _expiry_sentence(r):
    """单个账号的「开始过期日 + 提前一天用掉」提示（纯文本）。"""
    if not r["nearest_date"]:
        if not r["has_data"]:
            return "积分数据未取到（见下方告警）"
        return "暂无标注到期日的待用积分（多为长期有效奖励分）"
    days = r["nearest_days"]
    head = "%s 开始过期，剩 %s 天，这批 %g 分" % (
        r["nearest_date"], days if days is not None else "?", r["nearest_points"])
    # 今明两天就到期时，「提前一天」可能已经落在昨天，给出过期日期反而误导
    if isinstance(days, (int, float)) and days <= 1:
        return head + "；今明两天就到期"
    if r["use_by"]:
        return head + "；建议 %s 前用掉" % r["use_by"]
    return head


def build_report(rows):
    """Markdown 榜单，写入 Actions Summary（GitHub 页面用，不是手机推送）。"""
    if not rows:
        return "#### 每日积分播报\n\n- 本次没有收到任何账号的积分数据。\n"
    lines = ["#### 每日积分播报（按临期未用积分从多到少排序）", "",
             "| 排名 | 账号 | 当前积分 | 开始过期日 | 建议用掉日 | 临期待用 | 签到积分 | 奖励积分 | 签到状态 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for i, r in enumerate(rows, 1):
        if r["has_data"]:
            held = "**%g**" % r["held"]
            daily, reward, soon = "%g" % r["daily"], "%g" % r["reward"], "%g 分" % r["soon"]
            nearest = r["nearest_date"] or "—"
            use_by = r["use_by"] or "—"
            if r["nearest_date"] and isinstance(r["nearest_days"], (int, float)) \
                    and r["nearest_days"] <= 1:
                use_by = "今天"
        else:
            # 签到失败时云端不会查余额，账本里的 0 不是真实余额，不能显示成 0 分
            held, daily, reward, soon = "未取到", "—", "—", "—"
            nearest, use_by = "—", "—"
        lines.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            i, r["account"], held, nearest, use_by, soon, daily, reward,
            "成功" if r["status"] == "ok" else "失败"))
    lines.append("")
    lines.append("> 当前积分 = 客户端显示的 available_points（签到积分 + 奖励积分）。"
                 "开始过期日取还没花掉的最早一批积分的到期日；"
                 "建议用掉日 = 开始过期日的前一天。")
    lines.append("")
    bad = [r for r in rows if r["same_day"] > 0]
    if bad:
        lines.append("**⚠️ 未登录激活**：%s 今日 %g 分今晚 23:59 清零，请尽快用掉。"
                     % ("、".join(r["account"] for r in bad), sum(r["same_day"] for r in bad)))
        lines.append("")
    soon = [r for r in rows if r["soon"] > 0]
    if soon:
        lines.append("**建议：今天优先用 %s 跑代码**，可先消耗 %g 分（%s 天后到期）。"
                     % (soon[0]["account"], soon[0]["soon"], soon[0]["nearest_days"]))
        if len(soon) > 1:
            lines.append("多个号都有临期积分：按上表顺序依次使用，临期积分多的先用。")
    else:
        lines.append("当前没有账号有临期签到积分，无需特意切换账号。")
    return "\n".join(lines) + "\n"


def build_daily_text(rows, run_url=None, expected=None):
    """每日积分播报（纯文本 + emoji 色标，微信里直接显示彩色）。

    每个账号都列出：当前积分、积分开始过期的日子、提前一天用掉的具体日期。
    """
    today = datetime.now(BEIJING).strftime("%Y-%m-%d")
    lines = ["📌 小浣熊 · 每日积分播报", "📅 %s" % today]
    if not rows:
        lines.append("⚠️ 本次没有收到任何账号的积分数据，请查看运行详情")
        if run_url:
            lines.append("🔗 运行详情：%s" % run_url)
        return fit_text("\n".join(lines))

    ok_n = sum(1 for r in rows if r["status"] == "ok")
    bad_n = len(rows) - ok_n
    head = "📣 登记 %d 个账号，收到 %d 份积分数据" % (
        expected if expected else len(rows), len(rows))
    if bad_n:
        head += "，%d 个签到失败" % bad_n
    else:
        head += "，全部签到成功"
    lines.append(head)

    for r in rows:
        lines.append("")
        lines.append(("🟩 " if r["status"] == "ok" else "🟥 ") + r["account"])
        if r["status"] != "ok":
            lines.append("❗ 签到失败：%s" % ((r["message"] or "未知原因")[:60]))
            continue
        if not r["has_data"]:
            lines.append("⚪ 积分数据未取到")
            continue
        lines.append("💰 当前积分 %g 分（签到 %g / 奖励 %g）"
                     % (r["held"], r["daily"], r["reward"]))
        if r["nearest_date"]:
            days = r["nearest_days"]
            lines.append("📅 %s 开始过期，剩 %s 天，这批 %g 分"
                         % (r["nearest_date"], days if days is not None else "?",
                            r["nearest_points"]))
            if isinstance(days, (int, float)) and days <= 1:
                lines.append("⏰ 今天就用掉，明天开始作废")
            elif r["use_by"]:
                lines.append("⏰ 建议 %s 前用掉（提前一天）" % r["use_by"])
        else:
            lines.append("⚪ 暂无标注到期日的待用积分（多为长期奖励分）")
        if r["same_day"] > 0:
            lines.append("🔔 今日 %g 分今晚 23:59 清零，尽快登录用掉" % r["same_day"])

    soon = [r for r in rows if r["soon"] > 0]
    lines.append("")
    if soon:
        lines.append("📣 今天优先用 %s，临期积分先用，别拖到过期当天" % soon[0]["account"])
    else:
        lines.append("⚪ 今天没有临期积分，不用特意切号")
    lines.append("⚪ 注：签到积分有明确到期日；奖励分未标注到期日的不猜测")
    if run_url:
        lines.append("🔗 运行详情：%s" % run_url)
    return fit_text("\n".join(lines))


def recommend_main(root):
    rows = recommend_rows(root)
    print(build_report(rows))
    policy = str(os.environ.get("RECOMMEND_NOTIFY") or "daily").strip().lower()
    if policy in ("0", "off", "false", "no", "none", "disabled"):
        print("每日积分播报：已关闭（RECOMMEND_NOTIFY=%s）" % policy)
        return 0
    raw_expected = str(os.environ.get("RACCOON_ACCOUNTS") or "").strip()
    expected = len([x for x in raw_expected.split(",") if x.strip()]) or None
    text = build_daily_text(rows, os.environ.get("RACCOON_RUN_URL"), expected)
    print("每日积分播报：%s" % " | ".join(push_all(text)))
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
