"""Conversation compaction helpers."""

from __future__ import annotations

import json
import time

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from final_version_app.application.session_memory import annotate_messages
from final_version_app.config import (
    CONTEXT_COLLAPSE_KEEP_RECENT,
    CONTEXT_COLLAPSE_MAX_TOOL_TRACES,
    MICROCOMPACT_KEEP_RECENT,
    MICROCOMPACT_SUMMARY_HEAD,
    MICROCOMPACT_SUMMARY_TAIL,
    TOOL_RESULT_CHAR_BUDGET,
    TOOL_RESULT_HEAD_CHARS,
    TOOL_RESULT_TAIL_CHARS,
    TRANSCRIPT_DIR,
)
from final_version_app.infra.llm import invoke_langchain, render_ai_text


def apply_tool_result_budget(messages: list):
    # 第一层防线：防止单条 grep/read/log 结果过大，直接吞掉大量上下文。
    # 这里只保留头尾，因为错误摘要和关键信息通常出现在这两个位置。
    for idx, msg in enumerate(messages):
        if not isinstance(msg, ToolMessage):
            continue
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if len(content) <= TOOL_RESULT_CHAR_BUDGET:
            continue
        head = content[:TOOL_RESULT_HEAD_CHARS].rstrip()
        tail = content[-TOOL_RESULT_TAIL_CHARS:].lstrip()
        clipped = f"{head}\n\n[... clipped {len(content) - len(head) - len(tail)} chars ...]\n{tail}"
        messages[idx] = ToolMessage(content=clipped, tool_call_id=msg.tool_call_id)


