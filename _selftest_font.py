"""屏幕中文乱码（方块）的回归测试。

被修掉的 bug：屏幕文案（局末报告卡、F5 面板、即时提示）全是中文，但字体用的是
pygame.font.SysFont(None, ...)，指向自带的 freesansbold.ttf —— 没有汉字字形，
每个汉字都被画成同一个方块，看起来就是一串乱码。这个测试从几个角度盯住它：

1. 默认字体确实画不出汉字（把「bug 的成因」钉成断言，防止有人改回去）；
2. tank_game.load_font 找到的字体能画出不同的汉字（每个字各有各的字形）；
3. 没有汉字字体时，屏幕文案（report_lines / new_hint / 终端报告）自动切英文，
   一个非 ASCII 字符都不留 —— 宁可全英文，也不再出现方块；
4. 中文模式下文案仍是中文（别为了修乱码把界面全英文化）。

跑法：cd tank && SDL_VIDEODRIVER=dummy python3.11 _selftest_font.py
"""

import os
import subprocess
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import tank_coach as c
import tank_game as g


def glyphs(font, text):
    """每个字符各渲染一次，返回像素串列表（相同 = 同一个字形）。"""
    return [pygame.image.tostring(font.render(ch, False, (255, 255, 255)), "RGB")
            for ch in text]


def non_ascii(texts):
    return [t for t in texts if any(ord(ch) > 127 for ch in t)]


class FakeTank:
    """评估器只读 pos / angle / alive / invuln / cooldown，给个哑对象就够。"""

    def __init__(self, x, y, angle=0.0):
        self.pos = pygame.Vector2(x, y)
        self.angle = angle
        self.alive = True
        self.invuln = 0
        self.cooldown = 0


def sample_coach():
    """造一局有内容的对局：开过几枪（含一发超距）、挨过一发、死过一次。"""
    coach = c.BattleCoach(fps=60, fire_range=560, fire_cooldown=18,
                          invuln_frames=60, max_lives=5)
    human = FakeTank(100, 300, angle=0.0)
    cpu = FakeTank(400, 300, angle=180.0)
    for _ in range(120):
        human.pos.x += 0.5
        coach.observe(human, cpu, fire_held=False)
    coach.on_shot(human, cpu)                 # 300px：正常开火
    coach.on_hit(by_human=True, shield=False)
    cpu.pos.x, cpu.pos.y = 760, 120           # 超距那一发
    coach.on_shot(human, cpu)
    coach.on_enemy_shot()
    coach.on_hit(by_human=False)
    coach.on_death(victim_is_human=True)
    return coach


# ---------------------------------------------------------------- 1) bug 成因
default_font = pygame.font.SysFont(None, 20)
assert not g._font_has_cjk(default_font), \
    "默认字体居然能画汉字？那这个 bug 的前提变了，重新确认 load_font 的必要性"
assert len(set(glyphs(default_font, "命中率"))) == 1, \
    "默认字体的三个汉字应该都是同一个 .notdef 方块"
# 汉字被画成一个小方块（.notdef），三个字的宽度完全一样 —— 这就是「乱码」的观感
assert (default_font.size("命")[0] == default_font.size("中")[0]
        == default_font.size("率")[0] <= 8), default_font.size("命中率")
print("[1] 默认字体画不出汉字（3 个字 = 同一个方块），bug 成因确认")

# ---------------------------------------------------------------- 2) 找字体
src = g._font_source()
has_cjk = g.cjk_font_available()
print(f"[2] 汉字字体来源：{src}（可用 = {has_cjk}）")
font = g.load_font(18)
assert font is g.load_font(18), "同尺寸字体应复用同一个对象"
coach = sample_coach()

if has_cjk:
    assert len(set(glyphs(font, "命中率"))) == 3, "「命中率」应渲染成三种不同字形"
    lines = coach.report_lines({"version": 1, "rounds": []}, compact=False)
    chinese = {ch for ln in lines for ch in ln if ord(ch) > 127}
    shots = glyphs(font, "".join(sorted(chinese)))
    assert len(set(shots)) == len(chinese), \
        f"{len(chinese)} 个汉字里出现了重复字形（有字没被画出来）"
    # 顺带确认字体宽度是正常的汉字宽度，而不是 5px 的 .notdef
    assert font.size("命中率")[0] > 2 * default_font.size("命中率")[0]
    print(f"[2] load_font 能画出 {len(chinese)} 个不同汉字，报告卡不再是方块")
else:
    print("[2] 本机没有汉字字体：跳过字形校验（下面验证英文兜底）")

