"""LLM 网关与工具描述格式化。

这一层负责：
1. 根据模型名选择实际 provider
2. 统一 LangChain 调用入口
3. 把工具 schema 转成模型可读的说明文本
"""

from __future__ import annotations

import json
import os

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_openai import ChatOpenAI

from final_version_app.config import DASHSCOPE_BASE_URL, MODEL


def get_llm(max_tokens: int) -> ChatOpenAI:
    """根据 `MODEL_ID` 自动路由到对应 provider。"""
    model_name = MODEL.strip()
    lower_model = model_name.lower()

    if lower_model.startswith("qwen"):
        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise ValueError("MODEL_ID points to Qwen, but DASHSCOPE_API_KEY is missing.")
        return ChatOpenAI(
            model=model_name,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url=os.getenv("DASHSCOPE_BASE_URL") or DASHSCOPE_BASE_URL,
            extra_body={"enable_thinking": True},
        )

    if lower_model.startswith("deepseek"):
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("MODEL_ID points to DeepSeek, but DEEPSEEK_API_KEY is missing.")
        return ChatOpenAI(
            model=model_name,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com/v1",
        )

    api_key = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
    )
    if not api_key:
        raise ValueError("Missing API key for the configured MODEL_ID.")

    return ChatOpenAI(
        model=model_name,
        max_tokens=max_tokens,
        api_key=api_key,
        base_url=os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
    )


def render_ai_text(message: AIMessage) -> str:
    """把 LangChain 的 AIMessage 统一抽取成纯文本。"""
    if isinstance(message.content, str):
        return message.content
    parts = []
    for block in message.content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def invoke_langchain(
    messages: list[BaseMessage],
    tools: list | None = None,
    system: str | None = None,
    max_tokens: int = 8000,
) -> AIMessage:
    """统一的模型调用入口，负责注入 system 提示词和绑定工具。"""
    llm = get_llm(max_tokens)
    if tools:
        llm = llm.bind_tools(tools)
    inputs: list[BaseMessage] = []
    if system:
        inputs.append(SystemMessage(content=system))
    inputs.extend(messages)
    out = llm.invoke(inputs)
    if isinstance(out, AIMessage):
        return out
    return AIMessage(content=str(out))


def _tool_schema(tool_obj) -> dict:
    """兼容多种工具对象表示形式，提取参数 schema。"""
    if isinstance(tool_obj, dict):
        return tool_obj.get("input_schema", {"type": "object", "properties": {}})
    schema_model = getattr(tool_obj, "args_schema", None)
    if schema_model is None:
        return {"type": "object", "properties": {}}
    if hasattr(schema_model, "model_json_schema"):
        return schema_model.model_json_schema()
    if hasattr(schema_model, "schema"):
        return schema_model.schema()
    return {"type": "object", "properties": {}}


def format_tool_guide(tools: list) -> str:
    """把工具列表格式化成模型可读的工具说明。"""
    lines = []
    for tool_obj in tools:
        name = str(tool_obj.get("name", "unknown")) if isinstance(tool_obj, dict) else str(getattr(tool_obj, "name", "unknown"))
        desc = str(tool_obj.get("description", "")).strip() if isinstance(tool_obj, dict) else str(getattr(tool_obj, "description", "")).strip()
        schema = _tool_schema(tool_obj)
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
        arg_items = []
        if isinstance(props, dict):
            for arg, spec in props.items():
                if not isinstance(spec, dict):
                    spec = {}
                arg_type = str(spec.get("type", "any"))
                req = "required" if arg in required else "optional"
                arg_desc = str(spec.get("description", "")).strip()
                chunk = f"{arg}<{arg_type},{req}>"
                if arg_desc:
                    chunk += f": {arg_desc}"
                arg_items.append(chunk)
        args_text = "; ".join(arg_items) if arg_items else "(no args)"
        lines.append(f"- {name}: {desc} | args: {args_text}")
    return "\n".join(lines) if lines else "(no tools)"


def estimate_tokens(messages: list) -> int:
    """粗略估算上下文大小。

    这里不是精确 tokenizer，只用于触发压缩策略。
    """
    return len(json.dumps([str(message) for message in messages], ensure_ascii=False)) // 4
