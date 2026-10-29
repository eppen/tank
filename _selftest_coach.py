"""对战表现评估（coach）的测试：不需要显示器 / 手柄。

覆盖：
1. 几何与分档：aim_error / wrap_deg / 距离与偏差分档的边界；
2. 指标口径：开火时记距离与偏差、命中按「开枪那一刻」的分档归因（含护盾）；
3. 评分单调：同样的帧数下，打得准的那局分更高；空局不给分；
4. 纠正规则：按住不放 / 超距 / 打护盾 / 偏差大 / 浪费无敌 各自的触发与文案；
5. 误报防线：点按不算「按住不放」，等对方无敌不算「冷却空转」；
6. 历史：写入 / 读回 / 原子落盘 / 上限截断 / 坏文件容错 / 最佳与趋势；
7. 端到端：1p 模式跑 main()，AI 打掉人类 5 条命 → 终端出评估、历史落盘、
   F5 面板不崩。

运行：SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3.11 _selftest_coach.py
"""

import json
import os
import subprocess
import sys
import types

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.pop("TANK_MODE", None)
os.environ.pop("TANK_AI", None)
_COACH_TMP = "/tmp/tk_coach_selftest.json"
os.environ["TANK_COACH_FILE"] = _COACH_TMP
try:
    os.remove(_COACH_TMP)
except OSError:
    pass

import pygame

import tank_coach as c

# --------------------------------------------------------------- 假坦克
class FakeTank:
    """只有 coach 真正用到的属性：pos.x / pos.y / angle / alive / cooldown / invuln。"""

    def __init__(self, x, y, angle=0.0, cooldown=0, invuln=0, alive=True):
        self.pos = types.SimpleNamespace(x=float(x), y=float(y))
        self.angle = float(angle)
        self.cooldown = cooldown
        self.invuln = invuln
        self.alive = alive


class FakeBullet:
    """子弹只需要能被挂上 coach_band / coach_err_band 标签。"""


def human_at(x, y, angle):
    return FakeTank(x, y, angle)


def cpu_at(x, y):
    return FakeTank(x, y)


# --------------------------------------------------------------- 1) 几何与分档
assert c.wrap_deg(370) == 10 and c.wrap_deg(-190) == 170
assert abs(c.wrap_deg(180)) == 180, "±180 是同一个方向，只差符号"
h = human_at(100, 300, 90)            # 朝右
t = cpu_at(400, 300)                  # 正右方
assert abs(c.aim_error(h, t)) < 1e-9, c.aim_error(h, t)
h.angle = 60                          # 偏 30 度
assert abs(c.aim_error(h, t) - 30) < 1e-9, c.aim_error(h, t)
h.angle = 120
assert abs(c.aim_error(h, t) - 30) < 1e-9, c.aim_error(h, t)   # 取绝对值，不分左右
assert c._band_of(0, c.DISTANCE_BANDS) == "<120"
assert c._band_of(119.9, c.DISTANCE_BANDS) == "<120"
assert c._band_of(120, c.DISTANCE_BANDS) == "120-280"
assert c._band_of(560, c.DISTANCE_BANDS) == ">560"
assert c._band_of(4.99, c.ERROR_BANDS) == "<=5"
assert c._band_of(30, c.ERROR_BANDS) == ">20"
assert c.grade_of(90) == "S" and c.grade_of(60) == "B" and c.grade_of(0) == "D"
print("[1] 几何 / 分档 OK：aim_error 取绝对偏差，档位边界左闭右开")


# --------------------------------------------------------------- 2) 指标口径
coach = c.BattleCoach()
coach.begin_round("HARD")
human = human_at(200, 300, 90)
cpu = cpu_at(500, 300)                # 距离 300 => 中距档
bullet = FakeBullet()
dist, err = coach.on_shot(human, cpu, bullet)
assert abs(dist - 300) < 1e-6 and err < 1e-6
assert bullet.coach_band == "280-420" and bullet.coach_err_band == "<=5"
coach.on_hit(by_human=True, shield=False, bullet=bullet)
m = coach.metrics()
assert m["shots"] == 1 and m["hits"] == 1 and m["accuracy"] == 1.0
assert coach.band_hits["280-420"] == 1 and coach.err_hits["<=5"] == 1

