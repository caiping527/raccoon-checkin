#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""商汤小浣熊签到 · 本机一键更新工具（双击根目录「一键更新.bat」运行）。

作用：登录小浣熊客户端后运行本工具，自动完成
  1. 读出本机登录态里的明文 access_token，解出「内部ID尾号」（不显示令牌）
  2. 调只读 IAM 接口 /api/iam/v1/account 拿掩码手机号，自动取尾 4 位作显示标签
     （自动写回「账号标签.txt」，换号无需手动编辑任何文件）
  3. 建立账号专属 GitHub Environment「acct-<手机号尾号>」并写入：
       RACCOON_ACCESS_TOKEN（登录令牌） + RACCOON_ACCOUNT_ID（内部ID尾号，仅防串号）
  4. 把手机号尾号登记到仓库变量 RACCOON_ACCOUNTS（幂等）
  5. 触发一次云端只读自检，确认链路可用

显示标签永远是手机号尾号；内部ID尾号只进 Secret 做校验，绝不进推送/日志的账号位。
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
import urllib.request
from datetime import datetime, timedelta, timezone

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BEIJING = timezone(timedelta(hours=8))
DEFAULT_AUTH = os.path.join(os.path.expanduser("~"), ".office-raccoon",
                            "agent", "config", "auth.json")
DEFAULT_REPO = "caiping527/raccoon-checkin"
ORIGIN = (os.environ.get("RACCOON_MAIN_SITE_URL") or "https://xiaohuanxiong.com").rstrip("/")
IAM_ACCOUNT_URL = ORIGIN + "/api/iam/v1/account"
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(HERE, "更新记录.txt")
LABEL_FILE = os.path.join(HERE, "账号标签.txt")
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


