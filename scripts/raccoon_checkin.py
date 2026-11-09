#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""商汤小浣熊「每日登录动作」云端签到核心（零第三方依赖）。

机制（以客户端实测为准，不预设积分是否过期）：
  桌面端登录会触发当日 300 积分入账；云端每天模拟一次该登录动作，
  并用积分账本 daily_grant 确认「今日已入账」。
  账本里的 expire_at 字段仅作观测记录，不作为成功/失败判据，
  因此「积分是否过期」两种模型下都不会误报失败。

链路：
  [可选] POST /api/web/auth/v1/refresh              用 refresh_token 换 access_token
         GET  /api/web/office/v3/setting_info        桌面客户端启动动作（主触发，只读 GET）
  [兜底] POST /api/web/desktop/v1/login/points/grant  幂等发放（仅账本未见当日记录时）
         GET  /api/web/points/v1/bills               核对当日入账
         GET  /api/web/points/v1/balance             观测积分分项（daily_points 是否累积）

输出 result.json（不含任何令牌），退出码 0=成功 / 1=失败。
轮换出的新令牌写入 RACCOON_ROTATED_FILE 指定的文件，供 workflow 回写 Secret。
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BEIJING = timezone(timedelta(hours=8))
ORIGIN = (os.environ.get("RACCOON_MAIN_SITE_URL") or "https://xiaohuanxiong.com").rstrip("/")
AUTH_REFRESH_URL = ORIGIN + "/api/web/auth/v1/refresh"
SETTING_INFO_URL = ORIGIN + "/api/web/office/v3/setting_info"
GRANT_URL = ORIGIN + "/api/web/desktop/v1/login/points/grant"
BILLS_URL = ORIGIN + "/api/web/points/v1/bills?paging.limit=100&paging.offset=0"
BALANCE_URL = ORIGIN + "/api/web/points/v1/balance"

CLIENT_HEADERS = {
    "X-Client-Platform": "desktop-windows",
    "X-Client-Version": "v1.0.0",
    "X-Raccoon-Language": "zh",
}
HTTP_TIMEOUT = 20
RECHECK_DELAY = float(os.environ.get("RACCOON_RECHECK_DELAY") or "2")

RESULT_FILE = os.environ.get("RACCOON_RESULT_FILE") or "result.json"
ROTATED_FILE = os.environ.get("RACCOON_ROTATED_FILE") or "raccoon-rotated.env"
LABEL = os.environ.get("RACCOON_ACCOUNT_LABEL") or "未命名账号"
# 账号真实内部标识（JWT sub 尾 4 位）。与展示用的手机号尾号不是一回事。
INTERNAL_ID = (os.environ.get("RACCOON_ACCOUNT_ID") or "").strip()


def now_ts():
    return time.time()


def beijing_today():
    return datetime.now(BEIJING).strftime("%Y-%m-%d")


def jwt_payload(token):
    """只读 JWT payload，不校验签名、不用于鉴权。"""
    if not isinstance(token, str) or token.count(".") != 2:
        return {}
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        data = json.loads(base64.urlsafe_b64decode(seg))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def account_tail(token):
    """从 sub 取内部标识尾 4 位，仅用于归属核对（不是手机号尾号）。"""
    digits = "".join(c for c in str(jwt_payload(token).get("sub") or "") if c.isdigit())
    return digits[-4:] if len(digits) >= 4 else ""


def token_meta(token):
    """返回 (剩余天数, 过期时间字符串)。解析失败返回 (None, None)。"""
    exp = jwt_payload(token).get("exp")
    if not isinstance(exp, (int, float)):
        return None, None
    left = (float(exp) - now_ts()) / 86400.0
    dt = datetime.fromtimestamp(float(exp), BEIJING).strftime("%Y-%m-%d %H:%M")
    return round(left, 2), dt


def http_json(url, method="GET", headers=None, body=None):
    """返回 (status, parsed_dict_or_None, text)。异常不抛出。"""
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            text = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
        text = e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, None, "网络错误: %s" % str(e)[:200]
    try:
        parsed = json.loads(text)
        return status, (parsed if isinstance(parsed, dict) else None), text
    except Exception:
        return status, None, text[:500]


def auth_headers(token):
    return {"Accept": "application/json", "Authorization": "Bearer %s" % token}


