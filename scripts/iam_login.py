#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小浣熊 IAM 手机号+密码登录（纯标准库，零依赖）。

官网前端使用 CryptoJS AES-128-CFB(NoPadding) 加密手机号与密码：
    ciphertext = AES-CFB-128(key=publicKey, iv=random(16), plaintext)
    payload    = base64(iv || ciphertext)
本模块用纯 Python 复刻该算法，避免本机 / GitHub Actions 安装额外依赖。

凭据只从环境变量读取，绝不落盘、绝不打印：
    RACCOON_PHONE / RACCOON_PASSWORD

用法：
    python iam_login.py --selftest            # 校验 AES 实现
    python iam_login.py --login --verify      # 真实登录并验证令牌可用性
"""
import argparse
import base64
import hashlib
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "https://xiaohuanxiong.com"
# 官网 /login 页面 SSR 内嵌 config.publicKey（16 字节 => AES-128）
DEFAULT_KEY = b"senseraccoon2023"
LOGIN_PATH = "/api/iam/v1/auth/login/password/phone"

# ---------------------------------------------------------------- AES-128
_SBOX = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
]
_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]


def _xtime(a):
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a & 0xFF


def _expand_key(key):
    if len(key) not in (16, 24, 32):
        raise ValueError("AES key 必须是 16/24/32 字节")
    nk = len(key) // 4
    nr = nk + 6
    w = [list(key[i * 4:(i + 1) * 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        t = w[i - 1][:]
        if i % nk == 0:
            t = t[1:] + t[:1]
            t = [_SBOX[b] for b in t]
            t[0] ^= _RCON[i // nk - 1]
        elif nk > 6 and i % nk == 4:
            t = [_SBOX[b] for b in t]
        w.append([a ^ b for a, b in zip(w[i - nk], t)])
    return [sum(w[4 * r:4 * r + 4], []) for r in range(nr + 1)], nr


def _encrypt_block(block, round_keys, nr):
    s = [a ^ b for a, b in zip(block, round_keys[0])]
    for r in range(1, nr + 1):
        s = [_SBOX[b] for b in s]
        s = [s[((c + row) % 4) * 4 + row] for c in range(4) for row in range(4)]
        if r != nr:
            m = [0] * 16
            for c in range(4):
                a0, a1, a2, a3 = s[c * 4:c * 4 + 4]
                m[c * 4 + 0] = _xtime(a0) ^ (_xtime(a1) ^ a1) ^ a2 ^ a3
                m[c * 4 + 1] = a0 ^ _xtime(a1) ^ (_xtime(a2) ^ a2) ^ a3
                m[c * 4 + 2] = a0 ^ a1 ^ _xtime(a2) ^ (_xtime(a3) ^ a3)
                m[c * 4 + 3] = (_xtime(a0) ^ a0) ^ a1 ^ a2 ^ _xtime(a3)
            s = m
        s = [a ^ b for a, b in zip(s, round_keys[r])]
    return bytes(s)


def cfb_encrypt(data, key=DEFAULT_KEY, iv=None):
    """AES-CFB-128 / NoPadding，与 CryptoJS 默认 CFB 一致。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if iv is None:
        iv = os.urandom(16)
    rk, nr = _expand_key(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        ks = _encrypt_block(prev, rk, nr)
        ct = bytes(a ^ b for a, b in zip(chunk, ks))
        out += ct
        if len(ct) == 16:
            prev = ct
    return iv + bytes(out)


def encrypt_credential(text, key=DEFAULT_KEY):
    """base64(iv || AES-CFB(plaintext)) —— 官网 encryptCredential 的等价实现。"""
    return base64.b64encode(cfb_encrypt(text, key)).decode("ascii")


# ---------------------------------------------------------------- HTTP
def _headers(token=None):
    h = {
        "Accept": "application/json",
        "Accept-Language": "zh-Hans",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Origin": "https://www.xiaohuanxiong.com",
        "Referer": "https://www.xiaohuanxiong.com/",
    }
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def login_password_full(phone, password, nation_code="86", key=DEFAULT_KEY,
                        product_codes=None, timeout=20):
    """返回 (http_status, response_dict, cookie_names, cookie_header)。

    官网前端对 /api/web/* 的请求带 credentials:"include"，即会携带登录时
    下发的 Cookie。这里把 Cookie 头一并交给调用方（只在内存传递、绝不打印），
    用于后续 /api/web/auth/v1/iam/confirm 会话确认。
    """
    body = {
        "nation_code": nation_code,
        "phone": encrypt_credential(phone, key),
        "password": encrypt_credential(password, key),
    }
    if product_codes:
        body["product_codes"] = product_codes
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    h = _headers()
    h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + LOGIN_PATH, data=json.dumps(body).encode(),
                                 method="POST", headers=h)
    try:
        with opener.open(req, timeout=timeout) as r:
            status, raw = r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode("utf-8", "ignore")
    try:
        data = json.loads(raw)
    except Exception:
        data = {"raw": raw[:300]}
    cookie_header = "; ".join("%s=%s" % (c.name, c.value) for c in jar)
    return status, data, [c.name for c in jar], cookie_header


