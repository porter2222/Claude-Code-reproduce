"""工具装配与调度注册表。
这一版刻意保持“足够结构化，但不过度抽象”：
- 不再引入 ToolRegistry / HandlerRegistry
- 直接使用 `dict + list` 表示工具和 handler
- 通过几个显式装配函数，把基础工具、任务工具、协作工具、协议工具分组注册
"""

import json
import uuid
from dataclasses import dataclass
from typing import Callable

from langchain_core.tools import tool

from final_version_app.application.container import AppServices
from final_version_app.application.subagent import run_subagent
from final_version_app.application.workspace_ops import (
    run_edit,
    run_glob,
    run_grep,
    run_read,
    run_read_segment,
    run_write,
)
from final_version_app.config import TOOL_CACHE_LIMIT
from final_version_app.infra.shell import inspect_command_policy, run_bash

ToolHandler = Callable[..., str]


def _register_tool_pair(
    tools_by_name: dict[str, object],
    handlers: dict[str, ToolHandler],
    name: str,
    tool_obj: object,
    handler: ToolHandler,
):
    """一次性注册同名工具对象和执行函数。"""
    tools_by_name[name] = tool_obj
    handlers[name] = handler


def build_base_toolset(services: AppServices) -> tuple[dict[str, object], dict[str, ToolHandler]]:
    """构建所有运行时都会复用的基础工具。"""

    tools_by_name: dict[str, object] = {}
    handlers: dict[str, ToolHandler] = {}

    def handle_shell_command(command: str) -> str:
        action, reason = inspect_command_policy(command)
        if action == "block":
            return f"错误：危险命令已被拦截（{reason}）。"
        if action == "approval":
            return services.approvals.create("shell_command", {"command": command}, reason or "需要人工确认")
        return run_bash(command)

    @tool("bash")
    def bash_tool(command: str) -> str:
        """执行一条 shell 命令。"""
        return handle_shell_command(command)

    @tool("read_file")
    def read_file_tool(path: str, limit: int = None) -> str:
        """读取文件内容。"""
        return run_read(path, limit)

    @tool("read_file_segment")
    def read_file_segment_tool(
        path: str,
        start_line: int = None,
        end_line: int = None,
        center_line: int = None,
        before: int = 20,
        after: int = 20,
    ) -> str:
        """按明确行号范围或某个中心行附近读取局部文件片段。"""
        return run_read_segment(path, start_line, end_line, center_line, before, after)

    @tool("write_file")
    def write_file_tool(path: str, content: str) -> str:
        """把内容写入文件。"""
        return run_write(path, content)

    @tool("edit_file")
    def edit_file_tool(path: str, old_text: str, new_text: str) -> str:
        """在文件中精确替换指定文本。"""
        return run_edit(path, old_text, new_text)

    @tool("glob_files")
    def glob_files_tool(pattern: str, path: str = ".", limit: int = 100) -> str:
        """在工作区相对目录下，按 glob 模式查找文件或目录。"""
        return run_glob(pattern, path, limit)

    @tool("grep_content")
    def grep_content_tool(
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
        """用正则搜索文件内容，并返回命中内容、命中文件或统计信息。"""
        return run_grep(pattern, path, glob, output_mode, head_limit, offset, before, after, ignore_case, multiline)

    _register_tool_pair(tools_by_name, handlers, "bash", bash_tool, lambda **kw: handle_shell_command(kw["command"]))
    _register_tool_pair(tools_by_name, handlers, "read_file", read_file_tool, lambda **kw: run_read(kw["path"], kw.get("limit")))
    _register_tool_pair(
        tools_by_name,
        handlers,
        "read_file_segment",
        read_file_segment_tool,
        lambda **kw: run_read_segment(
            kw["path"],
            kw.get("start_line"),
            kw.get("end_line"),
            kw.get("center_line"),
            kw.get("before", 20),
            kw.get("after", 20),
        ),
    )
    _register_tool_pair(tools_by_name, handlers, "write_file", write_file_tool, lambda **kw: run_write(kw["path"], kw["content"]))
    _register_tool_pair(tools_by_name, handlers, "edit_file", edit_file_tool, lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]))
    _register_tool_pair(tools_by_name, handlers, "glob_files", glob_files_tool, lambda **kw: run_glob(kw["pattern"], kw.get("path", "."), kw.get("limit", 100)))
    _register_tool_pair(
        tools_by_name,
        handlers,
        "grep_content",
        grep_content_tool,
        lambda **kw: run_grep(
            kw["pattern"],
            kw.get("path", "."),
            kw.get("glob", "*"),
            kw.get("output_mode", "content"),
            kw.get("head_limit", 20),
            kw.get("offset", 0),
            kw.get("before", 0),
            kw.get("after", 0),
            kw.get("ignore_case", False),
            kw.get("multiline", False),
        ),
    )
    return tools_by_name, handlers


