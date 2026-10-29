"""攻击键配置自检：文件读写/校验 + F4 面板端到端（真跑 main）。"""
import json
import os
import tempfile

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import pygame
import tank_game as g

tmpdir = tempfile.mkdtemp(prefix="tank_keys_")
cfg = os.path.join(tmpdir, "tank_keys.json")

# ---------- 1) 文件不存在 → 默认 SPACE / ENTER ----------
codes, names, pad = g.load_fire_config(os.path.join(tmpdir, "missing.json"))
assert codes["p1"] == pygame.K_SPACE and codes["p2"] == pygame.K_RETURN, codes
assert names == {"p1": "space", "p2": "return"}, names
assert pad is None
print("[1] 缺配置文件 OK：默认 P1=SPACE P2=ENTER")

# ---------- 2) 保存 → 立刻读回（原子写，无 .tmp 残留） ----------
ok, info = g.save_fire_config({"p1": "f", "p2": "g"}, (0, 3), cfg)
assert ok, info
assert not os.path.exists(cfg + ".tmp"), "不应留下 .tmp"
raw = json.load(open(cfg, encoding="utf-8"))
assert raw["fire"] == {"p1": "f", "p2": "g"}, raw
codes2, names2, pad2 = g.load_fire_config(cfg)
assert codes2["p1"] == pygame.K_f and codes2["p2"] == pygame.K_g, codes2
assert names2 == {"p1": "f", "p2": "g"}, names2
assert pad2 == (0, 3), pad2
print("[2] 保存/读回 OK：", raw["fire"], "pad_fire=", raw["pad_fire"])

# ---------- 3) JSON 损坏 → 回退默认，不抛异常 ----------
open(cfg, "w", encoding="utf-8").write("{not json,,,")
codes3, names3, _ = g.load_fire_config(cfg)
assert names3 == {"p1": "space", "p2": "return"}, names3
print("[3] 文件损坏 OK：回退默认", names3)

# ---------- 4) 两名玩家撞键 / 非法键 / 保留键 ----------
json.dump({"fire": {"p1": "return", "p2": "return"}}, open(cfg, "w", encoding="utf-8"))
codes4, names4, _ = g.load_fire_config(cfg)
assert names4["p1"] == "space", names4          # 撞键 → P1 保持默认
assert names4["p2"] == "return", names4
json.dump({"fire": {"p1": "f2"}}, open(cfg, "w", encoding="utf-8"))
assert g.load_fire_config(cfg)[1]["p1"] == "space", "F2 是保留键，必须拒绝"
json.dump({"fire": {"p1": "definitely_not_a_key"}}, open(cfg, "w", encoding="utf-8"))
assert g.load_fire_config(cfg)[1]["p1"] == "space", "非法键名必须回退"
assert g.check_fire_key("escape")[0] is None
assert g.check_fire_key("f2")[0] is None
assert g.check_fire_key("f")[0] == pygame.K_f
print("[4] 校验 OK：撞键/保留键/非法键全部回退默认")

# ---------- 5) 手柄开火按钮：规整 + 真正影响 fire_held ----------
assert g.normalize_pad_fire([0, 3, 3, 99, -1, "x"]) == (0, 3)
g.set_pad_fire_buttons((3,))
assert g.PAD_FIRE_BUTTONS == (3,), g.PAD_FIRE_BUTTONS


class FakeController:
    def __init__(self, pressed):
        self.pressed = set(pressed)
        self.name = "FakePad"

    def get_button(self, b):
        return b in self.pressed

    def get_axis(self, a):
        return -1        # 扳机松到底

    def quit(self):
        pass

    def get_mapping(self):
        return {}


pad_b3 = g.Gamepad(0, controller=FakeController({3}))
assert pad_b3.fire_held() is True, "配置成 BTN3(Y) 后，按 Y 应该开火"
pad_b0 = g.Gamepad(1, controller=FakeController({0}))
assert pad_b0.fire_held() is False, "配置成 BTN3 后，按 A 不应再开火"
g.set_pad_fire_buttons(g.PAD_FIRE_DEFAULT_BUTTONS)
assert g.Gamepad(0, controller=FakeController({0})).fire_held() is True
print("[5] 手柄开火按钮 OK：可换成单个按钮，A 不再是开火键，扳机仍有效")

# ---------- 6) 端到端：F4 面板改键 → 立即生效 → 落盘 ----------
g.KEYS_CONFIG_PATH = cfg
if os.path.exists(cfg):
    os.remove(cfg)

orig_init = g.Tank.__init__


def patched_init(self, x, y, color, controls, **kw):
    orig_init(self, x, y, color, controls, **kw)
    if color == g.RED:
        self.pos.update(150, 300)
    else:
        self.pos.update(650, 300)
    self.cooldown = 0
    self.turn_speed = 0.0


g.Tank.__init__ = patched_init

fires = []
orig_fire = g.Tank.fire


def counting_fire(self):
    fires.append(self.color)
    return orig_fire(self)


g.Tank.fire = counting_fire


def pad_event(button):
    evt_type = getattr(pygame, "CONTROLLERBUTTONDOWN", pygame.JOYBUTTONDOWN)
    return pygame.event.Event(evt_type, which=0, button=button,
                              instance_id=1, state=1)


SDL2_BTN_B = pygame.CONTROLLER_BUTTON_B

SCRIPT = {
    1: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F4)],   # 开面板
    2: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_1)],    # 选 P1
    3: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_f)],    # 新键 F
    4: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F4)],   # 关面板
    8: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_f)],    # F = 开火
    12: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)],  # 旧键应失效
    16: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F4)],  # 开面板
    17: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_3)],   # 手柄槽位
    18: [pad_event(SDL2_BTN_B)],                                # 按手柄 B
    20: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F4)],  # 关面板
    24: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F4)],  # 再开面板
    25: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_0)],   # 恢复默认
    26: [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)],  # 关面板
}
frames = {"n": 0}


def fake_get():
    n = frames["n"]
    frames["n"] += 1
    evts = list(SCRIPT.get(n, []))
    if n >= 32:
        evts.append(pygame.event.Event(pygame.QUIT))
    return evts


pygame.event.get = fake_get

try:
    g.main()
except SystemExit as e:
    print("main 正常退出，code =", e.code)

state = json.load(open(cfg, encoding="utf-8"))
assert state["fire"] == {"p1": "space", "p2": "return"}, state   # 最后按了 0 恢复默认
print("[6] 面板流程跑完，最终配置 =", state)

# 中途的“已生效”证据：按 F 开过火、按 SPACE 没开火、手柄 B 被记下过
assert fires.count(g.RED) >= 1, f"按 F 应该开火，实际 {fires}"
assert len(fires) == 1, f"改键后旧键 SPACE 不应再开火，实际开火 {fires}"
print("    开火记录（颜色）：", fires, "（只有 1 次 = F 生效、SPACE 已失效）")
assert g.PAD_FIRE_BUTTONS == g.PAD_FIRE_DEFAULT_BUTTONS, g.PAD_FIRE_BUTTONS
print("    最终手柄开火键 =", g.pad_fire_label())

g.Tank.__init__ = orig_init
g.Tank.fire = orig_fire
for f in (cfg, cfg + ".tmp"):
    if os.path.exists(f):
        os.remove(f)
os.rmdir(tmpdir)
print("KEYS SELFTEST OK")
