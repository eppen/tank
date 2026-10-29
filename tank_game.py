import json
import math
import os
import random
import sys

import pygame

import tank_coach

# --- 初始化设置 ---
pygame.init()
try:
    pygame.joystick.init()
except pygame.error:  # dummy 视频驱动 / 无输入设备权限的 CI 环境
    pass

try:
    # SDL GameController 层：把 Xbox / DS4 / 8BitDo 等手柄映射成统一语义，
    # 避免裸 joystick 的原始轴号/按钮号因品牌而异。
    from pygame._sdl2 import controller as sdl2_controller

    sdl2_controller.init()
except Exception:  # pragma: no cover - 没有 SDL2 controller 支持时降级为“无手柄”
    sdl2_controller = None

# 屏幕设置
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 600
FPS = 60
screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
pygame.display.set_caption("Top-Down Tank Game")

# 颜色定义
WHITE = (255, 255, 255)
BLACK = (18, 18, 22)
RED = (214, 72, 72)
BLUE = (72, 118, 214)
GRAY = (120, 120, 130)
DARK = (44, 44, 52)
YELLOW = (255, 224, 130)

TANK_SIZE = 44
TANK_HALF = TANK_SIZE // 2

# --- 转向符号约定：全工程唯一出处，要反方向只改这里 ---
# 角度：0 = 朝上，顺时针为正（90 = 朝右）。屏幕 y 轴向下，所以「角度增大」
# 在画面上就是右转。键盘 A 键 / ← 键、手柄左摇杆左推都送 TURN_LEFT（-1），
# D 键 / → 键 / 摇杆右推都送 TURN_RIGHT（+1），drive() 只认这一个符号。
TURN_RIGHT = +1.0   # 顺时针 = 画面右转 = 角度增大
TURN_LEFT = -1.0    # 逆时针 = 画面左转 = 角度减小

# 战斗参数
MAX_HP = 100
DAMAGE = 20            # 每发扣 20 血 => 5 发击毁一次
# 生命值：每台坦克 5 条命。血条被打空算「被击毁一次」，扣一条命；
# 还有命就在出生点复活，命扣完才算输掉这一局。
MAX_LIVES = 5
RESPAWN_DELAY = 60     # 被击毁到复活的等待帧数（60 帧 = 1 秒）
SPAWN_INVULN = 60      # 复活后的无敌帧数（1 秒），避免刚出生就被补枪
FIRE_COOLDOWN = 18     # 冷却帧数，约 0.3 秒
BULLET_SPEED = 9.0
BULLET_RADIUS = 4
MUZZLE_OFFSET = TANK_HALF + 3


# --- 单人 vs 电脑参数 ---
# 三档难度只调「瞄得多准、多快下决心」，不改变任何物理规则（速度、转向速率、
# 冷却、伤害都相同），所以不会出现“AI 作弊比玩家转得快”的手感。
AI_SKILLS = {
    "easy": {"label": "EASY", "turn_gain": 0.75, "aim_jitter": 11.0,
             "fire_tol": 9.0, "reaction": 22, "keep_min": 180.0,
             "keep_max": 340.0, "speed": 0.60, "fire_chance": 0.55, "lead": 0.0},
    "normal": {"label": "NORMAL", "turn_gain": 1.00, "aim_jitter": 5.5,
               "fire_tol": 6.0, "reaction": 13, "keep_min": 140.0,
               "keep_max": 300.0, "speed": 0.85, "fire_chance": 0.85, "lead": 0.55},
    "hard": {"label": "HARD", "turn_gain": 1.25, "aim_jitter": 2.0,
             "fire_tol": 4.0, "reaction": 8, "keep_min": 120.0,
             "keep_max": 280.0, "speed": 1.00, "fire_chance": 1.00, "lead": 1.00},
}
AI_SKILL_ORDER = ("easy", "normal", "hard")
AI_FIRE_RANGE = 560.0     # 再远就是子弹先出屏，白送冷却，不开火
AI_TURN_FULL_ERR = 22.0   # 角度误差达到 22 度就打满舵


# --- 手柄参数 ---
# 统一按 SDL GameController 语义取值：左摇杆 / ABXY / 模拟扳机 / Start·Back。
# 因此不需要记录“某个品牌 A 是按钮几号”，SDL 的映射表会处理。
PAD_DEADZONE = 0.25
PAD_TRIGGER_ON = 0.5
PAD_AXIS_SCALE = 32767.0   # Controller.get_axis() 返回 -32768..32767 的整数
PAD_RUMBLE_MS = 180
PAD_DEBUG = os.environ.get("TANK_PAD_DEBUG") == "1"

# --- 表现评估（coach）---
# 只统计「人类 vs 电脑」那一局（1p 模式）；双人对战没有可比基准，不计数。
# 局末打印详细评估并写入 tank_coach.json，F5 开关实时面板，TANK_COACH=0 整体关掉。
COACH_ENABLED = os.environ.get("TANK_COACH", "1") != "0"


def _env_ints(name, default):
    """读取形如 TANK_PAD_AXES="0,1" 的环境变量，失败则用默认值。"""
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        vals = tuple(int(p) for p in raw.replace(" ", "").split(",") if p != "")
    except ValueError:
        print(f"[pad] 环境变量 {name}={raw!r} 解析失败，用默认值 {default}")
        return default
    if len(vals) != len(default):
        print(f"[pad] 环境变量 {name}={raw!r} 个数不对，用默认值 {default}")
        return default
    return vals


def _env_int_tuple(name, default):
    """任意长度的整数环境变量（默认值可以是空元组）。"""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return tuple(int(p) for p in raw.replace(" ", "").split(",") if p != "")
    except ValueError:
        print(f"[pad] 环境变量 {name}={raw!r} 解析失败，用默认值 {default}")
        return default


# 杂牌手柄把摇杆放在别的轴上时可用 TANK_PAD_AXES="0,1" 覆盖（x,y）
PAD_AXIS_X, PAD_AXIS_Y = _env_ints(
    "TANK_PAD_AXES",
    (pygame.CONTROLLER_AXIS_LEFTX, pygame.CONTROLLER_AXIS_LEFTY),
)
PAD_TRIGGER_AXES = (pygame.CONTROLLER_AXIS_TRIGGERLEFT,
                    pygame.CONTROLLER_AXIS_TRIGGERRIGHT)
# ABXY 任一开火（不挑品牌布局），LB/RB 也顺手算上
PAD_FIRE_DEFAULT_BUTTONS = (pygame.CONTROLLER_BUTTON_A, pygame.CONTROLLER_BUTTON_B,
                            pygame.CONTROLLER_BUTTON_X, pygame.CONTROLLER_BUTTON_Y,
                            pygame.CONTROLLER_BUTTON_LEFTSHOULDER,
                            pygame.CONTROLLER_BUTTON_RIGHTSHOULDER)
PAD_FIRE_BUTTONS = _env_ints("TANK_PAD_FIRE", PAD_FIRE_DEFAULT_BUTTONS)
PAD_RESTART_BUTTONS = (pygame.CONTROLLER_BUTTON_START,
                       pygame.CONTROLLER_BUTTON_BACK)

# --- 非标准手柄兜底（原始 joystick 通道）---
# SDL 只把「查得到映射表」的设备当成 GameController（XInput、Switch Pro、
# 内置映射库里的 DInput 手柄……）。厂商没进映射库的型号（例如 EasySMX X05
# 这类手柄在 macOS 上的 HID 模式、不少国产杂牌），SDL 不认，会被静默忽略。
# 这时退到 pygame.joystick 的原始读数：左摇杆默认按 axes 0/1 走，开火默认
# 按按钮 0-3（A/B/X/Y 的常见位），轴序不同就用 TANK_PAD_AXES="0,1" 覆盖。
# 扳机在原始模式下默认关闭：轴号纯靠猜，猜错会变成「一直开火」；
# 确实要用加 TANK_PAD_RAW_TRIGGERS="4,5"。TANK_PAD_RAW=0 可整体关掉兜底。
PAD_RAW_ENABLED = os.environ.get("TANK_PAD_RAW", "1") != "0"
PAD_RAW_TRIGGER_AXES = _env_int_tuple("TANK_PAD_RAW_TRIGGERS", ())


# --- 攻击键（开火键）配置 ---
# 键盘 P1/P2 的开火键、手柄开火按钮都可以改：运行中按 F4 打开配置面板，
# 改完立刻原子写入 tank_keys.json，下次启动自动读回。文件格式：
#   {"version": 1, "fire": {"p1": "space", "p2": "return"},
#    "pad_fire": [0, 1, 2, 3, 4, 5]}
# 手柄的 RT/LT 模拟扳机始终算开火（安全兜底），不受本配置影响。
FIRE_KEY_DEFAULTS = {"p1": "space", "p2": "return"}
FIRE_SLOTS = ("p1", "p2")
KEYS_CONFIG_PATH = os.environ.get("TANK_KEYS_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tank_keys.json")
# ESC 退出、F2/F3 切模式与难度、F4 开配置面板、F5 评估面板，都不允许被抢作开火键
RESERVED_KEY_NAMES = frozenset({"escape", "esc", "f1", "f2", "f3", "f4", "f5"})
PAD_BUTTON_MAX = getattr(pygame, "CONTROLLER_BUTTON_MAX", 11)
PAD_BUTTON_LABELS = {0: "A", 1: "B", 2: "X", 3: "Y", 4: "BACK", 5: "GUIDE",
                     6: "START", 7: "LSTICK", 8: "RSTICK", 9: "LB", 10: "RB",
                     11: "D-UP", 12: "D-DOWN", 13: "D-LEFT", 14: "D-RIGHT",
                     # SDL 2.24+ 的语义按钮：背部拨片（8BitDo 猎户座 XPro /
                     # Xbox Elite 这类）与分享键，没标签的会在 HUD 里显示 BTN16
                     15: "MISC1", 16: "PADDLE1", 17: "PADDLE2",
                     18: "PADDLE3", 19: "PADDLE4", 20: "TOUCHPAD"}


