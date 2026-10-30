import math
import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import pygame
import tank_game as g

print("pygame", pygame.version.ver)

# 1) 冷却与开火
t1 = g.Tank(200, 200, g.RED, {"forward": pygame.K_w, "back": pygame.K_s,
                             "left": pygame.K_a, "right": pygame.K_d,
                             "fire": pygame.K_SPACE})
t2 = g.Tank(600, 400, g.BLUE, {"forward": pygame.K_UP, "back": pygame.K_DOWN,
                              "left": pygame.K_LEFT, "right": pygame.K_RIGHT,
                              "fire": pygame.K_RETURN})
assert t1.can_fire()
b = t1.fire()
assert not t1.can_fire(), "开火后应立即进入冷却"
for _ in range(g.FIRE_COOLDOWN):
    t1.tick()
assert t1.can_fire(), "冷却结束后应可再次开火"

# 2) 子弹沿炮管方向飞出（角度 0 朝上）
b = g.Bullet(200, 200, 0, t1)
y0 = b.pos.y
b.update()
assert b.pos.y < y0 and abs(b.pos.x - 200) < 1e-9, (b.pos.x, b.pos.y)
assert abs((y0 - b.pos.y) - g.BULLET_SPEED) < 1e-6

# 3) 各角度方向正确
for angle in (0, 90, 180, 270, 45):
    b = g.Bullet(400, 300, angle, t1)
    start = pygame.Vector2(b.pos)
    b.update()
    rad = math.radians(angle)
    expect = start + pygame.Vector2(math.sin(rad), -math.cos(rad)) * g.BULLET_SPEED
    assert b.pos.distance_to(expect) < 1e-6, (angle, b.pos, expect)

# 4) 炮口位置在车头方向
t1.angle = 0
assert t1.muzzle_position().y < t1.pos.y - g.TANK_HALF
t1.angle = 90
assert t1.muzzle_position().x > t1.pos.x + g.TANK_HALF

# 5) 10 发击毁 + 闪白
SHOTS = g.MAX_HP // g.DAMAGE
assert SHOTS == 10, SHOTS
t2.hp = g.MAX_HP
destroyed = False
for i in range(SHOTS):
    destroyed = t2.take_hit(g.DAMAGE)
    if i < SHOTS - 1:
        assert not destroyed and t2.alive, i
assert destroyed and not t2.alive and t2.hp == 0
t2.tick()
assert t2.flash == 5, t2.flash          # take_hit 设 6，tick 一次后 5
for _ in range(6):
    t2.tick()
assert t2.flash == 0

# 5b) 生命值：默认 5 条命、扣命、复活满血回出生点、复活无敌免伤
assert g.MAX_LIVES == 5, g.MAX_LIVES
t3 = g.Tank(300, 300, g.BLUE, {"forward": pygame.K_UP, "back": pygame.K_DOWN,
                               "left": pygame.K_LEFT, "right": pygame.K_RIGHT,
                               "fire": pygame.K_RETURN})
t3.angle = 0
t3.sync_image()
assert t3.lives == g.MAX_LIVES
assert t3.spawn_point.distance_to(pygame.Vector2(300, 300)) < 1e-6
assert t3.spawn_angle == 0 and t3.invuln == 0 and t3.respawn_timer == 0

t3.pos.update(500, 100)          # 跑远一点，验证复活是回出生点
t3.angle = 90
destroyed = False
for _ in range(SHOTS):
    destroyed = t3.take_hit(g.DAMAGE)
assert destroyed and not t3.alive
assert t3.lose_life() is True, "5 条命时被击毁一次应该还有命"
assert t3.lives == g.MAX_LIVES - 1
assert t3.respawn_timer == g.RESPAWN_DELAY

# 复活点默认 = 出生点；main() 掉命时会改成离对手最远的角
assert t3.respawn_point.distance_to(t3.spawn_point) < 1e-6
assert t3.respawn_angle == t3.spawn_angle
t3.respawn()
assert t3.alive and t3.hp == g.MAX_HP and t3.lives == g.MAX_LIVES - 1
assert t3.pos.distance_to(pygame.Vector2(300, 300)) < 1e-6, t3.pos
assert t3.angle == 0 and t3.respawn_timer == 0
assert t3.invuln == g.SPAWN_INVULN

# 无敌期间不吃伤害
assert t3.take_hit(g.DAMAGE) is False and t3.hp == g.MAX_HP
for _ in range(g.SPAWN_INVULN):
    t3.tick()
