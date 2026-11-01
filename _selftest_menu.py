"""主菜单：启动不跳过时进主菜单，按 1 进入等待开始。

运行：SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3.11 _selftest_menu.py
"""

import builtins
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
# 本文件要测主菜单，必须关掉跳过
os.environ["TANK_SKIP_MENU"] = "0"
# 清掉可能让 skip_main_menu() 为真的 CLI 痕迹：本脚本 argv 不含 --1p
os.environ.pop("TANK_MODE", None)

import pygame

import tank_game as g

assert g.skip_main_menu() is False, "本场景应进入主菜单"

seen = {"menu": False, "await": False, "texts": []}
orig_print = builtins.print


def capturing_print(*args, **kwargs):
    line = " ".join(str(a) for a in args)
    if "[menu] 主菜单" in line:
        seen["menu"] = True
    if "等待 Start" in line or "主菜单 →" in line:
        seen["await"] = True
    return orig_print(*args, **kwargs)


builtins.print = capturing_print

real_load = g.load_font
fonts = {}


class Rec:
    def __init__(self, f):
        self._f = f

    def render(self, text, *a, **kw):
        seen["texts"].append((frame["i"], text))
        return self._f.render(text, *a, **kw)

    def __getattr__(self, n):
        return getattr(self._f, n)


def load_rec(size, bold=False):
    f = real_load(size, bold)
    fonts[id(f)] = fonts.get(id(f)) or Rec(f)
    return fonts[id(f)]


g.load_font = load_rec
frame = {"i": 0}


def fake_get():
    i = frame["i"]
    frame["i"] += 1
    # 帧 10：数字 1 → 单人 vs 电脑 → 等待开始
    if i == 10:
        return [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_1)]
    if i >= 40:
        return [pygame.event.Event(pygame.QUIT)]
    return []


pygame.event.get = fake_get
try:
    try:
        g.main()
    except SystemExit:
        pass
finally:
    builtins.print = orig_print
    g.load_font = real_load

assert seen["menu"], "启动应打印主菜单日志"
assert seen["await"], "按 1 后应进入等待开始"
titles = [t for _, t in seen["texts"]
          if "坦克对战" in t or t == "TANK DUEL"
          or "单人 vs 电脑" in t or "1P vs CPU" in t]
assert titles, f"应渲染主菜单/等待标题：{seen['texts'][:30]}"
print("MAIN_MENU OK：主菜单 → 按 1 → 等待开始")