def refresh_access_token(refresh_token):
    """返回 (access_token, new_refresh_token, err)。"""
    body = json.dumps({"refresh_token": refresh_token}).encode("utf-8")
    status, data, text = http_json(
        AUTH_REFRESH_URL, "POST", {"Content-Type": "application/json"}, body)
    payload = (data or {}).get("data") or {}
    at = payload.get("access_token") if isinstance(payload, dict) else None
    if status != 200 or not at:
        return None, None, "HTTP %s %s" % (status, text[:120])
    new_rt = payload.get("refresh_token") or ""
    return at, (new_rt if new_rt and new_rt != refresh_token else ""), None


def trigger_login(token, use_grant=False):
    """模拟桌面端登录动作。返回 (ok, detail)。"""
    headers = dict(auth_headers(token))
    headers.update(CLIENT_HEADERS)
    status, data, text = http_json(SETTING_INFO_URL, "GET", headers)
    if not data or data.get("code") != 0:
        return False, {"step": "setting_info", "http": status,
                       "code": (data or {}).get("code"),
                       "message": str((data or {}).get("message") or text)[:200]}
    detail = {"step": "setting_info", "http": status, "code": 0}
    if use_grant:
        g_status, g_data, g_text = http_json(
            GRANT_URL, "POST", dict(auth_headers(token),
                                    **{"Content-Type": "application/json"}), b"{}")
        detail["grant"] = {
            "http": g_status,
            "code": (g_data or {}).get("code"),
            "granted": ((g_data or {}).get("data") or {}).get("granted")
            if isinstance((g_data or {}).get("data"), dict) else None,
            "error": None if g_data else g_text[:120],
        }
    return True, detail


def parse_dt(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BEIJING)
        return dt
    except Exception:
        return None


def read_bills(token):
    """返回 (latest_daily_grant_or_None, err)。"""
    status, data, text = http_json(BILLS_URL, "GET", auth_headers(token))
    if not data or data.get("code") != 0:
        return None, "HTTP %s %s" % (status, str((data or {}).get("message") or text)[:150])
    payload = data.get("data") or {}
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return None, "账本响应缺少 items"
    for item in items:
        if isinstance(item, dict) and item.get("biz_type") == "daily_grant":
            return item, None
    return None, None


def read_balance(token):
    """只读积分分项。用于观测 daily_points 是否逐日累积（判断 300 是否过期）。"""
    status, data, text = http_json(BALANCE_URL, "GET", auth_headers(token))
    if not data or data.get("code") != 0:
        return None, "HTTP %s %s" % (status, str((data or {}).get("message") or text)[:150])
    payload = data.get("data") or {}
    if not isinstance(payload, dict):
        return None, "余额响应格式异常"
    keys = ("available_points", "daily_points", "reward_points", "monthly_points")
    return {k: payload.get(k) for k in keys if k in payload}, None


def attach_balance(result, token):
    """余额是观测项，查询失败不影响签到结论。"""
    balance, err = read_balance(token)
    if balance:
        result["detail"]["balance"] = balance
    elif err:
        result["detail"]["balance_error"] = err


def evaluate(grant, today):
    """成功判据：今天的每日积分已入账。

    expire_at - grant_at 的档位只记录到 detail 供观测，不参与判定：
    用户实测 300 积分不过期，若以「7 天档位」为判据会误报失败。
    """
    if not grant:
        return False, "账本窗口内无每日发放（daily_grant）记录", {}
    created = str(grant.get("created_at") or "")[:10]
    grant_at = parse_dt(grant.get("grant_at"))
    expire_at = parse_dt(grant.get("expire_at"))
    points = grant.get("points")
    detail = {"created_at": created,
              "grant_at": str(grant.get("grant_at") or ""),
              "expire_at": str(grant.get("expire_at") or ""),
              "points": points}
    if grant_at and expire_at:
        detail["valid_hours"] = round((expire_at - grant_at).total_seconds() / 3600, 2)
        detail["expire_at_policy"] = "observed_only"
    if created != today:
        return False, "今日未发放：最新发放记录创建于 %s（今天 %s）" % (created or "?", today), detail
    try:
        pts = float(points)
    except (TypeError, ValueError):
        pts = 0.0
    if pts <= 0:
        return False, "今日发放记录积分为 0 或缺失", detail
    suffix = ("；账本有效期字段至 %s（仅记录）" % detail["expire_at"]) if detail["expire_at"] else ""
    return True, "今日每日积分 +%g 已到账%s" % (pts, suffix), detail


