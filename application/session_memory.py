"""Session-memory extraction and compaction helpers."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from final_version_app.config import (
    MEMORY_DIR,
    SESSION_MEMORY_COMPACT_BUFFER,
    SESSION_MEMORY_COMPACT_WAIT_SECONDS,
    SESSION_MEMORY_EXTRACTION_STALE_SECONDS,
    SESSION_MEMORY_INIT_TOKENS,
    SESSION_MEMORY_MAX_RECENT_TOKENS,
    SESSION_MEMORY_MIN_RECENT_TOKENS,
    SESSION_MEMORY_MIN_TEXT_MESSAGES,
    SESSION_MEMORY_PATH,
    SESSION_MEMORY_RESERVED_BUDGET,
    SESSION_MEMORY_SECTION_CHAR_LIMIT,
    SESSION_MEMORY_STATE_PATH,
    SESSION_MEMORY_TOOL_THRESHOLD,
    SESSION_MEMORY_UPDATE_TOKENS,
    TOKEN_THRESHOLD,
)
from final_version_app.infra.llm import estimate_tokens, invoke_langchain, render_ai_text
from final_version_app.infra.workspace import read_text_auto

SESSION_MEMORY_SECTIONS = (
    # 长期结构化 session memory 的固定 schema。
    # 提取器可以改写各 section 正文，但不能改 section 的布局和顺序。
    "Session Title",
    "Current State",
    "Task specification",
    "Files and Functions",
    "Workflow",
    "Errors & Corrections",
    "Codebase and System Documentation",
    "Learnings",
    "Key results",
    "Worklog",
)
COMPACT_BOUNDARY_TAG = "<compact-boundary>"
SESSION_UUID_KEY = "session_message_uuid"


def default_session_memory_text() -> str:
    """
    用途：生成一份空的 session memory 模板文本。
    输入：无。
    输出：一段 Markdown 字符串，包含 10 个固定 section（Session Title、Current State 等），
          每个 section 的内容都是 "_Pending._"。
    举例返回：
      ## Session Title
      
      _Pending._
      
      ## Current State
      
      _Pending._
      ...
    """
    parts = []
    for title in SESSION_MEMORY_SECTIONS:
        parts.append(f"## {title}")
        parts.append("")
        parts.append("_Pending._")
        parts.append("")
    return "\n".join(parts).strip() + "\n"


@dataclass
class SessionMemoryState:
    # `session_memory.md` 只被认为“可靠覆盖”到了
    # `last_summarized_message_id` 这一条消息为止。
    # 压缩时会用它决定：哪些旧历史可以交给 memory 文件，
    # 哪些尾部消息还必须保留为 recent raw messages。
    last_summarized_message_id: str | None = None
    tokens_at_last_extraction: int = 0
    session_memory_initialized: bool = False
    extraction_started_at: float | None = None
    # 上次成功提取时，看到的最后一条消息 uuid。
    # 用它统计自上次提取以来又新增了多少工具调用。
    last_memory_message_uuid: str | None = None


def ensure_message_uuid(message: BaseMessage) -> str:
    """
    用途：确保一条消息有稳定的 UUID。如果消息已经有 UUID，直接返回；如果没有，生成一个新的并贴在消息上。
    输入：一条 LangChain 消息（HumanMessage / AIMessage / ToolMessage / SystemMessage）。
    输出：该消息的 UUID 字符串（之前已有则复用，没有则新生成）。
    关键行为：UUID 存在哪里？→ 存在 message.additional_kwargs["session_message_uuid"] 里。
    幂等性：对同一条消息重复调用，每次返回相同的 UUID。
    """
    if SESSION_UUID_KEY in message.additional_kwargs:
        return str(message.additional_kwargs[SESSION_UUID_KEY])
    msg_uuid = str(uuid.uuid4())
    message.additional_kwargs[SESSION_UUID_KEY] = msg_uuid
    return msg_uuid


def annotate_messages(messages: list[BaseMessage]):
    """
    用途：给消息列表里的每条消息贴上 UUID（如果还没有的话）。
    输入：消息列表 list[BaseMessage]。
    输出：无（不 return 任何东西）。但传入的 messages 里的每条消息都会被修改——它们的
          additional_kwargs 里会多一个 "session_message_uuid" 键。
    为什么不需要 return？因为 Python 传的是引用，函数内部改了消息对象的属性，外面能看到。
    """
    for message in messages:
        ensure_message_uuid(message)


def message_uuid(message: BaseMessage) -> str:
    """
    用途：获取一条消息的 UUID（等价于 ensure_message_uuid 的别名）。
    输入：一条 LangChain 消息。
    输出：该消息的 UUID 字符串。
    """
    return ensure_message_uuid(message)


def is_compact_boundary(message: BaseMessage) -> bool:
    """
    用途：判断一条消息是不是"压缩边界标记"（即之前压缩时留下的标记）。
    输入：一条消息。
    输出：True / False。
    判断依据：(1) 是 SystemMessage 类型 (2) 内容里包含 "<compact-boundary>" 标签。
    """
    return isinstance(message, SystemMessage) and COMPACT_BOUNDARY_TAG in str(message.content)


def compact_boundary_message(
    trigger: str,
    token_estimate: int,
    last_message_uuid: str | None,
    head_uuid: str | None,
    anchor_uuid: str | None,
    tail_uuid: str | None,
) -> SystemMessage:
    """
    用途：生成一条"压缩边界标记"消息。它的作用是告诉后续的压缩逻辑：
          "这里已经压缩过了，别再把保留范围扩过这条消息"——防止上下文无限膨胀。
    输入：
      - trigger: 触发压缩的原因（"auto" / "manual" / "overflow"）
      - token_estimate: 压缩时的 token 估算值
      - last_message_uuid: 当前消息列表最后一条的 UUID
      - head_uuid: recent tail 第一条的 UUID
      - anchor_uuid: summarized 边界的下一条的 UUID
      - tail_uuid: recent tail 最后一条的 UUID
    输出：一条 SystemMessage，内容里包含 "<compact-boundary>" 标签和以上信息的 JSON。
    """
    payload = {
        "trigger": trigger,
        "token_estimate": token_estimate,
        "last_message_uuid": last_message_uuid,
        "head_uuid": head_uuid,
        "anchor_uuid": anchor_uuid,
        "tail_uuid": tail_uuid,
    }
    msg = SystemMessage(content=f"{COMPACT_BOUNDARY_TAG}\n{json.dumps(payload, ensure_ascii=False)}")
    ensure_message_uuid(msg)
    return msg


def compact_summary_message(memory_text: str, truncated: bool) -> HumanMessage:
    """
    用途：生成一条包含 session memory 内容的 HumanMessage，用于注入到压缩后的消息列表中。
          这相当于用"平时持续维护的结构化记忆"来替代"临时总结整段历史"。
    输入：
      - memory_text: session_memory.md 的完整内容（Markdown 格式）
      - truncated: 是否因为太长而被截断过（True 的话会在末尾加提示文字）
    输出：一条 HumanMessage，内容用 <session-memory> XML 标签包裹。
    """
    suffix = "\n\n[session memory truncated for context budget; see .memory/session_memory.md for the full document]" if truncated else ""
    msg = HumanMessage(content=f"<session-memory>\n{memory_text}{suffix}\n</session-memory>")
    ensure_message_uuid(msg)
    return msg


def _message_text(message: BaseMessage) -> str:
    """
    用途：提取一条消息中的"可读文本内容"，ToolMessage 的文本为空（因为工具结果太长了不适合计数）。
    输入：一条消息。
    输出：字符串。
      - AIMessage: 提取 render_ai_text 的纯文本部分（不含 tool_calls）
      - ToolMessage: 返回空字符串（工具结果不计入"文本消息"计数）
      - HumanMessage / SystemMessage: 直接返回 content 的内容
    用途：在 compact_messages 中统计"有多少条有意义的文本消息"，用来判断是否够 min_text_messages。
    """
    if isinstance(message, AIMessage):
        return render_ai_text(message).strip()
    if isinstance(message, ToolMessage):
        return ""
    return str(message.content).strip()


def _message_token_size(message: BaseMessage) -> int:
    """
    用途：估算一条消息占多少个 token。
    输入：一条消息。
    输出：int（token 数量）。
    备注：这个函数在代码中定义了但没有被实际调用，属于保留用途。
    """
    return estimate_tokens([message])


def _serialize_message(message: BaseMessage) -> dict:
    """
    用途：把一条 LangChain 消息转成普通的 Python 字典，方便序列化成 JSON。
    输入：一条消息。
    输出：字典 dict，包含以下键：
      - uuid: 消息的 UUID
      - type: 消息类型（HumanMessage / AIMessage 等）
      - content: 消息文本内容
      - 如果是 AIMessage，额外包含: assistant_text, tool_calls, 可选的 message_id
      - 如果是 ToolMessage，额外包含: tool_call_id
    用途：在 maybe_schedule_extraction 中给整个消息列表拍"快照"，传给后台线程。
    """
    item = {
        "uuid": message_uuid(message),
        "type": message.__class__.__name__,
        "content": str(message.content),
    }
    if isinstance(message, AIMessage):
        item["assistant_text"] = render_ai_text(message)
        item["tool_calls"] = message.tool_calls or []
        group_id = getattr(message, "id", None) or message.additional_kwargs.get("message_id")
        if group_id:
            item["message_id"] = str(group_id)
    if isinstance(message, ToolMessage):
        item["tool_call_id"] = message.tool_call_id
    return item


def _slice_after_uuid(serialized_messages: list[dict], last_uuid: str | None) -> list[dict]:
    """
    用途：从序列化的消息列表中，截取"某个 UUID 之后"的部分（增量提取的核心）。
    输入：
      - serialized_messages: 经过 _serialize_message 处理后的消息字典列表
      - last_uuid: 上次处理到的消息 UUID（即 last_summarized_message_id）
    输出：从 last_uuid 之后开始的消息列表。如果 last_uuid 为 None，返回全部。
    举例：
      messages = [{uuid:"A"}, {uuid:"B"}, {uuid:"C"}, {uuid:"D"}]
      _slice_after_uuid(messages, "B") → [{uuid:"C"}, {uuid:"D"}]
    用途：后台提取时只处理"尚未沉淀进 memory 的新消息"，避免重复处理全部历史。
    """
    if not last_uuid:
        return serialized_messages
    for idx, item in enumerate(serialized_messages):
        if item["uuid"] == last_uuid:
            return serialized_messages[idx + 1:]
    return serialized_messages


def _natural_pause(messages: list[BaseMessage]) -> bool:
    """
    用途：判断对话是否到达了一个"自然停顿点"。
    输入：消息列表。
    输出：True / False。
    判断逻辑：从后往前找第一条 AIMessage，检查它有没有 tool_calls。
      - 没有 tool_calls → True（agent 只是说了句话，没有调用工具，适合安全地推进记忆边界）
      - 有 tool_calls → False（agent 调用了工具但还没看到结果，此时推进边界可能切碎上下文链）
    如果没有 AIMessage → False。
    """
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return not bool(message.tool_calls)
    return False


def _candidate_last_summarized_message_id(messages: list[BaseMessage]) -> str | None:
    """
    用途：计算一个"安全的记忆推进边界"——如果到达自然停顿点，返回最后一条消息的 UUID，否则返回 None。
    输入：消息列表。
    输出：UUID 字符串或 None。
      - 安全（最后一条 AIMessage 没有 tool_calls）：返回该消息的 UUID
      - 不安全（最后一条是 ToolMessage 或 AIMessage 有 tool_calls）：返回 None
    为什么需要安全？如果推进边界时最后还有未闭合的 tool_call/tool_result 配对，
    下次提取时这些配对会被切碎，上下文链断裂。
    """
    if not messages:
        return None
    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and not last_message.tool_calls:
        return message_uuid(last_message)
    return None


def _assistant_group_id(message: BaseMessage) -> str | None:
    """
    用途：获取一条 AIMessage 的"分组 ID"。
    输入：一条消息。
    输出：分组 ID 字符串，或 None（如果不是 AIMessage 或没有 group_id）。
    背景：有些 LLM 提供商（如 Anthropic）会把同一个逻辑回复拆成多条片段，但共享同一个 group_id。
    用途：在 compact_messages 中检查 AIMessage 是否跨了保留区边界，保证同一组的消息一起保留。
    查找顺序：message.id → additional_kwargs["message_id"] → 空字符串。
    """
    if not isinstance(message, AIMessage):
        return None
    return str(getattr(message, "id", None) or message.additional_kwargs.get("message_id") or "")


def _normalize_session_memory(text: str) -> str:
    """
    用途：把输入的 Markdown 文本"归一化"——不管 LLM 输出格式有多乱，强制重排回 10 个固定 section 结构。
    输入：任意 Markdown 字符串（可能来自 LLM 输出，格式不保证正确）。
    输出：标准化的 Markdown 字符串，严格按 SESSION_MEMORY_SECTIONS 的顺序和格式排列。
    防御性：
      - 如果 LLM 漏掉了某个 section → 补上并填 "_Pending._"
      - 如果 LLM 写了多余的 section → 丢弃
      - 如果 section 顺序乱了 → 按固定顺序重排
      - 如果换行符是 \r\n → 统一成 \n
    用途：在每次后台提取写文件之前，确保 memory 文件的结构永远是正确的。
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections: dict[str, list[str]] = {title: [] for title in SESSION_MEMORY_SECTIONS}
    current_title: str | None = None
    for line in lines:
        if line.startswith("## "):
            title = line[3:].strip()
            current_title = title if title in sections else None
            continue
        if current_title is not None:
            sections[current_title].append(line)
    chunks = []
    for title in SESSION_MEMORY_SECTIONS:
        body = "\n".join(sections[title]).strip() or "_Pending._"
        chunks.append(f"## {title}\n\n{body}\n")
    return "\n".join(chunks).strip() + "\n"


