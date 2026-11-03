# project_mcp 项目总报告

**架构审计 · 缺陷治理 · 全功能验收 · 优化落地**

| 项目 | 内容 |
| --- | --- |
| 项目路径 | `D:\workspace\project_mcp` |
| 运行环境 | Miniconda `myenv312`（`D:\miniconda3\envs\myenv312`），Python 3.12.12 |
| 报告日期 | 2026-11-03 |
| 覆盖周期 | 2026-11-03 全天，共 10 个阶段（审计 → 迁移 → 修复 → 接入 → 验收 → 优化 → 落地） |
| 报告性质 | **整合型总报告**：合并此前 4 份专项报告的全部结论、证据与状态，并更新至最终代码状态 |
| 数据来源 | `project_mcp_analysis_report.md`、`project_mcp_bugfix_recommendations.md`、`project_mcp_acceptance_report.md`、`project_mcp_optimization_plan.md` + 最终源码实测 |
| 密钥处理 | 全文不展示 `.env` 中任何真实密钥值 |

> 本报告为**唯一权威快照**。此前 4 份专项报告记录的是各自时点的中间状态（如「剩余 25 项」「有条件通过」「未修改源码」），均已过时；状态冲突时以本报告为准。

---

## 0. 执行摘要

### 0.1 最终结论

项目从**「默认启动 100% 失败、输出伪造车次与零换行文本、退出抛异步异常」**的不可交付状态，经过 10 个阶段的治理，已达到**「可独立运行、干净退出、数据不伪造、8 个 MCP 工具全通、80 用例验收 95% 通过、验收发现的 5 项缺陷全部修复并回归通过」**的可交付状态。

当前结论：**通过（Pass）**，剩余事项均为工程化增强与安全加固，不阻断交付与运行。

### 0.2 关键指标

| 指标 | 初始审计（阶段 1） | 全功能验收（阶段 8） | 最终状态（阶段 10 后） |
| --- | --- | --- | --- |
| 默认入口 `main.py` | 失败（LLM 404 / 429） | EXIT 0 | **EXIT 0**，异常计数 0、403 日志计数 0 |
| MCP 工具数 | 4 | 8 | **8**（+2 prompts +1 resource） |
| 输出格式 | 零换行、控制字符 `\x01`、假日期 | 正常 | **正常**，日期动态生成 |
| 路线数据 | `Check 12306 / 08:00→14:00 / 300-600 CNY` 伪造且 `success=True` | `data_quality=partial` 显式标注 | **质量字段贯通到前端 + 代码强制免责声明** |
| RAG | 全项目引用 0 次（孤岛） | 已接入，`rag:hits=4` | **已接入且不自回显**（`rag:no_hits`） |
| 远程 MCP | 传输模式分裂 | SSE 分支不可用（DEF-03） | **Streamable HTTP 实测可用**（`tools=8`） |
| 验收用例 | — | 80 / 76 PASS / 3 FAIL / 1 SKIP / 95.0% | 3 项 FAIL 全部修复；修复回归 **26/26** |
| Python 文件 / 行数 | 48 个 / 7,389 行 | 53 个 / ~8,900 行 | **53 个 / 8,919 行** |
| `.env` 键数 | ~37（含约 10 个废弃键） | 37 | **27**（废弃键清理完毕） |
| 全量 `compileall` | 0 错误 | 0 错误 | **0 错误** |

### 0.3 阶段成果速览

| # | 阶段 | 核心产出 |
| --- | --- | --- |
| 1 | 架构审计与运行诊断 | 48 个 `.py` / 7,389 行全量盘点；29 项问题（4 P0 / 12 P1 / 8 P2 / 5 P3）与三阶段路线图 |
| 2 | LLM 迁移 | 唯一出口 `LLMClientFactory` 切到 `CHATGPT_FREE_API_KEY`，5 个 Agent 零改动全局生效 |
| 3 | 修复 3 个 P0 | 恢复 `memory/` `web/`；统一同进程 `MCPAdapter`；消除 cancel scope 崩溃 |
| 4 | 数据真实性 | 删除假车次、引入 `data_quality/warnings/is_realtime`、质检硬门槛、动态日期、会话关闭 |
| 5 | 输出格式 + 意图/地名 | 3 处 `"\n".join`、加粗反向引用、空行正则；结构化意图解析；替换汉字黑名单 |
| 6 | 嵌入模型离线化 | 删除在线 DashScope 嵌入，指向 `tool_rag/mymodel`（bge-m3 / bce），`HF_HUB_OFFLINE=1` 实测加载 |
| 7 | RAG 接入主程序 | 新增 `rag_service.py` + 4 个 `rag_*` MCP 工具，工作流 RAG 节点真正消费检索结果 |
| 8 | 全功能验收 | 80 用例 / 95.0% / 结论「有条件通过」；发现 DEF-01~05、OBS-01~05 |
| 9 | 优化建议 | 10 项 OPT，复核修正 3 个根因结论，实测 streamable-http 可行 |
| 10 | 按方案实际修改 | 3 批次落地 OPT-01~06，26/26 回归、远程 HTTP 实测、真实 E2E、`main.py` EXIT 0 |

---

## 1. 项目定位与最终架构

### 1.1 定位与技术栈

