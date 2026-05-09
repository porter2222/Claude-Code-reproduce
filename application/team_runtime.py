"""持久化队友运行时。"""

import json
import threading
import time

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from final_version_app.application.container import AppServices
from final_version_app.application.workspace_ops import (
    run_edit,
    run_glob,
    run_grep,
    run_read,
    run_read_segment,
    run_write,
)
from final_version_app.config import IDLE_TIMEOUT, POLL_INTERVAL, TASKS_DIR, TEAM_DIR, TEAMMATE_MAX_WORK_CYCLES, WORKDIR
from final_version_app.infra.llm import format_tool_guide, invoke_langchain, render_ai_text
from final_version_app.infra.shell import run_bash
from final_version_app.infra.workspace import read_text_auto


class TeammateManager:
    """管理长生命周期的队友代理。"""

    def __init__(self, services: AppServices):
        TEAM_DIR.mkdir(exist_ok=True)
        self.services = services
        self.bus = services.bus
        self.task_mgr = services.task_mgr
        self.config_path = TEAM_DIR / "config.json"
        self.config = self._load()
        self.threads = {}
        self._normalize_stale_members()

    def _load(self) -> dict:
        if self.config_path.exists():
            return json.loads(read_text_auto(self.config_path))
        return {"team_name": "default", "members": []}

    def _save(self):
        self.config_path.write_text(json.dumps(self.config, indent=2, ensure_ascii=False), encoding="utf-8")

    def _find(self, name: str) -> dict | None:
        for member in self.config["members"]:
            if member["name"] == name:
                return member
        return None

    def _normalize_stale_members(self):
        """重置那些在新进程启动后无法延续的陈旧 working 状态。"""
        changed = False
        for member in self.config.get("members", []):
            if member.get("status") == "working":
                member["status"] = "shutdown"
                changed = True
        if changed:
            self._save()

    def spawn(self, name: str, role: str, prompt: str) -> str:
        member = self._find(name)
        if member:
            if member["status"] not in ("idle", "shutdown"):
                return f"错误：'{name}' 当前状态为 {member['status']}"
            member["status"] = "working"
            member["role"] = role
        else:
            member = {"name": name, "role": role, "status": "working"}
            self.config["members"].append(member)
        self._save()
        self.bus.send(
            name,
            "lead",
            f"队友 '{name}'（角色：{role}）已开始工作，完成后会回报结果。",
            "message",
        )
        thread = threading.Thread(target=self._loop, args=(name, role, prompt), daemon=True)
        self.threads[name] = thread
        thread.start()
        return f"已启动队友 '{name}'（角色：{role}）"

    def _set_status(self, name: str, status: str):
        member = self._find(name)
        if member:
            member["status"] = status
            self._save()

    def _auto_report(self, name: str, role: str, status: str, summary: str) -> None:
        clean_summary = (summary or "").strip() or "未生成详细总结。"
        content = (
            f"队友 '{name}'（角色：{role}）已结束，本次状态为 `{status}`。\n"
            f"总结：\n{clean_summary}"
        )
        self.bus.send(name, "lead", content, "message")

    def _teammate_exec(self, name: str, tool_name: str, args: dict) -> str:
        if tool_name == "bash":
            return run_bash(args["command"])
        if tool_name == "glob_files":
            return run_glob(args["pattern"], args.get("path", "."), args.get("limit", 100))
        if tool_name == "grep_content":
            return run_grep(
                args["pattern"],
                args.get("path", "."),
                args.get("glob", "*"),
                args.get("output_mode", "content"),
                args.get("head_limit", 20),
                args.get("offset", 0),
                args.get("before", 0),
                args.get("after", 0),
                args.get("ignore_case", False),
                args.get("multiline", False),
            )
        if tool_name == "read_file":
            return run_read(args["path"], args.get("limit"))
        if tool_name == "read_file_segment":
            return run_read_segment(
                args["path"],
                args.get("start_line"),
                args.get("end_line"),
                args.get("center_line"),
                args.get("before", 20),
                args.get("after", 20),
            )
        if tool_name == "write_file":
            return run_write(args["path"], args["content"])
        if tool_name == "edit_file":
            return run_edit(args["path"], args["old_text"], args["new_text"])
        if tool_name == "send_message":
            return self.bus.send(name, args["to"], args["content"], args.get("msg_type", "message"))
        if tool_name == "idle":
            return "进入空闲阶段。"
        if tool_name == "claim_task":
            return self.task_mgr.claim(args["task_id"], name)
        return f"未知工具：{tool_name}"

    def _teammate_tools(self, base_tools: dict[str, object], name: str) -> list:
        @tool("send_message")
        def teammate_send_message_tool(to: str, content: str, msg_type: str = "message") -> str:
            """向其他队友或主代理发送消息。"""
            return self._teammate_exec(name, "send_message", {"to": to, "content": content, "msg_type": msg_type})

        @tool("idle")
        def teammate_idle_tool() -> str:
            """表示当前已没有可执行工作。"""
            return self._teammate_exec(name, "idle", {})

        @tool("claim_task")
        def teammate_claim_task_tool(task_id: int) -> str:
            """从共享任务板中按 ID 认领任务。"""
            return self._teammate_exec(name, "claim_task", {"task_id": task_id})

        return [
            base_tools["bash"],
            base_tools["glob_files"],
            base_tools["grep_content"],
            base_tools["read_file"],
            base_tools["read_file_segment"],
            base_tools["write_file"],
            base_tools["edit_file"],
            teammate_send_message_tool,
            teammate_idle_tool,
            teammate_claim_task_tool,
        ]

    def _loop(self, name: str, role: str, prompt: str):
        from final_version_app.application.tooling import build_base_toolset

        base_tools, _ = build_base_toolset(self.services)
        team_name = self.config["team_name"]
        sys_prompt = (
            f"你是 '{name}'，角色是 {role}，所属团队是 {team_name}，当前工作目录是 {WORKDIR}。"
            "当你完成当前工作后，请使用 idle。你也可以自动认领任务。"
        )
        messages: list[BaseMessage] = [HumanMessage(content=prompt)]
        tools = self._teammate_tools(base_tools, name)
        sys_prompt = (
            f"{sys_prompt}\n"
            "工具使用说明：\n"
            f"{format_tool_guide(tools)}\n"
            "规则：\n"
            "- 打开文件前，先用 glob_files 找候选文件。\n"
            "- 在 read_file 之前，优先用 grep_content 定位相关代码或文本。\n"
            "- 当 grep_content 返回命中行后，优先用 read_file_segment，不要反复整文件读取。\n"
            "- 在 edit_file 或 write_file 之前，优先先读文件。\n"
            "- 跨代理协作时使用 send_message，并给出明确下一步行动。\n"
            "- 在进入 idle 之前，一定要向 lead 发送简洁的最终状态更新。\n"
            "- 只有在没有可执行工作时，才调用 idle。"
        )
        while True:
            last_summary = ""
            sent_report = False
            for _ in range(TEAMMATE_MAX_WORK_CYCLES):
                inbox = self.bus.read_inbox(name)
                for msg in inbox:
                    if msg.get("type") == "shutdown_request":
                        self._auto_report(name, role, "shutdown", last_summary or "主代理要求关闭。")
                        self._set_status(name, "shutdown")
                        return
                    messages.append(HumanMessage(content=json.dumps(msg, ensure_ascii=False)))
                try:
                    response = invoke_langchain(messages=messages, tools=tools, system=sys_prompt, max_tokens=8000)
                except Exception as exc:
                    self._auto_report(name, role, "error", f"队友执行时发生异常：{exc}")
                    self._set_status(name, "shutdown")
                    return
                messages.append(response)
                last_summary = render_ai_text(response).strip() or last_summary
                if not (response.tool_calls or []):
                    break
                idle_requested = False
                for call in response.tool_calls or []:
                    tool_name = str(call.get("name", ""))
                    tool_input = call.get("args", {})
                    if not isinstance(tool_input, dict):
                        tool_input = {}
                    tool_call_id = str(call.get("id", ""))
                    if tool_name == "idle":
                        idle_requested = True
                    if tool_name == "send_message":
                        sent_report = True
                    output = self._teammate_exec(name, tool_name, tool_input)
                    print(f"  [{name}] {tool_name}: {str(output)[:120]}")
                    messages.append(ToolMessage(content=str(output), tool_call_id=tool_call_id))
                if idle_requested:
                    break
            else:
                last_summary = last_summary or "已达到队友工具调用次数上限。"

            if not sent_report:
                self._auto_report(name, role, "completed", last_summary)

            self._set_status(name, "idle")
            resume = False
            for _ in range(IDLE_TIMEOUT // max(POLL_INTERVAL, 1)):
                time.sleep(POLL_INTERVAL)
                inbox = self.bus.read_inbox(name)
                if inbox:
                    for msg in inbox:
                        if msg.get("type") == "shutdown_request":
                            self._auto_report(name, role, "shutdown", last_summary or "主代理要求关闭。")
                            self._set_status(name, "shutdown")
                            return
                        messages.append(HumanMessage(content=json.dumps(msg, ensure_ascii=False)))
                    resume = True
                    break
                unclaimed = []
                for path in sorted(TASKS_DIR.glob("task_*.json")):
                    task = json.loads(read_text_auto(path))
                    if task.get("status") == "pending" and not task.get("owner") and not task.get("blockedBy"):
                        unclaimed.append(task)
                if unclaimed:
                    task = unclaimed[0]
                    self.task_mgr.claim(task["id"], name)
                    if len(messages) <= 3:
                        messages.insert(0, HumanMessage(content=f"<identity>你是 '{name}'，角色是 {role}，团队是 {team_name}。</identity>"))
                        messages.insert(1, AIMessage(content=f"我是 {name}，继续工作。"))
                    messages.append(HumanMessage(content=f"<auto-claimed>任务 #{task['id']}：{task['subject']}\n{task.get('description', '')}</auto-claimed>"))
                    messages.append(AIMessage(content=f"已认领任务 #{task['id']}，开始处理。"))
                    resume = True
                    break
            if not resume:
                self._set_status(name, "shutdown")
                return
            self._set_status(name, "working")

    def list_all(self) -> str:
        if not self.config["members"]:
            return "当前没有队友。"
        lines = [f"团队：{self.config['team_name']}"]
        for member in self.config["members"]:
            lines.append(f"  {member['name']} ({member['role']}): {member['status']}")
        return "\n".join(lines)

    def member_names(self) -> list:
        return [member["name"] for member in self.config["members"]]
