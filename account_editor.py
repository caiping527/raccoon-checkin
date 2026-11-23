# -*- coding: utf-8 -*-
"""小浣熊签到 · 账号管理（模块私有，只属于本模块）

账号密码存在本模块的「账号.json」里，只在本地使用，界面明文显示。
一个账号框、一个密码框、一个「保存」按钮：填好点保存就自动进下面的大框；
大框里选中一行点「删除选中」即可移除，不用输入逗号之类的分隔符。

这里只改本机文件，不碰云端。只有「一键更新.bat」才会把账号同步到 GitHub。
模块被删除时，工具箱其它栏目不受影响：本文件由小浣熊栏目按需动态加载。
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

CONFIG_PATH = os.path.join(HERE, '账号.json')


def load():
    try:
        with open(CONFIG_PATH, encoding='utf-8') as f:
            data = json.load(f)
        return [a for a in (data.get('accounts') or []) if isinstance(a, dict)]
    except Exception:
        return []


def save(accs):
    tmp = CONFIG_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({
            '_说明': '小浣熊签到模块私有账号；明文仅存本机，请勿外发。账号与密码分栏填写，不用逗号。',
            'accounts': accs,
        }, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def valid(phone, pwd):
    return bool(re.fullmatch(r'1\d{10}', phone) and pwd)


def open_editor(parent=None):
    own = parent is None
    if own:
        root = tk.Tk()
        win = root
    else:
        root = None
        win = tk.Toplevel(parent)
        win.transient(parent)
    win.title('小浣熊签到 · 账号与运行时间')
    place_top_right(win, 700, 700, parent=parent)
    win.minsize(660, 640)
    win.configure(bg='#F4F7FC')

    tk.Label(win, text='小浣熊账号', bg='#F4F7FC', fg='#1B3A66',
             font=('Microsoft YaHei UI', 15, 'bold')).pack(anchor='w', padx=18, pady=(14, 2))
    tk.Label(win, bg='#F4F7FC', fg='#5A6B82', justify='left',
             font=('Microsoft YaHei UI', 9),
             text='账号只保存在本模块（小浣熊签到更新\\账号.json），明文显示，本机自用。\n'
                  '填好账号和密码点「保存」，就会自动加到下面的大框里；选中一行可删除。\n'
                  '不用输入逗号、空格或任何分隔符。').pack(anchor='w', padx=18, pady=(0, 10))

    form = tk.Frame(win, bg='#FFFFFF')
    form.pack(fill='x', padx=18)
    tk.Label(form, text='账号（手机号）', bg='#FFFFFF', fg='#2B3A4D',
             font=('Microsoft YaHei UI', 9)).grid(row=0, column=0, sticky='w', padx=12, pady=(10, 2))
    tk.Label(form, text='密码', bg='#FFFFFF', fg='#2B3A4D',
             font=('Microsoft YaHei UI', 9)).grid(row=0, column=1, sticky='w', padx=12, pady=(10, 2))
    e_phone = tk.Entry(form, width=26, font=('Consolas', 11))
    e_pwd = tk.Entry(form, width=26, font=('Consolas', 11))
    e_phone.grid(row=1, column=0, sticky='ew', padx=12, pady=(0, 10))
    e_pwd.grid(row=1, column=1, sticky='ew', padx=12, pady=(0, 10))
    form.columnconfigure(0, weight=1)
    form.columnconfigure(1, weight=1)

    bar = tk.Frame(win, bg='#F4F7FC')
    bar.pack(fill='x', padx=18, pady=(8, 2))
    tip = tk.Label(win, text='', bg='#F4F7FC', fg='#16704A', justify='left',
                   font=('Microsoft YaHei UI', 9), wraplength=540)
    tip.pack(anchor='w', padx=18)

    tk.Label(win, text='已保存的账号（明文，可删除）', bg='#F4F7FC', fg='#2B3A4D',
             font=('Microsoft YaHei UI', 9)).pack(anchor='w', padx=18, pady=(8, 0))
    box = tk.Listbox(win, font=('Consolas', 11), height=6,
                     selectmode='single', activestyle='dotbox')
    box.pack(fill='both', expand=True, padx=18, pady=(4, 4))

    def refresh():
        box.delete(0, 'end')
        for a in load():
            box.insert('end', '  %s    %s' % (a.get('phone', ''), a.get('password', '')))

    def do_add():
        phone = e_phone.get().strip()
        pwd = e_pwd.get().strip()
        if not valid(phone, pwd):
            tip.configure(fg='#B4402F', text='请填 11 位手机号和非空密码，中间不用加逗号。')
            return
        accs = [a for a in load() if str(a.get('phone') or '').strip() != phone]
        accs.append({'phone': phone, 'password': pwd, 'enabled': True})
        try:
            save(accs)
        except Exception as e:
            tip.configure(fg='#B4402F', text='保存失败：%s' % e)
            return
        e_phone.delete(0, 'end')
        e_pwd.delete(0, 'end')
        refresh()
        tip.configure(fg='#16704A',
                      text='已保存 %s****%s，共 %d 个账号。' % (phone[:3], phone[-4:], len(accs)))

    def do_del():
        sel = box.curselection()
        if not sel:
            tip.configure(fg='#B4402F', text='先在下面的大框里选中一行，再点删除。')
            return
        idx = sel[0]
        accs = load()
        if idx >= len(accs):
            return
        gone = accs.pop(idx)
        try:
            save(accs)
        except Exception as e:
            tip.configure(fg='#B4402F', text='删除失败：%s' % e)
            return
        refresh()
        tip.configure(fg='#16704A', text='已删除 %s，剩 %d 个账号。'
                      % (str(gone.get('phone') or ''), len(accs)))

    ttk.Button(bar, text='保存', command=do_add).pack(side='left')
    ttk.Button(bar, text='删除选中', command=do_del).pack(side='left', padx=8)
    ttk.Button(bar, text='关闭',
               command=(win.destroy if not own else root.destroy)).pack(side='right')
    tk.Label(bar, text='双击「一键更新」才会同步到云端', bg='#F4F7FC',
             fg='#7A8899', font=('Microsoft YaHei UI', 9)).pack(side='right', padx=8)

    accs = load()
    if accs:
        tip.configure(text='已保存 %d 个账号。' % len(accs))
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