# 命中要是打在护盾上：不算命中、单独计数（开火时它就在无敌里）
cpu.invuln = 30
shield_bullet = FakeBullet()
coach.on_shot(human, cpu, shield_bullet)
coach.on_hit(by_human=True, shield=True, bullet=shield_bullet)
m = coach.metrics()
assert m["hits"] == 1 and m["shield_hits"] == 1
assert m["shield_shot_ratio"] == 0.5, "2 发里有 1 发是冲着无敌状态打的"
assert coach.shots_own_invuln == 0, "人类自己没无敌，不该记成白送"
assert coach.band_hits["280-420"] == 1, "护盾命中不该算进分档命中"

# 电脑打中你 = hits_taken，不影响你的命中率
coach.on_hit(by_human=False)
assert coach.metrics()["hits_taken"] == 1

# 死亡：自己的命计入 deaths，电脑计入 kills；复活窗口计次数
coach.on_death(victim_is_human=True)
coach.on_death(victim_is_human=False)
m = coach.metrics()
assert m["deaths"] == 1 and m["kills"] == 1
assert len(m["lifetimes"]) == 1, "被击毁一次应该记一条存活时长"
print("[2] 指标口径 OK：分档归因 / 护盾不算命中 / 命中与被命中分开计")


# --------------------------------------------------------------- 3) 逐帧观察
def player_round(coach, frames=180, hold=False, fire_every=18,
                 distance=350.0, angle_err=0.0, cpu_invuln=0, move=True):
    """跑一个假回合：人类在固定距离上按节奏开火，返回 coach。

    hold=True 模拟「按住不放」：只在第一帧产生一次上升沿，之后再不出枪。
    """
    cpu = cpu_at(120 + distance, 300)
    human = human_at(120, 300, 90 - angle_err)
    cpu.invuln = cpu_invuln
    fired = 0
    for frame in range(frames):
        if human.cooldown > 0:
            human.cooldown -= 1
        if cpu.invuln > 0:
            cpu.invuln -= 1
        fired_edge = False
        if human.cooldown <= 0 and (frame == 0 or (not hold and frame % fire_every == 0)):
            bullet = FakeBullet()
            coach.on_shot(human, cpu, bullet)
            human.cooldown = 18
            fired_edge = True
            fired += 1
            # 命中判定：偏差 0 且距离在射程内就算打中（护盾期不算命中）
            if angle_err == 0 and distance <= 560:
                coach.on_hit(by_human=True, shield=cpu.invuln > 0, bullet=bullet)
        if move:
            human.angle = (90 - angle_err) + (2 if frame % 2 else -2)   # 轻微摇摆
            human.pos.x = 120 + (2 if frame % 4 < 2 else -2)            # 前后挪动
        coach.observe(human, cpu, fire_held=(hold or fired_edge))
    return coach, fired


good = c.BattleCoach()
good.begin_round("NORMAL")
good, fired = player_round(good)
assert fired == 10, fired
gm = good.metrics()
assert gm["shots"] == 10 and gm["hits"] == 10 and gm["accuracy"] == 1.0
assert gm["far_shots"] == 0 and gm["held_blocked"] == 0
g_parts, g_total = good.scores()
assert g_total >= 80, f"完美打法应该高分，实际 {g_total:.1f}"

bad = c.BattleCoach()
bad.begin_round("NORMAL")
bad, fired_bad = player_round(bad, hold=True)
assert fired_bad == 1, fired_bad
b_parts, b_total = bad.scores()
assert b_total < g_total - 15, f"按住不放必须明显低于完美打法：{b_total:.1f} vs {g_total:.1f}"

empty = c.BattleCoach()
e_parts, e_total = empty.scores()
assert e_total == 0 and all(v == 0 for v in e_parts.values()), e_parts
print(f"[3] 逐帧观察 OK：完美 {g_total:.0f} 分 vs 按住不放 {b_total:.0f} 分 vs 空局 0 分")


# 观察到「死了就不计帧」
coach = c.BattleCoach()
coach.begin_round()
h2, c2 = human_at(100, 300, 0), cpu_at(200, 300)
coach.observe(h2, c2, False)
assert coach.frames == 1
h2.alive = False
coach.observe(h2, c2, False)
assert coach.frames == 1, "人类阵亡的帧不该计入统计"


# --------------------------------------------------------------- 4) 纠正规则
# 4a 按住不放
codes = [a["code"] for a in bad.advices()]
assert "held" in codes, codes
held_advice = [a for a in bad.advices() if a["code"] == "held"][0]
assert held_advice["severity"] >= 90 and "上升沿" in held_advice["fix"]
assert bad.metrics()["held_blocked_ratio"] > 0.5

