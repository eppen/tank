"""转向粒度自检：键盘为什么总是对不准敌人，改成「点按微转」后好在哪里。

覆盖：
1. 旧键盘的物理极限：单帧固定 turn_speed（4°）从任意起点只能落在 4° 网格上，
   对 38° 这种非整数倍方向的最好残差 = 2.0°（数学上永远等于不了）；
2. 多按一帧 = 4°：400px 上横向偏移 ≈ 26px > 命中半径 22px → 这一枪白打；
3. 新键盘的起手增益：点按一帧 = 1°（满舵的 1/4），38 次点按能精确停在 38°；
4. 闭环：模拟玩家「看误差 → 点一下 → 松手」把炮口对到 400px 外的目标上，
   残差 ≤ 0.5°，开火真的命中；同样的策略在旧物理下只能收敛到 2.0°；
5. 满舵速度没变：按住 TURN_RAMP_FRAMES 帧后每帧仍是 turn_speed（甩炮口不吃亏）；
6. 转向 dt 化：同样 0.5 秒，30fps 与 60fps 转过的角度差 < 10%（忽略 dt 会差 45%）。

运行：SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3.11 _selftest_turn.py
"""

import math
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import tank_game as g

P1_KEYS = {"forward": pygame.K_w, "back": pygame.K_s,
           "left": pygame.K_a, "right": pygame.K_d,
           "fire": pygame.K_SPACE}


class Keys(dict):
    """pygame.key.get_pressed() 的最小替身：没按的键就是 False。"""

    def __getitem__(self, k):
        return self.get(k, False)


def new_tank(x=200.0, y=500.0, color=g.RED):
    return g.Tank(x, y, color, dict(P1_KEYS))


def desired_to(tank, x, y):
    """从坦克指向 (x, y) 的角度，与游戏/AI 用的算式完全一致（0 朝上、顺时针正）。"""
    dx, dy = x - tank.pos.x, y - tank.pos.y
    return math.degrees(math.atan2(dx, -dy)) % 360.0


def wrap(err):
    return (err + 180.0) % 360.0 - 180.0


# 目标放在 38° 方向、400px 外：38 不是 4 的整数倍，正是「对不上」的典型情况
DESIRED = 38.0
DIST = 400.0
TARGET = (200.0 + DIST * math.sin(math.radians(DESIRED)),
          500.0 - DIST * math.cos(math.radians(DESIRED)))

_home = new_tank()
assert abs(desired_to(_home, *TARGET) - DESIRED) < 1e-9, desired_to(_home, *TARGET)

# ---------------------------------------- 1) 旧键盘的可达角度只有 4° 格点
per_frame = _home.turn_speed
assert per_frame > 0

# 实测：从 0 起连续右转 k 帧，角度严格是 4k（走的是模拟量通道，与旧键盘等价）
grid = []
for k in range(0, 91):
    t = new_tank()
    for _ in range(k):
        t.drive(turn=g.TURN_RIGHT, move=0.0)
    grid.append(t.angle % 360.0)
assert all(abs(wrap(grid[k] - per_frame * k)) < 1e-9 for k in range(91))

best_old = min(abs(wrap(DESIRED - a)) for a in grid)
assert 1.9 < best_old < 2.1, best_old          # 4° 网格 ⇒ 最大残差 2°
print(f"[1] 旧键盘 OK：只能落在 4° 格点上，对 {DESIRED:.1f}° 的最好残差 "
      f"{best_old:.2f}°（永远不等于，只能来回摆）")

# ---------------------------------------- 2) 多按一帧 = 4° = 400px 上打空
t = new_tank()
err4 = 4.0
t.angle = DESIRED + err4                     # 过冲一帧的角度
t.sync_image()
victim = g.Tank(TARGET[0], TARGET[1], g.BLUE, dict(P1_KEYS))
victim.sync_image()
bullet = t.fire()
hit = False
for _ in range(200):
    bullet.update()
    if bullet.touches(victim):
        hit = True
        break
offset4 = bullet.pos.distance_to(victim.pos)
assert not hit, f"4° 误差在 {DIST:.0f}px 上不该命中（最近距离 {offset4:.1f}px）"

t.angle = DESIRED                             # 误差 0：必中
t.sync_image()
t.cooldown = 0
bullet = t.fire()
hit = False
for _ in range(200):
    bullet.update()
    if bullet.touches(victim):
        hit = True
        break
assert hit, "零误差必须命中"
assert DIST * math.tan(math.radians(err4)) > g.HIT_RADIUS + g.BULLET_RADIUS
print(f"[2] 命中判定 OK：误差 0° 命中；过冲一帧（4°）在 {DIST:.0f}px 上偏离 "
      f"{DIST * math.tan(math.radians(err4)):.0f}px > 命中半径 "
      f"{g.HIT_RADIUS + g.BULLET_RADIUS}px → 打空")

# ---------------------------------------- 3) 新键盘：点按一帧 = 1°
assert 0.0 < g.TURN_FINE_GAIN < 1.0 and g.TURN_RAMP_FRAMES > 0


def tap_delta(frames):
    """实测「按住 frames 帧再松手」转过的角度（不是抄公式，是真跑代码）。"""
    t = new_tank()
    for _ in range(frames):
        t.handle_input(Keys({pygame.K_d: True}))
    t.handle_input(Keys({}))                  # 松手：加速段复位
    return t.angle


