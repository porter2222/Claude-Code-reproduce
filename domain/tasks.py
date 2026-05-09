"""持久任务板服务。"""

import json

from final_version_app.config import TASKS_DIR
from final_version_app.infra.workspace import read_text_auto


class TaskManager:
    """把任务状态持久化到 `.tasks/`，用于多轮、多步骤任务跟踪。"""
    def __init__(self):
        TASKS_DIR.mkdir(exist_ok=True)

    def _next_id(self) -> int:
        """分配下一个任务编号。"""
        ids = [int(path.stem.split("_")[1]) for path in TASKS_DIR.glob("task_*.json")]
        return max(ids, default=0) + 1

    def _load(self, task_id: int) -> dict:
        """读取单个任务文件。"""
        path = TASKS_DIR / f"task_{task_id}.json"
        if not path.exists():
            raise ValueError(f"Task {task_id} not found")
        return json.loads(read_text_auto(path))

    def _save(self, task: dict):
        """把任务对象写回磁盘。"""
        (TASKS_DIR / f"task_{task['id']}.json").write_text(
            json.dumps(task, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def create(self, subject: str, description: str = "") -> str:
        """创建任务。"""
        task = {
            "id": self._next_id(),
            "subject": subject,
            "description": description,
            "status": "pending",
            "owner": None,
            "blockedBy": [],
            "blocks": [],
        }
        self._save(task)
        return json.dumps(task, indent=2)

    def get(self, task_id: int) -> str:
        """获取任务详情。"""
        return json.dumps(self._load(task_id), indent=2)

    def update(
        self,
        task_id: int,
        status: str = None,
        add_blocked_by: list = None,
        add_blocks: list = None,
    ) -> str:
        """更新任务状态以及依赖关系。"""
        task = self._load(task_id)
        if status:
            task["status"] = status
            if status == "completed":
                for path in TASKS_DIR.glob("task_*.json"):
                    sibling = json.loads(read_text_auto(path))
                    if task_id in sibling.get("blockedBy", []):
                        sibling["blockedBy"].remove(task_id)
                        self._save(sibling)
            if status == "deleted":
                (TASKS_DIR / f"task_{task_id}.json").unlink(missing_ok=True)
                return f"Task {task_id} deleted"
        if add_blocked_by:
            task["blockedBy"] = list(set(task["blockedBy"] + add_blocked_by))
        if add_blocks:
            task["blocks"] = list(set(task["blocks"] + add_blocks))
        self._save(task)
        return json.dumps(task, indent=2)

    def list_all(self) -> str:
        """列出当前任务板。"""
        tasks = [json.loads(read_text_auto(path)) for path in sorted(TASKS_DIR.glob("task_*.json"))]
        if not tasks:
            return "No tasks."
        lines = []
        for task in tasks:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(task["status"], "[?]")
            owner = f" @{task['owner']}" if task.get("owner") else ""
            blocked = f" (blocked by: {task['blockedBy']})" if task.get("blockedBy") else ""
            lines.append(f"{marker} #{task['id']}: {task['subject']}{owner}{blocked}")
        return "\n".join(lines)

    def claim(self, task_id: int, owner: str) -> str:
        """为某个执行者认领任务。"""
        task = self._load(task_id)
        task["owner"] = owner
        task["status"] = "in_progress"
        self._save(task)
        return f"Claimed task #{task_id} for {owner}"