面向中文旅游场景的**智能旅游助手**：以 Model Context Protocol 为工具总线、LangGraph 为编排引擎，串联「意图识别 → 路由决策 → 工具调用 → 结果整合」，对外提供 CLI（`main.py`）、FastAPI/SSE（`frontend_api.py`）与 MCP 服务（`MCP/protocol/server.py`）三种入口。

实测技术栈：Python 3.12.12 · FastMCP **3.2.4** · LangGraph 1.1.10 · langchain-openai 1.1.11 · langchain-chroma 1.1.0 · chromadb 1.5.5 · FastAPI 0.136.0 · pydantic 2.13.3 · aiohttp / tenacity / httpx-sse。17 项关键依赖在 `myenv312` 中全部存在，无缺包问题。

### 1.2 最终分层架构

```
入口层     main.py(CLI) | frontend_api.py(FastAPI/SSE) | start_mcp.bat + server.py(MCP 服务)
   ↓
编排层     workflow/graph.py(StateGraph) + state.py(WorkflowState / AgentResponse)
   ↓
智能体层   intent_recognizer | search_agent | route_agent | travel_agent | rag_agent
   ↓
集成层     integration/tool_adapter.py(MCPAdapter 单例，引用计数 + call_tool 统一入口)
           protocol/client.py(同进程直连 / Streamable HTTP 双模式)
   ↓
协议层     protocol/server.py：8 tools / 2 prompts / 1 resource
   ↓
能力层     tool_amap/(高德 + 12306) | tool_rag/(本地 bge-m3 + Chroma) | core/(LLM/日志) | config/(Settings)
```

相对初始架构的三处结构性变化：

1. **删掉第二条 MCP 链路**：`RouteAgent` / `TravelAgent` 不再自建 SSE 连接，全部注入同一个 `MCPAdapter`；连接由工作流生命周期管理，建连/断连各恰好 1 次。
2. **新增 RAG 服务层**：`rag_service.py` 单例（懒加载、线程安全、优雅降级）→ `TravelRAG` → Chroma 持久化；由 4 个 MCP 工具暴露，工作流 RAG 节点消费。
3. **远程传输统一**：默认同进程；`MCP_HTTP_URL` 非空时切到 `transport="streamable-http"`，端点 `/mcp`，客户端显式 `StreamableHttpTransport`。

### 1.3 主链路数据流

```
用户输入 → intent_recognition(LLM，标签行结构化解析) → 条件路由
  ├ chat   → chat_response(LLM) → END
  ├ search → Search(Tavily + LLM 攻略)
  │          → rag 节点【先检索历史知识 → 再把本篇攻略入库】
  │          → 命中片段原文直出、追加到 final_output → END
  ├ route  → RouteAgent → MCPAdapter.call_tool('route_planning')
  │          → 高德 + 12306（失败诚实降级为「暂不可用」+ 距离估算） → END
  └ travel → TravelAgent = Search + RouteAgent + LLM 整合
             → 质量字段透传 + 代码强制免责声明 → END
                 ↓
        final_output → _clean_final_output() → CLI / /chat / /chat/stream
```

### 1.4 对外能力清单

**MCP 工具（8 个，全部实测可调）**

| 工具 | 职责 | 关键行为 |
| --- | --- | --- |
| `web_search` | Tavily 联网检索 | 6 组查询 / 5,648 字；兼容 None/list/dict；全失败返回 `success=False` |
| `route_planning` | 高德 + 12306 路线 | 返回 `data_quality / is_realtime / warnings / route_available`；不伪造车次 |
| `validate_travel_content` | 攻略质检 | 伪造痕迹 + 可核查来源 + ≥5 章节 + >800 字硬门槛，一票否决 |
| `format_sources` | 参考来源排版 | 多行输出、日期动态 |
| `rag_add_document` | 知识入库 | 确定性 ID + Chroma upsert 幂等，重复入库 0 新增 |
| `rag_query` | 语义检索 | L2 距离阈值过滤低相关片段，原文直出 |
| `rag_stats` | 知识库状态 | 返回启用状态、持久化路径、块数等 |
| `rag_clear` | 清空知识库 | 必须 `confirm=true`，否则拒绝 |

另有 **2 个 prompts**（`search_prompt`、`route_prompt`）、**1 个 resource**（`travel://standards` 旅游信息质量标准）、**8 个 LangGraph Tool**（`get_langgraph_tools()` 产物，目前仅用于日志/测试，未被业务消费）。

**HTTP 路由**：`/`、`/health`、`/intents`、`/location`、`/chat`、`/chat/stream`（SSE）、`/ui`、`/memory/sessions` 系列（CRUD）。

### 1.5 配置现状

`.env` 共 **27 个生效键**，分组如下（值均省略）：

| 分组 | 键 |
| --- | --- |
| LLM（唯一出口） | `CHATGPT_FREE_API_KEY`、`CHATGPT_FREE_BASE_URL`、`CHATGPT_FREE_MODEL_NAME` |
| 高德 | `AMAP_API_KEY`、`BASE_URL`、`GEOCODE_URL`、`CAR_URL` |
| 搜索 | `TAVILY_API_KEY`、`TAVILY_MAX_RESULTS` |
| 嵌入（本地离线） | `EMBEDDING_MODEL_DIR`（`MCP/tool_rag/mymodel`）、`EMBEDDING_MODEL`（`bge-m3`） |
| RAG | `RAG_ENABLED`、`RAG_PERSIST_DIR`、`RAG_COLLECTION_NAME`、`RAG_TOP_K`、`RAG_MAX_DISTANCE`、`RAG_CHUNK_SIZE`、`RAG_CHUNK_OVERLAP` |
| 服务 | `SERVER_HOST`、`SERVER_PORT`、`MCP_SERVER_NAME`、`LANGSMITH_TRACING`（`false`） |
| 策略 | `MAX_REVIEW_ITERATIONS`、`REVIEW_PASS_SCORE`、`MAX_RETRIES`、`MAX_NEWS_ITEMS`、`LOG_LEVEL` |

