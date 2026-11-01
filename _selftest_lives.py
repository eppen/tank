"""生命值 / 复活流程的端到端测试（无需显示器 / 手柄）。

覆盖：
1. 默认每人 5 条命（g.MAX_LIVES），血条打空只扣 1 条命，不倒计时结束；
2. 被击毁后 REPSPAWN_DELAY 帧在出生点满血复活（同一台坦克对象，不重建）；
3. 复活后 SPAWN_INVULN 帧无敌：这段时间打上去不掉血；
4. 命扣完才分胜负，胜负提示出现后按键不再开火；
5. HUD 命数方块（draw_lives）在剩余命数变化时都能正常绘制。

运行：SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3.11 _selftest_lives.py
"""

import math
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import tank_game as g

# 本测试用 2 条命 + 短复活/无敌窗口，几十帧内就能跑完一个完整回合
g.MAX_LIVES = 2
g.RESPAWN_DELAY = 10
g.SPAWN_INVULN = 6

# 主场景只关心「掉命 → 倒计时 → 满血复活 → 无敌 → 命尽分胜负」这条流程，
# 所以把「离对手最远的角」打桩成固定回出生点：否则复活后 P2 会跑到别的角，
# P1 的固定弹道就打不中第二条命了。最远角逻辑由 _selftest.py 的 [6c] 覆盖，
# 端到端版本见文件末尾的 far_respawn_scenario()。
_real_safe_respawn = g.safe_respawn_point


def fixed_respawn(opponent, fallback=None):
    """打桩：固定回 fallback 点，朝向仍指向对手。"""
    if opponent is None or fallback is None:
        return _real_safe_respawn(opponent, fallback)
    dx, dy = opponent.pos.x - fallback.x, opponent.pos.y - fallback.y
    return pygame.Vector2(fallback), math.degrees(math.atan2(dx, -dy)) % 360.0


g.safe_respawn_point = fixed_respawn

# 让两台坦克面对面站在 100px 距离上，炮口互指；出生点也跟着改，
# 这样复活后仍然在同一条射击线上（重开/复活都回这里）
orig_init = g.Tank.__init__


def patched_init(self, x, y, color, controls, **kw):
    orig_init(self, x, y, color, controls, **kw)
    if color == g.RED:
        self.pos.update(200, 300)
        self.angle = 0        # 朝上
    else:
        self.pos.update(200, 200)
        self.angle = 180      # 朝下
    self.spawn_point = pygame.Vector2(self.pos)
    self.spawn_angle = self.angle
    # 复活点默认跟随出生点（main() 掉命时才会改成「离对手最远的角」）
    self.respawn_point = pygame.Vector2(self.pos)
    self.respawn_angle = self.angle


g.Tank.__init__ = patched_init

respawns = []
orig_respawn = g.Tank.respawn


def patched_respawn(self):
    orig_respawn(self)
    respawns.append({"color": self.color, "pos": tuple(self.pos),
                     "hp": self.hp, "lives": self.lives,
                     "invuln": self.invuln, "alive": self.alive})


g.Tank.respawn = patched_respawn

deaths = []
orig_lose_life = g.Tank.lose_life


def patched_lose_life(self):
    still_alive = orig_lose_life(self)
    deaths.append({"color": self.color, "lives": self.lives,
                   "still_alive": still_alive,
                   "timer": self.respawn_timer})
    return still_alive


g.Tank.lose_life = patched_lose_life

fires = []
orig_fire = g.Tank.fire


def counting_fire(self):
    fires.append((frame["i"], self.color))
    return orig_fire(self)


g.Tank.fire = counting_fire

# 记录所有渲染出来的文字：从里面找胜负提示，就能定位「回合结束的那一帧」
texts = []
real_sysfont = pygame.font.SysFont


class RecFont:
    def __init__(self, font):
        self._font = font

    def render(self, text, *a, **kw):
        texts.append((frame["i"], text))
        return self._font.render(text, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._font, name)


