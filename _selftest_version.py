"""版本号单一来源检查：pyproject / MSIX 打包脚本必须与 tank_version 一致。

运行：python3.11 _selftest_version.py
"""

import re
import tomllib
from pathlib import Path

import tank_version

ROOT = Path(__file__).resolve().parent

version = tank_version.__version__
assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"版本号格式应为 X.Y.Z：{version!r}"

pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
pyproject_version = pyproject["project"]["version"]
assert pyproject_version == version, (
    f"pyproject.toml 版本 {pyproject_version!r} 与 tank_version.py "
    f"{version!r} 不一致；请只改 tank_version.__version__ 后同步 pyproject"
)

msix = tank_version.msix_version()
assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", msix), f"MSIX 版本格式不对：{msix!r}"

script = (ROOT / "packaging" / "windows" / "build_msix.ps1").read_text(
    encoding="utf-8")
assert "tank_version.py" in script, "build_msix.ps1 没有从 tank_version.py 读取版本"
assert "__version__" in script, "build_msix.ps1 没有读取 __version__"

print(f"VERSION OK：v{version}（MSIX {msix}），pyproject 与打包脚本一致")
