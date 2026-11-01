import json
import math
import os
import random
import sys
import time

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


# --- 字体：屏幕上要画中文，必须自己找一款带汉字字形的字体 ---
# pygame.font.SysFont(None, ...) 用的是自带的 freesansbold.ttf，只有拉丁字形，
# 汉字一律被画成同一个 5px 宽的方块——屏幕上看起来就是「一串乱码」。
# 这里按「TANK_FONT 环境变量 → 各平台常见字体文件 → 字体名」的顺序找，判定不是
# 看名字而是**真的渲染两个字比较像素**：能区分开才算有汉字字形（.ttc 的第一张
# 字面常常只有拉丁字母，光看名字会踩空）。都找不到就退回英文界面
# （tank_coach.ASCII_ONLY = True），宁可全英文也不再画方块。
FONT_FILE_CANDIDATES = (
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    # Windows
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyh.ttf",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    # Linux（发行版自带的 CJK 字体）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
)
FONT_NAME_CANDIDATES = ("pingfangsc", "hiraginosansgb", "microsoftyahei", "simhei",
                        "notosanscjksc", "notosanscjk", "sourcehansanssc",
                        "wenquanyimicrohei", "wenquanyizenhei", "arialunicodems",
                        "heitisc", "songtisc")
# 用来判定「有没有汉字字形」的样字：三个字形互不相同才算数
_CJK_PROBE = "命中率"

_CJK_FONT = None
_CJK_FONT_TRIED = False
_FONT_CACHE = {}

try:                        # freetype 对 .ttc（macOS 系统字体）最可靠
    from pygame import freetype as _freetype

    _freetype.init()
except Exception:           # pragma: no cover - 精简版 pygame 可能没带 freetype
    _freetype = None


class ScreenFont:
    """屏幕字体：对外只暴露 pygame.font.Font 的 render() / size()。

    内部优先用 pygame.freetype。原因很实在：这台机器上 pygame.font 读
    `/System/Library/Fonts/Hiragino Sans GB.ttc` 时，连字母 A 都会被画成
    一整块实心方块，而 freetype 读同一个文件完全正常。两种后端都包装成
    同一个接口，调用点（_fit_text / draw_coach_card / HUD）不用区分。
    """

    def __init__(self, ft_font):
        self._ft = ft_font
        self._sizes = {}

    def size(self, text):
        text = str(text)
        size = self._sizes.get(text)
        if size is None:
            if not text:
                size = (0, 0)
            else:
                try:
                    rect = self._ft.get_rect(text)
                    size = (rect.width, rect.height or self._ft.get_sized_height())
                except Exception:
                    size = (0, 0)
            if len(self._sizes) < 4096:      # _fit_text 会逐字试宽，缓存一下
                self._sizes[text] = size
        return size

    def render(self, text, antialias=False, color=(255, 255, 255), background=None):
        text = str(text)
        if not text:
            surf = pygame.Surface((1, 1), pygame.SRCALPHA)
            return surf
        try:
            surf, _ = self._ft.render(text, fgcolor=tuple(color),
                                      bgcolor=None if background is None
                                      else tuple(background))
            return surf
        except Exception:
            return pygame.Surface((1, 1), pygame.SRCALPHA)

    def set_bold(self, flag=True):
        try:
            self._ft.strong = bool(flag)
        except Exception:
            pass


def _make_font(kind, value, size, bold=False):
    """按来源造一个字体对象；都失败时返回 None。"""
    if _freetype is not None:
        path = value if kind == "file" else pygame.font.match_font(value)
        if path:
            try:
                ft = _freetype.Font(path, size)
                ft.antialiased = True
                if bold:
                    ft.strong = True
                return ScreenFont(ft)
            except Exception:
                pass
    try:
        font = (pygame.font.Font(value, size) if kind == "file"
                else pygame.font.SysFont(value, size))
        if bold:
            font.set_bold(True)
        return font
    except Exception:
        return None


def _font_has_cjk(font):
    """字体真的能画出不同的汉字吗？

    两条都满足才算：「命中率」三个字各有各的字形（不是同一个 .notdef），
    而且不是空白（缺字时 freetype 会画出空 surface）。
    """
    if font is None:
        return False
    shots = []
    for ch in _CJK_PROBE:
        try:
            raw = pygame.image.tostring(font.render(ch, False, (255, 255, 255)), "RGB")
        except Exception:
            return False
        if not any(raw):        # 全黑 = 什么都没画出来
            return False
        shots.append(raw)
    return len(set(shots)) == len(shots)


def _font_source():
    """找一款带汉字的字体，返回 ('file', 路径) / ('name', 名字) / None。"""
    global _CJK_FONT, _CJK_FONT_TRIED
    if _CJK_FONT_TRIED:
        return _CJK_FONT
    _CJK_FONT_TRIED = True
    cands = []
    env_font = os.environ.get("TANK_FONT")
    if env_font:
        cands.append(("file" if os.path.exists(env_font) else "name", env_font))
    cands += [("file", p) for p in FONT_FILE_CANDIDATES]
    cands += [("name", n) for n in FONT_NAME_CANDIDATES]
    for kind, value in cands:
        if kind == "file" and not os.path.exists(value):
            continue
        if _font_has_cjk(_make_font(kind, value, 20)):
            _CJK_FONT = (kind, value)
            return _CJK_FONT
    if env_font:
        print(f"[font] TANK_FONT={env_font!r} 没有汉字字形，已忽略")
    return None


def cjk_font_available():
    """界面上能不能安全地画中文（False 时屏幕文案走英文）。"""
    return _font_source() is not None


def load_font(size, bold=False):
    """带汉字字形的字体（同尺寸复用同一个对象）；找不到就退回默认字体。"""
    key = (int(size), bool(bold))
    font = _FONT_CACHE.get(key)
    if font is not None:
        return font
    src = _font_source()
    font = _make_font(src[0], src[1], size, bold) if src else None
    if font is None:
        font = pygame.font.SysFont(None, size, bold=bold)
    _FONT_CACHE[key] = font
    return font

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

# --- 键盘转向的「点按微转」---
# 满舵转速 = Tank.turn_speed（默认 4°/帧 @FPS=60，即 240°/s）。问题是人手
# 一次轻敲持续 3~6 帧 = 12~24°，而敌人的方向是 atan2 算出来的连续实数：
# 只按一下就越过目标 4° 的整数倍格点，于是永远只能「过冲 -> 反向过冲」。
# 这里让键盘起手那一帧只给 TURN_FINE_GAIN 的增益（4°/帧 × 0.25 = 1°/帧），
# 同向按住 TURN_RAMP_FRAMES 帧后线性升到满舵：点一下走 1°，甩炮口按住不放
# 时速度和以前完全一样。手柄摇杆本来就是连续量，不走这条加速曲线。
# 附带效果：可达角度从「起点 + 4k°」变成可以停在任意角度附近。
TURN_FINE_GAIN = 0.25      # 起手一帧的转向增益（满舵的 1/4 = 1°/帧）
TURN_RAMP_FRAMES = 8.0     # 从起手增益线性加到满舵所需的帧数（@60fps ≈ 0.13 秒）
TURN_SPEED_DEG_PER_SEC = 240.0   # 满舵转速 = 4°/帧 × 60fps，文档/换算用

# 战斗参数
MAX_HP = 100
DAMAGE = 10            # 每发扣 10 血 => 10 发击毁一次（一条命约 7 秒）
# 生命值：每台坦克 5 条命。血条被打空算「被击毁一次」，扣一条命；
# 还有命就在出生点复活，命扣完才算输掉这一局。
MAX_LIVES = 5
RESPAWN_DELAY = 60     # 被击毁到复活的等待帧数（60 帧 = 1 秒）
SPAWN_INVULN = 120     # 复活后的无敌帧数（2 秒），避免刚出生就被补枪
FIRE_COOLDOWN = 18     # 冷却帧数，约 0.3 秒
BULLET_SPEED = 9.0
BULLET_RADIUS = 4
# 命中判定用「车体中心 + 子弹中心」的圆，而不是旋转后贴图的包围盒：
# 44px 贴图转到 45° 时包围盒会涨到 62×62（再外扩子弹就是 78×78），
# 视觉上明明擦着车边飞过去也算命中。半径取 TANK_HALF - 4 = 18，
# 加上子弹半径 4 => 中心距 22px 以内算命中（车体视觉半宽 13 / 半高 17）。
HIT_RADIUS = TANK_HALF - 4
MUZZLE_OFFSET = TANK_HALF + 3
# 复活点候选：四个角，哪个离对手最远就去哪（全是远离边线的安全位置）
RESPAWN_CORNERS = ((120, 120), (SCREEN_WIDTH - 120, 120),
                   (120, SCREEN_HEIGHT - 120),
                   (SCREEN_WIDTH - 120, SCREEN_HEIGHT - 120))

# --- 障碍物（掩体）/ 多地图 ---
# 每张图的 rects 都必须满足三条硬约束（`_selftest_walls.py` 对非空图逐张断言）：
#   1) 180° 旋转对称：每块绕屏幕中心转 180° 后仍在集合里。
#   2) 不压出生点 / 复活角：到 RESPAWN_CORNERS 的距离 >= TANK_HALF + 40。
#   3) 不挡两条出生对角线：开局第一枪视线必须通。
# 另外 min(w, h) >= BULLET_SPEED * 2。TANK_WALLS=0 时所有地图都建成空组。
# 切图只在「等待开始」界面（键盘 M / 手柄 RB+Y）；开打后本局锁定。
OBSTACLE_ENABLED = os.environ.get("TANK_WALLS", "1") != "0"
OBSTACLE_SIZE = 56
_MAP_PILLARS = ((240, 120, OBSTACLE_SIZE, OBSTACLE_SIZE),
                (240, 300, OBSTACLE_SIZE, OBSTACLE_SIZE),
                (504, 244, OBSTACLE_SIZE, OBSTACLE_SIZE),
                (504, 424, OBSTACLE_SIZE, OBSTACLE_SIZE))
# 中线上下两条横廊（180° 对称），不挡出生对角线
_MAP_CORRIDORS = ((280, 180, 240, 40),
                  (280, 380, 240, 40))
MAPS = (
    {"id": "empty", "label": "EMPTY", "rects": ()},
    {"id": "pillars", "label": "PILLARS", "rects": _MAP_PILLARS},
    {"id": "corridors", "label": "CORRIDORS", "rects": _MAP_CORRIDORS},
)
DEFAULT_MAP_INDEX = next(i for i, m in enumerate(MAPS) if m["id"] == "pillars")
# 兼容旧自测 / 文档：默认等于 pillars 的 rects；运行中切图会改这个引用
OBSTACLE_RECTS = MAPS[DEFAULT_MAP_INDEX]["rects"]


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
# 距离控制的迟滞带宽（像素）：倒车要一直退到 keep_min + 这个值才重新往前。
# 没有它的话，位置刚好落在 keep_min 上会「退一帧→太远→进一帧→太近」地无限
# 抖动（一帧 1.9px 前 / 4.4px 后），把双方的位置变成互相喂的噪声：AI 的提前
# 量被这点抖动带偏，于是打着转瞄不准，两车就顶着原地磨（加了掩体后实测能磨
# 9000 帧）。带宽给 30px ≈ 坦克半宽多一点的视觉容差，玩家看不出来。
AI_KEEP_HYST = 30.0


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
# 菜单：优先专用键；8BitDo 等经 Apple MFI 时 START/BACK/摇杆键常读不到，
# 再用「双肩键 / LB+X」和弦兜底（这两路 ABXY·肩键一定能读到）。
PAD_MODE_BUTTONS = (pygame.CONTROLLER_BUTTON_BACK,
                    pygame.CONTROLLER_BUTTON_GUIDE)
