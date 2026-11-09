#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断「已被撤销的令牌」能否通过 refresh 接口复活。

仅在仓库变量 RACCOON_DEBUG=1 时由 workflow 调用。
只打印 HTTP 状态、响应码与令牌指纹，绝不输出令牌内容。
"""

import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://xiaohuanxiong.com"


def http(url, method="GET", headers=None, body=None):
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, type(e).__name__


def brief(token):
    digest = hashlib.sha1(token.encode()).hexdigest()[:8]
    sid = "-"
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        sid = str(json.loads(base64.urlsafe_b64decode(seg)).get("sid", ""))[:20]
    except Exception:
        pass
    return "sha1=%s sid=%s len=%d" % (digest, sid, len(token))


def code_of(text):
    try:
        d = json.loads(text)
        return "code=%s message=%s" % (d.get("code"), d.get("message"))
    except Exception:
        return text[:100]


def main():
    token = (os.environ.get("RACCOON_ACCESS_TOKEN") or "").strip()
    if not token:
        print("无令牌，跳过诊断")
        return 0
    print("[诊断] 令牌指纹:", brief(token))
    auth = {"Accept": "application/json", "Authorization": "Bearer " + token}

    status, text = http(BASE + "/api/web/points/v1/balance", headers=auth)
    print("[诊断] 1) balance            -> HTTP %s %s" % (status, code_of(text)))

    body = json.dumps({"refresh_token": token}).encode()
    for label, headers in (("带Bearer", {"Content-Type": "application/json", **auth}),
                           ("不带Bearer", {"Content-Type": "application/json"}),
                           ("仅body无鉴权", {"Content-Type": "application/json",
                                             "X-Client-Platform": "desktop-windows"})):
        s, t = http(BASE + "/api/web/auth/v1/refresh", "POST", headers, body)
        print("[诊断] 2) refresh(%s) -> HTTP %s %s" % (label, s, code_of(t)))
        try:
            data = (json.loads(t).get("data") or {})
        except Exception:
            continue
        at = data.get("access_token")
        if not at:
            continue
        print("[诊断]    返回新令牌:", brief(at))
        s2, t2 = http(BASE + "/api/web/points/v1/balance", headers={
            "Accept": "application/json", "Authorization": "Bearer " + at})
        print("[诊断]    用新令牌打 balance -> HTTP %s %s" % (s2, code_of(t2)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
