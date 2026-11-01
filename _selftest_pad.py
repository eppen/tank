"""手柄接入的自动化测试（不需要真实手柄）。

覆盖：
1. 无手柄环境（dummy 驱动）下，main() 仍能跑完、不抛异常，坦克默认无 pad；
2. 死区 / 线性过渡 / 轴取整归一化 / Y 轴取反；
3. 开火按键与扳机轴的“上升沿”语义（按住不连发）；
4. Start/Back 重开的上升沿；
5. drive(模拟量) 与 handle_input(键盘) 等价；
6. 两只手柄各控一台坦克、互不串台；
7. 不支持震动的手柄 / 少轴手柄不崩。

运行：SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3.11 _selftest_pad.py
"""

import builtins
import math
import os
import subprocess
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import tank_game as g

pygame.init()


# ---------------------------------------------------------------- 假手柄设备
class FakeController:
    """模仿 pygame._sdl2.controller.Controller 的最小接口。"""

    name = "Fake Pad"

    def __init__(self, axes=None, buttons=None, rumble_ok=True):
        # axes 用真实 SDL 语义的整数：摇杆 -32768..32767，扳机静止 -32768
        self.axes = dict(axes or {})
        self.buttons = dict(buttons or {})
        self.rumble_ok = rumble_ok
        self.rumble_calls = []
        self.closed = False

    def get_axis(self, axis):
        return self.axes.get(axis, -32768 if axis in g.PAD_TRIGGER_AXES else 0)

    def get_button(self, button):
        return bool(self.buttons.get(button, False))

    def get_mapping(self):
        return {"platform": "Mac OS X", "a": "b0"}

    def rumble(self, low, high, duration):
        if not self.rumble_ok:
            raise pygame.error("rumble not supported")
        self.rumble_calls.append((low, high, duration))
        return True

    def quit(self):
        self.closed = True


class FakeSDL2:
    """冒充 pygame._sdl2.controller 模块，只提供 main() 用到的几个函数。"""

    def __init__(self, controllers):
        self.controllers = controllers

    def get_count(self):
        return len(self.controllers)

    def is_controller(self, index):
        return 0 <= index < len(self.controllers)

    def Controller(self, index):
        return self.controllers[index]

    def update(self):
        pass

    def quit(self):
        pass


def pad_main_scenario():
    """用两只假手柄把 main() 真跑一遍：开火 → 击毁 → Start 重开。"""
    # 这里只验证「击毁 → 胜负 → Start 重开」这条链路，压到 1 条命；
    # 多条命与复活流程见 _selftest_lives.py。
    g.MAX_LIVES = 1
    seen = {"fire": [], "tanks": 0, "rematch_at": [], "stats": []}

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

    orig_print = builtins.print

    def capturing_print(*args, **kwargs):
        line = " ".join(str(a) for a in args)
        if "[stats]" in line:
            seen["stats"].append(line)
        if "再来一局" in line or line.endswith("Rematch") or "Rematch" == line.strip():
            seen["rematch_at"].append(frame["i"])
        # set_toast 不打印；rematch() 只 set_toast，靠 tanks 计数判断
        return orig_print(*args, **kwargs)

    g.Tank.__init__ = patched_init
    g.Tank.fire = counting_fire
    builtins.print = capturing_print

    fc1, fc2 = FakeController(), FakeController()
    fc2.name = "Fake P2"
    g.sdl2_controller = FakeSDL2([fc1, fc2])

    frame = {"i": 0}
    tanks_before_start = {"n": None}
    # 10 发击毁一次（DAMAGE=10），每 20 帧按一下 A：约 180 帧打完，200 帧左右分胜负

    def fake_get():
        i = frame["i"]
        frame["i"] += 1
        # 每 20 帧按一下 A（上升沿）→ 冷却刚好够再开火
        fc1.buttons[pygame.CONTROLLER_BUTTON_A] = (i % 20) < 2
        # 胜负后继续按 A 若干帧：不应 rematch；259 记下坦克数，260 Start 才重开
        if i == 259:
            tanks_before_start["n"] = seen["tanks"]
        fc1.buttons[pygame.CONTROLLER_BUTTON_START] = i in (260, 261)
        if i >= 340:
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
    print("PAD_MAIN fired =", len(seen["fire"]),
          "tank_colors =", seen["fire"], "tanks_created =", seen["tanks"])
    assert len(seen["fire"]) >= 10, seen
    assert set(seen["fire"]) == {g.RED}, "只有 P1 的手柄开火，不能串到 P2"
    assert seen["stats"], "局末应打印双方战损 [stats]"
    assert tanks_before_start["n"] == 2, \
        f"胜负后按 A 不应 rematch，Start 前 tanks={tanks_before_start['n']}"
    assert seen["tanks"] == 4, "初始 2 + 按 Start 重开 2"
    print("PAD_MAIN OK：手柄开火→击毁→战损→A 不重开→Start 重开")


