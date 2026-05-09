# Claude-Code-Reproduce

一个面向编码任务的多代理 Agent Runtime 系统。  
该项目参考 Claude Code 一类 coding agent 的运行机制，从主 Agent 循环、工具运行时、任务编排、代理协作到长上下文记忆管理，独立复现并工程化实现了一套可持续执行、可协同处理、可压缩上下文的智能体运行框架。

---

## 项目定位

这个项目不是一个简单的“聊天机器人壳”，而是一套 **Agent Runtime 基础设施**。  
它要解决的问题是：

- 如何让大模型从“单轮回答问题”升级为“围绕任务持续工作”
- 如何让模型具备真实环境操作能力，例如读写文件、执行命令、管理任务
- 如何把单 Agent 扩展为可分工的多智能体系统
- 如何在长任务、多工具、多轮对话下维持上下文稳定性

如果用一句话概括：

> 这个项目实现的是一套面向编码任务的多代理 Agent 运行时底座，而不是单点功能型 AI Demo。

---

## 简历对应项目描述

参考 Claude Code coding agent 的运行机制，独立复现并工程化实现一套面向编码任务的多代理 Agent Runtime 系统，围绕主 Agent 循环、工具运行时、任务编排、代理协作与长上下文记忆管理，构建具备持续执行、协同处理与上下文压缩能力的智能体运行框架。

---

## 核心亮点

### 1. 主 Agent Loop

- 支持用户输入、模型推理、工具调用、结果回写和多轮持续执行
- 不再停留在单次 prompt-response，而是形成 `推理 -> 调工具 -> 回写结果 -> 再推理` 的执行闭环
- 通过循环次数限制、任务提醒和消息结构化，增强运行稳定性

### 2. Tool Runtime

- 统一管理工具注册、工具 schema 暴露、handler 调度和结果封装
- 支持只读工具缓存，例如 `read_file`、`grep_content`、`glob_files`
- 支持写操作后的缓存失效控制，避免模型基于旧文件状态继续推理
- 将文件工具、shell、任务工具、协作工具、协议工具全部收敛到同一套运行时

### 3. 多代理协作

- 支持子代理拉起与持久化队友代理
- 通过消息总线实现消息收发、广播通信和异步协作
- 支持任务认领、队友生命周期管理和协议化控制
- 让系统从单 Agent 扩展为具备分工协作能力的多智能体系统

### 4. 上下文工程与 Session Memory

- 支持单条工具结果裁剪
- 支持 microcompact，对旧工具轨迹做低成本摘要化处理
- 支持 context collapse，在高上下文压力下折叠更早历史
- 支持 Session Memory，将长期有价值信息抽取为结构化记忆
- 通过 recent raw messages + structured memory 的组合，兼顾长期连续性与近期细节保真

### 5. 工程可靠性

- 支持后台任务执行，避免耗时命令阻塞主对话
- 支持工作区路径访问约束
- 支持高风险命令人工审批
- 支持跨平台 Shell 适配
- 补充了运行时行为、工具覆盖与记忆模块测试

---

## 技术设计概览

### 分层设计

项目核心采用类似 `runtime / core / domain / infra / tools` 的思路做分层，在当前实现中主要映射为：

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

### 各层职责

#### application

负责流程编排与运行时控制，包括：

- `agent_loop.py`
- `tooling.py`
- `repl.py`
- `team_runtime.py`
- `compression.py`
- `session_memory.py`

#### domain

负责核心状态与业务对象，包括：

- `TodoManager`
- `TaskManager`
- `MessageBus`
- `BackgroundManager`
- `SkillLoader`
- `ApprovalManager`

#### infra

负责环境适配和基础设施封装，包括：

- LLM 调用网关
- Shell 执行
- 工作区路径访问

---

## 运行机制

### 主执行链路

一个典型回合的执行过程如下：

1. 用户输入任务
2. 系统构造消息历史与 system prompt
3. Agent Loop 调用 LLM 判断下一步动作
4. 如果需要工具，则进入 Tool Runtime 执行
5. 工具结果被写回 `ToolMessage`
6. 根据上下文大小触发缓存、压缩和记忆提取
7. 如果需要，可拉起子代理或持久化队友代理协作
8. 重复循环直到本轮任务结束

### 为什么不是一次性回答

因为编码任务天然是：

- 多步的
- 依赖真实环境反馈的
- 需要状态持续推进的

所以系统核心不是“生成答案”，而是“推进任务”。

---

## Tool Runtime 设计

Tool Runtime 是这个项目的核心之一，主要解决：

- 工具如何统一注册
- 工具如何暴露给模型
- 工具如何统一执行
- 工具结果如何统一回写
- 工具缓存如何做
- 写操作后如何做缓存失效

### 只读工具缓存

当前默认缓存的只读工具包括：

- `glob_files`
- `grep_content`
- `read_file`
- `read_file_segment`

缓存键由：

- 工具名
- 参数 JSON 序列化结果

共同组成。

### 写操作失效控制

发生以下工具调用后，会清空只读缓存：

- `write_file`
- `edit_file`