约束与注意点：

- 所有生成式 LLM 调用只经 `LLMClientFactory` → `ChatOpenAI`（provider `chatgpt_free`），`base_url` 自动规范化到 `/v1`，日志不打印密钥。
- 嵌入模型**仅本地离线加载**（`HF_HUB_OFFLINE=1`），在线 DashScope 嵌入路径已彻底移除；`mymodel/` 约 5.4GB（bge-m3 1024 维 / bce 768 维），已加入 `.gitignore`。
- `MCP_HTTP_URL` 默认空（同进程）；启用远程需先启动 `python MCP/protocol/server.py`，再把 `.env` 中注释的 `MCP_HTTP_URL=http://127.0.0.1:9000/mcp` 打开并**重启进程**（该值在模块导入时固化）。
- `LANGSMITH_TRACING=false` 已生效，实测 403 日志计数为 0。
- 清理脚本中 `**/.git` / `**/memory` / `**/web` / `**/instruction` 删除规则已移除，仅保留可选清理 `logs`。

---

## 2. 全过程工作历程

| 阶段 | 用户指令 / 目标 | 关键改动 | 验证结果 |
| --- | --- | --- | --- |
| 1 | 读取项目、给架构与设计方向、运行查 bug、出分析报告 | 只读诊断：全量编译、逐模块 import、5 类运行场景、MCP 工具直调、纯逻辑探针 | 48 `.py` / 7,389 行；29 项问题；默认启动失败、输出伪造、退出崩溃 |
| 2 | 把 LLM 调用改用 `CHATGPT_FREE_API_KEY` | `settings.py` / `llm_client.py` / `.env`：统一唯一 LLM 出口，`/v1` 规范化、密钥占位 fail-fast、provider 别名兼容 | 端点实测 200；`invoke/ainvoke/astream` 全通；`main.py` EXIT 0（38s），P0-1 关闭 |
| 3 | 先修 3 个 P0 | 恢复 `memory/` 4 文件与 `web/index.html`；移除清理脚本危险规则；`MCPAdapter` 加 `_depth` 引用计数 + 统一 `call_tool()`；`RouteAgent`/`TravelAgent`/`graph.py` 删除手写 SSE 与假 `getattr` | **9000 端口关闭**下 `main.py` EXIT 0 / 21.7s；建连/断连各 1 次；无 cancel scope / RuntimeError / Traceback |
| 4 | 补全数据真实性 | `station_names.json`（3,365 条）作车站编码基线；删除 `_create_default_train_info()` 假车次；新增 `data_quality(real/partial/estimated/failed)` / `warnings` / `is_realtime`；`format_duration(0)` → 「暂无数据」；质检改硬门槛；Tavily 返回类型归一；硬编码日期改 `datetime.now()`；aiohttp 会话统一关闭 + QPS 重试上限 | `route_planning` 1067.29km / 378min / `partial` / 3 条 warnings / 无伪造标记；质检 100 通过、短文与含 `Check 12306` 一票否决；`main.py` EXIT 0 / 18.7s |
| 5 | 批次 B 输出格式；P1-8 / P1-13 | 3 处 `"".join` → `"\n".join`；`r'\x01'` → `r'\1'`；`r'{\n3,}'` → `r'\n{3,}'` + `\n\n`；`_parse_intent_response()` 标签行正则；`_looks_like_place()` 替换汉字黑名单 | 意图 9/9、地名 13/13、路线 5/5；`_clean_final_output` 保留文字且无控制字符；`format_sources` 4 行且日期为当天 |
| 6 | 去除在线嵌入，本地路径指向 `tool_rag\mymodel` | 重写 `embedding_manager.py`：删 `DashScopeEmbeddings` / 在线分支 / `EmbeddingType`；新增别名映射与本地路径校验，禁止静默联网下载 | 3 个别名解析均命中 `config.json`；离线真实加载 bge-m3 1024 维 / 5.5s、bce 768 维 / 4.8s，范数 1.0 |
| 7 | 将 RAG 工具接入主程序 | 新增 `rag_service.py`（单例、持久化、带分数检索、统计、清空）；`travel_rag.py` 加 `add_text/query_with_scores`；`server.py` 注册 4 个 `rag_*`；重写 `rag_agent.py`；`state.py` 补 `rag_text`；`graph.py` RAG 节点真正追加检索片段 | 8 工具全注册；离线入库/去重/跨文档过滤/清空重建全通；`RAG_ENABLED=false` 不加载模型；真实 search E2E `['search','rag:hits=4']` |
| 8 | 测试全部功能，做验收报告 | 7 分节 80 用例真实调用（不 mock 业务结果），破坏性接口仅验证拒绝路径 | **80 / 76 PASS / 3 FAIL / 1 SKIP / 95.0%**，结论「有条件通过」；发现 DEF-01~05 与 OBS-01~05 |
| 9 | 根据缺陷做优化建议 | 只读复核 + 一次性探针；修正 3 个根因结论；10 项 OPT 与 3 批路线图 | DEF-04 丢弃点精确定位到 `travel_agent.py`；`streamable-http` 探针 `OK tools=8 rag_stats_len=331`；未改源码 |
| 10 | 修改项目 | 3 批次落地：① 数据真实性闭环 + RAG 先检索后入库；② FAISS 重载 + Streamable HTTP；③ 直辖市定位归一、LangSmith 关闭、`.env` 清理、`instruction/` 恢复、文档同步 | 全量编译 0 错误；桩件 **26/26**；`REMOTE_HTTP: OK tools=8 rag_stats_len=331`；真实 E2E `search → rag:no_hits`、`travel` 带 `partial/3 warnings/免责声明`；`main.py` EXIT 0、异常 0、403 日志 0 |