if "--pad-main" in sys.argv:
    pad_main_scenario()
    sys.exit(0)


def await_start_scenario():
    """切 1P/2P 后必须等 Start/开火才开打；等待期间不应产生子弹。"""
    g.MAX_LIVES = 1
    seen = {"fire": [], "mode_logs": []}

    orig_print = builtins.print

    def capturing_print(*args, **kwargs):
        line = " ".join(str(a) for a in args)
        if "[mode]" in line:
            seen["mode_logs"].append(line)
        return orig_print(*args, **kwargs)

    orig_fire = g.Tank.fire

    def counting_fire(self):
        seen["fire"].append(frame["i"])
        return orig_fire(self)

    g.Tank.fire = counting_fire
    builtins.print = capturing_print

    fc1, fc2 = FakeController(), FakeController()
    fc2.name = "Fake P2"
    g.sdl2_controller = FakeSDL2([fc1, fc2])

    frame = {"i": 0}

    def fake_get():
        i = frame["i"]
        frame["i"] += 1
        fc1.buttons.clear()
        # 帧 5：F2 切到 1p → awaiting_start
        if i == 5:
            return [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F2)]
        # 帧 40：Start 确认开打（等待段不按开火，避免 fire 边沿提前开打）
        if i in (40, 41):
            fc1.buttons[pygame.CONTROLLER_BUTTON_START] = True
        # 帧 55–90：脉冲 A，开打后应能开火
        elif 55 <= i < 90 and (i % 6) < 2:
            fc1.buttons[pygame.CONTROLLER_BUTTON_A] = True
        if i >= 110:
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
        g.Tank.fire = orig_fire

    assert any("等待 Start" in m for m in seen["mode_logs"]), seen["mode_logs"]
    assert any("开打" in m for m in seen["mode_logs"]), seen["mode_logs"]
    assert seen["fire"], "开打后应有至少一次开火"
    assert min(seen["fire"]) >= 40, f"等待段不应开火：{seen['fire']}"
    print("AWAIT_START OK：切模式等待 → Start 开打 → 可开火，fired =",
          len(seen["fire"]), "first_at_frame =", min(seen["fire"]))


if "--await-start" in sys.argv:
    await_start_scenario()
    sys.exit(0)


def await_map_scenario():
    """等待开始时可切地图；开打后按 M 无效。"""
    g.MAX_LIVES = 1
    seen = {"walls": [], "mode": []}

    orig_print = builtins.print

    def capturing_print(*args, **kwargs):
        line = " ".join(str(a) for a in args)
        if "[walls]" in line and "地图 →" in line:
            seen["walls"].append((frame["i"], line))
        if "[mode]" in line:
            seen["mode"].append(line)
        return orig_print(*args, **kwargs)

    builtins.print = capturing_print
    g.sdl2_controller = FakeSDL2([FakeController(), FakeController()])

    frame = {"i": 0}

    def fake_get():
        i = frame["i"]
        frame["i"] += 1
        # 帧 5：F2 → awaiting_start（默认 pillars）
        if i == 5:
            return [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F2)]
        # 帧 15：M → corridors（pillars 的下一张）
        if i == 15:
            return [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_m)]
        # 帧 30：R 确认开打
        if i == 30:
            return [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_r)]
        # 帧 50：开打后再按 M，不应再切图
        if i == 50:
            return [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_m)]
        if i >= 70:
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

    assert any("等待 Start" in m for m in seen["mode"]), seen["mode"]
    assert any("开打" in m for m in seen["mode"]), seen["mode"]
    assert len(seen["walls"]) == 1, seen["walls"]
    assert seen["walls"][0][0] < 30, seen["walls"]
    assert "CORRIDORS" in seen["walls"][0][1], seen["walls"]
    print("AWAIT_MAP OK：等待时 M 切到 CORRIDORS，开打后再按 M 无效")


