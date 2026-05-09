"""高风险运行时操作的人工审批队列。"""

from __future__ import annotations

import json
import uuid


class ApprovalManager:
    """存储高风险动作的待审批请求。"""

    def __init__(self):
        self.requests: dict[str, dict] = {}

    def create(self, kind: str, payload: dict, reason: str) -> str:
        request_id = str(uuid.uuid4())[:8]
        self.requests[request_id] = {
            "id": request_id,
            "kind": kind,
            "payload": payload,
            "reason": reason,
            "status": "pending",
        }
        return (
            f"检测到需要人工确认的操作 [{request_id}]，类型：{kind}，原因：{reason}\n"
            f"如果处于交互式审批流程，请直接回答 yes 或 no；也可以使用 /approve {request_id} 或 /reject {request_id}。"
        )

    def get(self, request_id: str) -> dict | None:
        return self.requests.get(request_id)

    def approve(self, request_id: str) -> dict | None:
        req = self.requests.get(request_id)
        if not req:
            return None
        req["status"] = "approved"
        return req

    def reject(self, request_id: str, note: str = "") -> dict | None:
        req = self.requests.get(request_id)
        if not req:
            return None
        req["status"] = "rejected"
        if note:
            req["note"] = note
        return req

    def mark_executed(self, request_id: str, result: str) -> dict | None:
        req = self.requests.get(request_id)
        if not req:
            return None
        req["status"] = "executed"
        req["result"] = result
        return req

    def list_pending(self) -> str:
        pending = [item for item in self.requests.values() if item["status"] == "pending"]
        if not pending:
            return "当前没有待审批请求。"
        return json.dumps(pending, indent=2, ensure_ascii=False)

    def next_pending(self) -> dict | None:
        for item in self.requests.values():
            if item["status"] == "pending":
                return item
        return None
