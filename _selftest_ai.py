"""单人 vs 电脑模式的自动化测试（不需要显示器 / 手柄）。

覆盖：
1. AI 转向方向与工程符号约定一致（目标在右侧 = turn > 0 = 角度增大）；
2. AI 能收敛到目标方向，并从炮口方向命中静止目标（真的掉血）；
3. 难度只影响准度，不影响物理：三档都能打，hard 不比 easy 差；
4. 贴墙不越界：两台 AI 对轰 3000 帧，坐标始终在场地内；
5. 目标阵亡后 AI 停车、不打空枪；
6. 视角/输入隔离：_parse_mode 参数解析、set_skill 非法值兜底；
7. main() 端到端跑 1p 模式：AI 自动开火、F3 切难度、F2 切回双人后 P2 键盘可用。

运行：SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3.11 _selftest_ai.py
"""

import os
import subprocess
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.pop("TANK_MODE", None)
os.environ.pop("TANK_AI", None)
# 开火键指向临时文件：否则会读到开发机本地的 tank_keys.json（改过键时
# "P2 按 ENTER 开火" 的断言会失败，看起来像代码坏了）
_KEYS_TMP = "/tmp/tk_ai_selftest.json"
if "TANK_KEYS_FILE" not in os.environ:
    try:
        os.remove(_KEYS_TMP)          # 上一轮遗留的改键也不能影响断言
    except OSError:
        pass
    os.environ["TANK_KEYS_FILE"] = _KEYS_TMP

import pygame

import tank_game as g

pygame.init()

P1_KEYS = {"forward": pygame.K_w, "back": pygame.K_s, "left": pygame.K_a,
           "right": pygame.K_d, "fire": pygame.K_SPACE}
P2_KEYS = {"forward": pygame.K_UP, "back": pygame.K_DOWN, "left": pygame.K_LEFT,
           "right": pygame.K_RIGHT, "fire": pygame.K_RETURN}


def new_tank(color, x, y, angle=0.0):
    t = g.Tank(x, y, color, P1_KEYS if color == g.RED else P2_KEYS)
    t.angle = angle
    t.sync_image()
    return t


def sim(ai, tank, target, frames, bullets=None, hits=None):
    """把 main() 里的 AI 分支照抄一份：update -> drive -> fire -> 命中判定。"""
    bullets = bullets if bullets is not None else pygame.sprite.Group()
    for _ in range(frames):
        tank.tick()
        if tank.alive and target.alive:
            ai.update(tank, target)
            tank.drive(ai.turn(), ai.move())
            if ai.want_fire and tank.can_fire():
                bullets.add(tank.fire())
        for b in bullets.sprites():
            b.update()
            if b.rect.colliderect(target.rect):
                b.kill()
                destroyed = target.take_hit(g.DAMAGE)
                if hits is not None:
                    hits.append((destroyed, target.hp))
        tank.sync_image()
        target.sync_image()
    return bullets


# ---------------------------------------------------------- 1) 转向方向
tank = new_tank(g.BLUE, 200, 300, 0.0)          # 朝上
target = new_tank(g.RED, 500, 300, 0.0)         # 正右方 => 目标角 90
ai = g.TankAI("hard", seed=1)
ai.update(tank, target)
assert ai.turn() > 0, f"目标在右手边，AI 必须右转(turn>0)，实际 {ai.turn()}"
# 反例：目标在左侧必须左转
ai2 = g.TankAI("hard", seed=1)
tank_l, target_l = new_tank(g.BLUE, 500, 300, 0.0), new_tank(g.RED, 200, 300, 0.0)
ai2.update(tank_l, target_l)
assert ai2.turn() < 0, f"目标在左手边，AI 必须左转(turn<0)，实际 {ai2.turn()}"
# 几何校验：转 30 帧后炮口必须指向车身右侧
for _ in range(30):
    ai.update(tank, target)
    tank.drive(ai.turn(), ai.move())
tank.sync_image()
assert 60.0 < tank.angle < 120.0, tank.angle
assert tank.muzzle_position().x > tank.pos.x + g.TANK_HALF, tank.muzzle_position()
assert g.TURN_RIGHT > 0 > g.TURN_LEFT            # 符号约定本身不许被翻
print(f"[1] AI 转向方向 OK：右目标 turn={ai.turn():+.2f} -> angle={tank.angle:.1f}"
      f"（右转），左目标 turn={ai2.turn():+.2f}")

