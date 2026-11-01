"""可写数据目录：开发态脚本旁 / 冻结态 LocalAppData / 环境变量覆盖。"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile

import tank_paths


def _reload_data_modules():
    """环境变量变更后，重载路径相关模块以刷新模块级常量。"""
    importlib.reload(tank_paths)
    import tank_career
    import tank_coach

    importlib.reload(tank_career)
    importlib.reload(tank_coach)
    return tank_career, tank_coach


def main():
    here = os.path.dirname(os.path.abspath(__file__))

    # 1) 开发态：数据目录 = 源码目录
    assert not tank_paths.frozen()
    assert tank_paths.bundle_dir() == here
    assert tank_paths.data_dir() == here
    assert tank_paths.data_file("tank_keys.json") == os.path.join(here, "tank_keys.json")
    print("PATH_DEV_OK", tank_paths.data_dir())

    # 2) TANK_DATA_DIR 覆盖整个根
    with tempfile.TemporaryDirectory() as td:
        os.environ["TANK_DATA_DIR"] = td
        career, coach = _reload_data_modules()
        assert tank_paths.data_dir() == os.path.abspath(td)
        assert career.CAREER_PATH == os.path.join(td, "tank_career.json")
        assert coach.COACH_HISTORY_PATH == os.path.join(td, "tank_coach.json")
        career.save(career.blank_career())
        assert os.path.isfile(career.CAREER_PATH)
        print("PATH_DATA_DIR_OK", td)
        del os.environ["TANK_DATA_DIR"]

    # 3) 单文件环境变量仍优先
    with tempfile.TemporaryDirectory() as td:
        career_f = os.path.join(td, "custom_career.json")
        os.environ["TANK_CAREER_FILE"] = career_f
        career, _ = _reload_data_modules()
        assert career.CAREER_PATH == career_f
        career.save(career.blank_career())
        assert os.path.isfile(career_f)
        print("PATH_ENV_FILE_OK", career_f)
        del os.environ["TANK_CAREER_FILE"]

    # 4) 模拟冻结：写到 LOCALAPPDATA/TankGame（不 reload，直接打补丁）
    with tempfile.TemporaryDirectory() as td:
        fake_local = os.path.join(td, "Local")
        os.makedirs(fake_local)
        os.environ["LOCALAPPDATA"] = fake_local
        real_frozen = tank_paths.frozen
        tank_paths.frozen = lambda: True  # type: ignore[assignment]
        try:
            d = tank_paths.data_dir()
            expect = os.path.join(fake_local, tank_paths.APP_DIR_NAME)
            assert d == expect, (d, expect)
            f = tank_paths.data_file("tank_keys.json")
            assert f == os.path.join(expect, "tank_keys.json")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write("{}")
            assert os.path.isfile(f)
            print("PATH_FROZEN_LOCALAPPDATA_OK", d)
        finally:
            tank_paths.frozen = real_frozen  # type: ignore[assignment]
            del os.environ["LOCALAPPDATA"]

    for k in ("TANK_DATA_DIR", "TANK_CAREER_FILE", "TANK_COACH_FILE", "TANK_KEYS_FILE"):
        os.environ.pop(k, None)
    _reload_data_modules()
    print("PATH_ALL_OK")


if __name__ == "__main__":
    main()
    sys.exit(0)