PAD_DIFFICULTY_BUTTONS = (pygame.CONTROLLER_BUTTON_RIGHTSTICK,
                          pygame.CONTROLLER_BUTTON_LEFTSTICK)
PAD_RESTART_BUTTONS = (pygame.CONTROLLER_BUTTON_START,)
PAD_MENU_LB = pygame.CONTROLLER_BUTTON_LEFTSHOULDER
PAD_MENU_RB = pygame.CONTROLLER_BUTTON_RIGHTSHOULDER
PAD_MENU_DIFF_FACE = pygame.CONTROLLER_BUTTON_X   # LB 按住 + X = 切难度
PAD_MENU_MAP_FACE = pygame.CONTROLLER_BUTTON_Y    # RB 按住 + Y = 切地图（仅等待开始）
# 局末菜单确认：人脸键 Y（不用 A——主开火最易误触再来一局）；Start(+) 仍可用
PAD_MENU_CONFIRM_FACE = pygame.CONTROLLER_BUTTON_Y

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
# ESC 退出、F2/F3 切模式与难度、F4 开配置、F5 评估、M 切地图，都不允许被抢作开火键
RESERVED_KEY_NAMES = frozenset(
    {"escape", "esc", "f1", "f2", "f3", "f4", "f5", "m"})
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


def check_fire_key(name, other_name=None, ascii_only=False):
    """校验开火键名，返回 (键码, 错误原因)；合法时错误原因为 None。

    ascii_only=True 时错误原因是英文——F4 面板上显示它，没有汉字字体时
    中文会变成方块。
    """
    def msg(zh, en):
        return en if ascii_only else zh

    code = key_name_to_code(name)
    if code is None:
        return None, msg(f"无法识别的按键 {name!r}", f"unknown key {name!r}")
    norm = key_code_to_name(code)
    if norm in RESERVED_KEY_NAMES:
        return None, msg(f"{norm.upper()} 是系统保留键（ESC / F2-F5 / M）",
                         f"{norm.upper()} is reserved (ESC / F2-F5 / M)")
    if other_name:
        other_code = key_name_to_code(other_name)
        if other_code is not None and other_code == code:
            return None, msg(f"与另一玩家的开火键 {norm.upper()} 冲突",
                             f"clashes with the other player's {norm.upper()}")
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
    """把一行文字裁到指定宽度内（超出用省略号收尾），避免卡片内容溢出。"""
    if font.size(text)[0] <= width:
        return text
    dots = "…" if cjk_font_available() else "..."
    while text and font.size(text + dots)[0] > width:
        text = text[:-1]
    return text + dots


def draw_coach_card(surf, font, small_font, title, lines, x, y, width=384,
                    accent=YELLOW):
    """评估卡片：半透明底板 + 标题 + 若干短行（F5 实时面板 / 局末报告共用）。

    行首标记统一用 ASCII：`!` = 要纠正的（橙），`>` = 目标 / 历史（绿），
    这样中英两种文案、有没有汉字字体都能正确上色。
    """
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
        elif line.startswith((">", "目标", "历史")):
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


def safe_respawn_point(opponent, fallback=None):
    """挑一个离对手最远的复活角，返回 (Vector2 位置, 朝向角度)。

    固定回出生角的问题是「1 秒无敌一到点就顶着边线挨枪」：出生点永远在
    角落，对手早就在 120–280px 内架好枪。改成四个角里挑离对手最远的那一个，
    朝向自动指向对手（atan2(dx, -dy)，与 0 朝上、顺时针为正一致），复活的人
    一睁眼就对着威胁方向，至少有一段距离可以重新组织。
    """
    if opponent is None:
        point = pygame.Vector2(fallback if fallback is not None
                               else RESPAWN_CORNERS[0])
        return point, 0.0
    point = max((pygame.Vector2(c) for c in RESPAWN_CORNERS),
                key=lambda p: p.distance_squared_to(opponent.pos))
    dx = opponent.pos.x - point.x
    dy = opponent.pos.y - point.y
    return point, math.degrees(math.atan2(dx, -dy)) % 360.0


# --- 障碍物几何：全工程唯一的判断出处 ---
# 车体碰撞（Tank.drive）、子弹撞墙（main）、AI 探路（TankAI.update）、
# 两车互斥（separate_tanks）、屏幕边界（clamp_to_field）都只走下面这几个
# 函数，不再各自写一份 colliderect / 线段求交 / max-min 夹取。
# obstacles 可以是 pygame.Rect 的序列，也可以是 Obstacle 精灵组（取 .rect）。

def _closest_point(pos, rect):
    """矩形上离 pos 最近的点（pos 落在矩形内时就是 pos 自己）。"""
    return pygame.Vector2(min(max(pos.x, rect.left), rect.right),
                          min(max(pos.y, rect.top), rect.bottom))


def obstacle_at(pos, obstacles, radius=0.0):
    """返回第一个与「圆心 pos + 半径 radius」相交的障碍物矩形，没有则 None。

    radius=0 表示「点是否在方块里」。内部点（最近点 = 自己）也算相交，
    所以哪怕有一帧穿进了方块里也能被发现。
    """
    if not obstacles:
        return None
    for item in obstacles:
        rect = item.rect if hasattr(item, "rect") else item
        near = _closest_point(pos, rect)
        if near.distance_squared_to(pos) <= radius * radius:
            return rect
    return None


def resolve_obstacles(pos, radius, obstacles, passes=3):
    """把圆心 pos 推出所有障碍物（就地修改 Vector2），返回是否发生过推移。

    推出方向是「圆-矩最小穿透」：圆心在矩形外侧时沿最近点法线推到刚好相切，
    在矩形内部时按到四条边的最短距离推到最近的那条边外。所以斜着顶墙时只有
    垂直墙面的那一维被推回、沿墙分量原样保留——看上去就是贴着墙滑行，
    而不是撞上就粘死。passes>1 用来处理「被夹在两个方块之间」的情况。
    """
    if not obstacles:
        return False
    moved = False
    for _ in range(max(1, int(passes))):
        pushed = False
        for item in obstacles:
            rect = item.rect if hasattr(item, "rect") else item
            if (pos.x + radius <= rect.left or pos.x - radius >= rect.right
                    or pos.y + radius <= rect.top or pos.y - radius >= rect.bottom):
                continue        # 根本没碰上，省掉开方
            near = _closest_point(pos, rect)
            delta = pos - near
            dist = delta.length()
            if dist > 1e-9:
                pos.update(near.x + delta.x / dist * radius,
                           near.y + delta.y / dist * radius)
            else:
                # 圆心已在矩形内（或正压在边上）：推向最近的那条边
                left, right = pos.x - rect.left, rect.right - pos.x
                top, bottom = pos.y - rect.top, rect.bottom - pos.y
                edge = min(left, right, top, bottom)
                if edge == left:
                    pos.x = rect.left - radius
                elif edge == right:
                    pos.x = rect.right + radius
                elif edge == top:
                    pos.y = rect.top - radius
                else:
                    pos.y = rect.bottom + radius
            pushed = moved = True
        if not pushed:
            break
    return moved


def clamp_to_field(pos, radius=TANK_HALF):
    """把圆心 pos 夹进场地内（就地修改 Vector2），返回是否真的挪过。

    屏幕边界夹取的唯一出处：`Tank.drive()` 和 `separate_tanks()` 都调它，
    不再各自写一份 `max(TANK_HALF, min(SCREEN_WIDTH - TANK_HALF, x))`。
    左右/上下都留出一个 radius（车体半径），所以车贴边时车体不会被切掉。
    """
    x = max(radius, min(SCREEN_WIDTH - radius, pos.x))
    y = max(radius, min(SCREEN_HEIGHT - radius, pos.y))
    if x == pos.x and y == pos.y:
        return False
    pos.update(x, y)
    return True


# 车 × 车分离的容差（px）：分到「最小中心距 - 1」就收手，防止贴着时每帧
# 亚像素微震。1px 这个口径与 tank_blocked_ahead 里 TANK_HALF - 1 完全一致。
TANK_GAP = 1.0


def _nudge(tank, direction, amount, radius):
    """沿 direction 把 tank 推 amount，压回掩体/场地内，返回实际分离量。

    返回值是「沿 direction 的投影增量」，也就是这次真正让出来的距离：被墙或
    边界吃掉的部分自然变小，调用方（separate_tanks）据此把差额补给对手。
    """
    if amount <= 0.0:
        return 0.0
    before = tank.pos.x * direction.x + tank.pos.y * direction.y
    tank.pos += direction * amount
    resolve_obstacles(tank.pos, radius, tank.obstacles)
    clamp_to_field(tank.pos, radius)
    after = tank.pos.x * direction.x + tank.pos.y * direction.y
    return max(0.0, after - before)


def separate_tanks(a, b, radius=TANK_HALF, passes=3):
    """把重叠的两台车沿中心连线对称推开（双方各让一半），返回本帧是否重叠过。

    「允许双方交互位置」的唯一出处，语义按「双方都被影响」定：
      - 穿透量各推一半，不是「谁后动谁推人」——1p / 2p、人类 / AI 一样公平；
      - 每次推完各自跑 resolve_obstacles() + clamp_to_field() 压回掩体和场地；
        被墙 / 边界吃掉的那部分补给另一台（掩体与边界 = 无限质量），所以
        「贴墙 + 被顶」时不会两台一起陷进方块；
      - 中心距 >= 2*radius - TANK_GAP 时直接返回 False，坐标一位不动：幂等，
        已经分开的帧不会被改出抖动；
      - 完全重合（中心距 ≈ 0）时沿固定轴 (1, 0) 分开，避免除零 / NaN；
      - passes>1 处理「被掩体和对手三角夹住」，剩下的亚像素重叠下一帧继续分。

    调用点：main() 在两台车都 drive() 完之后、子弹命中判定之前调一次。
    不要塞进 Tank.drive()：drive() 是单车逻辑，主循环里两台车先后各调一次，
    写进去就变成「后动的那台单方面推前一台」，既不对称也会污染既有自测。
    """
    if a is b or a is None or b is None:
        return False
    if not (a.alive and b.alive):
        return False
    min_dist = 2.0 * radius
    overlapped = False
    for _ in range(max(1, int(passes))):
        delta = b.pos - a.pos
        dist = delta.length()
        if dist >= min_dist - TANK_GAP:
            break
        overlapped = True
        if dist > 1e-6:
            unit = delta / dist                 # a -> b 的单位向量
        else:
            unit = pygame.Vector2(1.0, 0.0)     # 完全重合：固定轴，避免除零
        half = (min_dist - dist) * 0.5
        moved_a = _nudge(a, -unit, half, radius)
        moved_b = _nudge(b, unit, half, radius)
        # 被墙 / 边界吃掉的部分补给另一台（墙 = 无限质量）
        if moved_a < half - 1e-9:
            _nudge(b, unit, half - moved_a, radius)
        elif moved_b < half - 1e-9:
            _nudge(a, -unit, half - moved_b, radius)
    return overlapped


