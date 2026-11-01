"""生涯战绩：空文件 → 记两局 → totals/recent 正确 → 坏文件容错。

运行：python3.11 _selftest_career.py
"""

import json
import os
import tempfile

_CAREER_TMP = os.path.join(tempfile.gettempdir(), "tk_career_selftest.json")
os.environ["TANK_CAREER_FILE"] = _CAREER_TMP
try:
    os.remove(_CAREER_TMP)
except OSError:
    pass

import tank_career as c


# --------------------------------------------------------------- 1) 空起步
data = c.load(_CAREER_TMP)
assert data["totals"]["1p"]["games"] == 0
assert data["totals"]["2p"]["wins"] == 0
assert data["recent"] == []
assert c.summary_lines(data, ascii_only=True)[0].startswith("1P")
assert "No games yet" in c.recent_lines(data, ascii_only=True)[0]
print("[1] 空文件 / 缺失文件 → blank_career OK")


# --------------------------------------------------------------- 2) 记两局
ok, path = c.record_game(
    data, mode="1p", won=True, kills=3, deaths=1,
    skill="NORMAL", map_label="PILLARS", path=_CAREER_TMP)
assert ok, path
ok, path = c.record_game(
    data, mode="2p", won=False, kills=2, deaths=4,
    skill="", map_label="EMPTY", path=_CAREER_TMP)
assert ok, path

t1, t2 = data["totals"]["1p"], data["totals"]["2p"]
assert t1 == {"wins": 1, "losses": 0, "kills": 3, "deaths": 1, "games": 1}, t1
assert t2 == {"wins": 0, "losses": 1, "kills": 2, "deaths": 4, "games": 1}, t2
assert len(data["recent"]) == 2
assert data["recent"][0]["mode"] == "1p" and data["recent"][0]["won"] is True
assert data["recent"][1]["mode"] == "2p" and data["recent"][1]["won"] is False

# 读回磁盘
reloaded = c.load(_CAREER_TMP)
assert reloaded["totals"]["1p"]["wins"] == 1
assert reloaded["totals"]["2p"]["losses"] == 1
assert len(reloaded["recent"]) == 2
lines = c.panel_lines(reloaded, ascii_only=True)
assert any("W1-L0" in line for line in lines), lines
assert any("2P L" in line or "2P" in line for line in lines)
print("[2] 1p 胜 + 2p 负 → totals/recent/落盘 OK")


# --------------------------------------------------------------- 3) recent 上限
for i in range(25):
    c.record_game(data, mode="1p", won=(i % 2 == 0), kills=1, deaths=0,
                  skill="EASY", map_label="CORRIDORS", path=_CAREER_TMP)
assert len(data["recent"]) == c.MAX_RECENT == 20
assert data["totals"]["1p"]["games"] == 1 + 25
print("[3] recent FIFO 截断到 20 OK")


# --------------------------------------------------------------- 4) 坏文件容错
with open(_CAREER_TMP, "w", encoding="utf-8") as f:
    f.write("{not-json")
bad = c.load(_CAREER_TMP)
assert bad["totals"]["1p"]["games"] == 0 and bad["recent"] == []

with open(_CAREER_TMP, "w", encoding="utf-8") as f:
    json.dump(["wrong"], f)
bad2 = c.load(_CAREER_TMP)
assert bad2["version"] == 1 and bad2["recent"] == []
print("[4] 坏 JSON / 非对象 → 空战绩起步 OK")


# --------------------------------------------------------------- 5) 主菜单常量对齐
import tank_game as g

assert g.MAIN_MENU_CAREER == 4
assert g.MAIN_MENU_KEYS == 5
assert g.MAIN_MENU_QUIT == 6
assert g.MAIN_MENU_COUNT == 7
labels = g.main_menu_item_labels(ascii_only=True)
assert labels[4].startswith("5"), labels
assert "Career" in labels[4]
assert labels[6].startswith("7")
# 子菜单退出键 = 人脸 X（与暂停键共用 pause_face_edge）
assert g.PAD_PAUSE_FACE == __import__("pygame").CONTROLLER_BUTTON_X
print("[5] 主菜单 7 项含 Career OK；子菜单退出 = pad X")

try:
    os.remove(_CAREER_TMP)
except OSError:
    pass
print("CAREER OK")
