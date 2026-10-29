"""对战表现评估（coach）：把「人类 vs 电脑」的一局量化成可纠正的指标。

设计取舍：
- **纯计算，不 import pygame**：几何只用 pos.x / pos.y / angle 三个属性，因此
  自测里可以用假坦克（哑对象）跑，不需要显示器。
- **阈值从 tank_game 传进来**（射程 560、冷却 18 帧、无敌 60 帧……），这个文件
  里不再写一份会漂移的魔法数字；建议文案里出现的数字因此永远和物理规则一致。
- **终端与屏幕共用同一份文案**（report_text / report_lines / hud_lines），不会
  出现「日志说命中率 41%，屏幕上写 38%」这种事。

用法（main() 里）：
    coach = BattleCoach(fire_range=AI_FIRE_RANGE, ...)
    coach.begin_round("HARD")
    ...每帧 coach.observe(human, cpu, fire_held)
    ...人类开火 coach.on_shot(human, cpu, bullet)
    ...子弹命中 coach.on_hit(by_human=True, shield=cpu.invuln > 0, bullet=bullet)
    ...有人被击毁 coach.on_death(victim_is_human=True)
    局末 print(coach.report_text(hist)) / record_round(hist, coach.history_row())
"""

import json
import math
import os

# --- 分档：分析「在什么距离 / 什么准度下开火」的命中率 ---
DISTANCE_BANDS = (
    (0.0, 120.0, "<120"),
    (120.0, 280.0, "120-280"),
    (280.0, 420.0, "280-420"),
    (420.0, 560.0, "420-560"),
    (560.0, float("inf"), ">560"),
)
ERROR_BANDS = (
    (0.0, 5.0, "<=5"),
    (5.0, 10.0, "5-10"),
    (10.0, 20.0, "10-20"),
    (20.0, 181.0, ">20"),
)

# 基准线：用来把本局换算成 0-100 的分项分数（达到基准 = 100 分）
BENCH_ACCURACY = 0.55        # 命中率 55% 以上算满分
BENCH_HITS_PER_SEC = 0.45    # 有效命中 0.45 发/秒 以上算满分（约 11 秒击毁一次）
BENCH_ENEMY_ACC = 0.35       # 电脑命中率低于 35% 不扣生存分

GRADE_STEPS = ((85, "S"), (75, "A"), (60, "B"), (45, "C"), (0, "D"))

COACH_HISTORY_PATH = os.environ.get("TANK_COACH_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tank_coach.json")
MAX_HISTORY = 20


def _clamp01(v):
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def wrap_deg(angle):
    """把角度折到 (-180, 180]。"""
    return (angle + 180.0) % 360.0 - 180.0


def aim_error(human, cpu):
    """人类炮口方向与实际目标方向的夹角绝对值（0..180）。"""
    dx = cpu.pos.x - human.pos.x
    dy = cpu.pos.y - human.pos.y
    desired = math.degrees(math.atan2(dx, -dy))
    return abs(wrap_deg(desired - human.angle))


def grade_of(total):
    for threshold, letter in GRADE_STEPS:
        if total >= threshold:
            return letter
    return "D"


def _band_of(value, bands):
    for lo, hi, label in bands:
        if lo <= value < hi:
            return label
    return bands[-1][2]