# ---------------------------------------------------------------- 3) 英文兜底
c.ASCII_ONLY = True
try:
    lines = coach.report_lines({"version": 1, "rounds": []}, compact=False)
    assert lines, "报告卡不能为空"
    assert not non_ascii(lines), f"ASCII 模式下仍有非 ASCII：{non_ascii(lines)!r}"
    assert any("HITS" in ln for ln in lines), lines

    hint = coach.new_hint()
    assert hint is None or not non_ascii([hint[1]]), hint

    text = coach.report_text({"version": 1, "rounds": []}, ascii_only=True)
    assert not non_ascii(text.splitlines()), non_ascii(text.splitlines())
    assert "ROUND REPORT" in text and "Next round goals" in text
    assert "本局评估" not in text

    hist = {"version": 1, "rounds": [
        {"skill": "NORMAL", "score": 71, "grade": "B", "accuracy": 0.625,
         "hits": 10, "shots": 16, "kills": 2, "deaths": 5, "duration": 14.2},
        {"skill": "HARD", "score": 58, "grade": "C", "accuracy": 0.41,
         "hits": 7, "shots": 17, "kills": 1, "deaths": 5, "duration": 12.0},
    ]}
    table = c.history_text(hist)
    assert not non_ascii(table.splitlines()), non_ascii(table.splitlines())
    print("[3] 没有汉字字体时，屏幕/终端文案全部退成纯 ASCII")
finally:
    c.ASCII_ONLY = False

# ---------------------------------------------------------------- 4) 中文模式
zh_lines = coach.report_lines({"version": 1, "rounds": []}, compact=True)
assert any("命中" in ln for ln in zh_lines), zh_lines
zh_text = coach.report_text({"version": 1, "rounds": []})
assert "本局评估" in zh_text and "下一局目标" in zh_text
print("[4] 中文模式未被误伤：报告卡/终端报告仍是中文")

# ---------------------------------------------------------------- 5) 卡片绘制
# 行首标记改成 ASCII 的 ! / >，两种语言、两种字体都能正确上色
card = pygame.Surface((420, 420))
marked = ["SCORE 71/100 (B)", "! Holding the fire button down", "> BEST 82 (A)"]
g.draw_coach_card(card, font, g.load_font(14), "BATTLE REPORT", marked, 4, 4,
                  width=404)
assert pygame.image.tostring(card, "RGB") != bytes(420 * 420 * 3), "卡片没画出来"
# 真正的回归断言：把中文报告卡画上去后，汉字位置不能是一片相同的方块
if has_cjk:
    card2 = pygame.Surface((420, 420))
    g.draw_coach_card(card2, font, g.load_font(14), "BATTLE REPORT",
                      zh_lines[:6], 4, 4, width=404)
    assert pygame.image.tostring(card2, "RGB") != pygame.image.tostring(card, "RGB")
print("[5] 报告卡能绘制，行首 ! / > 标记可用于上色")

# ---------------------------------------------------------------- 6) 端到端
# 独立进程里再验一次：报告卡上的每个汉字都画出不同字形（没有方块）。
# 必须只在 __main__ 下跑：子进程会 `import _selftest_font`，否则会无限递归。
def end_to_end_probe():
    here = os.path.dirname(os.path.abspath(__file__))
    probe = (
        "import os; os.environ.setdefault('SDL_VIDEODRIVER','dummy')\n"
        "import pygame, tank_coach, tank_game as g\n"
        "import _selftest_font as t\n"
        "c = t.sample_coach()\n"
        "f = g.load_font(14)\n"
        "joined = ''.join(c.report_lines(None, compact=False))\n"
        "ch = sorted({x for x in joined if ord(x) > 127})\n"
        "print('DISTINCT', len(set(t.glyphs(f, ''.join(ch)))), len(ch))\n")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, env=dict(os.environ, SDL_VIDEODRIVER="dummy"),
                         cwd=here)
    assert out.returncode == 0, out.stderr
    distinct, total = [int(x) for x in
                       out.stdout.split("DISTINCT")[1].split()[:2]]
    assert total > 0 and distinct == total, f"{total} 个汉字只画出 {distinct} 种字形"
    print(f"[6] 端到端：报告卡里 {total} 个汉字各画出不同字形（无方块）")


if __name__ == "__main__":
    if has_cjk:
        end_to_end_probe()
    print("字体测试全部通过：默认字体无汉字 / 自动选字体 / 中英兜底 / 卡片绘制 / 端到端")
