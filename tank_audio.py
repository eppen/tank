"""程序化音效：不依赖任何外部音频素材，无音频设备时自动静音。

设计要点：
* :func:`pre_init` 必须在 ``pygame.init()`` 之前调用，设定 44.1kHz / 16bit /
  立体声 / 小缓冲；否则 pygame 会用默认参数初始化 mixer。
* 所有音色在 :func:`init` 里用正弦波、方波和噪声现场合成（总共约 3 秒音频，
  纯 Python 只算一次），所以 PyInstaller / MSIX 打包不需要额外资源文件。
* 任何一步失败（没有声卡、CI 的 dummy 驱动、mixer 未编译）都只让
  :func:`play` 变成空操作，绝不影响游戏主流程和自测脚本。

对外接口只有 :func:`pre_init` / :func:`init` / :func:`play` /
:func:`toggle_mute` / :func:`describe`。

环境变量：
* ``TANK_SOUND=0``        关闭音效（默认开）
* ``TANK_SOUND_VOLUME``   主音量 0..1（默认 0.75）
"""

from __future__ import annotations

import array
import io
import math
import os
import random
import wave

import pygame

SAMPLE_RATE = 44100
NUM_CHANNELS = 24
DEFAULT_VOLUME = 0.75

_TAU = math.tau


def _env_volume(default=DEFAULT_VOLUME):
    raw = os.environ.get("TANK_SOUND_VOLUME")
    if raw is None:
        return default
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        return default


_enabled = os.environ.get("TANK_SOUND", "1").strip().lower() not in (
    "0", "off", "false", "no")
_volume = _env_volume()
_muted = False
_ready = False
_sounds = {}


# --- 波形合成 ---
def _sweep(dur, f0, f1, amp=1.0, decay=1.5, attack=0.004, shape="sine"):
    """线性扫频音：f0 → f1，指数衰减，起音做几毫秒淡入避免爆音。"""
    n = max(1, int(SAMPLE_RATE * dur))
    out = [0.0] * n
    phase = 0.0
    for i in range(n):
        t = i / SAMPLE_RATE
        p = i / max(1, n - 1)
        freq = f0 + (f1 - f0) * p
        phase += _TAU * freq / SAMPLE_RATE
        if shape == "square":
            sample = 0.6 if math.sin(phase) >= 0 else -0.6
        elif shape == "saw":
            sample = 2.0 * ((phase / _TAU) % 1.0) - 1.0
        else:
            sample = math.sin(phase)
        env = min(1.0, t / attack) if attack > 0 else 1.0
        if decay:
            env *= (1.0 - p) ** decay
        out[i] = amp * env * sample
    return out


def _noise(dur, amp=1.0, decay=1.5, lp=0.5, seed=1):
    """噪声：lp 越小越闷（一阶低通），爆炸用低通噪声当轰鸣。"""
    n = max(1, int(SAMPLE_RATE * dur))
    rng = random.Random(seed)
    out = [0.0] * n
    prev = 0.0
    for i in range(n):
        p = i / max(1, n - 1)
        prev += lp * (rng.uniform(-1.0, 1.0) - prev)
        env = (1.0 - p) ** decay if decay else 1.0
        out[i] = amp * env * prev
    return out


def _layer(target, source, start=0.0, gain=1.0):
    """把 source 叠加到 target 的 start 秒处；target 不够长就补零。"""
    offset = int(start * SAMPLE_RATE)
    need = offset + len(source)
    if len(target) < need:
        target.extend([0.0] * (need - len(target)))
    for i, value in enumerate(source):
        target[offset + i] += value * gain
    return target


def _fit(samples, peak):
    """把整段音频归一化到指定峰值，保证各音效响度可预期、不削波。"""
    top = max((abs(v) for v in samples), default=0.0)
    if top <= 0.0:
        return samples
    scale = peak / top
    return [v * scale for v in samples]


def _wav_bytes(samples):
    data = array.array("h")
    for value in samples:
        data.append(int(max(-1.0, min(1.0, value)) * 32767))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(data.tobytes())
    buf.seek(0)
    return buf


def _sound(samples, peak):
    return pygame.mixer.Sound(file=_wav_bytes(_fit(samples, peak)))


# --- 音色 ---
def _make_fire():
    """炮击：低频下坠 + 一点金属方波 + 起音噪声。"""
    dur = 0.22
    out = _sweep(dur, 220, 60, amp=0.9, decay=2.2, attack=0.002)
    _layer(out, _sweep(dur, 460, 150, amp=0.22, decay=3.0, shape="square"))
    _layer(out, _noise(0.05, amp=0.55, decay=2.0, lp=0.9, seed=11))
    return out, 0.55


def _make_hit():
    """命中车体：金属撞击的短促“铛”。"""
    dur = 0.14
    out = _sweep(dur, 1200, 780, amp=0.5, decay=4.0)
    _layer(out, _sweep(dur, 1850, 1400, amp=0.28, decay=5.0, shape="square"))
    _layer(out, _noise(0.05, amp=0.5, decay=3.0, lp=0.6, seed=12))
    return out, 0.42


def _make_wall():
    """子弹打墙：闷响，不要抢命中音。"""
    out = _sweep(0.09, 150, 70, amp=0.9, decay=3.0)
    _layer(out, _noise(0.04, amp=0.4, decay=2.0, lp=0.3, seed=13))
    return out, 0.3


def _make_explode():
    """坦克被击毁：低通噪声轰鸣 + 低频下坠 + 开头爆裂声。"""
    dur = 0.6
    out = _noise(dur, amp=0.9, decay=1.4, lp=0.12, seed=14)
    _layer(out, _sweep(dur, 120, 30, amp=0.8, decay=1.5))
    _layer(out, _noise(0.1, amp=0.5, decay=2.5, lp=0.7, seed=15))
    return out, 0.65


