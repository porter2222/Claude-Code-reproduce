"""工作区操作集合。

这里放“贴近业务但仍偏通用”的文件读写、搜索、编辑能力，
它们会被工具层、subagent 和 teammate runtime 复用。
"""

import json
import re
from fnmatch import fnmatch
from pathlib import Path

from final_version_app.infra.workspace import read_text_auto, relative_display, safe_path


def run_read(path: str, limit: int = None) -> str:
    """读取文件全文，可选限制返回行数。"""
    try:
        lines = read_text_auto(safe_path(path)).splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as exc:
        return f"Error: {exc}"


def run_read_segment(
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    center_line: int | None = None,
    before: int = 20,
    after: int = 20,
) -> str:
    """按行号范围或中心行读取局部片段。"""
    try:
        lines = read_text_auto(safe_path(path)).splitlines()
        total = len(lines)
        if total == 0:
            return "(empty file)"
        if center_line is not None:
            if center_line <= 0:
                return "Error: center_line must be >= 1"
            start_idx = max(0, center_line - 1 - max(before, 0))
            end_idx = min(total, center_line + max(after, 0))
        else:
            if start_line is None:
                start_line = 1
            if end_line is None:
                end_line = min(total, start_line + max(after, 0))
            if start_line <= 0 or end_line <= 0:
                return "Error: start_line and end_line must be >= 1"
            if end_line < start_line:
                return "Error: end_line must be >= start_line"
            start_idx = max(0, start_line - 1)
            end_idx = min(total, end_line)
        segment = [f"{line_no + 1}: {lines[line_no]}" for line_no in range(start_idx, end_idx)]
        header = f"[segment {start_idx + 1}-{end_idx} of {total} lines]"
        return f"{header}\n" + "\n".join(segment)
    except Exception as exc:
        return f"Error: {exc}"


def _safe_dir(path: str | None) -> Path:
    """校验目录路径存在且确实是目录。"""
    fp = safe_path(path or ".")
    if not fp.exists():
        raise ValueError(f"Path not found: {path or '.'}")
    if not fp.is_dir():
        raise ValueError(f"Path is not a directory: {path or '.'}")
    return fp


def _iter_search_files(base_dir: Path, glob_pattern: str) -> list[Path]:
    """递归收集符合 glob 模式的文件。"""
    files = []
    for path in base_dir.rglob(glob_pattern or "*"):
        if path.is_file():
            files.append(path)
    return sorted(files)


def _resolve_search_files(path: str = ".", glob_pattern: str = "*") -> list[Path]:
    """兼容“目录搜索”和“单文件搜索”两种输入方式。"""
    target = safe_path(path or ".")
    if not target.exists():
        raise ValueError(f"Path not found: {path or '.'}")
    if target.is_file():
        pattern = glob_pattern or "*"
        return [target] if fnmatch(target.name, pattern) else []
    return _iter_search_files(target, glob_pattern)


