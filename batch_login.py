#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小浣熊签到 · 按本模块账号清单一次登记全部账号（不用切客户端、不用逐个手输）。

配置边界
--------
* 公用的 GitHub 令牌 / 仓库地址：参数配置\配置.json（各模块共用）。
* 账号密码**只有一个来源**：AI路由魔改版（data\\accounts\\raccoon\\credentials.json）。
  本目录不再保存任何账号或密码 —— 以前那份明文「账号.json」已删除，
  因为同一个号在两个地方各存一份，改密码就会漏改一处。
  要加号 / 改密码：打开 AI路由魔改版 → 渠道账号 → 小浣熊，
  或用「小浣熊签到更新.html」里的账号管理（它写的也是 AI路由）。

小浣熊（商汤）是独立账号体系，AiRoute 里没有它的凭据，做不到「直接读取」，
但也不需要切客户端：用手机号 + 密码调登录接口换独立令牌。
双击「一键更新.bat」依次登录、依次写 GitHub Environment Secrets，
并默认把密码也存进云端，让云端每天自动登录，连令牌过期都不用管。

用法：
    双击「一键更新.bat」            依次登记全部账号
    python batch_login.py --dry-run  只预览账号清单，不登录、不写 GitHub
    python batch_login.py --no-check 登记但不触发云端自检

凭据：账号密码从 AI路由魔改版 读取；换来的令牌只经 stdin 传给 gh，
      不进命令行参数、不打印、不落盘。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONF_PATH = os.path.join(ROOT, '参数配置', '配置.json')
LEGACY_CONF = os.path.join(ROOT, '配置.json')  # 旧版位置回落，只读
sys.path.insert(0, os.path.join(HERE, 'scripts'))
sys.path.insert(0, HERE)
# 公共模块：账号统一从 AI路由魔改版 读（airoute_accounts.py 在里面）
sys.path.insert(0, os.path.join(ROOT, '公共模块'))

import iam_login  # noqa: E402
import update  # noqa: E402  复用 jwt_payload / fetch_phone_tail / log / resolve_repo

BEIJING = timezone(timedelta(hours=8))
TAIL_RE = re.compile(r'^\d{4}$')


def hr():
    print('=' * 64)


def ok(msg):
    print('  OK   %s' % msg)


def bad(msg):
    print('  FAIL %s' % msg)


def read_conf():
    """只读工具箱公用配置：令牌与仓库地址。账号密码不在这里。"""
    for path in (CONF_PATH, LEGACY_CONF):
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            continue
    return {}


def accounts_of():
    """读小浣熊账号清单。

    **唯一来源**是 AI路由魔改版（data\\accounts\\raccoon\\credentials.json，
    手机号 + 加密密码）。用户只在 AI路由 里登一次，四个签到模块都从这里读。
    本目录不再有第二份清单：读不到就是读不到，直接报错让人去 AI路由 加号，
    而不是偷偷回退到一个可能早就过期、密码还和真实密码不一致的旧文件。
    """
    try:
        import airoute_accounts
    except Exception as exc:
        raise RuntimeError(
            '缺少公共模块 公共模块\\airoute_accounts.py（%s）。\n'
            '  账号只能从 AI路由魔改版 读取，请确认公共模块目录完整。' % exc)

    out = []
    for acc in airoute_accounts.raccoon_accounts():
        phone = str(acc.get('phone') or '').strip()
        pwd = str(acc.get('password') or '')
        if not re.fullmatch(r'1\d{10}', phone):
            continue
        if not pwd:
            print('  WARN %s 的密码解不出来（AI路由 里这条记录没有密码）' % phone)
            continue
        out.append((phone, pwd))
    if out:
        path, why = airoute_accounts.explain()
        print('  账号来源：AI路由魔改版（%d 个）· %s' % (len(out), why))
    return out


def gh(env, args, stdin=None, timeout=90):
    kw = dict(env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
              text=True, encoding='utf-8', errors='replace', timeout=timeout)
    if stdin is None:
        kw['stdin'] = subprocess.DEVNULL
    else:
        kw['input'] = stdin
    try:
        return subprocess.run(['gh'] + args, **kw)
    except subprocess.TimeoutExpired:
        class R:
            returncode, stdout, stderr = -1, '', 'gh 命令超时'
        return R()
    except Exception as e:
        class R:
            returncode, stdout, stderr = -1, '', str(e)
        return R()



