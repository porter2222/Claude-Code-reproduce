"""Shell 执行工具，带有命令风险识别与人工审批支持。"""

from __future__ import annotations

import os
import re
import subprocess

from final_version_app.config import WORKDIR


def run_shell(command: str, timeout: int = 120) -> tuple[str, str]:
    """在工作目录中执行 shell 命令，并返回标准输出与标准错误。"""
    if os.name == "nt":
        ps_command = (
            "[Console]::InputEncoding=[System.Text.UTF8Encoding]::UTF8; "
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::UTF8; "
            "$OutputEncoding=[System.Text.UTF8Encoding]::UTF8; "
            f"{command}"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_command],
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    else:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    return result.stdout, result.stderr


def inspect_command_policy(command: str) -> tuple[str, str | None]:
    """返回命令策略判断结果：('allow'|'approval'|'block', 原因)。"""
    lowered = command.strip().lower()

    blocked_substrings = [
        "rm -rf /",
        "sudo",
        "shutdown",
        "reboot",
        "> /dev/",
        "powershell -encodedcommand",
        "powershell.exe -encodedcommand",
        "invoke-expression",
        "iex ",
    ]
    if any(token in lowered for token in blocked_substrings):
        return "block", "检测到高危 shell 模式"

    approval_patterns = [
        r"(^|[\s;(])remove-item\b",
        r"(^|[\s;(])del\b",
        r"(^|[\s;(])erase\b",
        r"(^|[\s;(])rd\b",
        r"(^|[\s;(])rmdir\b",
        r"(^|[\s;(])rm\b",
        r"(^|[\s;(])ri\b",
        r"(^|[\s;(])cmd(\.exe)?\s+/c\s+(del|erase|rd|rmdir)\b",
    ]
    if any(re.search(pattern, lowered) for pattern in approval_patterns):
        return "approval", "检测到删除类高风险命令"

    return "allow", None


def run_bash(command: str, approved: bool = False) -> str:
    """执行面向用户的 shell 命令，并进行保守的安全检查。"""
    action, reason = inspect_command_policy(command)
    if action == "block":
        return f"错误：危险命令已被拦截（{reason}）。"
    if action == "approval" and not approved:
        return f"需要人工确认：{reason}。"
    try:
        stdout, stderr = run_shell(command, timeout=120)
        output = (stdout + stderr).strip()
        return output[:50000] if output else "（无输出）"
    except subprocess.TimeoutExpired:
        return "错误：命令执行超时（120 秒）"