if "--await-map" in sys.argv:
    await_map_scenario()
    sys.exit(0)


def make_pad(**kw):
    return g.Gamepad(0, controller=FakeController(**kw), name="Fake Pad")


def almost(a, b, tol=1e-6):
    return abs(a - b) <= tol


# --------------------------------------------- 1) 无手柄：dummy 下 0 个设备
assert pygame.joystick.get_count() == 0, pygame.joystick.get_count()
assert g.sdl2_controller is not None, "pygame._sdl2.controller 应可用"
assert g.sdl2_controller.get_count() == 0

t = g.Tank(100, 100, g.RED, {"forward": pygame.K_w, "back": pygame.K_s,
                             "left": pygame.K_a, "right": pygame.K_d,
                             "fire": pygame.K_SPACE})
assert t.pad is None, "新建坦克默认不绑手柄"
# 打不开的设备不应该崩，只是拿不到 controller
orphan = g.Gamepad(0)
assert orphan.controller is None
assert orphan.turn() == 0.0 and orphan.move() == 0.0
assert orphan.fire_held() is False and orphan.restart_held() is False
orphan.poll()
assert orphan.fire_edge() is False
orphan.rumble()      # 无设备时静默
orphan.close()
print("[1] 无手柄路径 OK：get_count=0，Gamepad 空转不崩")

# --------------------------------------------- 2) 死区 / 归一化 / Y 轴取反
pad = make_pad()
fc = pad.controller

# 死区内归零
fc.axes[g.PAD_AXIS_X] = int(0.1 * g.PAD_AXIS_SCALE)
assert pad.turn() == 0.0, pad.turn()
# 满偏
fc.axes[g.PAD_AXIS_X] = 32767
assert pad.turn() == 1.0, pad.turn()
# 死区线性过渡：0.625 在 dz=0.25 下应恰好是 0.5
fc.axes[g.PAD_AXIS_X] = int(0.625 * g.PAD_AXIS_SCALE)
assert almost(pad.turn(), 0.5, 1e-4), pad.turn()
# 左推为负
fc.axes[g.PAD_AXIS_X] = -32768
assert pad.turn() == -1.0, pad.turn()
# 归一化也接受 float（-1..1）输入
fc.axes[g.PAD_AXIS_X] = 0.1
assert pad.turn() == 0.0
fc.axes[g.PAD_AXIS_X] = 0.625
assert almost(pad.turn(), 0.5), pad.turn()

# Y 轴取反：SDL 向下为正 → 推下应该是倒车（move < 0）
fc.axes[g.PAD_AXIS_Y] = 32767
assert pad.move() == -1.0, pad.move()
fc.axes[g.PAD_AXIS_Y] = -32768
assert pad.move() == 1.0, pad.move()
fc.axes[g.PAD_AXIS_Y] = 0
assert pad.move() == 0.0
print("[2] 死区/线性过渡/Y 轴取反 OK：0.1→0，1.0→1.0，0.625→0.5，推下→-1")

# --------------------------------------------- 3) 开火：按键 + 扳机上升沿
fc.axes[g.PAD_AXIS_X] = 0
fc.axes[g.PAD_AXIS_Y] = 0
btn_a = pygame.CONTROLLER_BUTTON_A
fc.buttons[btn_a] = True
pad.poll()
assert pad.fire_edge(), "第一次按下应产生上升沿"
pad.poll()
assert not pad.fire_edge(), "按住不放只能触发一次"
fc.buttons[btn_a] = False
pad.poll()
assert not pad.fire_edge(), "松手不产生上升沿"
fc.buttons[btn_a] = True
pad.poll()
assert pad.fire_edge(), "再次按下应重新产生上升沿"

# 扳机：静止 -32768 不触发，推过一半触发
fc.buttons.clear()
tl = pygame.CONTROLLER_AXIS_TRIGGERLEFT
fc.axes[tl] = -32768
pad.poll()
assert not pad.fire_held(), "扳机静止不该开火"
fc.axes[tl] = 32767
pad.poll()
assert pad.fire_held(), "扳机踩到底应开火"
fc.axes[tl] = -32768
pad.poll()

# 肩键也算开火
fc.buttons[pygame.CONTROLLER_BUTTON_RIGHTSHOULDER] = True
assert pad.fire_held()
fc.buttons.clear()
pad.poll()
assert not pad.fire_held()

