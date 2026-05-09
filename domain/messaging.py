"""收件箱与广播消息服务。"""

import json
import time

from final_version_app.config import INBOX_DIR
from final_version_app.infra.workspace import read_text_auto


class MessageBus:
    """基于文件的轻量消息总线。

    每个参与者一个 jsonl 收件箱文件，适合当前本地单机 agent 架构。
    """
    def __init__(self):
        INBOX_DIR.mkdir(parents=True, exist_ok=True)

    def send(
        self,
        sender: str,
        to: str,
        content: str,
        msg_type: str = "message",
        extra: dict = None,
    ) -> str:
        """向指定成员写入一条消息。"""
        msg = {"type": msg_type, "from": sender, "content": content, "timestamp": time.time()}
        if extra:
            msg.update(extra)
        with open(INBOX_DIR / f"{to}.jsonl", "a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(msg, ensure_ascii=False) + "\n")
        return f"Sent {msg_type} to {to}"

    def read_inbox(self, name: str) -> list:
        """读取并清空某个成员的收件箱。"""
        path = INBOX_DIR / f"{name}.jsonl"
        if not path.exists():
            return []
        messages = [json.loads(line) for line in read_text_auto(path).strip().splitlines() if line]
        path.write_text("", encoding="utf-8")
        return messages

    def broadcast(self, sender: str, content: str, names: list) -> str:
        """向团队中的其他成员群发消息。"""
        count = 0
        for name in names:
            if name != sender:
                self.send(sender, name, content, "broadcast")
                count += 1
        return f"Broadcast to {count} teammates"