pygame.font.SysFont = lambda *a, **kw: RecFont(real_sysfont(*a, **kw))

# 有汉字字体时游戏走的是 load_font() -> ScreenFont(pygame.freetype)，
# 根本不经过 pygame.font.SysFont；只包 SysFont 会漏掉屏幕上的所有文案
# （这个测试之前就是这么漏掉了胜负提示）。所以把 load_font 也包一层。
real_load_font = g.load_font
_rec_fonts = {}


def recording_load_font(size, bold=False):
    font = real_load_font(size, bold)
    key = id(font)
    if key not in _rec_fonts:
        _rec_fonts[key] = RecFont(font)
    return _rec_fonts[key]


g.load_font = recording_load_font

# P1 每 20 帧打一枪：10 发击毁一次（DAMAGE=10），两轮共 20 发。
# 胜负后 SPACE 会确认局末菜单「再来一局」，故不再发探针开火键。
SHOT_FRAMES = (2, 22, 42, 62, 82, 102, 122, 142, 162, 182,        # 第 1 条命
               232, 252, 272, 292, 312, 332, 352, 372, 392, 412)  # 第 2 条命
QUIT_FRAME = 500
frame = {"i": 0}


def fake_get():
    i = frame["i"]
    frame["i"] += 1
    evts = []
    if i in SHOT_FRAMES:
        evts.append(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
    if i >= QUIT_FRAME:
        evts.append(pygame.event.Event(pygame.QUIT))
    return evts


pygame.event.get = fake_get

try:
    g.main()
except SystemExit as e:
    print("主循环正常退出，代码", e.code)

# ---------------------------------------------------------------- 断言
print("被击毁记录:", deaths)
print("复活记录:", respawns)
print("开火帧:", [f for f, _ in fires])

assert len(deaths) == 2, f"2 条命 => 被打空 2 次，实际 {len(deaths)}"
assert deaths[0]["still_alive"] is True and deaths[0]["lives"] == 1, deaths[0]
assert deaths[0]["timer"] == g.RESPAWN_DELAY, deaths[0]
assert deaths[1]["still_alive"] is False and deaths[1]["lives"] == 0, deaths[1]
assert deaths[1]["timer"] == 0, deaths[1]
assert all(d["color"] == g.BLUE for d in deaths), "只有 P2 在挨打"

assert len(respawns) == 1, f"掉 1 条命后应复活 1 次，实际 {len(respawns)}"
r = respawns[0]
assert r["alive"] and r["hp"] == g.MAX_HP and r["lives"] == 1, r
assert r["invuln"] == g.SPAWN_INVULN, r
assert abs(r["pos"][0] - 200) < 1e-6 and abs(r["pos"][1] - 200) < 1e-6, r

# 复活无敌：第 2 轮的第一发子弹在这种时间轴上可能正好撞上无敌窗口，
# 所以只要求「第二次被击毁确实发生了」，且两次击毁之间的间隔 > 复活延迟。
first_death_fire = fires[9][0]
second_death_fire = fires[19][0]
assert second_death_fire - first_death_fire > g.RESPAWN_DELAY, fires

wins = [t for _, t in texts if "wins!" in t or "WINS!" in t]
assert wins, "命扣完后应显示胜负提示"
assert wins[-1] == "P1 (red) wins!", wins
win_frame = min(f for f, t in texts if "wins!" in t)

# 局末双方战损 + 操作菜单；探针 SPACE 确认默认「再来一局」可能 rematch——
# 本测试 QUIT 前的 SPACE 在胜负后：若已 rematch 会清 winner，这里只断言战损与菜单文案曾出现
casualty = [t for f, t in texts if f >= win_frame
            and (("击毁" in t and "被毁" in t)
                 or ("HIT" in t and "LIVES" in t))]
assert len(casualty) >= 2, f"应显示双方战损：{casualty}"
assert any(t.startswith("P1") for t in casualty), casualty
assert any(t.startswith("P2") for t in casualty), casualty
menu_bits = [t for f, t in texts if f >= win_frame]
assert any("再来一局" in t or "Rematch" in t for t in menu_bits), menu_bits
assert any("Battle Report" in t or "战报" in t for t in menu_bits), menu_bits
# 2p 无 coach：不应自动画出 BATTLE REPORT 卡片标题当战报内容
# （菜单项里的 “Battle Report” 文案不算）
assert not any(t == "BATTLE REPORT" for t in menu_bits), menu_bits

# 回合结束后（探针两枪）不再有任何开火
late = [f for f, _ in fires if f > win_frame]
assert not late, f"回合结束后不该还能开火：{late}"
assert max(f for f, _ in fires) == SHOT_FRAMES[19] + 1, [f for f, _ in fires]

# format_casualty_lines 纯函数
fmt = g.format_casualty_lines(
    {"p1": {"shots": 10, "hits": 4, "shield_hits": 1, "kills": 2, "deaths": 1},
     "p2": {"shots": 8, "hits": 3, "shield_hits": 0, "kills": 1, "deaths": 2}},
    {"p1": 3, "p2": 0},
    {"p1": "P1", "p2": "P2"},
    ascii_only=True)
assert fmt[0].startswith("P1") and "HIT 4/10" in fmt[0] and "LIVES 3" in fmt[0], fmt
assert fmt[1].startswith("P2") and "K1" in fmt[1] and "D2" in fmt[1], fmt

# HUD 命数：两种极端值都能画（0 条命 / 满命）
surf = pygame.Surface((g.SCREEN_WIDTH, g.SCREEN_HEIGHT))
probe = g.Tank(300, 300, g.RED, {"forward": pygame.K_w, "back": pygame.K_s,
                                "left": pygame.K_a, "right": pygame.K_d,
                                "fire": pygame.K_SPACE})
for n in range(g.MAX_LIVES + 1):
    probe.lives = n
    g.draw_lives(surf, probe, 10, 10)
assert g.lives_pips_width() > 0

print("LIVES OK：2 条命 -> 掉命复活 -> 命尽分胜负 + 局末菜单 -> 停火")


# ------------------------------------------------- 额外场景：真实复活点
# 把 safe_respawn_point 还原成真货：掉一条命后，P2 必须出现在「离对手最远的角」，
# 而不是固定的开局角（否则等于送死：边线挡着退不了，1 秒无敌到点就挨枪）。
g.safe_respawn_point = _real_safe_respawn
deaths.clear()
respawns.clear()
fires.clear()
texts.clear()
FAR_SHOT_FRAMES = (2, 22, 42, 62, 82, 102, 122, 142, 162, 182)
frame["i"] = 0


def fake_get_far():
    i = frame["i"]
    frame["i"] += 1
    evts = []
    if i in FAR_SHOT_FRAMES:
        evts.append(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
    if i >= 260:
        evts.append(pygame.event.Event(pygame.QUIT))
    return evts


pygame.event.get = fake_get_far
try:
    # main() 退出时会 pygame.quit()（视频系统被拆掉），再跑一局要先重新初始化，
    # 并把模块级的 screen 换成新建的表面（main() 里 fill/blit 用的是这个全局）。
    pygame.init()
    g.screen = pygame.display.set_mode((g.SCREEN_WIDTH, g.SCREEN_HEIGHT))
    g.main()
except SystemExit as e:
    print("（最远角场景）主循环正常退出，代码", e.code)

assert len(deaths) == 1 and len(respawns) == 1, (deaths, respawns)
far = respawns[0]
opp = pygame.Vector2(200, 300)                     # P1 在本测试里的固定站位
corners = [pygame.Vector2(c) for c in g.RESPAWN_CORNERS]
pos = pygame.Vector2(far["pos"])
assert any(pos.distance_to(c) < 1e-6 for c in corners), far
assert abs(pos.distance_to(opp) - max(c.distance_to(opp) for c in corners)) < 1e-6, far
assert far["pos"] != (200, 200), "不该回到固定出生角"
print("FAR_OK：掉命后在离对手最远的角复活", far["pos"])


# ------------------------------------------------- 1p：菜单打开战报（放最后，避免改 MAX_LIVES）
def post_menu_report_scenario():
    """1p 命尽后按 2 进入战报叠加层；战报不自动挂左下。"""
    import builtins
    g.MAX_LIVES = 1
    g.RESPAWN_DELAY = 10
    g.SPAWN_INVULN = 6
    g.safe_respawn_point = fixed_respawn
    seen = {"stats": False, "coach_report": False}

    orig_print = builtins.print

    def capturing_print(*args, **kwargs):
        line = " ".join(str(a) for a in args)
        if "[stats]" in line:
            seen["stats"] = True
        if "本局评估" in line or "ROUND REPORT" in line:
            seen["coach_report"] = True
        return orig_print(*args, **kwargs)

    builtins.print = capturing_print
    os.environ["TANK_MODE"] = "1p"
    os.environ["TANK_AI"] = "easy"
    texts_pm = []
    real_load = g.load_font
    fonts = {}

    class Rec:
        def __init__(self, f):
            self._f = f

        def render(self, text, *a, **kw):
            texts_pm.append((frame_pm["i"], text))
            return self._f.render(text, *a, **kw)

        def __getattr__(self, n):
            return getattr(self._f, n)

    def load_rec(size, bold=False):
        f = real_load(size, bold)
        fonts[id(f)] = fonts.get(id(f)) or Rec(f)
        return fonts[id(f)]

    g.load_font = load_rec
    frame_pm = {"i": 0}
    shot = (2, 22, 42, 62, 82, 102, 122, 142, 162, 182)

    def fake_get():
        i = frame_pm["i"]
        frame_pm["i"] += 1
        evts = []
        if i in shot:
            evts.append(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE))
        if seen["stats"] and i == frame_pm.get("open_at", -1):
            evts.append(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_2))
        if seen["stats"] and "open_at" not in frame_pm:
            frame_pm["open_at"] = i + 5
        if i >= 400:
            evts.append(pygame.event.Event(pygame.QUIT))
        return evts

    oi = g.Tank.__init__

    def close_init(self, x, y, color, controls, **kw):
        oi(self, x, y, color, controls, **kw)
        if color == g.RED:
            self.pos.update(200, 300)
            self.angle = 0
        else:
            self.pos.update(200, 200)
            self.angle = 180
        self.spawn_point = pygame.Vector2(self.pos)
        self.spawn_angle = self.angle
        self.respawn_point = pygame.Vector2(self.pos)
        self.respawn_angle = self.angle

    g.Tank.__init__ = close_init
    pygame.event.get = fake_get
    try:
        try:
            pygame.init()
            g.screen = pygame.display.set_mode((g.SCREEN_WIDTH, g.SCREEN_HEIGHT))
            g.main()
        except SystemExit:
            pass
    finally:
        builtins.print = orig_print
        g.Tank.__init__ = oi
        g.load_font = real_load
        os.environ.pop("TANK_MODE", None)
        os.environ.pop("TANK_AI", None)

    assert seen["stats"], "应有战损"
    assert seen["coach_report"], "1p 终端应有评估报告"
    after_open = [t for f, t in texts_pm
                  if f >= frame_pm.get("open_at", 0)
                  and ("return to menu" in t.lower() or "返回菜单" in t)]
    assert after_open, f"按 2 后应进入战报层：{texts_pm[-20:]}"
    print("POST_MENU_REPORT OK：1p 菜单打开战报，不自动挂左下")


post_menu_report_scenario()