def main():
    ap = argparse.ArgumentParser(description='按本模块账号清单一键更新小浣熊账号')
    ap.add_argument('--dry-run', action='store_true', help='只预览，不登录、不写 GitHub')
    ap.add_argument('--no-check', action='store_true', help='不触发云端自检')
    ap.add_argument('--no-store-password', action='store_true',
                    help='不把密码存进云端（默认存，云端每天自动登录）')
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    hr()
    print('  小浣熊签到 · 一键更新 AI路由魔改版 里的全部小浣熊账号')
    if args.dry_run:
        print('  【预览模式】不登录、不写入任何 Secret')
    hr()

    conf = read_conf()
    try:
        accs = accounts_of()
    except RuntimeError as exc:
        print('\n  【失败】%s' % exc)
        return 1
    if not accs:
        print('\n  没有读到任何小浣熊账号。')
        print('  账号只在 AI路由魔改版 里维护（登一次号，四个签到模块共用）：')
        print('    打开 AI路由魔改版 → 渠道账号 → 小浣熊 → 添加账号，填手机号 + 密码。')
        print('    也可以双击 小浣熊签到更新.html，用页面上的账号管理（写的同样是 AI路由）。')
        return 1

    print('  待登记账号：%d 个' % len(accs))
    for phone, _ in accs:
        print('    %s****%s' % (phone[:3], phone[-4:]))
    hr()

    if args.dry_run:
        print('\n  预览完成，未登录、未写入任何内容。')
        return 0

    env = dict(os.environ)
    token = str(conf.get('github_token') or '').strip() or env.get('GH_TOKEN', '')
    if not token:
        print('\n  缺少 GitHub 令牌。请到 参数配置\参数配置.exe 填一次。')
        return 1
    env['GH_TOKEN'] = token
    env['PYTHONIOENCODING'] = 'utf-8'
    env['PYTHONUTF8'] = '1'
    repo = str(conf.get('raccoon_repo') or update.resolve_repo(None))

    gh(env, ['auth', 'status'])
    # gh auth status 在有 GH_TOKEN 时也能通过；不通过也不硬拦，后面写入会报错
    print('  目标仓库：%s' % repo)

    done, failed = [], []
    for i, (phone, pwd) in enumerate(accs, 1):
        label_show = '%s****%s' % (phone[:3], phone[-4:])
        print('\n  [%d/%d] %s' % (i, len(accs), label_show))
        try:
            status, data, _ = iam_login.login_password(phone, pwd, '86')
            new_token = iam_login.extract_token(data)
        except Exception as e:
            bad('登录请求异常：%s' % type(e).__name__)
            failed.append(label_show)
            continue
        if status != 200 or not new_token:
            bad('密码登录失败：HTTP %s %s' % (status, str(data.get('code') or '')[:80]))
            failed.append(label_show)
            continue
        ok('登录成功，已取得独立令牌（不显示内容）')

        phone_tail, nickname, _err = update.fetch_phone_tail(new_token)
        label = phone_tail or phone[-4:]
        payload = update.jwt_payload(new_token)
        digits = ''.join(c for c in str(payload.get('sub') or '') if c.isdigit())
        internal_tail = digits[-4:] if len(digits) >= 4 else ''
        if not internal_tail:
            bad('无法从令牌识别内部 ID 尾号，跳过该账号')
            failed.append(label_show)
            continue
        env_name = 'acct-%s' % label
        ok('识别账号：尾号 %s%s → %s' % (label, (' · ' + nickname) if nickname else '',
                                      env_name))

        r = gh(env, ['api', '-X', 'PUT', 'repos/%s/environments/%s' % (repo, env_name)])
        if r.returncode != 0:
            bad('创建环境失败：%s' % (r.stderr or '').strip()[:150])
            failed.append(label_show)
            continue

        writes = [
            ('RACCOON_ACCESS_TOKEN', new_token),
            ('RACCOON_ACCOUNT_ID', internal_tail),
        ]
        if not args.no_store_password:
            writes += [('RACCOON_PHONE', phone), ('RACCOON_PASSWORD', pwd)]
        allgood = True
        for name, value in writes:
            rr = gh(env, ['secret', 'set', name, '--repo', repo, '--env', env_name],
                    stdin=value)
            if rr.returncode != 0:
                bad('写入 %s 失败：%s' % (name, (rr.stderr or '').strip()[:150]))
                allgood = False
        if not allgood:
            failed.append(label_show)
            continue
        ok('已写入 %s' % ' / '.join(n for n, _ in writes))
        done.append(label)

    if done:
        cur = gh(env, ['variable', 'get', 'RACCOON_ACCOUNTS', '--repo', repo])
        existing = [t for t in re.split(r'[,\s]+', (cur.stdout or '').strip())
                    if TAIL_RE.fullmatch(t)]
        changed = False
        for t in done:
            if t not in existing:
                existing.append(t)
                changed = True
        if changed:
            gh(env, ['variable', 'set', 'RACCOON_ACCOUNTS', '--repo', repo,
                     '--body', ','.join(existing)])
        ok('云端账号列表：%s' % (','.join(existing) or '（空）'))

    print()
    hr()
    print('  成功 %d 个，失败 %d 个' % (len(done), len(failed)))
    hr()

    if done and not args.no_check:
        view = gh(env, ['repo', 'view', repo, '--json', 'defaultBranchRef',
                        '--jq', '.defaultBranchRef.name'])
        branch = (view.stdout or '').strip() or 'main'
        r = gh(env, ['workflow', 'run', 'checkin.yml', '--repo', repo,
                     '--ref', branch, '-f', 'check_only=true'])
        print('\n  云端自检：%s' % ('已触发（约 1~3 分钟后看 Actions）'
                              if r.returncode == 0 else '触发失败，不影响定时签到'))
    return 0 if not failed else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n  已取消。')
        sys.exit(130)
    except Exception as e:
        print('\n  【失败】未预期错误：%s' % e)
        sys.exit(1)
