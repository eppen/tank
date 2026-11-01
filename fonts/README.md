# 打包用中文字体（可选）

把可商用的 CJK 字体放到本目录，文件名优先使用其一：

- `NotoSansSC-Regular.otf`
- `NotoSansCJK-Regular.ttc`
- `SourceHanSansSC-Regular.otf`

`tank_game.py` 会优先从打包目录的 `fonts/` 加载。Windows 系统一般已有微软雅黑，不附带字体也能显示中文；附带字体是为了 MSIX 审核机 / 精简系统环境更稳。

请自行确认字体许可（例如 [SIL OFL](https://scripts.sil.org/OFL) 的 Noto / Source Han）。本仓库不附带字体二进制。