# Start 重开上升沿
start = pygame.CONTROLLER_BUTTON_START
fc.buttons[start] = True
pad.poll()
assert pad.restart_edge()
pad.poll()
assert not pad.restart_edge(), "按住 Start 不应反复重开"
fc.buttons[start] = False
pad.poll()
pad.poll()
assert not pad.restart_edge()

# BACK：切 1P↔2P 上升沿
fc.buttons.clear()
back = pygame.CONTROLLER_BUTTON_BACK
fc.buttons[back] = True
pad.poll()
assert pad.mode_edge(), "BACK 第一次按下应产生 mode 上升沿"
assert not pad.restart_edge(), "BACK 不应再触发 restart"
pad.poll()
assert not pad.mode_edge(), "按住 BACK 不应反复切模式"
fc.buttons[back] = False
pad.poll()

# 右摇杆按下：循环 AI 难度上升沿
fc.buttons.clear()
rstick = pygame.CONTROLLER_BUTTON_RIGHTSTICK
fc.buttons[rstick] = True
pad.poll()
assert pad.difficulty_edge(), "RSTICK 第一次按下应产生 difficulty 上升沿"
pad.poll()
assert not pad.difficulty_edge(), "按住 RSTICK 不应反复切难度"
fc.buttons[rstick] = False
pad.poll()

# MFI 兜底和弦：LB+RB = 切模式，LB+X = 切难度
fc.buttons.clear()
lb = pygame.CONTROLLER_BUTTON_LEFTSHOULDER
rb = pygame.CONTROLLER_BUTTON_RIGHTSHOULDER
xbtn = pygame.CONTROLLER_BUTTON_X
fc.buttons[lb] = True
pad.poll()
assert not pad.mode_edge(), "只按 LB 不应切模式"
fc.buttons[rb] = True
pad.poll()
assert pad.mode_edge(), "LB+RB 应切模式"
assert pad.menu_consumed_fire(), "菜单和弦应占用开火"
pad.poll()
assert not pad.mode_edge()
fc.buttons.clear()
pad.poll()

fc.buttons[lb] = True
fc.buttons[xbtn] = True
pad.poll()
assert pad.difficulty_edge(), "LB+X 应切难度"
pad.poll()
assert not pad.difficulty_edge()
fc.buttons.clear()
pad.poll()

# RB+Y：切地图上升沿（与 LB+X 对称；主循环仅 awaiting_start 时响应）
ybtn = pygame.CONTROLLER_BUTTON_Y
fc.buttons[rb] = True
pad.poll()
assert not pad.map_edge(), "只按 RB 不应切地图"
fc.buttons[ybtn] = True
pad.poll()
assert pad.map_edge(), "RB+Y 应切地图"
assert pad.menu_consumed_fire(), "切地图和弦应占用开火"
pad.poll()
assert not pad.map_edge(), "按住 RB+Y 不应反复切地图"
fc.buttons.clear()
pad.poll()
# 带 LB 时不应认成切地图（避免跟 LB+RB 模式和弦打架）
fc.buttons[lb] = True
fc.buttons[rb] = True
fc.buttons[ybtn] = True
pad.poll()
assert not pad.map_edge(), "LB+RB+Y 不应出 map 边沿"
fc.buttons.clear()
pad.poll()

# 事件闩锁：get_button 为假时仍能靠 latch 出边沿
pad.latch_menu_button(pygame.CONTROLLER_BUTTON_START)
pad.poll()
assert pad.restart_edge(), "START 闩锁应变成 restart 边沿"
pad.poll()
assert not pad.restart_edge()
print("[3] 开火/扳机/Start/BACK/RSTICK/LB+RB/LB+X/RB+Y/闩锁 上升沿 OK")

# --------------------------------------------- 4) 震动：支持与不支持都不崩
pad.rumble(180, 0.8)
assert fc.rumble_calls and fc.rumble_calls[0] == (0.8, 0.8, 180), fc.rumble_calls
weak = make_pad(rumble_ok=False)
weak.rumble(180, 0.8)          # 内部吞掉 pygame.error
no_rumble = g.Gamepad(0, controller=object(), name="NoRumble")
no_rumble.rumble(100, 0.5)     # AttributeError 也不能崩
print("[4] 震动降级 OK：支持则调用，不支持静默")

