from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
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
        raise RuntimeError("DummyChatOpenAI should not be invoked in runtime behavior tests.")


langchain_openai_stub.ChatOpenAI = _DummyChatOpenAI
sys.modules.setdefault("langchain_openai", langchain_openai_stub)

from langchain_core.messages import AIMessage, HumanMessage

from final_version_app.application.agent_loop import agent_loop
from final_version_app.application.container import AppServices
from final_version_app.application.team_runtime import TeammateManager
from final_version_app.application.tooling import ToolRuntime
from final_version_app.domain.approvals import ApprovalManager
from final_version_app.domain.background import BackgroundManager
from final_version_app.domain.messaging import MessageBus
from final_version_app.domain.tasks import TaskManager
from final_version_app.domain.todos import TodoManager


class _DummySessionMemory:
    def maybe_schedule_extraction(self, messages):
        return None

    def auto_compact_threshold(self) -> int:
        return 10**9


class _DummySkills:
    def descriptions(self) -> str:
        return ""


class RuntimeBehaviorTests(unittest.TestCase):
    def test_todowrite_defaults_active_form_when_missing(self):
        manager = TodoManager()
        rendered = manager.update(
            [
                {"content": "Inspect runtime", "status": "in_progress"},
                {"content": "Write summary", "status": "pending"},
            ]
        )
        self.assertIn("Inspect runtime <- Inspect runtime", rendered)
        self.assertEqual(manager.items[0]["activeForm"], "Inspect runtime")

    def test_todowrite_uses_fallback_text_for_missing_content(self):
        manager = TodoManager()
        rendered = manager.update(
            [
                {"activeForm": "Check memory flow", "status": "pending"},
            ]
        )
        self.assertIn("Check memory flow", rendered)
        self.assertEqual(manager.items[0]["content"], "Check memory flow")

    def test_agent_loop_stops_after_max_cycles(self):
        messages = [HumanMessage(content="Keep working forever.")]
        services = AppServices(
            todo=TodoManager(),
            skills=_DummySkills(),
            task_mgr=SimpleNamespace(),
            bg=BackgroundManager(),
            bus=MessageBus(),
            approvals=ApprovalManager(),
            session_memory=_DummySessionMemory(),
        )
        tool_runtime = ToolRuntime(
            tools=[],
            handlers={},
            base_tools={},
            cacheable_tool_names=set(),
            cache_invalidating_tool_names=set(),
            shutdown_requests={},
            plan_requests={},
        )

        endless_response = AIMessage(
            content="Calling a nonexistent tool again.",
            tool_calls=[{"id": "loop-1", "name": "unknown_tool", "args": {}}],
        )

        with patch("final_version_app.application.agent_loop.AGENT_LOOP_MAX_CYCLES", 2):
            with patch("final_version_app.application.agent_loop.invoke_langchain", return_value=endless_response):
                agent_loop(messages, services, tool_runtime, "system")

        self.assertIsInstance(messages[-1], AIMessage)
        self.assertIn("internal tool-call limit", str(messages[-1].content))

    def test_teammate_auto_report_writes_to_lead_inbox(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            team_dir = base / ".team"
            inbox_dir = team_dir / "inbox"
            tasks_dir = base / ".tasks"
            team_dir.mkdir()
            inbox_dir.mkdir(parents=True)
            tasks_dir.mkdir()

            services = AppServices(
                todo=TodoManager(),
                skills=_DummySkills(),
                task_mgr=SimpleNamespace(claim=lambda *args, **kwargs: "claimed"),
                bg=BackgroundManager(),
                bus=MessageBus(),
                approvals=ApprovalManager(),
                session_memory=_DummySessionMemory(),
            )

            with patch("final_version_app.application.team_runtime.TEAM_DIR", team_dir):
                with patch("final_version_app.application.team_runtime.TASKS_DIR", tasks_dir):
                    with patch("final_version_app.domain.messaging.INBOX_DIR", inbox_dir):
                        manager = TeammateManager(services)
                        manager._auto_report("reviewer", "auditor", "completed", "Looks good.")
                        inbox = services.bus.read_inbox("lead")

        self.assertEqual(len(inbox), 1)
        self.assertIn("本次状态为 `completed`", inbox[0]["content"])
        self.assertIn("Looks good.", inbox[0]["content"])

    def test_teammate_manager_resets_stale_working_state_on_startup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            team_dir = base / ".team"
            inbox_dir = team_dir / "inbox"
            tasks_dir = base / ".tasks"
            team_dir.mkdir()
            inbox_dir.mkdir(parents=True)
            tasks_dir.mkdir()
            config_path = team_dir / "config.json"
            config_path.write_text(
                '{"team_name":"default","members":[{"name":"reviewer","role":"auditor","status":"working"}]}',
                encoding="utf-8",
            )

            services = AppServices(
                todo=TodoManager(),
                skills=_DummySkills(),
                task_mgr=SimpleNamespace(claim=lambda *args, **kwargs: "claimed"),
                bg=BackgroundManager(),
                bus=MessageBus(),
                approvals=ApprovalManager(),
                session_memory=_DummySessionMemory(),
            )

            with patch("final_version_app.application.team_runtime.TEAM_DIR", team_dir):
                with patch("final_version_app.application.team_runtime.TASKS_DIR", tasks_dir):
                    with patch("final_version_app.domain.messaging.INBOX_DIR", inbox_dir):
                        manager = TeammateManager(services)

            member = manager._find("reviewer")
            self.assertIsNotNone(member)
            self.assertEqual(member["status"], "shutdown")


if __name__ == "__main__":
    unittest.main()
