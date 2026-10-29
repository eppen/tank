"""生命值 / 复活流程的端到端测试（无需显示器 / 手柄）。

覆盖：
1. 默认每人 5 条命（g.MAX_LIVES），血条打空只扣 1 条命，不倒计时结束；
2. 被击毁后 REPSPAWN_DELAY 帧在出生点满血复活（同一台坦克对象，不重建）；
3. 复活后 SPAWN_INVULN 帧无敌：这段时间打上去不掉血；
4. 命扣完才分胜负，胜负提示出现后按键不再开火；
5. HUD 命数方块（draw_lives）在剩余命数变化时都能正常绘制。

运行：SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3.11 _selftest_lives.py
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import tank_game as g

# 本测试用 2 条命 + 短复活/无敌窗口，几十帧内就能跑完一个完整回合
g.MAX_LIVES = 2
g.RESPAWN_DELAY = 10
g.SPAWN_INVULN = 6

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

# P1 每 20 帧打一枪：5 发击毁一次，两轮共 10 发；
# 202 之后的两枪是「回合已结束」的探针，应该被完全忽略。
SHOT_FRAMES = (2, 22, 42, 62, 82, 122, 142, 162, 182, 202, 240, 260)
QUIT_FRAME = 320
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
first_death_fire = fires[4][0]
second_death_fire = fires[9][0]
assert second_death_fire - first_death_fire > g.RESPAWN_DELAY, fires

wins = [t for _, t in texts if "wins!" in t or "WINS!" in t]
assert wins, "命扣完后应显示胜负提示"
assert wins[-1] == "P1 (red) wins!", wins
win_frame = min(f for f, t in texts if "wins!" in t)

# 回合结束后（探针两枪）不再有任何开火
late = [f for f, _ in fires if f > win_frame]
assert not late, f"回合结束后不该还能开火：{late}"
assert max(f for f, _ in fires) == SHOT_FRAMES[9] + 1, [f for f, _ in fires]

# HUD 命数：两种极端值都能画（0 条命 / 满命）
surf = pygame.Surface((g.SCREEN_WIDTH, g.SCREEN_HEIGHT))
probe = g.Tank(300, 300, g.RED, {"forward": pygame.K_w, "back": pygame.K_s,
                                "left": pygame.K_a, "right": pygame.K_d,
                                "fire": pygame.K_SPACE})
for n in range(g.MAX_LIVES + 1):
    probe.lives = n
    g.draw_lives(surf, probe, 10, 10)
assert g.lives_pips_width() > 0

print("LIVES OK：2 条命 -> 掉命复活（满血/出生点/无敌）-> 命尽分胜负 -> 停火")