# --------------------------------------------- 5) 少轴/坏设备读取兜底
class BadController:
    def get_axis(self, axis):
        raise IndexError("no such axis")

    def get_button(self, button):
        raise IndexError("no such button")

    def get_mapping(self):
        return {}


bad = g.Gamepad(0, controller=BadController(), name="Broken")
assert bad.turn() == 0.0 and bad.move() == 0.0
assert bad.fire_held() is False
print("[5] 轴/键越界兜底 OK：返回 0 / False")

# --------------------------------------------- 6) drive 与键盘逐位等价
def new_tank(color=g.RED, x=300, y=300):
    return g.Tank(x, y, color, {"forward": pygame.K_w, "back": pygame.K_s,
                                "left": pygame.K_a, "right": pygame.K_d,
                                "fire": pygame.K_SPACE})


class Keys(dict):
    def __getitem__(self, k):
        return self.get(k, False)


# 6a 键盘「按住到满舵」== 模拟量满偏（右转 + 倒车，按键用 D/S）
#    键盘有起手加速段（TURN_FINE_GAIN -> 1.0，见模块顶部注释），先按住几帧
#    把加速段走完，再比**同样一帧**的转角与位移——这才是两通道共同的物理。
a = new_tank()
b = new_tank()
hold = Keys({pygame.K_d: True, pygame.K_s: True})
for _ in range(int(g.TURN_RAMP_FRAMES) + 2):
    b.handle_input(hold)
a.angle, a.pos = b.angle, pygame.Vector2(b.pos)   # 对齐到同一状态再比一帧
a.drive(turn=1.0, move=-1.0)
b.handle_input(hold)
assert almost(a.angle, b.angle) and a.pos.distance_to(b.pos) < 1e-9, (a.pos, b.pos)

# 6a-2 点按一帧只转「满舵 × 起手增益」= 1°：这就是能停在任意角度上的来源
tap = new_tank()
tap.handle_input(Keys({pygame.K_d: True}))
assert almost(tap.angle, g.TURN_FINE_GAIN * tap.turn_speed), tap.angle
assert g.TURN_FINE_GAIN < 1.0 and g.TURN_RAMP_FRAMES > 0

