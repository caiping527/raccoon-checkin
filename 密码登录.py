#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小浣熊签到 · 密码登录登记工具（双击根目录「密码登录.bat」运行）。

与「一键更新.bat」的区别：
  一键更新.bat 读取桌面客户端 auth.json 里的登录态 —— 客户端切号/退出登录会撤销会话，
  导致云端 401。本工具改用官方「手机号 + 密码」接口直接换一张独立令牌，
  完全不依赖桌面客户端，也不需要打开或退出小浣熊。

流程：
  1. 本机输入手机号与密码（密码不回显、不落盘、不打印、不进命令行参数）
  2. POST /api/iam/v1/auth/login/password/phone 换取 access_token
     （官网同款 AES-128-CFB 加密，实现见 scripts/iam_login.py）
  3. 只读 IAM 接口取掩码手机号后 4 位作显示标签，JWT sub 尾号作防串号校验
  4. 写入账号专属 Environment / Secrets，登记账号列表，触发云端只读自检
  5. 可选 --store-password：把手机号+密码也写入该环境 Secret，
     之后云端每天自动登录换新令牌，连 30 天令牌过期都不用管

凭据只在本机内存中出现；传给 gh 的令牌/密码一律走 stdin。
"""
import argparse
import getpass
import json
import os
import re
import sys

import update  # 复用 gh / log / 标签映射 / 账号登记等既有逻辑

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
import iam_login  # noqa: E402

PHONE_RE = re.compile(r"^1\d{10}$")


def ask_credentials(args):
    """手机号与密码由使用者本机输入；也允许高级用户用环境变量自行提供。"""
    phone = (args.phone or os.environ.get("RACCOON_PHONE") or "").strip()
    password = args.password or os.environ.get("RACCOON_PASSWORD") or ""
    if not phone:
        if not sys.stdin.isatty():
            update.die("未取得手机号", "双击「密码登录.bat」后按提示输入，或自行设置 RACCOON_PHONE")
        phone = input("  请输入手机号（11 位，如 13800138000）：").strip()
    if not PHONE_RE.fullmatch(phone):
        update.die("手机号格式不对（需要 11 位大陆手机号）", "重新运行并输入正确手机号")
    if not password:
        if not sys.stdin.isatty():
            update.die("未取得密码", "双击「密码登录.bat」后按提示输入，或自行设置 RACCOON_PASSWORD")
        password = getpass.getpass("  请输入密码（输入不显示）：")
    if not password:
        update.die("密码为空", "重新运行并输入密码")
    return phone, password


def main():
    ap = argparse.ArgumentParser(description="小浣熊签到 · 密码登录登记")
    ap.add_argument("--phone", help="手机号（不建议在命令行明文传，留空交互输入）")
    ap.add_argument("--password", help="密码（不建议在命令行明文传，留空交互输入）")
    ap.add_argument("--nation-code", default="86")
    ap.add_argument("--repo", help="目标仓库 owner/repo")
    ap.add_argument("--store-password", action="store_true",
                    help="同时把手机号+密码写入该账号 Environment Secret，供云端每天自动登录")
    ap.add_argument("--no-run", action="store_true", help="不触发云端自检")
    args = ap.parse_args()

    print("=" * 60)
    print(" 商汤小浣熊签到 · 密码登录登记（免桌面客户端）")
    print("=" * 60)
    repo = update.resolve_repo(args.repo)
    print("  目标仓库：%s" % repo)
    if not (os.environ.get("GH_TOKEN") or "").strip():
        update.die("缺少 GH_TOKEN 环境变量",
                   "把你的 GitHub 令牌设为系统环境变量 GH_TOKEN（需 Secrets 读写 + Actions 写权限）")

    phone, password = ask_credentials(args)
    print("\n  正在用密码向官方换取独立登录会话…（凭据不外显）")
    status, data, _ = iam_login.login_password(phone, password, args.nation_code)
    token = data.get("access_token")
    if status != 200 or not token:
        code = data.get("code") or data.get("message") or data.get("raw") or "无详情"
        update.die("密码登录失败：HTTP %s %s" % (status, str(code)[:200]),
                   "确认手机号/密码正确；若提示人机验证或账号被锁定，先在官网登录页手工登录一次再试",
                   target="密码登录")
    print("  ✓ 登录成功，已取得独立令牌（%s）" % json.dumps(
        iam_login.token_meta(token), ensure_ascii=False))

    # 显示标签：优先 IAM 掩码手机号，其次输入的手机号尾 4 位
    phone_tail, nickname, iam_err = update.fetch_phone_tail(token)
    label_source = "IAM 接口自动识别" if phone_tail else "输入手机号尾号"
    label = phone_tail or phone[-4:]
    payload = update.jwt_payload(token)
    digits = "".join(c for c in str(payload.get("sub") or "") if c.isdigit())
    internal_tail = digits[-4:] if len(digits) >= 4 else ""
    if not internal_tail:
        update.die("无法从令牌识别内部ID尾号", "请反馈给维护者（令牌解析异常）", target="密码登录")
    if phone_tail:
        update.save_label(internal_tail, phone_tail)

    env_name = "acct-%s" % label
    print("  识别账号：手机号尾号 %s%s（%s）" % (
        label, (" · " + nickname) if nickname else "", label_source))
    print("  云端环境：%s" % env_name)

    probe = update.gh(["api", "repos/%s/environments/%s" % (repo, env_name)], check=False)
    if probe.returncode != 0:
        update.gh(["api", "--method", "PUT",
                   "repos/%s/environments/%s" % (repo, env_name)], check=False)
        print("  ✓ 已创建环境 %s" % env_name)
    else:
        print("  ✓ 环境 %s 已存在" % env_name)

    # 令牌与密码都经 stdin 传给 gh，不进 argv、不落盘
    update.gh(["secret", "set", "RACCOON_ACCESS_TOKEN",
               "--repo", repo, "--env", env_name], stdin=token)
    update.gh(["secret", "set", "RACCOON_ACCOUNT_ID",
               "--repo", repo, "--env", env_name], stdin=internal_tail)
    print("  ✓ 已写入 RACCOON_ACCESS_TOKEN / RACCOON_ACCOUNT_ID")

    if args.store_password:
        update.gh(["secret", "set", "RACCOON_PHONE",
                   "--repo", repo, "--env", env_name], stdin=phone)
        update.gh(["secret", "set", "RACCOON_PASSWORD",
                   "--repo", repo, "--env", env_name], stdin=password)
        print("  ✓ 已写入 RACCOON_PHONE / RACCOON_PASSWORD（云端每天自动登录，不再需要管令牌过期）")
    else:
        print("  · 未开启 --store-password：约 30 天后令牌到期时重新运行本工具即可")

    cur = update.gh(["variable", "get", "RACCOON_ACCOUNTS", "--repo", repo], check=False)
    existing = [t for t in re.split(r"[,\s]+", (cur.stdout or "").strip())
                if update.TAIL_RE.fullmatch(t)]
    if label not in existing:
        existing.append(label)
        update.gh(["variable", "set", "RACCOON_ACCOUNTS", "--repo", repo,
                   "--body", ",".join(existing)])
        print("  ✓ 已登记账号列表：%s" % ",".join(existing))
    else:
        print("  ✓ 账号已在列表中，无需重复登记")

    if not args.no_run:
        view = update.gh(["repo", "view", repo, "--json", "defaultBranchRef",
                          "--jq", ".defaultBranchRef.name"], check=False)
        branch = (view.stdout or "").strip() or "main"
        p = update.gh(["workflow", "run", "checkin.yml", "--repo", repo,
                       "--ref", branch, "-f", "check_only=true"], check=False)
        print("  ✓ 已触发云端只读自检" if p.returncode == 0
              else "  ! 自检触发失败（令牌已写入，不影响定时）：%s" % (p.stderr or "").strip()[:150])

    update.log(label, "密码登录 %s" % env_name, True)
    print("\n【成功】手机号尾号%s 已登记。该令牌与桌面客户端登录态无关，" % label)
    print("       在客户端切号 / 退出登录都不会再让它失效。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
