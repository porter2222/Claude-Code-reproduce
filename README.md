# Claude-Code-Reproduce

一个面向编码任务的多代理 Agent Runtime 项目。  
该项目参考 Claude Code 一类 coding agent 的运行思路，围绕主 Agent Loop、工具运行时、任务编排、代理协作、上下文压缩与 Session Memory，构建了一套可持续执行的智能体运行框架。

## Features

- `Agent Loop`：支持用户输入、模型推理、工具调用、结果回写和多轮持续执行
- `Tool Runtime`：统一注册和调度工具，支持只读工具缓存和写操作失效控制
- `Multi-Agent Collaboration`：支持子代理、持久化队友、消息总线、任务认领和协议控制
- `Context Engineering`：支持工具结果裁剪、microcompact、context collapse 和 Session Memory
- `Background Jobs`：支持耗时 shell 命令异步执行，并将结果回流主对话
- `Human-in-the-Loop`：对高风险命令支持人工审批

## Project Structure

```text
final_version_app/
├─ application/   # 主循环、工具装配、压缩、session memory、team runtime
├─ domain/        # todo、task、message bus、background、skills、approvals
├─ infra/         # LLM 网关、shell 封装、workspace 访问
├─ tests/         # 运行时与记忆相关测试
├─ runtime.py     # 项目启动入口
├─ config.py      # 运行时配置
├─ requirements.txt
└─ README.md
```

## Architecture Overview

### 1. Agent Loop

`application/agent_loop.py`

主循环负责驱动整个智能体回合：

1. 注入消息历史和 system prompt
2. 调用 LLM 决策是否需要使用工具
3. 执行工具并把结果写回 `ToolMessage`
4. 触发缓存、压缩、记忆提取和后台通知处理
5. 重复循环直到当前回合结束

### 2. Tool Runtime

`application/tooling.py`

统一管理工具注册与执行，包含：

- 文件读写工具
- Shell 执行工具
- Todo 与任务板工具
- 队友协作工具
- 协议类工具
- 工具缓存与失效控制

### 3. Multi-Agent Collaboration

`application/team_runtime.py`

支持：

- 主代理拉起队友
- 队友独立线程运行
- 消息收发与广播
- `idle / working / shutdown` 生命周期
- 任务认领与协作汇报

### 4. Session Memory

`application/session_memory.py`

负责：

- 结构化长期记忆维护
- 后台异步提取
- 压缩边界控制
- recent raw messages 保留
- 与 compact 流程结合

## Requirements

- Python `3.11` 或 `3.12`
- Windows 下推荐使用 `PowerShell`
- 一组可用的大模型 API Key

## Installation

### 1. Clone repository

```bash
git clone https://github.com/porter2222/Claude-Code-reproduce.git
cd Claude-Code-reproduce
```

如果你的实际项目目录名仍然是 `final_version_app`，也可以直接进入该目录运行。

### 2. Create virtual environment

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

macOS / Linux:

```bash
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

## Configuration

在项目根目录创建 `.env` 文件，并至少配置一组可用模型：

```env
MODEL_ID="deepseek-chat"
DEEPSEEK_API_KEY="your_api_key"
```

也可以根据需要切换到其他模型：

### DeepSeek

```env
MODEL_ID="deepseek-chat"
DEEPSEEK_API_KEY="your_api_key"
```

### Qwen

```env
MODEL_ID="qwen-max"
DASHSCOPE_API_KEY="your_api_key"
```

### OpenAI

```env
MODEL_ID="gpt-4o"
OPENAI_API_KEY="your_api_key"
```

## Quick Start

### Start the runtime

推荐首次运行时直接使用标准入口：

```bash
python runtime.py
```

如果你本机已经配置过快捷命令 `cjy`，也可以使用：

```bash
cjy
```

### First interaction

启动成功后，你会看到类似提示：

```text
请输入你的问题：
```

可以先输入一条最简单的消息验证系统是否正常工作：

```text
你是谁
```

如果系统能够正常回复自己的身份、工作目录或能力说明，就说明项目已经启动成功。

## Built-in REPL Commands

在 REPL 中支持以下内置命令：

- `/compact`：手动触发上下文压缩
- `/tasks`：查看任务板
- `/team`：查看当前队友
- `/inbox`：查看主代理收件箱
- `/approvals`：查看待审批请求

## Example Workflow

一个典型交互流程如下：

1. 用户输入一个编码任务
2. Agent 进入主循环
3. 模型决定是否调用工具
4. 工具返回结果并写回上下文
5. 必要时创建 Todo、任务、子代理或队友代理
6. 当上下文增长时，触发压缩和 Session Memory
7. 最终输出结果或继续下一轮执行

## Common Issues

### 1. Missing API key

如果启动时报 API Key 缺失，请检查：

- `.env` 是否存在
- `MODEL_ID` 是否和对应 key 匹配

例如：

- `MODEL_ID=deepseek-chat` 需要 `DEEPSEEK_API_KEY`
- `MODEL_ID=qwen...` 需要 `DASHSCOPE_API_KEY`
- `MODEL_ID=gpt-...` 需要 `OPENAI_API_KEY`

### 2. Runtime starts but does not respond

优先检查：

1. 模型 key 是否可用
2. 当前网络是否可访问对应模型服务
3. Python 环境是否安装了正确依赖

### 3. `cjy` 和 `python runtime.py` 的区别

- `python runtime.py`：标准入口，适合首次启动和排查问题
- `cjy`：本机额外配置的快捷命令，适合日常使用

如果是首次调试，优先建议使用：

```bash
python runtime.py
```

## Highlights

这个项目适合展示以下能力：

- Agent Runtime 分层设计
- 工具系统与调度机制
- 多代理协作
- 上下文工程与记忆机制
- 人工审批与安全控制
- 后台任务与工程化可靠性

## Repository Notes

当前仓库默认不提交以下运行态或敏感内容：

- `.env`
- `.memory/`
- `.tasks/`
- `.team/`
- `.transcripts/`

这样做是为了避免泄露密钥、提交本地状态文件，并保持公开仓库更干净。