def load_label_map():
    """读「账号标签.txt」：每行 `内部ID尾号=手机号尾号`，# 开头为注释。"""
    mapping = {}
    if not os.path.isfile(LABEL_FILE):
        return mapping
    try:
        with open(LABEL_FILE, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = re.split(r"[=:\s]+", line, maxsplit=1)
                if len(parts) == 2 and TAIL_RE.fullmatch(parts[0].strip()) \
                        and TAIL_RE.fullmatch(parts[1].strip()):
                    mapping[parts[0].strip()] = parts[1].strip()
    except Exception:
        pass
    return mapping


def save_label(internal_tail, phone_tail):
    """幂等把映射写回文件，保留注释与其它账号行。"""
    lines = []
    if os.path.isfile(LABEL_FILE):
        try:
            with open(LABEL_FILE, encoding="utf-8-sig") as f:
                lines = f.read().splitlines()
        except Exception:
            lines = []
    if not lines:
        lines = ["# 账号显示标签映射：内部ID尾号=手机号尾号（由一键更新自动维护，无需手改）",
                 "# 内部ID尾号仅用于防串号校验，不会出现在企业微信推送里。"]
    replaced = False
    out = []
    for line in lines:
        if line.strip().startswith("#") or not line.strip():
            out.append(line)
            continue
        parts = re.split(r"[=:\s]+", line.strip(), maxsplit=1)
        if len(parts) == 2 and parts[0].strip() == internal_tail:
            if not replaced:
                out.append("%s=%s" % (internal_tail, phone_tail))
                replaced = True
            continue
        out.append(line)
    if not replaced:
        out.append("%s=%s" % (internal_tail, phone_tail))
    try:
        with open(LABEL_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(out).rstrip() + "\n")
        return True
    except Exception:
        return False


def validate_label(value):
    return value if TAIL_RE.fullmatch((value or "").strip()) else None


def main():
    ap = argparse.ArgumentParser(description="小浣熊签到令牌一键更新")
    ap.add_argument("--account", help="强制校验内部ID尾号（4 位数字）")
    ap.add_argument("--label", help="手动指定手机号尾号（IAM 接口不可用时使用）")
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
    gh(["auth", "status"], check=False)

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
    internal_tail = digits[-4:] if len(digits) >= 4 else ""
    if not internal_tail:
        die("无法从令牌识别内部ID尾号", "可用 --account <4位尾号> 手动指定")
    if args.account and args.account.strip() != internal_tail:
        die("账号校验不通过：本机内部ID尾号 %s 与指定 %s 不一致"
            % (internal_tail, args.account.strip()),
            "确认登录的是目标账号后重试")

    exp = payload.get("exp")
    if isinstance(exp, (int, float)) and exp <= datetime.now().timestamp():
        died_at = datetime.fromtimestamp(exp, BEIJING).strftime("%Y-%m-%d %H:%M")
        die("登录令牌已于 %s 过期" % died_at, "在客户端退出并重新登录该账号后重试",
            internal_tail)

    # 2) 自动取手机号尾号：IAM 接口 > 映射表 > --label > 交互输入
    phone_tail, nickname, iam_err = fetch_phone_tail(token)
    label_source = ""
    if phone_tail:
        label = phone_tail
        label_source = "IAM 接口自动识别"
        if save_label(internal_tail, phone_tail):
            print("  ✓ 已自动登记映射到「账号标签.txt」")
    elif load_label_map().get(internal_tail):
        label = load_label_map()[internal_tail]
        label_source = "账号标签.txt 缓存"
    elif validate_label(args.label):
        label = validate_label(args.label)
        label_source = "--label 手动指定"
        save_label(internal_tail, label)
    elif sys.stdin.isatty():
        label = ""
        for _ in range(3):
            typed = input("  IAM 接口不可用（%s）\n  请输入该账号手机号后 4 位："
                          % (iam_err or "未知原因")).strip()
            if TAIL_RE.fullmatch(typed):
                label = typed
                break
            print("  需要 4 位数字，请重试")
        if not label:
            die("未取得手机号尾号", "可双击后重新输入，或运行 一键更新.bat --label <手机号后4位>")
        label_source = "手动输入"
        save_label(internal_tail, label)
    else:
        die("无法自动取得手机号尾号：%s" % (iam_err or "未知原因"),
            "运行 一键更新.bat --label <手机号后4位>，或在「账号标签.txt」补一行 %s=手机号后4位"
            % internal_tail)
        return 1

    env_name = "acct-%s" % label
    print("  识别账号：手机号尾号 %s%s（%s）" % (
        label, (" · " + nickname) if nickname else "", label_source))
    print("  内部ID尾号 %s 仅用于防串号，不会出现在企业微信推送里" % internal_tail)
    print("  云端环境：%s（令牌内容不显示）" % env_name)

    # 3) 确保 Environment 存在
    probe = gh(["api", "repos/%s/environments/%s" % (repo, env_name)], check=False)
    if probe.returncode != 0:
        gh(["api", "--method", "PUT",
            "repos/%s/environments/%s" % (repo, env_name)], check=False)
        print("  ✓ 已创建环境 %s" % env_name)
    else:
        print("  ✓ 环境 %s 已存在" % env_name)

    # 4) 写入账号专属 Secret（stdin 传递，不进 argv / 不落盘）
    gh(["secret", "set", "RACCOON_ACCESS_TOKEN",
        "--repo", repo, "--env", env_name], stdin=token)
    gh(["secret", "set", "RACCOON_ACCOUNT_ID",
        "--repo", repo, "--env", env_name], stdin=internal_tail)
    print("  ✓ 已写入 RACCOON_ACCESS_TOKEN / RACCOON_ACCOUNT_ID（防串号）")

    # 5) 登记到动态账号列表（幂等）
    cur = gh(["variable", "get", "RACCOON_ACCOUNTS", "--repo", repo], check=False)
    existing = [t for t in re.split(r"[,\s]+", (cur.stdout or "").strip())
                if TAIL_RE.fullmatch(t)]
    if label not in existing:
        existing.append(label)
        gh(["variable", "set", "RACCOON_ACCOUNTS", "--repo", repo,
            "--body", ",".join(existing)])
        print("  ✓ 已登记账号列表：%s" % ",".join(existing))
    else:
        print("  ✓ 账号已在列表中，无需重复登记")

    # 6) 触发云端只读自检
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

    log(label, "环境 %s" % env_name, True)
    print("\n【成功】手机号尾号%s 已同步。每天北京时间 09:17 云端自动完成登录动作。" % label)
    print("       企业微信只在失败 / 令牌临期时推送，账号一律显示手机号尾号。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
