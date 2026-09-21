"""仓库卫生与完整性检查（CI 用，零依赖）。

这些检查对应的是**真实踩过的坑**，不是假想的规范：

1. 0 字节文件 —— 实测有过一个 0 字节的「盈亏平衡线」被误提交，
   是命令里的中文被当成重定向目标产生的，打包时才被发现
2. 根目录无扩展名文件 —— 同上，这类文件通常是命令事故的产物
3. base-schema.json 完整性 —— 它是重建多维表格的唯一依据，坏了就重建不了
4. 文档链接完整性 —— README/docs 里指向的仓库内文件必须存在
5. Python 语法 —— 编译一遍，比等到运行时才发现强
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

# Windows 控制台默认是 OEM 代码页（简中为 GBK），直接 print 中文/符号会 UnicodeEncodeError。
# CI 在 Linux 上是 UTF-8，但本地也要能跑，所以显式重设输出编码。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
FAILED: list[str] = []
PASSED: list[str] = []


def ok(msg: str) -> None:
    PASSED.append(msg)


def fail(msg: str) -> None:
    FAILED.append(msg)


def tracked_files() -> list[str]:
    """走 git，只检查真正会进发布包的文件。"""
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        # 非 git 环境（例如下载的 zip 解压后）退回遍历文件系统
        return [str(p.relative_to(ROOT)).replace("\\", "/")
                for p in ROOT.rglob("*")
                if p.is_file() and ".git" not in p.parts and "dist" not in p.parts]
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


# ---------------------------------------------------------------- 1. 0 字节文件
def check_empty_files(files: list[str]) -> None:
    bad = []
    for f in files:
        p = ROOT / f
        if p.is_file() and p.stat().st_size == 0:
            bad.append(f)
    if bad:
        fail("存在 0 字节文件（通常是命令事故的产物）:\n    " + "\n    ".join(bad))
    else:
        ok(f"无 0 字节文件（检查了 {len(files)} 个）")


# ------------------------------------------------------- 2. 根目录可疑文件名
SUSPICIOUS_ROOT = re.compile(r"^[^\x00-\x7F]+$")   # 纯非 ASCII 的根级文件名


def check_suspicious_names(files: list[str]) -> None:
    bad = []
    for f in files:
        if "/" in f or "\\" in f:
            continue
        stem = Path(f).stem
        # 根目录出现「无扩展名 + 纯非 ASCII」的文件，基本可以断定是事故产物
        if "." not in f and SUSPICIOUS_ROOT.match(stem):
            bad.append(f)
    if bad:
        fail("根目录存在可疑文件（无扩展名 + 非 ASCII，疑似重定向事故）:\n    " + "\n    ".join(bad))
    else:
        ok("根目录无可疑文件名")


# ----------------------------------------------------------- 3. base-schema.json
REQUIRED_FIELDS = {"关键词", "规则层动作", "规则层依据", "Jev动作", "Jev置信度", "是否分歧", "处置"}
JEV_DESC_FIELDS = {"Jev置信度", "Jev紧迫度", "无效花费概率"}


def check_base_schema() -> None:
    p = ROOT / "src" / "base-schema.json"
    if not p.is_file():
        fail("缺少 src/base-schema.json")
        return
    try:
        schema = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"base-schema.json 不是合法 JSON: {e}")
        return

    names = {f.get("name") for f in schema}
    missing = REQUIRED_FIELDS - names
    if missing:
        fail(f"base-schema.json 缺少必需字段: {sorted(missing)}")
    else:
        ok(f"base-schema.json 合法，{len(schema)} 个字段，必需字段齐全")

    # 三个 Jev 字段必须带 description（表内注释的唯一来源）
    no_desc = [n for n in JEV_DESC_FIELDS if n in names
               and not next(f for f in schema if f.get("name") == n).get("description")]
    if no_desc:
        fail(f"以下字段缺少 description（会导致重建的表格丢失注释）: {sorted(no_desc)}")
    else:
        ok("三个 Jev 字段均带 description")


# ------------------------------------------------------------ 4. 文档链接完整性
LINK_RE = re.compile(r"\]\((?!https?:|#)([^)]+)\)")


def check_doc_links(files: list[str]) -> None:
    md_files = [f for f in files if f.endswith(".md")]
    broken: list[str] = []
    checked = 0
    for f in md_files:
        text = (ROOT / f).read_text(encoding="utf-8", errors="replace")
        for m in LINK_RE.finditer(text):
            target = m.group(1).split("#")[0].strip()
            if not target or target.startswith("mailto:"):
                continue
            resolved = (ROOT / f).parent / target
            checked += 1
            if not resolved.exists():
                broken.append(f"{f} -> {target}")
    if broken:
        fail("文档里的仓库内链接指向不存在的文件:\n    " + "\n    ".join(broken))
    else:
        ok(f"文档内链完整（检查了 {checked} 条，覆盖 {len(md_files)} 个 md）")


# ---------------------------------------------------------------- 5. Python 语法
def check_python_syntax() -> None:
    r = subprocess.run([sys.executable, "-m", "compileall", "-q", "src"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        fail("src/ 存在语法错误:\n" + (r.stdout or r.stderr)[:800])
    else:
        ok("src/ 全部通过语法编译")


def main() -> int:
    files = tracked_files()
    print(f"仓库卫生检查：{len(files)} 个已跟踪文件\n" + "=" * 56)
    check_empty_files(files)
    check_suspicious_names(files)
    check_base_schema()
    check_doc_links(files)
    check_python_syntax()

    print()
    for p in PASSED:
        print(f"  ✓ {p}")
    for f in FAILED:
        print(f"  ✗ {f}")
    print("=" * 56)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())