def has_line_of_sight(a, b, obstacles):
    """a 到 b 的线段有没有被障碍物挡住（全工程唯一的视线出处）。

    用 Rect.clipline 求线段与矩形的交集：空元组 = 没穿过。线段端点落在方块
    内、或正好擦着方块边角，都算「被挡住」（保守口径，AI 不会隔墙开枪）。
    """
    if not obstacles:
        return True
    for item in obstacles:
        rect = item.rect if hasattr(item, "rect") else item
        if rect.clipline((a.x, a.y), (b.x, b.y)):
            return False
    return True


def tank_blocked_ahead(tank, move, obstacles, reach=2.0, angle=None, other=None):
    """按给定朝向（默认车头）前进会不会马上顶到墙 / 出场地 / 顶到对手。

    口径沿用旧版 TankAI._would_leave_screen（沿朝向探 speed*move + 2px，
    看车体半径的位置是否越界），只是多查一次障碍物；AI 探路与「贴墙停车」
    共用这一份判定。

    障碍物这一半要用 TANK_HALF - 1 而不是 TANK_HALF：车静止时正好停在
    「距墙面 TANK_HALF」上（resolve_obstacles 就是推到相切），拿 TANK_HALF
    去比，贴着墙的所有方向（包括平行、背离）都会被判成「挡」——车于是既
    不敢前进也不敢转，只能原地抖。少 1px 就等于「只有真的会往里挤才算挡」。

    other：对手坦克（可选）。给了就把「探针进到两车相切 - 1px 以内」也算挡，
    口径同上（少 1px 容差，贴住时不会既不敢走也不敢转）。不认对手的话，
    两车互斥之后 AI 会一直往前挤、被玩家顶着磨到墙角站桩挨打。默认 None，
    所有既有调用行为逐位不变。
    """
    rad = math.radians(tank.angle if angle is None else angle)
    step = tank.speed * max(0.0, move) + reach
    probe = pygame.Vector2(tank.pos.x + math.sin(rad) * step,
                           tank.pos.y - math.cos(rad) * step)
    if not (TANK_HALF <= probe.x <= SCREEN_WIDTH - TANK_HALF
            and TANK_HALF <= probe.y <= SCREEN_HEIGHT - TANK_HALF):
        return True
    if obstacle_at(probe, obstacles, TANK_HALF - 1.0) is not None:
        return True
    return (other is not None and other is not tank
            and probe.distance_to(other.pos) <= 2 * TANK_HALF - 1.0)


class Tank(pygame.sprite.Sprite):
    """俯视角坦克：车体 + 履带 + 炮塔 + 炮管，角度 0 表示朝上。"""

    def __init__(self, x, y, color, controls, speed=3.2, turn_speed=4.0):
        super().__init__()
        self.color = color
        self.controls = controls  # forward/back/left/right/fire -> keycode
        self.pad = None           # 由 main() 绑定 Gamepad；None = 只用键盘
        self.ai = None            # 单人模式下由 main() 绑定 TankAI；None = 人类
        # 场地里的障碍物（Rect 序列或 Obstacle 精灵组）。默认空 = 无障碍物，
        # 所有 _selftest*/沙盘脚本直接 Tank(...) 时行为与加入障碍物之前完全一致；
        # main() / _preview.py 会显式挂上。
        self.obstacles = ()
        self.speed = speed
        self.turn_speed = turn_speed
        self.angle = 0.0  # 0 = 朝上，顺时针为正
        # 键盘转向的加速段状态：同向连续按住的时长（按 60fps 的帧计）与方向
        self.turn_hold = 0.0
        self.turn_sign = 0.0
        self.pos = pygame.Vector2(x, y)
        # 出生点：复位 / 掉命复活都回到这里（朝向也回到出生朝向）
        self.spawn_point = pygame.Vector2(x, y)
        self.spawn_angle = self.angle
        # 复活点：默认等于出生点；main() 会在掉命时改成「离对手最远的角」
        self.respawn_point = pygame.Vector2(x, y)
        self.respawn_angle = self.angle

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
    def drive(self, turn=0.0, move=0.0, dt=1.0 / FPS):
        """模拟量输入通道：turn/move 为 -1..1，dt 为上一帧耗时（秒）。

        符号约定（见模块顶部 TURN_RIGHT / TURN_LEFT，唯一出处）：
        turn > 0 = 顺时针 = 画面右转 = 角度增大；turn < 0 = 画面左转。
        键盘（turn=±1、move=±1）和手柄摇杆（连续量）共用这一份物理逻辑。

        turn_speed / speed 都是「每标准帧（1/60 秒）」的量，这里乘 dt × FPS
        折算成真实时间：
          - dt 默认 1/60 时与旧版逐位一致（所有自测脚本 / 试跑脚本都不传 dt）；
          - main() 把 clock.tick() 实测的帧间隔传进来，掉帧（字体渲染、评估
            面板）时转速与车速不再跟着变慢，手感不再随帧率漂移。

        障碍物：位置更新后先 resolve_obstacles() 把车体推出方块，再做屏幕边界
        夹取（顺序固定：先障碍物、后边界）。推出是沿最小穿透方向，所以斜着顶墙
        会自然地贴着墙滑行而不是粘住；转向不改变位置，所以原地转向不会被卡住。
        """
        scale = dt * FPS          # 1.0 = 标准帧；掉帧 > 1，超快帧 < 1
        if turn:
            self.angle += self.turn_speed * turn * scale
        self.angle %= 360

        if move:
            # 倒车慢一点：前进全速，后退 0.6 倍
            speed_scale = 1.0 if move > 0 else 0.6
            rad = math.radians(self.angle)
            step = self.speed * speed_scale * abs(move) * scale
            direction = 1.0 if move > 0 else -1.0
            self.pos.x += math.sin(rad) * step * direction
            self.pos.y -= math.cos(rad) * step * direction

        # 障碍物碰撞：沿最小穿透方向把车体推出方块（撞墙 = 沿墙滑行）
        resolve_obstacles(self.pos, TANK_HALF, self.obstacles)

        # 限制在屏幕内（唯一出处：clamp_to_field，separate_tanks 也用它）
        clamp_to_field(self.pos, TANK_HALF)

    def read_keyboard_axes(self, keys, dt=1.0 / FPS):
        """把键盘按键折算成 (turn, move)：move ∈ {-1,0,1}，turn ∈ [-1,1]。

        turn 的幅度带「点按微转」：起手那一帧只给 TURN_FINE_GAIN（默认 0.25，
        即 1°/帧），同向按住 TURN_RAMP_FRAMES 帧后线性升到满舵（4°/帧）。
        原因见模块顶部 TURN_FINE_GAIN 的注释：一步 4° 的格点永远落不到
        atan2 算出的敌人方向上，只能靠小步长把炮口停在目标附近。
        """

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

        if turn:
            sign = 1.0 if turn > 0 else -1.0
            if sign != self.turn_sign:      # 换向：加速段从头开始
                self.turn_sign = sign
                self.turn_hold = 0.0
            # 按住时长按 60fps 的「帧」累计，这样换帧率不影响加速曲线
            self.turn_hold += max(dt, 0.0) * FPS
            gain = min(1.0, TURN_FINE_GAIN +
                       (1.0 - TURN_FINE_GAIN) *
                       max(0.0, self.turn_hold - 1.0) / TURN_RAMP_FRAMES)
            turn *= gain
        else:
            self.turn_sign = 0.0            # 松手 / 左右同按：加速段复位
            self.turn_hold = 0.0

        move = 0.0
        if pressed(self.controls["forward"]):
            move += 1.0
        if pressed(self.controls["back"]):
            move -= 1.0
        return turn, move

    def handle_input(self, keys, dt=1.0 / FPS):
        """键盘输入（签名保持兼容：_selftest.py / 6a 都按位置传 keys）。"""
        turn, move = self.read_keyboard_axes(keys, dt)
        self.drive(turn, move, dt)

    def poll_controls(self, keys, dt=1.0 / FPS):
        """键盘 + 手柄合成输入；没有手柄时等价于 handle_input(keys, dt)。"""
        turn, move = self.read_keyboard_axes(keys, dt)
        if self.pad is not None:
            turn += self.pad.turn()
            move += self.pad.move()
        self.drive(max(-1.0, min(1.0, turn)), max(-1.0, min(1.0, move)), dt)

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
        """在复活点复活：满血、清冷却、给一段无敌。

        复活点默认就是出生点；main() 掉命时会先把它改成离对手最远的角，
        所以直接调 respawn() 的行为和以前完全一致。
        """
        self.pos.update(self.respawn_point)
        self.angle = self.respawn_angle
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

    def touches(self, tank):
        """这颗子弹是否命中 tank（圆形判定，与车体朝向无关）。

        全工程唯一的命中判定出处：main() 和所有试跑 / 测试脚本都调它，
        不再各自写一份 `rect.colliderect(tank.rect)`。
        """
        return self.pos.distance_to(tank.pos) <= HIT_RADIUS + BULLET_RADIUS


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


class Obstacle(pygame.sprite.Sprite):
    """场地里的固定掩体方块：挡车、挡子弹、挡住视线（不可破坏、不重生）。

    贴图在构造时一次性画好，绘制直接用 Group.draw()，主循环里零额外代码。
    rect 就是碰撞用的矩形（`obstacle_at` / `resolve_obstacles` / `clipline`
    都直接用它的几何，不依赖贴图）。
    """

    def __init__(self, spec):
        super().__init__()
        self.rect = pygame.Rect(*spec)
        surf = pygame.Surface(self.rect.size, pygame.SRCALPHA)
        body = surf.get_rect()
        pygame.draw.rect(surf, (46, 46, 56), body, border_radius=5)
        # 左上亮、右下暗：给方块一点厚度感，跟 DEMO 里的坦克配色一致
        pygame.draw.line(surf, (88, 88, 106), (5, 5), (body.width - 6, 5), 2)
        pygame.draw.line(surf, (88, 88, 106), (5, 5), (5, body.height - 6), 2)
        pygame.draw.line(surf, (26, 26, 32), (5, body.height - 5),
                         (body.width - 6, body.height - 5), 2)
        pygame.draw.line(surf, (26, 26, 32), (body.width - 5, 5),
                         (body.width - 5, body.height - 5), 2)
        pygame.draw.rect(surf, GRAY, body, width=2, border_radius=5)
        self.image = surf