def key_name_to_code(name):
    """按键名（"space" / "f" / "return"）→ pygame 键码；非法返回 None。"""
    if not isinstance(name, str) or not name.strip():
        return None
    key = name.strip().lower()
    try:
        code = pygame.key.key_code(key)
    except Exception:
        code = None
    if code in (None, pygame.K_UNKNOWN):
        code = getattr(pygame, "K_" + key.upper(), None)
    if code in (None, pygame.K_UNKNOWN):
        return None
    return int(code)


def key_code_to_name(code):
    """键码 → 小写按键名（配置文件里存的就是它）。"""
    try:
        return pygame.key.name(int(code)).lower()
    except Exception:
        return str(code)


def check_fire_key(name, other_name=None):
    """校验开火键名，返回 (键码, 错误原因)；合法时错误原因为 None。"""
    code = key_name_to_code(name)
    if code is None:
        return None, f"无法识别的按键 {name!r}"
    norm = key_code_to_name(code)
    if norm in RESERVED_KEY_NAMES:
        return None, f"{norm.upper()} 是系统保留键（ESC 退出 / F2-F5 功能键）"
    if other_name:
        other_code = key_name_to_code(other_name)
        if other_code is not None and other_code == code:
            return None, f"与另一玩家的开火键 {norm.upper()} 冲突"
    return code, None


def normalize_pad_fire(buttons):
    """把手柄开火按钮列表规整成去重、合法范围内的元组；全非法返回 None。"""
    out = []
    for v in buttons or ():
        try:
            b = int(v)
        except (TypeError, ValueError):
            continue
        if 0 <= b < PAD_BUTTON_MAX and b not in out:
            out.append(b)
    return tuple(out) or None


def set_pad_fire_buttons(buttons):
    """替换手柄开火按钮集（配置面板 / 配置文件用），返回生效后的元组。"""
    global PAD_FIRE_BUTTONS
    norm = normalize_pad_fire(buttons)
    if norm:
        PAD_FIRE_BUTTONS = norm
    return PAD_FIRE_BUTTONS


def pad_fire_label(buttons=None):
    """给 HUD / 面板用的人话按钮名。"""
    if buttons is None:
        buttons = PAD_FIRE_BUTTONS
    return ", ".join(PAD_BUTTON_LABELS.get(b, f"BTN{b}") for b in buttons) + " / RT·LT"


def load_fire_config(path=None):
    """读取攻击键配置，返回 (codes, names, pad_fire)。

    文件缺失 / JSON 损坏 / 键名非法 / 两名玩家撞键，一律回退默认值并打印原因，
    绝不抛异常——配置写坏了也不能让人开不了游戏。
    """
    path = path or KEYS_CONFIG_PATH
    names = dict(FIRE_KEY_DEFAULTS)
    pad_fire = None
    data = None
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            print(f"[keys] {path} 读取失败（{exc}），用默认开火键")
            data = None
    if isinstance(data, dict):
        fire = data.get("fire")
        if isinstance(fire, dict):
            for slot in FIRE_SLOTS:
                if slot not in fire:
                    continue
                other = names["p2"] if slot == "p1" else names["p1"]
                code, err = check_fire_key(fire[slot], other)
                if err:
                    print(f"[keys] {slot.upper()} 开火键 {fire[slot]!r} 无效：{err}；"
                          f"保持 {names[slot].upper()}")
                else:
                    names[slot] = key_code_to_name(code)
        pad_fire = normalize_pad_fire(data.get("pad_fire"))
    codes = {}
    for slot in FIRE_SLOTS:
        code = key_name_to_code(names[slot])
        if code is None:                 # 极端兜底：连默认名都解析不出来
            names[slot] = FIRE_KEY_DEFAULTS[slot]
            code = key_name_to_code(names[slot]) or pygame.K_SPACE
        codes[slot] = code
    return codes, names, pad_fire


def save_fire_config(names, pad_fire=None, path=None):
    """原子写入（先写 .tmp 再 os.replace），返回 (成功, 路径或错误信息)。"""
    path = path or KEYS_CONFIG_PATH
    data = {"version": 1,
            "fire": {slot: names.get(slot, FIRE_KEY_DEFAULTS[slot])
                     for slot in FIRE_SLOTS}}
    norm_pad = normalize_pad_fire(pad_fire)
    if norm_pad:
        data["pad_fire"] = list(norm_pad)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        return True, path
    except Exception as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, str(exc)


def _fit_text(font, text, width):
    """把一行文字裁到指定宽度内（超出用 … 收尾），避免卡片内容溢出。"""
    if font.size(text)[0] <= width:
        return text
    while text and font.size(text + "…")[0] > width:
        text = text[:-1]
    return text + "…"


def draw_coach_card(surf, font, small_font, title, lines, x, y, width=384,
                    accent=YELLOW):
    """评估卡片：半透明底板 + 标题 + 若干短行（F5 实时面板 / 局末报告共用）。"""
    pad = 12
    line_h = 18
    height = 32 + line_h * max(1, len(lines))
    panel = pygame.Surface((width, height), pygame.SRCALPHA)
    panel.fill((10, 10, 14, 228))
    pygame.draw.rect(panel, (86, 86, 104), panel.get_rect(), width=2,
                     border_radius=8)
    surf.blit(panel, (x, y))
    surf.blit(font.render(_fit_text(font, title, width - pad * 2), True, accent),
              (x + pad, y + 7))
    for i, line in enumerate(lines):
        color = (206, 206, 218)
        if line.startswith("!"):
            color = (240, 176, 120)
        elif line.startswith("目标") or line.startswith("历史"):
            color = (150, 220, 160)
        surf.blit(small_font.render(_fit_text(small_font, line, width - pad * 2),
                                    True, color),
                  (x + pad, y + 29 + i * line_h))
    return height


def draw_key_config(surf, font, small_font, names, slot, msg):
    """F4 配置面板：半透明底板 + 当前绑定 + 状态提示。"""
    w, h = 430, 246
    x = (SCREEN_WIDTH - w) // 2
    y = (SCREEN_HEIGHT - h) // 2 - 34
    panel = pygame.Surface((w, h), pygame.SRCALPHA)
    panel.fill((10, 10, 14, 232))
    pygame.draw.rect(panel, (96, 96, 118), panel.get_rect(), width=2, border_radius=10)
    surf.blit(panel, (x, y))
    surf.blit(font.render("FIRE KEY CONFIG   (ESC / F4 close)", True, YELLOW),
              (x + 16, y + 12))

    rows = [
        ("1", f"P1 fire : {names['p1'].upper()}"),
        ("2", f"P2 fire : {names['p2'].upper()}"),
        ("3", f"Pad fire: {pad_fire_label()}"),
        ("0", "restore defaults (SPACE / ENTER / ABXY)"),
    ]
    for i, (key, text) in enumerate(rows):
        color = WHITE if slot == "menu" else (198, 198, 210)
        surf.blit(small_font.render(f"[{key}] {text}", True, color),
                  (x + 20, y + 46 + i * 24))

    if slot == "pad":
        prompt = "Press the gamepad button to use for fire  (ESC cancel)"
    elif slot in ("p1", "p2"):
        prompt = f"Press new {slot.upper()} fire key  (ESC cancel)"
    else:
        prompt = "Pick 1/2/3 to rebind, 0 to reset, ESC to close"
    surf.blit(small_font.render(prompt, True, YELLOW), (x + 20, y + 162))
    if msg:
        surf.blit(small_font.render(msg[:70], True, (150, 220, 160)), (x + 20, y + 194))
    surf.blit(small_font.render(f"file: {os.path.basename(KEYS_CONFIG_PATH)}",
                                True, GRAY), (x + 20, y + 216))

# 手柄热插拔事件（CONTROLLER* 事件在部分构建里可能没有）
PAD_ADDED_EVENTS = tuple(
    getattr(pygame, n) for n in ("JOYDEVICEADDED", "CONTROLLERDEVICEADDED")
    if hasattr(pygame, n)
)
PAD_REMOVED_EVENTS = tuple(
    getattr(pygame, n) for n in ("JOYDEVICEREMOVED", "CONTROLLERDEVICEREMOVED")
    if hasattr(pygame, n)
)
PAD_BUTTON_EVENTS = tuple(
    getattr(pygame, n) for n in ("JOYBUTTONDOWN", "CONTROLLERBUTTONDOWN")
    if hasattr(pygame, n)
)


