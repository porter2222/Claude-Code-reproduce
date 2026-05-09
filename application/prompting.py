"""提示词组装工具。"""

from final_version_app.config import OS_NAME, SHELL_NAME, WORKDIR
from final_version_app.infra.llm import format_tool_guide


def build_base_system(skills_description: str) -> str:
    """构造基础系统提示词，不包含工具清单。"""
    return f"""你是一个位于 {WORKDIR} 的编程智能体，请优先通过工具完成任务。
对于多步骤工作，优先使用 task_create、task_update、task_list 管理任务。
对于短期步骤清单，优先使用 TodoWrite。
需要子代理协作时使用 task。
需要专门知识时使用 load_skill。
可用技能：{skills_description}

运行环境：
- 操作系统：{OS_NAME}
- Shell：{SHELL_NAME}

命令使用建议：
- 如果当前系统是 Windows，优先使用 PowerShell 原生命令：
  Get-ChildItem、Select-String、Measure-Object、Get-Content。
- 避免在 Windows 上使用仅适用于 Linux 的命令或参数，例如：ls -la、wc。"""


def build_system(base_system: str, tools: list) -> str:
    """在基础提示词上追加工具说明和操作规则。"""
    return (
        f"{base_system}\n\n"
        "工具使用说明：\n"
        f"{format_tool_guide(tools)}\n\n"
        "操作规则：\n"
        "- 在编辑前，先用 read_file 或 bash 验证你的判断。\n"
        "- 打开文件前，优先使用 glob_files 查找候选路径。\n"
        "- 在大范围读取文件或执行扫描前，优先用 grep_content 定位相关代码或文本。\n"
        "- 当 grep_content 返回命中行后，优先使用 read_file_segment 查看局部，不要反复整文件读取。\n"
        "- 定点修改优先使用 edit_file，完整替换或新建文件优先使用 write_file。\n"
        "- 对于多步骤任务，使用 task_create、task_update、task_list、task_get 维护明确的任务依赖图。\n"
        "- background_run 只用于耗时较长的命令，并通过 check_background 或通知机制获取结果。\n"
        "- 团队协作时，使用 send_message、broadcast、read_inbox，并给出明确的行动项。\n"
        "- shutdown_request、plan_approval 仅用于协议动作，不要拿它们代替普通任务更新。"
    )
