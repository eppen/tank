#!/usr/bin/env bash
# 在 macOS 上打一份同结构 onedir，验证 frozen + LocalAppData 路径逻辑。
# 不能替代 Windows exe / MSIX，但可在无 Windows 时冒烟路径与启动。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

python3.11 -m pip install -q -r packaging/windows/requirements-build.txt
rm -rf dist/TankGame build/tank_game
python3.11 -m PyInstaller packaging/windows/tank_game.spec --noconfirm

APP="$ROOT/dist/TankGame/TankGame"
if [[ ! -f "$APP" ]]; then
  echo "Looking for binary under dist/TankGame ..."
  ls -la dist/TankGame
  APP="$(find dist/TankGame -maxdepth 1 -type f -perm +111 | head -1)"
fi
echo "Binary: $APP"

PROBE_ROOT="$(mktemp -d)"
export LOCALAPPDATA="$PROBE_ROOT/Local"
mkdir -p "$LOCALAPPDATA"
export TANK_SKIP_MENU=1
export TANK_MODE=2p
export SDL_VIDEODRIVER=dummy
export SDL_AUDIODRIVER=dummy
LOG="$PROBE_ROOT/run.log"

# 前台短跑：runw（无控制台）可能 fork，不要用后台 PID+kill
perl -e 'alarm shift; exec @ARGV' 5 "$APP" >"$LOG" 2>&1 || true

DATA="$LOCALAPPDATA/TankGame"
if [[ ! -d "$DATA" ]]; then
  echo "FAIL: expected data dir $DATA (frozen 启动应 makedirs)"
  echo "---- log ----"
  cat "$LOG" || true
  exit 1
fi
# windowed bootloader 常吞掉 stdout；目录出现即证明 data_dir() 走了打包分支
echo "MAC_SMOKE_DATA_DIR_OK $DATA"
echo "MAC_SMOKE_ALL_OK"
