# Microsoft Store 上架清单（坦克大战）

本游戏为 **本地离线** pygame 桌面应用：无账号、无联网、无内购。通过 **PyInstaller onedir → MSIX（runFullTrust）→ Partner Center** 上架。

## 前置

1. [Partner Center](https://partner.microsoft.com/) 开发者账号（个人约一次性 $19）。
2. Windows 10/11 构建机：Python 3.11+、Windows SDK（`makeappx`）。
3. 本仓库已改好可写路径：打包态 JSON 写 `%LOCALAPPDATA%\TankGame\`（见 `tank_paths.py`）。

## 构建

在仓库 `tank/` 根目录：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_test.ps1
powershell -ExecutionPolicy Bypass -File packaging\windows\build_msix.ps1 `
  -IdentityName "YourPublisher.TankGame" `
  -Publisher "CN=YourPublisher" `
  -Version "1.0.0.0"
```

产物：`dist\TankGame\TankGame.exe`、`dist\TankGame.msix`。

Identity / Publisher 必须与 Partner Center 应用页生成的值一致（创建应用后可复制）。

## 商店页必填

| 项 | 建议 |
| --- | --- |
| 名称 | 坦克大战 / Tank Game |
| 简短说明 | 本地双人与人机坦克对战 |
| 说明 | 单人 AI / 双人同机；手柄与键鼠；生涯战绩本地保存 |
| 类别 | 游戏 → 动作 / 射击 |
| 年龄分级 | 按问卷如实勾选（轻度卡通战斗，通常 E10+ / 类似） |
| 隐私策略 | 声明**不收集个人数据**；存档仅本机 `%LOCALAPPDATA%\TankGame\` |
| 截图 | 至少 1 张桌面截图（可用 `tank_preview.png` 裁切） |
| 图标 | 见 `msix/Assets/README.md` |

## 能力与政策

- 清单仅声明 `runFullTrust`（桌面桥接），不申请网络/麦克风等能力。
- 不要在描述里承诺未实现的联机、成就、Xbox Live。
- 提交前用真机装一次 MSIX（或先跑 `dist\TankGame\TankGame.exe`）：主菜单、改键落盘、战绩面板、手柄。

## 审核常见拒因（本项目对照）

| 拒因 | 本仓库对策 |
| --- | --- |
| 启动即崩 | Windows 上跑 `smoke_test.ps1`；缺 VC++ 运行库时用 onedir 自带 DLL |
| 无法保存进度 | 已用 `tank_paths.data_dir()` → LocalAppData，勿再写安装目录 |
| 缺隐私说明 | 商店页写明不收集数据 |
| 素材尺寸不对 | 替换 `msix/Assets` 占位图 |

## 本机无法代提交的部分

Partner Center 登录、付款、填写问卷、上传 MSIX、应答认证邮件必须由发布者账号完成。仓库已提供可重复的打包与清单模板。
