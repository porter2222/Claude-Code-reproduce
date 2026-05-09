"""后台命令执行服务。"""

import threading
import uuid
from queue import Queue

from final_version_app.infra.shell import inspect_command_policy, run_shell


class BackgroundManager:
    """负责把耗时命令放到后台线程执行，并提供结果通知。"""

    def __init__(self):
        self.tasks = {}
        self.notifications = Queue()

    def run(self, command: str, timeout: int = 120) -> str:
        """启动一个后台任务。"""
        action, reason = inspect_command_policy(command)
        if action == "block":
            return f"错误：危险命令已被拦截（{reason}）。"
        if action == "approval":
            return f"需要人工确认：{reason}。"
        task_id = str(uuid.uuid4())[:8]
        self.tasks[task_id] = {"status": "running", "command": command, "result": None}
        threading.Thread(target=self._exec, args=(task_id, command, timeout), daemon=True).start()
        return f"后台任务 {task_id} 已启动：{command[:80]}"

    def run_approved(self, command: str, timeout: int = 120) -> str:
        """在人工批准后启动一个后台任务。"""
        task_id = str(uuid.uuid4())[:8]
        self.tasks[task_id] = {"status": "running", "command": command, "result": None}
        threading.Thread(target=self._exec, args=(task_id, command, timeout), daemon=True).start()
        return f"后台任务 {task_id} 已在批准后启动：{command[:80]}"

    def _exec(self, task_id: str, command: str, timeout: int):
        """后台线程实际执行逻辑。"""
        try:
            stdout, stderr = run_shell(command, timeout=timeout)
            output = (stdout + stderr).strip()[:50000]
            self.tasks[task_id].update({"status": "completed", "result": output or "（无输出）"})
        except Exception as exc:
            self.tasks[task_id].update({"status": "error", "result": str(exc)})
        self.notifications.put(
            {
                "task_id": task_id,
                "status": self.tasks[task_id]["status"],
                "result": self.tasks[task_id]["result"][:500],
            }
        )

    def check(self, task_id: str = None) -> str:
        """查询指定后台任务，或列出全部后台任务。"""
        if task_id:
            task = self.tasks.get(task_id)
            return f"[{task['status']}] {task.get('result', '（运行中）')}" if task else f"未知任务：{task_id}"
        return "\n".join(f"{key}: [{value['status']}] {value['command'][:60]}" for key, value in self.tasks.items()) or "当前没有后台任务。"

    def drain(self) -> list:
        """一次性取出所有待消费的后台通知。"""
        notifications = []
        while not self.notifications.empty():
            notifications.append(self.notifications.get_nowait())
        return notifications
