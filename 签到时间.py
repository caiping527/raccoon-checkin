# -*- coding: utf-8 -*-
"""小浣熊签到 · 云端运行时间（本模块私有，集成在「账号管理」窗口里）

面板由 account_editor.py 嵌入；也可以单独运行本文件，得到一个同样功能的窗口。

为什么要有这个
--------------
GitHub Actions 的 schedule 是**排队执行**：计划值写得越早，实际跑起来越晚越散。
把计划值往后放，真实签到才会落进作息时间内的安全区。

怎么用
------
只填北京时间。点【一键更新到 GitHub】= 保存到本地 + 提交云端 + 校验云端，一步完成：
  1. 写回本地 .github/workflows/checkin.yml 的那条 cron（其它内容不动）
  2. 提交云端同名文件的那条 cron（先拉全文、只换这一行，不整份覆盖）
  3. 立刻校验云端：workflow 是否正常、cron 是否已生效

【测试一次（只查询，不领取）】是可选动作：让 GitHub 现在跑一遍签到脚本的
「只自检」模式（--check-only），只读状态、不模拟登录、不领取 300 积分。
用来确认改完还能正常执行，而不是等第二天早上才发现坏了。
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
# 共用小工具统一放在上一级的「公共模块」里；根目录也留着，老版本照旧能跑
for _p in (os.path.join(BASE, '公共模块'), BASE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from ui_pos import place_top_right          # 窗口统一弹在屏幕右上角（左边在看电视）
CONF = os.path.join(BASE, '参数配置', '配置.json')
LOCAL = os.path.join(HERE, '.github', 'workflows', 'checkin.yml')
REPO = 'caiping527/raccoon-checkin'
REMOTE = '.github/workflows/checkin.yml'
WORKFLOW = 'checkin.yml'
TITLE = '小浣熊签到'

NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
DELAY_MIN_H, DELAY_MAX_H = 5, 8

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

CRON_LINE = re.compile(r"^(\s*-?\s*cron:\s*')([^'\n]+)(')", re.M)
TIME_TAG = re.compile(r"\s*#\s*北京时间\s*\d{1,2}:\d{2}（[^）]*签到时间）\s*$")


def cron_to_bj(cron):
    parts = str(cron or '').split()
    if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        return None
    mi, ho = int(parts[0]), int(parts[1])
    if not (0 <= mi < 60 and 0 <= ho < 24):
        return None
    total = (ho * 60 + mi + 8 * 60) % (24 * 60)
    return '%02d:%02d' % (total // 60, total % 60)


def bj_to_cron(hhmm):
    m = re.match(r'^\s*(\d{1,2})\s*:\s*(\d{2})\s*$', str(hhmm or ''))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h < 24 and 0 <= mi < 60):
        return None
    total = (h * 60 + mi - 8 * 60) % (24 * 60)
    return '%d %d * * *' % (total % 60, total // 60)


def landing(hhmm):
    m = re.match(r'^(\d{1,2}):(\d{2})$', str(hhmm or ''))
    if not m:
        return '—'
    base = int(m.group(1)) * 60 + int(m.group(2))
    a = (base + DELAY_MIN_H * 60) % (24 * 60)
    b = (base + DELAY_MAX_H * 60) % (24 * 60)
    return '%02d:%02d–%02d:%02d' % (a // 60, a % 60, b // 60, b % 60)


def read_local_cron():
    if not os.path.isfile(LOCAL):
        raise RuntimeError('找不到本地 workflow：%s' % LOCAL)
    with open(LOCAL, encoding='utf-8', newline='') as f:
        text = f.read()
    m = CRON_LINE.search(text)
    if not m:
        raise RuntimeError('本地 workflow 里没有 cron 行')
    return m.group(2).strip(), text


def _rewrite(text, cron):
    m = CRON_LINE.search(text)
    if not m:
        return text, None
    old = m.group(2).strip()
    bj = cron_to_bj(cron) or '?'
    line_end = text.find('\n', m.end())
    line_end = len(text) if line_end < 0 else line_end
    line = TIME_TAG.sub('', text[m.start():line_end])
    new_line = "%s%s%s   # 北京时间 %s（%s · 签到时间）" % (
        m.group(1), cron, m.group(3), bj, TITLE)
    return text[:m.start()] + new_line + text[line_end:], old


def write_local_cron(cron):
    old, text = read_local_cron()
    new, old = _rewrite(text, cron)
    tmp = LOCAL + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='') as f:
        f.write(new)
    os.replace(tmp, LOCAL)
    return old


def gh(args, stdin=None, timeout=90):
    env = dict(os.environ)
    env['GH_PAGER'] = 'cat'
    try:
        with open(CONF, encoding='utf-8') as f:
            tok = str(json.load(f).get('github_token') or '').strip()
    except Exception:
        tok = ''
    if tok:
        env['GH_TOKEN'] = tok
    try:
        return subprocess.run(['gh'] + list(args), input=stdin, capture_output=True,
                              text=True, encoding='utf-8', errors='replace',
                              timeout=timeout, env=env, creationflags=NO_WINDOW)
    except FileNotFoundError:
        raise RuntimeError('未找到 GitHub CLI（gh），请先安装 https://cli.github.com/')
    except subprocess.TimeoutExpired:
        raise RuntimeError('gh 命令超时')


def remote_get():
    r = gh(['api', 'repos/%s/contents/%s' % (REPO, REMOTE)])
    if r.returncode != 0:
        raise RuntimeError('读取云端失败：%s' % (r.stderr or r.stdout).strip()[:300])
    d = json.loads(r.stdout)
    return base64.b64decode(d['content']).decode('utf-8'), d['sha']


def remote_put(text, sha, message):
    body = {'message': message,
            'content': base64.b64encode(text.encode('utf-8')).decode('ascii'),
            'sha': sha, 'branch': 'main'}
    r = gh(['api', '--method', 'PUT', 'repos/%s/contents/%s' % (REPO, REMOTE),
            '--input', '-'], stdin=json.dumps(body))
    if r.returncode != 0:
        raise RuntimeError('提交云端失败：%s' % (r.stderr or r.stdout).strip()[:300])


def cloud_state():
    r = gh(['api', 'repos/%s/actions/workflows/%s' % (REPO, WORKFLOW), '--jq', '.state'])
    if r.returncode != 0:
        return False, None, 'workflow 不可用（YAML 可能写坏了）'
    state = (r.stdout or '').strip()
    try:
        text, _sha = remote_get()
        m = CRON_LINE.search(text)
        cron = m.group(2).strip() if m else None
    except Exception:
        cron = None
    return state == 'active', cron, ('正常' if state == 'active' else '状态 %s' % state)


def update_all(hhmm):
    cron = bj_to_cron(hhmm)
    if not cron:
        return ['✗ 北京时间格式不对：%s（应写成 03:58）' % hhmm]
    bj = cron_to_bj(cron)
    out = []
    old = write_local_cron(cron)
    out.append('✓ 已保存到本地：%s → %s（UTC %s）' % (cron_to_bj(old) or old, bj, cron))
    text, sha = remote_get()
    new, rold = _rewrite(text, cron)
    if rold == cron:
        out.append('· 云端本来就是 %s（北京 %s），未重复提交' % (cron, bj))
    else:
        remote_put(new, sha, 'chore(schedule): 签到改为北京时间 %s（UTC %s）· %s'
                   % (bj, cron, TITLE))
        out.append('✓ 已更新云端：%s（北京 %s）→ %s（北京 %s）'
                   % (rold, cron_to_bj(rold) or '?', cron, bj))
    ok, actual, why = cloud_state()
    if ok and actual == cron:
        out.append('✓ 云端校验通过：workflow 正常，cron 已生效')
        out.append('  预计实际跑起来：%s（GitHub 排队会延迟 %d~%d 小时）'
                   % (landing(bj), DELAY_MIN_H, DELAY_MAX_H))
    elif ok:
        out.append('⚠ 云端 workflow 正常，但读到的 cron 是 %r，与目标 %r 不一致；'
                   '稍后再点一次【一键更新到 GitHub】' % (actual, cron))
    else:
        out.append('✗ 云端校验未通过：%s —— 建议把时间改回上一个值再提交' % why)
    return out


def trigger_test(wait_seconds=180):
    r = gh(['workflow', 'run', WORKFLOW, '--repo', REPO, '-f', 'check_only=true'])
    if r.returncode != 0:
        return ['✗ 触发失败：%s' % (r.stderr or r.stdout).strip()[:300]]
    out = ['✓ 已让 GitHub 跑一次「只自检」模式（不模拟登录、不领取积分）',
           '  等待结果（最多 %d 秒）…' % wait_seconds]
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(10)
        q = gh(['run', 'list', '--repo', REPO, '--workflow', WORKFLOW, '--limit', '1',
                '--json', 'status,conclusion,createdAt'])
        if q.returncode != 0 or not (q.stdout or '').strip():
            continue
        try:
            js = json.loads(q.stdout)
        except Exception:
            continue
        if not js:
            continue
        x = js[0]
        if x.get('status') == 'completed':
            if x.get('conclusion') == 'success':
                out.append('✓ 测试通过：云端能正常跑（%s）' % str(x.get('createdAt') or '')[:19])
            else:
                out.append('✗ 测试未通过：%s（去 Actions 页面看日志）' % x.get('conclusion'))
            return out
        out.append('  · 正在跑：%s' % x.get('status'))
    out.append('· 还没跑完（GitHub 排队可能延迟）。可过几分钟到 Actions 页面看结果。')
    return out


# ---------------------------------------------------------------- 可嵌入面板
def make_panel(parent, log_height=4):
    """在 parent 里放一个「云端签到时间」面板，返回 Frame。

    账号管理窗口直接把这一块挂进去，用户不用再开第二个窗口。
    """
    import tkinter as tk
    from tkinter import ttk, messagebox

    frame = tk.Frame(parent, bg='#F4F7FC')
    tk.Label(frame, text='云端签到时间（北京时间）', bg='#F4F7FC', fg='#1B3A66',
             font=('Microsoft YaHei UI', 10, 'bold')).pack(anchor='w')
    tk.Label(frame, bg='#F4F7FC', fg='#5A6B82', justify='left',
             font=('Microsoft YaHei UI', 9),
             text='只填北京时间。因 GITHUB 原因延迟 %d~%d 小时，'
                  '「预计实际跑起来」比计划值晚这么多。' % (DELAY_MIN_H, DELAY_MAX_H)
             ).pack(anchor='w', pady=(0, 4))

    row = tk.Frame(frame, bg='#FFFFFF', bd=1, relief='solid')
    row.pack(fill='x')
    tk.Label(row, text='北京时间', bg='#E8EEF8', fg='#26374F',
             font=('Microsoft YaHei UI', 9, 'bold')).grid(row=0, column=0, padx=10, pady=5, sticky='w')
    tk.Label(row, text='UTC', bg='#E8EEF8', fg='#26374F',
             font=('Microsoft YaHei UI', 9, 'bold')).grid(row=0, column=1, padx=10, pady=5, sticky='w')
    tk.Label(row, text='预计实际跑起来', bg='#E8EEF8', fg='#26374F',
             font=('Microsoft YaHei UI', 9, 'bold')).grid(row=0, column=2, padx=10, pady=5, sticky='w')

    entry = tk.Entry(row, width=9, font=('Consolas', 11, 'bold'), justify='center')
    entry.grid(row=1, column=0, padx=10, pady=(0, 7))
    lb_utc = tk.Label(row, text='—', bg='#FFFFFF', fg='#4A5B72',
                      font=('Consolas', 10), anchor='w')
    lb_utc.grid(row=1, column=1, padx=10, pady=(0, 7), sticky='w')
    lb_land = tk.Label(row, text='—', bg='#FFFFFF', fg='#16704A',
                       font=('Consolas', 10), anchor='w')
    lb_land.grid(row=1, column=2, padx=10, pady=(0, 7), sticky='w')

    try:
        cron, _ = read_local_cron()
        cur = cron_to_bj(cron) or ''
    except Exception:
        cron, cur = None, ''
    entry.insert(0, cur)

    def refresh(*_a):
        c = bj_to_cron(entry.get().strip())
        b = cron_to_bj(c) if c else None
        lb_utc.configure(text=c or '格式错误')
        lb_land.configure(text=landing(b) if b else '—')

    entry.bind('<KeyRelease>', refresh)
    refresh()

    log = tk.Text(frame, height=log_height, font=('Consolas', 9), bg='#FBFCFE',
                  fg='#22303F', bd=1, relief='solid', wrap='word')
    log.pack(fill='x', pady=(4, 4))

    def say(lines):
        if isinstance(lines, str):
            lines = [lines]
        log.insert('end', '\n'.join(lines) + '\n')
        log.see('end')

    state = {'busy': False}
    buttons = []

    def run_bg(fn):
        if state['busy']:
            say('· 上一个动作还没结束，请稍候')
            return

        def worker():
            try:
                lines = fn()
            except Exception as exc:
                lines = ['✗ %s' % exc]

            def done():
                state['busy'] = False
                for b in buttons:
                    b.configure(state='normal')
                say(lines)

            frame.after(0, done)

        state['busy'] = True
        for b in buttons:
            b.configure(state='disabled')
        threading.Thread(target=worker, daemon=True).start()

    def do_update():
        bj = entry.get().strip()
        if not bj_to_cron(bj):
            say('✗ 北京时间「%s」格式不对，应写成 03:58' % bj)
            return
        say('开始更新…')
        run_bg(lambda: update_all(bj))

    def do_test():
        if not messagebox.askyesno(
                '测试一次',
                '这会让 GitHub 立刻跑一次签到脚本的「只自检」模式：\n\n'
                '· 只检查令牌与登录前提\n'
                '· 不做登录动作，不领取 300 积分\n'
                '· 约 1~3 分钟出结果（GitHub 排队会更久）\n\n'
                '用来确认云端这份 workflow 改完还能正常执行。要现在测试吗？'):
            return
        say('已触发测试…')
        run_bg(lambda: trigger_test(180))

    bar = tk.Frame(frame, bg='#F4F7FC')
    bar.pack(fill='x')
    for text, fn in (('一键更新到 GitHub', do_update),
                     ('测试一次（只查询，不领取）', do_test)):
        b = ttk.Button(bar, text=text, command=fn)
        b.pack(side='left', padx=(0, 8))
        buttons.append(b)

    say('当前云端计划：北京 %s（UTC %s）' % (cur or '—', cron or '—'))
    say('改完只需点一次【一键更新到 GitHub】，本地和云端会一起更新并自动校验。')
    return frame


def open_window(_test=False):
    import tkinter as tk

    root = tk.Tk()
    root.title('%s · 云端运行时间（北京时间）' % TITLE)
    place_top_right(root, 700, 430)
    root.minsize(660, 400)
    root.configure(bg='#F4F7FC')
    tk.Label(root, text='%s · 云端运行时间' % TITLE, bg='#F4F7FC', fg='#1B3A66',
             font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor='w', padx=20, pady=(16, 8))
    make_panel(root, log_height=9).pack(fill='both', expand=True, padx=20)
    tk.Button(root, text='关闭', command=root.destroy).pack(anchor='e', padx=20, pady=12)
    if _test:
        return root
    root.mainloop()
    return None


if __name__ == '__main__':
    open_window()