---

## 3. 缺陷全景与最终状态

### 3.1 初始审计 29 项（P0 / P1 / P2 / P3）

> 状态口径：`已修复` = 代码已改且实测通过；`部分` = 主问题已解、附带项仍存；`遗留` = 未改动，属增强/加固类。

| 编号 | 主题 | 状态 | 说明 |
| --- | --- | --- | --- |
| P0-1 | LLM 模型名 404 / 429 限流 | 已修复 | 迁移 `CHATGPT_FREE_API_KEY`，唯一出口全局生效 |
| P0-2 | `memory/` `web/` 被清理脚本误删，`frontend_api` 导入即崩 | 已修复 | 恢复 4 个 `.py` + `web/index.html`；危险删除规则移除；`/memory/*` 与 `/ui` 恢复 |
| P0-3 | SSE 手动 `__aenter__/__aexit__` 导致 cancel scope 崩溃 | 已修复 | 统一 `MCPAdapter`，删除手写 `sse_client` / `ClientSession` |
| P0-4 | 同进程 / SSE 传输模式分裂 | 已修复 | 统一同进程 + `MCP_HTTP_URL` 配置化；远程改 Streamable HTTP |
| P1-5 | 3 处 `"".join(lines)` 零换行 | 已修复 | `"\n".join(lines)` |
| P1-6 | 加粗被替换成 `0x01` 控制字符 | 已修复 | 替换串改 `r'\1'` |
| P1-7 | 空行压缩正则写错 | 已修复 | `r'\n{3,}'` → `'\n\n'` |
| P1-8 | 意图全文子串匹配误判 | 已修复 | `_parse_intent_response()` 标签行解析，失败降级 `chat/0.5` |
| P1-9 | `no_fake_info` 恒真、短文也能过质检 | 已修复 | 伪造痕迹 + 来源 + 章节 + 字数硬门槛 |
| P1-10 | Tavily `None` 写入字面 `"None"` | 已修复 | `_coerce_text` / `_normalize_tavily_response`，全失败返回失败态 |
| P1-11 | 车站空缓存 → 伪造车次且 `success=True` | 已修复 | 3,365 条基线映射 + 质量字段 + 估算标注 + 时长兜底 |
| P1-12 | aiohttp 会话未关闭、QPS 无限递归 | 已修复 | `__aenter__/__aexit__` + 幂等 `close()` + 3 次上限指数退避 |
| P1-13 | 汉字黑名单误杀颐和园 / 人民广场 | 已修复 | `_looks_like_place()`；目的地缺失返回追问文案 |
| P1-14 | `tool_rag/` 全项目引用 0 次；RAG 结果被丢弃 | 已修复 | 阶段 7 完整接入，阶段 10 消除自回显 |
| P1-15 | `simple_test.py` 写 `logs/` 目录不存在 | 遗留 | 低优先级辅助脚本，未纳入本轮范围 |
| P1-16 | 搜索/整理时间硬编码 2026 年 5 月 | 已修复 | `datetime.now()` 动态生成 |
| P2-17 | 单例 adapter 被双重 `async with`；`get_langgraph_tools()` 产物无人用 | 部分 | 引用计数已修；工具转换产物仍仅用于日志/测试（见 OBS-02/03） |
| P2-18 | Agent / LLM 客户端重复实例化 | 遗留 | 功能无影响，未做缓存优化 |
| P2-19 | `rag_text` 未定义；`run_stream` chat 分支重复调 LLM；记忆固定存 `default` | 部分 | `rag_text` 及 3 个质量字段已补；后两项未改 |
| P2-20 | `search → rag` 结果被丢弃；非 chat 按 50 字切片伪流式 | 部分 | RAG 结果已真正追加；分段推送仍保留（设计取舍，未标为真流式） |
| P2-21 | 无效类型注解、SSE 与 Streamable HTTP 协议错配 | 已修复 | `Optional[Union[...]]`；远程统一 `/mcp` |
| P2-22 | `.env` ~30 项 vs Settings 12 项，配置漂移 | 部分 | LLM / RAG / 嵌入配置已补齐，废弃键清理至 27；个别非声明键仍被忽略 |
| P2-23 | 死代码与散落辅助脚本 | 部分 | 高级 RAG 已在包内标注为实验性；根目录辅助脚本仍保留 |
| P2-24 | `PROJECT_STRUCTURE.md` 统计失真（14 文件 / 已删除 memory、web） | 部分 | 曾更新至 53 个 `.py`；**当前该文件不在工作区**（git 索引中为暂存删除），其说明职责由本总报告承担 |
| P3-25 | `.env` 明文密钥；缺 `.env.example` | 部分 | `.gitignore` 已覆盖、废弃键清理；仍建议提供 `.env.example` 并检查 git 历史是否曾泄露密钥 |
| P3-26 | `LANGSMITH_TRACING=true` 持续 403 噪音 | 已修复 | 改 `false` 并移除无效 key；实测 403 计数 0 |
| P3-27 | CORS `allow_origins=["*"]`；MCP 绑定 `0.0.0.0:9000` 无鉴权 | 遗留 | **安全风险**：启用远程模式时必须限内网 + 加认证 |
| P3-28 | 清理脚本含 `**/.git` 等删除规则 | 已修复 | 规则移除，仅保留可选 `logs` 清理 |
| P3-29 | 无 `requirements.txt` / 自动化测试 | 部分 | 已有 `compileall` + 26/26 桩件回归；`requirements.txt` 与 `tests/` 尚未固化 |