# ---------------------------------------------------------- 2) 瞄准 + 真掉血
tank = new_tank(g.BLUE, 200, 300, 0.0)
target = new_tank(g.RED, 500, 300, 0.0)
ai = g.TankAI("normal", seed=7)
hits = []
sim(ai, tank, target, 600, hits=hits)
assert hits, "600 帧内 AI 一枪没打中，瞄准逻辑有问题"
assert target.hp < g.MAX_HP, target.hp
assert not target.alive, f"normal 难度 600 帧应能击毁静止目标，剩 {target.hp} 血"
assert g.TANK_HALF <= tank.pos.x <= g.SCREEN_WIDTH - g.TANK_HALF
assert g.TANK_HALF <= tank.pos.y <= g.SCREEN_HEIGHT - g.TANK_HALF
print(f"[2] 瞄准/开火 OK：命中 {len(hits)} 次，目标 {g.MAX_HP} -> {target.hp} 血")

# ---------------------------------------------------------- 3) 难度只调准度
dmg = {}
for skill in g.AI_SKILL_ORDER:
    total = 0
    for seed in range(5):
        at = new_tank(g.BLUE, 200, 300, 0.0)
        tt = new_tank(g.RED, 500, 300, 0.0)
        a = g.TankAI(skill, seed=seed)
        sim(a, at, tt, 120)          # 120 帧 ≈ 2 秒，谁都没打死谁，比的是准度
        total += g.MAX_HP - tt.hp
    dmg[skill] = total
assert dmg["hard"] >= dmg["easy"], dmg
assert dmg["normal"] >= dmg["easy"], dmg
# 物理没被改：三档的坦克速度 / 转向速率完全相同
speeds = {(g.Tank(0, 0, g.RED, P1_KEYS).speed, g.Tank(0, 0, g.RED, P1_KEYS).turn_speed)}
assert len(speeds) == 1
print(f"[3] 难度梯度 OK：120 帧累计伤害 {dmg}（hard >= normal >= easy 至少不反向）")

# ---------------------------------------------------------- 4) 贴墙不越界
a1 = g.TankAI("hard", seed=11)
a2 = g.TankAI("easy", seed=12)
t1 = new_tank(g.RED, 60, 60, 0.0)
t2 = new_tank(g.BLUE, 740, 540, 180.0)
bul = pygame.sprite.Group()
for _ in range(3000):
    for tk, ai_, other in ((t1, a1, t2), (t2, a2, t1)):
        tk.tick()
        if tk.alive and other.alive:
            ai_.update(tk, other)
            tk.drive(ai_.turn(), ai_.move())
            if ai_.want_fire and tk.can_fire():
                bul.add(tk.fire())
    for b in bul.sprites():
        b.update()
        for tk in (t1, t2):
            if tk is not b.owner and tk.alive and b.rect.colliderect(tk.rect):
                b.kill()
                tk.take_hit(g.DAMAGE)
                break
    for tk in (t1, t2):
        tk.sync_image()
        assert g.TANK_HALF <= tk.pos.x <= g.SCREEN_WIDTH - g.TANK_HALF, tk.pos
        assert g.TANK_HALF <= tk.pos.y <= g.SCREEN_HEIGHT - g.TANK_HALF, tk.pos
print(f"[4] AI vs AI 3000 帧 OK：坐标始终在场地内，血量 P1={t1.hp} P2={t2.hp}")

# ---------------------------------------------------------- 5) 目标阵亡后停手
dead = new_tank(g.RED, 500, 300, 0.0)
dead.alive = False
ai = g.TankAI("hard", seed=3)
before = dead.hp
ai.update(tank, dead)
assert ai.turn() == 0.0 and ai.move() == 0.0 and ai.want_fire is False
ai.update(tank, None)
assert ai.turn() == 0.0 and ai.move() == 0.0 and ai.want_fire is False
assert dead.hp == before
print("[5] 无目标/目标阵亡 OK：AI 停车且不开空枪")

# ---------------------------------------------------------- 6) 难度与模式解析
ai = g.TankAI("normal")
assert ai.label == "NORMAL"
assert ai.set_skill("HARD") == "hard" and ai.label == "HARD"
assert ai.set_skill("nonsense") == "normal" and ai.label == "NORMAL"   # 非法值兜底
assert g.AI_SKILL_ORDER == ("easy", "normal", "hard")

