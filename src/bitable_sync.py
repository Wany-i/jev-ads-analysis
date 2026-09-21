"""把分析结果同步到飞书多维表格（Base）。

为什么选多维表格做分析载体：
- 分歧案例需要人看，表格天然适合筛选/分组/批注
- 一层「是否分歧」筛选视图就能把需要复核的案例挑出来
- 规则层依据与 Jev 输出并排，人能直接对比两层的判断

用法：
    python src/bitable_sync.py --base-token <token> --table-id <id> \
        --input output/pipeline-output.json --as user
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

CLI = shutil.which("lark-cli") or shutil.which("lark-cli.cmd")

# 同步到 Base 的字段（与 creates 的 schema 对齐）
SYNC_FIELDS = [
    "关键词", "站点", "匹配方式", "曝光", "点击", "花费", "销售额", "订单",
    "运行天数", "当前出价", "目标ACOS", "实测ACOS", "盈亏平衡ACOS", "可接受CPC",
    "动态点击阈值", "规则层动作", "规则层依据", "Jev动作", "Jev置信度",
    "Jev紧迫度", "无效花费概率", "是否分歧", "处置",
]


def _to_record(row: dict) -> dict:
    """把流水线输出的一行转成 Base 的 fields 对象。

    CellValue 约定：select 传字符串名，number 传数值，checkbox 传布尔。
    空值不传（交给 Base 保持为空），避免写入无效类型。
    """
    fields: dict[str, Any] = {}
    for k in SYNC_FIELDS:
        v = row.get(k)
        if v is None or v == "" or v == "未接入":
            continue
        # 比例类字段转成百分数更利于表格阅读
        if k in ("目标ACOS", "实测ACOS", "盈亏平衡ACOS"):
            if isinstance(v, (int, float)) and v >= 0:
                fields[k] = round(float(v), 4)
                continue
        fields[k] = v
    return {"fields": fields}


def build_payload(rows: list[dict]) -> list[dict]:
    return [_to_record(r) for r in rows if r.get("关键词")]


def sync(base_token: str, table_id: str, rows: list[dict], *, as_identity: str = "user",
         dry_run: bool = False) -> dict:
    if not CLI:
        raise RuntimeError("未找到 lark-cli，请先安装并登录")
    records = build_payload(rows)
    if not records:
        return {"ok": False, "error": "没有可同步的记录"}

    body = json.dumps(records, ensure_ascii=False)
    cmd = [
        CLI, "base", "+record-batch-create",
        "--base-token", base_token,
        "--table-id", table_id,
        "--json", body,
        "--as", as_identity,
        "--format", "json",
    ]
    if dry_run:
        return {"ok": True, "dry_run": True, "count": len(records), "command_preview": cmd[:6]}

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        return {"ok": False, "returncode": proc.returncode, "stderr": proc.stderr[-800:]}
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
    ap.add_argument("--dry-run", action="store_true", help="只打印将发送的记录数，不写入")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    rows = data.get("rows", data if isinstance(data, list) else [])
    result = sync(args.base_token, args.table_id, rows, as_identity=args.identity,
                  dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
