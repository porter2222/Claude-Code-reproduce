# final_version_app

一个面向编码任务的多代理 Agent Runtime 项目。该项目参考 Claude Code 一类 coding agent 的运行思路，围绕主 Agent Loop、工具运行时、任务编排、代理协作、上下文压缩与 Session Memory，构建了一套可持续执行的智能体运行框架。

## 项目特点

- 主 Agent Loop：支持用户输入、模型推理、工具调用、结果回写和多轮持续执行
- Tool Runtime：统一注册和调度工具，支持只读工具缓存和写操作后的缓存失效
- 多代理协作：支持子代理、持久化队友代理、消息总线、任务认领和协议化控制
- 上下文工程：支持工具结果裁剪、microcompact、context collapse 和 Session Memory
- 后台任务：支持耗时 shell 命令异步执行，并回流结果到主对话
- 人工审批：对高风险 shell/后台命令支持 yes/no 批准流程

## 目录结构

```text
final_version_app/
├─ application/   # 主循环、工具装配、压缩、session memory、team runtime
├─ domain/        # todo、task、message bus、background、skills、approvals
├─ infra/         # LLM 网关、shell 封装、workspace 访问
├─ tests/         # 运行时和记忆相关测试
├─ runtime.py     # 项目启动入口
├─ config.py      # 运行时配置
└─ README.md
```

## 核心模块

### 1. 主 Agent Loop

`application/agent_loop.py`

负责驱动整个智能体回合：

1. 注入消息历史和 system prompt
2. 调用 LLM 决策是否需要使用工具
3. 执行工具并把结果写回 `ToolMessage`
4. 触发缓存、压缩、记忆提取和后台通知处理
5. 重复循环直到当前回合结束

### 2. Tool Runtime

`application/tooling.py`

统一管理工具注册与执行，包含：

- 文件读写工具
- shell 执行工具
- Todo 与任务板工具
- 队友协作工具
- 协议类工具
- 工具缓存与失效控制

### 3. 多代理协作

`application/team_runtime.py`

支持：

- 主代理拉起队友
- 队友独立线程运行
- 消息收发与广播
- idle / working / shutdown 生命周期
- 任务认领与协作汇报

### 4. Session Memory

`application/session_memory.py`

负责：

- 结构化长期记忆维护
- 后台异步提取
- 压缩边界控制
- recent raw messages 保留
- 和 compact 流程结合

## 运行方式

### 1. 安装依赖

建议使用 Python 3.11 或 3.12，并安装项目依赖：

```bash
pip install langchain-core langchain-openai python-dotenv
```

如果你还有额外依赖，请按本地环境补充安装。

### 2. 配置环境变量

在项目根目录创建 `.env`，至少配置一种模型密钥：

```env
MODEL_ID="deepseek-chat"
DEEPSEEK_API_KEY="your_api_key"
```

如果使用 Qwen / OpenAI，也可以配置对应 key：

- `DASHSCOPE_API_KEY`
- `OPENAI_API_KEY`

### 3. 启动项目

在项目根目录执行：

```bash
python runtime.py
```

或者如果你本地已经配置了 `cjy` 启动命令，也可以直接运行：

```bash
cjy
```

## 常用交互命令

在 REPL 中支持一些内置命令：

- `/compact`：手动触发上下文压缩
- `/tasks`：查看任务板
- `/team`：查看当前队友
- `/inbox`：查看主代理收件箱
- `/approvals`：查看待审批请求

## 适合展示的亮点

这个项目比较适合用于展示以下能力：

- Agent Runtime 分层设计
- 工具系统与调度机制
- 多代理协作
- 上下文工程与记忆机制
- 人工审批与安全控制
- 后台任务与工程化可靠性

## 说明

当前仓库默认不提交以下运行态或敏感内容：

- `.env`
- `.memory/`
- `.tasks/`
- `.team/`
- `.transcripts/`

这样做是为了避免泄露密钥、提交本地状态文件，保持仓库更干净，也更适合公开展示。
