"""两车车体互斥自检：不重叠 / 对称分离 / 幂等 / 与掩体叠加 / 完全重合 / AI 不磨。

覆盖（每条断言都直接对应「允许双方交互位置」这条需求）：
1. 不重叠：两车面对面全速开 120 帧，每帧分离后中心距始终 >= 2*TANK_HALF - 1；
2. 双方对称：手工造 20px 穿透，一次分离后两车各走 10px、方向相反；
3. 幂等：已分开时连调 5 次，坐标逐位不变（不抖），返回值也变回 False；
4. 与掩体叠加：一台被顶在方块上、另一台从对面压上来，分离后没人陷进方块；
5. 完全重合：坐标相同也能一次分开，且没有 NaN、都在场地内；
6. AI 不磨：玩家车全速死顶 AI 300 帧，AI 保持 >= 43px、一直在动、仍在开火。

运行：SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3.11 _selftest_body.py
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

MIN_DIST = 2 * g.TANK_HALF      # 44px：两车中心的最小距离
TOL = g.TANK_GAP                # 1px：与 separate_tanks 内部收手阈值同一口径


def new_tank(x, y, color=g.RED, angle=0.0):
    t = g.Tank(x, y, color, dict(P1_KEYS))
    t.angle = angle
    t.sync_image()
    return t


def walls_from_layout():
    return tuple(pygame.Rect(*spec) for spec in g.OBSTACLE_RECTS)


def assert_in_field(tank):
    assert g.TANK_HALF <= tank.pos.x <= g.SCREEN_WIDTH - g.TANK_HALF, tank.pos
    assert g.TANK_HALF <= tank.pos.y <= g.SCREEN_HEIGHT - g.TANK_HALF, tank.pos


# ================================================== 1) 面对面全速开：不穿模
red = new_tank(400, 150, g.RED, angle=180.0)      # 朝下
blue = new_tank(400, 450, g.BLUE, angle=0.0)      # 朝上
separated_frames = 0
min_seen = 1e9
for frame in range(120):
    red.drive(0.0, 1.0)
    blue.drive(0.0, 1.0)
    if g.separate_tanks(red, blue):
        separated_frames += 1
    dist = red.pos.distance_to(blue.pos)
    min_seen = min(min_seen, dist)
    assert dist >= MIN_DIST - TOL, f"第 {frame} 帧两车重叠：{dist:.2f}px"
    assert_in_field(red)
    assert_in_field(blue)
assert separated_frames > 0, "全速对撞居然一次都没触发分离"
print(f"[1] 不重叠 OK：120 帧对撞，最小中心距 {min_seen:.2f}px"
      f"（下限 {MIN_DIST - TOL:.0f}px），触发分离 {separated_frames} 帧")

# ================================================== 2) 双方各让一半（对称）
a = new_tank(300, 300, g.RED, angle=0.0)
b = new_tank(300 + MIN_DIST - 20.0, 300, g.BLUE, angle=180.0)   # 穿透 20px
before_a, before_b = pygame.Vector2(a.pos), pygame.Vector2(b.pos)
assert g.separate_tanks(a, b) is True
moved_a, moved_b = a.pos - before_a, b.pos - before_b
assert abs(moved_a.length() - 10.0) < 0.5, f"被撞的一方走了 {moved_a.length():.2f}px"
assert abs(moved_b.length() - 10.0) < 0.5, f"撞人的一方走了 {moved_b.length():.2f}px"
assert moved_a.x < 0 < moved_b.x, (moved_a, moved_b)      # 方向相反
assert abs(moved_a.y) < 1e-9 and abs(moved_b.y) < 1e-9, (moved_a, moved_b)
assert a.pos.distance_to(b.pos) >= MIN_DIST - TOL
print(f"[2] 双方对称 OK：穿透 20px -> 各退 {moved_a.length():.1f}px / "
      f"{moved_b.length():.1f}px，中心距 {a.pos.distance_to(b.pos):.1f}px")

# ================================================== 3) 已分开 = 幂等（不抖）
snap = (tuple(a.pos), tuple(b.pos))
for _ in range(5):
    assert g.separate_tanks(a, b) is False, "已经分开了却还在推"
assert (tuple(a.pos), tuple(b.pos)) == snap, "已分开时坐标被改动了（会抖）"
print(f"[3] 幂等 OK：连调 5 次坐标逐位不变 {snap[0]} / {snap[1]}")

# ================================================== 4) 掩体 + 对手三角夹
BLOCK = pygame.Rect(*g.OBSTACLE_RECTS[1])       # (240, 300, 56, 56)
WALLS = walls_from_layout()
blue = new_tank(BLOCK.right + g.TANK_HALF, BLOCK.centery, g.BLUE, angle=0.0)
red = new_tank(blue.pos.x + 20.0, blue.pos.y, g.RED, angle=270.0)
blue.obstacles = red.obstacles = WALLS
assert g.separate_tanks(red, blue) is True
for tank in (red, blue):
    assert g.obstacle_at(tank.pos, WALLS, g.TANK_HALF - 1) is None, \
        f"分离后陷进方块：{tank.color} {tank.pos}"
    assert_in_field(tank)
assert blue.pos.x >= BLOCK.right + g.TANK_HALF - 1e-6, blue.pos
assert red.pos.distance_to(blue.pos) >= MIN_DIST - TOL
print(f"[4] 掩体叠加 OK：被墙挡住的那一半由另一台承担，"
      f"红 x={red.pos.x:.1f} 蓝 x={blue.pos.x:.1f}，都在方块外")

# ================================================== 5) 完全重合（退化情形）
a = new_tank(400, 300, g.RED, angle=0.0)
b = new_tank(400, 300, g.BLUE, angle=90.0)
assert g.separate_tanks(a, b) is True
dist = a.pos.distance_to(b.pos)
assert dist > 0.0, "完全重合没能分开"
assert dist >= MIN_DIST - TOL, dist
for tank in (a, b):
    assert math.isfinite(tank.pos.x) and math.isfinite(tank.pos.y), tank.pos
    assert_in_field(tank)
print(f"[5] 完全重合 OK：一次分离后中心距 {dist:.1f}px（无 NaN、不出场地）")

# ================================================== 6) AI 被死顶也不磨
player = new_tank(400, 300, g.RED, angle=180.0)
ai_tank = new_tank(400, 60, g.BLUE, angle=180.0)
ai = g.TankAI("hard", seed=5)
bullets = pygame.sprite.Group()
prev = pygame.Vector2(ai_tank.pos)
travelled = 0.0
shots = 0
min_seen = 1e9
for frame in range(300):
    ai_tank.tick()
    # 玩家车每帧朝 AI 全速压上来：这是「死顶」，不是站着不动的靶
    dx = ai_tank.pos.x - player.pos.x
    dy = ai_tank.pos.y - player.pos.y
    player.angle = math.degrees(math.atan2(dx, -dy)) % 360.0
    player.drive(0.0, 1.0)
    ai.update(ai_tank, player)
    ai_tank.drive(ai.turn(), ai.move())
    if ai.want_fire and ai_tank.can_fire():
        # 真的开出去（会进 18 帧冷却），统计的是「真开火」而不是「想开火」
        bullets.add(ai_tank.fire())
        shots += 1
    for bullet in bullets.sprites():
        # 只让它飞、不算伤害：这条用例考的是「让开 + 照打」，不是击毁流程
        bullet.update()
    g.separate_tanks(player, ai_tank)
    dist = player.pos.distance_to(ai_tank.pos)
    min_seen = min(min_seen, dist)
    assert dist >= MIN_DIST - TOL, f"第 {frame} 帧被顶穿：{dist:.2f}px"
    assert_in_field(ai_tank)
    travelled += ai_tank.pos.distance_to(prev)
    prev.update(ai_tank.pos)
assert travelled > 0.0, f"AI 一步没挪（被顶成雕塑），位移 {travelled:.1f}px"
assert shots >= 1, "被顶住时一枪没开，等于站着挨打"
print(f"[6] AI 不磨 OK：300 帧死顶下最小距离 {min_seen:.2f}px、"
      f"位移 {travelled:.0f}px、开火 {shots} 次")

print("BODY SELFTEST OK：两车位置真的双向交互（对称分离 / 不穿模 / AI 会让开）")