这样做是为了避免“模型刚改完文件，却继续读旧缓存”的状态不一致问题。

---

## 多代理协作机制

项目中有两类协作单元：

### 1. 子代理

- 通过 `task` 工具拉起
- 更适合一次性探索或局部执行
- 生命周期较短

### 2. 持久化队友代理

- 通过 `spawn_teammate` 拉起
- 独立线程运行
- 可收消息、发消息、进入 idle、被重新唤醒
- 更适合持续化协作

### 消息与协议

通过 `MessageBus` 进行：

- 点对点消息
- 广播
- 主代理收件箱读取

协议控制则进一步提供：

- `shutdown_request`
- `plan_approval_response`

这使得多代理不仅能“沟通”，还能“受控协作”。

---

## 上下文工程与记忆机制

这个项目的一大重点是上下文治理，而不是单纯依赖模型大窗口。

### 分层压缩策略

#### 第一层：工具结果预算裁剪

- 限制单条工具结果过长
- 保留头尾信息

#### 第二层：microcompact

- 最近工具结果保真
- 更早工具结果转为摘要

#### 第三层：session-memory-first compact

- 优先使用已维护的结构化 Session Memory 替代更早历史

#### 第四层：context collapse

- 在更高 token 压力下，把更早历史折叠成结构化概览

#### 第五层：legacy fallback compact

- 如果 Session Memory 路径不可用，则回退到传统 summary 压缩

### Session Memory 机制

Session Memory 不是简单摘要，而是结构化长期记忆。  
它会把重要信息按固定 section 写入 `.memory/session_memory.md`，例如：

- Current State
- Task specification
- Files and Functions
- Errors & Corrections
- Key results

这使得系统能在长任务中同时保留：

- 近期原始上下文
- 长期稳定事实

---

## 项目经历对应说明

### 1. 完成 Agent Runtime 的分层设计与核心实现

- 按运行时职责拆分应用层、领域层和基础设施层
- 明确主循环、工具、任务、消息、后台、审批、记忆各模块边界
- 让系统具备可扩展、可维护的工程结构

### 2. 实现主 Agent Loop 与 Tool Runtime

- 打通 `LLM -> tool call -> handler -> ToolMessage -> LLM` 的闭环
- 实现统一调度、结果回写和缓存控制
- 通过 Todo、任务板和 Skill Loader 支撑多步任务规划

### 3. 构建多代理协作机制

- 主代理负责统筹
- 队友代理负责分工执行
- 通过消息总线和任务认领形成协作网络

### 4. 围绕长上下文实现分层压缩与 Session Memory

- 从工具结果裁剪到历史折叠，再到结构化记忆沉淀
- 提升系统在复杂多轮任务中的上下文保持能力

### 5. 增强工程可靠性与可验证性

- 增加后台任务执行
- 增加强制审批与安全控制
- 增加 Shell / workspace 约束
- 增加测试验证关键行为

---

## Quick Start

下面这套流程以“第一次在新机器上跑起来”为目标，按顺序执行即可。

### 1. 准备环境

- Python `3.11` 或 `3.12`
- Windows 下建议使用 `PowerShell`

确认 Python 可用：

```bash
python --version
```

### 2. 进入项目目录

```bash
cd D:\大模型第三期课件\final_version_app
```

### 3. 创建虚拟环境并安装依赖

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 4. 配置环境变量

在项目根目录创建 `.env` 文件，至少配置一组可用模型：

```env
MODEL_ID="deepseek-chat"
DEEPSEEK_API_KEY="your_api_key"
```

你也可以切换为：

- Qwen：`DASHSCOPE_API_KEY`
- OpenAI：`OPENAI_API_KEY`

### 5. 启动项目

推荐使用标准入口：

```bash
python runtime.py
```

如果你本机已经配置过全局快捷命令，也可以使用：

```bash
cjy
```

### 6. 验证是否启动成功

正常情况下，你会看到：

```text
请输入你的问题：
```

然后输入：

```text
你是谁
```

如果系统能正常回应自身身份与能力说明，就说明基本运行成功。

---

## 常用 REPL 命令

- `/compact`：手动触发上下文压缩
- `/tasks`：查看任务板
- `/team`：查看当前队友
- `/inbox`：查看主代理收件箱
- `/approvals`：查看待审批请求

---

## 测试

项目包含部分关键行为测试，例如：

- 运行时行为测试
- 工具覆盖测试
- Session Memory 测试

可按需运行：

```bash
pytest tests
```

---

## 适合面试展示的点

这个仓库适合重点展示以下能力：

- Agent Runtime 分层设计
- Tool Runtime 与缓存失效机制
- 多代理协作与消息协议
- 长上下文压缩与 Session Memory
- 后台任务与 Human-in-the-Loop
- 工程化实现与测试验证

---

## Repository Notes

当前仓库默认不提交以下运行态或敏感内容：

- `.env`
- `.memory/`
- `.tasks/`
- `.team/`
- `.transcripts/`

这样做是为了避免泄露密钥、提交本地状态文件，并保持仓库适合公开展示与协作。