assert t3.invuln == 0, t3.invuln
assert t3.take_hit(g.DAMAGE) is False and t3.hp == g.MAX_HP - g.DAMAGE

# 命扣到 0：不再复活，交给 main() 分胜负
t3.lives = 1
t3.hp = g.DAMAGE
t3.invuln = 0
assert t3.take_hit(g.DAMAGE) is True
assert t3.lose_life() is False and t3.lives == 0 and t3.respawn_timer == 0
print("[5b] 生命值 OK：5 条命 / 扣命 / 出生点满血复活 / 无敌免伤 / 命尽不复活")

# 6) 命中判定：子弹打中对方坦克（圆形判定，唯一出处 Bullet.touches）
t1 = g.Tank(200, 200, g.RED, {"forward": pygame.K_w, "back": pygame.K_s,
                             "left": pygame.K_a, "right": pygame.K_d,
                             "fire": pygame.K_SPACE})
t2 = g.Tank(200, 240, g.BLUE, {"forward": pygame.K_UP, "back": pygame.K_DOWN,
                              "left": pygame.K_LEFT, "right": pygame.K_RIGHT,
                              "fire": pygame.K_RETURN})
t1.sync_image(); t2.sync_image()
bullet = g.Bullet(200, 380, 0, t1)  # 从下方往上飞，迎头命中
hits = 0
for _ in range(30):
    bullet.update(); t2.sync_image()
    if bullet.touches(t2):
        hits += 1
        t2.take_hit(g.DAMAGE)
        break
assert hits == 1 and t2.hp == g.MAX_HP - g.DAMAGE, (hits, t2.hp)

# 6b) 擦边不算命中：子弹从车体旁边 30px 飞过（旧包围盒判定会误判命中）
t2.hp = g.MAX_HP
t2.angle = 0
t2.sync_image()
miss = g.Bullet(230, 380, 0, t1)     # x 差 30px > HIT_RADIUS + BULLET_RADIUS
near = 0
for _ in range(30):
    miss.update(); t2.sync_image()
    if miss.touches(t2):
        near += 1
assert near == 0, "30px 外的擦边不该算命中"
assert t2.hp == g.MAX_HP
print(f"[6] 命中判定 OK：圆形半径 {g.HIT_RADIUS}+{g.BULLET_RADIUS}，擦边 30px 不命中")

# 6c) 复活点：四个角里挑离对手最远的那个，朝向指向对手
far = g.Tank(120, 120, g.RED, {"forward": pygame.K_w, "back": pygame.K_s,
                               "left": pygame.K_a, "right": pygame.K_d,
                               "fire": pygame.K_SPACE})
point, angle = g.safe_respawn_point(far, None)
assert point.distance_to(pygame.Vector2(680, 480)) < 1e-6, point
expect = math.degrees(math.atan2(120 - 680, -(120 - 480))) % 360
assert abs(angle - expect) < 1e-6, (angle, expect)
alone = g.safe_respawn_point(None, pygame.Vector2(300, 300))
assert alone[0].distance_to(pygame.Vector2(300, 300)) < 1e-6 and alone[1] == 0.0
print("[6c] 复活点 OK：挑最远角 + 朝向对手")

# 7) 爆炸动画会按时消失
ex = g.Explosion(300, 300, g.YELLOW, 40, 12)
for i in range(13):
    ex.update()
assert ex.alive() is False

# 8) 长时间主循环等价逻辑无异常 + 边界不越界
t1 = g.Tank(120, 120, g.RED, {"forward": pygame.K_w, "back": pygame.K_s,
                             "left": pygame.K_a, "right": pygame.K_d,
                             "fire": pygame.K_SPACE})
keys = {pygame.K_w: True, pygame.K_a: True}
class K(dict):
    def __getitem__(self, k):
        return self.get(k, False)
k = K(keys)
world = pygame.Surface((g.SCREEN_WIDTH, g.SCREEN_HEIGHT))
for _ in range(600):
    t1.handle_input(k); t1.tick(); t1.sync_image()
assert g.TANK_HALF <= t1.pos.x <= g.SCREEN_WIDTH - g.TANK_HALF
assert g.TANK_HALF <= t1.pos.y <= g.SCREEN_HEIGHT - g.TANK_HALF
g.draw_background(world)
g.draw_health_bar(world, t1)
g.draw_lives(world, t1, 10, 10)
t1.lives = 2
g.draw_lives(world, t1, 10, 10)

print("所有测试通过：开火/冷却/弹道/10发击毁/闪白/生命值与复活/命中判定/爆炸/边界")
