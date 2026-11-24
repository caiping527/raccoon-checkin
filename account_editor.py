# -*- coding: utf-8 -*-
"""小浣熊签到 · 账号管理（改的是 AI路由魔改版 里的账号）

为什么要改
----------
以前这个小工具改的是本模块的「账号.json」，等于同一个号要在两个地方各存一份：
AI路由 里登一次、这里再填一次，改密码还得改两处、两边还可能不一致。

现在只改 AI路由 这一份（data\\accounts\\raccoon\\credentials.json）：

  · **保存**：调 AI路由 自己的登录接口（account.create / passwordLogin），
    由 AI路由 完成登录 → 换令牌 → 加密保存密码。所以存进去的密码一定能用，
    不是「写了但登录失败」那种假成功。
  · **删除**：调 AI路由 的删号接口，内存里的号池与磁盘文件同时清掉。
  · AI路由 没在运行时：只允许改**已有账号**的密码（直接改本地凭据文件，
    加密后写入），新增账号需要联网登录，会提示先启动 AI路由。

本机还会自动找账号目录（见 公共模块\\airoute_accounts.py），换电脑把整个
「Github&Gitee签到」文件夹拷走即可，副本里带着最新凭据。
"""
from __future__ import annotations

import json
import os
import re
import sys
import tkinter as tk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(HERE)
# 共用小工具统一放在上一级的「公共模块」里；根目录也留着，老版本照旧能跑
for _p in (os.path.join(_PARENT, '公共模块'), _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from ui_pos import place_top_right          # 窗口统一弹在屏幕右上角（左边在看电视）

try:
    import airoute_accounts as AA
except Exception as _exc:                   # 公共模块被删时给出可读的提示
    AA = None
    _IMPORT_ERROR = str(_exc)
else:
    _IMPORT_ERROR = ''


def _mask(pwd):
    """密码只显示首尾各一位，中间打码（本机自用，够核对即可）。"""
    text = str(pwd or '')
    if not text:
        return '（无法解密）'
    if len(text) <= 2:
        return '*' * len(text)
    return text[0] + '*' * (len(text) - 2) + text[-1]


def load():
    """当前 AI路由 里的全部小浣熊账号。"""
    if AA is None:
        return []
    try:
        return AA.raccoon_accounts()
    except Exception:
        return []


def _status_text():
    """顶部来源说明：读的是哪个目录、AI路由 在不在跑。"""
    if AA is None:
        return '公共模块 airoute_accounts.py 未加载：%s' % _IMPORT_ERROR
    try:
        path, why = AA.explain()
    except Exception as exc:
        return '账号目录解析失败：%s' % exc
    running = 'AI路由 运行中（保存即登录）' if AA.service_running() else 'AI路由 未运行（只能改已有账号的密码）'
    return '账号来源：%s\n%s\n%s' % (path, why, running)


def _update_password_offline(phone, password):
    """AI路由 没在跑时，直接改本地凭据文件里该账号的 passwordEnc。

    只动 secret 里的密码，其余字段（令牌、uid、昵称）原样保留 —— 令牌就算
    还剩几天也能继续用，下次到期重登时用的就是新密码。
    """
    enc = AA.encrypt_credential(password)
    if not enc:
        raise RuntimeError('缺少 pycryptodome，无法加密密码（pip install pycryptodome）')
    path = os.path.join(AA.data_dir(), 'accounts', 'raccoon', 'credentials.json')
    if not os.path.isfile(path):
        raise RuntimeError('找不到 AI路由 的小浣熊凭据文件：%s' % path)
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)

    hit = False
    for key, val in list(raw.items()):
        obj = json.loads(val) if isinstance(val, str) else val
        if not isinstance(obj, dict):
            continue
        secret = obj.get('secret') or {}
        if str(secret.get('phone') or '').strip() != phone:
            continue
        secret['passwordEnc'] = enc
        obj['secret'] = secret
        raw[key] = json.dumps(obj, ensure_ascii=False) if isinstance(val, str) else obj
        hit = True
    if not hit:
        raise RuntimeError('AI路由 里没有这个账号，新增账号请先启动 AI路由')

    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return '已更新本地密码（AI路由 未运行，下次启动后生效）'


