from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

AGENTS_DIR = Path(__file__).resolve().parents[2]
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

langchain_openai_stub = ModuleType("langchain_openai")


class _DummyChatOpenAI:
    def __init__(self, *args, **kwargs):
        pass

    def bind_tools(self, tools):
        return self

    def invoke(self, inputs):
        raise RuntimeError("DummyChatOpenAI should not be invoked in session-memory tests.")


langchain_openai_stub.ChatOpenAI = _DummyChatOpenAI
sys.modules.setdefault("langchain_openai", langchain_openai_stub)

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from final_version_app.application import compression as compression_module
from final_version_app.application import repl as repl_module
from final_version_app.application import session_memory as session_memory_module
from final_version_app.application.session_memory import SessionMemoryManager, annotate_messages, default_session_memory_text


UPDATED_MEMORY = """## Session Title

Runtime import fix session

## Current State

The agent is updating runtime import handling and retaining recent raw messages.

## Task specification

Fix the runtime flow and preserve conversation continuity.

## Files and Functions

- agents/final_version_app/runtime.py: startup path bootstrap
- agents/final_version_app/application/agent_loop.py: main loop

## Workflow

Investigate, patch, verify, compact.

## Errors & Corrections

- Initial import path failed.
- Updated bootstrap path handling.

## Codebase and System Documentation

The package uses absolute imports under final_version_app.

## Learnings

Compact using session memory before legacy fallback.

## Key results

Session memory compaction is active.

## Worklog

- Read files
- Updated runtime
"""


def make_messages(with_terminal_tool_call: bool = False):
    messages = [
        HumanMessage(content="User requests a runtime import fix." + " context" * 20),
        AIMessage(content="I will inspect the runtime entrypoint."),
        AIMessage(
            content="Calling grep and read tools.",
            tool_calls=[
                {"id": "call-read", "name": "read_file", "args": {"path": "runtime.py"}},
                {"id": "call-grep", "name": "grep_content", "args": {"pattern": "final_version_app"}},
            ],
        ),
        ToolMessage(content="runtime.py contents ..." + " lines" * 30, tool_call_id="call-read"),
        ToolMessage(content="grep hits ..." + " match" * 30, tool_call_id="call-grep"),
        HumanMessage(content="Please continue and keep the recent context intact." + " note" * 20),
    ]
    if with_terminal_tool_call:
        messages.append(
            AIMessage(
                content="I am still waiting on tools.",
                tool_calls=[{"id": "call-edit", "name": "edit_file", "args": {"path": "runtime.py"}}],
            )
        )
    else:
        messages.append(AIMessage(content="I updated the runtime bootstrap and verified the next issue."))
    annotate_messages(messages)
    return messages


class SessionMemoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.memory_dir = self.base / ".memory"
        self.memory_path = self.memory_dir / "session_memory.md"
        self.state_path = self.memory_dir / "session_memory_state.json"
        self.transcript_dir = self.base / ".transcripts"

        self.patches = [
            patch.object(session_memory_module, "MEMORY_DIR", self.memory_dir),
            patch.object(session_memory_module, "SESSION_MEMORY_PATH", self.memory_path),
            patch.object(session_memory_module, "SESSION_MEMORY_STATE_PATH", self.state_path),
            patch.object(session_memory_module, "SESSION_MEMORY_INIT_TOKENS", 10),
            patch.object(session_memory_module, "SESSION_MEMORY_UPDATE_TOKENS", 5),
            patch.object(session_memory_module, "SESSION_MEMORY_TOOL_THRESHOLD", 1),
            patch.object(session_memory_module, "SESSION_MEMORY_MIN_RECENT_TOKENS", 1),
            patch.object(session_memory_module, "SESSION_MEMORY_MIN_TEXT_MESSAGES", 1),
            patch.object(session_memory_module, "SESSION_MEMORY_MAX_RECENT_TOKENS", 1000),
            patch.object(session_memory_module, "SESSION_MEMORY_RESERVED_BUDGET", 20),
            patch.object(session_memory_module, "SESSION_MEMORY_COMPACT_BUFFER", 5),
            patch.object(session_memory_module, "TOKEN_THRESHOLD", 100),
            patch.object(compression_module, "TRANSCRIPT_DIR", self.transcript_dir),
        ]
        for item in self.patches:
            item.start()
        self.addCleanup(self._cleanup_patches)

    def _cleanup_patches(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp_dir.cleanup()

    def test_session_memory_initializes_and_writes_markdown(self):
        manager = SessionMemoryManager()
        messages = make_messages()

        with patch.object(manager, "_run_memory_extraction_llm", return_value=UPDATED_MEMORY):
            manager.maybe_schedule_extraction(messages)
            manager.wait_for_extraction(5)

        self.assertTrue(self.memory_path.exists())
        memory_text = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("## Current State", memory_text)
        self.assertIn("Runtime import fix session", memory_text)
        self.assertTrue(manager._state.session_memory_initialized)
        self.assertGreater(manager._state.tokens_at_last_extraction, 0)

    def test_last_summarized_message_id_only_advances_without_tool_calls(self):
        manager = SessionMemoryManager()

        with patch.object(manager, "_run_memory_extraction_llm", return_value=UPDATED_MEMORY):
            messages = make_messages(with_terminal_tool_call=False)
            last_uuid = session_memory_module.message_uuid(messages[-1])
            manager.maybe_schedule_extraction(messages)
            manager.wait_for_extraction(5)
            self.assertEqual(manager._state.last_summarized_message_id, last_uuid)

            tool_call_messages = make_messages(with_terminal_tool_call=True)
            manager.maybe_schedule_extraction(tool_call_messages)
            manager.wait_for_extraction(5)
            self.assertEqual(manager._state.last_summarized_message_id, last_uuid)

    def test_compaction_prefers_session_memory_and_keeps_recent_messages(self):
        manager = SessionMemoryManager()
        messages = make_messages()
        with patch.object(manager, "_run_memory_extraction_llm", return_value=UPDATED_MEMORY):
            manager.maybe_schedule_extraction(messages)
            manager.wait_for_extraction(5)

        compacted = manager.compact_messages(messages, token_estimate=200, trigger="auto")
        self.assertIsNotNone(compacted)
        assert compacted is not None
        self.assertTrue(session_memory_module.is_compact_boundary(compacted[0]))
        self.assertIn("<session-memory>", str(compacted[1].content))
        self.assertIn("Session memory compaction is active.", str(compacted[1].content))
        self.assertIsInstance(compacted[-1], AIMessage)
        retained_tool_ids = {msg.tool_call_id for msg in compacted if isinstance(msg, ToolMessage)}
        if retained_tool_ids:
            retained_call_ids = {
                str(call.get("id", ""))
                for msg in compacted
                if isinstance(msg, AIMessage)
                for call in (msg.tool_calls or [])
            }
            self.assertTrue(retained_tool_ids.issubset(retained_call_ids))

    def test_compaction_falls_back_to_legacy_when_memory_unusable(self):
        manager = SessionMemoryManager()
        messages = make_messages()
        self.memory_dir.mkdir(exist_ok=True)
        self.memory_path.write_text(default_session_memory_text(), encoding="utf-8")

        with patch.object(compression_module, "_run_legacy_summary_llm", return_value="Legacy summary continuity."):
            compacted = compression_module.auto_compact(messages, session_memory=manager, trigger="auto")

        self.assertEqual(len(compacted), 2)
        self.assertIn("Legacy summary continuity.", str(compacted[0].content))
        self.assertIn("Understood. Continuing with summary context.", str(compacted[1].content))

    def test_runtime_config_validation_reports_missing_credentials_early(self):
        with patch.object(repl_module, "MODEL", "qwen3.6-plus"):
            with patch.object(repl_module, "get_llm", side_effect=ValueError("missing key")):
                with self.assertRaises(RuntimeError) as ctx:
                    repl_module.validate_runtime_config()
        self.assertIn("DASHSCOPE_API_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