def _make_shield():
    """无敌护盾弹开子弹：高音金属 ping。"""
    out = _sweep(0.16, 1400, 1900, amp=0.5, decay=2.0)
    _layer(out, _sweep(0.16, 2100, 2600, amp=0.25, decay=2.5))
    return out, 0.35


def _make_respawn():
    """复活：两段上行音。"""
    out = _sweep(0.18, 330, 660, amp=0.5, decay=0.7, attack=0.01)
    _layer(out, _sweep(0.2, 660, 1320, amp=0.4, decay=1.2), start=0.16)
    return out, 0.4


def _make_start():
    """开打号角：小三和弦长音。"""
    dur = 0.42
    out = _sweep(dur, 196, 196, amp=0.3, decay=0.8, shape="saw")
    for freq in (247.0, 294.0, 392.0):
        _layer(out, _sweep(dur, freq, freq, amp=0.22, decay=0.9, shape="saw"))
    return out, 0.45


def _make_win():
    """胜利：C 大调上行四音。"""
    out = []
    for i, freq in enumerate((523.25, 659.25, 783.99, 1046.50)):
        _layer(out, _sweep(0.22, freq, freq, amp=0.5, decay=2.4, attack=0.003),
               start=0.10 * i)
    _layer(out, _sweep(0.35, 1568.0, 1568.0, amp=0.12, decay=2.0), start=0.30)
    return out, 0.45


def _make_lose():
    """失败：下行四音。"""
    out = []
    for i, freq in enumerate((392.0, 329.63, 261.63, 196.0)):
        _layer(out, _sweep(0.2, freq, freq, amp=0.5, decay=2.0, attack=0.003),
               start=0.15 * i)
    return out, 0.45


def _make_move():
    """菜单上下移动：很轻的短促滴声。"""
    return _sweep(0.03, 1100, 1100, amp=0.5, decay=0.5, attack=0.001), 0.2


def _make_select():
    """菜单确认：两段上行小提示音。"""
    out = _sweep(0.05, 660, 660, amp=0.5, decay=1.0)
    _layer(out, _sweep(0.08, 990, 990, amp=0.5, decay=1.5), start=0.04)
    return out, 0.28


_BUILDERS = {
    "fire": _make_fire,
    "hit": _make_hit,
    "wall": _make_wall,
    "explode": _make_explode,
    "shield": _make_shield,
    "respawn": _make_respawn,
    "start": _make_start,
    "win": _make_win,
    "lose": _make_lose,
    "move": _make_move,
    "select": _make_select,
}


# --- 对外接口 ---
def pre_init():
    """在 ``pygame.init()`` 之前调用：固定混音格式，避免各平台默认值不一。"""
    try:
        pygame.mixer.pre_init(SAMPLE_RATE, -16, 2, 512)
    except Exception:
        pass


def init():
    """初始化 mixer 并合成全部音效；返回是否真正可用。"""
    global _ready
    if not _enabled:
        _ready = False
        return False
    if pygame.mixer.get_init() is None:
        try:
            pygame.mixer.init(SAMPLE_RATE, -16, 2, 512)
        except Exception:
            _ready = False
            return False
    try:
        pygame.mixer.set_num_channels(NUM_CHANNELS)
        _sounds.clear()
        for name, builder in _BUILDERS.items():
            samples, peak = builder()
            _sounds[name] = _sound(samples, peak)
    except Exception:
        _sounds.clear()
        _ready = False
        return False
    _ready = True
    return True


def available():
    return _ready


def enabled():
    return _enabled


def muted():
    return _muted


def toggle_mute():
    """F6：开/静音；返回切换后的静音状态。"""
    global _muted
    _muted = not _muted
    if not _muted:
        play("select")
    return _muted


def set_volume(value):
    """设置主音量 0..1（当前只用于测试 / 将来做音量菜单）。"""
    global _volume
    try:
        _volume = max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        pass
    return _volume


def volume():
    return _volume


def play(name, gain=1.0, pan=0.0):
    """播放音效；未初始化 / 静音 / 未知名字时安全地什么都不做。

    pan: -1 = 全左，0 = 居中，+1 = 全右。按坦克/弹着点在屏幕上的 x 做
    轻微声像，双人同屏时更容易分辨是谁在开火。
    """
    if not _ready or _muted or _volume <= 0.0:
        return None
    sound = _sounds.get(name)
    if sound is None:
        return None
    try:
        channel = sound.play()
        if channel is None:          # 24 个声道都占满时 pygame 会返回 None
            return None
        pan = max(-1.0, min(1.0, pan))
        left = _volume * gain * min(1.0, 1.0 - pan)
        right = _volume * gain * min(1.0, 1.0 + pan)
        channel.set_volume(max(0.0, min(1.0, left)),
                           max(0.0, min(1.0, right)))
        return channel
    except Exception:
        return None


def describe():
    """启动日志用的一行状态说明。"""
    if not _enabled:
        return "音效已关闭（TANK_SOUND=0）"
    if not _ready:
        return "没有可用音频设备 → 静音运行（游戏不受影响）"
    return (f"音效已启用：{len(_sounds)} 种，"
            f"主音量 {_volume:.0%}（F6 静音，TANK_SOUND_VOLUME 可调）")


def pan_for_x(x, width=800):
    """把屏幕 x 坐标折算成 -0.7..0.7 的声像。"""
    try:
        norm = max(0.0, min(1.0, float(x) / float(width)))
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0
    return (norm * 2.0 - 1.0) * 0.7
