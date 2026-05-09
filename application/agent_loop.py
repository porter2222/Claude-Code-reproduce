"""Main agent loop."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from final_version_app.application.compression import apply_tool_result_budget, auto_compact, context_collapse, microcompact
from final_version_app.application.session_memory import annotate_messages, ensure_message_uuid
from final_version_app.application.tooling import ToolRuntime, make_tool_cache_key, store_tool_cache
from final_version_app.config import AGENT_LOOP_MAX_CYCLES, CONTEXT_COLLAPSE_TRIGGER_RATIO, TOKEN_THRESHOLD
from final_version_app.infra.llm import estimate_tokens, invoke_langchain


def _append_message(messages: list, message):
    ensure_message_uuid(message)
    messages.append(message)


def agent_loop(messages: list, services, tool_runtime: ToolRuntime, system_prompt: str):
    annotate_messages(messages)
    rounds_without_todo = 0
    tool_cache: dict[str, str] = {}
    tool_cache_order: list[str] = []
    cycle_count = 0

    while True:
        cycle_count += 1
        if cycle_count > AGENT_LOOP_MAX_CYCLES:
            _append_message(
                messages,
                AIMessage(
                    content=(
                        "Stopping this turn after reaching the internal tool-call limit. "
                        "Please review the partial results above or ask me to continue from here."
                    )
                ),
            )
            services.session_memory.maybe_schedule_extraction(messages)
            return

        annotate_messages(messages)
        services.session_memory.maybe_schedule_extraction(messages)
        apply_tool_result_budget(messages)
        microcompact(messages)

        token_estimate = estimate_tokens(messages)
        if token_estimate > services.session_memory.auto_compact_threshold():
            print("[session-memory compact triggered]")
            messages[:] = auto_compact(messages, session_memory=services.session_memory, trigger="auto")
            annotate_messages(messages)
            token_estimate = estimate_tokens(messages)

        if token_estimate > int(TOKEN_THRESHOLD * CONTEXT_COLLAPSE_TRIGGER_RATIO):
            print("[context-collapse triggered]")
            messages[:] = context_collapse(messages)
            annotate_messages(messages)
            token_estimate = estimate_tokens(messages)

        if token_estimate > TOKEN_THRESHOLD:
            print("[legacy compact triggered]")
            messages[:] = auto_compact(messages, session_memory=services.session_memory, trigger="overflow")
            annotate_messages(messages)

        notifs = services.bg.drain()
        if notifs:
            text = "\n".join(f"[bg:{item['task_id']}] {item['status']}: {item['result']}" for item in notifs)
            _append_message(messages, HumanMessage(content=f"<background-results>\n{text}\n</background-results>"))
            _append_message(messages, AIMessage(content="Noted background results."))

        inbox = services.bus.read_inbox("lead")
        if inbox:
            _append_message(messages, HumanMessage(content=f"<inbox>{json.dumps(inbox, indent=2, ensure_ascii=False)}</inbox>"))
            _append_message(messages, AIMessage(content="Noted inbox messages."))

        response = invoke_langchain(messages=messages, tools=tool_runtime.tools, system=system_prompt, max_tokens=8000)
        ensure_message_uuid(response)
        messages.append(response)
        if not (response.tool_calls or []):
            services.session_memory.maybe_schedule_extraction(messages)
            return

        used_todo = False
        manual_compress = False
        for call in response.tool_calls or []:
            tool_name = str(call.get("name", ""))
            tool_input = call.get("args", {})
            if not isinstance(tool_input, dict):
                tool_input = {}
            tool_call_id = str(call.get("id", ""))
            if tool_name == "compress":
                manual_compress = True
            cache_key = make_tool_cache_key(tool_name, tool_input)
            if tool_name in tool_runtime.cache_invalidating_tool_names:
                tool_cache.clear()
                tool_cache_order.clear()
            if tool_name in tool_runtime.cacheable_tool_names and cache_key in tool_cache:
                output = tool_cache[cache_key]
                print(f"> {tool_name}: [cache hit] {str(output)[:180]}")
            else:
                handler = tool_runtime.handlers.get(tool_name)
                try:
                    output = handler(**tool_input) if handler else f"Unknown tool: {tool_name}"
                except Exception as exc:
                    output = f"Error: {exc}"
                if tool_name in tool_runtime.cacheable_tool_names and not str(output).startswith("Error:"):
                    store_tool_cache(tool_cache, tool_cache_order, cache_key, str(output))
                print(f"> {tool_name}: {str(output)[:200]}")
            tool_message = ToolMessage(content=str(output), tool_call_id=tool_call_id)
            ensure_message_uuid(tool_message)
            messages.append(tool_message)
            if tool_name == "TodoWrite":
                used_todo = True

        rounds_without_todo = 0 if used_todo else rounds_without_todo + 1
        if services.todo.has_open_items() and rounds_without_todo >= 3:
            _append_message(messages, HumanMessage(content="<reminder>Update your todos.</reminder>"))

        services.session_memory.maybe_schedule_extraction(messages)
        if manual_compress:
            print("[manual compact]")
            messages[:] = auto_compact(messages, session_memory=services.session_memory, trigger="manual")
            annotate_messages(messages)