# 4b 超距开火
far = c.BattleCoach()
far.begin_round()
far, _ = player_round(far, frames=180, distance=700.0, fire_every=18)
assert far.metrics()["far_shots"] >= 5, far.metrics()["far_shots"]
assert "far" in [a["code"] for a in far.advices()]

# 4c 打无敌护盾
shield = c.BattleCoach()
shield.begin_round()
shield, _ = player_round(shield, frames=120, cpu_invuln=10 ** 6)
assert shield.metrics()["shield_hits"] >= 5, shield.metrics()["shield_hits"]
assert "shield" in [a["code"] for a in shield.advices()]

# 4d 炮口没对准（偏差 25°，且不命中）
sloppy = c.BattleCoach()
sloppy.begin_round()
sloppy, _ = player_round(sloppy, frames=180, angle_err=25.0, move=False)
sm = sloppy.metrics()
assert sm["avg_err"] > 20, sm["avg_err"]
assert "aim" in [a["code"] for a in sloppy.advices()]

# 4e 浪费自己的无敌：复活过但无敌期间一发没打
waste = c.BattleCoach()
waste.begin_round()
hh, cc = human_at(120, 300, 90), cpu_at(470, 300)
hh.invuln = 0
waste.observe(hh, cc, False)          # 先来一帧基线
hh.invuln = 60                        # 模拟复活
for _ in range(10):
    waste.observe(hh, cc, False)
assert waste.metrics()["own_invuln_windows"] == 1
assert "own_invuln" in [a["code"] for a in waste.advices()]

# 4f 干净的一局没有纠正项
assert good.advices() == [], [a["code"] for a in good.advices()]
assert good.positives(), "打得好的地方不该是空的"
print("[4] 纠正规则 OK：按住不放 / 超距 / 打护盾 / 偏差大 / 浪费无敌 全部命中")


# --------------------------------------------------------------- 5) 即时提示去重
coach = c.BattleCoach()
coach.begin_round()
coach.held_frames = 200
coach.held_blocked = 150
first = coach.new_hint()
assert first and first[0] == "held", first
assert coach.new_hint() is None, "同一条纠正不该重复提示"
print("[5] 即时提示 OK：同一纠正一局只提示一次，且文案带「怎么改」")


# --------------------------------------------------- 5b) 误报防线（两条容易错判的）
# 点按（隔帧敲）不是「按住不放」：连续按住才计数，否则认真瞄准的人会被冤枉
tapper = c.BattleCoach()
tapper.begin_round()
h_, c_ = human_at(120, 300, 90), cpu_at(470, 300)
for f in range(180):
    h_.cooldown = max(0, h_.cooldown - 1)
    held_ = (f % 2 == 0)                       # 隔帧点按
    if held_ and h_.cooldown == 0:
        tapper.on_shot(h_, c_, FakeBullet())
        h_.cooldown = c.FIRE_COOLDOWN if hasattr(c, "FIRE_COOLDOWN") else 18
    tapper.observe(h_, c_, held_)
assert tapper.held_frames > 60, tapper.held_frames
assert tapper.held_blocked == 0, f"点按不该算「按住不放」，实际 {tapper.held_blocked}"
assert "held" not in [a["code"] for a in tapper.advices()]

# 对方无敌时不开火是正确纪律，不该算成「冷却空转」
waiting = c.BattleCoach()
waiting.begin_round()
h_, c_ = human_at(120, 300, 90), cpu_at(470, 300)
c_.invuln = 10 ** 6
for _ in range(120):
    waiting.observe(h_, c_, False)
assert waiting.idle_ready_frames == 0, "对方在无敌里，等它是纪律不是空转"
c_.invuln = 0
for _ in range(120):
    waiting.observe(h_, c_, False)
assert waiting.idle_ready_frames == 120, "对方没无敌还不打，才算空转"
print("[5b] 误报防线 OK：点按不冤枉成「按住」，等对方无敌不冤枉成「空转」")


# --------------------------------------------------------------- 6) 历史
hist = {"version": 1, "rounds": []}
for i, acc in enumerate((0.2, 0.45, 0.55)):
    ok, path = c.record_round(hist, {"skill": "NORMAL", "score": 40 + i * 10,
                                     "grade": "C", "accuracy": acc, "hits": i,
                                     "shots": 5, "kills": 0, "deaths": 1,
                                     "duration": 30.0},
                              path=_COACH_TMP)
    assert ok, path