def map_rects(map_index):
    """给定地图索引的障碍物规格；越界回退到默认 pillars。"""
    if not (0 <= int(map_index) < len(MAPS)):
        map_index = DEFAULT_MAP_INDEX
    return MAPS[int(map_index)]["rects"]


def map_label(map_index):
    if not (0 <= int(map_index) < len(MAPS)):
        map_index = DEFAULT_MAP_INDEX
    return MAPS[int(map_index)]["label"]


def build_obstacles(map_index=None):
    """按指定地图建掩体；TANK_WALLS=0 时返回空组。

    map_index 默认 pillars。运行中切图会清组重建；R / START 重开不换图。
    """
    if map_index is None:
        map_index = DEFAULT_MAP_INDEX
    group = pygame.sprite.Group()
    if OBSTACLE_ENABLED:
        for spec in map_rects(map_index):
            group.add(Obstacle(spec))
    return group


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
        self.prev_mode = False
        self.prev_difficulty = False
        self.prev_map = False
        self.prev_confirm_face = False
        self._fire_edge = False
        self._restart_edge = False
        self._mode_edge = False
        self._difficulty_edge = False
        self._map_edge = False
        self._confirm_face_edge = False
        # CONTROLLERBUTTONDOWN 闩锁：部分 MFI 手柄 get_button(START/BACK) 一直
        # 为假，但事件能到；poll 时并入上升沿，用完清掉。
        self._latch_mode = False
        self._latch_difficulty = False
        self._latch_restart = False
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

    def mode_held(self):
        """BACK/GUIDE，或双肩键同时按住（MFI 兜底）。"""
        if self._button(PAD_MODE_BUTTONS):
            return True
        return (self._button((PAD_MENU_LB,)) and self._button((PAD_MENU_RB,)))

    def difficulty_held(self):
        """摇杆键，或 LB 按住 + X（MFI 兜底；此时不配 RB，避免跟切模式和弦打架）。"""
        if self._button(PAD_DIFFICULTY_BUTTONS):
            return True
        return (self._button((PAD_MENU_LB,))
                and self._button((PAD_MENU_DIFF_FACE,))
                and not self._button((PAD_MENU_RB,)))

    def map_held(self):
        """RB 按住 + Y：切地图（仅等待开始时主循环才认）。"""
        return (self._button((PAD_MENU_RB,))
                and self._button((PAD_MENU_MAP_FACE,))
                and not self._button((PAD_MENU_LB,)))

    def confirm_face_held(self):
        """人脸键 Y：局末菜单确认（主循环仅 winner 时认；对局中 Y 仍开火）。"""
        return self._button((PAD_MENU_CONFIRM_FACE,))

    def latch_menu_button(self, button):
        """事件路径：把 CONTROLLERBUTTONDOWN 的语义按钮记成下一帧边沿。"""
        try:
            btn = int(button)
        except (TypeError, ValueError):
            return False
        if btn in PAD_MODE_BUTTONS:
            self._latch_mode = True
            return True
        if btn in PAD_DIFFICULTY_BUTTONS:
            self._latch_difficulty = True
            return True
        if btn in PAD_RESTART_BUTTONS:
            self._latch_restart = True
            return True
        return False

    def poll(self):
        """每帧刷新一次；把“按住”变成“按下那一帧”的上升沿。"""
        fire = self.fire_held()
        restart = self.restart_held()
        mode = self.mode_held()
        difficulty = self.difficulty_held()
        map_btn = self.map_held()
        confirm_face = self.confirm_face_held()
        self._fire_edge = fire and not self.prev_fire
        self._restart_edge = ((restart and not self.prev_restart)
                              or self._latch_restart)
        self._mode_edge = (mode and not self.prev_mode) or self._latch_mode
        self._difficulty_edge = ((difficulty and not self.prev_difficulty)
                                 or self._latch_difficulty)
        self._map_edge = map_btn and not self.prev_map
        self._confirm_face_edge = confirm_face and not self.prev_confirm_face
        self._latch_mode = self._latch_difficulty = self._latch_restart = False
        self.prev_fire, self.prev_restart = fire, restart
        self.prev_mode, self.prev_difficulty = mode, difficulty
        self.prev_map = map_btn
        self.prev_confirm_face = confirm_face
        return self

    def fire_edge(self):
        """本帧是否是开火键的上升沿（须先 poll()）。"""
        return self._fire_edge

    def restart_edge(self):
        return self._restart_edge

    def mode_edge(self):
        """切 1P↔2P（须先 poll()）。"""
        return self._mode_edge

    def difficulty_edge(self):
        """循环 AI 难度（须先 poll()）。"""
        return self._difficulty_edge

    def map_edge(self):
        """切地图（须先 poll()；主循环仅在 awaiting_start 时响应）。"""
        return self._map_edge

    def confirm_face_edge(self):
        """局末菜单用 Y 确认（须先 poll()；主循环仅 winner 时响应）。"""
        return self._confirm_face_edge

    def menu_consumed_fire(self):
        """本帧菜单和弦占用了肩键/面键，开火应让路，避免误开枪。"""
        return (self._mode_edge or self._difficulty_edge or self._map_edge
                or self._confirm_face_edge)

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


def hud_text(tank, other, hp=None):
    """左上/右上那行 HUD 文案：血量 / 炮口角度 / 瞄准残差 / 到对手的距离。

    全工程唯一出处——主循环和 `_preview.py` 截图都调它，避免两份格式串漂移。

    - `ERR`：到对手的瞄准残差（度），正数 = 目标在右手边（该右转）；
    - `DIST`：两车中心的**欧氏距离**（px），实时跟着走位变化；
      超过 `AI_FIRE_RANGE`（560px）就是废弹距离——子弹会先飞出屏幕，
      那个距离上开火只换来一次冷却（`tank_coach` 的 `far` 规则口径相同）。
    - `hp=None` 时取坦克当前血量；截图脚本要摆固定血量时才传值。
    """
    dx = other.pos.x - tank.pos.x
    dy = other.pos.y - tank.pos.y
    err = _wrap_deg(math.degrees(math.atan2(dx, -dy)) - tank.angle)
    dist = math.hypot(dx, dy)
    if hp is None:
        hp = tank.hp
    return (f"HP {hp:3d}  ANG {tank.angle % 360:3.0f}  "
            f"ERR {err:+5.1f}  DIST {dist:3.0f}")


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
        # 撞墙脱困的迟滞状态：绕行方向（±0.8）与还要保持几帧
        self._wall_dir = 0.0
        self._wall_frames = 0
        # 距离控制的迟滞状态：True = 正在倒车拉开（退到 keep_min + AI_KEEP_HYST 外才撤销）
        self._backing_off = False
        # 撞墙绕行的「磨了多久」计数：见 _unstick_turn / _wall_forget
        self._wall_busy = 0
        # 绕行模式：打不出去就一直绕，直到「打得出去 + 前面不挡」（见 update）
        self._wall_mode = False
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
        # 已经被顶到贴身（各档 keep_min 最小也有 120px，能到 46px 只可能是被
        # 玩家主动顶上来）：这一帧倒车让开，而不是继续压上去磨。瞄准和开火
        # 照常，所以「让开」不会变成「站着挨打」。
        pressed = dist <= 2 * TANK_HALF + 2
        # 视线分两个口径，别混用：
        #  · los（车心 → 目标）：只用来决定「要不要往前压」。「看不见人就压上」
        #    必须用车心，否则车头顶着掩体、炮口插进方块里时判定会随瞄准角
        #    一帧一变，AI 就在「站着瞄」和「转身绕」之间无限翻烙饼（实测原地
        #    磨了 9000 帧，一枪没打出去）。
        #  · can_shoot（炮口 → 目标，且炮口不能贴着方块）：只用来决定开不开枪。
        #    炮口比车体半径还远 3px，顶着墙时它已经插进方块里了；子弹还有
        #    4px 半径，炮口离墙面 2px 时子弹一出生就判撞墙（实测白打 600 多发）。
        muzzle = tank.muzzle_position()
        los = has_line_of_sight(tank.pos, target.pos, tank.obstacles)
        can_shoot = (has_line_of_sight(muzzle, target.pos, tank.obstacles)
                     and obstacle_at(muzzle, tank.obstacles,
                                     BULLET_RADIUS + 1) is None)

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

        # 距离控制带迟滞（Schmitt）：只在越过阈值时切换「压上 / 拉开 / 保持」，
        # 免得刚好卡在 keep_min 上一帧前进一帧倒车地抖（见 AI_KEEP_HYST 注释）。
        if dist < p["keep_min"]:
            self._backing_off = True
        elif dist > p["keep_min"] + AI_KEEP_HYST:
            self._backing_off = False

        if pressed:
            move = -0.8           # 被顶住：先让开（下面还有最终兜底，见末尾）
        elif dist > p["keep_max"]:
            move = 1.0            # 太远：压上去
        elif self._backing_off:
            move = -0.8           # 太近：倒车拉开（退到迟滞带外才收手）
        elif dist > (p["keep_min"] + p["keep_max"]) * 0.5 and abs(err) <= 12.0:
            # 只在「舒适区上半段」才缓推：如果一进舒适区就往前挤，就会
            # 「挤过头→倒车→又进舒适区→再挤」地来回磨，距离永远在 keep_min
            # 上下摆一个像素级周期，AI 自己也被这点抖动带得瞄不准。
            move = 0.35
        else:
            move = 0.0
        # 打不出去（隔墙 / 炮口被方块埋住）就先压上去，逼自己绕出射界。
        # 判据用 can_shoot 而不是「车心看不看得见」：车心和目标之间只擦着方块
        # 边角时，车心视线是通的、子弹却一定撞角——按车心算 AI 会站在那儿
        # 「看着对方但一枪不开」到天荒地老。
        if not can_shoot and not pressed:
            move = 1.0
        move *= p["speed"]

        # 撞墙自保：按当前朝向前进会顶到墙 / 出场地就停车 + 挑一侧绕行。
        # 「打不出去」（can_shoot 为假）时进入绕行模式，并且**一直绕到「打得
        # 出去 + 前面不挡」才退出**：中途只要切回瞄准，瞄准就会把车头转回墙里、
        # 下一帧又被判挡，一帧绕一帧瞄地原地抖（实测在方块角上磨了上千帧）。
        blocked = tank_blocked_ahead(tank, 1.0, tank.obstacles, other=target)
        if not can_shoot:
            self._wall_mode = True
        elif self._wall_mode and not blocked:
            self._wall_mode = False
        if self._wall_mode:
            turn = self._unstick_turn(tank, target)
            # 前面空出来就一边转一边压上（绕着方块走），别停在原地等
            move = 0.0 if blocked else p["speed"]
        elif blocked and move > 0:
            move = 0.0
            turn = self._unstick_turn(tank, target)
        else:
            self._wall_forget()

        # move 决策链的最终兜底：被顶住就倒车让开，连「绕墙模式里压上」
        # 也不许覆盖这条；turn 仍然生效，所以被顶在墙角时会「边转身边倒车」
        # 沿墙滑出来，而不是原地顶牛。
        if pressed:
            move = -0.8 * p["speed"]

        self.turn_cmd = turn
        self.move_cmd = move

        if (abs(err) <= p["fire_tol"] and dist <= AI_FIRE_RANGE and can_shoot
                and tank.can_fire() and self.rng.random() < p["fire_chance"]):
            self.want_fire = True

    # --- 撞墙脱困（启发式，不做寻路）---
    WALL_PROBE_DEG = 40.0   # 往左右各探这么多度，看哪边是空的
    WALL_TURN_HOLD = 8      # 每段转向命令持续多少帧（8 × 3.2° ≈ 26°）
    WALL_FLIP_FRAMES = 240  # 同一个方向绕了这么多帧还出不来（进了死角）才换一边

    def _unstick_turn(self, tank, target=None):
        """被挡住时挑一侧绕，并且**一直朝同一侧转**（撞墙脱困的唯一出处）。

        两条要点，都是踩坑换来的：
        1. 侧向只选一次。探路是相对「当前车头」量的，车头一转刚才空的那边
           就可能变成堵的；每帧重探会左右横跳、原地磨（实测顶着掩体磨了
           9000 帧，一枪没开出去）。
        2. 同一个方向磨太久（WALL_FLIP_FRAMES）才换边——那是真进了死角
           （U 形夹缝），换一边绕。只要还在往前挪就不换，这样才会沿着方块
           走完一整圈把射界打开，而不是来回摆。
        3. 侧向探测也带上对手车体（other=target）：两车互斥之后，把 AI 顶在
           角落的往往是玩家而不是墙；不认对手的话它会朝「只剩玩家的方向」
           继续转，绕进对方的车体里。
        """
        self._wall_busy += 1
        if self._wall_frames > 0:
            self._wall_frames -= 1
            return self._wall_dir
        if not self._wall_dir:
            free_left = not tank_blocked_ahead(
                tank, 1.0, tank.obstacles, angle=tank.angle - self.WALL_PROBE_DEG,
                other=target)
            free_right = not tank_blocked_ahead(
                tank, 1.0, tank.obstacles, angle=tank.angle + self.WALL_PROBE_DEG,
                other=target)
            if free_left and not free_right:
                self._wall_dir = TURN_LEFT * 0.8
            elif free_right and not free_left:
                self._wall_dir = TURN_RIGHT * 0.8
            else:
                self._wall_dir = TURN_RIGHT * 0.8   # 两边一样：先朝右绕
        elif self._wall_busy > self.WALL_FLIP_FRAMES:
            self._wall_dir = -self._wall_dir        # 死角：换一边
            self._wall_busy = 0
        self._wall_frames = self.WALL_TURN_HOLD
        return self._wall_dir

    def _wall_forget(self):
        """这一帧没被挡住：慢慢忘掉绕行方向（连着一阵子不挡才重新探路）。"""
        if self._wall_busy > 0:
            self._wall_busy -= 1
            if self._wall_busy == 0:
                self._wall_dir = 0.0


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