def run_glob(pattern: str, path: str = ".", limit: int = 100) -> str:
    """按路径模式搜索文件或目录。"""
    try:
        if not pattern or not pattern.strip():
            return "Error: pattern is required"
        if limit <= 0:
            return "Error: limit must be positive"
        base_dir = _safe_dir(path)
        matches = []
        for item in sorted(base_dir.rglob(pattern.strip())):
            matches.append({"path": relative_display(item), "type": "dir" if item.is_dir() else "file"})
            if len(matches) >= min(limit, 500):
                break
        return json.dumps(matches, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"Error: {exc}"


def run_grep(
    pattern: str,
    path: str = ".",
    glob: str = "*",
    output_mode: str = "content",
    head_limit: int = 20,
    offset: int = 0,
    before: int = 0,
    after: int = 0,
    ignore_case: bool = False,
    multiline: bool = False,
) -> str:
    """按内容搜索文本，支持返回命中内容、命中文件或计数。"""
    try:
        if not pattern:
            return "Error: pattern is required"
        if output_mode not in {"content", "files_with_matches", "count"}:
            return "Error: output_mode must be one of: content, files_with_matches, count"
        if head_limit <= 0:
            return "Error: head_limit must be positive"
        if offset < 0 or before < 0 or after < 0:
            return "Error: offset/before/after must be non-negative"

        files = _resolve_search_files(path, glob)
        flags = re.MULTILINE
        if ignore_case:
            flags |= re.IGNORECASE
        if multiline:
            flags |= re.DOTALL
        regex = re.compile(pattern, flags)

        file_hits: list[str] = []
        content_hits: list[dict] = []
        total_count = 0

        for file_path in files:
            try:
                text = read_text_auto(file_path)
            except Exception:
                continue

            rel_path = relative_display(file_path)
            if multiline:
                matches = list(regex.finditer(text))
                if not matches:
                    continue
                file_hits.append(rel_path)
                total_count += len(matches)
                if output_mode == "content":
                    for match in matches:
                        start = max(0, match.start() - 120)
                        end = min(len(text), match.end() + 120)
                        snippet = text[start:end].replace("\r\n", "\n")
                        content_hits.append({"path": rel_path, "match": match.group(0)[:500], "snippet": snippet[:1000]})
                continue

            lines = text.splitlines()
            matched_in_file = False
            for idx, line in enumerate(lines):
                if not regex.search(line):
                    continue
                matched_in_file = True
                total_count += 1
                if output_mode == "content":
                    start = max(0, idx - before)
                    end = min(len(lines), idx + after + 1)
                    content_hits.append(
                        {
                            "path": rel_path,
                            "line": idx + 1,
                            "match": line[:500],
                            "context": lines[start:end],
                        }
                    )
            if matched_in_file:
                file_hits.append(rel_path)

        if output_mode == "count":
            return json.dumps({"pattern": pattern, "path": path, "glob": glob, "count": total_count, "files": len(set(file_hits))}, ensure_ascii=False, indent=2)
        if output_mode == "files_with_matches":
            unique_files = sorted(set(file_hits))
            window = unique_files[offset:offset + head_limit]
            return json.dumps({"total": len(unique_files), "offset": offset, "returned": len(window), "files": window}, ensure_ascii=False, indent=2)

        window = content_hits[offset:offset + head_limit]
        return json.dumps({"total": len(content_hits), "offset": offset, "returned": len(window), "matches": window}, ensure_ascii=False, indent=2)
    except re.error as exc:
        return f"Error: Invalid regex: {exc}"
    except Exception as exc:
        return f"Error: {exc}"


def run_write(path: str, content: str) -> str:
    """整文件写入，必要时自动创建父目录。"""
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as exc:
        return f"Error: {exc}"


def _find_stripped_line_window(lines: list[str], old_lines: list[str]):
    """忽略前后空白后，寻找旧文本块在文件中的窗口位置。"""
    if not old_lines:
        return None
    old_len = len(old_lines)
    if old_len > len(lines):
        return None
    for start in range(len(lines) - old_len + 1):
        if all(lines[start + idx].strip() == old_lines[idx].strip() for idx in range(old_len)):
            return (start, start + old_len)
    return None


def run_edit(path: str, old_text: str, new_text: str) -> str:
    """尽量稳健地做一次定点替换。

    匹配顺序：
    1. 精确匹配
    2. 归一化换行后匹配
    3. 忽略缩进差异的块匹配
    """
    try:
        fp = safe_path(path)
        content = read_text_auto(fp)
        if not old_text:
            return "Error: old_text is empty"
        if old_text in content:
            fp.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
            return f"Edited {path}"

        normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
        normalized_old = old_text.replace("\r\n", "\n").replace("\r", "\n")
        if normalized_old in normalized_content:
            normalized_new = new_text.replace("\r\n", "\n").replace("\r", "\n")
            fp.write_text(normalized_content.replace(normalized_old, normalized_new, 1), encoding="utf-8")
            return f"Edited {path} (matched after newline normalization)"

        window = _find_stripped_line_window(content.splitlines(), old_text.splitlines())
        if window:
            start, end = window
            replacement = new_text.splitlines()
            lines = content.splitlines()
            new_lines = lines[:start] + replacement + lines[end:]
            sep = "\r\n" if "\r\n" in content else "\n"
            trailing_newline = content.endswith("\n") or content.endswith("\r")
            new_content = sep.join(new_lines)
            if trailing_newline:
                new_content += sep
            fp.write_text(new_content, encoding="utf-8")
            return f"Edited {path} (matched ignoring indentation)"

        return f"Error: Text not found in {path}. Tip: call read_file again and use a shorter, exact old_text anchor."
    except Exception as exc:
        return f"Error: {exc}"