def open_editor(parent=None):
    own = parent is None
    if own:
        root = tk.Tk()
        win = root
    else:
        root = None
        win = tk.Toplevel(parent)
        win.transient(parent)
    win.title('小浣熊签到 · 账号（统一在 AI路由 里维护）')
    place_top_right(win, 760, 720, parent=parent)
    win.minsize(700, 660)
    win.configure(bg='#F4F7FC')

    tk.Label(win, text='小浣熊账号', bg='#F4F7FC', fg='#1B3A66',
             font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor='w', padx=18, pady=(14, 2))
    tk.Label(win, bg='#F4F7FC', fg='#5A6B82', justify='left',
             font=('Microsoft YaHei UI', 9),
             text='账号与 AI路由魔改版 共用同一份：这里保存 = 写进 AI路由，'
                  '签到云端也读同一份，不会再出现两边不一致。\n'
                  '填手机号和密码点「保存」即可（新增或改密码都行）；选中一行可删除。').pack(
        anchor='w', padx=18, pady=(0, 8))

    src = tk.Label(win, text=_status_text(), bg='#EAF1FB', fg='#2B4A73', justify='left',
                   font=('Microsoft YaHei UI', 8), wraplength=700, anchor='w')
    src.pack(fill='x', padx=18, pady=(0, 8), ipady=4)

    form = tk.Frame(win, bg='#FFFFFF')
    form.pack(fill='x', padx=18)
    tk.Label(form, text='账号（手机号）', bg='#FFFFFF', fg='#2B3A4D',
             font=('Microsoft YaHei UI', 9)).grid(row=0, column=0, sticky='w', padx=12, pady=(10, 2))
    tk.Label(form, text='密码', bg='#FFFFFF', fg='#2B3A4D',
             font=('Microsoft YaHei UI', 9)).grid(row=0, column=1, sticky='w', padx=12, pady=(10, 2))
    e_phone = tk.Entry(form, width=26, font=('Consolas', 11))
    e_pwd = tk.Entry(form, width=26, font=('Consolas', 11), show='*')
    e_phone.grid(row=1, column=0, sticky='ew', padx=12, pady=(0, 10))
    e_pwd.grid(row=1, column=1, sticky='ew', padx=12, pady=(0, 10))
    form.columnconfigure(0, weight=1)
    form.columnconfigure(1, weight=1)

    bar = tk.Frame(win, bg='#F4F7FC')
    bar.pack(fill='x', padx=18, pady=(8, 2))
    tip = tk.Label(win, text='', bg='#F4F7FC', fg='#16704A', justify='left',
                   font=('Microsoft YaHei UI', 9), wraplength=690)
    tip.pack(anchor='w', padx=18)

    tk.Label(win, text='AI路由 里的小浣熊账号', bg='#F4F7FC', fg='#2B3A4D',
             font=('Microsoft YaHei UI', 9)).pack(anchor='w', padx=18, pady=(8, 0))
    box = tk.Listbox(win, font=('Consolas', 10), height=7,
                     selectmode='single', activestyle='dotbox')
    box.pack(fill='both', expand=True, padx=18, pady=(4, 4))

    rows = []          # 与 Listbox 行一一对应，删除时按行取 uid

    def refresh():
        box.delete(0, 'end')
        rows.clear()
        data = load()
        for a in data:
            rows.append(a)
            box.insert('end', '  %-13s 尾号 %-5s %-12s %s' % (
                a.get('phone', ''), a.get('tail', ''), a.get('nickname', '')[:12], _mask(a.get('password'))))
        src.configure(text=_status_text())

    def do_save():
        phone = e_phone.get().strip()
        pwd = e_pwd.get().strip()
        if not re.fullmatch(r'1\d{10}', phone):
            tip.configure(fg='#B4402F', text='请填 11 位手机号。')
            return
        if not pwd:
            tip.configure(fg='#B4402F', text='密码不能为空。')
            return
        if AA is None:
            tip.configure(fg='#B4402F', text='公共模块未加载，无法保存：%s' % _IMPORT_ERROR)
            return
        try:
            if AA.service_running():
                tip.configure(fg='#2B4A73', text='正在让 AI路由 登录并保存……')
                win.update_idletasks()
                AA.raccoon_save(phone, pwd)
                msg = '已写入 AI路由 并完成登录：%s' % phone
            else:
                msg = _update_password_offline(phone, pwd)
        except Exception as exc:
            tip.configure(fg='#B4402F', text='保存失败：%s' % exc)
            return
        e_phone.delete(0, 'end')
        e_pwd.delete(0, 'end')
        refresh()
        tip.configure(fg='#16704A', text='%s（共 %d 个账号）' % (msg, len(rows)))

    def do_del():
        sel = box.curselection()
        if not sel:
            tip.configure(fg='#B4402F', text='先在下面的大框里选中一行，再点删除。')
            return
        acc = rows[sel[0]]
        if AA is None:
            tip.configure(fg='#B4402F', text='公共模块未加载，无法删除。')
            return
        try:
            if AA.service_running():
                AA.raccoon_delete(acc['uid'])
                msg = '已从 AI路由 删除 %s' % acc['phone']
            else:
                _delete_offline(acc)
                msg = '已从本地凭据删除 %s（AI路由 未运行）' % acc['phone']
        except Exception as exc:
            tip.configure(fg='#B4402F', text='删除失败：%s' % exc)
            return
        refresh()
        tip.configure(fg='#16704A', text='%s，剩 %d 个账号。' % (msg, len(rows)))

    def _delete_offline(acc):
        path = os.path.join(AA.data_dir(), 'accounts', 'raccoon', 'credentials.json')
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        gone = None
        for key, val in list(raw.items()):
            obj = json.loads(val) if isinstance(val, str) else val
            if isinstance(obj, dict) and str(obj.get('uid') or key) == acc['uid']:
                gone = key
                break
        if gone is None:
            raise RuntimeError('本地凭据里找不到该账号')
        raw.pop(gone, None)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    ttk.Button(bar, text='保存到 AI路由', command=do_save).pack(side='left')
    ttk.Button(bar, text='删除选中', command=do_del).pack(side='left', padx=8)
    ttk.Button(bar, text='刷新', command=refresh).pack(side='left')
    ttk.Button(bar, text='关闭',
               command=(win.destroy if not own else root.destroy)).pack(side='right')
    tk.Label(bar, text='保存后云端签到读到的就是这一份', bg='#F4F7FC',
             fg='#7A8899', font=('Microsoft YaHei UI', 9)).pack(side='right', padx=8)

    refresh()

    # ── 云端签到时间：直接挂在这个窗口里，不另开窗口 ──
    # 时间属于本模块私有的云端配置，跟账号放一起最直观：改账号、改时间都在同一页。
    # 面板由同目录的「签到时间.py」提供；它被删掉时只影响这一块，账号管理照常用。
    try:
        import importlib.util
        _spec = importlib.util.spec_from_file_location(
            'raccoon_schedule', os.path.join(HERE, '签到时间.py'))
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.make_panel(win).pack(fill='x', padx=18, pady=(6, 6))
    except Exception as exc:  # noqa: BLE001
        tk.Label(win, text='签到时间面板未加载：%s（账号功能不受影响）' % exc,
                 bg='#F4F7FC', fg='#B4402F', justify='left',
                 font=('Microsoft YaHei UI', 9), wraplength=640).pack(anchor='w', padx=18)

    if own:
        root.mainloop()
    else:
        win.grab_set()
    return win


if __name__ == '__main__':
    open_editor(None)
