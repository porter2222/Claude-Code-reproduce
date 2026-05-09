"""工作区文件访问工具。

核心目标：
1. 所有路径都限制在当前工作区内
2. 尽量兼容常见中文 Windows 编码文件
3. 日志中尽量返回相对路径，便于阅读
"""

from pathlib import Path

from final_version_app.config import WORKDIR


def safe_path(path_text: str) -> Path:
    """把输入路径约束到工作区内部，阻止越界访问。"""
    path = (WORKDIR / path_text).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {path_text}")
    return path


def read_text_auto(fp: Path) -> str:
    # Prefer UTF-8, fallback to common Windows encodings for legacy files.
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            return fp.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return fp.read_text(encoding="utf-8", errors="replace")


def relative_display(path: Path) -> str:
    """把绝对路径尽量转换成相对工作区路径，便于展示。"""
    try:
        return str(path.relative_to(WORKDIR)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")
