"""把分析结果同步到飞书多维表格（Base）。

为什么选多维表格做分析载体：
- 分歧案例需要人看，表格天然适合筛选/分组/批注
- 一层「是否分歧」筛选视图就能把需要复核的案例挑出来
- 规则层依据与 Jev 输出并排，人能直接对比两层的判断

用法：
    python src/bitable_sync.py --base-token <token> --table-id <id> \
        --input output/pipeline-output.json --as user [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

def _resolve_cli() -> str | None:
    """定位 lark-cli 可执行文件。

    Windows 上**必须优先用原生 .exe**：`.cmd` 会经 cmd.exe 二次解析命令行，
    长 JSON 参数（我们的 create_records 载荷）会被引号规则破坏，
    表现为 "The system cannot find the file specified"。
    """
    if os.name == "nt":
        exe = shutil.which("lark-cli.exe")
        if exe:
            return exe
        cmd_path = shutil.which("lark-cli.cmd") or shutil.which("lark-cli")
        if cmd_path:
            # npm 全局布局：<prefix>\lark-cli.cmd -> <prefix>\node_modules\@larksuite\cli\bin\lark-cli.exe
            cand = Path(cmd_path).parent / "node_modules" / "@larksuite" / "cli" / "bin" / "lark-cli.exe"
            if cand.is_file():
                return str(cand)
            return cmd_path
        return None
    return shutil.which("lark-cli")


CLI = _resolve_cli()

# 同步到 Base 的字段（与建表 schema 对齐）
SYNC_FIELDS = [
    "关键词", "站点", "匹配方式", "曝光", "点击", "花费", "销售额", "订单",
    "运行天数", "当前出价", "目标ACOS", "实测ACOS", "盈亏平衡ACOS", "可接受CPC",
    "动态点击阈值", "规则层动作", "规则层依据", "规则层优先级", "Jev动作", "Jev置信度",
    "Jev紧迫度", "无效花费概率", "是否分歧", "处置",
]

# CellValue 类型约定（见 lark-base-cell-value 参考）
SELECT_FIELDS = {"站点", "匹配方式", "规则层动作", "规则层优先级", "Jev动作", "处置"}
# select 字段的合法选项白名单：值不在其中就跳过该字段。
# 教训：redhen 数据集里自动广告的 Match Type 是 "-"，一个非法值会让整批 45 条写入全部失败
# （API 报 not_found: Provide an existing option value）。宁可少写一个字段，也不要整批失败。
VALID_SELECT = {
    "站点": {"US", "UK", "DE", "JP"},
    "匹配方式": {"自动", "广泛", "词组", "精准", "商品投放"},
    "规则层动作": {"加价", "降价", "暂停", "保持", "否定"},
    "规则层优先级": {"P0", "P1", "P2"},
    "Jev动作": {"加价", "降价", "暂停", "保持", "否定"},
    "处置": {"自动执行", "人工复核", "待标定"},
}
RATIO_FIELDS = {"目标ACOS", "实测ACOS", "盈亏平衡ACOS"}


def _to_record(row: dict) -> dict:
    """把流水线输出的一行转成 Base 的字段映射（不含外层包裹）。"""
    fields: dict[str, Any] = {}
    for k in SYNC_FIELDS:
        v = row.get(k)
        if v is None or v == "" or v in ("未接入", "dry_run"):
            continue
        if k in RATIO_FIELDS and isinstance(v, (int, float)):
            fields[k] = round(float(v), 4)
        elif k in SELECT_FIELDS:
            allowed = VALID_SELECT.get(k)
            if allowed and v not in allowed:
                continue             # 非法选项直接跳过，不让整批写入失败
            fields[k] = [v]          # select 的 CellValue 必须是数组
        else:
            fields[k] = v            # text / number / checkbox 直接传
    return fields


def build_payload(rows: list[dict]) -> dict:
    """Jev 端要求 {"create_records": [...]}，不是裸数组。"""
    return {"create_records": [_to_record(r) for r in rows if r.get("关键词")]}


def sync(base_token: str, table_id: str, rows: list[dict], *,
         as_identity: str = "user", dry_run: bool = False) -> dict:
    if not CLI:
        raise RuntimeError("未找到 lark-cli，请先安装并登录")

    payload = build_payload(rows)
    records = payload["create_records"]
    if not records:
        return {"ok": False, "error": "没有可同步的记录"}

    cmd = [
        CLI, "base", "+record-batch-create",
        "--base-token", base_token,
        "--table-id", table_id,
        "--json", json.dumps(payload, ensure_ascii=False),
        "--as", as_identity,
        "--format", "json",
    ]
    if dry_run:
        return {"ok": True, "dry_run": True, "count": len(records)}

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        return {"ok": False, "returncode": proc.returncode,
                "stderr": (proc.stderr or "")[-800:], "stdout": (proc.stdout or "")[-400:]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": True, "raw": proc.stdout[-800:]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="同步分析结果到飞书多维表格")
    ap.add_argument("--base-token", required=True)
    ap.add_argument("--table-id", required=True)
    ap.add_argument("--input", required=True, help="pipeline 输出的 JSON")
    ap.add_argument("--as", dest="identity", default="user", choices=["user", "bot"])
    ap.add_argument("--dry-run", action="store_true", help="只统计将写入的记录数，不实际写入")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    rows = data.get("rows", data if isinstance(data, list) else [])
    result = sync(args.base_token, args.table_id, rows,
                  as_identity=args.identity, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())