tap1 = tap_delta(1)
assert abs(tap1 - per_frame * g.TURN_FINE_GAIN) < 1e-9, tap1
fine_grid = [tap_delta(1) * k % 360.0 for k in range(361)]
best_new = min(abs(wrap(DESIRED - a)) for a in fine_grid)
assert best_new < 1e-9, best_new
print(f"[3] 点按微转 OK：点按 1 帧 = {tap1:.2f}°（满舵的 "
      f"{g.TURN_FINE_GAIN:g}），可达角度按 1° 分格 → 对 {DESIRED:.1f}° 的残差 "
      f"{best_new:.2f}°（38 次点按精确停在 38°）")


# ---------------------------------------- 4) 闭环瞄准：新物理收敛、旧物理收敛不了
def aim_by_tapping(tank, tx, ty, digits_ok=0.5, cycles=240):
    """模拟玩家：看一眼误差 -> 点一下（按 1 帧 + 松 1 帧）-> 再看。"""
    for _ in range(cycles):
        err = wrap(desired_to(tank, tx, ty) - tank.angle)
        if abs(err) <= digits_ok:
            break
        key = pygame.K_d if err > 0 else pygame.K_a
        tank.handle_input(Keys({key: True}))
        tank.handle_input(Keys({}))           # 松手复位，下一次仍是 1° 起手
    return wrap(desired_to(tank, tx, ty) - tank.angle)


def aim_with_full_turn(tank, tx, ty, cycles=240):
    """旧键盘：单帧只能给 ±turn_speed，误差小于一帧就再也压不下去。"""
    for _ in range(cycles):
        err = wrap(desired_to(tank, tx, ty) - tank.angle)
        if abs(err) <= per_frame / 2.0:       # 4° 格点的极限：半格 2°
            break
        tank.drive(turn=g.TURN_RIGHT if err > 0 else g.TURN_LEFT, move=0.0)
    return wrap(desired_to(tank, tx, ty) - tank.angle)


shooter = new_tank()
new_err = aim_by_tapping(shooter, *TARGET)
assert abs(new_err) <= 0.5, new_err
shooter.sync_image()
shooter.cooldown = 0
bullet = shooter.fire()
hit = False
for _ in range(200):
    bullet.update()
    if bullet.touches(victim):
        hit = True
        break
assert hit, f"点按瞄准后应命中，残差 {new_err:.2f}°"

old_shooter = new_tank()
old_err = aim_with_full_turn(old_shooter, *TARGET)
assert abs(old_err) > 1.9, old_err
print(f"[4] 闭环瞄准 OK：新键盘点按收敛到 {new_err:+.2f}° 并命中；"
      f"旧物理只能到 {old_err:+.2f}°（4° 格点，再点就越过去）")

# ---------------------------------------- 5) 满舵速度没变（甩炮口不吃亏）
t = new_tank()
for _ in range(int(g.TURN_RAMP_FRAMES) + 4):
    t.handle_input(Keys({pygame.K_d: True}))
before = t.angle
t.handle_input(Keys({pygame.K_d: True}))
assert abs(wrap(t.angle - before) - per_frame) < 1e-9, (t.angle, before)
assert abs(g.TURN_SPEED_DEG_PER_SEC - per_frame * g.FPS) < 1e-9
print(f"[5] 满舵 OK：加速段结束后每帧 {per_frame:g}°（= "
      f"{g.TURN_SPEED_DEG_PER_SEC:g}°/s），与改动前完全一致")

# ---------------------------------------- 6) 转向 dt 化：换帧率不失衡
a = new_tank()
b = new_tank()
for _ in range(15):                    # 30fps × 15 帧 = 0.5 秒
    a.handle_input(Keys({pygame.K_d: True}), dt=1.0 / 30.0)
for _ in range(30):                    # 60fps × 30 帧 = 同样的 0.5 秒
    b.handle_input(Keys({pygame.K_d: True}), dt=1.0 / 60.0)
assert abs(a.angle - b.angle) / b.angle < 0.10, (a.angle, b.angle)

# 对照：完全不看 dt 的旧写法（每帧固定 4°，15 帧）会慢一半
c = new_tank()
for _ in range(15):
    c.drive(turn=g.TURN_RIGHT, move=0.0)
assert abs(b.angle - c.angle) / b.angle > 0.40, (b.angle, c.angle)

# 模拟量通道的 dt 才是严格的：两次半帧 == 一次整帧
d = new_tank()
e = new_tank()
d.drive(g.TURN_RIGHT, 0.0, 2.0 / 60.0)
e.drive(g.TURN_RIGHT, 0.0, 1.0 / 60.0)
e.drive(g.TURN_RIGHT, 0.0, 1.0 / 60.0)
assert abs(d.angle - e.angle) < 1e-9
print(f"[6] dt 化 OK：同样 0.5 秒，30fps 转 {a.angle:.2f}°、60fps 转 "
      f"{b.angle:.2f}°（差 {abs(a.angle - b.angle) / b.angle * 100:.1f}%）；"
      f"忽略 dt 的旧写法只有 {c.angle:.2f}°")

print("TURN SELFTEST OK：4° 格点 -> 1° 点按微转 + 帧率无关的转向")