class BattleCoach:
    """一局对战的表现记录、评分、纠正建议。

    observe() 每帧调一次（人类活着、对决进行中），on_* 是事件钩子。
    所有计数器都以「帧」为单位累计，报告时才换算成秒/百分比。
    """

    def __init__(self, fps=60, fire_range=560.0, fire_cooldown=18,
                 invuln_frames=60, max_lives=5, close_range=120.0,
                 aim_tol=5.0, reaction_frames=12,
                 distance_bands=DISTANCE_BANDS, error_bands=ERROR_BANDS):
        self.fps = float(fps)
        self.fire_range = float(fire_range)
        self.fire_cooldown = int(fire_cooldown)
        self.invuln_frames = int(invuln_frames)
        self.max_lives = int(max_lives)
        self.close_range = float(close_range)
        self.aim_tol = float(aim_tol)
        self.reaction_frames = int(reaction_frames)
        self.distance_bands = tuple(distance_bands)
        self.error_bands = tuple(error_bands)
        self.begin_round("NORMAL")

    # ------------------------------------------------------------------ 生命周期
    def begin_round(self, skill_label="NORMAL"):
        """开新一局：所有计数清零（F2 切模式 / R 重开都会走到这里）。"""
        self.skill_label = str(skill_label)
        self.step = 0

        # 开火与命中
        self.shots = 0
        self.hits = 0
        self.shield_hits = 0          # 命中但对方处于复活无敌（不掉血）
        self.kills = 0                # 打爆电脑的次数
        self.errs = []                # 每次开火时的角度误差（度）
        self.dists = []               # 每次开火时的距离（px）
        self.shots_far = 0            # 超过有效射程的射击
        self.shots_close = 0          # 贴身射击
        self.shots_own_invuln = 0     # 自己无敌期间的射击（白送）
        self.shots_enemy_invuln = 0   # 对方无敌期间的射击（无效）
        self.band_shots = {b[2]: 0 for b in self.distance_bands}
        self.band_hits = {b[2]: 0 for b in self.distance_bands}
        self.err_shots = {b[2]: 0 for b in self.error_bands}
        self.err_hits = {b[2]: 0 for b in self.error_bands}

        # 时间 / 站位 / 位移
        self.frames = 0               # 观察到的有效帧（双方都活着）
        self.stationary_frames = 0
        self.in_range_frames = 0      # 电脑在射程内的帧
        self.band_frames = {b[2]: 0 for b in self.distance_bands}
        self.turn_flips = 0

        # 开火键使用（上升沿误用）
        self.held_frames = 0
        self.held_blocked = 0         # 连按 + 冷却已好 + 却没出枪的帧
        self._held_streak = 0         # 当前连续按住多少帧（区分「按住」与「点按」）
        self.idle_ready_frames = 0    # 对准 + 在射程内 + 冷却好了却没开火

        # 生存
        self.hits_taken = 0
        self.deaths = 0
        self.enemy_shots = 0
        self.own_invuln_windows = 0
        self.lifetimes = []           # 每条命活了多久（秒）
        self.life_start = 0

        # 反应：电脑开火后是否换向 / 移动
        self.dodge_reacted = 0
        self._enemy_shot_step = None
        self._dodge_counted = True

        # 内部游标
        self._pending_shot = False
        self._pending_enemy_shot = False
        self._prev_pos = None
        self._prev_angle = None
        self._prev_invuln = 0
        self._last_sign = 0
        self._last_dist = 0.0
        self._last_err = 0.0
        self._hinted = set()

    # ------------------------------------------------------------------ 每帧
    def observe(self, human, cpu, fire_held=False):
        """采样一帧：距离、角度、位移、开火键状态。人类/电脑不齐时直接跳过。"""
        if human is None or cpu is None:
            return
        if not getattr(human, "alive", False) or not getattr(cpu, "alive", False):
            return
        self.step += 1
        self.frames += 1

        dist = math.hypot(cpu.pos.x - human.pos.x, cpu.pos.y - human.pos.y)
        err = aim_error(human, cpu)
        self._last_dist, self._last_err = dist, err
        band = _band_of(dist, self.distance_bands)
        self.band_frames[band] += 1
        if dist <= self.fire_range:
            self.in_range_frames += 1

        fired = self._pending_shot
        self._pending_shot = False

        # 位移 / 转向：用于「站着不动」和「转向抖动」
        if self._prev_pos is not None:
            moved = math.hypot(human.pos.x - self._prev_pos[0],
                               human.pos.y - self._prev_pos[1])
            if moved < 0.5:
                self.stationary_frames += 1
        self._prev_pos = (human.pos.x, human.pos.y)

        turned = 0.0
        if self._prev_angle is not None:
            delta = wrap_deg(human.angle - self._prev_angle)
            turned = abs(delta)
            if abs(delta) > 0.3:
                sign = 1 if delta > 0 else -1
                if self._last_sign and sign != self._last_sign:
                    self.turn_flips += 1
                self._last_sign = sign
        self._prev_angle = human.angle

        # 自己的无敌窗口（复活瞬间）：数次数，用来判断有没有白用
        invuln = int(getattr(human, "invuln", 0) or 0)
        if invuln > 0 and self._prev_invuln <= 0:
            self.own_invuln_windows += 1
        self._prev_invuln = invuln

        # 开火键：连续按住（第 2 帧起）& 冷却已好 & 这帧没出枪 = 被上升沿吃掉的一发
        # 只按 1 帧的「点按」不算：那是正常敲，只是这一帧恰好没到开火条件。
        if fire_held:
            self._held_streak += 1
            self.held_frames += 1
            if (self._held_streak > 1 and not fired
                    and int(getattr(human, "cooldown", 0) or 0) <= 0):
                self.held_blocked += 1
        else:
            self._held_streak = 0

        # 冷却空转：对准（5° 以内，和「转到 5° 内再打」的建议同口径）、在射程内、
        # 冷却好了、而且对方不是在无敌里，却没打
        # （它在无敌里时不开火是正确纪律，不能算空转）
        if (not fired and int(getattr(human, "cooldown", 0) or 0) <= 0
                and dist <= self.fire_range and err <= self.aim_tol
                and int(getattr(cpu, "invuln", 0) or 0) <= 0):
            self.idle_ready_frames += 1

        # 电脑刚开过枪：12 帧内换向就算「有反应」（它开火后要等 18 帧冷却）
        if self._pending_enemy_shot:
            self._pending_enemy_shot = False
            self._enemy_shot_step = self.step
            self._dodge_counted = False
        elif self._enemy_shot_step is not None and not self._dodge_counted \
                and self.step - self._enemy_shot_step <= self.reaction_frames:
            if turned > 1.0:
                self.dodge_reacted += 1
                self._dodge_counted = True

    # ------------------------------------------------------------------ 事件
    def on_shot(self, human, cpu, bullet=None):
        """人类开了一枪：记录当时的距离与准度，并把分档标签挂在子弹上。"""
        dist = math.hypot(cpu.pos.x - human.pos.x, cpu.pos.y - human.pos.y)
        err = aim_error(human, cpu)
        self.shots += 1
        self.errs.append(err)
        self.dists.append(dist)
        band = _band_of(dist, self.distance_bands)
        eband = _band_of(err, self.error_bands)
        self.band_shots[band] += 1
        self.err_shots[eband] += 1
        if dist > self.fire_range:
            self.shots_far += 1
        if dist < self.close_range:
            self.shots_close += 1
        if int(getattr(human, "invuln", 0) or 0) > 0:
            self.shots_own_invuln += 1
        if int(getattr(cpu, "invuln", 0) or 0) > 0:
            self.shots_enemy_invuln += 1
        self._pending_shot = True
        if bullet is not None:
            # 命中时靠这两个标签把结果归到「开枪那一刻」的分档里
            bullet.coach_band = band
            bullet.coach_err_band = eband
        return (dist, err)

    def on_enemy_shot(self):
        """电脑开了一枪（用于统计它对你的命中率、以及你有没有做出反应）。"""
        self.enemy_shots += 1
        self._pending_enemy_shot = True

    def on_hit(self, by_human, shield=False, bullet=None):
        """子弹命中。shield=True 表示对方在复活无敌里，不掉血。"""
        if not by_human:
            self.hits_taken += 1
            return
        band = getattr(bullet, "coach_band", None)
        eband = getattr(bullet, "coach_err_band", None)
        if shield:
            self.shield_hits += 1
            return
        self.hits += 1
        if band in self.band_hits:
            self.band_hits[band] += 1
        if eband in self.err_hits:
            self.err_hits[eband] += 1

    def on_death(self, victim_is_human):
        """有人被击毁（血条打空，扣一条命）。"""
        if victim_is_human:
            self.deaths += 1
            self.lifetimes.append((self.frames - self.life_start) / self.fps)
            self.life_start = self.frames
        else:
            self.kills += 1

    # ------------------------------------------------------------------ 指标
    def metrics(self):
        """把计数器换算成人能读的数字。空数据一律给 0，不做除法。"""
        frames = max(1, self.frames)
        dur = max(1e-6, self.frames / self.fps)
        shots = self.shots
        held = max(1, self.held_frames)
        return {
            "skill": self.skill_label,
            "frames": self.frames,
            "duration": dur,
            "shots": shots,
            "hits": self.hits,
            "accuracy": self.hits / shots if shots else 0.0,
            "shield_hits": self.shield_hits,
            "shield_shot_ratio": (self.shots_enemy_invuln / shots) if shots else 0.0,
            "far_shots": self.shots_far,
            "far_shot_ratio": (self.shots_far / shots) if shots else 0.0,
            "close_shots": self.shots_close,
            "avg_err": (sum(self.errs) / len(self.errs)) if self.errs else 0.0,
            "avg_dist": (sum(self.dists) / len(self.dists)) if self.dists else 0.0,
            "held_frames": self.held_frames,
            "held_blocked": self.held_blocked,
            "held_blocked_ratio": self.held_blocked / held,
            "idle_ready_frames": self.idle_ready_frames,
            "idle_lost_shots": self.idle_ready_frames // max(1, self.fire_cooldown),
            "hits_per_sec": self.hits / dur,
            "hits_taken": self.hits_taken,
            "deaths": self.deaths,
            "kills": self.kills,
            "enemy_shots": self.enemy_shots,
            "enemy_accuracy": (self.hits_taken / self.enemy_shots
                               if self.enemy_shots else 0.0),
            "stationary_ratio": self.stationary_frames / frames,
            "in_range_ratio": self.in_range_frames / frames,
            "turn_flips": self.turn_flips,
            "dodge_ratio": (self.dodge_reacted / self.enemy_shots
                            if self.enemy_shots else 0.0),
            "own_invuln_windows": self.own_invuln_windows,
            "shots_own_invuln": self.shots_own_invuln,
            "lifetimes": list(self.lifetimes),
        }

    def scores(self):
        """五个分项 + 加权总分（0-100）。权重：准度 .30 输出 .25 纪律 .20 生存 .15 走位 .10。"""
        m = self.metrics()
        if self.frames <= 0:          # 还没打（或时间被跳过）：不给分，避免"0 发=45 分"
            zero = {k: 0.0 for k in ("accuracy", "output", "discipline",
                                     "survival", "movement")}
            return zero, 0.0
        acc_s = 100.0 * _clamp01(m["accuracy"] / BENCH_ACCURACY)
        if m["shots"] < 5:
            # 样本太少时按比例打折：只打 1 发就中（100%）不该拿满准度分
            acc_s *= m["shots"] / 5.0
        out_s = 100.0 * _clamp01(m["hits_per_sec"] / BENCH_HITS_PER_SEC)

        disc = 100.0
        disc -= min(30.0, 300.0 * m["far_shot_ratio"])
        disc -= min(25.0, 250.0 * m["shield_shot_ratio"])
        disc -= min(20.0, 400.0 * m["held_blocked_ratio"])
        disc -= min(20.0, 2.0 * m["idle_lost_shots"])
        disc = max(0.0, disc)

        surv = 100.0
        surv -= min(70.0, 14.0 * m["deaths"])
        surv -= min(30.0, max(0.0, m["enemy_accuracy"] - BENCH_ENEMY_ACC) * 120.0)
        surv = max(0.0, surv)

        move_s = 100.0
        move_s -= max(0.0, m["stationary_ratio"] - 0.40) * 120.0
        move_s -= min(15.0, m["turn_flips"] / max(1.0, m["duration"]) * 6.0)
        move_s = max(0.0, move_s)

        parts = {"accuracy": acc_s, "output": out_s, "discipline": disc,
                 "survival": surv, "movement": move_s}
        total = (0.30 * acc_s + 0.25 * out_s + 0.20 * disc
                 + 0.15 * surv + 0.10 * move_s)
        return parts, total

    def grade(self):
        return grade_of(self.scores()[1])

    # ------------------------------------------------------------------ 纠正
    def advices(self):
        """按严重程度排好序的纠正项：每条都带「证据（本局数字）+ 怎么改」。"""
        m = self.metrics()
        out = []

        if m["held_blocked"] >= 12 and m["held_blocked_ratio"] >= 0.15:
            out.append({
                "code": "held", "severity": 92,
                "title": "开火键按着不放",
                "evidence": f"按住开火键的 {m['held_frames']} 帧里，"
                            f"{m['held_blocked']} 帧冷却已好却没出枪。",
                "fix": "开火是上升沿：按住只打一枪。改成每 0.3 秒敲一下，"
                       "冷却期间松开再按。",
            })

        if m["far_shots"] >= 2 and m["far_shot_ratio"] >= 0.1:
            out.append({
                "code": "far", "severity": 85,
                "title": "超距开火，白送冷却",
                "evidence": f"{m['far_shots']} 发（{m['far_shot_ratio']*100:.0f}%）是在 "
                            f"{self.fire_range:.0f}px 外打的，子弹先飞出屏幕。",
                "fix": f"压到 {self.fire_range:.0f}px 以内再开火；太远时先接近，"
                       "把冷却留给能打中的距离。",
            })

        if m["shield_hits"] >= 1 and m["shield_shot_ratio"] >= 0.08:
            out.append({
                "code": "shield", "severity": 78,
                "title": "打在电脑的无敌护盾上",
                "evidence": f"{m['shield_hits']} 发打在它复活后的 "
                            f"{self.invuln_frames / self.fps:.0f} 秒无敌里，一点血都没掉。",
                "fix": "电脑刚复活时停火，等白环消失；这 1 秒用来拉距离或换角度。",
            })

        if m["avg_err"] >= 8.0 and m["shots"] >= 3:
            worst = self._worst_err_band()
            detail = f"，其中 {worst} 的 {self.err_shots[worst]} 发只中了 " \
                     f"{self.err_hits[worst]} 发" if worst else ""
            out.append({
                "code": "aim", "severity": min(88.0, 60.0 + (m["avg_err"] - 8.0) * 2.0),
                "title": "炮口没对准就开火",
                "evidence": f"开火时平均偏差 {m['avg_err']:.1f}°{detail}。",
                "fix": "转到 5° 内再打：满舵 4°/帧，5° 只要 0.02 秒，"
                       "比浪费 18 帧冷却划算得多。",
            })

        if m["own_invuln_windows"] >= 1 and m["shots_own_invuln"] == 0:
            out.append({
                "code": "own_invuln", "severity": 62,
                "title": "浪费了自己的无敌 1 秒",
                "evidence": f"你复活了 {m['own_invuln_windows']} 次，无敌期间一发都没打。",
                "fix": f"复活瞬间就朝电脑开火：{self.invuln_frames} 帧够打 "
                       f"{self.invuln_frames // self.fire_cooldown + 1} 发"
                       f"（第 0/18/36/54 帧各一发）。",
            })

        if m["idle_lost_shots"] >= 3:
            out.append({
                "code": "idle", "severity": 68,
                "title": "冷却空转",
                "evidence": f"有 {m['idle_ready_frames']} 帧处在「对准 + 射程内 + "
                            f"冷却已好」却没开火，约等于少打 {m['idle_lost_shots']} 发。",
                "fix": "到位就打。冷却只有 0.3 秒，空着就是白丢伤害。",
            })

        if m["stationary_ratio"] >= 0.6 and m["in_range_ratio"] >= 0.3:
            out.append({
                "code": "static", "severity": 55,
                "title": "几乎站着不动",
                "evidence": f"{m['stationary_ratio']*100:.0f}% 的时间原地不动"
                            f"（有 {m['in_range_ratio']*100:.0f}% 的时间在它的射程内）。",
                "fix": "持续小幅换向/前后移动，破坏它算的提前量；"
                       "静止的目标 hard 命中率接近 100%。",
            })

        if m["deaths"] >= 3:
            out.append({
                "code": "deaths", "severity": 60,
                "title": "被击毁太频繁",
                "evidence": f"本局被击毁 {m['deaths']} 次，平均一条命只撑了"
                            f"{m['duration'] / max(1, m['deaths']):.1f} 秒。",
                "fix": "把距离维持在 280–420px：这个区间它的命中率最低，"
                       "你的命中率还能保住。",
            })

        if m["shots"] >= 5 and m["accuracy"] < 0.35:
            out.append({
                "code": "acc", "severity": 70,
                "title": "命中率偏低",
                "evidence": f"{m['shots']} 发只中了 {m['hits']} 发"
                            f"（{m['accuracy']*100:.0f}%）。",
                "fix": "宁可少打几发：等准星收进 5° 再开火，命中率会明显上去。",
            })

        out.sort(key=lambda a: -a["severity"])
        return out

    def positives(self):
        """做得好的地方（评估不只挑错）。"""
        m = self.metrics()
        good = []
        if m["shots"] >= 3 and m["accuracy"] >= 0.5:
            good.append(f"命中率 {m['accuracy']*100:.0f}%（{m['hits']}/{m['shots']}）")
        if m["far_shots"] == 0 and m["shots"] >= 3:
            good.append("没有一发超距浪费")
        if m["shield_hits"] == 0 and m["shots"] >= 3:
            good.append("没打电脑的无敌护盾")
        if m["deaths"] == 0 and m["kills"] >= 1:
            good.append("一条命没丢就击毁它")
        if m["held_blocked"] == 0 and m["shots"] >= 3:
            good.append("开火节奏干净（没有按住空转）")
        if m["dodge_ratio"] >= 0.5 and m["enemy_shots"] >= 3:
            good.append(f"它开火后 {m['dodge_ratio']*100:.0f}% 的情况你都换了向")
        if m["kills"] >= 2:
            good.append(f"击毁 {m['kills']} 次")
        return good

    def drills(self):
        """下一局的可量化目标：取最弱的两项，给具体数字。"""
        m = self.metrics()
        parts, _ = self.scores()
        targets = {
            "accuracy": (
                f"命中率 {m['accuracy']*100:.0f}% → {BENCH_ACCURACY*100:.0f}%",
                "只在准星收进 5° 内开火"),
            "output": (
                f"有效输出 {m['hits_per_sec']:.2f} → {BENCH_HITS_PER_SEC:.2f} 命中/秒",
                "冷却一好就打，一下一下敲"),
            "discipline": (
                "废弹（超距 / 打护盾 / 按住空转）压到 5% 以内",
                "打之前先看一眼距离和它的白环"),
            "survival": (
                f"被击毁 {m['deaths']} 次 → 1 次以内",
                "距离保持 280–420px，别贴脸对枪"),
            "movement": (
                f"原地不动 {m['stationary_ratio']*100:.0f}% → 40% 以内",
                "一直在动：小幅换向比站桩安全"),
        }
        order = sorted(parts, key=lambda k: parts[k])
        out = []
        for key in order[:2]:
            title, how = targets[key]
            out.append({"item": key, "target": title, "how": how,
                        "score": round(parts[key])})
        return out

    # ------------------------------------------------------------------ 文案
    def new_hint(self):
        """本帧新触发的纠正（用于游戏里的即时提示，每条只提示一次）。

        返回 (code, 短文案) 或 None。只报严重程度 >= 60 的项，避免啰嗦。
        """
        for advice in self.advices():
            if advice["severity"] < 60 or advice["code"] in self._hinted:
                continue
            self._hinted.add(advice["code"])
            return advice["code"], f"{advice['title']}：{advice['fix']}"
        return None

    def hud_lines(self):
        """实时面板（F5）的几行字。"""
        m = self.metrics()
        parts, total = self.scores()
        return [
            f"SCORE {total:.0f}/100 ({grade_of(total)})  {m['duration']:.0f}s",
            f"HITS {m['hits']}/{m['shots']} = {m['accuracy']*100:.0f}%"
            f"   OUT {m['hits_per_sec']:.2f}/s",
            f"AIM {m['avg_err']:.1f} deg   DIST {m['avg_dist']:.0f}px",
            f"WASTE far {m['far_shots']}  shield {m['shield_hits']}"
            f"  held {m['held_blocked']}f",
            f"A/S {parts['accuracy']:.0f}  O {parts['output']:.0f}"
            f"  D {parts['discipline']:.0f}  V {parts['survival']:.0f}"
            f"  M {parts['movement']:.0f}",
        ]

    def report_text(self, hist=None):
        """终端用：一局结束的完整评估。"""
        m = self.metrics()
        parts, total = self.scores()
        lines = [
            f"=== 本局评估 · {m['skill']} · {m['duration']:.1f}s ===",
            f"综合 {total:.0f}/100 ({grade_of(total)})    "
            f"命中 {m['hits']}/{m['shots']} = {m['accuracy']*100:.0f}%    "
            f"有效输出 {m['hits_per_sec']:.2f} 命中/秒",
            f"开火纪律：超距 {m['far_shots']} 发 / 打护盾 {m['shield_hits']} 发 / "
            f"按住空转 {m['held_blocked']} 帧 / 冷却空转约 {m['idle_lost_shots']} 发",
            (f"站位：平均 {m['avg_dist']:.0f}px（贴身 {m['close_shots']} 发），"
             f"原地不动 {m['stationary_ratio']*100:.0f}%"
             if m["shots"] else
             f"站位：本局没开过枪，原地不动 {m['stationary_ratio']*100:.0f}%"),
            f"生存：被击毁 {m['deaths']} 次，挨 {m['hits_taken']} 发"
            f"（电脑命中率 {m['enemy_accuracy']*100:.0f}%），"
            f"它开火后换向 {m['dodge_ratio']*100:.0f}%",
            f"分项：准度 {parts['accuracy']:.0f} · 输出 {parts['output']:.0f} · "
            f"纪律 {parts['discipline']:.0f} · 生存 {parts['survival']:.0f} · "
            f"走位 {parts['movement']:.0f}",
        ]

        advices = self.advices()
        lines.append("")
        if advices:
            lines.append("纠正：")
            for i, a in enumerate(advices[:4], 1):
                lines.append(f" {i}. [{'!' if a['severity'] >= 70 else '·'}] {a['title']}")
                lines.append(f"    证据：{a['evidence']}")
                lines.append(f"    怎么改：{a['fix']}")
        else:
            lines.append("纠正：这一局没发现明显问题。")

        good = self.positives()
        if good:
            lines.append("")
            lines.append("做得好的地方：" + "；".join(good))

        drills = self.drills()
        if drills:
            lines.append("")
            lines.append("下一局目标：")
            for d in drills:
                lines.append(f" · {d['target']}（{d['how']}）")

        trend = history_trend_text(hist)
        if trend:
            lines.append("")
            lines.append(trend)
        return "\n".join(lines)

    def report_lines(self, hist=None, compact=True):
        """屏幕用：短行，够看清结论即可。"""
        m = self.metrics()
        parts, total = self.scores()
        lines = [
            f"SCORE {total:.0f}/100  ({grade_of(total)})   {m['duration']:.0f}s  "
            f"{m['skill']}",
            f"命中 {m['hits']}/{m['shots']} = {m['accuracy']*100:.0f}%   "
            f"输出 {m['hits_per_sec']:.2f}/s",
            f"准度{parts['accuracy']:.0f} 输出{parts['output']:.0f} "
            f"纪律{parts['discipline']:.0f} 生存{parts['survival']:.0f} "
            f"走位{parts['movement']:.0f}",
            f"废弹：超距{m['far_shots']} 护盾{m['shield_hits']} "
            f"按住{m['held_blocked']}帧",
        ]
        if not compact:
            lines.append(f"平均距离 {m['avg_dist']:.0f}px  "
                         f"平均偏差 {m['avg_err']:.1f}°")
        advice = self.advices()
        if advice:
            lines.append("")
            for a in advice[:3]:
                lines.append(f"! {a['title']}")
                lines.append(f"  {a['fix']}")
        drills = self.drills()
        if drills:
            lines.append("")
            lines.append(f"目标：{drills[0]['target']}")
        best = best_row(hist)
        if best:
            lines.append(f"历史最佳 {best.get('score', 0)} 分"
                         f"（{best.get('grade', '?')}，第 {best.get('index', '?')} 局）")
        return lines

    # ------------------------------------------------------------------ 内部
    def _worst_err_band(self):
        """开火最多的那个「偏差大」的分档（用来举例说明偏差的代价）。"""
        candidates = [b for b in self.err_shots
                      if b in ("10-20", ">20") and self.err_shots[b] >= 2]
        if not candidates:
            return None
        return max(candidates, key=lambda b: self.err_shots[b])

    def history_row(self, won=None):
        """写入历史文件的一行（只存能横向比较的数字）。"""
        m = self.metrics()
        parts, total = self.scores()
        row = {
            "skill": m["skill"],
            "score": round(total),
            "grade": grade_of(total),
            "accuracy": round(m["accuracy"], 3),
            "hits": m["hits"],
            "shots": m["shots"],
            "kills": m["kills"],
            "deaths": m["deaths"],
            "duration": round(m["duration"], 1),
        }
        if won is not None:
            row["won"] = bool(won)
        return row


