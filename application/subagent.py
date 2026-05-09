"""子代理执行流程。"""

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from final_version_app.application.workspace_ops import (
    run_edit,
    run_glob,
    run_grep,
    run_read,
    run_read_segment,
    run_write,
)
from final_version_app.config import SUBAGENT_MAX_CYCLES
from final_version_app.infra.llm import format_tool_guide, invoke_langchain, render_ai_text
from final_version_app.infra.shell import run_bash


def run_subagent(prompt: str, agent_type: str, sub_tools: list) -> str:
    """运行一个短生命周期的子代理，并限制其可用工具集合。"""
    sub_handlers = {
        "bash": lambda **kw: run_bash(kw["command"]),
        "glob_files": lambda **kw: run_glob(kw["pattern"], kw.get("path", "."), kw.get("limit", 100)),
        "grep_content": lambda **kw: run_grep(
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
        "read_file": lambda **kw: run_read(kw["path"]),
        "read_file_segment": lambda **kw: run_read_segment(
            kw["path"],
            kw.get("start_line"),
            kw.get("end_line"),
            kw.get("center_line"),
            kw.get("before", 20),
            kw.get("after", 20),
        ),
        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
    }
    system = (
        "你是一个编程子代理。请完成委派给你的任务，并返回简洁总结。\n"
        f"子代理类型：{agent_type}\n"
        "工具使用说明：\n"
        f"{format_tool_guide(sub_tools)}\n"
        "规则：\n"
        "- 读取文件前，优先使用 glob_files 和 grep_content 缩小搜索范围。\n"
        "- 当 grep_content 返回命中行后，优先使用 read_file_segment，不要反复整文件读取。\n"
        "- 编辑文件前，优先先读取文件内容。\n"
        "- 修改要尽量小而精准。\n"
        "- 如果命令或编辑失败，先分析输出，再用更小范围的动作重试。"
    )
    messages: list[BaseMessage] = [HumanMessage(content=prompt)]
    response: AIMessage | None = None
    for _ in range(SUBAGENT_MAX_CYCLES):
        response = invoke_langchain(messages=messages, tools=sub_tools, system=system, max_tokens=8000)
        messages.append(response)
        if not (response.tool_calls or []):
            break
        for call in response.tool_calls or []:
            tool_name = str(call.get("name", ""))
            tool_input = call.get("args", {})
            if not isinstance(tool_input, dict):
                tool_input = {}
            tool_call_id = str(call.get("id", ""))
            handler = sub_handlers.get(tool_name, lambda **kw: "未知工具")
            output = str(handler(**tool_input))[:50000]
            messages.append(ToolMessage(content=output, tool_call_id=tool_call_id))
    if response:
        text = render_ai_text(response).strip()
        if text:
            return text
        if response.tool_calls:
            return "子代理已达到工具调用次数上限，请结合上面的部分结果继续处理。"
        return "（无总结）"
    return "（子代理执行失败）"