def _has_non_template_content(text: str) -> bool:
    """
    用途：判断一段文本是否不再是空的模板内容（是否写入过真实信息）。
    输入：Markdown 字符串。
    输出：True / False。
      - True: 所有内容都是 "_Pending._" 或完全空白 → 还是模板，不可用
      - False: 至少有一个 section 有真实内容 → 可用
    用途：在 has_usable_memory 中判断 session_memory.md 是否真的值得在压缩中使用。
    """
    if not text.strip():
        return False
    normalized = _normalize_session_memory(text)
    return normalized.strip() != default_session_memory_text().strip()


def _truncate_memory_for_context(text: str) -> tuple[str, bool]:
    """
    用途：把 session memory 文本按 section 截断，防止 memory 内容本身太大撑爆上下文。
    输入：原始 session memory Markdown 文本。
    输出：
      - 截断后的 Markdown 文本（每个 section 超过 SESSION_MEMORY_SECTION_CHAR_LIMIT=2000 字符的部分会被切掉）
      - 布尔值：True 表示至少有一个 section 被截断了
    用途：在 compact_messages 中把 memory 内容注入上下文之前，先截断到合理大小。
    """
    sections = _normalize_session_memory(text).strip().split("\n## ")
    rendered_sections: list[str] = []
    truncated = False
    for idx, raw_section in enumerate(sections):
        section = raw_section if idx == 0 else f"## {raw_section}"
        lines = section.splitlines()
        if not lines:
            continue
        heading = lines[0]
        body = "\n".join(lines[1:]).strip()
        if len(body) > SESSION_MEMORY_SECTION_CHAR_LIMIT:
            body = body[:SESSION_MEMORY_SECTION_CHAR_LIMIT].rstrip() + "\n... [truncated]"
            truncated = True
        rendered_sections.append(f"{heading}\n\n{body}".rstrip())
    return "\n\n".join(rendered_sections).strip(), truncated


