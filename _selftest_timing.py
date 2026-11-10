"""固定逻辑步回归：把实测帧间隔换算成 60Hz 逻辑步。

覆盖：
1. 60fps 稳定：每个渲染帧恰好 1 步；
2. 30fps 掉帧：每个渲染帧 2 步，逻辑时间不缩水；
3. 时钟抖动（16/17ms 交替）：总步数与真实时间一致；
4. 单帧卡 50ms：补 3 步，不把时间丢掉；
5. 异常间隔（首帧 0、拖窗口 1s、负值）：按 1 步算，避免瞬移；
6. 600 帧长跑不漂移，累加器回到 0；
7. 同输入跑两遍，步数序列完全一致；
8. 跨帧余量保留：两次 10ms 合成 1 步，而不是各丢一半。

运行：SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3.11 _selftest_timing.py
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import tank_game as g

STEP = 1.0 / g.FPS


def run(sequence, start=0.0):
    """按渲染帧序列喂 fixed_steps，返回 (总步数, 最终累加器, 每帧步数)。"""
    acc = start
    total = 0
    per_frame = []
    for elapsed in sequence:
        steps, acc = g.fixed_steps(elapsed, acc)
        total += steps
        per_frame.append(steps)
    return total, acc, per_frame


# 1) 60fps：每个渲染帧恰好 1 步
total, acc, per = run([STEP] * 60)
assert per == [1] * 60, per[:10]
assert total == 60, total
assert abs(acc) < 1e-9, acc
print("[1] 60fps OK：60 帧 = 60 步")

# 2) 30fps：一个渲染帧补 2 步；60 个渲染帧 = 120 步（2 秒逻辑时间）
total, acc, per = run([2 * STEP] * 60)
assert per == [2] * 60, per[:10]
assert total == 120, total
print("[2] 30fps OK：60 帧 = 120 步，逻辑时间不缩水")

# 3) 时钟抖动：16ms / 17ms 交替，总步数不偏离真实时间（±1）
total, _, _ = run([0.016, 0.017] * 30)          # 合计 0.99 秒
assert abs(total - 60) <= 1, total
print(f"[3] 时钟抖动 OK：0.99 秒 -> {total} 步")

# 4) 单帧卡 50ms：补 3 步，余量保留
steps, acc = g.fixed_steps(0.05, 0.0)
assert steps == 3, steps
assert abs(acc - (0.05 - 3 * STEP)) < 1e-9, acc
# 上限：0.1 秒最多补 6 步，不会一帧跑完整局
assert g.fixed_steps(0.1, 0.0)[0] == 6
print("[4] 掉帧补步 OK：50ms -> 3 步，100ms 上限 6 步")

# 5) 异常间隔按 1 步算（首帧、拖窗口、配置面板暂停）
assert g.fixed_steps(0.0, 0.0)[0] == 1
assert g.fixed_steps(1.0, 0.0)[0] == 1
assert g.fixed_steps(-0.5, 0.0)[0] == 1
print("[5] 异常间隔 OK：<=0 或 >0.1 秒按 1 步算")

# 6) 长时间 60fps 不漂移：600 帧 = 600 步，累加器回到 0
total, acc, _ = run([STEP] * 600)
assert total == 600, total
assert abs(acc) < 1e-9, acc
print("[6] 长跑不漂移 OK：600 帧 = 600 步")

# 7) 确定性：同一组输入跑两遍，步数序列与最终余量逐位一致
seq = [0.011, 0.022, 0.033, 0.016, 0.05, 0.008] * 20
a = run(seq)
b = run(seq)
assert a == b, (a[0], b[0], a[1], b[1])
print(f"[7] 确定性 OK：{len(seq)} 帧回放两次结果一致")

# 8) 跨帧余量保留：两次 10ms 合成 1 步（各自都不足 15ms 阈值）
steps1, acc = g.fixed_steps(0.010, 0.0)
steps2, acc = g.fixed_steps(0.010, acc)
assert steps1 == 0, steps1
assert steps2 == 1, steps2
assert abs(acc + STEP - 0.020) < 1e-9, acc
print("[8] 跨帧余量 OK：两次 10ms 合成 1 步")

print("TIMING OK：固定逻辑步换算全部通过")