def write_result(result):
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def write_rotated(pairs):
    """pairs: [(SECRET_NAME, token)]；只写文件、绝不打印。"""
    if not pairs:
        return
    with open(ROTATED_FILE, "w", encoding="utf-8") as f:
        for name, token in pairs:
            if name and token:
                f.write("%s=%s\n" % (name, token.replace("\n", "").replace("\r", "")))


def base_result():
    return {
        "app": "raccoon",
        "account": LABEL,
        "status": "error",
        "action": "none",
        "message": "",
        "checked_at": datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S"),
        "token_expire_at": None,
        "token_days_left": None,
        "rotated": False,
        "detail": {},
    }


def check_ownership(result, token):
    """防串号：优先用账号专属内部 ID；旧环境未配置时退回标签包含判断。"""
    tail = account_tail(token)
    result["detail"]["account_tail"] = tail
    if not tail:
        return True
    if INTERNAL_ID and INTERNAL_ID != tail:
        # 该消息会进企业微信推送，不写任何内部ID尾号
        result["message"] = ("凭据归属不符：令牌属于另一个账号，已中止"
                             "（请重新登录该账号并双击「一键更新.bat」）")
        return False
    return True


def diagnose(token):
    """只读自检：不触发登录动作，不产生任何签到副作用。"""
    result = base_result()
    result["action"] = "diagnose"
    days, exp = token_meta(token)
    result["token_expire_at"] = exp
    result["token_days_left"] = days
    if not check_ownership(result, token):
        write_result(result)
        print(result["message"])
        return 1
    if days is not None and days <= 0:
        result["message"] = "登录令牌已过期，请在本机运行「一键更新.bat」"
        write_result(result)
        print(result["message"])
        return 1
    grant, err = read_bills(token)
    if err:
        if "401" in err or "authorization_verify" in err:
            result["detail"]["revoked"] = True
            result["message"] = (
                "登录会话已被服务端撤销(401)：客户端退出/重登该账号所致。"
                "请双击「密码登录.bat」用手机号+密码重新登记（不依赖桌面客户端）；"
                "若在客户端操作，切勿点退出登录"
            )
        else:
            result["message"] = "账本只读查询失败：%s" % err
        write_result(result)
        print(result["message"])
        return 1
    result["status"] = "ok"
    result["message"] = "自检通过：令牌有效（剩余 %s 天），账本接口可读" % days
    result["detail"]["latest_grant"] = {
        k: grant.get(k) for k in ("created_at", "grant_at", "expire_at", "points")
    } if grant else None
    attach_balance(result, token)
    write_result(result)
    print(result["message"])
    return 0