class SessionMemoryManager:
    """
    用途：管理结构化 session memory 文件的读写和压缩集成。
    核心职责：
      1. 维护 .memory/session_memory.md（结构化长期记忆）
      2. 在后台线程中周期性调用 LLM 提取新信息、更新 memory 文件
      3. 响应压缩请求（compact_messages），用 memory 文件替代旧历史
    使用方式：
      manager = SessionMemoryManager()
      manager.maybe_schedule_extraction(messages)   # 主循环每轮调用
      manager.compact_messages(messages, ...)        # 触发压缩时调用
    """

    def __init__(self):
        """
        用途：创建 SessionMemoryManager 实例。
        输入：无。
        输出：无。但会做以下事情：
          - 创建 threading.Lock（保护 _state 并发访问）
          - 从文件加载状态到 self._state
          - 确保 .memory/ 目录和 session_memory.md / session_memory_state.json 存在
        """
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state = self._load_state()
        self.ensure_files()

    def ensure_files(self):
        """
        用途：确保 .memory/ 目录以及两个关键文件存在。如果不存在就创建。
        输入：无。
        输出：无（副作用：在磁盘上创建目录和文件）。
        创建的文件：
          - .memory/session_memory.md：初始内容为模板（所有 section 都是 "_Pending._"）
          - .memory/session_memory_state.json：初始状态（空）
        """
        MEMORY_DIR.mkdir(exist_ok=True)
        if not SESSION_MEMORY_PATH.exists():
            SESSION_MEMORY_PATH.write_text(default_session_memory_text(), encoding="utf-8")
        if not SESSION_MEMORY_STATE_PATH.exists():
            self._save_state(self._state)

    def _load_state(self) -> SessionMemoryState:
        """
        用途：从 .memory/session_memory_state.json 加载持久化的状态。
        输入：无。
        输出：SessionMemoryState 实例。
        行为：
          - 如果文件不存在 → 返回一个全新的 SessionMemoryState() 空状态
          - 如果文件损坏或解析失败 → 返回空状态（安全降级）
          - 如果文件正常 → 解析 JSON 并填充到 SessionMemoryState
        """
        MEMORY_DIR.mkdir(exist_ok=True)
        if not SESSION_MEMORY_STATE_PATH.exists():
            return SessionMemoryState()
        try:
            payload = json.loads(read_text_auto(SESSION_MEMORY_STATE_PATH))
        except Exception:
            return SessionMemoryState()
        return SessionMemoryState(
            last_summarized_message_id=payload.get("last_summarized_message_id"),
            tokens_at_last_extraction=int(payload.get("tokens_at_last_extraction", 0) or 0),
            session_memory_initialized=bool(payload.get("session_memory_initialized", False)),
            extraction_started_at=payload.get("extraction_started_at"),
            last_memory_message_uuid=payload.get("last_memory_message_uuid"),
        )

    def _save_state(self, state: SessionMemoryState):
        """
        用途：把当前状态持久化到 .memory/session_memory_state.json。
        输入：SessionMemoryState 实例。
        输出：无（副作用：写入 JSON 文件）。
        """
        SESSION_MEMORY_STATE_PATH.write_text(json.dumps(asdict(state), ensure_ascii=False, indent=2), encoding="utf-8")

    def _count_tool_calls_since(self, messages: list[BaseMessage], last_uuid: str | None) -> int:
        """
        用途：统计从上次提取之后，新增了多少次工具调用。
        输入：
          - messages: 当前完整消息列表
          - last_uuid: 上次提取时看到的最后一条消息的 UUID（即 last_memory_message_uuid）
        输出：int（新增的工具调用次数）。
        判断逻辑：从 last_uuid 之后开始遍历消息，每遇到一条 AIMessage，就加上它的 tool_calls 数量。
        用途：在 maybe_schedule_extraction 中判断"新增的工作量是否足够多，值得更新 memory"。
        """
        count = 0
        seen_anchor = last_uuid is None
        for message in messages:
            msg_uuid = message_uuid(message)
            if not seen_anchor:
                if msg_uuid == last_uuid:
                    seen_anchor = True
                continue
            if isinstance(message, AIMessage):
                count += len(message.tool_calls or [])
        return count

    def _run_memory_extraction_llm(self, prompt: str) -> str:
        """
        用途：调用 LLM 执行一次 session memory 提取/更新。
        输入：prompt 字符串（包含当前 memory 内容和需要合并的新消息 JSON）。
        输出：LLM 返回的 Markdown 字符串（新的 session memory 内容）。
        备注：这是一个纯 LLM 调用，可能耗时几秒到几十秒。
        """
        response = invoke_langchain(messages=[HumanMessage(content=prompt)], tools=[], system=None, max_tokens=4000)
        return render_ai_text(response)

    def maybe_schedule_extraction(self, messages: list[BaseMessage]):
        """
        用途：判断当前是否值得启动一次后台 memory 提取。这是整个提取流程的总闸门。
        输入：当前消息列表。
        输出：无（副作用：可能启动一个后台线程执行 _extract_memory）。
        触发条件（满足全部）：
          初始化前：
            - 上下文 token ≥ 10,000（首次触发门槛）
          初始化后：
            - 新增 token ≥ 5,000（距离上次提取以来）
            - 且（新增工具调用 ≥ 3 次 或 到达自然停顿点）
        不触发条件：
          - 已经有提取线程在运行且未卡死（卡死标准：超过 60 秒）
        启动前会：
          1. 拍消息列表快照（序列化）
          2. 读取当前 memory 文件
          3. 计算安全边界
          4. 把状态标记为"提取中"
          5. 启动守护线程
        """
        annotate_messages(messages)
        token_estimate = estimate_tokens(messages)
        with self._lock:
            state = self._state
            now = time.time()
            in_progress = state.extraction_started_at is not None and self._thread and self._thread.is_alive()
            if in_progress and state.extraction_started_at and now - state.extraction_started_at <= SESSION_MEMORY_EXTRACTION_STALE_SECONDS:
                return
            if not state.session_memory_initialized:
                if token_estimate < SESSION_MEMORY_INIT_TOKENS:
                    return
            else:
                if token_estimate - state.tokens_at_last_extraction < SESSION_MEMORY_UPDATE_TOKENS:
                    return
                if self._count_tool_calls_since(messages, state.last_memory_message_uuid) < SESSION_MEMORY_TOOL_THRESHOLD and not _natural_pause(messages):
                    return

            snapshot = [_serialize_message(message) for message in messages]
            current_memory = read_text_auto(SESSION_MEMORY_PATH)
            candidate_last_summarized = _candidate_last_summarized_message_id(messages)
            last_message_uuid = snapshot[-1]["uuid"] if snapshot else None

            # 在线程真正启动前，先把状态标记为"提取中"。
            # 这样如果此时发生压缩，压缩器才能决定要不要短暂等待。
            state.extraction_started_at = now
            self._save_state(state)
            self._thread = threading.Thread(
                target=self._extract_memory,
                args=(snapshot, current_memory, token_estimate, candidate_last_summarized, last_message_uuid),
                daemon=True,
            )
            self._thread.start()

    def _extract_memory(
        self,
        snapshot: list[dict],
        current_memory: str,
        token_estimate: int,
        candidate_last_summarized: str | None,
        last_message_uuid: str | None,
    ):
        """
        用途：后台工作线程，真正执行 memory 提取。调用 LLM 合并旧 memory 和新消息，写回文件。
        输入：
          - snapshot: 启动提取时消息列表的快照（序列化后的字典列表）
          - current_memory: 当前 .memory/session_memory.md 的内容
          - token_estimate: 启动提取时估算的 token 数
          - candidate_last_summarized: 安全边界 UUID（可能为 None）
          - last_message_uuid: 消息列表最后一条的 UUID
        输出：无（副作用：写入 session_memory.md，更新 session_memory_state.json）。
        流程：
          1. 找到上次处理到的边界（last_summarized_message_id）
          2. 截取边界之后的新消息（最多 80 条）
          3. 构建 prompt："当前 memory" + "新消息 JSON"
          4. 调用 LLM 生成新的 memory 内容
          5. 归一化（确保 section 结构正确）
          6. 写回 .memory/session_memory.md
          7. 更新状态（仅当 candidate_last_summarized 安全时才推进边界）
        异常处理：任何异常都会清理 extraction_started_at，但绝不推进 last_summarized_message_id。
        """
        try:
            with self._lock:
                previous_last = self._state.last_summarized_message_id
            unsummarized = _slice_after_uuid(snapshot, previous_last)
            transcript = json.dumps(unsummarized[-80:], ensure_ascii=False, indent=2)
            prompt = (
                "Update the session memory markdown for this coding session.\n"
                "Rules:\n"
                "- Keep the exact section headings and order.\n"
                "- Return only markdown.\n"
                "- Current State must describe the latest working state.\n"
                "- Errors & Corrections must capture failed approaches and fixes.\n"
                "- Files and Functions must list key files and responsibilities.\n"
                "- Key results must capture user-visible outcomes and pending deliverables.\n\n"
                f"Current session memory:\n```markdown\n{current_memory}\n```\n\n"
                f"New transcript slice to merge:\n```json\n{transcript}\n```"
            )
            memory_text = _normalize_session_memory(self._run_memory_extraction_llm(prompt) or current_memory)
            SESSION_MEMORY_PATH.write_text(memory_text, encoding="utf-8")
            with self._lock:
                self._state.session_memory_initialized = True
                self._state.tokens_at_last_extraction = token_estimate
                self._state.last_memory_message_uuid = last_message_uuid
                # 只有调用方提供了"安全边界"时，才推进压缩边界。
                # 安全边界的含义就是：最后 assistant turn 没有未闭合 tool call。
                if candidate_last_summarized:
                    self._state.last_summarized_message_id = candidate_last_summarized
                self._state.extraction_started_at = None
                self._save_state(self._state)
        except Exception:
            with self._lock:
                self._state.extraction_started_at = None
                self._save_state(self._state)

    def wait_for_extraction(self, timeout_seconds: int = SESSION_MEMORY_COMPACT_WAIT_SECONDS):
        """
        用途：等待后台提取线程写完最新的 memory 文件。压缩前调用。
        输入：timeout_seconds（等待超时秒数，默认 15）。
        输出：无。
        三种情况：
          - 没有提取线程在运行 → 直接返回
          - 提取线程运行中且未卡死 → join 等待最多 timeout 秒
          - 提取线程运行中但已卡死（超过 60 秒）→ 清理状态，不等了
        """
        thread: threading.Thread | None = None
        with self._lock:
            if not self._thread or not self._thread.is_alive():
                return
            started_at = self._state.extraction_started_at
            if started_at and time.time() - started_at > SESSION_MEMORY_EXTRACTION_STALE_SECONDS:
                self._state.extraction_started_at = None
                self._save_state(self._state)
                return
            thread = self._thread
        if thread:
            thread.join(timeout_seconds)

    def auto_compact_threshold(self) -> int:
        """
        用途：计算"提前触发压缩"的 token 阈值（不是硬上限本身）。
        输入：无。
        输出：int（token 数量，默认 100000 - 20000 - 13000 = 67000）。
        为什么提前触发？要给 compact summary（20,000）和下一轮推理输出（13,000）预留空间。
        计算公式：TOKEN_THRESHOLD - SESSION_MEMORY_RESERVED_BUDGET - SESSION_MEMORY_COMPACT_BUFFER
        """
        return max(1, TOKEN_THRESHOLD - SESSION_MEMORY_RESERVED_BUDGET - SESSION_MEMORY_COMPACT_BUFFER)

    def has_usable_memory(self) -> bool:
        """
        用途：检查 session_memory.md 是否真的有可用内容（不仅仅是模板）。
        输入：无。
        输出：True / False。
          - True: memory 文件中至少有一个 section 不是 "_Pending._" → 可用于压缩
          - False: memory 文件还是初始模板或空白 → 不可用，需走 legacy 压缩路径
        """
        self.ensure_files()
        try:
            return _has_non_template_content(read_text_auto(SESSION_MEMORY_PATH))
        except Exception:
            return False

    def compact_messages(
        self,
        messages: list[BaseMessage],
        token_estimate: int,
        trigger: str,
    ) -> list[BaseMessage] | None:
        """
        用途：用 session memory 替代旧历史，生成一个紧凑的消息列表（压缩的核心逻辑）。
        输入：
          - messages: 当前完整的消息列表
          - token_estimate: 当前的 token 估算值
          - trigger: 压缩触发原因（"auto" / "manual" / "overflow"）
        输出：
          - 压缩后的消息列表（如果成功）：[compact_boundary, session_memory, recent raw tail...]
          - None（如果失败，比如 memory 文件不可用或找不到边界）
        执行流程：
          1. annotate_messages（确保所有消息有 UUID）
          2. wait_for_extraction（等后台提取写完最新的 memory）
          3. has_usable_memory（检查 memory 是否真的有内容）
          4. 找到 last_summarized_message_id 对应的索引（summarized_idx）
          5. 找到上次 compact-boundary 的位置（floor_idx，防膨胀）
          6. 从 summarized_idx+1 开始向前回扩，构建 recent raw tail
             - 至少保留 10,000 tokens 和 5 条文本消息
             - 最多保留 40,000 tokens
          7. 回扩保链：如果保留区里有 ToolMessage 但对应的 AIMessage 在外面，包含进来
          8. 回扩保链：如果 AIMessage group_id 跨了边界，包含进来
          9. 截断 memory 内容（每个 section 最多 2000 字符）
          10. 返回：[compact_boundary, session_memory_summary, *recent_tail]
        """
        annotate_messages(messages)
        self.wait_for_extraction()
        if not self.has_usable_memory():
            return None

        with self._lock:
            state = self._state

        memory_text = read_text_auto(SESSION_MEMORY_PATH)
        summarized_idx = None
        if state.last_summarized_message_id:
            for idx, message in enumerate(messages):
                if message_uuid(message) == state.last_summarized_message_id:
                    summarized_idx = idx
                    break
            if summarized_idx is None:
                return None

        latest_boundary_idx = max((idx for idx, message in enumerate(messages) if is_compact_boundary(message)), default=-1)
        floor_idx = latest_boundary_idx + 1
        anchor_idx = summarized_idx + 1 if summarized_idx is not None else len(messages)
        retained_start = min(anchor_idx, len(messages))

        def current_slice() -> list[BaseMessage]:
            return messages[retained_start:]

        while retained_start > floor_idx:
            # 先构建"最小 recent raw tail"。
            # 这些消息太新、太细，不适合完全依赖后台 memory 文件，
            # 所以压缩后仍要原样保留。
            retained = current_slice()
            retained_tokens = estimate_tokens(retained) if retained else 0
            text_messages = sum(1 for message in retained if _message_text(message))
            if retained_tokens >= SESSION_MEMORY_MIN_RECENT_TOKENS and text_messages >= SESSION_MEMORY_MIN_TEXT_MESSAGES:
                break
            retained_start -= 1
            if estimate_tokens(current_slice()) >= SESSION_MEMORY_MAX_RECENT_TOKENS:
                break

        changed = True
        while changed:
            changed = False
            retained = current_slice()
            tool_ids = {message.tool_call_id for message in retained if isinstance(message, ToolMessage)}
            # 如果保留区里有 ToolMessage，
            # 那对应的 assistant tool_call 也必须一并保留，
            # 否则上下文语义链会断。
            for idx in range(retained_start - 1, floor_idx - 1, -1):
                message = messages[idx]
                if not isinstance(message, AIMessage):
                    continue
                call_ids = {str(call.get("id", "")) for call in message.tool_calls or []}
                if tool_ids & call_ids:
                    retained_start = idx
                    changed = True
                    break
            if changed:
                continue
            retained = current_slice()
            assistant_group_ids = {gid for gid in (_assistant_group_id(message) for message in retained) if gid}
            if not assistant_group_ids:
                continue
            # 有些 provider 会把一个逻辑上的 assistant 回复拆成多条片段，
            # 但共享同一个 message/group id。
            # 这里要把它们一起保留，避免 thinking/tool 上下文被撕裂。
            for idx in range(retained_start - 1, floor_idx - 1, -1):
                message = messages[idx]
                if _assistant_group_id(message) in assistant_group_ids:
                    retained_start = idx
                    changed = True
                    break

        retained = current_slice()
        rendered_memory, truncated = _truncate_memory_for_context(memory_text)
        head_uuid = message_uuid(retained[0]) if retained else None
        tail_uuid = message_uuid(retained[-1]) if retained else None
        anchor_uuid = message_uuid(messages[anchor_idx]) if anchor_idx < len(messages) else None
        last_message_uuid = message_uuid(messages[-1]) if messages else None
        # 压缩后的最终形态：
        # - 一条 system compact boundary，供后续压缩识别
        # - 一条 human compact summary，内容就是 session memory markdown
        # - 一段 recent raw messages，保留最新的原始上下文
        return [
            compact_boundary_message(trigger, token_estimate, last_message_uuid, head_uuid, anchor_uuid, tail_uuid),
            compact_summary_message(rendered_memory, truncated),
            *retained,
        ]