**小结**：4 个 P0 全部关闭；12 个 P1 中 11 个完全修复，仅 `P1-15`（辅助脚本日志目录）遗留；P2 / P3 剩余项均为架构整洁、性能优化、安全加固与工程基线，不影响功能交付。

### 3.2 验收发现的 5 项缺陷（DEF-01 ~ DEF-05）

| 编号 | 严重度 | 问题 | 最终修复 | 验证证据 |
| --- | --- | --- | --- | --- |
| DEF-01 | 中 | `FAISS.load_local` 缺 `allow_dangerous_deserialization`，已有索引无法重载，异常被静默吞掉 | 补 `allow_dangerous_deserialization=True`（仅自产索引）；新增 `manifest.json` 校验模型签名，不兼容自动重建；`last_error` + `logger.exception` 不再静默返回 False | 桩件：重载后 `ntotal=2`；换别名/维度不兼容自动重建 |
| DEF-02 | 低 | 高德对直辖市返回 `city=[]`，`/location` 拼出 `"[]东城区"` | `client.py` 新增 `_as_text()` 在 SDK 边界归一，`city or province`；前端二次防御 | 桩件返回 `'北京市东城区'`；`regeocode_city` 为字符串 |
| DEF-03 | 中 | SSE 服务端可启动但三种客户端连接方式全部初始化超时 | 服务端改 `transport="streamable-http"`（`/mcp`），保留 `run_mcp_sse` 别名；客户端显式 `StreamableHttpTransport` + `init_timeout=15` | `REMOTE_HTTP: OK tools=8 rag_stats_len=331`（走产品 `MCPStreamHTTPClient`） |
| DEF-04 | 中（数据真实性风险） | `TravelAgent` 忽略 `data_quality/is_realtime/warnings`，整合提示词仍写「实时精确」 | 读出 `route_agent` 已透传的质量字段；提示词条件化 + 质量声明块；费用/耗时标注估算；**代码强制追加**「请勿据此购票」免责声明；字段回传 `TravelResponse.content` → state → `/chat` → `done` 事件 | 真实 E2E：`travel → data_quality=partial / is_realtime=false / warnings=3 / 免责声明=True / 无伪造`；travel 9 项质量断言通过 |
| DEF-05 | 低 | LangSmith 失效 key 导致每次 LLM 调用打印 403 | `.env` 改 `LANGSMITH_TRACING=false`、移除无效密钥 | `main.py` 冒烟日志 403 计数 0 |

> DEF-04 的复核要点：`route_agent.py:128-130` 本就已透传质量字段，丢弃点在 `travel_agent.py`；且 `route_success=True` **不代表**实时（`route_agent` 在 `success or route_info` 即返回成功），判据只能是 `is_realtime` + `data_quality`。免责声明必须由代码追加，只写进提示词时 LLM 可能省略。

### 3.3 优化方案 10 项（OPT）实施状态

| 编号 | 内容 | 优先级 | 状态 |
| --- | --- | --- | --- |
| OPT-01 | DEF-04 数据真实性闭环 | P0 | 已落地 |
| OPT-02 | DEF-01 FAISS 持久化 + manifest | P1 | 已落地 |
| OPT-03 | DEF-03 统一 Streamable HTTP | P1 | 已落地（方案 A） |
| OPT-04 | OBS-01 消除 RAG 自命中回显（先检索后入库） | P1 | 已落地 |
| OPT-05 | DEF-02 直辖市定位归一 | P2 | 已落地 |
| OPT-06 | DEF-05 消除 LangSmith 噪音 | P2 | 已落地 |
| OPT-07 | 高级 RAG / 死代码治理 | P2 | 部分：已在 `tool_rag/__init__.py` 标注实验性；未迁移目录、未删除 `get_langgraph_tools()` |
| OPT-08 | 配置与密钥卫生 | P2 | 部分：`.env` 废弃键已清理（37→27）；`.env.example` 未提供 |
| OPT-09 | 工程基线（恢复文档、分主题提交、打 tag） | P2 | 部分：`instruction/` 曾恢复；**git 尚未提交 / 未打 tag**；当前工作区仅有 6 个文档的暂存删除 |
| OPT-10 | 性能 / 可观测性 / 回归资产固化 | 增强 | 未做：`tests/acceptance/`、`RAG_WARMUP_ON_STARTUP`、前端质量徽标 |