def wait_for_joysticks(frames=30, frame_dt=1.0 / 60.0):
    """窗口创建后短暂泵事件，等 SDL 把手柄枚举出来。

    macOS 上经 Apple Game Controller / MFI 暴露的蓝牙手柄（如 8BitDo
    Ultimate 3mode Xbox）在 set_mode 当下 get_count() 仍是 0，必须至少
    处理一轮 JOYDEVICEADDED / CONTROLLERDEVICEADDED 才会现身。dummy
    视频驱动下没有真设备，泵完仍是 0，行为与以前一致。
    """
    try:
        if pygame.joystick.get_count() > 0:
            return pygame.joystick.get_count()
    except Exception:
        return 0
    for _ in range(max(0, int(frames))):
        try:
            pygame.event.pump()
        except Exception:
            break
        try:
            if pygame.joystick.get_count() > 0:
                return pygame.joystick.get_count()
        except Exception:
            return 0
        time.sleep(frame_dt)
    try:
        return pygame.joystick.get_count()
    except Exception:
        return 0


def print_pad_scan():
    """`python tank_game.py --pad-scan`：打印机柄识别结果 + 排错建议。"""
    wait_for_joysticks()
    rows = describe_joysticks()
    print("=== 手柄扫描 ===")
    print(f"pygame {pygame.version.ver} / SDL {pygame.version.SDL} / {sys.platform}")
    if sdl2_controller is None:
        print("！SDL GameController 层不可用，只能走原始 joystick 兜底")
    if not rows:
        print("没有检测到任何手柄设备（HID joystick 数量 = 0）。")
        print("- 先确认系统层面能看到它：Windows「设置 → 蓝牙和其他设备」，"
              "macOS「系统信息 → USB」/「蓝牙」")
        print("- 有线务必用数据线（不少线只能充电）；无线用 2.4G 接收器或先完成蓝牙配对")
        if sys.platform == "darwin":
            print("- macOS 蓝牙手柄常走 Apple Game Controller：需真实窗口"
                  "（不要 SDL_VIDEODRIVER=dummy）；仍为 0 时优先试 2.4G/USB")
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


def blank_side_stats():
    """一局里单侧战损计数（与 coach 解耦，1p/2p 通用）。"""
    return {"shots": 0, "hits": 0, "shield_hits": 0, "kills": 0, "deaths": 0}


def format_casualty_lines(stats_by_side, lives_by_side, labels_by_side,
                          ascii_only=False):
    """局末双方战损文案（屏幕中央用）。

    stats_by_side / lives_by_side / labels_by_side 的 key 为 "p1"/"p2"。
    """
    lines = []
    for key in ("p1", "p2"):
        s = stats_by_side[key]
        label = labels_by_side[key]
        lives = lives_by_side[key]
        if ascii_only:
            lines.append(
                f"{label}  K{s['kills']} D{s['deaths']}  "
                f"HIT {s['hits']}/{s['shots']}  LIVES {lives}")
        else:
            lines.append(
                f"{label}  击毁{s['kills']} 被毁{s['deaths']}  "
                f"命中{s['hits']}/{s['shots']}  剩命{lives}")
    return lines


# 局末操作菜单（胜负后）；索引与数字键 1–5 对齐
POST_MENU_REMATCH = 0
POST_MENU_REPORT = 1
POST_MENU_MODE = 2
POST_MENU_DIFF = 3
POST_MENU_QUIT = 4
POST_MENU_COUNT = 5

# 启动主菜单；索引与数字键 1–6 对齐
MAIN_MENU_1P = 0
MAIN_MENU_2P = 1
MAIN_MENU_DIFF = 2
MAIN_MENU_MAP = 3
MAIN_MENU_KEYS = 4
MAIN_MENU_QUIT = 5
MAIN_MENU_COUNT = 6


def post_menu_item_labels(ascii_only=False):
    """局末菜单五项文案（含序号）。"""
    if ascii_only:
        return (
            "1  Rematch",
            "2  Battle Report",
            "3  Switch 1P/2P",
            "4  AI Difficulty",
            "5  Back to Main",
        )
    return (
        "1  再来一局",
        "2  战报 Battle Report",
        "3  切换 1P/2P",
        "4  切换 AI 难度",
        "5  回主菜单",
    )


def main_menu_item_labels(ascii_only=False):
    """主菜单六项文案（含序号）。"""
    if ascii_only:
        return (
            "1  1P vs CPU",
            "2  2P Duel",
            "3  AI Difficulty",
            "4  Map",
            "5  Fire Keys",
            "6  Quit",
        )
    return (
        "1  单人 vs 电脑",
        "2  双人对战",
        "3  AI 难度",
        "4  地图",
        "5  改开火键",
        "6  退出",
    )


def skip_main_menu():
    """CLI 指定模式或 TANK_SKIP_MENU=1 时跳过主菜单（自测/脚本用）。"""
    if os.environ.get("TANK_SKIP_MENU") == "1":
        return True
    for arg in sys.argv[1:]:
        a = arg.strip().lower()
        if a in ("--1p", "-1", "--single", "--cpu", "--ai",
                 "--2p", "-2", "--dual", "--versus"):
            return True
        if a.startswith("--mode="):
            return True
    return False


