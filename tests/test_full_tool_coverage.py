from __future__ import annotations

import json
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
        raise RuntimeError("DummyChatOpenAI should not be invoked in coverage tests.")


langchain_openai_stub.ChatOpenAI = _DummyChatOpenAI
sys.modules.setdefault("langchain_openai", langchain_openai_stub)

from final_version_app.application.container import AppServices
from final_version_app.application.team_runtime import TeammateManager
from final_version_app.application.tooling import build_tool_runtime
from final_version_app.domain.approvals import ApprovalManager
from final_version_app.domain.background import BackgroundManager
from final_version_app.domain.messaging import MessageBus
from final_version_app.domain.skills import SkillLoader
from final_version_app.domain.tasks import TaskManager
from final_version_app.domain.todos import TodoManager


class _DummySessionMemory:
    def maybe_schedule_extraction(self, messages):
        return None

    def auto_compact_threshold(self) -> int:
        return 10**9


class FullToolCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.tasks_dir = self.base / ".tasks"
        self.team_dir = self.base / ".team"
        self.inbox_dir = self.team_dir / "inbox"
        self.skills_dir = self.base / "skills"
        self.tasks_dir.mkdir()
        self.inbox_dir.mkdir(parents=True)
        self.skills_dir.mkdir()
        (self.skills_dir / "demo").mkdir()
        (self.skills_dir / "demo" / "SKILL.md").write_text(
            "---\nname: demo\ndescription: demo skill\n---\nUse the demo flow.",
            encoding="utf-8",
        )

        patches = [
            patch("final_version_app.config.WORKDIR", self.base),
            patch("final_version_app.infra.workspace.WORKDIR", self.base),
            patch("final_version_app.infra.shell.WORKDIR", self.base),
            patch("final_version_app.domain.tasks.TASKS_DIR", self.tasks_dir),
            patch("final_version_app.application.team_runtime.TASKS_DIR", self.tasks_dir),
            patch("final_version_app.config.TASKS_DIR", self.tasks_dir),
            patch("final_version_app.domain.messaging.INBOX_DIR", self.inbox_dir),
            patch("final_version_app.config.INBOX_DIR", self.inbox_dir),
            patch("final_version_app.application.team_runtime.TEAM_DIR", self.team_dir),
            patch("final_version_app.config.TEAM_DIR", self.team_dir),
        ]
        for item in patches:
            item.start()
        self.addCleanup(lambda: [item.stop() for item in reversed(patches)])
        self.addCleanup(self.temp_dir.cleanup)

        self.services = AppServices(
            todo=TodoManager(),
            skills=SkillLoader(self.skills_dir),
            task_mgr=TaskManager(),
            bg=BackgroundManager(),
            bus=MessageBus(),
            approvals=ApprovalManager(),
            session_memory=_DummySessionMemory(),
        )
        self.team_mgr = TeammateManager(self.services)
        self.runtime = build_tool_runtime(self.services, self.team_mgr)

    def test_full_handler_coverage(self):
        handlers = self.runtime.handlers

        readme = self.base / "sample.txt"
        readme.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

        out = handlers["write_file"](path="new.txt", content="hello")
        self.assertIn("Wrote", out)

        out = handlers["read_file"](path="new.txt")
        self.assertIn("hello", out)

        out = handlers["edit_file"](path="new.txt", old_text="hello", new_text="hello world")
        self.assertIn("Edited", out)

        out = handlers["read_file_segment"](path="sample.txt", start_line=1, end_line=2)
        self.assertIn("alpha", out)

        out = handlers["glob_files"](pattern="*.txt", path=".")
        self.assertIn("sample.txt", out)

        out = handlers["grep_content"](pattern="beta", path=".", glob="*.txt")
        self.assertIn("beta", out)

        out = handlers["bash"](command="Get-Location")
        self.assertTrue(out)

        out = handlers["TodoWrite"](
            items=[
                {"content": "step one", "status": "in_progress"},
                {"content": "step two", "status": "pending"},
            ]
        )
        self.assertIn("step one", out)

        created = json.loads(handlers["task_create"](subject="task a", description="desc"))
        task_id = created["id"]

        out = handlers["task_get"](task_id=task_id)
        self.assertIn("task a", out)

        out = handlers["task_update"](task_id=task_id, status="completed", add_blocked_by=None, add_blocks=None)
        self.assertIn("completed", out)

        out = handlers["task_list"]()
        self.assertIn("#", out)

        created = json.loads(handlers["task_create"](subject="task b", description="claim me"))
        claim_id = created["id"]
        out = handlers["claim_task"](task_id=claim_id)
        self.assertIn("Claimed task", out)

        with patch("final_version_app.application.subagent.invoke_langchain", return_value=ModuleType("dummy")):
            pass

        with patch("final_version_app.application.subagent.invoke_langchain") as mock_sub_llm:
            from langchain_core.messages import AIMessage

            mock_sub_llm.return_value = AIMessage(content="Subagent done.", tool_calls=[])
            out = handlers["task"](prompt="Inspect sample", agent_type="Explore")
            self.assertIn("Subagent done", out)

        out = handlers["load_skill"](name="demo")
        self.assertIn("<skill name=\"demo\">", out)

        out = handlers["background_run"](command="Get-ChildItem", timeout=30)
        self.assertIn("已启动", out)
        bg_task_id = out.split()[1]

        import time

        for _ in range(20):
            status = handlers["check_background"](task_id=bg_task_id)
            if "[completed]" in status or "[error]" in status:
                break
            time.sleep(0.1)
        self.assertIn("[completed]", status)

        with patch("final_version_app.application.team_runtime.invoke_langchain") as mock_team_llm:
            from langchain_core.messages import AIMessage

            mock_team_llm.return_value = AIMessage(content="I finished the review.", tool_calls=[])
            out = handlers["spawn_teammate"](name="auditor", role="reviewer", prompt="Review memory")
            self.assertIn("已启动队友 'auditor'", out)
            time.sleep(0.3)

        out = handlers["list_teammates"]()
        self.assertIn("auditor", out)

        out = handlers["read_inbox"]()
        self.assertIn("\\u5df2\\u5f00\\u59cb\\u5de5\\u4f5c", out)

        out = handlers["send_message"](to="auditor", content="Please send details.", msg_type="message")
        self.assertIn("Sent message", out)

        out = handlers["broadcast"](content="Team sync")
        self.assertIn("Broadcast to", out)

        out = handlers["shutdown_request"](teammate="auditor")
        self.assertIn("发送关闭请求", out)

        out = handlers["plan_approval"](request_id="missing", approve=True, feedback="ok")
        self.assertIn("未知的计划审批 request_id", out)

        out = handlers["compress"]()
        self.assertEqual(out, "正在压缩上下文……")

        out = handlers["idle"]()
        self.assertEqual(out, "主代理不会进入空闲状态。")


if __name__ == "__main__":
    unittest.main()
