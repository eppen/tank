"""对局暂停：开打后 P 打开暂停 → 再按 P 继续 → 再开暂停选退出。

运行：SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3.11 _selftest_pause.py
"""

import builtins
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ["TANK_SKIP_MENU"] = "1"
os.environ.pop("TANK_MODE", None)

import pygame

import tank_game as g

assert g.PAUSE_MENU_COUNT == 3
assert g.PAUSE_MENU_RESUME == 0
labels = g.pause_menu_item_labels(ascii_only=True)
assert "Resume" in labels[0] and "Quit" in labels[2]
assert "p" in g.RESERVED_KEY_NAMES
assert g.PAD_PAUSE_FACE == pygame.CONTROLLER_BUTTON_X
assert pygame.CONTROLLER_BUTTON_X not in g.PAD_FIRE_DEFAULT_BUTTONS

seen = {"pause": False, "resume": False, "texts": []}
orig_print = builtins.print


def capturing_print(*args, **kwargs):
    line = " ".join(str(a) for a in args)
    if "[pause] 暂停" in line:
        seen["pause"] = True
    if "[pause] 继续" in line:
        seen["resume"] = True
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
    # 跳过主菜单后已在对局中：帧 5 按 P 暂停
    if i == 5:
        return [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)]
    # 帧 15 再按 P 继续
    if i == 15:
        return [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)]
    # 帧 25 再暂停，帧 30 数字 3 退出游戏
    if i == 25:
        return [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)]
    if i == 30:
        return [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_3)]
    if i >= 80:
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

assert seen["pause"], "应按 P/Esc 进入暂停"
assert seen["resume"], "应再按 P 继续"
pause_titles = [t for _, t in seen["texts"]
                if t in ("暂停", "PAUSED") or "继续" in t or "Resume" in t]
assert pause_titles, f"应渲染暂停菜单：{seen['texts'][:40]}"
print("PAUSE OK：P 暂停 → P 继续 → Esc 暂停 → 3 退出")