def run():
    result = base_result()
    at = (os.environ.get("RACCOON_ACCESS_TOKEN") or "").strip()
    rt = (os.environ.get("RACCOON_REFRESH_TOKEN") or "").strip()
    check_only = "--diagnose" in sys.argv or "--check-only" in sys.argv

    # 可选「云端密码登录」：每次运行用手机号+密码现取一张独立会话。
    # 这样令牌不依赖桌面客户端 auth.json：客户端切号 / 退出登录都不会让它失效，
    # 也不存在 30 天到期问题。凭据由账号专属 Environment Secret 注入，绝不打印。
    phone = (os.environ.get("RACCOON_PHONE") or "").strip()
    pwd = os.environ.get("RACCOON_PASSWORD") or ""
    if phone and pwd:
        try:
            import iam_login
            status, data, _ = iam_login.login_password(
                phone, pwd, os.environ.get("RACCOON_NATION_CODE") or "86")
            fresh = data.get("access_token")
            if status == 200 and fresh:
                at = fresh
                result["detail"]["auth_mode"] = "password"
                print("云端密码登录成功：已取得独立会话（令牌内容不外显）")
            else:
                why = str(data.get("code") or data.get("message") or "无详情")[:120]
                print("云端密码登录失败(HTTP %s %s)，回退到已存令牌" % (status, why))
                result["detail"]["password_login_error"] = why
        except Exception as exc:
            print("云端密码登录异常(%s)，回退到已存令牌" % type(exc).__name__)
            result["detail"]["password_login_error"] = type(exc).__name__

    if not at and not rt:
        result["message"] = "未配置凭据：需要 RACCOON_ACCESS_TOKEN 或 RACCOON_REFRESH_TOKEN"
        write_result(result)
        print(result["message"])
        return 1

    rotated_pairs = []
    if not at and rt:
        at, new_rt, err = refresh_access_token(rt)
        if not at:
            result["message"] = "令牌刷新失败：%s" % err
            write_result(result)
            print(result["message"])
            return 1
        rotated_pairs.append(("RACCOON_ACCESS_TOKEN", at))
        if new_rt:
            rotated_pairs.append(("RACCOON_REFRESH_TOKEN", new_rt))
        result["rotated"] = True

    days, exp = token_meta(at)
    result["token_expire_at"] = exp
    result["token_days_left"] = days
    if not check_ownership(result, at):
        write_result(result)
        print(result["message"])
        return 1

    if days is not None and days <= 0 and rt:
        at, new_rt, err = refresh_access_token(rt)
        if not at:
            result["message"] = "令牌已过期且刷新失败：%s" % err
            write_result(result)
            print(result["message"])
            return 1
        rotated_pairs = [("RACCOON_ACCESS_TOKEN", at)]
        if new_rt:
            rotated_pairs.append(("RACCOON_REFRESH_TOKEN", new_rt))
        result["rotated"] = True
        days, exp = token_meta(at)
        result["token_expire_at"] = exp
        result["token_days_left"] = days

    if days is not None and days <= 0:
        result["message"] = "登录令牌已过期，请在本机运行「一键更新.bat」"
        write_rotated(rotated_pairs)
        write_result(result)
        print(result["message"])
        return 1

    if check_only:
        return diagnose(at)

    result["action"] = "checkin"
    ok, detail = trigger_login(at)
    result["detail"]["trigger"] = detail
    if not ok:
        # 401/403 时若有 refresh_token，刷新后重试一次
        if rt and detail.get("http") in (401, 403):
            new_at, new_rt, err = refresh_access_token(rt)
            if new_at:
                at = new_at
                rotated_pairs = [("RACCOON_ACCESS_TOKEN", at)]
                if new_rt:
                    rotated_pairs.append(("RACCOON_REFRESH_TOKEN", new_rt))
                result["rotated"] = True
                ok, detail = trigger_login(at)
                result["detail"]["trigger"] = detail
        if not ok:
            result["message"] = "登录动作触发失败：%s" % json.dumps(detail, ensure_ascii=False)[:200]
            write_rotated(rotated_pairs)
            write_result(result)
            print(result["message"])
            return 1

    today = beijing_today()
    grant, err = read_bills(at)
    if err:
        result["message"] = "账本核对失败：%s" % err
        write_rotated(rotated_pairs)
        write_result(result)
        print(result["message"])
        return 1

    # 服务端异步建当日发放行：首次未看到今天则补一次 grant 并重查
    if not grant or str(grant.get("created_at") or "")[:10] != today:
        if RECHECK_DELAY > 0:
            time.sleep(min(RECHECK_DELAY, 10))
        grant, err = read_bills(at)
        if err:
            result["message"] = "账本复核失败：%s" % err
            write_rotated(rotated_pairs)
            write_result(result)
            print(result["message"])
            return 1
    if not grant or str(grant.get("created_at") or "")[:10] != today:
        # 兜底：显式调用幂等发放接口后再查一次
        ok2, gdetail = trigger_login(at, use_grant=True)
        result["detail"]["grant_fallback"] = gdetail
        if RECHECK_DELAY > 0:
            time.sleep(min(RECHECK_DELAY, 10))
        grant, err = read_bills(at)

    ok, message, detail = evaluate(grant, today)
    result["detail"]["grant"] = detail
    result["message"] = message
    result["status"] = "ok" if ok else "error"
    attach_balance(result, at)
    write_rotated(rotated_pairs)
    write_result(result)
    print("[%s] %s" % (LABEL, message))
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(run())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # 兜底：任何未预期异常也要落 result，便于企微告警
        emergency = base_result()
        emergency["message"] = "未预期异常：%s: %s" % (type(exc).__name__, str(exc)[:200])
        try:
            write_result(emergency)
        except Exception:
            pass
        print(emergency["message"])
        sys.exit(1)