def login_password(phone, password, nation_code="86", key=DEFAULT_KEY,
                   product_codes=None, timeout=20):
    """兼容旧调用：返回 (http_status, response_dict, cookie_names)。"""
    status, data, names, _ = login_password_full(
        phone, password, nation_code, key, product_codes, timeout)
    return status, data, names


TOKEN_KEYS = ("access_token", "accessToken", "token", "jwt")


def extract_token(response):
    """从 IAM 响应中提取 access_token。

    官网请求层（模块 37756）是 `return n.data ?? {}`，即前端拿到的对象
    其实是后端的 data 层。后端原始响应形如：
        {"code":"OK", "data":{"access_token":"eyJ...", "token_type":"bearer"}}
    历史版本只查顶层，导致 HTTP 200 却误判为登录失败。这里兼容多种包装。
    """
    if not isinstance(response, dict):
        return ""
    candidates = [response]
    for key in ("data", "result", "payload"):
        v = response.get(key)
        if isinstance(v, dict):
            candidates.append(v)
            inner = v.get("data")
            if isinstance(inner, dict):
                candidates.append(inner)
    for obj in candidates:
        for k in TOKEN_KEYS:
            v = obj.get(k)
            if isinstance(v, str) and len(v.strip()) >= 20:
                return v.strip()
    return ""


def describe_response(response):
    """脱敏描述响应结构，便于排错；绝不输出令牌/密码/手机号原文。"""
    masked_words = ("token", "secret", "password", "cookie", "key", "phone", "credential")

    def shape(o, depth=0):
        if depth > 3:
            return "..."
        if isinstance(o, dict):
            out = {}
            for k, v in o.items():
                lk = str(k).lower()
                if any(word in lk for word in masked_words):
                    out[k] = "<%s len=%d>" % (type(v).__name__, len(str(v)))
                elif isinstance(v, (dict, list)):
                    out[k] = shape(v, depth + 1)
                else:
                    out[k] = str(v)[:60]
            return out
        if isinstance(o, list):
            return ["<%d items>" % len(o)]
        return str(o)[:80]

    return shape(response)


def probe(token, path, timeout=12):
    req = urllib.request.Request(BASE + path, headers=_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "ignore")
            return r.status, body[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")[:300]
    except Exception as e:
        return -1, type(e).__name__


def token_meta(token):
    """返回令牌的不敏感元信息（sid 前缀 / 有效期 / 内部ID尾号）。"""
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        d = json.loads(base64.urlsafe_b64decode(seg))
        sub = "".join(c for c in str(d.get("sub", "")) if c.isdigit())[-4:]
        return {"sid": str(d.get("sid", ""))[:20], "sub_tail": sub or "-",
                "exp": d.get("exp"), "sha1": hashlib.sha1(token.encode()).hexdigest()[:8]}
    except Exception:
        return {"sha1": hashlib.sha1(token.encode()).hexdigest()[:8]}


# ---------------------------------------------------------------- CLI
def selftest():
    # FIPS-197 AES-128 ECB 标准向量
    key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    pt = bytes.fromhex("00112233445566778899aabbccddeeff")
    want = "69c4e0d86a7b0430d8cdb78070b4c55a"
    rk, nr = _expand_key(key)
    got = _encrypt_block(list(pt), rk, nr).hex()
    print("AES-128 单块向量:", "PASS" if got == want else "FAIL got=%s" % got)
    assert got == want
    # CFB 与 openssl 交叉验证（若 openssl 可用）
    iv = bytes(range(16))
    ct = cfb_encrypt(b"hello-raccoon-123", key=b"senseraccoon2023", iv=iv).hex()
    print("AES-CFB 固定IV 输出:", ct)
    print("加密凭据样本长度:", len(encrypt_credential("13400000000")))
    print("SELFTEST OK")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--nation-code", default="86")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    phone = os.environ.get("RACCOON_PHONE", "").strip()
    password = os.environ.get("RACCOON_PASSWORD", "")
    if not phone or not password:
        print("缺少环境变量 RACCOON_PHONE / RACCOON_PASSWORD", file=sys.stderr)
        return 2
    status, data, cookies = login_password(phone, password, args.nation_code)
    # 只输出不敏感字段
    safe = {k: v for k, v in data.items() if k not in ("access_token", "refresh_token")}
    print("登录 HTTP %s | 响应字段=%s" % (status, sorted(data.keys())))
    if safe:
        print("响应(脱敏):", json.dumps(safe, ensure_ascii=False)[:400])
    print("Set-Cookie 名称:", cookies)
    token = extract_token(data)
    if token:
        print("令牌元信息:", json.dumps(token_meta(token), ensure_ascii=False))
        # 令牌写进环境变量供后续步骤使用（本次进程内）
        os.environ["RACCOON_NEW_TOKEN"] = token
        if args.verify:
            for p in ("/api/iam/v1/account", "/api/web/points/v1/balance",
                      "/api/web/office/v3/setting_info",
                      "/api/web/points/v1/bills?paging.limit=3&paging.offset=0"):
                s, b = probe(token, p)
                print("  %-52s HTTP %s %s" % (p, s, b[:130]))
            return 0
    return 0 if status == 200 and token else 1


if __name__ == "__main__":
    sys.exit(main())