### 3.4 观察项（OBS）状态

| 编号 | 观察 | 状态 |
| --- | --- | --- |
| OBS-01 | 首次 RAG 回显本篇攻略片段 | **已消除**：`_rag_node` 改为先检索历史再入库；干净库首次为 `rag:no_hits` |
| OBS-02 | `ParentChildRAG` / `DualVectorRAG` / `DatabaseManager` 未被业务调用 | 仍在；已标注为实验性，功能本身验收通过（RAG-02/03/04） |
| OBS-03 | `get_langgraph_tools()` 产物未被消费 | 仍在；工作流走 `MCPAdapter.call_tool()` |
| OBS-04 | `.env` 约 10 个废弃键 | **已清理**（37 → 27） |
| OBS-05 | `instruction/` 未恢复、大量改动未提交 | 文档曾恢复，但当前 `instruction/` 为空且 git 索引中为暂存删除；源码改动已在工作区生效，`git status` 仅剩 6 个文档删除项 |

---

## 4. 全功能验收与回归证据

### 4.1 80 用例结果（阶段 8 基线）

| 分节 | 用例 | 通过 | 未通过 | 跳过 | 通过率 |
| --- | --: | --: | --: | --: | --: |
| 环境与静态层 | 19 | 19 | 0 | 0 | 100% |
| MCP Server 能力 | 20 | 20 | 0 | 0 | 100% |
| 工作流端到端 | 6 | 6 | 0 | 0 | 100% |
| 组件级（LLM/RAG/高德） | 13 | 12 | 1 | 0 | 92% |
| FastAPI 接口层 | 17 | 15 | 1 | 1 | 88% |
| 双传输模式 | 2 | 1 | 1 | 0 | 50% |
| 入口冒烟 | 3 | 3 | 0 | 0 | 100% |
| **合计** | **80** | **76** | **3** | **1** | **95.0%** |

3 项 FAIL 对应 DEF-01（RAG-06）、DEF-02（API-05B）、DEF-03（SSE-02）；1 项 SKIP 为破坏性接口 `DELETE /memory/sessions`（仅静态确认路由已注册）。DEF-04 / DEF-05 虽未计入 FAIL，但作为数据真实性风险与日志噪音列入缺陷清单，并已在阶段 10 修复。

### 4.2 主链路关键正向证据

- `web_search`：6 组查询 / 5,648 字，结构化 6 条；`route_planning`：`success=false / route_available=true / data_quality=partial / is_realtime=false / warnings=3` / 1067km。
- 4 条意图真实跑通：`chat`（0.99）、`route`（0.95）、`search`（0.97，`search → rag:hits=4`）、`travel`（0.95）；`run_stream` 19 个事件含 `step/partial/done`；目的地缺失守卫返回 `destination_not_recognized`。
- 本地 bge-m3 离线加载 1024 维、范数 1.0；`ParentChildRAG` 父上下文回填、`DualVectorRAG` combined/hybrid、Chroma+FAISS CRUD 均通过。
- 质检：完整攻略 100 分通过；90 字短文 80 分 `passed=false`；含 `Check 12306` 95 分 `passed=false`。
- 车站编码 3,365 条离线加载（`北京 → BJP`、`上海虹桥 → AOH`）；`format_duration(0)` → 「暂无数据」。
- `main.py` EXIT 0，无 Traceback / RuntimeError / cancel scope，建连断连各 1 次，9000 端口无残留。

### 4.3 阶段 10 修复后的回归

| 验证项 | 结果 |
| --- | --- |
| 全量 `compileall` | 0 错误 |
| 桩件回归 | **26/26**（FAISS 重载 `ntotal=2`、manifest 不兼容自动重建、travel 9 项质量断言、RAG 调用顺序 `retrieve→index`、直辖市 `'北京市东城区'` 等） |
| 远程 HTTP 模式 | `REMOTE_HTTP: OK tools=8 rag_stats_len=331` |
| 真实 `search` 链路 | `execution_path=['search','rag:no_hits']`，不再自回显 |
| 真实 `travel` 链路 | `data_quality=partial / is_realtime=false / warnings=3 / 免责声明=True / 无伪造` |
| `main.py` 冒烟 | EXIT 0；异常计数 0；**LangSmith 403 计数 0**；9000 端口无监听残留 |
| 临时产物 | 探针脚本、日志、`_acceptance/`、`data/memory/` 测试会话全部清理 |

### 4.4 ⚠️ 期望值变更（复测必读）

验收报告 **WF-03** 记录的 `rag:hits=4` 来自「先入库本篇 → 再检索」的自命中。阶段 10 调整为「先检索历史 → 再入库本篇」后：

- 干净知识库下**首次**同目的地提问应为 `rag:no_hits`（或仅命中其它历史文档）；
- **第二次**相同/相关目的地提问才会命中上一篇入库内容；
- 这是**期望值变更，不是回归失败**。复测前需更新 WF-03 断言，否则会误判。

---

## 5. 当前系统状态

