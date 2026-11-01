# Windows / Microsoft Store 打包

| 步骤 | 脚本 | 说明 |
| --- | --- | --- |
| 1. 打 exe | `build.ps1` | PyInstaller onedir → `dist\TankGame\` |
| 2. 冒烟 | `smoke_test.ps1` | 确认 `%LOCALAPPDATA%\TankGame\` |
| 3. MSIX | `build_msix.ps1` | 需 Windows SDK `makeappx` |
| 4. 上架 | 见 `STORE.md` | Partner Center |

无 Windows 时可用 `build_mac_smoke.sh` 验证「冻结态写 LocalAppData」逻辑（不能代替 Store 包）。

可选：将可商用 CJK 字体放入仓库根 `fonts/`（见该目录 README）。
