"""Skill 加载服务。"""

import re
from pathlib import Path

from final_version_app.infra.workspace import read_text_auto


class SkillLoader:
    """扫描 `skills/` 目录，把 `SKILL.md` 组织成可加载知识块。"""
    def __init__(self, skills_dir: Path):
        self.skills = {}
        if skills_dir.exists():
            for skill_file in sorted(skills_dir.rglob("SKILL.md")):
                text = read_text_auto(skill_file)
                match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
                meta, body = {}, text
                if match:
                    for line in match.group(1).strip().splitlines():
                        if ":" in line:
                            key, value = line.split(":", 1)
                            meta[key.strip()] = value.strip()
                    body = match.group(2).strip()
                name = meta.get("name", skill_file.parent.name)
                self.skills[name] = {"meta": meta, "body": body}

    def descriptions(self) -> str:
        """返回给 system prompt 使用的技能摘要列表。"""
        if not self.skills:
            return "(no skills)"
        return "\n".join(f"  - {name}: {skill['meta'].get('description', '-')}" for name, skill in self.skills.items())

    def load(self, name: str) -> str:
        """按名称加载完整 skill 正文。"""
        skill = self.skills.get(name)
        if not skill:
            return f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"
        return f"<skill name=\"{name}\">\n{skill['body']}\n</skill>"