assert os.path.exists(_COACH_TMP)
reloaded = c.load_history(_COACH_TMP)
assert len(reloaded["rounds"]) == 3
best = c.best_row(reloaded)
assert best["score"] == 60 and best["index"] == 3, best
trend = c.history_trend_text(reloaded)
assert "第 3 局" in trend and "20% → 45% → 55%" in trend, trend
table = c.history_text(reloaded)
assert "共 3 局" in table and "55%" in table

# 上限截断：写到 25 局只保留最近 20 局
hist = {"version": 1, "rounds": []}
for i in range(c.MAX_HISTORY + 5):
    c.record_round(hist, {"score": i, "accuracy": 0.5}, path=_COACH_TMP)
assert len(hist["rounds"]) == c.MAX_HISTORY, len(hist["rounds"])
assert c.load_history(_COACH_TMP)["rounds"][-1]["score"] == c.MAX_HISTORY + 4

# 坏文件 / 非 dict / 缺字段：一律回退空历史，不抛异常
with open(_COACH_TMP, "w", encoding="utf-8") as f:
    f.write("{ 这不是 json")
assert c.load_history(_COACH_TMP)["rounds"] == []
with open(_COACH_TMP, "w", encoding="utf-8") as f:
    json.dump([1, 2, 3], f)
assert c.load_history(_COACH_TMP)["rounds"] == []
assert c.history_text({"rounds": []}).startswith("还没有历史记录")
assert c.best_row({"rounds": []}) is None
assert c.history_trend_text({"rounds": []}) == ""
print("[6] 历史 OK：写入/读回/上限 20 局/坏文件回退/最佳与趋势文案")


# --------------------------------------------------------------- 7) 端到端
def coach_main_scenario():
    """1p 模式下人类不操作：AI 打光它的 5 条命，局末自动出评估。"""
    os.environ["TANK_MODE"] = "1p"
    os.environ["TANK_AI"] = "hard"
    os.environ["TANK_COACH_FILE"] = _COACH_TMP
    seen = {"frame": 0}
    events_at = {30: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F5)],
                 60: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F5)]}

    def fake_get():
        i = seen["frame"]
        seen["frame"] += 1
        if i >= 1500:
            return [pygame.event.Event(pygame.QUIT)]
        return list(events_at.get(i, []))

    pygame.event.get = fake_get
    # 不真的等 60fps：这里只关心帧数，Sleep 会让测试跑上几分钟
    class _FastClock:
        def tick(self, fps=60):
            return 0

    pygame.time.Clock = _FastClock
    try:
        g.main()
    except SystemExit as e:
        assert e.code in (0, None), e.code
    print("FRAMES =", seen["frame"])


if "--coach-main" in sys.argv:
    import tank_game as g
    coach_main_scenario()
    sys.exit(0)

env = dict(os.environ)
env["SDL_VIDEODRIVER"] = "dummy"
env["SDL_AUDIODRIVER"] = "dummy"
env["TANK_MODE"] = "1p"
env["TANK_AI"] = "hard"
env["TANK_COACH_FILE"] = _COACH_TMP
try:
    os.remove(_COACH_TMP)
except OSError:
    pass
proc = subprocess.run([sys.executable, os.path.abspath(__file__), "--coach-main"],
                      capture_output=True, text=True, env=env)
out = "\n".join(l for l in (proc.stdout + proc.stderr).splitlines()
                if not l.startswith(("pygame ", "Hello from", "  from pkg")))
print(out)
assert proc.returncode == 0, f"coach-main 子进程失败\n{out}"
assert "本局评估" in proc.stdout, "局末必须打印评估报告"
assert "综合 " in proc.stdout and "下一局目标" in proc.stdout
assert "[coach] 实时面板 开" in proc.stdout, "F5 应该能开实时面板"
assert "历史写入失败" not in proc.stdout

rows = c.load_history(_COACH_TMP)["rounds"]
assert len(rows) == 1, rows
row = rows[0]
assert row["deaths"] == 5, f"人类 5 条命应该全丢，实际 {row}"
assert row["won"] is False and row["skill"] == "HARD"
assert 0 <= row["score"] <= 100
print("[7] 端到端 OK：1p 局末出评估、5 次阵亡入账、F5 面板、历史落盘")
print("评估测试全部通过：分档/指标/评分/纠正/误报防线/提示去重/历史/1p 端到端")
