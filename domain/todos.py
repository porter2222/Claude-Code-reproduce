"""Todo list model."""


class TodoManager:
    """Keep a lightweight task list for the lead agent."""

    def __init__(self):
        self.items = []

    def update(self, items: list) -> str:
        """Validate and replace the current todo list."""
        validated, in_progress = [], 0
        status_aliases = {
            "pending": "pending",
            "待办": "pending",
            "待处理": "pending",
            "未开始": "pending",
            "in_progress": "in_progress",
            "in-progress": "in_progress",
            "in progress": "in_progress",
            "进行中": "in_progress",
            "处理中": "in_progress",
            "completed": "completed",
            "complete": "completed",
            "完成": "completed",
            "已完成": "completed",
        }
        for index, item in enumerate(items):
            content = str(item.get("content", "")).strip()
            status_raw = item.get("status", "pending")
            status_text = str(status_raw).strip()
            status = status_aliases.get(status_text.lower()) or status_aliases.get(status_text)
            active_form_raw = item.get("activeForm", "")
            if not content and isinstance(active_form_raw, str) and active_form_raw.strip():
                content = active_form_raw.strip()
            if not content:
                raise ValueError(f"Item {index}: content required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {index}: invalid status '{status_text}'")
            if isinstance(active_form_raw, str) and active_form_raw.strip():
                active_form = active_form_raw.strip()
            else:
                # Be forgiving with model-generated todos and fall back to the content text.
                active_form = content
            if status == "in_progress":
                in_progress += 1
            validated.append({"content": content, "status": status, "activeForm": active_form})
        if len(validated) > 20:
            raise ValueError("Max 20 todos")
        if in_progress > 1:
            raise ValueError("Only one in_progress allowed")
        self.items = validated
        return self.render()

    def render(self) -> str:
        """Render the todo list for terminal output."""
        if not self.items:
            return "No todos."
        lines = []
        for item in self.items:
            marker = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]"}.get(item["status"], "[?]")
            suffix = f" <- {item['activeForm']}" if item["status"] == "in_progress" else ""
            lines.append(f"{marker} {item['content']}{suffix}")
        done = sum(1 for item in self.items if item["status"] == "completed")
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)

    def has_open_items(self) -> bool:
        """Return whether any todo remains incomplete."""
        return any(item.get("status") != "completed" for item in self.items)
