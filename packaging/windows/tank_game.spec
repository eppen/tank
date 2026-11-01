# -*- mode: python ; coding: utf-8 -*-
# 在仓库根目录（tank/）执行：
#   pyinstaller packaging/windows/tank_game.spec
# 产物：dist/TankGame/TankGame.exe（onedir，便于打 MSIX）

import os

block_cipher = None
root = os.path.abspath(SPECPATH + "/../..")
fonts_dir = os.path.join(root, "fonts")

datas = []
if os.path.isdir(fonts_dir):
    # 把 fonts/ 拷进包内，供 tank_game 的 bundle 字体候选使用
    datas.append((fonts_dir, "fonts"))

a = Analysis(
    [os.path.join(root, "tank_game.py")],
    pathex=[root],
    binaries=[],
    datas=datas,
    hiddenimports=["tank_career", "tank_coach", "tank_paths"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["_selftest", "_selftest_ai", "_selftest_body", "_selftest_career",
              "_selftest_coach", "_selftest_font", "_selftest_keys", "_selftest_lives",
              "_selftest_main", "_selftest_menu", "_selftest_pad", "_selftest_paths",
              "_selftest_pause", "_selftest_raw", "_selftest_turn", "_selftest_walls",
              "_preview", "_sim_coach"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TankGame",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # 游戏窗口；调试可改 True 看终端日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(os.path.dirname(SPECPATH), "assets", "TankGame.ico")
    if os.path.isfile(os.path.join(os.path.dirname(SPECPATH), "assets", "TankGame.ico"))
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TankGame",
)
