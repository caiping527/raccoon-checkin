#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""商汤小浣熊签到 · 本机一键更新工具（双击「一键更新.bat」运行）。

作用：登录小浣熊客户端后运行本工具，自动完成
  1. 读出本机登录态里的明文 access_token，并解出账号尾号（不显示令牌）
  2. 建立该账号专属的 GitHub Environment「acct-<尾号>」并写入 Secret
  3. 把尾号登记到仓库变量 RACCOON_ACCOUNTS（幂等，重复运行不会重复添加）
  4. 触发一次云端只读自检，确认链路可用

令牌只经 stdin 传给 gh，不落盘、不进命令行参数、不打印。
需要环境变量 GH_TOKEN（GitHub 令牌，仅用于写 Secret / 触发工作流）。
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BEIJING = timezone(timedelta(hours=8))
DEFAULT_AUTH = os.path.join(os.path.expanduser("~"), ".office-raccoon",
                            "agent", "config", "auth.json")
DEFAULT_REPO = "caiping527/raccoon-checkin"
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "更新记录.txt")


def log(tail, target, ok, note=""):
    line = "%s\t尾号%s\t%s\t%s%s" % (
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
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        p = subprocess.run(["git", "remote", "get-url", "origin"], cwd=here,
                           capture_output=True, text=True, timeout=10)
        url = (p.stdout or "").strip()
        m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", url)
        if m:
            return "%s/%s" % (m.group(1), m.group(2))
    except Exception:
        pass
    return DEFAULT_REPO


def main():
    ap = argparse.ArgumentParser(description="小浣熊签到令牌一键更新")
    ap.add_argument("--account", help="强制校验账号尾号（4 位数字）")
    ap.add_argument("--repo", help="目标仓库 owner/repo")
    ap.add_argument("--auth-file", help="登录态文件路径")
    ap.add_argument("--no-run", action="store_true", help="不触发云端自检")
    args = ap.parse_args()

    print("=" * 58)
    print(" 商汤小浣熊签到 · 令牌一键更新")
    print("=" * 58)

    repo = resolve_repo(args.repo)
    auth_file = args.auth_file or os.environ.get("RACCOON_AUTH_FILE") or DEFAULT_AUTH
    print("  目标仓库：%s" % repo)

    if not (os.environ.get("GH_TOKEN") or "").strip():
        die("缺少 GH_TOKEN 环境变量",
            "把你的 GitHub 令牌设为系统环境变量 GH_TOKEN（仅需 Secrets 读写 + Actions 写权限）")
    gh(["auth", "status"], check=False)  # 只做连通性探测，失败由后续命令给出准确报错

    # 1) 读取本机登录态
    if not os.path.isfile(auth_file):
        die("未找到登录态文件：%s" % auth_file,
            "打开商汤小浣熊客户端并登录账号后重试")
    try:
        with open(auth_file, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        die("登录态文件解析失败：%s" % e, "重新登录客户端后再试")

    token = ""
    for key in ("access_token", "accessToken", "token"):
        v = data.get(key)
        if isinstance(v, str) and v.startswith("eyJ"):
            token = v.strip()
            break
    if not token:
        die("登录态里没有明文 access_token",
            "客户端版本可能启用了加密信封；请确认已登录并检查文件：%s" % auth_file)

    payload = jwt_payload(token)
    digits = "".join(c for c in str(payload.get("sub") or "") if c.isdigit())
    tail = digits[-4:] if len(digits) >= 4 else ""
    if not tail:
        die("无法从令牌识别账号尾号", "可用 --account <4位尾号> 手动指定")
    if args.account and args.account.strip() != tail:
        die("账号校验不通过：本机登录尾号 %s 与指定 %s 不一致" % (tail, args.account.strip()),
            "确认登录的是目标账号后重试")

    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and exp <= datetime.now().timestamp():
        died_at = datetime.fromtimestamp(exp, BEIJING).strftime("%Y-%m-%d %H:%M")
        die("登录令牌已于 %s 过期" % died_at, "在客户端退出并重新登录该账号后重试", tail)

    env_name = "acct-%s" % tail
    print("  识别账号：尾号%s → 环境 %s（令牌内容不显示）" % (tail, env_name))

    # 2) 确保 Environment 存在（PUT 为 upsert；GET 仅用于减少无谓请求）
    probe = gh(["api", "repos/%s/environments/%s" % (repo, env_name)], check=False)
    if probe.returncode != 0:
        gh(["api", "--method", "PUT",
            "repos/%s/environments/%s" % (repo, env_name)], check=False)
        print("  ✓ 已创建环境 %s" % env_name)
    else:
        print("  ✓ 环境 %s 已存在" % env_name)

    # 3) 写入账号专属 Secret（stdin 传递，不进 argv / 不落盘）
    gh(["secret", "set", "RACCOON_ACCESS_TOKEN",
        "--repo", repo, "--env", env_name], stdin=token)
    print("  ✓ 已写入 RACCOON_ACCESS_TOKEN")

    # 4) 登记到动态账号列表（幂等）
    cur = gh(["variable", "get", "RACCOON_ACCOUNTS", "--repo", repo], check=False)
    existing = [t for t in re.split(r"[,\s]+", (cur.stdout or "").strip())
                if re.fullmatch(r"\d{4}", t)]
    if tail not in existing:
        existing.append(tail)
        gh(["variable", "set", "RACCOON_ACCOUNTS", "--repo", repo,
            "--body", ",".join(existing)])
        print("  ✓ 已登记账号列表：%s" % ",".join(existing))
    else:
        print("  ✓ 账号已在列表中，无需重复登记")

    # 5) 触发云端只读自检
    if not args.no_run:
        view = gh(["repo", "view", repo, "--json", "defaultBranchRef",
                   "--jq", ".defaultBranchRef.name"], check=False)
        branch = (view.stdout or "").strip() or "main"
        p = gh(["workflow", "run", "checkin.yml", "--repo", repo,
                "--ref", branch, "-f", "check_only=true"], check=False)
        if p.returncode == 0:
            print("  ✓ 已触发云端只读自检")
        else:
            print("  ! 自检触发失败（令牌已写入，不影响明天定时）：%s"
                  % (p.stderr or "").strip()[:150])

    log(tail, "环境 %s" % env_name, True)
    print("\n【成功】尾号%s 已同步。从明天起每天北京时间 09:17 云端自动登录延效。" % tail)
    print("       只有签到失败或令牌临期（剩余 7 天）才会收到企业微信提醒。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