### 5.1 能力矩阵

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| CLI 独立运行 | ✅ | `main.py` 无需手动起服务，EXIT 0 |
| Web 服务 | ✅ | FastAPI 全路由 + `/ui` 静态资源 + SSE 流式 |
| MCP 同进程（默认） | ✅ | 8 tools / 2 prompts / 1 resource |
| MCP 远程（opt-in） | ✅ | Streamable HTTP `/mcp`，需启用配置并重启 |
| 4 类意图 | ✅ | chat / route / search / travel，结构化解析 |
| RAG 知识库 | ✅ | 本地 bge-m3 + Chroma 持久化，幂等入库、带分数检索、阈值过滤 |
| 数据真实性 | ✅ | 四级质量标注 + warnings + 代码强制免责声明 + 质检硬门槛 |
| 会话记忆 | ✅ | `memory/` CRUD，隔离临时目录验证通过 |
| 离线嵌入 | ✅ | `HF_HUB_OFFLINE=1` 真实加载，无联网下载 |
| 日志 | ✅ | 无 403 噪音，无 Traceback |
| 工程基线 | ⚠️ | 未提交 / 未打 tag / 无 `requirements.txt` / 无 `tests/` |

### 5.2 运行方式

```bash
# 环境：D:/miniconda3/envs/myenv312/python.exe
cd D:/workspace/project_mcp

# 1) CLI（默认，最常用）
python -X utf8 main.py

# 2) Web API
python -X utf8 -m uvicorn frontend_api:app --host 127.0.0.1 --port 8000

# 3) 仅启动 MCP 服务（远程模式用；端点 /mcp）
python -X utf8 MCP/protocol/server.py

# 4) 启用远程模式：先放开 .env 中注释的 MCP_HTTP_URL，再重启进程
#    MCP_HTTP_URL=http://127.0.0.1:9000/mcp

# 5) 全量语法检查
python -X utf8 -m compileall -q MCP main.py frontend_api.py
```

> 安全提醒：远程模式服务端当前绑定 `0.0.0.0:9000` 且无鉴权，仅限本机/内网使用，勿暴露公网（P3-27 遗留）。

### 5.3 数据真实性机制（本次治理的核心原则）

1. **质量分级**：所有路线结果带 `data_quality = real | partial | estimated | failed` 与 `is_realtime`、`warnings`；估算值不再置 `success=True`。
2. **无数据时不编造**：12306 查不到车次 → 「暂不可用（请查询铁路 12306 官方）」；火车段按距离估算（250km/h + 40min 缓冲）并显式标注「非实时」。
3. **免责声明由代码追加**：`travel` 输出末尾强制追加「请勿据此购票，请以铁路12306或航司官网为准」，不依赖 LLM 自觉。
4. **字段贯通**：`route_agent` → `travel_agent` → `WorkflowState` → `/chat` 响应与 `done` 事件，前端可直接渲染「实时 / 估算 / 降级」徽标（UI 尚未实现）。
5. **质检硬门槛**：攻略含伪造/占位痕迹、缺可核查来源、章节不足 5、字数 ≤800 任一即 `passed=false`。
6. **RAG 原文直出**：检索片段不经 LLM 改写，标注「非实时数据，仅供参考」，避免二次编造。

---

## 6. 剩余风险与后续建议

### 6.1 风险清单

| # | 风险 | 影响 | 建议 |
| --- | --- | --- | --- |
| R1 | **未提交 / 未打 tag** | 改动无法回溯、无法回滚；`MCP/tool_rag/rag_service.py` 等新增文件需确认纳入版本库 | 按主题分 4 次提交（① LLM 与配置 ② P0 与数据真实性 ③ RAG 接入 ④ 文档治理），打 `v1.2-acceptance` tag |
| R2 | `PROJECT_STRUCTURE.md` 与 `instruction/` 5 份历史文档处于 git 暂存删除状态，工作区不存在 | 项目自带文档缺失 | 确认是否恢复；本总报告已承担结构说明职责 |
| R3 | **CORS `*` 与 MCP `0.0.0.0` 无鉴权**（P3-27） | 启用远程模式时任意主机可调用工具消耗 API 额度 | 限内网/白名单 + Token；不要暴露 9000 到公网 |
| R4 | 外部依赖强约束 | Tavily / 高德 / 12306 / chatanywhere 任一限流或失效即降级该链路 | 保持现有诚实降级；统一加 `degradation` 日志字段 |
| R5 | 本地嵌入模型 5.4GB | 首次 RAG 调用约 10s；检测到 CUDA 会加载到 GPU | 单例懒加载已实现；部署时预留显存，可加 `RAG_WARMUP_ON_STARTUP` 开关 |
| R6 | 无 `requirements.txt` / `tests/` 固化 | 依赖不可复现、每轮改动需重写探针 | 用 `pip freeze` 锁定；把 6 组高频回归固化为 `tests/acceptance/` |
| R7 | 密钥卫生 | `.env` 明文存真实 Key（已被 `.gitignore` 覆盖） | 提供 `.env.example`（仅键名与占位值）；检查 git 历史是否曾提交密钥，若有则轮换 |
| R8 | 高级 RAG / `get_langgraph_tools()` 悬空 | 后来者易误认为已上线能力 | 已标注实验性；后续二选一：产品化（加 `RAG_STRATEGY` 工厂）或移入 `experimental/` |

### 6.2 建议实施顺序