@dataclass
class ToolRuntime:
    """工具运行时元数据。"""

    tools: list
    handlers: dict[str, ToolHandler]
    base_tools: dict[str, object]
    cacheable_tool_names: set[str]
    cache_invalidating_tool_names: set[str]
    shutdown_requests: dict
    plan_requests: dict


def _register_task_tools(
    tools_by_name: dict[str, object],
    handlers: dict[str, ToolHandler],
    services: AppServices,
    run_task_subagent: Callable[..., str],
):
    """注册任务管理相关工具。"""

    @tool("TodoWrite")
    def todo_write_tool(items: list[dict]) -> str:
        """更新待办跟踪清单。"""
        return services.todo.update(items)

    @tool("task")
    def task_tool(prompt: str, agent_type: str = "Explore") -> str:
        """启动一个子代理，用于独立探索或执行任务。"""
        return run_task_subagent(prompt, agent_type)

    @tool("load_skill")
    def load_skill_tool(name: str) -> str:
        """按名称加载专门技能知识。"""
        return services.skills.load(name)

    @tool("task_create")
    def task_create_tool(subject: str, description: str = "") -> str:
        """创建一个持久化任务。"""
        return services.task_mgr.create(subject, description)

    @tool("task_get")
    def task_get_tool(task_id: int) -> str:
        """按任务 ID 获取详情。"""
        return services.task_mgr.get(task_id)

    @tool("task_update")
    def task_update_tool(task_id: int, status: str = None, add_blocked_by: list = None, add_blocks: list = None) -> str:
        """更新任务状态或依赖关系。"""
        return services.task_mgr.update(task_id, status, add_blocked_by, add_blocks)

    @tool("task_list")
    def task_list_tool() -> str:
        """列出所有任务。"""
        return services.task_mgr.list_all()

    @tool("claim_task")
    def claim_task_tool(task_id: int) -> str:
        """从任务板认领一个任务。"""
        return services.task_mgr.claim(task_id, "lead")

    _register_tool_pair(tools_by_name, handlers, "TodoWrite", todo_write_tool, lambda **kw: services.todo.update(kw["items"]))
    _register_tool_pair(tools_by_name, handlers, "task", task_tool, lambda **kw: run_task_subagent(kw["prompt"], kw.get("agent_type", "Explore")))
    _register_tool_pair(tools_by_name, handlers, "load_skill", load_skill_tool, lambda **kw: services.skills.load(kw["name"]))
    _register_tool_pair(tools_by_name, handlers, "task_create", task_create_tool, lambda **kw: services.task_mgr.create(kw["subject"], kw.get("description", "")))
    _register_tool_pair(tools_by_name, handlers, "task_get", task_get_tool, lambda **kw: services.task_mgr.get(kw["task_id"]))
    _register_tool_pair(
        tools_by_name,
        handlers,
        "task_update",
        task_update_tool,
        lambda **kw: services.task_mgr.update(kw["task_id"], kw.get("status"), kw.get("add_blocked_by"), kw.get("add_blocks")),
    )
    _register_tool_pair(tools_by_name, handlers, "task_list", task_list_tool, lambda **kw: services.task_mgr.list_all())
    _register_tool_pair(tools_by_name, handlers, "claim_task", claim_task_tool, lambda **kw: services.task_mgr.claim(kw["task_id"], "lead"))


