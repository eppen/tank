"""版本号唯一来源：启动日志、pyproject 与 MSIX 打包都以这里为准。

改版本时只改 ``__version__``。``_selftest_version.py`` 会检查
``pyproject.toml`` 与 ``packaging/windows/build_msix.ps1`` 没有再次手写
版本号，CI / run_selftests.py 都会跑这条检查。
"""

from __future__ import annotations

__version__ = "1.0.0"


def msix_version() -> str:
    """MSIX / AppxManifest 要求四段数字版本；三段版本补一个 .0。"""
    parts = [p for p in __version__.split(".") if p]
    parts += ["0"] * (4 - len(parts))
    return ".".join(parts[:4])