def _parse_mode():
    """决定开局模式与 AI 难度。

    优先级：命令行参数 > 环境变量 > 默认（2p，保持原有双人对战行为）。
        python3.11 tank_game.py --1p --ai=hard
        TANK_MODE=1p TANK_AI=easy python3.11 tank_game.py
    运行中还能用 F2 切模式、F3 切难度。无 CLI 跳过时先进入主菜单。
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
        hist = tank_coach.load_history()
        try:
            print(tank_coach.history_text(hist))
        except UnicodeEncodeError:      # 终端编码不够：换英文表格再打一次
            tank_coach.ASCII_ONLY = True
            print(tank_coach.history_text(hist))
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
          f"{RESPAWN_DELAY / FPS:.1f} 秒后在离对手最远的角复活，"
          f"复活后 {SPAWN_INVULN / FPS:.1f} 秒无敌；命扣完才算输")
    if COACH_ENABLED:
        print(f"[coach] 1p 模式下每局自动评估：局末打印评分/纠正/下一局目标，"
              f"F5 开实时面板，历史写入 {os.path.basename(tank_coach.COACH_HISTORY_PATH)}"
              f"（TANK_COACH=0 关闭）")
    else:
        print("[coach] 评估已关闭（TANK_COACH=0）")
    if OBSTACLE_ENABLED:
        print(f"[walls] 地图 {len(MAPS)} 张（默认 {map_label(DEFAULT_MAP_INDEX)}）；"
              f"等待开始时按 M / RB+Y 切换；TANK_WALLS=0 可关掉全部掩体")
    else:
        print("[walls] 障碍物已关闭（TANK_WALLS=0）")
    # 字体：找不到带汉字的字体就把屏幕文案切英文（否则中文会画成方块）
    font = load_font(22)
    small_font = load_font(18)
    big_font = load_font(56)
    tank_coach.ASCII_ONLY = not cjk_font_available()
    if tank_coach.ASCII_ONLY:
        print("[font] 系统里没找到带汉字字形的字体 → 屏幕文案改用英文"
              "（终端仍是中文）；可用 TANK_FONT=/path/to/font.ttc 指定")
    else:
        kind, value = _font_source()
        print(f"[font] 屏幕字体：{value}（{kind}）")

    world = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
    tanks = pygame.sprite.Group()
    bullets = pygame.sprite.Group()
    explosions = pygame.sprite.Group()
    map_index = DEFAULT_MAP_INDEX
    obstacles = build_obstacles(map_index)
    walls = tuple(ob.rect for ob in obstacles)   # 纯函数用的矩形；空组 = 无障碍物
    # 兼容旧代码/自测读模块级 OBSTACLE_RECTS：切图时同步
    global OBSTACLE_RECTS
    OBSTACLE_RECTS = map_rects(map_index)

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
        casualty_lines.clear()
        round_stats["p1"] = blank_side_stats()
        round_stats["p2"] = blank_side_stats()
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
        # 障碍物挂在坦克上：drive() 每帧用它做碰撞（放在 tanks.add 之前，
        # 保证第一帧就有碰撞）。默认空元组 → 不挂就是无障碍物。
        t1.obstacles = t2.obstacles = walls
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
    # 双方战损（1p/2p 通用）；casualty_lines 在胜负时生成，重开清空
    round_stats = {"p1": blank_side_stats(), "p2": blank_side_stats()}
    casualty_lines = []
    toast = {"text": "", "frames": 0}
    coach_panel = False      # F5：实时评估面板
    # 局末菜单：index 0..4；view "menu" | "report"；nav 用于摇杆/十字键边沿
    post_menu_index = 0
    post_view = "menu"
    post_nav = {"prev": 0}
    # 主菜单：phase "main" | "play"；与局末共用摇杆边沿状态机
    main_menu_index = 0
    main_nav = {"prev": 0}
    skip_menu = skip_main_menu()
    phase = "play" if skip_menu else "main"

    tank1, tank2 = reset()
    wait_for_joysticks()   # macOS 蓝牙/MFI：set_mode 后需泵事件才枚举得到
    rebuild_pads()         # 覆盖“启动时手柄已经插好”的情况
    bind_pads()
    winner = None
    shake = 0.0
    # 仅切 1P/2P（或主菜单选模式）后为 True：等 Start(+)/R/开火才开打
    # 跳过主菜单时保持旧行为：直接开打
    awaiting_start = False
    if phase == "main":
        print("[menu] 主菜单：选模式后等待开始；局末「回主菜单」可返回"
              "（TANK_SKIP_MENU=1 / --1p|--2p 可跳过）")
    else:
        print("[menu] 已跳过主菜单（TANK_SKIP_MENU 或 CLI 指定模式）")

    def side_key(tank):
        return "p1" if tank is tank1 else "p2"

    def try_fire(tank):
        """键盘与手柄共用的开火逻辑（冷却由 Tank.fire 内部控制）。"""
        if not tank.can_fire():
            return False
        bullet = tank.fire()
        bullets.add(bullet)
        muzzle = tank.muzzle_position()
        explosions.add(Explosion(muzzle.x, muzzle.y, YELLOW, 16, 7))
        round_stats[side_key(tank)]["shots"] += 1
        if coach is not None:
            if tank is tank1:
                coach.on_shot(tank, tank2, bullet)   # 记下开枪那一刻的距离/准度
            else:
                coach.on_enemy_shot()
        return True

    def reset_post_menu():
        """胜负刚出现时：菜单回到「再来一局」，关掉报告层。"""
        nonlocal post_menu_index, post_view
        post_menu_index = POST_MENU_REMATCH
        post_view = "menu"
        post_nav["prev"] = 0

    def capture_casualties():
        """胜负时生成双方战损文案（屏幕 + 终端）。"""
        if mode == "1p":
            labels = {
                "p1": "YOU" if tank_coach.ASCII_ONLY else "你",
                "p2": "CPU",
            }
        else:
            labels = {"p1": "P1", "p2": "P2"}
        lives = {"p1": tank1.lives, "p2": tank2.lives}
        casualty_lines[:] = format_casualty_lines(
            round_stats, lives, labels, ascii_only=tank_coach.ASCII_ONLY)
        print("[stats] " + " | ".join(casualty_lines))
        return casualty_lines

    def finish_round(won=None):
        """一局结束：终端打详细评估 → 写历史 → 缓存屏幕报告。"""
        capture_casualties()
        reset_post_menu()
        if coach is None:
            return []
        try:
            print(coach.report_text(coach_hist))
        except UnicodeEncodeError:      # 终端编码不够（如非 UTF-8 控制台）
            print(coach.report_text(coach_hist, ascii_only=True))
        ok, info = tank_coach.record_round(coach_hist, coach.history_row(won=won))
        if not ok:
            print(f"[coach] 历史写入失败：{info}")
        else:
            print(f"[coach] 本局已写入 {os.path.basename(str(info))}"
                  f"（共 {len(coach_hist.get('rounds', []))} 局）")
        report_cache[:] = coach.report_lines(coach_hist, compact=True)
        return report_cache

    def post_item_enabled(index):
        """局末菜单项是否可选。"""
        if index == POST_MENU_REPORT:
            return bool(report_cache)
        if index == POST_MENU_DIFF:
            return mode == "1p"
        return True

    def post_menu_move(delta):
        """上下移动高亮；跳过没有意义的跳转不需要，固定 5 项都可停。"""
        nonlocal post_menu_index
        if post_view != "menu":
            return
        post_menu_index = (post_menu_index + delta) % POST_MENU_COUNT

    def post_menu_confirm(index=None):
        """确认当前（或指定）菜单项；灰显则 toast 原因。"""
        nonlocal post_view
        if index is None:
            index = post_menu_index
        index = int(index) % POST_MENU_COUNT
        if not post_item_enabled(index):
            if index == POST_MENU_REPORT:
                set_toast("No report (2P / coach off)"
                          if tank_coach.ASCII_ONLY else
                          "无战报（双人或评估关闭）")
            elif index == POST_MENU_DIFF:
                set_toast("AI difficulty: 1P only"
                          if tank_coach.ASCII_ONLY else
                          "切难度仅单人模式可用")
            return False
        if index == POST_MENU_REMATCH:
            rematch()
            return True
        if index == POST_MENU_REPORT:
            post_view = "report"
            return True
        if index == POST_MENU_MODE:
            toggle_mode()
            return True
        if index == POST_MENU_DIFF:
            cycle_difficulty()
            return True
        if index == POST_MENU_QUIT:
            go_main_menu()
            return True
        return False

    def main_menu_move(delta):
        nonlocal main_menu_index
        main_menu_index = (main_menu_index + delta) % MAIN_MENU_COUNT

    def main_menu_confirm(index=None):
        """主菜单确认：开打进等待 / 改难度地图 / 改键 / 退出。"""
        nonlocal running
        if index is None:
            index = main_menu_index
        index = int(index) % MAIN_MENU_COUNT
        if index == MAIN_MENU_1P:
            enter_mode_from_menu("1p")
            return True
        if index == MAIN_MENU_2P:
            enter_mode_from_menu("2p")
            return True
        if index == MAIN_MENU_DIFF:
            cycle_difficulty()
            return True
        if index == MAIN_MENU_MAP:
            advance_map()
            return True
        if index == MAIN_MENU_KEYS:
            bind_state["slot"] = "menu"
            set_msg("Pick 1/2/3 to rebind, 0 to reset, ESC to close")
            print(f"[keys] 打开攻击键配置：P1={fire_names['p1'].upper()} "
                  f"P2={fire_names['p2'].upper()} 手柄={pad_fire_label()}")
            return True
        if index == MAIN_MENU_QUIT:
            running = False
            return True
        return False

    def poll_main_menu_pad():
        """主菜单：摇杆/十字键移动；Start(+) 或 Y 确认。"""
        raw = post_nav_raw_dir()
        prev = main_nav["prev"]
        if raw != 0 and raw != prev:
            main_menu_move(raw)
        main_nav["prev"] = raw
        if any(pad.restart_edge() or pad.confirm_face_edge() for pad in pads):
            main_menu_confirm()
            return True
        return False

    def post_nav_raw_dir():
        """手柄当前「菜单上下」意图：-1 上 / 0 无 / +1 下。"""
        dpad_up = getattr(pygame, "CONTROLLER_BUTTON_DPAD_UP", None)
        dpad_down = getattr(pygame, "CONTROLLER_BUTTON_DPAD_DOWN", None)
        for pad in pads:
            if dpad_up is not None and pad._button((dpad_up,)):
                return -1
            if dpad_down is not None and pad._button((dpad_down,)):
                return 1
            hat = pad._hat()
            if hat is not None:
                if hat[1] > 0:
                    return -1
                if hat[1] < 0:
                    return 1
            # move()>0 = 摇杆上推（前进）；菜单上移
            my = pad.move()
            if my > 0.55:
                return -1
            if my < -0.55:
                return 1
        return 0

    def poll_post_menu_pad():
        """胜负菜单：摇杆/十字键边沿移动；Start(+) 或 Y 确认；战报层仅 Back 返回。

        不用 A/任意开火边沿确认：对局末尾最容易误触再来一局。Y 在 ABXY 里最靠上、
        与主开火 A 分开；macOS 上 + 读不到时用 Y。
        """
        nonlocal post_view
        if post_view == "report":
            if any(pad.mode_edge() for pad in pads):
                post_view = "menu"
                return True
            return False
        raw = post_nav_raw_dir()
        prev = post_nav["prev"]
        if raw != 0 and raw != prev:
            post_menu_move(raw)
        post_nav["prev"] = raw
        if any(pad.restart_edge() or pad.confirm_face_edge() for pad in pads):
            post_menu_confirm()
            return True
        return False

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
        # 错误原因会显示在 F4 面板上：没有汉字字体时要英文，否则是方块
        code, err = check_fire_key(name, fire_names[other],
                                   ascii_only=not cjk_font_available())
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

    def set_toast(text, frames=120):
        """屏幕中央短提示（模式/难度/教练共用）。"""
        toast["text"], toast["frames"] = text, frames

    def apply_map():
        """按 map_index 重建掩体，并挂到当前两台坦克上。"""
        nonlocal obstacles, walls, tank1, tank2
        global OBSTACLE_RECTS
        obstacles.empty()
        for ob in build_obstacles(map_index):
            obstacles.add(ob)
        walls = tuple(ob.rect for ob in obstacles)
        OBSTACLE_RECTS = map_rects(map_index)
        if tank1 is not None:
            tank1.obstacles = walls
        if tank2 is not None:
            tank2.obstacles = walls
        return walls

    def advance_map():
        """循环地图并重建掩体（主菜单 / 等待开始共用）。"""
        nonlocal map_index
        map_index = (map_index + 1) % len(MAPS)
        apply_map()
        label = map_label(map_index)
        print(f"[walls] 地图 → {label}（{MAPS[map_index]['id']}）")
        set_toast(f"Map: {label}" if tank_coach.ASCII_ONLY else f"地图：{label}")
        return True

    def cycle_map():
        """仅等待开始时可切：empty → pillars → corridors → …"""
        if not awaiting_start:
            return False
        return advance_map()

    def enter_mode_from_menu(new_mode):
        """主菜单选 1P/2P：摆场地并进入等待开始。"""
        nonlocal mode, tank1, tank2, winner, shake, awaiting_start, phase
        mode = new_mode
        tank1, tank2 = reset()
        bind_pads()
        apply_map()
        winner = None
        shake = 0.0
        awaiting_start = True
        phase = "play"
        if mode == "1p":
            label = AI_SKILLS[ai_skill]["label"]
            msg = (f"Mode: 1P vs CPU ({label}) — press Start(+)/R/fire"
                   if tank_coach.ASCII_ONLY else
                   f"模式：单人 vs 电脑（{label}）— 按 Start(+)/R/开火 开始")
            print("[mode] 主菜单 → 单人 vs 电脑（等待 Start 开始）")
        else:
            msg = ("Mode: 2P duel — press Start(+)/R/fire"
                   if tank_coach.ASCII_ONLY else
                   "模式：双人对战 — 按 Start(+)/R/开火 开始")
            print("[mode] 主菜单 → 双人对战（等待 Start 开始）")
        set_toast(msg, frames=180)
        return True

    def go_main_menu():
        """局末「回主菜单」：清胜负，回到启动菜单。"""
        nonlocal tank1, tank2, winner, shake, awaiting_start, phase
        tank1, tank2 = reset()
        bind_pads()
        apply_map()
        winner = None
        shake = 0.0
        awaiting_start = False
        phase = "main"
        main_menu_index_reset()
        print("[menu] 已回主菜单")
        set_toast("Main Menu" if tank_coach.ASCII_ONLY else "主菜单")
        return True

    def main_menu_index_reset():
        nonlocal main_menu_index
        main_menu_index = MAIN_MENU_1P
        main_nav["prev"] = 0

    def toggle_mode():
        """F2 / LB+RB：双人 ↔ 单人 vs 电脑；摆好场地后进入等待开始。"""
        nonlocal mode, tank1, tank2, winner, shake, awaiting_start, phase
        mode = "2p" if mode == "1p" else "1p"
        tank1, tank2 = reset()
        bind_pads()
        apply_map()          # 保留当前 map_index，重新挂墙
        winner = None
        shake = 0.0
        awaiting_start = True
        phase = "play"
        if mode == "1p":
            label = AI_SKILLS[ai_skill]["label"]
            msg = (f"Mode: 1P vs CPU ({label}) — press Start(+)/R/fire"
                   if tank_coach.ASCII_ONLY else
                   f"模式：单人 vs 电脑（{label}）— 按 Start(+)/R/开火 开始")
            print("[mode] 已切换为单人 vs 电脑（等待 Start 开始）")
        else:
            msg = ("Mode: 2P duel — press Start(+)/R/fire"
                   if tank_coach.ASCII_ONLY else
                   "模式：双人对战 — 按 Start(+)/R/开火 开始")
            print("[mode] 已切换为双人对战（等待 Start 开始）")
        set_toast(msg, frames=180)
        return True

    def cycle_difficulty():
        """F3 / LB+X：EASY → NORMAL → HARD 循环（等待开始时也可改）。"""
        nonlocal ai_skill
        ai_skill = AI_SKILL_ORDER[
            (AI_SKILL_ORDER.index(ai_skill) + 1) % len(AI_SKILL_ORDER)]
        if tank2.ai is not None:
            tank2.ai.set_skill(ai_skill)
        if coach is not None:
            coach.skill_label = AI_SKILLS[ai_skill]["label"]
        label = AI_SKILLS[ai_skill]["label"]
        print(f"[ai] 难度 → {label}")
        set_toast(f"AI: {label}" if tank_coach.ASCII_ONLY else f"难度：{label}")
        return True

    def confirm_start():
        """等待态下按 Start(+)/R/开火：真正开打。"""
        nonlocal awaiting_start
        if not awaiting_start:
            return False
        awaiting_start = False
        if coach is not None:
            # 从确认开打起算本局评估，不含等待帧
            coach.begin_round(AI_SKILLS[ai_skill]["label"])
        print("[mode] 开打")
        set_toast("Fight!" if tank_coach.ASCII_ONLY else "开始！")
        return True

    def rematch():
        """胜负后重开一局（直接开打，不进等待）。"""
        nonlocal tank1, tank2, winner, shake, awaiting_start, phase
        tank1, tank2 = reset()
        bind_pads()
        winner = None
        shake = 0.0
        awaiting_start = False
        phase = "play"
        set_toast("Rematch" if tank_coach.ASCII_ONLY else "再来一局")
        return True

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
    dt = 1.0 / FPS          # 上一帧耗时（秒）；每帧末尾用 clock.tick() 实测刷新

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
            elif (event.type == getattr(pygame, "CONTROLLERBUTTONDOWN", -1)
                  and bind_state["slot"] is None):
                # MFI 上 get_button(START/BACK) 可能一直为假，事件里的语义号仍可用
                btn = getattr(event, "button", None)
                inst = getattr(event, "instance_id",
                               getattr(event, "which", -1))
                target = tank_of_pad(inst)
                hit = False
                if target is not None and target.pad is not None:
                    hit = target.pad.latch_menu_button(btn)
                if not hit:
                    for pad in pads:
                        if pad.latch_menu_button(btn):
                            break
                if PAD_DEBUG:
                    print(f"[pad] CONTROLLERBUTTONDOWN button={btn} "
                          f"latch={'yes' if hit else 'broadcast'}")
            elif PAD_DEBUG and event.type in PAD_BUTTON_EVENTS:
                print(f"[pad] {pygame.event.event_name(event.type)} "
                      f"joy={getattr(event, 'joy', '?')} "
                      f"button={getattr(event, 'button', '?')} "
                      f"which_tank={tank_of_pad(getattr(event, 'instance_id', -1))}")
            elif event.type == pygame.KEYDOWN:
                if bind_state["slot"] is not None:
                    handle_config_key(event.key, bind_state["slot"])
                elif phase == "main":
                    if event.key == pygame.K_ESCAPE:
                        main_menu_confirm(MAIN_MENU_QUIT)
                    elif event.key in (pygame.K_UP, pygame.K_w):
                        main_menu_move(-1)
                    elif event.key in (pygame.K_DOWN, pygame.K_s):
                        main_menu_move(1)
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER,
                                       pygame.K_SPACE):
                        main_menu_confirm()
                    elif event.key in (pygame.K_1, pygame.K_KP1):
                        main_menu_confirm(0)
                    elif event.key in (pygame.K_2, pygame.K_KP2):
                        main_menu_confirm(1)
                    elif event.key in (pygame.K_3, pygame.K_KP3):
                        main_menu_confirm(2)
                    elif event.key in (pygame.K_4, pygame.K_KP4):
                        main_menu_confirm(3)
                    elif event.key in (pygame.K_5, pygame.K_KP5):
                        main_menu_confirm(4)
                    elif event.key in (pygame.K_6, pygame.K_KP6):
                        main_menu_confirm(5)
                    elif event.key == pygame.K_F4:
                        main_menu_confirm(MAIN_MENU_KEYS)
                elif winner is not None:
                    # 局末菜单：Esc 报告层返回 / 菜单层回主菜单；F2/F3/R 不再旁路
                    if post_view == "report":
                        if event.key in (pygame.K_ESCAPE, pygame.K_b,
                                         pygame.K_BACKSPACE):
                            post_view = "menu"
                    elif event.key == pygame.K_ESCAPE:
                        post_menu_confirm(POST_MENU_QUIT)
                    elif event.key in (pygame.K_UP, pygame.K_w):
                        post_menu_move(-1)
                    elif event.key in (pygame.K_DOWN, pygame.K_s):
                        post_menu_move(1)
                    elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER,
                                       pygame.K_SPACE):
                        post_menu_confirm()
                    elif event.key in (pygame.K_1, pygame.K_KP1):
                        post_menu_confirm(0)
                    elif event.key in (pygame.K_2, pygame.K_KP2):
                        post_menu_confirm(1)
                    elif event.key in (pygame.K_3, pygame.K_KP3):
                        post_menu_confirm(2)
                    elif event.key in (pygame.K_4, pygame.K_KP4):
                        post_menu_confirm(3)
                    elif event.key in (pygame.K_5, pygame.K_KP5):
                        post_menu_confirm(4)
                    elif event.key == pygame.K_F4:
                        bind_state["slot"] = "menu"
                        set_msg("Pick 1/2/3 to rebind, 0 to reset, ESC to close")
                elif event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_F4:
                    # F4：打开攻击键配置面板（这期间对战暂停，按键全归面板）
                    bind_state["slot"] = "menu"
                    set_msg("Pick 1/2/3 to rebind, 0 to reset, ESC to close")
                    print(f"[keys] 打开攻击键配置：P1={fire_names['p1'].upper()} "
                          f"P2={fire_names['p2'].upper()} 手柄={pad_fire_label()}")
                elif event.key == pygame.K_F2:
                    toggle_mode()
                elif event.key == pygame.K_F3:
                    cycle_difficulty()
                elif event.key == pygame.K_F5:
                    # F5：对局中实时评估面板（局末战报改走菜单）
                    coach_panel = not coach_panel
                    if coach is None:
                        print("[coach] 双人模式没有评估基准，切到 1p（F2）才有")
                    else:
                        print(f"[coach] 实时面板 {'开' if coach_panel else '关'}"
                              f"（本局累计到第 {coach.metrics()['frames']} 帧）")
                elif awaiting_start:
                    if event.key == pygame.K_m:
                        cycle_map()
                    elif event.key == pygame.K_r:
                        confirm_start()
                    else:
                        for tank in (tank1, tank2):
                            if tank.ai is not None or not tank.controls:
                                continue
                            if event.key == tank.controls["fire"]:
                                confirm_start()
                                break
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

        menu_ate_fire = False
        if not paused:
            # 手柄：主菜单 / 局末菜单优先；否则专用键 / 等待开始确认
            if phase == "main":
                if poll_main_menu_pad():
                    menu_ate_fire = True
            elif winner is not None:
                if poll_post_menu_pad():
                    menu_ate_fire = True
            elif any(pad.mode_edge() for pad in pads):
                toggle_mode()
                menu_ate_fire = True
            elif any(pad.difficulty_edge() for pad in pads):
                cycle_difficulty()
                menu_ate_fire = True
            elif awaiting_start and any(pad.map_edge() for pad in pads):
                cycle_map()
                menu_ate_fire = True
            elif awaiting_start and any(
                    pad.restart_edge() or pad.fire_edge() for pad in pads):
                confirm_start()
                menu_ate_fire = True
            if not menu_ate_fire:
                menu_ate_fire = any(pad.menu_consumed_fire() for pad in pads)

            if phase == "main" or awaiting_start:
                if toast["frames"] > 0:
                    toast["frames"] -= 1
            else:
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
                        tank.drive(tank.ai.turn(), tank.ai.move(), dt)
                        if tank.ai.want_fire:
                            try_fire(tank)
                    else:
                        # 键盘 + 手柄合成，无手柄时 = handle_input
                        tank.poll_controls(keys, dt)
                        if (tank.pad is not None and tank.pad.fire_edge()
                                and not menu_ate_fire):
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
                                toast["text"] = "COACH: " + hint[1]
                                toast["frames"] = 150
                                print(f"[coach] 即时提示：{hint[1]}")

                # 帧末分离：两台车都动完之后、命中判定之前，按「各让一半」分开。
                # 位置很关键——早于两车更新会被下一次 drive 盖掉，晚于命中判定则
                # 子弹已经按旧位置判过命中。一局结束后（winner 已定）不再推挤，
                # 免得把「尸体」顶走。
                if winner is None:
                    separate_tanks(tank1, tank2)

                bullets.update()
                explosions.update()

                # 子弹 × 障碍物：先撞墙，再看坦克（同一帧不允许穿墙命中）
                if walls:
                    for bullet in bullets.sprites():
                        if obstacle_at(bullet.pos, walls, BULLET_RADIUS):
                            explosions.add(Explosion(bullet.pos.x, bullet.pos.y,
                                                     GRAY, 14, 10))
                            bullet.kill()

                # 子弹命中判定
                for bullet in bullets.sprites():
                    for tank in (tank1, tank2):
                        if tank is bullet.owner or not tank.alive:
                            continue
                        if bullet.touches(tank):
                            hit_point = pygame.Vector2(bullet.pos)
                            bullet.kill()
                            explosions.add(Explosion(hit_point.x, hit_point.y,
                                                     YELLOW, 26, 14))
                            shake = max(shake, 5.0)
                            shielded = tank.invuln > 0    # 复活无敌：这一发不掉血
                            owner_s = round_stats[side_key(bullet.owner)]
                            if shielded:
                                owner_s["shield_hits"] += 1
                            else:
                                owner_s["hits"] += 1
                            if coach is not None:
                                coach.on_hit(by_human=(bullet.owner is tank1),
                                             shield=shielded, bullet=bullet)
                            if tank.take_hit(DAMAGE):
                                if tank.pad is not None:
                                    tank.pad.rumble(PAD_RUMBLE_MS, 0.8)
                                explosions.add(Explosion(tank.pos.x, tank.pos.y,
                                                         tank.color, 72, 28))
                                shake = max(shake, 15.0)
                                tank.kill()  # 移出精灵组，不再绘制
                                owner_s["kills"] += 1
                                round_stats[side_key(tank)]["deaths"] += 1
                                if coach is not None:
                                    coach.on_death(victim_is_human=(tank is tank1))
                                if tank.lose_life():
                                    point, angle = safe_respawn_point(
                                        tank1 if tank is tank2 else tank2,
                                        tank.respawn_point)
                                    tank.respawn_point = point
                                    tank.respawn_angle = angle
                                    print(f"[life] {tank_label(tank)} 被击毁，"
                                          f"剩 {tank.lives} 条命，"
                                          f"{RESPAWN_DELAY / FPS:.1f} 秒后复活")
                                elif mode == "1p":
                                    winner = ("YOU WIN!" if tank is tank2
                                              else "CPU WINS!")
                                    print(f"[life] {tank_label(tank)} 命已用尽，"
                                          f"本局结束")
                                    finish_round(won=(tank is tank2))
                                else:
                                    winner = ("P1 (red) wins!" if tank is tank2
                                              else "P2 (blue) wins!")
                                    print(f"[life] {tank_label(tank)} 命已用尽，"
                                          f"本局结束")
                                    capture_casualties()
                                    reset_post_menu()
                            break

                shake = max(0.0, shake - 0.9)
                if toast["frames"] > 0:
                    toast["frames"] -= 1

        for tank in (tank1, tank2):
            tank.sync_image()

        # --- 绘制 ---
        draw_background(world)
        obstacles.draw(world)      # 掩体画在背景之上、坦克之下
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
                # 等待复活：在**实际复活点**上显示倒计时（掉命时被改过）
                secs = tank.respawn_timer / FPS
                tip = small_font.render(f"RESPAWN {secs:.1f}s", True, tank.color)
                world.blit(tip, tip.get_rect(center=(round(tank.respawn_point.x),
                                                     round(tank.respawn_point.y))))

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

        # 模式提示（键盘 F2/F3；有手柄时另见底栏 Back / R-stick）
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
                          "LB+RB 1P-2P, LB+X AI, RB+Y map, Start(+)/fire")
        else:
            pad_text = "No gamepad - plug in a controller (auto-detect)"
        pad_hint = small_font.render(pad_text, True, GRAY)
        screen.blit(pad_hint, pad_hint.get_rect(center=(SCREEN_WIDTH // 2,
                                                       SCREEN_HEIGHT - 20)))

        # 血量数值 + 炮口角度 / 瞄准残差 / 到对手距离 + 剩余命数（一排小方块，扣掉的变暗）
        # ANG/ERR 是给玩家的直接反馈：键盘一格原来是 4°，现在点按是 1°，
        # 有了 ERR 才知道自己到底差几度，而不是「按了没反应 / 怎么都对不上」。
        # DIST = 到对手的实时距离（px），超过 AI_FIRE_RANGE 打的都是废弹
        # （子弹先出屏），不用再靠目测估距离开枪。
        # 文案唯一出处是 hud_text()，_preview.py 截图用的是同一份。
        for tank, is_left in ((tank1, True), (tank2, False)):
            other = tank2 if tank is tank1 else tank1
            color = (200, 120, 120) if is_left else (120, 150, 220)
            text = small_font.render(hud_text(tank, other), True, color)
            screen.blit(text, (12, 34) if is_left
                        else (SCREEN_WIDTH - text.get_width() - 12, 34))
        draw_lives(screen, tank1, 12, 54)
        draw_lives(screen, tank2,
                   SCREEN_WIDTH - 12 - lives_pips_width(), 54)

        # 主菜单 / 等待开始 / 胜负提示
        if phase == "main" and bind_state["slot"] is None:
            title = big_font.render(
                "TANK DUEL" if tank_coach.ASCII_ONLY else "坦克对战", True, WHITE)
            screen.blit(title, title.get_rect(
                center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 150)))
            status = font.render(
                f"AI {AI_SKILLS[ai_skill]['label']}   Map {map_label(map_index)}"
                if tank_coach.ASCII_ONLY else
                f"难度 {AI_SKILLS[ai_skill]['label']}　地图 {map_label(map_index)}",
                True, GRAY)
            screen.blit(status, status.get_rect(
                center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 108)))
            labels = main_menu_item_labels(tank_coach.ASCII_ONLY)
            for i, label in enumerate(labels):
                if i == main_menu_index:
                    color, prefix = YELLOW, "> "
                else:
                    color, prefix = WHITE, "  "
                row = font.render(prefix + label, True, color)
                screen.blit(row, row.get_rect(
                    center=(SCREEN_WIDTH // 2,
                            SCREEN_HEIGHT // 2 - 60 + i * 30)))
            hint = small_font.render(
                "Up/Down  Enter/Space  Start(+)/Y  1-6"
                if tank_coach.ASCII_ONLY else
                "上下选择  Enter/空格  Start(+)/Y 确认  数字键 1-6",
                True, GRAY)
            screen.blit(hint, hint.get_rect(
                center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 140)))
        elif awaiting_start and bind_state["slot"] is None:
            if mode == "1p":
                title = (f"1P vs CPU ({AI_SKILLS[ai_skill]['label']})"
                         if tank_coach.ASCII_ONLY else
                         f"单人 vs 电脑（{AI_SKILLS[ai_skill]['label']}）")
            else:
                title = "2P Duel" if tank_coach.ASCII_ONLY else "双人对战"
            text = big_font.render(title, True, WHITE)
            sub = font.render(
                "Start (+) / R / fire to begin    "
                "F2/LB+RB mode  F3/LB+X AI", True, GRAY)
            map_line = font.render(
                f"Map: {map_label(map_index)}  (M / RB+Y)", True, GRAY)
            screen.blit(text, text.get_rect(
                center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 28)))
            screen.blit(sub, sub.get_rect(
                center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 10)))
            screen.blit(map_line, map_line.get_rect(
                center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 40)))
        elif winner:
            if post_view == "report" and report_cache:
                title = big_font.render(
                    "BATTLE REPORT" if tank_coach.ASCII_ONLY else "战报",
                    True, WHITE)
                screen.blit(title, title.get_rect(
                    center=(SCREEN_WIDTH // 2, 56)))
                h = 32 + 18 * len(report_cache)
                card_w = min(520, SCREEN_WIDTH - 40)
                draw_coach_card(screen, font, small_font, "BATTLE REPORT",
                                report_cache,
                                (SCREEN_WIDTH - card_w) // 2,
                                max(90, (SCREEN_HEIGHT - h) // 2 - 20),
                                width=card_w)
                tip = small_font.render(
                    "Esc / Back — return to menu" if tank_coach.ASCII_ONLY
                    else "Esc / Back(-) — 返回菜单", True, GRAY)
                screen.blit(tip, tip.get_rect(
                    center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT - 48)))
            else:
                text = big_font.render(winner, True, WHITE)
                screen.blit(text, text.get_rect(
                    center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 118)))
                for i, line in enumerate(casualty_lines):
                    row = font.render(
                        line, True,
                        (200, 120, 120) if i == 0 else (120, 150, 220))
                    screen.blit(row, row.get_rect(
                        center=(SCREEN_WIDTH // 2,
                                SCREEN_HEIGHT // 2 - 72 + i * 26)))
                labels = post_menu_item_labels(tank_coach.ASCII_ONLY)
                for i, label in enumerate(labels):
                    enabled = post_item_enabled(i)
                    if i == post_menu_index:
                        color = YELLOW if enabled else (140, 120, 60)
                        prefix = "> "
                    else:
                        color = WHITE if enabled else (90, 90, 100)
                        prefix = "  "
                    row = font.render(prefix + label, True, color)
                    screen.blit(row, row.get_rect(
                        center=(SCREEN_WIDTH // 2,
                                SCREEN_HEIGHT // 2 + 8 + i * 28)))
                hint = small_font.render(
                    "Up/Down  Enter/Space  Start(+)/Y  1-5"
                    if tank_coach.ASCII_ONLY else
                    "上下选择  Enter/空格  Start(+)/Y 确认  数字键 1-5",
                    True, GRAY)
                screen.blit(hint, hint.get_rect(
                    center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 160)))

        # 表现评估：仅对局中 F5 实时面板（局末战报改走菜单，不自动挂左下）
        if coach is not None and coach_panel and winner is None:
            draw_coach_card(screen, font, small_font,
                            f"COACH (F5) - {coach.skill_label}",
                            coach.hud_lines(), 12, 76, width=300)

        # 即时纠正提示（新触发的误用只在屏幕上挂 2.5 秒，不打断对局）
        if toast["frames"] > 0 and toast["text"]:
            tip = small_font.render(_fit_text(small_font, toast["text"], 560),
                                    True, (250, 196, 130))
            bg = pygame.Surface((tip.get_width() + 16, tip.get_height() + 8),
                                pygame.SRCALPHA)
            bg.fill((12, 12, 16, 210))
            screen.blit(bg, bg.get_rect(center=(SCREEN_WIDTH // 2, 74)))
            screen.blit(tip, tip.get_rect(center=(SCREEN_WIDTH // 2, 74)))

        pygame.display.flip()
        # 实测帧间隔：掉帧（字体渲染 / F5 面板）时转向与车速保持真实时间一致。
        # 首帧、拖窗口、配置面板暂停造成的异常间隔（<=0 或 >0.1s）按标准帧算，
        # 免得一帧跳过几十度。
        elapsed = clock.tick(FPS) / 1000.0
        dt = elapsed if 0.0 < elapsed <= 0.1 else 1.0 / FPS

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
