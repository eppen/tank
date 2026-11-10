"""音效自测：合成、播放、静音、无设备降级。"""

import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ.pop("TANK_SOUND", None)
os.environ.pop("TANK_SOUND_VOLUME", None)

import pygame

import tank_audio

tank_audio.pre_init()
pygame.init()

# 1) 正常初始化：全部音色都要合成成功，且时长大于 0
assert tank_audio.init(), "dummy 音频驱动下音效应能初始化"
assert tank_audio.available()
assert tank_audio.describe().startswith("音效已启用")
expected = {"fire", "hit", "wall", "explode", "shield", "respawn",
            "start", "win", "lose", "move", "select"}
assert expected <= set(tank_audio._sounds), set(tank_audio._sounds)
for name, sound in tank_audio._sounds.items():
    assert sound.get_length() > 0, name

# 2) 每个音效都能播放，不抛异常；未知名字返回 None
for name in expected:
    assert tank_audio.play(name) is not None, name
assert tank_audio.play("no-such-sound") is None

# 3) 声像与音量都在合法范围内
for x in (-100, 0, 400, 800, 900):
    pan = tank_audio.pan_for_x(x, 800)
    assert -0.7 <= pan <= 0.7, (x, pan)
assert tank_audio.set_volume(0.5) == 0.5
assert tank_audio.set_volume(9) == 1.0
assert tank_audio.set_volume("bad") == 1.0
tank_audio.set_volume(0.75)

# 4) F6 静音：静音期间不播放；恢复后能再次播放
assert not tank_audio.muted()
assert tank_audio.toggle_mute()
assert tank_audio.muted()
assert tank_audio.play("fire") is None
assert not tank_audio.toggle_mute()
assert not tank_audio.muted()
assert tank_audio.play("fire") is not None

# 5) 无音频设备时的降级：init 返回 False，play 变成空操作而不是崩溃
orig_init = pygame.mixer.init
pygame.mixer.quit()


def broken_init(*args, **kwargs):
    raise pygame.error("selftest: no audio device")


pygame.mixer.init = broken_init
try:
    assert not tank_audio.init()
    assert not tank_audio.available()
    assert tank_audio.play("fire") is None
    assert "没有可用音频设备" in tank_audio.describe()
finally:
    pygame.mixer.init = orig_init

# 恢复后还能正常工作，证明失败路径没有把状态搞坏
assert tank_audio.init()
assert tank_audio.available()

# 6) TANK_SOUND=0：模块级别的硬关闭
tank_audio._enabled = False
assert not tank_audio.init()
assert tank_audio.play("fire") is None
assert "TANK_SOUND=0" in tank_audio.describe()
tank_audio._enabled = True
assert tank_audio.init()

print("audio selftest OK")
