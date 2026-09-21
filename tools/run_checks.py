"""本地一键跑全部检查（等价于 CI）。

    python tools/run_checks.py
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

STEPS = [
    ("单元测试", [sys.executable, "-m", "unittest", "discover", "-s", "tests"]),
    ("仓库卫生检查", [sys.executable, "tools/check_repo.py"]),
    ("端到端干跑", [sys.executable, "src/pipeline.py",
                    "--input", "examples/input-sample.csv",
                    "--price", "39.99", "--cost", "8", "--fba", "7.5", "--first-leg", "3",
                    "--commission-rate", "15", "--return-rate", "5",
                    "--target-acos", "25", "--running-days", "30", "--bid", "0.85",
                    "--out", "output/_checks.json"]),
]


def main() -> int:
    failed = []
    for name, cmd in STEPS:
        print(f"\n{'=' * 56}\n▶ {name}\n{'=' * 56}")
        r = subprocess.run(cmd, cwd=ROOT)
        if r.returncode != 0:
            failed.append(name)
    print(f"\n{'=' * 56}")
    if failed:
        print(f"✗ 失败：{'、'.join(failed)}")
        return 1
    print("✓ 全部检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