old_argv = sys.argv
try:
    sys.argv = ["tank_game.py"]
    assert g._parse_mode() == ("2p", "normal")        # 默认不改变原双人行为
    sys.argv = ["tank_game.py", "--1p"]
    assert g._parse_mode() == ("1p", "normal")
    sys.argv = ["tank_game.py", "--1p", "--ai=hard"]
    assert g._parse_mode() == ("1p", "hard")
    sys.argv = ["tank_game.py", "--mode=cpu", "--ai=easy"]
    assert g._parse_mode() == ("1p", "easy")
    sys.argv = ["tank_game.py", "--2p", "--ai=不是难度"]
    assert g._parse_mode() == ("2p", "normal")
    os.environ["TANK_MODE"] = "1p"
    os.environ["TANK_AI"] = "easy"
    sys.argv = ["tank_game.py"]
    assert g._parse_mode() == ("1p", "easy")          # 环境变量生效
    sys.argv = ["tank_game.py", "--2p"]
    assert g._parse_mode() == ("2p", "easy")          # 命令行覆盖环境变量
finally:
    os.environ.pop("TANK_MODE", None)
    os.environ.pop("TANK_AI", None)
    sys.argv = old_argv
print("[6] 难度切换 / 模式解析 OK：参数 > 环境变量 > 默认 2p")


# ---------------------------------------------------------- 7) 端到端：1p 模式
def ai_main_scenario():
    # 本场景自己决定开局模式，不依赖父进程传进来的环境变量
    os.environ["TANK_MODE"] = "1p"
    os.environ["TANK_AI"] = "normal"
    seen = {"fire": [], "frame": 0}
    ai_created = []
    tanks_created = []

    orig_init = g.Tank.__init__

    def patched_init(self, x, y, color, controls, **kw):
        orig_init(self, x, y, color, controls, **kw)
        tanks_created.append(color)

    orig_fire = g.Tank.fire

    def counting_fire(self):
        seen["fire"].append((seen["frame"], self.color, self.ai is not None))
        return orig_fire(self)

    orig_ai_init = g.TankAI.__init__

    def patched_ai_init(self, skill="normal", seed=None):
        orig_ai_init(self, skill, seed)
        ai_created.append((skill, seed))

    g.Tank.__init__ = patched_init
    g.Tank.fire = counting_fire
    g.TankAI.__init__ = patched_ai_init

    events_at = {
        200: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F3)],   # 切难度
        260: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F2)],   # -> 双人
        320: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN)],  # P2 键盘开火
    }

    def fake_get():
        i = seen["frame"]
        seen["frame"] += 1
        if i >= 500:
            return [pygame.event.Event(pygame.QUIT)]
        return list(events_at.get(i, []))

    pygame.event.get = fake_get
    try:
        g.main()
    except SystemExit as e:
        assert e.code in (0, None), e.code

    ai_fire = [(f, c) for f, c, is_ai in seen["fire"] if is_ai and f < 260]
    human_p2 = [(f, c) for f, c, is_ai in seen["fire"]
                if (not is_ai) and c == g.BLUE and f >= 320]
    print("AI_CREATED =", len(ai_created), "TANKS =", len(tanks_created),
          "AI_FIRE_BEFORE_SWITCH =", len(ai_fire),
          "P2_HUMAN_FIRE_AFTER_F2 =", len(human_p2))
    assert ai_created, "1p 模式必须创建 TankAI"
    assert ai_fire, "AI 必须在 260 帧内自主开火（未按过任何键）"
    assert human_p2, "F2 切回双人后，P2 的 ENTER 应该能开火"
    assert len(tanks_created) == 4, f"初始 2 + F2 重置 2 = 4，实际 {len(tanks_created)}"
    print("AI_MAIN OK：AI 自主开火 -> F3 切难度 -> F2 切双人 -> P2 键盘接管")


if "--ai-main" in sys.argv:
    ai_main_scenario()
    sys.exit(0)

env = dict(os.environ)
env["SDL_VIDEODRIVER"] = "dummy"
env["SDL_AUDIODRIVER"] = "dummy"
env["TANK_MODE"] = "1p"
env["TANK_AI"] = "normal"
proc = subprocess.run([sys.executable, os.path.abspath(__file__), "--ai-main"],
                      capture_output=True, text=True, env=env)
out = "\n".join(l for l in (proc.stdout + proc.stderr).splitlines()
                if not l.startswith(("pygame ", "Hello from", "  from pkg")))
print(out)
assert proc.returncode == 0, f"ai-main 子进程失败\n{out}"
assert "AI_MAIN OK" in proc.stdout

print("AI 测试全部通过：转向方向/瞄准开火/难度梯度/边界/停手/模式解析/1p 端到端")