def _register_collaboration_tools(
    tools_by_name: dict[str, object],
    handlers: dict[str, ToolHandler],
    services: AppServices,
    team_mgr,
):
    """注册协作与后台执行相关工具。"""

    def handle_background_run(command: str, timeout: int = 120) -> str:
        action, reason = inspect_command_policy(command)
        if action == "block":
            return f"错误：危险命令已被拦截（{reason}）。"
        if action == "approval":
            return services.approvals.create(
                "background_shell_command",
                {"command": command, "timeout": timeout},
                reason or "需要人工确认",
            )
        return services.bg.run(command, timeout)

    @tool("background_run")
    def background_run_tool(command: str, timeout: int = 120) -> str:
        """在后台线程中执行命令。"""
        return handle_background_run(command, timeout)

    @tool("check_background")
    def check_background_tool(task_id: str = None) -> str:
        """检查后台任务状态。"""
        return services.bg.check(task_id)

    @tool("spawn_teammate")
    def spawn_teammate_tool(name: str, role: str, prompt: str) -> str:
        """启动一个持久化的自主队友代理。"""
        return team_mgr.spawn(name, role, prompt)

    @tool("list_teammates")
    def list_teammates_tool() -> str:
        """列出所有队友。"""
        return team_mgr.list_all()

    @tool("send_message")
    def send_message_tool(to: str, content: str, msg_type: str = "message") -> str:
        """向某个队友发送消息。"""
        return services.bus.send("lead", to, content, msg_type)

    @tool("read_inbox")
    def read_inbox_tool() -> str:
        """读取并清空主代理的收件箱。"""
        return json.dumps(services.bus.read_inbox("lead"), indent=2, ensure_ascii=False)

    @tool("broadcast")
    def broadcast_tool(content: str) -> str:
        """向所有队友广播消息。"""
        return services.bus.broadcast("lead", content, team_mgr.member_names())

    @tool("idle")
    def idle_tool() -> str:
        """进入空闲状态。"""
        return "主代理不会进入空闲状态。"

    _register_tool_pair(
        tools_by_name,
        handlers,
        "background_run",
        background_run_tool,
        lambda **kw: handle_background_run(kw["command"], kw.get("timeout", 120)),
    )
    _register_tool_pair(tools_by_name, handlers, "check_background", check_background_tool, lambda **kw: services.bg.check(kw.get("task_id")))
    _register_tool_pair(tools_by_name, handlers, "spawn_teammate", spawn_teammate_tool, lambda **kw: team_mgr.spawn(kw["name"], kw["role"], kw["prompt"]))
    _register_tool_pair(tools_by_name, handlers, "list_teammates", list_teammates_tool, lambda **kw: team_mgr.list_all())
    _register_tool_pair(
        tools_by_name,
        handlers,
        "send_message",
        send_message_tool,
        lambda **kw: services.bus.send("lead", kw["to"], kw["content"], kw.get("msg_type", "message")),
    )
    _register_tool_pair(tools_by_name, handlers, "read_inbox", read_inbox_tool, lambda **kw: json.dumps(services.bus.read_inbox("lead"), indent=2))
    _register_tool_pair(tools_by_name, handlers, "broadcast", broadcast_tool, lambda **kw: services.bus.broadcast("lead", kw["content"], team_mgr.member_names()))
    _register_tool_pair(tools_by_name, handlers, "idle", idle_tool, lambda **kw: "主代理不会进入空闲状态。")


