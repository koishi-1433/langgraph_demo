# 🌍 LangGraph 旅游规划助手（langgraph_demo）

> 基于 **LangGraph + MCP stdio** 的多智能体旅游规划助手。
> 用户输入自然语言，系统通过 LLM 意图识别路由到对应 Agent，Agent 通过 MCP 调用高德地图、Tavily 搜索、12306 火车查询等外部工具，最终以自然语言返回完整出行方案。

---

## 📖 目录

- [核心能力](#核心能力)
- [架构总览](#架构总览)
- [技术栈](#技术栈)
- [目录结构](#目录结构)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [系统设计详解](#系统设计详解)
- [测试](#测试)
- [相关文档](#相关文档)

---

## ✨ 核心能力

| 能力 | 说明 | 触发示例 |
|------|------|----------|
| 💬 普通聊天 | 无工具纯 LLM 闲聊 | “你好”“介绍一下自己” |
| 🗺️ 路线规划 | 驾车 / 公交 / 火车路线，跨城自动匹配火车并输出三段式方案 | “北京到上海怎么走” |
| 🔍 旅游搜索 | Tavily 多维度搜索并生成结构化攻略（景点/美食/住宿/贴士） | “西安有什么好玩的” |
| 🌍 综合规划 | 并发执行「攻略搜索 + 路线规划」，由整合 Agent 输出完整旅行方案 | “去成都玩三天怎么安排” |
| 📍 GPS 定位 | 浏览器定位 → 逆地理编码 → 自动作为出行起点 | 打开页面自动触发 |

支持**多轮对话**（会话历史持久化）与 **SSE 流式输出**。

---

## 🏗️ 架构总览

```
用户输入 + GPS坐标/地址
        │
        ▼
┌─────────────────────────────────┐
│ intent_recognition（IntentAgent）│  ← 输出 {intent, confidence}
└───────────────┬─────────────────┘
                │ conditional_edges
                ▼
   ┌────────────┼──────────────┬──────────────┐
   ▼            ▼              ▼              ▼
 chat        search          route          travel
(无工具)    (tavily)      (高德+12306)     (并发编排)
   │            │              │              │
   │            │              │   search ∥ route（asyncio.gather）
   │            │              │        │
   │            │              │   integrator 整合
   │            │              │              │
   └────────────┴──────────────┴──────────────┘
                        │
                        ▼
                 final_output（清洗后返回 + 写入会话历史）
```

**核心设计原则**：

1. **工具统一走 MCP stdio** —— Tavily / 高德 / 12306 全部注册在 `mcp_server/mcp_server_stdio.py` 一个入口，配置集中在 `config/settings.py`，Agent 不感知具体实现。
2. **编排只在 graph 层** —— 路由和分支唯一存在于 `workflow/graph.py`，Agent 只负责“用工具”。
3. **Agent 瘦化** —— 每个 Agent = LLM + system prompt + 被授权的工具子集，统一继承 `BaseAgent`（`create_react_agent` 封装）。
4. **统一响应协议** —— `AgentResponse(success, content, error, metadata)` 贯穿全链路。
5. **快速失败** —— 图初始化时校验各 Agent 所需 MCP 工具是否齐全，缺失即抛错，避免静默降级。

---

## 🧰 技术栈

| 类别 | 技术 |
|------|------|
| 编排框架 | LangGraph（`StateGraph` + `create_react_agent`） |
| LLM 接口 | LangChain `ChatOpenAI`（OpenAI 兼容接口） |
| 工具协议 | MCP stdio（FastMCP + `langchain_mcp_adapters.MultiServerMCPClient`） |
| 搜索 | Tavily（`langchain_tavily`） |
| 地图 | 高德 REST API（地理编码 / 逆地理 / 驾车 / 公交 / POI） |
| 火车数据 | 12306 官方接口（自封装 `Py12306Client`） |
| Web 服务 | FastAPI + SSE 流式输出 |
| 异步 HTTP | aiohttp |
| 配置 | pydantic-settings + python-dotenv |
| 日志 | logging + colorlog（控制台彩色 + 文件双输出） |
| 测试 | pytest |

---

## 📁 目录结构

```
langgraph_demo/
├── app.py                     # ★ Web 统一入口（FastAPI + SSE）
├── index.html                 # 前端单页聊天 UI（原生 JS，无构建）
├── requirements.txt           # Python 依赖清单
├── __init__.py                # 包标记
│
├── agents/                    # ── Agent 层（LLM + prompt + 工具子集）
│   ├── base_agent.py          #   BaseAgent：create_react_agent 封装 + 统一 run()
│   ├── intent_agent.py        #   IntentAgent：prompt 约束 JSON 输出 + 正则解析意图
│   ├── chat_agent.py          #   ChatAgent：无工具闲聊
│   ├── search_agent.py        #   SearchAgent：仅授权 tavily_web_search
│   ├── route_agent.py         #   RouteAgent：仅授权 4 个地图/火车工具
│   └── integrator_agent.py    #   IntegratorAgent：无工具，整合攻略 + 路线
│
├── workflow/                  # ── 编排层（唯一编排）
│   ├── state.py               #   WorkflowState（TypedDict）+ AgentResponse（统一协议）
│   └── graph.py               #   TravelPlanWorkflow：StateGraph 构建 + 节点 + 工具分发
│
├── config/                    # ── 配置层
│   ├── settings.py            #   pydantic-settings：.env 加载 + MCP 客户端实例
│   ├── logger.py              #   colorlog 双输出（控制台彩色 + 文件）
│   ├── llm_client.py          #   LLMClient + LLMClientFactory（三档：意图/搜索/聊天）
│   ├── .env.example           #   环境配置模板（复制为 .env 后填写密钥）
│   └── .env                   #   密钥（不入库）
│
├── mcp_server/                # ── MCP 工具层（统一暴露外部能力）
│   ├── mcp_server_stdio.py    #   ★ stdio Server：@mcp.tool() 注册 5 个工具
│   ├── tool_tavily.py         #   Tavily 搜索封装
│   └── tool_amap/             #   高德地图 + 12306
│       ├── client.py          #   UnifiedAMapClient：aiohttp 异步封装（缓存+限速+多Key轮询）
│       ├── route_planner.py   #   RoutePlanner：三段式火车路线规划（核心业务逻辑）
│       ├── train_crawler.py   #   Py12306Client（重试+缓存+反爬+城市→车站映射）
│       ├── async_wrappers.py  #   异步封装（供 MCP 注册，共享单例 + atexit 关闭）
│       ├── wrappers.py        #   同步封装（独立脚本调试用）
│       ├── validators.py      #   输入校验
│       ├── utils.py           #   费用估算 / 时长距离格式化
│       └── data/              #   12306 车站编码缓存（json）
│
├── tests/                     # ── pytest 测试（离线单测 + 模拟数据回归）
├── .chat_memory/              # 会话历史持久化（运行时生成）
├── logs/                      # 运行日志（运行时生成）
└── 项目介绍.md / 优化方案.md / 优化报告.md / 路径引用审计.md   # 设计文档
```

---

## 🚀 快速开始

### 1. 环境准备

- Python 3.12+
- 安装依赖：

```bash
pip install -r requirements.txt
```

### 2. 配置密钥

基于模板创建 `config/.env`（`.env` 已在 `.gitignore` 中排除，**切勿提交真实密钥**）：

```bash
# Windows
copy config\.env.example config\.env

# Linux / macOS
cp config/.env.example config/.env
```

然后编辑 `config/.env`，填入各服务密钥（完整变量说明见下方 [配置说明](#配置说明)）：

```bash
NVIDIA_API_KEY=...
NVIDIA_BASE_URL=https://api.nvidia.com/v1
NVIDIA_MODEL_NAME=...
AMAP_API_KEY=...
TAVILY_API_KEY=...
```

### 3. 启动 Web 服务

```bash
# 从项目父目录以包方式启动（推荐）
cd D:\workspace\deepseekharness_space
python -m uvicorn langgraph_demo.app:app --host 0.0.0.0 --port 8000

# 或直接脚本方式运行
python langgraph_demo/app.py
```

### 4. 访问

浏览器打开 **http://localhost:8000/**，即可使用聊天界面。

> 首次请求时工作流会懒加载：自动拉起 MCP stdio 子进程并加载 5 个工具，稍等片刻即可对话。

### 5.（可选）单独调试 MCP 工具

```bash
python langgraph_demo/mcp_server/mcp_server_stdio.py
```

---

## ⚙️ 配置说明

`config/.env` 环境变量一览（完整定义见 `config/settings.py`）：

| 变量 | 必填 | 说明 |
|------|------|------|
| `NVIDIA_API_KEY` | ✅ | **实际生效的 LLM 密钥**（`LLMClient` 的 openai/nvidia 分支均读取此项） |
| `NVIDIA_BASE_URL` | ✅ | OpenAI 兼容接口地址 |
| `NVIDIA_MODEL_NAME` | ✅ | 模型名 |
| `AMAP_API_KEY` | ✅ | 高德地图 Web 服务密钥（地理编码/路线/POI） |
| `TAVILY_API_KEY` | ✅ | Tavily 搜索密钥 |
| `TAVILY_MAX_RESULTS` | 否 | 搜索返回条数，默认 5 |
| `MCP_PYTHON_PATH` | 否 | MCP 子进程解释器路径，默认当前 `sys.executable` |
| `LOG_LEVEL` | 否 | 文件日志级别，默认 INFO |
| `CHATGPT_FREE_API_KEY` / `CHATGPT_FREE_BASE_URL` / `CHATGPT_FREE_MODEL_NAME` | 否 | 预留配置（当前代码未使用） |
| `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL_NAME` | 否 | 预留配置（当前代码未使用） |

---

## 🔧 系统设计详解

### 1. 意图识别与路由（`agents/intent_agent.py` + `workflow/graph.py`）

- `IntentAgent` 用**普通 LLM 调用 + 正则提取 JSON** 的方式输出 `{"intent": "chat|route|search|travel", "confidence": 0.x}`。
- 之所以不用 `with_structured_output`：部分 OpenAI 兼容中转 API 不支持 `response_format` 参数，会导致意图识别全部失败兜底为 chat；改为 prompt 约束 JSON 解析后兼容任意兼容接口。
- 判定优先级：含“旅游/游玩/攻略”+ 目的地 → `travel`；含“A到B/怎么走” → `route`；含“景点/美食” → `search`；其余 → `chat`。
- 解析失败或意图非法时兜底返回 `{chat, 0.5}`，保证任何输入都不中断。

### 2. Agent 层（`agents/`）

所有 Agent 继承 `BaseAgent`，由 `create_react_agent` 生成 ReAct 循环，工具由 graph 依赖注入：

| Agent | 授权工具 | 职责 |
|-------|----------|------|
| `ChatAgent` | 无 | 通用闲聊 |
| `SearchAgent` | `tavily_web_search` | 多轮搜索并输出结构化攻略 |
| `RouteAgent` | `amap_geocode` / `amap_regeocode` / `amap_route_planning` / `py12306_query_train_schedule` | 三段式路线规划 |
| `IntegratorAgent` | 无 | 将攻略 + 路线整合为完整旅行方案 |

### 3. MCP 工具层（`mcp_server/mcp_server_stdio.py`）

FastMCP stdio Server 统一注册 **5 个工具**：

| 工具 | 数据源 | 说明 |
|------|--------|------|
| `tavily_web_search` | Tavily | 网页搜索，返回摘要 + 来源 |
| `amap_geocode` | 高德 | 地址 → 经纬度 |
| `amap_regeocode` | 高德 | 经纬度 → 地址/城市 |
| `amap_route_planning` | 高德 + 12306 | 驾车/公交/火车路线（`mode=auto` 时 >120km 自动选火车） |
| `py12306_query_train_schedule` | 12306 | 车次查询（支持日期/余票/发车时段过滤） |

工具契约（V2）：

- 所有工具返回 `ToolResult`：`text` = 可直接展示的精简摘要，`structuredContent` = 完整结构化数据；
- 每次调用记录耗时 / 成功 / `error_code` 结构化日志（走 stderr，不污染 stdio 协议）；
- 错误统一携带 `hint` 字段，帮助 LLM 在 ReAct 循环中自我纠错。

底层能力（`mcp_server/tool_amap/`）：

- `UnifiedAMapClient`：aiohttp 异步实现，内置 **24h 内存缓存、0.4s 请求限速、多 Key 轮询、QPS 超限自动换 Key 重试**；
- `RoutePlanner`：三段式火车方案（市内→出发站 / 火车段 / 到达站→市内），自动决策 + 费用估算兜底；
- `Py12306Client`：12306 车次/余票/票价查询，带 TTL 缓存、失败负缓存、确定性错误不重试、跨实例限速、反爬伪装，内置全国 300+ 城市→车站映射表。

### 4. 工作流编排（`workflow/graph.py`）

- `TravelPlanWorkflow.__aenter__`：通过 `settings.MCP_CLIENT`（`MultiServerMCPClient`，stdio 子进程）一次性 `get_tools()` → 按工具名分发注入各 Agent → `_build_graph()` 编译 `StateGraph(WorkflowState)`。
- 节点：`intent_recognition` →（条件边）→ `chat_response` / `search` / `route` / `travel` → `END`。
- **`travel` 节点**：`asyncio.gather` 并发执行攻略搜索与路线规划，再交由 `IntegratorAgent` 按“交通 → 游玩”时序整合。
- 辅助逻辑：`_extract_destination`（规则优先 → LLM 提炼 → 规则兜底）、`_parse_location_info`（解析起终点，支持 `[当前位置:北京]` / `[位于xx]` 前缀兜底）、`_clean_final_output`（去除 markdown 噪音）。

### 5. Web 层（`app.py` + `index.html`）

FastAPI 提供 REST + SSE 接口，与前端单页 UI 一一对应：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 返回 `index.html` |
| GET | `/health` | 健康检查 |
| GET | `/intents` | 意图定义列表（前端“功能介绍”弹窗数据源） |
| GET | `/location?lat=&lng=` | 逆地理编码（经纬度 → 地址） |
| POST | `/chat/stream` | SSE 流式聊天（`step` / `partial` / `done` / `error` 事件） |
| GET | `/memory/sessions` | 会话列表 |
| GET | `/memory/sessions/{id}` | 会话详情 |
| DELETE | `/memory/sessions/{id}` | 删除单个会话 |
| DELETE | `/memory/sessions` | 清空全部会话 |

前端特性（`index.html`，原生 JS 无构建）：

- 浏览器 GPS 定位自动获取当前位置（可关闭），作为出行起点；
- SSE 流式接收回复，模拟逐字输出；
- 会话列表 / 历史记录管理（JSON 文件持久化于 `.chat_memory/`）；
- 功能介绍弹窗、快捷示例等。

---

## 🧪 测试

```bash
# 运行全部离线测试（默认跳过需要真实网络的用例）
pytest

# 运行包含真实网络/外部 API 的测试（Tavily / 高德 / 12306）
pytest --run-network
```

测试覆盖：graph 辅助方法（起终点解析、目的地提取、输出清洗、工具校验）、意图识别、BaseAgent、设置项加载，以及基于模拟数据的 MCP 工具回归（三段式路线、缓存、错误协议等）。

---

## 📚 相关文档

| 文档 | 内容 |
|------|------|
| [项目介绍.md](./项目介绍.md) | 项目详细介绍与设计决策记录 |
| [优化方案.md](./优化方案.md) | 架构优化方案 |
| [优化报告.md](./优化报告.md) | 重构过程报告 |
| [路径引用审计.md](./路径引用审计.md) | 导入路径审计 |
| [mcp_server/MCP工具总结.md](./mcp_server/MCP工具总结.md) | MCP 工具层总结 |
| [mcp_server/工具优化方案.md](./mcp_server/工具优化方案.md) | 工具层优化方案 |

---

## ⚠️ 注意事项

- **LLM 中转兼容**：意图识别已改为 prompt 约束 JSON 解析，兼容不支持 `response_format` 的中转 API；若更换模型供应商，确认其在 `config/.env` 中的 Base URL 与模型名。
- **MCP 子进程解释器**：`MCP_PYTHON_PATH` 未设置时使用当前 Python 解释器；若环境中依赖包不全，可显式指定已安装依赖的 Python 路径。
- **高德 QPS**：默认单 Key 限速 0.4s/次，可在 `UnifiedAMapClient.AMAP_KEYS` 追加多个 Key 提升并发。
- **12306 反爬**：`Py12306Client` 内置限速与重试，请勿高频并发调用；车站编码缓存 7 天过期后自动重新抓取。
- **数据口径**：火车信息查询失败时会生成**估算占位数据**并在结果中标注，市内接驳段费用按驾车费率估算，请注意结果中的口径说明。
