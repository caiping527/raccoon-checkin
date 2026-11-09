#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小浣熊签到 · 本机公共库（仅供「密码登录.bat」调用，不直接运行）。

只保留 password_login.py 实际复用的最小集合：
  gh / die / log / resolve_repo / jwt_payload / fetch_phone_tail / TAIL_RE
凭据一律经 stdin 传给 gh，不落盘、不进命令行参数、不打印。
"""

import base64
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BEIJING = timezone(timedelta(hours=8))
DEFAULT_REPO = "caiping527/raccoon-checkin"
ORIGIN = (os.environ.get("RACCOON_MAIN_SITE_URL") or "https://xiaohuanxiong.com").rstrip("/")
IAM_ACCOUNT_URL = ORIGIN + "/api/iam/v1/account"
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, "更新记录.txt")
TAIL_RE = re.compile(r"^\d{4}$")


def log(tail, target, ok, note=""):
    line = "%s\t手机号尾号%s\t%s\t%s%s" % (
        datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S"), tail or "????",
        target, "同步成功" if ok else "失败", (" · " + note) if note else "")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def die(msg, advice="", tail="", target="-"):
    print("\n【失败】%s" % msg)
    if advice:
        print("  处理：%s" % advice)
    log(tail, target, False, msg)
    sys.exit(1)


def gh(args, stdin=None, check=True, timeout=60):
    try:
        p = subprocess.run(["gh"] + args, input=stdin, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except FileNotFoundError:
        die("未找到 GitHub CLI（gh）", "安装 https://cli.github.com/ 后重试")
    except subprocess.TimeoutExpired:
        die("gh 命令超时", "检查网络后重试")
    if check and p.returncode != 0:
        die("gh 执行失败：%s" % (p.stderr or p.stdout).strip()[:300],
            "检查网络、GH_TOKEN 是否有效、仓库是否存在且有管理员权限")
    return p


def jwt_payload(token):
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return {}


def resolve_repo(explicit):
    if explicit:
        return explicit
    env_repo = (os.environ.get("RACCOON_REPO") or "").strip()
    if env_repo:
        return env_repo
    try:
        p = subprocess.run(["git", "remote", "get-url", "origin"], cwd=HERE,
                           capture_output=True, text=True, timeout=10)
        url = (p.stdout or "").strip()
        m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", url)
        if m:
            return "%s/%s" % (m.group(1), m.group(2))
    except Exception:
        pass
    return DEFAULT_REPO


def fetch_phone_tail(token):
    """只读 IAM 账号接口，返回 (手机号尾4位, 昵称, err)。不触发登录动作。"""
    req = urllib.request.Request(
        IAM_ACCOUNT_URL,
        headers={"Accept": "application/json", "Authorization": "Bearer %s" % token,
                 "X-Client-Platform": "desktop-windows"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return None, "", "IAM 接口异常 %s" % type(e).__name__
    if not isinstance(data, dict):
        return None, "", "IAM 响应格式异常"
    user = (data.get("data") or {}).get("user") or {}
    if not isinstance(user, dict):
        return None, "", "IAM 响应缺少 user"
    phone = str(user.get("phone") or "")
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 4:
        return None, "", "IAM 未返回手机号（%s）" % (data.get("message") or "无详情")
    return digits[-4:], str(user.get("name") or ""), None
