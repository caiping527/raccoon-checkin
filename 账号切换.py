#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小浣熊多账号登录态管理（免登出换号）。

客户端「退出登录」会让服务端把该账号的 access_token 会话（sid）作废，
导致云端签到报 HTTP 401 authorization_verify_error。
本工具把每个账号的 auth.json 存进本机「账号凭据」目录，以后换号只替换文件，
全程不调用登录/登出接口，云端已登记的令牌就不会被撤销。

注意：账号凭据目录里是明文登录令牌，已加入 .gitignore，请勿外传。
"""

import base64
import json
import os
import shutil
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
AUTH = r"C:\Users\Administrator\.office-raccoon\agent\config\auth.json"
VAULT = os.path.join(ROOT, "账号凭据")
LABEL_FILE = os.path.join(ROOT, "账号标签.txt")


def jwt_payload(token):
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return {}


def internal_tail(token):
    sub = str(jwt_payload(token).get("sub", ""))
    digits = "".join(c for c in sub if c.isdigit())
    return digits[-4:] if digits else sub[-4:]


def load_labels():
    mapping = {}
    if not os.path.isfile(LABEL_FILE):
        return mapping
    with open(LABEL_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(k) == 4 and len(v) == 4 and k.isdigit() and v.isdigit():
                mapping[k] = v
    return mapping


def read_auth():
    if not os.path.isfile(AUTH):
        return None
    try:
        with open(AUTH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def label_of(auth):
    token = (auth or {}).get("access_token") or ""
    if not token:
        return None, None
    itail = internal_tail(token)
    return load_labels().get(itail, itail), itail


def vault_files():
    if not os.path.isdir(VAULT):
        return {}
    out = {}
    for name in os.listdir(VAULT):
        if name.endswith(".json"):
            out[name[:-5]] = os.path.join(VAULT, name)
    return out


def save_current():
    auth = read_auth()
    if not auth or not (auth.get("access_token") or "").strip():
        print("✗ 当前客户端未登录（auth.json 里没有令牌），请先登录一个账号")
        return
    label, itail = label_of(auth)
    os.makedirs(VAULT, exist_ok=True)
    dst = os.path.join(VAULT, "%s.json" % label)
    shutil.copyfile(AUTH, dst)
    print("✓ 已保存当前登录态：手机号尾号%s（内部ID尾号%s）" % (label, itail))
    print("  保险箱：%s" % dst)


def switch_to(label):
    files = vault_files()
    if label not in files:
        print("✗ 没有找到尾号 %s 的登录态，请先用「一键更新.bat」+「保存当前登录态」登记" % label)
        return
    print("\n⚠️ 请先完全关闭小浣熊客户端（直接关窗口或任务管理器结束进程）")
    print("   不要在客户端里点「退出登录」，也不要打断正在跑的任务")
    try:
        input("   确认客户端已关闭后按回车继续（Ctrl+C 取消）… ")
    except KeyboardInterrupt:
        print("\n已取消")
        return
    try:
        os.makedirs(os.path.dirname(AUTH), exist_ok=True)
        shutil.copyfile(files[label], AUTH)
    except Exception as e:
        print("✗ 写入登录态失败（客户端可能还没关干净）：%s" % e)
        return
    print("✓ 已切换到手机号尾号%s 的登录态" % label)
    print("  现在可以重新打开客户端，会自动是这个账号，无需登录、无需退出登录")


def main():
    print("=" * 52)
    print("  小浣熊 · 账号切换（免登出，保护云端令牌）")
    print("=" * 52)
    auth = read_auth()
    if auth:
        label, _ = label_of(auth)
        print("当前客户端登录：手机号尾号%s" % label)
    else:
        print("当前客户端：未登录")
    files = vault_files()
    print("已保存登录态：%s" % (", ".join(sorted(files)) or "（无）"))
    print("-" * 52)
    print("  [1] 保存当前登录态到本机")
    print("  [2] 切换到已保存的账号（换号用它，不要在客户端退出登录）")
    print("  [3] 退出")
    choice = input("请选择 [1/2/3]：").strip()
    if choice == "1":
        save_current()
    elif choice == "2":
        if not files:
            print("✗ 还没有保存过任何账号，请先选 [1]")
            return
        print("可切换账号：%s" % ", ".join(sorted(files)))
        label = input("输入要切换的手机号尾号：").strip()
        if label in files:
            switch_to(label)
        else:
            print("✗ 尾号不在列表里")
    else:
        print("已退出")


if __name__ == "__main__":
    main()