def _register_protocol_tools(
    tools_by_name: dict[str, object],
    handlers: dict[str, ToolHandler],
    services: AppServices,
    shutdown_requests: dict[str, dict],
    plan_requests: dict[str, dict],
):
    """注册协议相关工具，例如压缩、审批和关闭请求。"""

    def handle_shutdown_request(teammate: str) -> str:
        req_id = str(uuid.uuid4())[:8]
        shutdown_requests[req_id] = {"target": teammate, "status": "pending"}
        services.bus.send("lead", teammate, "请关闭当前运行。", "shutdown_request", {"request_id": req_id})
        return f"已向队友 '{teammate}' 发送关闭请求，编号：{req_id}"

    def handle_plan_review(request_id: str, approve: bool, feedback: str = "") -> str:
        req = plan_requests.get(request_id)
        if not req:
            return f"错误：未知的计划审批 request_id '{request_id}'"
        req["status"] = "approved" if approve else "rejected"
        services.bus.send(
            "lead",
            req["from"],
            feedback,
            "plan_approval_response",
            {"request_id": request_id, "approve": approve, "feedback": feedback},
        )
        return f"已对 '{req['from']}' 的计划执行{ '批准' if approve else '拒绝' }"

    @tool("compress")
    def compress_tool() -> str:
        """手动压缩对话上下文。"""
        return "正在压缩上下文……"

    @tool("shutdown_request")
    def shutdown_request_tool(teammate: str) -> str:
        """请求某个队友关闭。"""
        return handle_shutdown_request(teammate)

    @tool("plan_approval")
    def plan_approval_tool(request_id: str, approve: bool, feedback: str = "") -> str:
        """批准或拒绝队友提交的计划。"""
        return handle_plan_review(request_id, approve, feedback)

    _register_tool_pair(tools_by_name, handlers, "compress", compress_tool, lambda **kw: "正在压缩上下文……")
    _register_tool_pair(tools_by_name, handlers, "shutdown_request", shutdown_request_tool, lambda **kw: handle_shutdown_request(kw["teammate"]))
    _register_tool_pair(
        tools_by_name,
        handlers,
        "plan_approval",
        plan_approval_tool,
        lambda **kw: handle_plan_review(kw["request_id"], kw["approve"], kw.get("feedback", "")),
    )


def build_tool_runtime(services: AppServices, team_mgr) -> ToolRuntime:
    """把基础工具、业务工具和协议工具装配成完整运行时。"""

    base_tools, base_handlers = build_base_toolset(services)
    tools_by_name = dict(base_tools)
    handlers = dict(base_handlers)
    shutdown_requests: dict[str, dict] = {}
    plan_requests: dict[str, dict] = {}

    def run_task_subagent(prompt: str, agent_type: str = "Explore") -> str:
        """为 `task` 工具构造适合当前任务类型的子代理工具集。"""
        sub_tools = [
            base_tools["bash"],
            base_tools["glob_files"],
            base_tools["grep_content"],
            base_tools["read_file"],
            base_tools["read_file_segment"],
        ]
        if agent_type != "Explore":
            sub_tools += [base_tools["write_file"], base_tools["edit_file"]]
        return run_subagent(prompt, agent_type, sub_tools)

    _register_task_tools(tools_by_name, handlers, services, run_task_subagent)
    _register_collaboration_tools(tools_by_name, handlers, services, team_mgr)
    _register_protocol_tools(tools_by_name, handlers, services, shutdown_requests, plan_requests)

    return ToolRuntime(
        tools=list(tools_by_name.values()),
        handlers=handlers,
        base_tools=base_tools,
        cacheable_tool_names={"glob_files", "grep_content", "read_file", "read_file_segment"},
        cache_invalidating_tool_names={"write_file", "edit_file"},
        shutdown_requests=shutdown_requests,
        plan_requests=plan_requests,
    )


def make_tool_cache_key(tool_name: str, tool_input: dict) -> str:
    """把工具名和参数序列化成缓存键。"""
    payload = json.dumps(tool_input, ensure_ascii=False, sort_keys=True, default=str)
    return f"{tool_name}:{payload}"


def store_tool_cache(cache: dict[str, str], cache_order: list[str], cache_key: str, output: str):
    """按固定上限维护一个最近使用的工具结果缓存。"""
    cache[cache_key] = output
    if cache_key in cache_order:
        cache_order.remove(cache_key)
    cache_order.append(cache_key)
    while len(cache_order) > TOOL_CACHE_LIMIT:
        oldest = cache_order.pop(0)
        cache.pop(oldest, None)