# ---------------------------------------------------------------------- 历史
def load_history(path=None):
    """读取历史（不存在 / 坏了都当空历史，绝不让游戏起不来）。"""
    path = path or COACH_HISTORY_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"version": 1, "rounds": []}
    except Exception as exc:
        print(f"[coach] 历史文件读取失败（{exc}），从空历史开始")
        return {"version": 1, "rounds": []}
    if isinstance(data, dict) and isinstance(data.get("rounds"), list):
        data["version"] = 1
        return data
    print("[coach] 历史文件格式不对，从空历史开始")
    return {"version": 1, "rounds": []}


def record_round(hist, row, path=None):
    """追加一行历史并原子落盘，返回 (成功, 路径或错误)。"""
    path = path or COACH_HISTORY_PATH
    rounds = hist.setdefault("rounds", [])
    rounds.append(dict(row))
    del rounds[:-MAX_HISTORY]
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(hist, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        return True, path
    except Exception as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, str(exc)


def best_row(hist):
    """历史里分数最高的一局（带 #序号，从 1 开始）。"""
    rows = [r for r in (hist or {}).get("rounds", []) if isinstance(r, dict)]
    if not rows:
        return None
    best = max(rows, key=lambda r: r.get("score", 0))
    best = dict(best)
    best["index"] = rows.index(best) + 1
    return best


def history_trend_text(hist, n=5):
    """「第 4 局 · 最佳 71 分 · 命中率 31% → 44% → 41%」这样一行。"""
    rows = [r for r in (hist or {}).get("rounds", []) if isinstance(r, dict)]
    if not rows:
        return ""
    accs = [r.get("accuracy") for r in rows[-n:]
            if isinstance(r.get("accuracy"), (int, float))]
    best = best_row(hist)
    text = f"历史：第 {len(rows)} 局"
    if best:
        text += f"，最佳 {best.get('score', 0)} 分（{best.get('grade', '?')}）"
    if len(accs) >= 2:
        text += "，命中率 " + " → ".join(f"{a*100:.0f}%" for a in accs)
    return text


def history_text(hist, n=10):
    """`--coach-history`：把历史打印成小表格，方便看自己有没有在进步。"""
    rows = [r for r in (hist or {}).get("rounds", []) if isinstance(r, dict)]
    if not rows:
        return "还没有历史记录：先在 1p 模式下打完一局（或打完按 R 再来）。"
    shown = rows[-n:]
    out = [f"=== 对战评估历史（共 {len(rows)} 局，显示最近 {len(shown)} 局）===",
           f"{'#':>3} {'难度':<7}{'综合':>5}{'等级':>5}{'命中率':>7}"
           f"{'命中/开枪':>10}{'击毁/被毁':>10}{'时长':>7}"]
    start = len(rows) - len(shown)
    for i, r in enumerate(shown, start + 1):
        acc = r.get("accuracy")
        acc_s = f"{acc*100:.0f}%" if isinstance(acc, (int, float)) else "-"
        out.append(f"{i:>3} {str(r.get('skill', '?')):<7}{r.get('score', 0):>5}"
                   f"{str(r.get('grade', '?')):>5}{acc_s:>7}"
                   f"{str(r.get('hits', 0)) + '/' + str(r.get('shots', 0)):>10}"
                   f"{str(r.get('kills', 0)) + '/' + str(r.get('deaths', 0)):>10}"
                   f"{r.get('duration', 0):>6.1f}s")
    out.append("")
    trend = history_trend_text(hist)
    if trend:
        out.append(trend)
    return "\n".join(out)
