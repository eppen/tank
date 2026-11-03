#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双履 Twin Treads —— 自测聚合入口。

把仓库里所有 ``_selftest*.py`` / ``_preview.py`` / ``_sim_coach.py`` 用
**子进程**逐个跑一遍，统一注入 SDL dummy 驱动，收集退出码并汇总。

为什么用子进程而不是直接 import：
  * 绝大多数自测脚本是「顶层断言 + import 副作用」，import 即执行；
  * ``_selftest_font.py`` 明确要求只在 ``__main__`` 下运行，否则子进程
    ``import _selftest_font`` 会无限递归；
  * ``tank_game`` 在 import 时就 ``pygame.init()`` + ``set_mode()``，
    需要先设好 ``SDL_VIDEODRIVER=dummy``。
  子进程隔离是唯一安全且互不污染的方式。

用法：
  python run_selftests.py            # 跑全部自测，末尾打印「通过 N / 失败 M」
  python run_selftests.py --list     # 只列出脚本与说明，不执行
  python run_selftests.py --quick    # 跳过耗时较长的 AI / coach 回归
  python run_selftests.py a b c      # 只跑指定的脚本（可给名字或路径）

退出码：全部通过返回 0，任一失败 / 超时返回 1。
"""

from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 需要纳入聚合的自测脚本（按名字排序，稳定输出）。
SELFTEST_GLOBS = ("_selftest*.py",)
EXTRA_SCRIPTS = ("_preview.py", "_sim_coach.py")

# 跳过这些脚本时，--quick 仍会跑其余的快速自测。
QUICK_SKIP = {"_selftest_ai.py", "_sim_coach.py", "_selftest_coach.py"}

# 单个脚本的超时（秒）。AI / coach 回归跑上千帧，给更宽的预算。
DEFAULT_TIMEOUT = 180
SLOW_TIMEOUT = 420

# 少数脚本没有模块 docstring，这里补一份说明（仅用于 --list 展示）。
DESCRIPTION_FALLBACK = {
    "_selftest.py": "核心机制自检：冷却 / 开火、掩体、转向等基础断言。",
    "_selftest_main.py": "主循环路径：击毁 → 胜负提示 → 按 R 重开。",
    "_preview.py": "渲染预览：把战场画到 Surface 上做冒烟检查。",
}


def discover() -> list[str]:
    """按名字排序返回要跑的脚本文件名（去重、只保留存在的）。"""
    names: list[str] = []
    for pat in SELFTEST_GLOBS:
        names.extend(p.name for p in sorted(ROOT.glob(pat)))
    names.extend(EXTRA_SCRIPTS)
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        if (ROOT / n).is_file():
            out.append(n)
    return out


def describe(name: str) -> str:
    """取脚本模块 docstring 的第一行；没有则用兜底说明。"""
    path = ROOT / name
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        doc = ast.get_docstring(tree)
        if doc:
            first = doc.strip().splitlines()[0].strip()
            if first:
                return first
    except (OSError, SyntaxError):
        pass
    return DESCRIPTION_FALLBACK.get(name, "（无说明）")


def is_slow(name: str) -> bool:
    return name in QUICK_SKIP


def timeout_for(name: str) -> int:
    return SLOW_TIMEOUT if is_slow(name) else DEFAULT_TIMEOUT


def build_env() -> dict[str, str]:
    """在当前环境基础上注入 SDL dummy 驱动（脚本自己设的值优先级更高也无妨）。"""
    env = dict(os.environ)
    env.setdefault("SDL_VIDEODRIVER", "dummy")
    env.setdefault("SDL_AUDIODRIVER", "dummy")
    # 让子进程能 import 到仓库根的模块。
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def run_one(name: str, env: dict[str, str]) -> tuple[str, int, float, str]:
    """跑单个脚本，返回 (名字, 退出码, 耗时秒, 失败摘要)。"""
    cmd = [sys.executable, name]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_for(name),
        )
        code = proc.returncode
        output = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return name, 1, time.monotonic() - t0, f"超时（>{timeout_for(name)}s）"
    except OSError as exc:
        return name, 1, time.monotonic() - t0, f"无法启动：{exc}"

    elapsed = time.monotonic() - t0
    if code == 0:
        return name, 0, elapsed, ""
    # 失败时抓最后几行，便于定位。
    lines = [ln for ln in output.strip().splitlines() if ln.strip()]
    tail = " | ".join(lines[-3:]) if lines else "（无输出）"
    return name, code, elapsed, tail


def do_list(names: list[str]) -> int:
    print(f"双履 Twin Treads —— 自测清单（共 {len(names)} 个）\n")
    width = max(len(n) for n in names)
    for n in names:
        flag = "  [慢]" if is_slow(n) else ""
        print(f"  {n:<{width}}  {describe(n)}{flag}")
    print(
        "\n运行全部：python run_selftests.py\n"
        "跳过慢速回归：python run_selftests.py --quick"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="聚合运行双履 Twin Treads 的全部自测脚本。"
    )
    parser.add_argument("--list", action="store_true", help="只列出脚本与说明，不执行")
    parser.add_argument(
        "--quick", action="store_true", help="跳过耗时较长的 AI / coach 回归"
    )
    parser.add_argument(
        "scripts", nargs="*", help="只跑指定脚本（名字如 _selftest_ai.py）"
    )
    args = parser.parse_args(argv)

    all_names = discover()

    if args.list:
        return do_list(all_names)

    if args.scripts:
        # 允许传名字或路径，统一成文件名。
        targets: list[str] = []
        for s in args.scripts:
            base = Path(s).name
            if base in all_names:
                targets.append(base)
            else:
                print(f"错误：未知脚本 {s!r}（用 --list 查看可用脚本）", file=sys.stderr)
                return 1
        names = targets
    else:
        names = [n for n in all_names if not (args.quick and is_slow(n))]

    if not names:
        print("没有可运行的自测脚本。", file=sys.stderr)
        return 1

    env = build_env()
    print(
        f"双履 Twin Treads 自测：共 {len(names)} 个脚本"
        f"（解释器 {sys.executable}）\n"
        f"SDL_VIDEODRIVER={env.get('SDL_VIDEODRIVER')} "
        f"SDL_AUDIODRIVER={env.get('SDL_AUDIODRIVER')}\n"
    )

    passed: list[str] = []
    failed: list[tuple[str, int, float, str]] = []
    for i, name in enumerate(names, 1):
        print(f"[{i}/{len(names)}] {name} ...", end=" ", flush=True)
        name_out, code, elapsed, summary = run_one(name, env)
        if code == 0:
            passed.append(name_out)
            print(f"OK  ({elapsed:.1f}s)")
        else:
            failed.append((name_out, code, elapsed, summary))
            print(f"FAIL (exit={code}, {elapsed:.1f}s)")
            print(f"        {summary}")

    total = len(names)
    print("\n" + "=" * 60)
    print(f"通过 {len(passed)} / 失败 {len(failed)}  （共 {total}）")
    if failed:
        print("失败脚本：")
        for name_out, code, _elapsed, summary in failed:
            print(f"  - {name_out} (exit={code}): {summary}")
        print("=" * 60)
        return 1
    print("全部通过 ✅")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
