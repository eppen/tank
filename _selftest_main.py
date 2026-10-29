import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import pygame
import tank_game as g

# 本测试只验证「击毁 → 胜负提示 → 按 R 重开」这条主循环路径，
# 所以把每人命数压到 1 条（默认 5 条时第一次击毁只会触发复活）。
# 多条命 / 复活流程见 _selftest_lives.py。
g.MAX_LIVES = 1

# 让两台坦克面对面站在 100px 距离上，炮口互指
orig_init = g.Tank.__init__


def patched_init(self, x, y, color, controls, **kw):
    orig_init(self, x, y, color, controls, **kw)
    if color == g.RED:
        self.pos.update(200, 300)
        self.angle = 0        # 朝上
    else:
        self.pos.update(200, 200)
        self.angle = 180      # 朝下


g.Tank.__init__ = patched_init

hits = []
orig_hit = g.Tank.take_hit


def patched_hit(self, damage=g.DAMAGE):
    destroyed = orig_hit(self, damage)
    hits.append((self.color, self.hp, destroyed))
    return destroyed


g.Tank.take_hit = patched_hit
created = []
orig_init2 = g.Tank.__init__


def counting_init(self, *a, **kw):
    created.append(a[2] if len(a) > 2 else None)
    orig_init2(self, *a, **kw)


g.Tank.__init__ = counting_init

state = {"n": 0}
FIRE_FRAMES = {2: [pygame.K_SPACE, pygame.K_RETURN],
               22: [pygame.K_SPACE, pygame.K_RETURN],
               42: [pygame.K_SPACE, pygame.K_RETURN],
               62: [pygame.K_SPACE, pygame.K_RETURN],
               82: [pygame.K_SPACE, pygame.K_RETURN],
               140: [pygame.K_r]}   # 结束后按 R 重开


def fake_get():
    n = state["n"]
    state["n"] += 1
    evts = []
    for key in FIRE_FRAMES.get(n, []):
        evts.append(pygame.event.Event(pygame.KEYDOWN, key=key))
    if n >= 230:
        evts.append(pygame.event.Event(pygame.QUIT))
    return evts


pygame.event.get = fake_get

try:
    g.main()
except SystemExit as e:
    print("主循环正常退出，代码", e.code)

print("完整主循环跑完帧数：", state["n"], "（含开火、命中、击毁、胜利提示、按 R 重开）")
print("建坦克次数:", len(created), "(应为 4 = 初始 2 + 重开 2)")
print("受击记录:", hits)
assert len(created) == 4, created
assert any(d for _, _, d in hits), "应至少有一次击毁"
assert max(hp for _, hp, _ in hits) < g.MAX_HP
assert sum(1 for _, _, d in hits if d) >= 1
print("OK：真的有掉血、有击毁、按 R 后重开了新的一局")

