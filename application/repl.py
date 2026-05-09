"""交互式 REPL 入口。"""

from __future__ import annotations

import json
import os

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from final_version_app.application.agent_loop import agent_loop
from final_version_app.application.compression import auto_compact
from final_version_app.application.container import build_services
from final_version_app.application.prompting import build_base_system, build_system
from final_version_app.application.session_memory import ensure_message_uuid
from final_version_app.application.team_runtime import TeammateManager
from final_version_app.application.tooling import build_tool_runtime
from final_version_app.config import MODEL
from final_version_app.infra.llm import get_llm, render_ai_text
from final_version_app.infra.shell import run_bash


def validate_runtime_config() -> None:
    """当模型缺少凭证时尽早失败，并给出清晰提示。"""
    try:
        get_llm(16)
    except ValueError as exc:
        model_name = MODEL.strip()
        lower_model = model_name.lower()
        if lower_model.startswith("qwen"):
            expected_env = "DASHSCOPE_API_KEY"
        elif lower_model.startswith("deepseek"):
            expected_env = "DEEPSEEK_API_KEY"
        else:
            expected_env = "OPENAI_API_KEY"
        raise RuntimeError(
            f"当前 MODEL_ID='{model_name}' 缺少模型凭证。"
            f"请在启动 REPL 之前先配置环境变量 `{expected_env}`。"
        ) from exc


def build_runtime():
    validate_runtime_config()
    services = build_services()
    team_mgr = TeammateManager(services)
    tool_runtime = build_tool_runtime(services, team_mgr)
    base_system = build_base_system(services.skills.descriptions())
    system_prompt = build_system(base_system, tool_runtime.tools)
    return services, team_mgr, tool_runtime, system_prompt


def _execute_approval_request(req: dict, services) -> str:
    kind = req["kind"]
    payload = req["payload"]
    if kind == "shell_command":
        return run_bash(payload["command"], approved=True)
    if kind == "background_shell_command":
        return services.bg.run_approved(payload["command"], int(payload.get("timeout", 120)))
    return f"错误：不支持的审批类型 '{kind}'"


def _prompt_for_pending_approval(history: list[BaseMessage], services, tool_runtime, system_prompt):
    req = services.approvals.next_pending()
    if not req:
        return
    print(f"[需要人工确认] 类型：{req['kind']}，原因：{req['reason']}")
    print(json.dumps(req["payload"], indent=2, ensure_ascii=False))
    while True:
        try:
            decision = input("是否批准执行？(yes/no): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            decision = "no"
        if decision in {"yes", "y"}:
            approved_req = services.approvals.approve(req["id"])
            result = _execute_approval_request(approved_req, services)
            services.approvals.mark_executed(req["id"], result)
            approval_message = HumanMessage(
                content=(
                    f"<approval-result id='{req['id']}' status='approved' kind='{req['kind']}'>\n"
                    f"命令执行结果：\n{result}\n"
                    f"</approval-result>"
                )
            )
            ensure_message_uuid(approval_message)
            history.append(approval_message)
            agent_loop(history, services, tool_runtime, system_prompt)
            if history and isinstance(history[-1], AIMessage):
                text = render_ai_text(history[-1]).strip()
                if text:
                    print(text)
            print()
            return
        if decision in {"no", "n"}:
            rejected_req = services.approvals.reject(req["id"])
            rejection_message = HumanMessage(
                content=(
                    f"<approval-result id='{req['id']}' status='rejected' kind='{rejected_req['kind']}'>\n"
                    f"已被人工拒绝。\n"
                    f"</approval-result>"
                )
            )
            ensure_message_uuid(rejection_message)
            history.append(rejection_message)
            agent_loop(history, services, tool_runtime, system_prompt)
            if history and isinstance(history[-1], AIMessage):
                text = render_ai_text(history[-1]).strip()
                if text:
                    print(text)
            print()
            return
        print("请输入 yes 或 no。")


def main():
    try:
        services, team_mgr, tool_runtime, system_prompt = build_runtime()
    except RuntimeError as exc:
        print(f"[启动错误] {exc}")
        return
    history: list[BaseMessage] = []

    while True:
        try:
            query = input("请输入你的问题：")
        except (EOFError, KeyboardInterrupt):
            break

        stripped = query.strip()
        if stripped.lower() in ("q", "exit", ""):
            break
        if stripped == "/compact":
            if history:
                print("[通过 /compact 手动压缩上下文]")
                history[:] = auto_compact(history, session_memory=services.session_memory, trigger="manual")
            continue
        if stripped == "/tasks":
            print(services.task_mgr.list_all())
            continue
        if stripped == "/team":
            print(team_mgr.list_all())
            continue
        if stripped == "/inbox":
            print(json.dumps(services.bus.read_inbox("lead"), indent=2, ensure_ascii=False))
            continue
        if stripped == "/approvals":
            print(services.approvals.list_pending())
            continue
        if stripped.startswith("/approve "):
            request_id = stripped.split(maxsplit=1)[1].strip()
            req = services.approvals.approve(request_id)
            if not req:
                print(f"未知的审批请求：{request_id}")
                continue
            kind = req["kind"]
            payload = req["payload"]
            if kind == "shell_command":
                result = run_bash(payload["command"], approved=True)
            elif kind == "background_shell_command":
                result = services.bg.run_approved(payload["command"], int(payload.get("timeout", 120)))
            else:
                result = f"错误：不支持的审批类型 '{kind}'"
            services.approvals.mark_executed(request_id, result)
            approval_message = HumanMessage(
                content=(
                    f"<approval-result id='{request_id}' status='approved' kind='{kind}'>\n"
                    f"命令执行结果：\n{result}\n"
                    f"</approval-result>"
                )
            )
            ensure_message_uuid(approval_message)
            history.append(approval_message)
            agent_loop(history, services, tool_runtime, system_prompt)
            if history and isinstance(history[-1], AIMessage):
                text = render_ai_text(history[-1]).strip()
                if text:
                    print(text)
            print()
            continue
        if stripped.startswith("/reject "):
            request_id = stripped.split(maxsplit=1)[1].strip()
            req = services.approvals.reject(request_id)
            if not req:
                print(f"未知的审批请求：{request_id}")
                continue
            rejection_message = HumanMessage(
                content=(
                    f"<approval-result id='{request_id}' status='rejected' kind='{req['kind']}'>\n"
                    f"已被人工拒绝。\n"
                    f"</approval-result>"
                )
            )
            ensure_message_uuid(rejection_message)
            history.append(rejection_message)
            agent_loop(history, services, tool_runtime, system_prompt)
            if history and isinstance(history[-1], AIMessage):
                text = render_ai_text(history[-1]).strip()
                if text:
                    print(text)
            print()
            continue

        message = HumanMessage(content=query)
        ensure_message_uuid(message)
        history.append(message)
        agent_loop(history, services, tool_runtime, system_prompt)
        _prompt_for_pending_approval(history, services, tool_runtime, system_prompt)
        if history and isinstance(history[-1], AIMessage):
            text = render_ai_text(history[-1]).strip()
            if text:
                print(text)
        print()