1. **先固化基线**：确认 `git status`，把全部源码改动与新增文件提交，打 tag（R1 / R2）。
2. **再补工程资产**：`requirements.txt`、`.env.example`、`tests/acceptance/`（复用 INTENT-01 / PLACE-01 / MCP-13 / MCP-09~10 / MCP-15~18 / MCP-01~03 / RAG-06 / API-05B / WF-03 期望更新）。
3. **然后做安全与展示**：CORS 与 MCP 鉴权、前端质量徽标（`/chat` 已带字段）。
4. **最后评估增强项**：`RAG_STRATEGY` 工厂、`RAG_WARMUP_ON_STARTUP`、真流式 `llm.astream()`、LLM 客户端按 `(provider, model, temperature)` 缓存。

### 6.3 明确未做事项

- 未执行 `git commit` / 打 tag（留待确认）。
- 未创建 `tests/acceptance/`、`requirements.txt`、`.env.example`。
- 未实现前端「实时 / 估算 / 降级」徽标（后端字段已就绪）。
- 未改 `run_stream` 伪流式、未做 Agent/LLM 实例化缓存、未处理 `P1-15` 辅助脚本。
- 未删除高级 RAG 与 `get_langgraph_tools()`，仅标注为实验性。
- 未恢复 `instruction/` 5 份历史项目文档（当前目录仅有本总报告）。

---

## 7. 附录

### 附录 A：报告索引

| 报告 | 时点 | 性质 | 与本总报告关系 |
| --- | --- | --- | --- |
| `project_mcp_analysis_report.md` / `.html` | 阶段 1 | 架构审计与运行诊断（29 项问题、三阶段路线图） | 第 1、3.1 节为其结论的更新版 |
| `project_mcp_bugfix_recommendations.md` | 阶段 2~5 | LLM 迁移说明 + 逐批修复建议（含 4 次状态更新） | 第 2、3.1 节吸收其全部修复记录 |
| `project_mcp_acceptance_report.md` / `.html` | 阶段 8 | 80 用例逐项矩阵、DEF-01~05、OBS-01~05 | 第 3.2、4 节为其结论 + 修复后状态 |
| `project_mcp_optimization_plan.md` / `.html` | 阶段 9 | 10 项 OPT、3 批路线图、回归清单 | 第 3.3、6 节为其落地状态 |
| **`project_mcp_final_report.md` / `.html`（本文）** | 阶段 10 后 | **整合型总报告** | 状态冲突时以本文为准 |

上述 4 份专项报告位于 `C:\Users\koishi\WorkBuddy\2026-11-01-19-35-52\`；本总报告位于 `D:\workspace\project_mcp\instruction\`。

### 附录 B：本轮涉及的主要改动文件

| 模块 | 文件 |
| --- | --- |
| 配置 | `.env`、`MCP/config/settings.py`、`.gitignore`、`start_mcp.bat` |
| LLM | `MCP/core/llm_client.py` |
| 协议 / 集成 | `MCP/protocol/server.py`、`MCP/protocol/client.py`、`MCP/integration/tool_adapter.py` |
| 编排 | `MCP/workflow/graph.py`、`MCP/workflow/state.py` |
| 智能体 | `MCP/agents/specialized/intent_recognizer.py`、`route_agent.py`、`travel_agent.py`、`search_agent.py`、`rag_agent.py` |
| 高德 / 12306 | `MCP/tool_amap/client.py`、`route_planner.py`、`train_crawler.py`、`py12306_client.py`、`utils.py`、`data/station_codes.json` |
| RAG | `MCP/tool_rag/rag_service.py`（新增）、`travel_rag.py`、`embedding_manager.py`、`database_manager.py`、`__init__.py` |
| API | `frontend_api.py` |
| 文档 / 脚本 | `PROJECT_STRUCTURE.md`、`cleanup_project.py`、`simple_cleanup.py`、`instruction/` |

### 附录 C：验证命令速查

```bash
# 全量编译
python -X utf8 -m compileall -q MCP main.py frontend_api.py

# MCP 工具清单（同进程）
python -X utf8 -c "import asyncio,sys; sys.path.insert(0,'.'); from MCP.integration.tool_adapter import get_mcp_adapter; ..."

# 入口冒烟（关注 EXIT / Traceback / 403 / cancel scope / 9000 端口）
python -X utf8 main.py

# 远程模式（先起服务，再放开 MCP_HTTP_URL 并重启进程）
python -X utf8 MCP/protocol/server.py
```

### 附录 D：约定与口径

- **状态优先级**：本总报告 > 优化方案 > 验收报告 > 修复建议 > 初始审计报告。
- **数据真实性口径**：`real` = 实时接口返回；`partial` = 部分实时、部分估算；`estimated` = 全部估算；`failed` = 无可用数据。估算一律显式标注且不得置 `success=True`。
- **RAG 口径**：检索片段为**原文直出**，不经 LLM 改写；`RAG_MAX_DISTANCE` 为 L2 距离阈值，超过即过滤。
- **安全口径**：`allow_dangerous_deserialization=True` 仅可用于本程序 `save_local()` 自产的 FAISS 索引目录，持久化路径必须来自可信配置。
- **配置口径**：`settings` 在导入时读取，`client.py` 的 `_MCP_HTTP_URL` 为模块级常量，改 `.env` 必须重启进程。

---

*报告由项目全周期实测证据整合生成 · 2026-11-03 · 不展示任何真实密钥值*
