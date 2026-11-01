"""生涯战绩：1P/2P 胜负与击毁累计 + 最近对局列表。

与 tank_coach（1p 评估分）独立；读写失败不挡开游戏。
"""

import json
import os
import time

CAREER_PATH = os.environ.get("TANK_CAREER_FILE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "tank_career.json")
MAX_RECENT = 20
# 屏幕文案：由 tank_game 同步 tank_coach.ASCII_ONLY
ASCII_ONLY = False


def _t(zh, en):
    return en if ASCII_ONLY else zh


def _blank_side():
    return {"wins": 0, "losses": 0, "kills": 0, "deaths": 0, "games": 0}


def blank_career():
    return {
        "version": 1,
        "totals": {"1p": _blank_side(), "2p": _blank_side()},
        "recent": [],
    }


def load(path=None):
    """读取生涯战绩；不存在 / 坏了都返回空结构。"""
    path = path or CAREER_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return blank_career()
    except Exception as exc:
        print(f"[career] 战绩文件读取失败（{exc}），从空战绩开始")
        return blank_career()
    if not isinstance(data, dict):
        return blank_career()
    out = blank_career()
    totals = data.get("totals")
    if isinstance(totals, dict):
        for key in ("1p", "2p"):
            side = totals.get(key)
            if isinstance(side, dict):
                for field in out["totals"][key]:
                    try:
                        out["totals"][key][field] = int(side.get(field, 0) or 0)
                    except (TypeError, ValueError):
                        pass
    recent = data.get("recent")
    if isinstance(recent, list):
        out["recent"] = [r for r in recent if isinstance(r, dict)][-MAX_RECENT:]
    out["version"] = 1
    return out


def save(career, path=None):
    """原子写入；返回 (成功, 路径或错误)。"""
    path = path or CAREER_PATH
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(career, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        return True, path
    except Exception as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False, str(exc)


def record_game(career, *, mode, won, kills, deaths, skill="", map_label="",
                path=None):
    """追加一局并落盘。mode 为 '1p'/'2p'；won/kills/deaths 以 P1（你）视角。"""
    mode = "1p" if str(mode).strip().lower() in ("1p", "1") else "2p"
    side = career.setdefault("totals", blank_career()["totals"]).setdefault(
        mode, _blank_side())
    side["games"] = int(side.get("games", 0)) + 1
    if won:
        side["wins"] = int(side.get("wins", 0)) + 1
    else:
        side["losses"] = int(side.get("losses", 0)) + 1
    side["kills"] = int(side.get("kills", 0)) + max(0, int(kills))
    side["deaths"] = int(side.get("deaths", 0)) + max(0, int(deaths))
    row = {
        "mode": mode,
        "won": bool(won),
        "skill": str(skill or ""),
        "map": str(map_label or ""),
        "kills": max(0, int(kills)),
        "deaths": max(0, int(deaths)),
        "ts": int(time.time()),
    }
    recent = career.setdefault("recent", [])
    recent.append(row)
    del recent[:-MAX_RECENT]
    career["version"] = 1
    return save(career, path=path)


def summary_lines(career, ascii_only=None):
    """主菜单战绩面板：1P/2P 各一行汇总。"""
    if ascii_only is None:
        ascii_only = ASCII_ONLY
    lines = []
    totals = (career or {}).get("totals") or {}
    for key, title_zh, title_en in (
            ("1p", "单人", "1P"),
            ("2p", "双人", "2P")):
        s = totals.get(key) or _blank_side()
        title = title_en if ascii_only else title_zh
        w, l = int(s.get("wins", 0)), int(s.get("losses", 0))
        k, d = int(s.get("kills", 0)), int(s.get("deaths", 0))
        g = int(s.get("games", 0))
        if ascii_only:
            lines.append(f"{title}  W{w}-L{l}  K{k}/D{d}  games {g}")
        else:
            lines.append(f"{title}  胜{w}负{l}  击毁{k}/被毁{d}  共{g}局")
    return lines


def recent_lines(career, n=8, ascii_only=None):
    """最近对局短行（新→旧）。"""
    if ascii_only is None:
        ascii_only = ASCII_ONLY
    rows = list((career or {}).get("recent") or [])
    if not rows:
        return ["No games yet" if ascii_only else "还没有对局记录"]
    lines = []
    for r in reversed(rows[-n:]):
        mode = r.get("mode", "?")
        won = r.get("won")
        result = ("W" if won else "L") if ascii_only else ("胜" if won else "负")
        skill = r.get("skill") or "-"
        mmap = r.get("map") or "-"
        k, d = r.get("kills", 0), r.get("deaths", 0)
        if ascii_only:
            lines.append(f"{mode.upper()} {result}  K{k}/D{d}  {mmap} {skill}")
        else:
            lines.append(f"{mode.upper()} {result}  击{k}/被{d}  {mmap} {skill}")
    return lines


def panel_lines(career, ascii_only=None):
    """战绩面板完整行（汇总 + 空行 + 最近）。"""
    if ascii_only is None:
        ascii_only = ASCII_ONLY
    lines = list(summary_lines(career, ascii_only=ascii_only))
    lines.append("")
    lines.append("Recent" if ascii_only else "最近对局")
    lines.extend(recent_lines(career, ascii_only=ascii_only))
    return lines
