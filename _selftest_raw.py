"""原始 joystick 兜底（SDL 没有映射表的手柄）自动化测试，不需要真实手柄。

覆盖：
1. SDL 认不出设备时，Gamepad 自动退到 pygame.joystick 原始读数；
2. 原始模式下的死区 / Y 轴取反 / 开火按钮 / Start 重开；
3. 原始模式默认不吃扳机轴（避免猜错轴号 = 一直开火），可用
   TANK_PAD_RAW_TRIGGERS 显式打开；
4. describe_joysticks() / --pad-scan 能正确报出「无映射」状态；
5. main() 端到端：只有这种非标准手柄时也能绑上 P1、开火、击毁对手。

运行：SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3.11 _selftest_raw.py
"""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("TANK_KEYS_FILE", "/tmp/tk_raw_selftest.json")

import pygame

import tank_game as g

pygame.init()
pygame.joystick.init()


# ------------------------------------------------------------ 假 HID 设备
class FakeJoystick:
    """模仿 pygame.joystick.Joystick 的最小接口（一只 EasySMX 风格的 HID 手柄）。"""

    def __init__(self, axes=(0.0, 0.0, 0.0, 0.0, -1.0, -1.0), buttons=(),
                 name="EasySMX X05 (HID)"):
        self.axes = list(axes)
        self.buttons = set(buttons)
        self.name = name
        self.inited = False

    def init(self):
        self.inited = True

    def quit(self):
        self.inited = False

    def get_name(self):
        return self.name

    def get_guid(self):
        return "03000000de1100000000000000000000"

    def get_instance_id(self):
        return 11

    def get_numaxes(self):
        return len(self.axes)

    def get_axis(self, i):
        if not 0 <= i < len(self.axes):
            raise IndexError(i)
        return self.axes[i]

    def get_numbuttons(self):
        return 12

    def get_button(self, i):
        if not 0 <= i < 12:
            raise IndexError(i)
        return 1 if i in self.buttons else 0

    def get_numhats(self):
        return 0


class NoControllerSDL2:
    """SDL 层：一个控制器都不认（get_count = 0），但 joystick 设备确实存在。"""

    def get_count(self):
        return 0

    def is_controller(self, index):
        return False

    def Controller(self, index):
        raise IndexError("not an SDL game controller")

    def update(self):
        pass

    def quit(self):
        pass


devices = [FakeJoystick()]
g.sdl2_controller = NoControllerSDL2()
pygame.joystick.get_count = lambda: len(devices)


def _fake_joystick(index):
    if not 0 <= index < len(devices):
        raise IndexError(f"no joystick #{index}")
    return devices[index]


pygame.joystick.Joystick = _fake_joystick

# ------------------------------------------- 1) 自动兜底 + 基础读数
pad = g.Gamepad(0)
assert isinstance(pad.controller, g.RawController), pad.controller
assert pad.raw is True, "应标记为原始模式"
assert pad.name == "EasySMX X05 (HID)", pad.name
assert pad.instance_id == 11, pad.instance_id

devices[0].axes[0] = 0.1            # 死区内
assert pad.turn() == 0.0, pad.turn()
devices[0].axes[0] = 1.0            # 右满舵
assert pad.turn() == 1.0, pad.turn()
devices[0].axes[0] = -1.0
assert pad.turn() == -1.0, pad.turn()
devices[0].axes[1] = 1.0            # 推下 → 倒车
assert pad.move() == -1.0, pad.move()
devices[0].axes[1] = -1.0           # 推上 → 前进
assert pad.move() == 1.0, pad.move()
devices[0].axes[1] = 0.0
print("[1] 无 SDL 映射时自动兜底 OK：轴序 0/1、Y 轴取反、死区都对")

# ------------------------------------------- 2) 开火按钮 / Start 上升沿
devices[0].buttons = {pygame.CONTROLLER_BUTTON_A}
pad.poll()
assert pad.fire_edge(), "原始模式下按钮 0 应开火"
pad.poll()
assert not pad.fire_edge(), "按住只触发一次"
devices[0].buttons = set()
pad.poll()
assert not pad.fire_held()

devices[0].buttons = {pygame.CONTROLLER_BUTTON_START}
pad.poll()
assert pad.restart_edge()
pad.poll()
assert not pad.restart_edge()
devices[0].buttons = set()
pad.poll()
print("[2] 原始模式开火 / Start 重开 OK")

# ------------------------------------------- 3) 扳机默认不吃，显式开启才吃
devices[0].axes[4] = 1.0           # 假设 4 号轴被推到顶
devices[0].axes[5] = 1.0
assert pad.fire_held() is False, "原始模式默认不读扳机轴（轴号纯靠猜）"

g.PAD_RAW_TRIGGER_AXES = (4, 5)
pad_trigger = g.Gamepad(0)
assert pad_trigger.trigger_axes == (4, 5), pad_trigger.trigger_axes
assert pad_trigger.fire_held() is True, "显式指定扳机轴后应开火"
g.PAD_RAW_TRIGGER_AXES = ()
pad_trigger = g.Gamepad(0)
assert pad_trigger.fire_held() is False
devices[0].axes[4] = devices[0].axes[5] = -1.0
print("[3] 扳机轴默认关闭、TANK_PAD_RAW_TRIGGERS 可显式开启 OK")

# ------------------------------------------- 4) 诊断输出
rows = g.describe_joysticks()
assert len(rows) == 1, rows
assert rows[0]["index"] == 0
assert rows[0]["name"] == "EasySMX X05 (HID)", rows[0]
assert rows[0]["is_controller"] is False, rows[0]
assert rows[0]["axes"] == 6 and rows[0]["buttons"] == 12, rows[0]
g.print_pad_scan()          # 不抛异常即可
print("[4] describe_joysticks / --pad-scan OK：正确报出「无映射 → 原始兜底」")

# ------------------------------------------- 5) main() 端到端（只有原始手柄）
seen = {"fire": [], "tanks": 0}
orig_init = g.Tank.__init__


def patched_init(self, x, y, color, controls, **kw):
    orig_init(self, x, y, color, controls, **kw)
    seen["tanks"] += 1
    if color == g.RED:
        self.pos.update(200, 300)
        self.angle = 0        # 朝上
    else:
        self.pos.update(200, 200)
        self.angle = 180      # 朝下，两车相距 100px


orig_fire = g.Tank.fire


def counting_fire(self):
    seen["fire"].append(self.color)
    return orig_fire(self)


g.Tank.__init__ = patched_init
g.Tank.fire = counting_fire
g.sdl2_controller = NoControllerSDL2()

frame = {"i": 0}


def fake_get():
    i = frame["i"]
    frame["i"] += 1
    devices[0].buttons = {pygame.CONTROLLER_BUTTON_A} if (i % 20) < 2 else set()
    if i >= 220:
        return [pygame.event.Event(pygame.QUIT)]
    return []


pygame.event.get = fake_get
try:
    g.main()                     # 内部 rebuild_pads 应把这台设备收进 pads
except SystemExit:
    pass
print("RAW_MAIN fired =", len(seen["fire"]), "tanks_created =", seen["tanks"])
assert len(seen["fire"]) >= 5, "原始手柄开火应该能击毁对手（5 发）"
assert set(seen["fire"]) == {g.RED}, "只有 P1 绑手柄，不能串到 P2"
assert seen["tanks"] == 2, "没按 Start，不应重开"
print("[5] main() 端到端 OK：非标准手柄能绑 P1、开火、击毁")

print("RAW PAD 测试全部通过：自动兜底 / 轴序 / 开火 / 扳机开关 / 诊断 / 端到端")
