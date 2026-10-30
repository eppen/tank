"""coach 的交叉验证：在真实物理上跑三种「人类」打法，看评分与建议说不说得通。

不是产品功能，是调参 / 回归用的沙盘（改评分权重或阈值后跑一遍，看排序是否还成立）：
  naive   ：站着不动 + 按住开火键不放（开局打一枪之后再没出枪）
  spam    ：站着不动 + 冷却一好就盲打（不管距离和准度）
  trained ：按 README 攻略打：280-420px、准星 5° 内、一下一下敲、
            它开火后换向、自己无敌期照打、它无敌期停火
每档打 6 局 vs HARD，打印三种打法的均分、命中率、分项与主要纠正。

运行：SDL_VIDEODRIVER=dummy python3.11 _sim_coach.py
预期排序：naive（按住不放）< spam（盲打）< trained（按攻略打），且 trained
不出现「按住不放 / 冷却空转」这类误报。
"""

import math
import os
import random

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ["TANK_COACH_FILE"] = "/tmp/sim_coach_hist.json"

import pygame

import tank_game as g
import tank_coach as c

pygame.init()

P1 = {"forward": pygame.K_w, "back": pygame.K_s, "left": pygame.K_a,
      "right": pygame.K_d, "fire": pygame.K_SPACE}


def wrap(a):
    return (a + 180.0) % 360.0 - 180.0


def err_to(h, t):
    return abs(wrap(math.degrees(math.atan2(t.pos.x - h.pos.x, -(t.pos.y - h.pos.y)))
                    - h.angle))


def dist_of(h, t):
    return h.pos.distance_to(t.pos)


def play(policy, seed, max_frames=9000):
    """跑一局：返回 (coach, 是否人类赢)。"""
    random.seed(seed)
    h = g.Tank(120, 120, g.RED, P1)
    cpu = g.Tank(g.SCREEN_WIDTH - 120, g.SCREEN_HEIGHT - 120, g.BLUE, P1)
    cpu.ai = g.TankAI("hard", seed=seed)
    coach = c.BattleCoach(fps=g.FPS, fire_range=g.AI_FIRE_RANGE,
                          fire_cooldown=g.FIRE_COOLDOWN,
                          invuln_frames=g.SPAWN_INVULN, max_lives=g.MAX_LIVES)
    coach.begin_round("HARD")
    bullets = pygame.sprite.Group()
    prev_fire = False
    enemy_shot_frame = -999
    won = None
    held = False

    for frame in range(max_frames):
        h.tick()
        cpu.tick()
        if not h.alive:
            if h.respawn_timer > 0:
                h.respawn_timer -= 1
                if h.respawn_timer == 0:
                    h.respawn()
            h.sync_image()
            cpu.sync_image()
            continue
        if not cpu.alive:
            if cpu.respawn_timer > 0:
                cpu.respawn_timer -= 1
                if cpu.respawn_timer == 0:
                    cpu.respawn()
            h.sync_image()
            cpu.sync_image()
            continue

        # --- 电脑 ---
        cpu.ai.update(cpu, h)
        cpu.drive(cpu.ai.turn(), cpu.ai.move())
        if cpu.ai.want_fire and cpu.can_fire():
            bullet = cpu.fire()
            bullets.add(bullet)
            coach.on_enemy_shot()
            enemy_shot_frame = frame

        # --- 人类策略 ---
        d = dist_of(h, cpu)
        e = err_to(h, cpu)
        turn = move = 0.0
        want_fire = False
        if policy == "naive":
            held = True                      # 按住不放（只会有第一帧那一次上升沿）
            want_fire = True
        elif policy == "spam":
            held = (frame % 2 == 1)          # 一下一下猛敲，但不管准度距离
            want_fire = True
        else:                                # trained
            turn = max(-1.0, min(1.0, wrap(
                math.degrees(math.atan2(cpu.pos.x - h.pos.x,
                                        -(cpu.pos.y - h.pos.y))) - h.angle) / 22.0))
            if d > 420:
                move = 1.0
            elif d < 280:
                move = -0.8
            if frame - enemy_shot_frame < 12:        # 它刚开火：立刻换向
                turn = 1.0 if (frame // 12) % 2 else -1.0
                move = 0.0
            held = (frame % 4 == 0)                  # 每 4 帧敲一次（快于冷却）
            want_fire = (e <= 5.0 and d <= 560.0 and cpu.invuln == 0)
        h.drive(turn, move)

        fire_edge = held and not prev_fire
        prev_fire = held
        if fire_edge and want_fire and h.can_fire():
            bullet = h.fire()
            bullets.add(bullet)
            coach.on_shot(h, cpu, bullet)

        coach.observe(h, cpu, held)

        # --- 子弹 / 命中 ---
        for bullet in bullets.sprites():
            bullet.update()
            for tank in (h, cpu):
                if tank is bullet.owner or not tank.alive:
                    continue
                if bullet.touches(tank):
                    shielded = tank.invuln > 0
                    bullet.kill()
                    coach.on_hit(by_human=(bullet.owner is h),
                                 shield=shielded, bullet=bullet)
                    if tank.take_hit(g.DAMAGE):
                        coach.on_death(victim_is_human=(tank is h))
                        tank.kill()
                        if not tank.lose_life():
                            won = (tank is cpu)
                            return coach, won
        h.sync_image()
        cpu.sync_image()
    return coach, won


def summarize(policy, rows):
    scores = [co.scores()[1] for co, _ in rows]
    accs = [co.metrics()["accuracy"] for co, _ in rows]
    parts = {}
    for co, _ in rows:
        for k, v in co.scores()[0].items():
            parts.setdefault(k, []).append(v)
    wins = sum(1 for _, w in rows if w)
    codes = {}
    for co, _ in rows:
        for a in co.advices():
            codes[a["code"]] = codes.get(a["code"], 0) + 1
    top = ", ".join(f"{k}×{v}" for k, v in sorted(codes.items(), key=lambda kv: -kv[1])[:4])
    print(f"{policy:<8} 分 {sum(scores)/len(scores):5.1f}  "
          f"胜 {wins}/{len(rows)}  "
          f"命中率 {100*sum(accs)/len(accs):5.1f}%  "
          f"分项 " + " ".join(f"{k[:3]}{sum(v)/len(v):4.0f}" for k, v in parts.items()))
    print(f"{'':<8} 主要纠正：{top or '无'}")


results = {}
for policy in ("naive", "spam", "trained"):
    rows = [play(policy, seed) for seed in range(6)]
    results[policy] = rows
    summarize(policy, rows)

print()
print("示例报告（trained 第一局）：")
print(results["trained"][0][0].report_text())
print()
print("示例报告（naive 第一局）：")
print(results["naive"][0][0].report_text())
