"""障碍物（掩体）自检：布局不变量 / 车体碰撞 / 沿墙滑行 / 视线 / 子弹 / AI。

覆盖：
1. 布局不变量：180° 旋转对称、不压出生点与复活角、不挡两条出生对角线、
   两两不重叠、方块厚度 >= 子弹两帧位移（防穿透）；
2. 车体碰撞：满速朝方块开 120 帧，车体始终在方块表面之外（从不陷进去）；
3. 沿墙滑行：45° 顶墙时切向坐标真的在走（不是撞上就粘死）；
4. 纯函数口径：obstacle_at / has_line_of_sight / tank_blocked_ahead；
5. 子弹：进块即被判定命中并 kill()，空地上飞不算撞墙；
6. AI：隔墙不开枪（want_fire = False），1500 帧 AI vs AI 不卡死、不穿墙；
7. TANK_WALLS 开关：build_obstacles() 受 OBSTACLE_ENABLED 控制。

运行：SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3.11 _selftest_walls.py
      TANK_WALLS=0 ... python3.11 _selftest_walls.py   # 只验证「关闭后确实没有障碍物」
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import tank_game as g

P1_KEYS = {"forward": pygame.K_w, "back": pygame.K_s,
           "left": pygame.K_a, "right": pygame.K_d,
           "fire": pygame.K_SPACE}


def new_tank(x, y, color=g.RED, angle=0.0):
    t = g.Tank(x, y, color, dict(P1_KEYS))
    t.angle = angle
    t.sync_image()
    return t


def walls_from_rects(rects):
    return tuple(pygame.Rect(*spec) for spec in rects)


def assert_layout_invariants(rects, map_id):
    """三条硬约束 + 不重叠 + 厚度；empty 图应传入空 rects。"""
    walls = walls_from_rects(rects)
    if not walls:
        assert map_id == "empty"
        return walls
    for r in walls:
        assert 0 <= r.left and r.right <= g.SCREEN_WIDTH, (map_id, r)
        assert 0 <= r.top and r.bottom <= g.SCREEN_HEIGHT, (map_id, r)
        assert min(r.width, r.height) >= g.BULLET_SPEED * 2, (map_id, r)
    for i, a in enumerate(walls):
        for b in walls[i + 1:]:
            assert not a.colliderect(b), f"{map_id} 方块重叠：{a} / {b}"
    for r in walls:
        mirror = pygame.Rect(g.SCREEN_WIDTH - r.right, g.SCREEN_HEIGHT - r.bottom,
                             r.width, r.height)
        assert any(mirror == other for other in walls), \
            f"{map_id}: {r} 没有 180° 对称方块"
    for corner in g.RESPAWN_CORNERS:
        pos = pygame.Vector2(corner)
        for r in walls:
            gap = g._closest_point(pos, r).distance_to(pos)
            assert gap >= g.TANK_HALF + 40, \
                f"{map_id}: 复活角 {corner} 离方块 {r} 只有 {gap:.0f}px"
    spawn_a = pygame.Vector2(g.RESPAWN_CORNERS[0])
    spawn_b = pygame.Vector2(g.RESPAWN_CORNERS[3])
    for a, b in ((spawn_a, spawn_b),
                 (pygame.Vector2(g.RESPAWN_CORNERS[1]),
                  pygame.Vector2(g.RESPAWN_CORNERS[2]))):
        for r in walls:
            assert not r.clipline((a.x, a.y), (b.x, b.y)), \
                f"{map_id}: 出生对角线被 {r} 挡住"
        assert g.has_line_of_sight(a, b, walls)
    return walls


# =============================================== 开关关掉时：只验证「真的没有」
if not g.OBSTACLE_ENABLED:
    group = g.build_obstacles(g.DEFAULT_MAP_INDEX)
    assert len(group) == 0, f"TANK_WALLS=0 时不该建出方块，实际 {len(group)} 个"
    t = new_tank(400, 328, angle=270.0)
    t.obstacles = tuple(o.rect for o in group)
    for _ in range(60):
        t.drive(0.0, 1.0)
    assert t.pos.x < 240, f"关掉障碍物后应能开过原方块位置，实际 x={t.pos.x:.1f}"
    print(f"[1] 障碍物已关闭（TANK_WALLS=0）OK：build_obstacles() 返回空组，"
          f"坦克从 x=400 一路开到 x={t.pos.x:.0f}（原方块位置畅通）")
    print("WALLS SELFTEST OK（关闭模式）：只验证开关，不验证碰撞")
    raise SystemExit(0)


# =============================================== 1) 每张地图的布局不变量
assert len(g.MAPS) >= 3, g.MAPS
assert g.MAPS[g.DEFAULT_MAP_INDEX]["id"] == "pillars"
for m in g.MAPS:
    if m["id"] == "empty":
        assert m["rects"] == ()
        group = g.build_obstacles(next(i for i, x in enumerate(g.MAPS)
                                       if x["id"] == "empty"))
        assert len(group) == 0
        assert_layout_invariants((), "empty")
        print("[1] EMPTY OK：build_obstacles → 空组")
        continue
    walls_m = assert_layout_invariants(m["rects"], m["id"])
    built = g.build_obstacles(next(i for i, x in enumerate(g.MAPS)
                                   if x["id"] == m["id"]))
    assert len(built) == len(walls_m), (m["id"], len(built), len(walls_m))
    print(f"[1] {m['label']} OK：{len(walls_m)} 块，"
          f"180° 对称、离复活角 ≥{g.TANK_HALF + 40}px、不挡出生对角线")

# 后续碰撞/滑行/AI 用例仍用默认 pillars（有已知的 (240,300) 测试块）
WALLS = walls_from_rects(g.map_rects(g.DEFAULT_MAP_INDEX))
assert WALLS, "默认 pillars 不能为空"


# =============================================== 2) 车体碰撞：从不陷入方块
# (240,300) 那块的右表面在 x=296，坦克从 x=400 以满速水平撞过去
BLOCK = next(r for r in WALLS if r.left == 240 and r.top == 300)
tank = new_tank(400, 328, angle=270.0)      # 270 = 朝左
tank.obstacles = WALLS
for _ in range(120):
    tank.drive(0.0, 1.0)
    assert g.obstacle_at(tank.pos, WALLS, g.TANK_HALF - 1) is None, \
        f"车体陷进方块了：{tank.pos}（{g.obstacle_at(tank.pos, WALLS, g.TANK_HALF - 1)}）"
assert tank.pos.x >= BLOCK.right + g.TANK_HALF - 0.5, \
    f"车体应该被挡在方块右侧，实际 x={tank.pos.x:.2f}"
assert tank.pos.x < 400, "坦克应该真的开动了（不能原地不动）"

# 反例：没有障碍物时同样的操作会一路开过去（证明上面挡住的是墙，不是别的东西）
free = new_tank(400, 328, angle=270.0)
for _ in range(120):
    free.drive(0.0, 1.0)
assert free.pos.x < 100, free.pos.x
print(f"[2] 车体碰撞 OK：满速撞墙 120 帧停在 x={tank.pos.x:.1f}"
      f"（墙面 {BLOCK.right}+半宽 {g.TANK_HALF}），无墙时同一操作到 x={free.pos.x:.0f}")


# =============================================== 3) 沿墙滑行（不是粘住）
# 贴着方块右表面（x = 296+22 = 318），45° 往「左 + 下」顶，应当沿墙面往下滑
slider = new_tank(BLOCK.right + g.TANK_HALF, 340, angle=225.0)   # 225 = 左下
slider.obstacles = WALLS
y0 = slider.pos.y
min_x = slider.pos.x
for _ in range(15):
    slider.drive(0.0, 1.0)
    assert g.obstacle_at(slider.pos, WALLS, g.TANK_HALF - 0.5) is None, \
        f"车体被挤进墙里：{slider.pos}"
    min_x = min(min_x, slider.pos.x)
assert slider.pos.y - y0 > 20, f"没沿墙滑行，y 只从 {y0:.1f} 到 {slider.pos.y:.1f}"
print(f"[3] 沿墙滑行 OK：45° 顶墙 15 帧，沿墙面下滑 {slider.pos.y - y0:.1f}px，"
      f"x 最靠里 {min_x:.1f}（墙面 {BLOCK.right} + 半宽 {g.TANK_HALF} = "
      f"{BLOCK.right + g.TANK_HALF}，只允许被墙角法线推得更开）")


# =============================================== 4) 纯函数口径
center = pygame.Vector2(BLOCK.center)
assert g.obstacle_at(center, WALLS) == BLOCK           # 点在块内
assert g.obstacle_at(pygame.Vector2(BLOCK.right + 1, center.y), WALLS) is None
assert g.obstacle_at(pygame.Vector2(BLOCK.right + 1, center.y), WALLS,
                     g.BULLET_RADIUS) == BLOCK          # 圆擦到表面也算
assert g.obstacle_at(center, ()) is None                # 空场地

# 视线：穿块 False / 同侧 True / 方块不在中间 True
assert not g.has_line_of_sight(pygame.Vector2(120, center.y), pygame.Vector2(400, center.y), WALLS)
assert g.has_line_of_sight(pygame.Vector2(320, 340), pygame.Vector2(400, 340), WALLS)
assert g.has_line_of_sight(pygame.Vector2(120, 560), pygame.Vector2(680, 560), WALLS)
assert g.has_line_of_sight(pygame.Vector2(120, 120), pygame.Vector2(680, 480), WALLS)
assert g.has_line_of_sight(pygame.Vector2(120, center.y), pygame.Vector2(400, center.y), ())

# 探路：正对墙 True / 背对墙 False / 朝空地 False
probe = new_tank(BLOCK.right + g.TANK_HALF, 328, angle=270.0)
assert g.tank_blocked_ahead(probe, 1.0, WALLS) is True, "正对墙应判定被挡"
probe.angle = 90.0                                       # 背对墙
assert g.tank_blocked_ahead(probe, 1.0, WALLS) is False
probe.pos.update(700, 560)
assert g.tank_blocked_ahead(probe, 1.0, WALLS) is False, "空地不该判定被挡"
print("[4] 纯函数 OK：obstacle_at 块内/块外、has_line_of_sight 穿块/同侧、"
      "tank_blocked_ahead 正对/背对")


# =============================================== 5) 子弹撞墙
shooter = new_tank(400, 328, angle=270.0)     # 朝左，正对方块右表面
bullet = shooter.fire()
killed = None
for frame in range(60):
    bullet.update()
    hit = g.obstacle_at(bullet.pos, WALLS, g.BULLET_RADIUS)
    if hit:
        killed = (frame, pygame.Vector2(bullet.pos))
        bullet.kill()                          # main() 里就是这么处理的
        break
assert killed is not None, "子弹应该被墙拦下"
assert not bullet.alive()
# 判定是逐帧做的：最多允许「撞墙那一帧」已经飞进块里一个位移单位（9px）
assert killed[1].x >= BLOCK.right - g.BULLET_SPEED, f"子弹钻得太深：{killed[1]}"
assert killed[1].x > BLOCK.centerx, f"子弹应该拦在靠外面这一半：{killed[1]}"

# 空地上的子弹不算撞墙（只是飞出屏幕后被 update() 自己回收）
open_shot = new_tank(100, 560, angle=270.0).fire()
for _ in range(80):
    open_shot.update()
    assert g.obstacle_at(open_shot.pos, WALLS, g.BULLET_RADIUS) is None
print(f"[5] 子弹 OK：朝墙开的子弹在第 {killed[0]} 帧于 x={killed[1].x:.0f} 被拦下"
      f"（墙面 {BLOCK.right}），空地上的子弹一路无阻挡")


# =============================================== 6) AI 认得墙
# 目标正藏在方块后面：不能开枪（隔墙开火 = 白送冷却，评估里还算 miss）
hider = new_tank(120, 328, color=g.BLUE)
blocked_ai = g.TankAI("hard", seed=5)
watcher = new_tank(400, 328, angle=270.0)
watcher.obstacles = WALLS
assert not g.has_line_of_sight(watcher.pos, hider.pos, WALLS)
for _ in range(40):
    blocked_ai.update(watcher, hider)
    watcher.drive(blocked_ai.turn(), blocked_ai.move())
    assert blocked_ai.want_fire is False, "隔墙不该开枪"
    assert g.obstacle_at(watcher.pos, WALLS, g.TANK_HALF - 1) is None

# 两个 AI 带障碍物打 1500 帧（含子弹、撞墙、命中、掉血）：一行照抄 main() 的
# 战斗判定顺序 —— 子弹先撞墙、再看坦克。要证明的是「掩体没有把 AI 变成雕塑」：
#   1) 不越界、不陷进方块；
#   2) 双方都真的走了 200px 以上（会主动压上 / 拉开，而不是原地磨）；
#   3) 真的打得起来（有人掉血）—— 撞墙停车 + 隔墙不开枪叠在一起时，最容易
#      退化成「两车隔着柱子站桩、谁也不开枪」。
a_red, a_blue = g.TankAI("normal", seed=21), g.TankAI("hard", seed=22)
r_tank = new_tank(120, 120, color=g.RED)
b_tank = new_tank(680, 480, color=g.BLUE, angle=180.0)
r_tank.obstacles = b_tank.obstacles = WALLS
start = (pygame.Vector2(r_tank.pos), pygame.Vector2(b_tank.pos))
travelled = [0.0, 0.0]
prev = [pygame.Vector2(r_tank.pos), pygame.Vector2(b_tank.pos)]
bullets = pygame.sprite.Group()
shots = [0, 0]
for _ in range(1500):
    for i, (me, ai, other) in enumerate(((r_tank, a_red, b_tank),
                                         (b_tank, a_blue, r_tank))):
        me.tick()
        ai.update(me, other)
        me.drive(ai.turn(), ai.move())
        if ai.want_fire and me.can_fire():
            bullets.add(me.fire())
            shots[i] += 1
        travelled[i] += me.pos.distance_to(prev[i])
        prev[i].update(me.pos)
        assert g.TANK_HALF <= me.pos.x <= g.SCREEN_WIDTH - g.TANK_HALF, me.pos
        assert g.TANK_HALF <= me.pos.y <= g.SCREEN_HEIGHT - g.TANK_HALF, me.pos
        assert g.obstacle_at(me.pos, WALLS, g.TANK_HALF - 1) is None, \
            f"AI 开进方块了：{me.pos}"
        me.sync_image()
    # 帧末分离：两台车本帧都动完了，按「各让一半」分开（顺序与 main() 一致）
    g.separate_tanks(r_tank, b_tank)
    for b in bullets.sprites():
        b.update()
        assert g.obstacle_at(b.pos, WALLS, g.BULLET_RADIUS) is None or b.alive()
        if g.obstacle_at(b.pos, WALLS, g.BULLET_RADIUS):
            b.kill()                        # 先撞墙：不再看坦克
            continue
        for victim in (r_tank, b_tank):
            if victim is not b.owner and b.touches(victim):
                victim.take_hit()
                b.kill()
                break

net = [r_tank.pos.distance_to(start[0]), b_tank.pos.distance_to(start[1])]
hp_loss = (g.MAX_HP - r_tank.hp) + (g.MAX_HP - b_tank.hp)
assert min(travelled) > 200, f"AI 卡死了：累计位移 {travelled}"
assert min(net) > 40, f"AI 基本没挪窝：{net}"
assert sum(shots) > 10, f"AI 隔着掩体基本不开枪了：{shots}"
assert hp_loss > 0, f"1500 帧一发都没打中，掩体把战斗卡死了（开火 {shots}）"
print(f"[6] AI OK：隔墙 40 帧不开枪；AI vs AI 1500 帧共开火 {shots[0]}/{shots[1]} 发、"
      f"掉血 {hp_loss}、累计位移 {travelled[0]:.0f}/{travelled[1]:.0f}px、"
      f"终点位移 {net[0]:.0f}/{net[1]:.0f}px，全程在场地内且不穿墙")


# =============================================== 7) TANK_WALLS 开关
saved = g.OBSTACLE_ENABLED
try:
    g.OBSTACLE_ENABLED = False
    assert len(g.build_obstacles()) == 0
    g.OBSTACLE_ENABLED = True
    group = g.build_obstacles()
    assert len(group) == len(g.OBSTACLE_RECTS) == len(WALLS)
    assert all(sprite.rect in WALLS for sprite in group)
finally:
    g.OBSTACLE_ENABLED = saved
print(f"[7] 开关 OK：OBSTACLE_ENABLED=False → 空组；True → {len(WALLS)} 块，"
      f"精灵 rect 与 OBSTACLE_RECTS 一致")

print("WALLS SELFTEST OK：布局对称 / 车体不穿墙 / 沿墙滑行 / 视线 / 子弹 / AI 不卡死")
