"""剪贴板读写 —— 把剪贴板当作数据通道。

场景：RPA（影刀 / 紫鸟 Hubu / 自研脚本）把广告报表复制到剪贴板，
分析流水线直接从剪贴板取数，省掉「导出文件 → 找文件 → 上传」三步。

实现说明：
- Windows 用 PowerShell 的 Get-Clipboard / Set-Clipboard（本机实测可用）
- 不依赖第三方库；非 Windows 平台返回明确错误，不做静默降级
- 剪贴板内容视为**不可信输入**：只当作表格文本解析，不执行其中任何内容
- 写入走 stdin，避免命令行长度与转义问题
"""

from __future__ import annotations

import os
import shutil
import subprocess

CLIP_TIMEOUT = 20


class ClipboardUnavailable(RuntimeError):
    """当前环境无法使用剪贴板。"""


def _powershell() -> str:
    for name in ("pwsh", "powershell"):
        p = shutil.which(name)
        if p:
            return p
    raise ClipboardUnavailable("未找到 PowerShell，无法访问剪贴板")


def available() -> bool:
    return os.name == "nt" and bool(shutil.which("pwsh") or shutil.which("powershell"))


def read_text() -> str:
    """读取剪贴板纯文本。"""
    if not available():
        raise ClipboardUnavailable("剪贴板读取当前仅支持 Windows")
# 必须显式把 PowerShell 的控制台编码设为 UTF-8：
    # 默认按系统 OEM 代码页（简中为 GBK/936）读写，中文会变乱码
    script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
              "Get-Clipboard -Raw")
    proc = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", timeout=CLIP_TIMEOUT,
    )
    if proc.returncode != 0:
        raise ClipboardUnavailable(f"Get-Clipboard 失败: {(proc.stderr or '')[:200]}")
    return (proc.stdout or "").replace("\r\n", "\n").rstrip("\n")


def write_text(text: str) -> None:
    """写入剪贴板纯文本（经 stdin，避免命令行转义问题）。"""
    if not available():
        raise ClipboardUnavailable("剪贴板写入当前仅支持 Windows")
# 同上：stdin 必须按 UTF-8 解，否则中文写入后是乱码
    script = ("[Console]::InputEncoding=[Text.Encoding]::UTF8;"
              "$in=[Console]::In.ReadToEnd(); Set-Clipboard -Value $in")
    proc = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
        input=text, capture_output=True, text=True, encoding="utf-8", timeout=CLIP_TIMEOUT,
    )
    if proc.returncode != 0:
        raise ClipboardUnavailable(f"Set-Clipboard 失败: {(proc.stderr or '')[:200]}")


def sniff_delimiter(text: str) -> str:
    """判断表格分隔符：从 Excel/RPA 复制通常是制表符，从网页复制可能是逗号。"""
    head = text.split("\n", 1)[0] if text else ""
    return "\t" if head.count("\t") >= head.count(",") else ","


def looks_like_table(text: str) -> bool:
    """粗略判断是否像表格数据：至少两行，且首行含分隔符与至少 3 列。"""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) < 2:
        return False
    d = sniff_delimiter(text)
    return d in lines[0] and len(lines[0].split(d)) >= 3

