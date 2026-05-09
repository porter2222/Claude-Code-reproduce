"""final_version_app 运行时入口。"""

from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    package_root = Path(__file__).resolve().parent
    parent_dir = str(package_root.parent)
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

from final_version_app.application.agent_loop import agent_loop  # noqa: F401
from final_version_app.application.repl import build_runtime, main  # noqa: F401
from final_version_app.application.team_runtime import TeammateManager  # noqa: F401
from final_version_app.application.tooling import ToolRuntime, build_tool_runtime  # noqa: F401


if __name__ == "__main__":
    main()