def _deadzone(v, dz=PAD_DEADZONE):
    """死区 + 线性过渡：死区内归零，死区外映射到 0..±1，避免非黑即白。"""
    if abs(v) < dz:
        return 0.0
    sign = 1.0 if v > 0 else -1.0
    return max(-1.0, min(1.0, (abs(v) - dz) / (1.0 - dz))) * sign


class Tank(pygame.sprite.Sprite):
    """俯视角坦克：车体 + 履带 + 炮塔 + 炮管，角度 0 表示朝上。"""

    def __init__(self, x, y, color, controls, speed=3.2, turn_speed=4.0):
        super().__init__()
        self.color = color
        self.controls = controls  # forward/back/left/right/fire -> keycode
        self.pad = None           # 由 main() 绑定 Gamepad；None = 只用键盘
        self.ai = None            # 单人模式下由 main() 绑定 TankAI；None = 人类
        self.speed = speed
        self.turn_speed = turn_speed
        self.angle = 0.0  # 0 = 朝上，顺时针为正
        self.pos = pygame.Vector2(x, y)
        # 出生点：复位 / 掉命复活都回到这里（朝向也回到出生朝向）
        self.spawn_point = pygame.Vector2(x, y)
        self.spawn_angle = self.angle

        self.hp = MAX_HP
        self.lives = MAX_LIVES     # 剩余命数，扣到 0 才算输
        self.alive = True
        self.cooldown = 0
        self.flash = 0  # 受击闪白剩余帧数
        self.invuln = 0  # 复活无敌剩余帧数（>0 时免伤）
        self.respawn_timer = 0  # 被击毁后的复活倒计时（>0 表示正在等复活）

        self.base_image = self._make_tank_surface()
        # 受击闪白贴图：在基础贴图上整体提亮
        self.flash_image = self.base_image.copy()
        self.flash_image.fill((150, 150, 150, 0), special_flags=pygame.BLEND_RGBA_ADD)

        self.image = self.base_image
        self.rect = self.image.get_rect(center=(round(x), round(y)))

    def _make_tank_surface(self):
        surf = pygame.Surface((TANK_SIZE, TANK_SIZE), pygame.SRCALPHA)
        w = TANK_SIZE
        # 左右履带
        pygame.draw.rect(surf, DARK, (0, 8, 9, w - 16), border_radius=3)
        pygame.draw.rect(surf, DARK, (w - 9, 8, 9, w - 16), border_radius=3)
        for y in range(11, w - 10, 6):
            pygame.draw.line(surf, GRAY, (1, y), (8, y), 2)
            pygame.draw.line(surf, GRAY, (w - 8, y), (w - 1, y), 2)

        # 车体
        hull = pygame.Rect(0, 0, 26, 34)
        hull.center = (w // 2, w // 2)
        pygame.draw.rect(surf, self.color, hull, border_radius=6)
        pygame.draw.rect(surf, WHITE, hull, width=2, border_radius=6)

        # 炮塔
        pygame.draw.circle(surf, self.color, (w // 2, w // 2), 10)
        pygame.draw.circle(surf, WHITE, (w // 2, w // 2), 10, 2)

        # 炮管（朝上）
        pygame.draw.rect(surf, WHITE, (w // 2 - 3, 0, 6, 15), border_radius=2)
        return surf

    # --- 移动 ---
    def drive(self, turn=0.0, move=0.0):
        """模拟量输入通道：turn/move 为 -1..1。

        符号约定（见模块顶部 TURN_RIGHT / TURN_LEFT，唯一出处）：
        turn > 0 = 顺时针 = 画面右转 = 角度增大；turn < 0 = 画面左转。
        键盘（turn=±1、move=±1）和手柄摇杆（连续量）共用这一份物理逻辑。
        """
        if turn:
            self.angle += self.turn_speed * turn
        self.angle %= 360

        if move:
            # 倒车慢一点：前进全速，后退 0.6 倍
            speed_scale = 1.0 if move > 0 else 0.6
            rad = math.radians(self.angle)
            step = self.speed * speed_scale * abs(move)
            direction = 1.0 if move > 0 else -1.0
            self.pos.x += math.sin(rad) * step * direction
            self.pos.y -= math.cos(rad) * step * direction

        # 限制在屏幕内
        self.pos.x = max(TANK_HALF, min(SCREEN_WIDTH - TANK_HALF, self.pos.x))
        self.pos.y = max(TANK_HALF, min(SCREEN_HEIGHT - TANK_HALF, self.pos.y))

    def read_keyboard_axes(self, keys):
        """把键盘按键折算成 (turn, move)，取值 ∈ {-1, 0, 1}，供 drive() 使用。"""

        def pressed(keycode):
            try:
                return bool(keys[keycode])
            except (KeyError, IndexError):
                return False

        turn = 0.0
        if pressed(self.controls["left"]):
            turn += TURN_LEFT      # 逆时针 = 画面左转 = 角度减小
        if pressed(self.controls["right"]):
            turn += TURN_RIGHT     # 顺时针 = 画面右转 = 角度增大

        move = 0.0
        if pressed(self.controls["forward"]):
            move += 1.0
        if pressed(self.controls["back"]):
            move -= 1.0
        return turn, move

    def handle_input(self, keys):
        """键盘输入（签名与语义保持不变，_selftest.py 依赖它）。"""
        self.drive(*self.read_keyboard_axes(keys))

    def poll_controls(self, keys):
        """键盘 + 手柄合成输入；没有手柄时等价于 handle_input(keys)。"""
        turn, move = self.read_keyboard_axes(keys)
        if self.pad is not None:
            turn += self.pad.turn()
            move += self.pad.move()
        self.drive(max(-1.0, min(1.0, turn)), max(-1.0, min(1.0, move)))

    # --- 射击 ---
    def muzzle_position(self):
        rad = math.radians(self.angle)
        return self.pos + pygame.Vector2(math.sin(rad), -math.cos(rad)) * MUZZLE_OFFSET

    def can_fire(self):
        return self.alive and self.cooldown <= 0

    def fire(self):
        """射出一颗子弹（从炮口位置出发），并进入冷却。"""
        self.cooldown = FIRE_COOLDOWN
        muzzle = self.muzzle_position()
        return Bullet(muzzle.x, muzzle.y, self.angle, self)

    def take_hit(self, damage=DAMAGE):
        """扣血，返回本次是否被击毁（复活无敌期间免伤，直接返回 False）。"""
        if not self.alive or self.invuln > 0:
            return False
        self.hp = max(0, self.hp - damage)
        self.flash = 6
        if self.hp == 0:
            self.alive = False
            return True
        return False

    # --- 生命值 ---
    def lose_life(self):
        """被击毁一次：扣一条命，返回是否还有命可复活（并起算复活倒计时）。"""
        self.lives = max(0, self.lives - 1)
        self.respawn_timer = RESPAWN_DELAY if self.lives > 0 else 0
        return self.lives > 0

    def respawn(self):
        """在出生点复活：满血、清冷却、给一小段无敌。"""
        self.pos.update(self.spawn_point)
        self.angle = self.spawn_angle
        self.hp = MAX_HP
        self.alive = True
        self.cooldown = 0
        self.flash = 0
        self.invuln = SPAWN_INVULN
        self.respawn_timer = 0
        self.sync_image()

    # --- 每帧计时 ---
    def tick(self):
        if self.cooldown > 0:
            self.cooldown -= 1
        if self.flash > 0:
            self.flash -= 1
        if self.invuln > 0:
            self.invuln -= 1

    def sync_image(self):
        # pygame.transform.rotate 逆时针为正，坦克角度顺时针为正，所以要取负
        src = self.flash_image if self.flash > 0 else self.base_image
        self.image = pygame.transform.rotate(src, -self.angle)
        self.rect = self.image.get_rect(center=(round(self.pos.x), round(self.pos.y)))


class Bullet(pygame.sprite.Sprite):
    """沿炮口方向直线飞行的炮弹，飞出屏幕后自动消失。"""

    def __init__(self, x, y, angle, owner):
        super().__init__()
        self.pos = pygame.Vector2(x, y)
        self.angle = angle
        self.owner = owner
        self.color = owner.color
        rad = math.radians(angle)
        self.vel = pygame.Vector2(math.sin(rad), -math.cos(rad)) * BULLET_SPEED

        size = BULLET_RADIUS * 4
        self.image = pygame.Surface((size, size), pygame.SRCALPHA)
        c = size // 2
        pygame.draw.circle(self.image, (*self.color, 70), (c, c), BULLET_RADIUS + 3)
        pygame.draw.circle(self.image, self.color, (c, c), BULLET_RADIUS)
        pygame.draw.circle(self.image, WHITE, (c, c), 1)
        self.rect = self.image.get_rect(center=(round(x), round(y)))

    def update(self):
        self.pos += self.vel
        self.rect.center = (round(self.pos.x), round(self.pos.y))
        if not (-30 <= self.pos.x <= SCREEN_WIDTH + 30
                and -30 <= self.pos.y <= SCREEN_HEIGHT + 30):
            self.kill()


class Explosion(pygame.sprite.Sprite):
    """扩散并淡出的圆环爆炸。"""

    def __init__(self, x, y, color, max_radius=46, life=20):
        super().__init__()
        self.pos = pygame.Vector2(x, y)
        self.color = color
        self.max_radius = max_radius
        self.life = life
        self.frame = 0
        self.image = pygame.Surface((1, 1), pygame.SRCALPHA)
        self.rect = self.image.get_rect(center=(round(x), round(y)))

    def update(self):
        self.frame += 1
        if self.frame > self.life:
            self.kill()
            return

        t = self.frame / self.life
        radius = max(2, int(self.max_radius * t))
        size = radius * 2 + 8
        c = size // 2
        alpha = int(255 * (1.0 - t) ** 1.3)

        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        # 外层冲击波圆环
        pygame.draw.circle(surf, (*self.color, alpha), (c, c), radius,
                           width=max(1, int(4 * (1 - t)) + 1))
        # 内层亮环
        pygame.draw.circle(surf, (255, 255, 255, max(0, int(alpha * 0.75))), (c, c),
                           max(1, int(radius * 0.55)), width=max(1, int(2 * (1 - t)) + 1))
        self.image = surf
        self.rect = surf.get_rect(center=(round(self.pos.x), round(self.pos.y)))


class RawController:
    """把 pygame.joystick.Joystick 包成 Controller 的接口子集（兜底通道）。

    SDL 给不出映射表（is_controller() 为假）时用它：轴/按钮号按
    「XInput 风格」的常见布局猜（0/1 = 左摇杆，0-3 = ABXY），越界或读失败
    一律返回 0.0 / False，绝不让游戏崩。
    """

    is_raw = True

    def __init__(self, joystick):
        self.joystick = joystick
        try:
            self.name = joystick.get_name()
        except Exception:
            self.name = "Raw pad"

    def get_mapping(self):
        return {}

    def get_axis(self, axis):
        try:
            return float(self.joystick.get_axis(int(axis)))
        except Exception:
            return 0.0

    def get_button(self, button):
        try:
            return bool(self.joystick.get_button(int(button)))
        except Exception:
            return False

    def quit(self):
        try:
            self.joystick.quit()
        except Exception:
            pass

    def rumble(self, *args, **kwargs):
        raise NotImplementedError        # 原始模式下没有震动通道


class Gamepad:
    """单个手柄的读数封装。

    统一走 SDL GameController 语义（pygame._sdl2.controller），
    Xbox / DualSense / 8BitDo 的原始轴号与按钮号差异由 SDL 映射表消化。
    poll() 每帧调一次（刷新按住状态与上升沿），turn()/move() 返回 -1..1。
    """

    def __init__(self, index, controller=None, joystick=None, name=None):
        self.index = index
        self.controller = controller
        self.joystick = joystick
        self.instance_id = -1
        self.mapping = {}
        self.name = name or f"Gamepad {index}"
        self.prev_fire = False
        self.prev_restart = False
        self._fire_edge = False
        self._restart_edge = False
        if self.controller is None:
            self._open(index)
        # 原始模式（RawController）：扳机轴号不可靠，默认只认按钮
        self.raw = getattr(self.controller, "is_raw", False)
        self.trigger_axes = PAD_RAW_TRIGGER_AXES if self.raw else PAD_TRIGGER_AXES

    # --- 打开 / 关闭 ---
    def _open(self, index):
        if sdl2_controller is not None:
            try:
                self.controller = sdl2_controller.Controller(index)
                self.name = self.controller.name or self.name
                self.mapping = dict(self.controller.get_mapping() or {})
            except Exception as exc:
                self.controller = None
                print(f"[pad] 打开手柄 #{index} 失败：{exc}")
            else:
                try:  # instance_id 用于“事件 → 哪台坦克”的反查，蓝牙重连后会变
                    self.joystick = self.controller.as_joystick()
                    self.instance_id = self.joystick.get_instance_id()
                except Exception:
                    self.joystick = None
                if PAD_DEBUG:
                    print(f"[pad] #{index} 已打开：{self.name} "
                          f"instance_id={self.instance_id} mapping_platform="
                          f"{self.mapping.get('platform', '?')}")
                return
        self._open_raw(index)

    def _open_raw(self, index):
        """SDL 不认这台设备时退到原始 joystick（PAD_RAW_ENABLED=0 可关）。"""
        if not PAD_RAW_ENABLED:
            return
        try:
            js = pygame.joystick.Joystick(index)
            js.init()
        except Exception as exc:
            if PAD_DEBUG:
                print(f"[pad] 原始模式打开 #{index} 失败：{exc}")
            return
        self.controller = RawController(js)
        self.joystick = js
        self.name = self.controller.name or self.name
        try:
            self.instance_id = js.get_instance_id()
        except Exception:
            self.instance_id = -1
        print(f"[pad] #{index}「{self.name}」SDL 没有映射表 → 走原始 joystick 兜底"
              f"（左摇杆 axes {PAD_AXIS_X},{PAD_AXIS_Y}，开火按钮 "
              f"{list(PAD_FIRE_BUTTONS)}）；方向不对就用 TANK_PAD_AXES 换轴")

    def close(self):
        try:
            if self.controller is not None:
                self.controller.quit()
        except Exception:
            pass
        self.controller = None

    # --- 轴 / 键读取 ---
    def _axis(self, axis):
        if self.controller is None:
            return 0.0
        try:
            raw = float(self.controller.get_axis(axis))
        except Exception:
            return 0.0
        if abs(raw) > 1.5:          # Controller.get_axis() 是 -32768..32767 的整数
            raw /= PAD_AXIS_SCALE
        return max(-1.0, min(1.0, raw))

    def _hat(self):
        """十字键（hat）兜底：部分杂牌手柄把方向放在 hat 上而不是轴上。"""
        if self.joystick is None:
            return None
        try:
            if self.joystick.get_numhats() <= 0:
                return None
            return self.joystick.get_hat(0)
        except Exception:
            return None

    def _button(self, buttons):
        if self.controller is None:
            return False
        for b in buttons:
            try:
                if self.controller.get_button(b):
                    return True
            except Exception:
                continue
        return False

    # --- 对外语义 ---
    def turn(self):
        """左摇杆 X + hat 左右：右推 = 顺时针 = 画面右转（= TURN_RIGHT 方向）。

        SDL 的 LEFTX 右推为 +，与本工程的符号约定同向，故无需取反。
        """
        v = _deadzone(self._axis(PAD_AXIS_X))
        hat = self._hat()
        if hat is not None:
            v += hat[0]
        return max(-1.0, min(1.0, v))

    def move(self):
        """左摇杆 Y 取负（SDL 向下为正）+ hat 上下，正 = 前进。"""
        v = _deadzone(-self._axis(PAD_AXIS_Y))
        hat = self._hat()
        if hat is not None:
            v += hat[1]      # SDL hat: y=+1 表示上
        return max(-1.0, min(1.0, v))

    def fire_held(self):
        """ABXY / 肩键任一按下，或模拟扳机按过 TRIGGER_ON。"""
        if self._button(PAD_FIRE_BUTTONS):
            return True
        for axis in self.trigger_axes:
            # 触发器归一化：-1..1 -> 0..1
            if (self._axis(axis) + 1.0) / 2.0 > PAD_TRIGGER_ON:
                return True
        return False

    def restart_held(self):
        return self._button(PAD_RESTART_BUTTONS)

    def poll(self):
        """每帧刷新一次；把“按住”变成“按下那一帧”的上升沿。"""
        fire = self.fire_held()
        restart = self.restart_held()
        self._fire_edge = fire and not self.prev_fire
        self._restart_edge = restart and not self.prev_restart
        self.prev_fire, self.prev_restart = fire, restart
        return self

    def fire_edge(self):
        """本帧是否是开火键的上升沿（须先 poll()）。"""
        return self._fire_edge

    def restart_edge(self):
        return self._restart_edge

    def rumble(self, ms=PAD_RUMBLE_MS, strength=0.8):
        """震动；不支持的手柄（有线猎户座青春版等）静默失败。"""
        if self.controller is None:
            return
        try:
            self.controller.rumble(strength, strength, ms)
        except (AttributeError, pygame.error, NotImplementedError, TypeError):
            pass
        except Exception:
            pass


def _wrap_deg(angle):
    """把角度折到 (-180, 180]，得到「最短该往哪边转」。"""
    return (angle + 180.0) % 360.0 - 180.0


class TankAI:
    """电脑坦克：瞄准 / 保持距离 / 开火。

    只调用 Tank 的公开接口（drive、can_fire），转向符号沿用模块顶部的
    TURN_RIGHT > 0，因此 AI、键盘、手柄共享同一份物理逻辑与同一套角度正负，
    不存在「AI 专用的第二套转向约定」。

    用法（每帧一次）：
        ai.update(tank, target)
        tank.drive(ai.turn(), ai.move())
        if ai.want_fire and tank.can_fire(): ...
    """

    def __init__(self, skill="normal", seed=None):
        self.rng = random.Random(seed)
        self.frame = 0
        self.turn_cmd = 0.0
        self.move_cmd = 0.0
        self.want_fire = False
        self._wobble = 0.0
        self._last_target_id = None
        self._last_target_pos = None
        self._target_vel = pygame.Vector2()
        self.skill = "normal"
        self.params = AI_SKILLS["normal"]
        self.set_skill(skill)

    # --- 难度（运行中可切换）---
    def set_skill(self, skill):
        key = (skill or "normal").strip().lower()
        if key not in AI_SKILLS:
            print(f"[ai] 未知难度 {skill!r}，回退 normal")
            key = "normal"
        self.skill = key
        self.params = AI_SKILLS[key]
        return key

    @property
    def label(self):
        return self.params["label"]

    def turn(self):
        return self.turn_cmd

    def move(self):
        return self.move_cmd

    # --- 每帧决策 ---
    def update(self, tank, target):
        p = self.params
        self.frame += 1
        self.want_fire = False

        if tank is None or not tank.alive or target is None or not target.alive:
            self.turn_cmd = self.move_cmd = 0.0   # 没人可打了就停车
            return

        # 每 reaction 帧重掷一次瞄准抖动：模拟人手的“手感”，而不是逐帧噪声
        if self.frame % max(1, p["reaction"]) == 1:
            self._wobble = self.rng.uniform(-p["aim_jitter"], p["aim_jitter"])

        dist = tank.pos.distance_to(target.pos)

        # 目标速度估计：位置差分（每帧一次 update，故 dt = 1 帧），做一次轻平滑。
        # 只有真的在动才有提前量，静止目标必须直瞄——否则 hard 的 lead 会永远打偏。
        if self._last_target_id == id(target) and self._last_target_pos is not None:
            self._target_vel = (self._target_vel * 0.7
                                + (target.pos - self._last_target_pos) * 0.3)
        else:
            self._target_vel = pygame.Vector2()
        self._last_target_id = id(target)
        self._last_target_pos = pygame.Vector2(target.pos)

        # 提前量：按估计速度 × 子弹飞行时间，最多提前 80px（防急转向时过冲）
        aim_x, aim_y = target.pos.x, target.pos.y
        if p["lead"] > 0 and dist > 1.0:
            offset = self._target_vel * (dist / max(1.0, BULLET_SPEED)) * p["lead"]
            if offset.length() > 80.0:
                offset.scale_to_length(80.0)
            aim_x += offset.x
            aim_y += offset.y

        # 目标角：atan2(dx, -dy) 与「0 朝上、顺时针为正」一致
        desired = math.degrees(math.atan2(aim_x - tank.pos.x,
                                          -(aim_y - tank.pos.y)))
        err = _wrap_deg(desired + self._wobble - tank.angle)
        # err > 0 => 目标在右手边 => turn > 0 = 顺时针 = 画面右转
        turn = max(-1.0, min(1.0, (err / AI_TURN_FULL_ERR) * p["turn_gain"]))

        if dist > p["keep_max"]:
            move = 1.0            # 太远：压上去
        elif dist < p["keep_min"]:
            move = -0.8           # 太近：倒车拉开
        else:
            move = 0.35 if abs(err) <= 12.0 else 0.0   # 距离合适：正对才缓推
        move *= p["speed"]

        # 贴墙自保：按当前朝向前进会撞出场地就只原地转向，别拿车头顶墙
        if move > 0 and self._would_leave_screen(tank, move):
            move = 0.0

        self.turn_cmd = turn
        self.move_cmd = move

        if (abs(err) <= p["fire_tol"] and dist <= AI_FIRE_RANGE
                and tank.can_fire() and self.rng.random() < p["fire_chance"]):
            self.want_fire = True

    @staticmethod
    def _would_leave_screen(tank, move):
        rad = math.radians(tank.angle)
        step = tank.speed * max(0.0, move) + 2.0
        nx = tank.pos.x + math.sin(rad) * step
        ny = tank.pos.y - math.cos(rad) * step
        return not (TANK_HALF <= nx <= SCREEN_WIDTH - TANK_HALF
                    and TANK_HALF <= ny <= SCREEN_HEIGHT - TANK_HALF)


def describe_joysticks():
    """列出系统里所有 HID 手柄设备及 SDL 映射状态（只读，不改全局状态）。"""
    rows = []
    try:
        count = pygame.joystick.get_count()
    except Exception as exc:
        return [{"index": -1, "name": f"<joystick 子系统不可用：{exc}>", "guid": "",
                 "is_controller": False, "axes": 0, "buttons": 0, "hats": 0,
                 "error": str(exc)}]
    for i in range(count):
        row = {"index": i, "name": "?", "guid": "", "is_controller": False,
               "axes": 0, "buttons": 0, "hats": 0, "error": None}
        try:
            js = pygame.joystick.Joystick(i)
            js.init()
            row.update(name=js.get_name(), guid=js.get_guid(),
                       axes=js.get_numaxes(), buttons=js.get_numbuttons(),
                       hats=js.get_numhats())
            js.quit()
        except Exception as exc:
            row["error"] = str(exc)
        if sdl2_controller is not None:
            try:
                row["is_controller"] = bool(sdl2_controller.is_controller(i))
            except Exception:
                pass
        rows.append(row)
    return rows


def print_pad_scan():
    """`python tank_game.py --pad-scan`：打印机柄识别结果 + 排错建议。"""
    rows = describe_joysticks()
    print("=== 手柄扫描 ===")
    print(f"pygame {pygame.version.ver} / SDL {pygame.version.SDL} / {sys.platform}")
    if sdl2_controller is None:
        print("！SDL GameController 层不可用，只能走原始 joystick 兜底")
    if not rows:
        print("没有检测到任何手柄设备（HID joystick 数量 = 0）。")
        print("- 先确认系统层面能看到它：Windows「设置 → 蓝牙和其他设备」，"
              "macOS「系统信息 → USB」")
        print("- 有线务必用数据线（不少线只能充电）；无线用 2.4G 接收器或先完成蓝牙配对")
        return
    for row in rows:
        if row["is_controller"]:
            tag = "标准映射（SDL 认，直接可用）"
        elif PAD_RAW_ENABLED:
            tag = "无 SDL 映射 → 走原始 joystick 兜底"
        else:
            tag = "无 SDL 映射，且兜底已被 TANK_PAD_RAW=0 关闭"
        print(f"#{row['index']} {row['name']}")
        print(f"    guid={row['guid']}  axes={row['axes']} buttons={row['buttons']}"
              f" hats={row['hats']}")
        print(f"    -> {tag}")
        if row["error"]:
            print(f"    打开失败：{row['error']}")
    print("\n提示：")
    print('- 摇杆方向不对 / 不动：TANK_PAD_AXES="0,1" 或 "2,3" 换轴；'
          'TANK_PAD_DEBUG=1 可看实时读数')
    print('- 开火按钮：进游戏按 F4 改（也可以 TANK_PAD_FIRE="0,1" 直接指定按钮号）')
    print('- 扳机开火（仅原始模式）：TANK_PAD_RAW_TRIGGERS="4,5"')
    print("- XInput/DInput 双模手柄（如 EasySMX X05）：PC 上优先用 XInput 模式，"
          "按住 BACK+START 约 3 秒切换，指示灯变蓝即 XInput")


def refresh_pads(pads):
    """每帧刷新 SDL 手柄状态，再让每个 Gamepad 计算上升沿。"""
    if sdl2_controller is not None:
        try:
            sdl2_controller.update()   # 不调用的话 get_axis/get_button 不会更新
        except Exception:
            pass
    for pad in pads:
        pad.poll()


def draw_background(surf):
    surf.fill(BLACK)
    for x in range(0, SCREEN_WIDTH, 50):
        pygame.draw.line(surf, (32, 32, 38), (x, 0), (x, SCREEN_HEIGHT))
    for y in range(0, SCREEN_HEIGHT, 50):
        pygame.draw.line(surf, (32, 32, 38), (0, y), (SCREEN_WIDTH, y))


def draw_health_bar(surf, tank):
    if not tank.alive:
        return
    w, h = 40, 6
    x = round(tank.pos.x - w / 2)
    y = max(2, round(tank.pos.y - TANK_HALF - 14))
    pygame.draw.rect(surf, (28, 28, 34), (x - 1, y - 1, w + 2, h + 2), border_radius=3)
    ratio = tank.hp / MAX_HP
    if ratio > 0.6:
        color = (90, 210, 120)
    elif ratio > 0.3:
        color = (230, 200, 70)
    else:
        color = (230, 90, 80)
    if ratio > 0:
        pygame.draw.rect(surf, color, (x, y, max(2, int(w * ratio)), h), border_radius=2)


def lives_pips_width(size=8, gap=4):
    """一排命数小方块的宽度（HUD 右对齐要用）。"""
    return MAX_LIVES * (size + gap) - gap


def draw_lives(surf, tank, x, y, size=8, gap=4):
    """剩余命数：一排小方块，还有的命用队伍色，扣掉的用暗色。"""
    for i in range(MAX_LIVES):
        color = tank.color if i < tank.lives else (52, 52, 60)
        pygame.draw.rect(surf, color, (x + i * (size + gap), y, size, size),
                         border_radius=2)


def _parse_mode():
    """决定开局模式与 AI 难度。

    优先级：命令行参数 > 环境变量 > 默认（2p，保持原有双人对战行为）。
        python3.11 tank_game.py --1p --ai=hard
        TANK_MODE=1p TANK_AI=easy python3.11 tank_game.py
    运行中还能用 F2 切模式、F3 切难度。
    """
    mode = os.environ.get("TANK_MODE", "2p")
    skill = os.environ.get("TANK_AI", "normal")
    for arg in sys.argv[1:]:
        a = arg.strip().lower()
        if a in ("--1p", "-1", "--single", "--cpu", "--ai"):
            mode = "1p"
        elif a in ("--2p", "-2", "--dual", "--versus"):
            mode = "2p"
        elif a.startswith("--mode="):
            mode = a.split("=", 1)[1]
        elif a.startswith("--ai="):
            skill = a.split("=", 1)[1]

    mode = "1p" if mode.strip().lower() in ("1p", "1", "single", "cpu", "ai") else "2p"
    skill = skill.strip().lower()
    if skill not in AI_SKILLS:
        print(f"[ai] 未知难度 {skill!r}，用 normal")
        skill = "normal"
    return mode, skill


def main():
    args = [a.strip().lower() for a in sys.argv[1:]]
    if "--pad-scan" in args or os.environ.get("TANK_PAD_SCAN") == "1":
        print_pad_scan()      # 只诊断不游戏，排手柄问题用
        return
    if "--coach-history" in args:
        # 只看历史战绩（不开窗口、不初始化对局），用来判断自己有没有在进步
        print(tank_coach.history_text(tank_coach.load_history()))
        return
    mode, ai_skill = _parse_mode()
    # 攻击键配置：启动即读（读不到/写坏都回退默认），手柄开火按钮也一起生效
    fire_codes, fire_names, pad_fire_cfg = load_fire_config()
    if pad_fire_cfg:
        set_pad_fire_buttons(pad_fire_cfg)
    if mode == "1p":
        print(f"[mode] 单人 vs 电脑（难度 {AI_SKILLS[ai_skill]['label']}）；"
              f"F2 切双人，F3 切难度，F4 改开火键")
    else:
        print("[mode] 双人对战；F2 切单人 vs 电脑，F4 改开火键")
    print(f"[keys] P1 开火 = {fire_names['p1'].upper()}，"
          f"P2 开火 = {fire_names['p2'].upper()}，手柄开火 = {pad_fire_label()}"
          f"（配置文件 {KEYS_CONFIG_PATH}）")
    print(f"[rule] 每人 {MAX_LIVES} 条命：血条打空算被击毁一次，"
          f"{RESPAWN_DELAY / FPS:.1f} 秒后出生点复活，"
          f"复活后 {SPAWN_INVULN / FPS:.1f} 秒无敌；命扣完才算输")
    if COACH_ENABLED:
        print(f"[coach] 1p 模式下每局自动评估：局末打印评分/纠正/下一局目标，"
              f"F5 开实时面板，历史写入 {os.path.basename(tank_coach.COACH_HISTORY_PATH)}"
              f"（TANK_COACH=0 关闭）")
    else:
        print("[coach] 评估已关闭（TANK_COACH=0）")
    font = pygame.font.SysFont(None, 22)
    small_font = pygame.font.SysFont(None, 18)
    big_font = pygame.font.SysFont(None, 56)

    world = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
    tanks = pygame.sprite.Group()
    bullets = pygame.sprite.Group()
    explosions = pygame.sprite.Group()

    # --- 手柄设备管理（就地修改 pads 列表，闭包内外共享同一份）---
    pads = []

    def rebuild_pads():
        """按当前连接的设备重建 pads：SDL 认的优先，其余走原始 joystick 兜底。"""
        wanted = []
        if sdl2_controller is not None:
            try:
                count = sdl2_controller.get_count()
            except Exception:
                count = 0
            for i in range(count):
                try:
                    if sdl2_controller.is_controller(i):
                        wanted.append(i)
                except Exception:
                    continue
        if PAD_RAW_ENABLED:
            try:
                joy_count = pygame.joystick.get_count()
            except Exception:
                joy_count = 0
            for i in range(joy_count):
                if i in wanted:
                    continue
                if sdl2_controller is not None:
                    try:
                        if sdl2_controller.is_controller(i):
                            continue      # 已作为标准手柄收进 wanted 了
                    except Exception:
                        pass
                wanted.append(i)          # 排标准手柄后面，P1 仍是第一只标准手柄
        keep = {pad.index: pad for pad in pads}
        fresh = []
        for i in wanted:
            pad = keep.pop(i, None)
            if pad is None or pad.controller is None:
                pad = Gamepad(i)          # 蓝牙重连后重新打开，index 会变
            if pad.controller is not None:
                fresh.append(pad)
        for pad in keep.values():
            pad.close()
        pads[:] = fresh
        if PAD_DEBUG:
            print(f"[pad] 重新扫描：{len(pads)} 个标准手柄 {[p.name for p in pads]}")
        return pads

    def bind_pads():
        """按接入顺序绑定槽位：P1 = 红 = 第一只，P2 = 蓝 = 第二只。

        单人模式下 P2 是电脑，第二只手柄不参与（留给切回双人时用）。
        """
        tank1.pad = pads[0] if len(pads) >= 1 else None
        if tank2.ai is None and len(pads) >= 2:
            tank2.pad = pads[1]
        else:
            tank2.pad = None

    def tank_of_pad(instance_id):
        """按 instance_id 反查坦克（不按 index 长期缓存：重连后 index 会变）。"""
        if instance_id in (-1, None):
            return None
        for tank in (tank1, tank2):
            pad = tank.pad
            if pad is not None and pad.instance_id == instance_id:
                return tank
        return None

    def reset():
        tanks.empty()
        bullets.empty()
        explosions.empty()
        report_cache.clear()
        toast["text"], toast["frames"] = "", 0
        t1 = Tank(
            120, 120, RED,
            {"forward": pygame.K_w, "back": pygame.K_s,
             "left": pygame.K_a, "right": pygame.K_d,
             "fire": fire_codes["p1"]},          # 开火键来自配置（默认 SPACE）
        )
        t2 = Tank(
            SCREEN_WIDTH - 120, SCREEN_HEIGHT - 120, BLUE,
            {"forward": pygame.K_UP, "back": pygame.K_DOWN,
             "left": pygame.K_LEFT, "right": pygame.K_RIGHT,
             "fire": fire_codes["p2"]},          # 开火键来自配置（默认 ENTER）
        )
        if mode == "1p":
            # 单人模式：蓝车交给电脑，P1 可以用键盘或手柄
            t2.ai = TankAI(ai_skill, seed=random.randrange(1 << 30))
        # 重开时手柄绑定跟着新坦克走（重开后双人不用重插）
        t1.pad = pads[0] if len(pads) >= 1 else None
        t2.pad = pads[1] if (t2.ai is None and len(pads) >= 2) else None
        tanks.add(t1, t2)
        # 评估器：每局一个（F2 切模式 / R 重开都跟着换），只在 1p 模式下存在
        nonlocal coach
        coach = None
        if mode == "1p" and COACH_ENABLED:
            coach = tank_coach.BattleCoach(
                fps=FPS, fire_range=AI_FIRE_RANGE, fire_cooldown=FIRE_COOLDOWN,
                invuln_frames=SPAWN_INVULN, max_lives=MAX_LIVES)
            coach.begin_round(AI_SKILLS[ai_skill]["label"])
        return t1, t2

    # 评估器与它的历史文件；report_cache 是局末那份屏幕报告（避免每帧重算）
    coach = None
    coach_hist = tank_coach.load_history()
    report_cache = []
    toast = {"text": "", "frames": 0}
    coach_panel = False      # F5：实时评估面板

    tank1, tank2 = reset()
    rebuild_pads()   # 覆盖“启动时手柄已经插好”的情况
    bind_pads()
    winner = None
    shake = 0.0

    def try_fire(tank):
        """键盘与手柄共用的开火逻辑（冷却由 Tank.fire 内部控制）。"""
        if not tank.can_fire():
            return False
        bullet = tank.fire()
        bullets.add(bullet)
        muzzle = tank.muzzle_position()
        explosions.add(Explosion(muzzle.x, muzzle.y, YELLOW, 16, 7))
        if coach is not None:
            if tank is tank1:
                coach.on_shot(tank, tank2, bullet)   # 记下开枪那一刻的距离/准度
            else:
                coach.on_enemy_shot()
        return True

    def finish_round(won=None):
        """一局结束：终端打详细评估 → 写历史 → 缓存屏幕报告。"""
        if coach is None:
            return []
        print(coach.report_text(coach_hist))
        ok, info = tank_coach.record_round(coach_hist, coach.history_row(won=won))
        if not ok:
            print(f"[coach] 历史写入失败：{info}")
        else:
            print(f"[coach] 本局已写入 {os.path.basename(str(info))}"
                  f"（共 {len(coach_hist.get('rounds', []))} 局）")
        report_cache[:] = coach.report_lines(coach_hist, compact=True)
        return report_cache

    def tank_label(tank):
        """日志用的人话标签（1p 模式下蓝车是电脑）。"""
        if tank is tank1:
            return "P1 (red)"
        return "CPU (blue)" if tank.ai is not None else "P2 (blue)"

    # --- 攻击键配置面板（F4）---
    # slot: None = 关闭；"menu" = 选槽位；"p1"/"p2" = 等键盘；"pad" = 等手柄按钮
    bind_state = {"slot": None, "msg": ""}

    def set_msg(text):
        bind_state["msg"] = text

    def apply_fire_key(slot, keycode):
        """把某个槽位换成 keycode；校验 → 立即生效 → 立刻落盘。"""
        other = "p2" if slot == "p1" else "p1"
        name = key_code_to_name(keycode)
        code, err = check_fire_key(name, fire_names[other])
        if err:
            set_msg(f"Rejected: {err}")
            print(f"[keys] 拒绝 {slot.upper()} 开火键 {name!r}：{err}")
            return False
        old = fire_names[slot]
        fire_names[slot] = key_code_to_name(code)
        fire_codes[slot] = code
        target = tank1 if slot == "p1" else tank2
        if target is not None and target.controls:
            target.controls["fire"] = code       # 不用重开，当前这局马上生效
        ok, info = save_fire_config(fire_names, PAD_FIRE_BUTTONS)
        set_msg(f"{slot.upper()} fire = {fire_names[slot].upper()} (was {old.upper()})"
                + (f" - saved {os.path.basename(str(info))}" if ok
                   else f" - save failed: {info}"))
        print(f"[keys] {slot.upper()} 开火键 → {fire_names[slot].upper()}"
              f"（原 {old.upper()}）"
              f"{'，已保存到 ' + str(info) if ok else '，保存失败：' + str(info)}")
        return True

    def apply_pad_fire(button):
        """手柄槽位：只认按下的那个按钮（RT/LT 扳机始终保留为开火）。"""
        try:
            btn = int(button)
        except (TypeError, ValueError):
            set_msg(f"Rejected: bad button {button!r}")
            return False
        if not 0 <= btn < PAD_BUTTON_MAX:
            set_msg(f"Rejected: button {btn} out of range 0..{PAD_BUTTON_MAX - 1}")
            return False
        set_pad_fire_buttons((btn,))
        ok, info = save_fire_config(fire_names, PAD_FIRE_BUTTONS)
        set_msg(f"Pad fire = {pad_fire_label()}"
                + (f" - saved {os.path.basename(str(info))}" if ok
                   else f" - save failed: {info}"))
        print(f"[keys] 手柄开火键 → {pad_fire_label()}"
              f"{'，已保存' if ok else '，保存失败：' + str(info)}")
        bind_state["slot"] = "menu"
        return True

    def restore_default_fire_keys():
        for slot in FIRE_SLOTS:
            fire_names[slot] = FIRE_KEY_DEFAULTS[slot]
            fire_codes[slot] = (key_name_to_code(FIRE_KEY_DEFAULTS[slot])
                                or pygame.K_SPACE)
        tank1.controls["fire"] = fire_codes["p1"]
        tank2.controls["fire"] = fire_codes["p2"]
        set_pad_fire_buttons(PAD_FIRE_DEFAULT_BUTTONS)
        ok, info = save_fire_config(fire_names, PAD_FIRE_BUTTONS)
        set_msg("Defaults restored" + ("" if ok else f" (save failed: {info})"))
        print(f"[keys] 已恢复默认开火键：P1 SPACE / P2 ENTER / 手柄 {pad_fire_label()}"
              f"{'，已保存' if ok else '，保存失败：' + str(info)}")

    def handle_config_key(key, slot):
        """面板按键分发：这期间所有按键都归面板，不会再触发开火。"""
        if key in (pygame.K_ESCAPE, pygame.K_F4):
            bind_state["slot"] = None
            set_msg("")
            return
        if slot == "menu":
            if key in (pygame.K_1, pygame.K_KP1):
                bind_state["slot"] = "p1"
                set_msg("Press new P1 fire key  (ESC cancel)")
            elif key in (pygame.K_2, pygame.K_KP2):
                bind_state["slot"] = "p2"
                set_msg("Press new P2 fire key  (ESC cancel)")
            elif key in (pygame.K_3, pygame.K_KP3):
                bind_state["slot"] = "pad"
                set_msg("Press the gamepad button to use for fire  (ESC cancel)")
            elif key in (pygame.K_0, pygame.K_KP0):
                restore_default_fire_keys()
            return
        if slot == "pad":
            return           # 手柄槽位只认 PAD_BUTTON_EVENTS，键盘按下不算
        if apply_fire_key(slot, key):
            bind_state["slot"] = "menu"

    running = True
    clock = pygame.time.Clock()

    while running:
        # --- 事件 ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type in PAD_ADDED_EVENTS:
                rebuild_pads()
                bind_pads()
                print(f"[pad] 已接入：{[p.name for p in pads]}（共 {len(pads)} 只）")
            elif event.type in PAD_REMOVED_EVENTS:
                rebuild_pads()
                bind_pads()
                print(f"[pad] 已断开，剩余 {len(pads)} 只")
            elif event.type in PAD_BUTTON_EVENTS and bind_state["slot"] == "pad":
                # 配置面板的手柄槽位：按下哪个按钮就把它当开火键
                apply_pad_fire(getattr(event, "button", None))
            elif PAD_DEBUG and event.type in PAD_BUTTON_EVENTS:
                print(f"[pad] {pygame.event.event_name(event.type)} "
                      f"joy={getattr(event, 'joy', '?')} "
                      f"button={getattr(event, 'button', '?')} "
                      f"which_tank={tank_of_pad(getattr(event, 'instance_id', -1))}")
            elif event.type == pygame.KEYDOWN:
                if bind_state["slot"] is not None:
                    handle_config_key(event.key, bind_state["slot"])
                elif event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_F4:
                    # F4：打开攻击键配置面板（这期间对战暂停，按键全归面板）
                    bind_state["slot"] = "menu"
                    set_msg("Pick 1/2/3 to rebind, 0 to reset, ESC to close")
                    print(f"[keys] 打开攻击键配置：P1={fire_names['p1'].upper()} "
                          f"P2={fire_names['p2'].upper()} 手柄={pad_fire_label()}")
                elif event.key == pygame.K_F2:
                    # F2：双人 <-> 单人 vs 电脑（切换即重开一局）
                    mode = "2p" if mode == "1p" else "1p"
                    tank1, tank2 = reset()
                    bind_pads()
                    winner = None
                    shake = 0.0
                    print("[mode] 已切换为 " +
                          ("单人 vs 电脑" if mode == "1p" else "双人对战"))
                elif event.key == pygame.K_F3:
                    # F3：EASY -> NORMAL -> HARD 循环（单人模式才有效果）
                    ai_skill = AI_SKILL_ORDER[
                        (AI_SKILL_ORDER.index(ai_skill) + 1) % len(AI_SKILL_ORDER)]
                    if tank2.ai is not None:
                        tank2.ai.set_skill(ai_skill)
                    if coach is not None:
                        coach.skill_label = AI_SKILLS[ai_skill]["label"]
                    print(f"[ai] 难度 → {AI_SKILLS[ai_skill]['label']}")
                elif event.key == pygame.K_F5:
                    # F5：实时评估面板（局末报告不受它影响，总会显示）
                    coach_panel = not coach_panel
                    if coach is None:
                        print("[coach] 双人模式没有评估基准，切到 1p（F2）才有")
                    else:
                        print(f"[coach] 实时面板 {'开' if coach_panel else '关'}"
                              f"（本局累计到第 {coach.metrics()['frames']} 帧）")
                elif winner is not None:
                    if event.key == pygame.K_r:
                        tank1, tank2 = reset()
                        winner = None
                        shake = 0.0
                else:
                    for tank in (tank1, tank2):
                        if tank.ai is not None or not tank.controls:
                            continue          # 电脑坦克不听键盘
                        if event.key == tank.controls["fire"]:
                            try_fire(tank)

        # --- 更新 ---
        keys = pygame.key.get_pressed()
        refresh_pads(pads)
        # 配置面板打开时暂停对战（按键全归面板），但照常渲染，方便边看边改
        paused = bind_state["slot"] is not None

        if not paused:
            # 手柄 Start/Back 重开（用上升沿，按住不会反复重开）
            if winner is not None and any(pad.restart_edge() for pad in pads):
                tank1, tank2 = reset()
                winner = None
                shake = 0.0

            for tank in (tank1, tank2):
                tank.tick()
                if winner is not None:
                    continue
                if not tank.alive:
                    # 被击毁但还有命：复活倒计时走完就回出生点（同一台坦克对象）
                    if tank.respawn_timer > 0:
                        tank.respawn_timer -= 1
                        if tank.respawn_timer == 0:
                            tank.respawn()
                            tanks.add(tank)
                            explosions.add(Explosion(tank.pos.x, tank.pos.y,
                                                     tank.color, 40, 16))
                            print(f"[life] {tank_label(tank)} 复活，"
                                  f"剩 {tank.lives} 条命（{SPAWN_INVULN} 帧无敌）")
                    continue
                if tank.ai is not None:
                    # 电脑坦克：决策 -> 走同一条 drive() 通道（转向符号不变）
                    tank.ai.update(tank, tank1 if tank is tank2 else tank2)
                    tank.drive(tank.ai.turn(), tank.ai.move())
                    if tank.ai.want_fire:
                        try_fire(tank)
                else:
                    # 键盘 + 手柄合成，无手柄时 = handle_input
                    tank.poll_controls(keys)
                    if tank.pad is not None and tank.pad.fire_edge():
                        try_fire(tank)
                    if coach is not None and tank is tank1:
                        # 开火键是否还按着：判断「按住不放」这种最常见的误用
                        try:
                            held = bool(keys[tank.controls["fire"]])
                        except (KeyError, IndexError):
                            held = False
                        held = held or (tank.pad is not None and tank.pad.fire_held())
                        coach.observe(tank, tank2, held)
                        hint = coach.new_hint()
                        if hint:
                            toast["text"], toast["frames"] = hint[1], 150
                            print(f"[coach] 即时提示：{hint[1]}")

            bullets.update()
            explosions.update()

            # 子弹命中判定
            for bullet in bullets.sprites():
                for tank in (tank1, tank2):
                    if tank is bullet.owner or not tank.alive:
                        continue
                    if bullet.rect.colliderect(tank.rect):
                        hit_point = pygame.Vector2(bullet.pos)
                        bullet.kill()
                        explosions.add(Explosion(hit_point.x, hit_point.y, YELLOW, 26, 14))
                        shake = max(shake, 5.0)
                        shielded = tank.invuln > 0        # 复活无敌：这一发不掉血
                        if coach is not None:
                            coach.on_hit(by_human=(bullet.owner is tank1),
                                         shield=shielded, bullet=bullet)
                        if tank.take_hit(DAMAGE):
                            if tank.pad is not None:
                                tank.pad.rumble(PAD_RUMBLE_MS, 0.8)   # 击毁时震一下
                            explosions.add(Explosion(tank.pos.x, tank.pos.y,
                                                     tank.color, 72, 28))
                            shake = max(shake, 15.0)
                            tank.kill()  # 移出精灵组，不再绘制
                            # 生命值：扣一条命，还有命就等复活，扣完才分胜负
                            if coach is not None:
                                # 放在 lose_life() 之前：最后一条命也算进统计
                                coach.on_death(victim_is_human=(tank is tank1))
                            if tank.lose_life():
                                print(f"[life] {tank_label(tank)} 被击毁，"
                                      f"剩 {tank.lives} 条命，"
                                      f"{RESPAWN_DELAY / FPS:.1f} 秒后复活")
                            elif mode == "1p":
                                winner = "YOU WIN!" if tank is tank2 else "CPU WINS!"
                                print(f"[life] {tank_label(tank)} 命已用尽，本局结束")
                                finish_round(won=(tank is tank2))
                            else:
                                winner = ("P1 (red) wins!" if tank is tank2
                                          else "P2 (blue) wins!")
                                print(f"[life] {tank_label(tank)} 命已用尽，本局结束")
                        break

            shake = max(0.0, shake - 0.9)
            if toast["frames"] > 0:
                toast["frames"] -= 1

        for tank in (tank1, tank2):
            tank.sync_image()

        # --- 绘制 ---
        draw_background(world)
        tanks.draw(world)
        bullets.draw(world)
        explosions.draw(world)
        for tank in (tank1, tank2):
            draw_health_bar(world, tank)
            if tank.alive and tank.invuln > 0:
                # 复活无敌：车体外画一圈白环当提示
                pygame.draw.circle(world, (210, 210, 225),
                                   (round(tank.pos.x), round(tank.pos.y)),
                                   TANK_HALF + 6, 2)
            elif not tank.alive and winner is None and tank.respawn_timer > 0:
                # 等待复活：出生点上显示倒计时
                secs = tank.respawn_timer / FPS
                tip = small_font.render(f"RESPAWN {secs:.1f}s", True, tank.color)
                world.blit(tip, tip.get_rect(center=(round(tank.spawn_point.x),
                                                     round(tank.spawn_point.y))))

        screen.fill(BLACK)
        if shake > 0:
            offset = (random.randint(-int(shake), int(shake)),
                      random.randint(-int(shake), int(shake)))
        else:
            offset = (0, 0)
        screen.blit(world, offset)

        # 攻击键配置面板（F4）盖在最上层
        if bind_state["slot"] is not None:
            draw_key_config(screen, font, small_font, fire_names,
                            bind_state["slot"], bind_state["msg"])

        # 操作提示（开火键显示实际配置，改完全场立刻同步）
        hint1 = font.render(f"P1: WASD / {fire_names['p1'].upper()}", True,
                            (200, 120, 120))
        if mode == "1p":
            ai_label = tank2.ai.label if tank2.ai is not None else AI_SKILLS[ai_skill]["label"]
            hint2 = font.render(f"CPU (blue): {ai_label}", True, (120, 150, 220))
        else:
            hint2 = font.render(f"P2: Arrows / {fire_names['p2'].upper()}", True,
                                (120, 150, 220))
        screen.blit(hint1, (12, 10))
        screen.blit(hint2, (SCREEN_WIDTH - hint2.get_width() - 12, 10))

        # 模式提示（F2 切模式 / F3 切难度 / F4 改开火键，随时可按）
        mode_text = (f"Mode: 1P vs CPU - F2 switch, F3 AI level "
                     f"({AI_SKILLS[ai_skill]['label']}), F4 fire keys, F5 coach"
                     if mode == "1p" else
                     "Mode: 2P duel - F2 play vs CPU, F4 fire keys")
        mode_hint = small_font.render(mode_text, True, GRAY)
        screen.blit(mode_hint, mode_hint.get_rect(center=(SCREEN_WIDTH // 2,
                                                         SCREEN_HEIGHT - 38)))

        # 手柄状态（放屏幕底部居中，避开中心的胜负提示）
        if pads:
            raw_n = sum(1 for p in pads if p.raw)
            pad_text = (f"Pads: {len(pads)} connected"
                        + (f" ({raw_n} raw mode)" if raw_n else "")
                        + f" - L-stick drive, {pad_fire_label()} fire, "
                          "Start restart")
        else:
            pad_text = "No gamepad - plug in a controller (auto-detect)"
        pad_hint = small_font.render(pad_text, True, GRAY)
        screen.blit(pad_hint, pad_hint.get_rect(center=(SCREEN_WIDTH // 2,
                                                       SCREEN_HEIGHT - 20)))

        # 血量数值 + 剩余命数（一排小方块，扣掉的变暗）
        hp1 = small_font.render(f"HP {tank1.hp}", True, (200, 120, 120))
        hp2 = small_font.render(f"HP {tank2.hp}", True, (120, 150, 220))
        screen.blit(hp1, (12, 34))
        screen.blit(hp2, (SCREEN_WIDTH - hp2.get_width() - 12, 34))
        draw_lives(screen, tank1, 12, 54)
        draw_lives(screen, tank2,
                   SCREEN_WIDTH - 12 - lives_pips_width(), 54)

        # 胜负提示
        if winner:
            text = big_font.render(winner, True, WHITE)
            sub = font.render("R / Start restart   F2 1P-2P   F3 AI level   "
                              "F4 fire keys   F5 coach   ESC quit", True, GRAY)
            screen.blit(text, text.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 20)))
            screen.blit(sub, sub.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 26)))

        # 表现评估：局末报告卡（左下，避开中央的胜负提示） / F5 实时面板
        if coach is not None:
            if winner is not None and report_cache:
                h = 32 + 18 * len(report_cache)
                draw_coach_card(screen, font, small_font, "BATTLE REPORT",
                                report_cache, 12, max(70, SCREEN_HEIGHT - 34 - h),
                                width=404)
            elif coach_panel and winner is None:
                draw_coach_card(screen, font, small_font,
                                f"COACH (F5) - {coach.skill_label}",
                                coach.hud_lines(), 12, 76, width=300)

        # 即时纠正提示（新触发的误用只在屏幕上挂 2.5 秒，不打断对局）
        if toast["frames"] > 0 and toast["text"]:
            tip = small_font.render(_fit_text(small_font, "COACH: " + toast["text"], 560),
                                    True, (250, 196, 130))
            bg = pygame.Surface((tip.get_width() + 16, tip.get_height() + 8),
                                pygame.SRCALPHA)
            bg.fill((12, 12, 16, 210))
            screen.blit(bg, bg.get_rect(center=(SCREEN_WIDTH // 2, 74)))
            screen.blit(tip, tip.get_rect(center=(SCREEN_WIDTH // 2, 74)))

        pygame.display.flip()
        clock.tick(FPS)

    for pad in pads:
        pad.close()
    if sdl2_controller is not None:
        try:
            sdl2_controller.quit()
        except Exception:
            pass
    pygame.joystick.quit()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
