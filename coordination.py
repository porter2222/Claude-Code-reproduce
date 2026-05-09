"""兼容导出层。

旧代码如果仍从 `coordination` 导入协调相关服务，
这里继续提供同名导出，避免重构后大量导入路径失效。
"""

from final_version_app.domain.background import BackgroundManager  # noqa: F401
from final_version_app.domain.messaging import MessageBus  # noqa: F401
from final_version_app.domain.tasks import TaskManager  # noqa: F401
