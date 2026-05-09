"""Application service container."""

from __future__ import annotations

from dataclasses import dataclass

from final_version_app.application.session_memory import SessionMemoryManager
from final_version_app.config import SKILLS_DIR
from final_version_app.domain.approvals import ApprovalManager
from final_version_app.domain.background import BackgroundManager
from final_version_app.domain.messaging import MessageBus
from final_version_app.domain.skills import SkillLoader
from final_version_app.domain.tasks import TaskManager
from final_version_app.domain.todos import TodoManager


@dataclass
class AppServices:
    todo: TodoManager
    skills: SkillLoader
    task_mgr: TaskManager
    bg: BackgroundManager
    bus: MessageBus
    approvals: ApprovalManager
    session_memory: SessionMemoryManager


def build_services() -> AppServices:
    return AppServices(
        todo=TodoManager(),
        skills=SkillLoader(SKILLS_DIR),
        task_mgr=TaskManager(),
        bg=BackgroundManager(),
        bus=MessageBus(),
        approvals=ApprovalManager(),
        session_memory=SessionMemoryManager(),
    )