# 6b 长跑 600 帧，键盘路径与 poll_controls（无手柄）逐位一致
c = new_tank()
d = new_tank()
seq = [pygame.K_w, pygame.K_a, pygame.K_s, pygame.K_d]
for i in range(600):
    keys = Keys({seq[(i // 37) % 4]: True})
    c.handle_input(keys)
    d.poll_controls(keys)      # pad is None → 应完全等价
assert c.pos.distance_to(d.pos) < 1e-9 and almost(c.angle, d.angle), (c.pos, d.pos)
assert g.TANK_HALF <= c.pos.x <= g.SCREEN_WIDTH - g.TANK_HALF

# 6c A/D 方向回归：A 左转（角度减小）、D 右转（角度增大）
#    单帧只走起手增益那一档（点按微转），满舵转速见 6a
left_t = new_tank()
left_t.handle_input(Keys({pygame.K_a: True}))
assert almost(left_t.angle,
              (360 - g.TURN_FINE_GAIN * left_t.turn_speed) % 360), left_t.angle
right_t = new_tank()
right_t.handle_input(Keys({pygame.K_d: True}))
assert almost(right_t.angle, g.TURN_FINE_GAIN * right_t.turn_speed % 360), right_t.angle

# 6c-2 几何校验（与贴图旋转符号解耦）：右转后炮口必须落在车身右侧
assert right_t.muzzle_position().x > right_t.pos.x, right_t.muzzle_position()
assert left_t.muzzle_position().x < left_t.pos.x, left_t.muzzle_position()
# 符号常量本身：正 = 右转
assert g.TURN_RIGHT > 0 > g.TURN_LEFT

# 6d 摇杆右推 = 车头顺时针 = 角度增大（与 D 键同向）
pad_right = make_pad(axes={g.PAD_AXIS_X: 32767})
pad_tank = new_tank()
pad_tank.pad = pad_right
pad_tank.poll_controls(Keys({}))
assert almost(pad_tank.angle, pad_tank.turn_speed % 360), pad_tank.angle
print("[6] drive/键盘等价 + A/D 方向 + 摇杆右推同向 OK")

# --------------------------------------------- 7) 两只手柄各控一台，不串台
p1 = make_pad(axes={g.PAD_AXIS_X: 32767})
p2 = make_pad(axes={g.PAD_AXIS_X: -32768})
t1 = new_tank(g.RED, 200, 200)
t2 = new_tank(g.BLUE, 600, 400)
t1.pad, t2.pad = p1, p2
for _ in range(3):
    g.refresh_pads([p1, p2])
    t1.poll_controls(Keys({}))
    t2.poll_controls(Keys({}))
assert almost(t1.angle, 3 * t1.turn_speed % 360), t1.angle        # 右推 = 顺时针
assert almost(t2.angle, -3 * t2.turn_speed % 360), t2.angle       # 左推 = 逆时针

# 一只手柄推前进，另一台坦克不动
p1.controller.axes[g.PAD_AXIS_X] = 0
p1.controller.axes[g.PAD_AXIS_Y] = -32768     # 上 = 前进
p2.controller.axes[g.PAD_AXIS_Y] = 0
y1, y2 = t1.pos.y, t2.pos.y
g.refresh_pads([p1, p2])
t1.poll_controls(Keys({}))
t2.poll_controls(Keys({}))
assert t1.pos.y < y1, "P1 应向前（y 减小）"
assert almost(t2.pos.y, y2), "P2 不该被 P1 的手柄带动"
print("[7] 双柄分控 OK：各控一台、互不串台")

# 手柄 + 键盘同时生效（单手柄玩家随时切键盘）
p_mix = make_pad(axes={g.PAD_AXIS_Y: -32768})
mix = new_tank()
mix.pad = p_mix
x0, y0 = mix.pos.x, mix.pos.y
mix.poll_controls(Keys({pygame.K_d: True}))
assert mix.angle > 0 and mix.pos.y < y0, (mix.angle, mix.pos)
print("[8] 键盘+手柄叠加 OK")

# --------------------------------------------- 9) main() 无手柄端到端跑一遍
frames = {"n": 0}


def fake_get():
    n = frames["n"]
    frames["n"] += 1
    evts = []
    if n in (5, 25):                       # 顺便按两下开火键
        evts.append(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
    if n >= 60:
        evts.append(pygame.event.Event(pygame.QUIT))
    return evts


pygame.event.get = fake_get
try:
    g.main()                               # 内部会 rebuild_pads / bind_pads / refresh_pads
except SystemExit as e:
    print("[9] main() 正常退出，code =", e.code)
print(f"[9] main() 无手柄跑完 {frames['n']} 帧，未抛异常")

# --------------------------------------------- 10) 假手柄端到端跑 main()
env = dict(os.environ)
env["SDL_VIDEODRIVER"] = "dummy"
env["SDL_AUDIODRIVER"] = "dummy"
proc = subprocess.run([sys.executable, os.path.abspath(__file__), "--pad-main"],
                      capture_output=True, text=True, env=env)
out = "\n".join(l for l in (proc.stdout + proc.stderr).splitlines()
                if not l.startswith(("pygame ", "Hello from", "  from pkg")))
print(out)
assert proc.returncode == 0, f"pad-main 子进程失败\n{out}"
assert "PAD_MAIN OK" in proc.stdout

# --------------------------------------------- 11) 切模式后等待 Start 再开打
proc2 = subprocess.run([sys.executable, os.path.abspath(__file__), "--await-start"],
                       capture_output=True, text=True, env=env)
out2 = "\n".join(l for l in (proc2.stdout + proc2.stderr).splitlines()
                 if not l.startswith(("pygame ", "Hello from", "  from pkg")))
print(out2)
assert proc2.returncode == 0, f"await-start 子进程失败\n{out2}"
assert "AWAIT_START OK" in proc2.stdout

# --------------------------------------------- 12) 等待开始切地图，开打后锁定
proc3 = subprocess.run([sys.executable, os.path.abspath(__file__), "--await-map"],
                       capture_output=True, text=True, env=env)
out3 = "\n".join(l for l in (proc3.stdout + proc3.stderr).splitlines()
                 if not l.startswith(("pygame ", "Hello from", "  from pkg")))
print(out3)
assert proc3.returncode == 0, f"await-map 子进程失败\n{out3}"
assert "AWAIT_MAP OK" in proc3.stdout

print("PAD 测试全部通过：无手柄降级/死区/归一化/Y 轴/上升沿/双柄分控/键盘等价/端到端/等待开始/切地图")
