"""可写数据目录：开发态用脚本旁，打包 / MSIX 用 LocalAppData。

环境变量（TANK_KEYS_FILE / TANK_COACH_FILE / TANK_CAREER_FILE）仍可覆盖单文件路径。
TANK_DATA_DIR 可覆盖整个数据根目录。
"""

from __future__ import annotations

import os
import sys


APP_DIR_NAME = "TankGame"


def frozen() -> bool:
    """PyInstaller / cx_Freeze 等冻结运行时为 True。"""
    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


def bundle_dir() -> str:
    """只读资源根：冻结时为 PyInstaller _MEIPASS（或 exe 旁），否则为源码目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    if frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir() -> str:
    """可写数据根目录（战绩 / 改键 / 评估历史）。"""
    override = os.environ.get("TANK_DATA_DIR")
    if override:
        path = os.path.abspath(override)
        os.makedirs(path, exist_ok=True)
        return path

    if frozen():
        # MSIX / 安装目录只读：落到用户 LocalAppData
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if not base:
            base = os.path.join(os.path.expanduser("~"), ".local", "share")
        path = os.path.join(base, APP_DIR_NAME)
        os.makedirs(path, exist_ok=True)
        return path

    return bundle_dir()


def data_file(name: str, env_var: str | None = None) -> str:
    """返回可写数据文件完整路径；env_var 非空时优先用环境变量。"""
    if env_var:
        override = os.environ.get(env_var)
        if override:
            return override
    return os.path.join(data_dir(), name)