def summarize_tool_result(tool_name: str, content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return f"[summary: used {tool_name}; empty output]"
    if len(normalized) <= MICROCOMPACT_SUMMARY_HEAD:
        return f"[summary: used {tool_name}]\n{normalized}"
    head = normalized[:MICROCOMPACT_SUMMARY_HEAD].rstrip()
    tail = normalized[-MICROCOMPACT_SUMMARY_TAIL:].lstrip() if len(normalized) > MICROCOMPACT_SUMMARY_HEAD else ""
    omitted = max(len(normalized) - len(head) - len(tail), 0)
    line_count = normalized.count("\n") + 1
    summary = f"[summary: used {tool_name}; {line_count} lines; omitted {omitted} chars]\n{head}"
    if tail:
        summary += f"\n...\n{tail}"
    return summary


def _tool_name_map(messages: list) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for msg in messages:
        if isinstance(msg, AIMessage):
            for call in msg.tool_calls or []:
                mapping[str(call.get("id", ""))] = str(call.get("name", "unknown"))
    return mapping


def microcompact(messages: list):
    # 更早的工具输出通常只是“过程证据”，不再是当前工作的核心上下文。
    # 所以把旧工具输出改成摘要，但保留最近几次工具结果的原文，
    # 这样模型还能继续当前的局部调试/编辑链路。
    tool_result_indices = [idx for idx, msg in enumerate(messages) if isinstance(msg, ToolMessage)]
    if len(tool_result_indices) <= MICROCOMPACT_KEEP_RECENT:
        return
    tool_name_map = _tool_name_map(messages)
    for idx in tool_result_indices[:-MICROCOMPACT_KEEP_RECENT]:
        msg = messages[idx]
        if not isinstance(msg, ToolMessage):
            continue
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        tool_name = tool_name_map.get(msg.tool_call_id, "unknown")
        summary = summarize_tool_result(tool_name, content)
        if summary != content:
            messages[idx] = ToolMessage(content=summary, tool_call_id=msg.tool_call_id)


def context_collapse(messages: list) -> list:
    # 旧版中强度压缩：
    # - 最近消息保留原文
    # - 更早的历史改写成一个结构化文本块
    # 现在主循环会优先尝试 session memory 压缩，
    # 但这里仍然保留，作为额外的一层缓压手段。
    if len(messages) <= CONTEXT_COLLAPSE_KEEP_RECENT + 4:
        return messages
    older = messages[:-CONTEXT_COLLAPSE_KEEP_RECENT]
    recent = messages[-CONTEXT_COLLAPSE_KEEP_RECENT:]
    tool_name_map = _tool_name_map(messages)

    human_notes: list[str] = []
    ai_notes: list[str] = []
    tool_traces: list[str] = []
    tool_counts: dict[str, int] = {}

    for msg in older:
        if isinstance(msg, HumanMessage):
            text = str(msg.content).replace("\r\n", "\n").strip()
            if text and len(human_notes) < 6:
                human_notes.append(text[:160].replace("\n", " "))
        elif isinstance(msg, AIMessage):
            text = render_ai_text(msg).replace("\r\n", "\n").strip()
            if text and len(ai_notes) < 4:
                ai_notes.append(text[:160].replace("\n", " "))
        elif isinstance(msg, ToolMessage):
            tool_name = tool_name_map.get(msg.tool_call_id, "unknown")
            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
            if len(tool_traces) < CONTEXT_COLLAPSE_MAX_TOOL_TRACES:
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                tool_traces.append(summarize_tool_result(tool_name, content))

    lines = ["<context-collapse>"]
    lines.append(f"Collapsed {len(older)} earlier messages; kept the most recent {len(recent)} messages verbatim.")
    if human_notes:
        lines.append("Earlier user intents:")
        lines.extend(f"- {note}" for note in human_notes)
    if ai_notes:
        lines.append("Earlier assistant conclusions:")
        lines.extend(f"- {note}" for note in ai_notes)
    if tool_counts:
        counts = ", ".join(f"{name} x{count}" for name, count in sorted(tool_counts.items()))
        lines.append(f"Tool usage counts: {counts}")
    if tool_traces:
        lines.append("Representative tool traces:")
        lines.extend(f"- {trace}" for trace in tool_traces)
    lines.append("</context-collapse>")

    collapsed = [
        HumanMessage(content="\n".join(lines)),
        AIMessage(content="Understood. Continuing with collapsed context and recent verbatim messages."),
        *recent,
    ]
    annotate_messages(collapsed)
    return collapsed


def legacy_auto_compact(messages: list) -> list:
    # 最终 fallback：
    # 当结构化 session memory 不可用时，
    # 先把完整历史落盘到 .transcripts，
    # 再让模型临时总结整段历史，生成 continuity summary。
    annotate_messages(messages)
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.jsonl"
    with open(path, "w", encoding="utf-8") as file_obj:
        for msg in messages:
            file_obj.write(json.dumps({"type": msg.__class__.__name__, "content": str(msg)}, ensure_ascii=False) + "\n")
    conv_text = json.dumps([str(msg) for msg in messages], ensure_ascii=False)[:80000]
    summary = _run_legacy_summary_llm(conv_text).strip() or "(empty summary)"
    compacted = [
        HumanMessage(content=f"[Compressed. Transcript: {path}]\n{summary}"),
        AIMessage(content="Understood. Continuing with summary context."),
    ]
    annotate_messages(compacted)
    return compacted


def auto_compact(messages: list, session_memory=None, trigger: str = "manual") -> list:
    # 优先级顺序：
    # 1. 优先尝试 session-memory-first compaction
    #    因为它复用了平时持续维护的结构化记忆，通常更稳定、成本更低
    # 2. 如果任何一步失败，就退回 legacy compaction
    #    这样不会因为压缩失败直接把主循环卡死
    annotate_messages(messages)
    token_estimate = 0
    if session_memory is not None:
        try:
            from final_version_app.infra.llm import estimate_tokens

            token_estimate = estimate_tokens(messages)
            compacted = session_memory.compact_messages(messages, token_estimate=token_estimate, trigger=trigger)
            if compacted:
                return compacted
        except Exception:
            pass
    return legacy_auto_compact(messages)


def _run_legacy_summary_llm(conv_text: str) -> str:
    resp = invoke_langchain(
        messages=[HumanMessage(content=f"Summarize for continuity:\n{conv_text}")],
        tools=[],
        system=None,
        max_tokens=2000,
    )
    return render_ai_text(resp)
