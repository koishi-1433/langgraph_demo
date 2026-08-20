# MCP 工具集总结：目录结构与逻辑实现分析

> 范围：`langgraph_demo/mcp_server/`（5 个 MCP stdio 工具）
> 技术栈：FastMCP 3.2.4 + MCP Python SDK（stdio 传输）、aiohttp、pydantic
> 版本：langgraph_demo_tools v1.0.0 ｜ 文档日期：2026-08-20

---

## 一、概述

`mcp_server` 通过 **MCP（Model Context Protocol）stdio 协议**向外部（langgraph workflow 的 ReAct Agent、独立调试脚本）暴露 5 个工具，聚合三个数据源：

| 数据源 | 能力 | 依赖配置 |
|--------|------|----------|
| **Tavily** | 网页搜索（摘要 + 来源） | `TAVILY_API_KEY` |
| **高德地图** | 地理编码 / 逆地理编码 / 路线规划（驾车、公交） | `AMAP_API_KEY` |
| **12306** | 火车班次 / 余票 / 票价查询 | 无密钥（公开接口，需反爬规避） |

架构上分四层，职责单一、可独立测试：

```
MCP 协议层        mcp_server_stdio.py   —— @mcp.tool() 注册、参数校验、结构化返回、调用日志
     │
异步包装层        tool_amap/async_wrappers.py —— 共享单例、错误协议（error_code + hint）、过滤逻辑
     │
业务逻辑层        tool_amap/route_planner.py  —— 三段式路线规划（市内→火车→市内）
                  tool_amap/train_crawler.py  —— py12306 客户端（缓存/限速/重试/解析）
                  tool_tavily.py              —— Tavily 搜索封装
                  tool_amap/client.py         —— 高德客户端（缓存/限速/多Key轮询）
     │
数据源           Tavily API / 高德 REST API / kyfw.12306.cn
```

---

## 二、完整目录结构

```
langgraph_demo/
├── mcp_server/                          # ★ MCP 服务器（本总结主体）
│   ├── mcp_server_stdio.py              # MCP 入口：注册 5 个 @mcp.tool()，stdio 传输
│   ├── tool_tavily.py                   # Tavily 搜索封装（TavilySearch.invoke）
│   ├── tool_amap/
│   │   ├── __init__.py                  # 空包
│   │   ├── client.py                    # UnifiedAMapClient：高德 8 类接口 + 缓存/限速/多Key轮询
│   │   ├── validators.py                # InputValidator：坐标/日期/地址/API Key 校验
│   │   ├── train_crawler.py             # Py12306Client：班次/余票/票价查询（核心解析）
│   │   ├── route_planner.py             # RoutePlanner：三段式路线规划 + 火车降级占位
│   │   ├── async_wrappers.py            # 异步包装层（MCP 直接用，共享单例 + hint + 过滤）
│   │   ├── wrappers.py                  # 同步兼容层（仅供独立脚本调试，非 MCP 使用）
│   │   ├── utils.py                     # estimate_train_cost / format_duration 等工具函数
│   │   └── data/
│   │       └── station_codes.json       # 12306 车站编码缓存（2311 站，7 天 TTL，损坏自愈）
│   └── 工具优化方案.md                   # P0/P1/P2 + V2 优化方案与实施状态
├── config/
│   ├── settings.py                      # 配置：AMAP/TAVILY Key、MCP 客户端 stdio 配置
│   └── .env                             # 密钥（AMAP_API_KEY / TAVILY_API_KEY 等）
├── agents/
│   ├── route_agent.py                   # 路线 Agent：仅授权 4 个地图/火车工具
│   └── search_agent.py                  # 搜索 Agent：授权 tavily_web_search
├── workflow/
│   └── graph.py                         # 编排：MCP 客户端加载工具 → ReAct Agent
├── tests/
│   ├── conftest.py                      # pytest 全局配置 + --run-network 标记
│   ├── test_mcp_tools.py                # 离线 11 用例 + 网络 2 用例（e2e）
│   └── test_mcp_tools_simulated.py      # 模拟回归 15 用例（S1~S13 + V2 行为）
└── app.py                               # 应用入口（启动 workflow）
```

> 说明：`tool_adapter` 仅残留过期的 `__pycache__` 产物，无源码；`tool_amap/wrappers.py` 为早期同步调试层，MCP 实际走 `async_wrappers.py`（避免 `asyncio.run` 每次新建事件循环的黑洞）。

---

## 三、工具清单与调用链总览

| # | 工具名 | 用途 | 参数 | 内部调用链 |
|---|--------|------|------|------------|
| 1 | `tavily_web_search` | 网页搜索 | `query`, `max_results(1~10)` | `tool_tavily.web_search`（线程池） |
| 2 | `amap_geocode` | 地址→经纬度 | `address` | `async_wrappers.geocode` → `client.geocode` |
| 3 | `amap_regeocode` | 坐标→地址 | `location(lng,lat)` | `async_wrappers.regeocode` → `client.regeocode` |
| 4 | `amap_route_planning` | 路线规划（三段式） | `origin`, `destination`, `mode(auto/train/driving/transit)` | `async_wrappers.route_planning` → `RoutePlanner.plan_route` |
| 5 | `py12306_query_train_schedule` | 火车班次查询 | `from_station`, `to_station`, `date`, `only_available`, `earliest_time`, `latest_time` | `async_wrappers.query_train_schedule` → `RoutePlanner._get_train_schedule` → `Py12306Client.query_train_schedule` |

**统一返回契约（V2）**：所有工具返回 FastMCP `ToolResult`
- `text`：精简文本摘要（可直接展示 / 喂给 LLM）
- `structuredContent`：完整结构化数据
- 错误统一 `{"success": false, "error_code": ..., "error": ..., "hint": ...}`

---

## 四、逐工具逻辑实现分析

### 4.1 tavily_web_search —— 网页搜索

**入口** `mcp_server_stdio.py`：校验 `max_results ∈ [1,10]`（越界返回 `INVALID_INPUT`），随后 `asyncio.to_thread(web_search, ...)` 在线程池执行同步 Tavily 调用，避免阻塞事件循环。

**实现** `tool_tavily.py::web_search`
1. `max_results` 钳制到 Tavily 上限 `[1,20]`；
2. 构造 `TavilySearch(api_key, max_results, include_answer=True)`——`include_answer` 让 Tavily 真正返回 AI 摘要（`answer` 字段），而非只给来源；
3. `invoke({"query": ...})` → 提取 `answer` + `results[]` 的 `title/url/score` → 序列化为 `{"answer", "sources"}`；
4. 异常时返回 `{"error", "error_code": "TAVILY_SEARCH_FAILED"}`。

**返回摘要示例**
```
搜索「DeepSeek 最新发布」共 3 条结果
摘要：DeepSeek-V4 于 2026-04-24 发布，采用稀疏注意力架构。
来源：
1. 来源1 (https://example.com/1)
```

---

### 4.2 amap_geocode —— 地址 → 经纬度

**链路**：`async_wrappers.geocode` → `UnifiedAMapClient.geocode`。

**核心逻辑** `client.py::geocode`
1. 缓存键 `geo_{address}_{city}`，命中（24h TTL）直接返回；
2. 带 `city` 时拼接 `f"{city}{address}"` 提升解析率；
3. 请求高德 `/v3/geocode/geo`，`status=="1"` 且 `geocodes` 非空 → 取首个 `location` 写入缓存；
4. 失败返回 `None` → 包装层给 `error_code=GEOCODE_FAILED` + hint「请检查地址拼写或使用更完整地址」。

---

### 4.3 amap_regeocode —— 坐标 → 地址

**核心逻辑** `client.py::regeocode`
1. 缓存键 `regeo_{location}`；
2. 仅校验格式（`lng,lat` 可转 float），不过度拦截——修复过"合法坐标被误杀"的坑；
3. 请求 `/v3/geocode/regeo` → 重组为扁平化字段：`formatted_address / country / province / city / district / township / street / business_areas`；
4. 失败 `error_code=REGEOCODE_FAILED` + hint「坐标格式应为 lng,lat」。

---

### 4.4 amap_route_planning —— 三段式路线规划（最复杂工具）

**入口**：`mode` 枚举校验（`auto/train/driving/transit`）。

**`RoutePlanner.plan_route` 分支逻辑**

| mode | 行为 |
|------|------|
| `train` | 直接走火车三段式 |
| `driving` / `transit` | 单方式规划（费用 `max(50, km×0.8)` 油费+过路费口径） |
| `auto` | `_should_use_train`：直线距离 **>120km** → 火车；否则驾车/公交按 `total_duration` 择优；两者都失败则回退火车 |

**火车三段式 `_plan_train_route`（核心）**

```
第1段 市内交通：城市中心 → 出发站（驾车实测 distance/duration）
第2段 高铁段：  出发站 → 到达站（车次/时间/票价）
第3段 市内交通：到达站 → 城市中心（驾车实测）
总览：总距离（城市间直线）/ 总耗时 / 总费用
```

关键子逻辑：

1. **车站定位**：城市名通过 `Py12306Client.CITY_STATION_MAP`（单一权威映射，北京→北京南等 300+ 城市）映射到具体车站；不在映射表则 `find_train_stations` 附近搜索 + `_clean_station_name` 净化。
2. **车站编码重试链 `_geocode_station`**：站名原样 → `+站` → `城市+站名` → `城市市+站名` → `search_poi` POI 兜底。修复了"北京南无法编码 → 第 1 段距离恒为 0"的问题。
3. **班次查询**：`_get_train_schedule`（见 4.5）取第一趟最优车次；失败时降级。
4. **票价语义（关键修复）**：`_resolve_train_cost` **优先真实票价 `price.second_class`**，缺失才回退距离估算；`seats` 字段明确标注为**余票状态**（"有"/"无"/数量），绝不当票价（曾出现把余票 18 当 ¥18 票价的 bug）。
5. **降级占位（V2）**：班次查询失败时 `_create_default_train_info` 按起终点**直线距离动态生成**：时长 ≈ 距离/250km/h（北京→广州 1889km ≈ 7h33m）、二等座 ≈ 分级费率×距离，并标注 `estimated=true` + `warning` 透传真实失败原因；不再固定 "4-6 hours / 300-600 CNY"。
6. **费用口径（V2）**：总费用 = 火车票价 + 两段 `max(20, km×0.8)`（驾车估算），顶层 `segment_cost_note="市内接驳（按驾车估算）"` 标注口径，`segment_1/3` 各带 `cost_note`。

**返回结构**（structuredContent）：
```json
{
  "transport_mode": "Train Travel",
  "total_distance": 1067.9, "total_duration": 324, "total_cost": 593.0,
  "warning": null, "segment_cost_note": "市内接驳（按驾车估算）",
  "train_info": {"train_number": "G1", "price": {"second_class": 553.0}, "seats": {"second_class": "18"}, ...},
  "segment_1": {"step": 1, "icon": "🚗", "mode": "市内交通", "distance": 7.0, "duration": 25, "cost_note": "..."},
  "segment_2": {"step": 2, "icon": "🚄", "mode": "高铁", "distance": 1067.9, "duration": 269, "detail": "..."},
  "segment_3": {"step": 3, "icon": "🚗", "mode": "市内交通", "distance": 8.0, "duration": 30, "cost_note": "..."},
  "route_details": ["📍 第1段：市内交通", "...", "📊 总览", "...", "※ 市内段费用口径：市内接驳（按驾车估算）"]
}
```

---

### 4.5 py12306_query_train_schedule —— 火车班次查询

**链路**：`async_wrappers.query_train_schedule`（过滤）→ `RoutePlanner._get_train_schedule`（会话保活）→ `Py12306Client.query_train_schedule`（核心）。

#### 4.5.1 请求流程（`train_crawler.py`）

```
参数校验（日期/站名）→ 缓存检查 → 会话初始化 → 车站编码解析
→ init 握手 → queryG/query/queryA 三接口依次尝试
→ '|' 行解析 → 排序 → 前 3 趟补票价 → 成功写缓存
```

1. **输入校验**：日期 `datetime.strptime`（拒绝 2026-13-99）、站名 ≥2 字符；
2. **车站编码解析 `_get_station_code`** 六级匹配：精确 → 去"站" → 城市映射表 → 自动城市推导（方位后缀）→ 模糊前缀 → 特殊映射（滨海→天津）；
3. **反爬**：init 握手 → queryG 优先（失败依次 query/queryA）；UA 轮换（Chrome/Edge/Safari）+ `Origin/Referer/X-Requested-With` 伪装；
4. **解析 `_parse_train_info`**：`|` 分隔行，字段偏移抽为命名常量（见下表），少于 35 字段视为垃圾行丢弃；
5. **票价增强**：仅对前 3 趟调 `queryAllPrice`（限频），失败静默回退，不影响主流程。

#### 4.5.2 12306 字段偏移表（`IDX_*` 常量）

| 索引 | 常量 | 含义 |
|------|------|------|
| 2 | `IDX_TRAIN_NO` | 车次编号（训练用，如 240000G1010） |
| 3 | `IDX_TRAIN_NUMBER` | 车次（G1） |
| 8 / 9 / 10 | `IDX_DEPARTURE/ARRIVAL/DURATION` | 发时 / 到时 / 历时 HH:MM |
| 23 / 26 | `IDX_SOFT_SLEEPER` / `IDX_NO_SEAT` | 软卧 / 无座余票 |
| 28 / 29 | `IDX_HARD_SLEEPER` / `IDX_HARD_SEAT` | 硬卧 / 硬座余票 |
| 30 / 31 / 32 | `IDX_SECOND/FIRST/BUSINESS_SEAT` | 二等 / 一等 / 商务余票 |

#### 4.5.3 票价解析 `_extract_prices`

`queryAllPrice` 返回座位编码 → 可读字段映射（A1 商务 / A2 一等 / A3 二等 / A4 特等 / A6、P6 高级软卧 / M 二等卧 / P1 硬座 / P3 硬卧 / P4 软卧 / P5 无座）。`seat_price` 为数字才入字段，无法解析置 `None`。

> ⚠️ 已知限制：`queryAllPrice` 直连常返回 HTML 验证页（需浏览器会话 Cookie），因此真实票价为"尽力而为"，缺失时自动回退 `estimate_train_cost`（V2 已升级估算精度：haversine×1.15 绕行系数 + 分级费率 <500km 0.5 / 500~1000km 0.45 / >1000km 0.42，硬顶 2000）。

#### 4.5.4 重试与缓存策略

| 机制 | 行为 |
|------|------|
| **确定性错误不重试（V2 #1）** | `NO_TRAINS` / `12306接口错误` 直接返回；只对网络/传输类异常（连接失败、接口不可用）重试（默认 2 次，指数退避 1~2s） |
| **成功 TTL 缓存** | `(date, from, to)` → 结果，300s TTL，上限 64 条（LRU 淘汰最旧） |
| **失败负缓存（V2 #9）** | 失败结果 30s 负缓存，上游抖动时不反复打站 |
| **全局限速** | `LAST_CALL_TS` 跨实例共享，默认最小间隔 1.0s |

#### 4.5.5 班次过滤（V2 #8，在 async_wrappers 层）

`only_available`（任一席别非 "无"）、`earliest_time` / `latest_time`（HH:MM 真实时刻校验，字符串比较）——**缓存后过滤**：过滤参数不进缓存键，避免同班次因过滤组合碎片化缓存，过滤零额外请求。响应新增 `total_matched`（过滤后数量）与 `filter` 回显。

---

## 五、跨工具公共机制

### 5.1 错误协议（P1-6 + V2 #7）

所有错误统一 `error_code`，V2 起附带 `hint` 帮助 LLM 自我纠错：

| error_code | 场景 | hint |
|------------|------|------|
| `INVALID_INPUT` | 参数非法（日期/时段/mode/越界） | 请检查参数格式后重试 |
| `STATION_NOT_FOUND` | 找不到车站编码 | 请使用城市名（如北京）或标准站名（如北京南站） |
| `NO_TRAINS` | 无班次 | 可尝试调整出发日期或更换车站 |
| `GEOCODE_FAILED` / `REGEOCODE_FAILED` | 地址/坐标解析失败 | 地址拼写 / 坐标格式 lng,lat |
| `AMAP_REQUEST_FAILED` / `ROUTE_PLAN_FAILED` | 高德异常 | 稍后重试 / 检查起终点 |
| `UPSTREAM_UNREACHABLE` / `UPSTREAM_ERROR` | 12306 不可达 / 接口错误 | 稍后重试 |
| `QUERY_FAILED` / `TRAIN_QUERY_FAILED` / `TAVILY_SEARCH_FAILED` | 兜底 | 稍后重试 |

### 5.2 缓存体系

| 缓存 | 位置 | TTL | 上限 | 说明 |
|------|------|-----|------|------|
| `GEO_CACHE` | client.py 类属性 | 24h | 2048 | geocode/regeocode 结果，`(ts, value)` 存储 + LRU 淘汰 |
| `SCHEDULE_CACHE` | train_crawler.py 类属性 | 300s | 64 | 班次成功结果，跨实例共享 |
| `FAIL_CACHE` | train_crawler.py 类属性 | 30s | 64 | 班次失败负缓存 |
| `station_codes.json` | data/ 文件 | 7 天 | — | 车站编码表；`_is_valid_station_codes` 校验（≥100 条、电报码格式 `^[A-Z]{2,5}$`），损坏自动丢弃重抓 |

### 5.3 会话生命周期（P0-2 + V2 #2）

- **高德**：`UnifiedAMapClient` 模块级共享单例（`_get_shared_amap`），aiohttp 会话复用；
- **12306**：`Py12306Client` 由共享 `RoutePlanner` 持有，**进程内保活**（不再每次调用后 close，省去重复 TCP/TLS 握手），进程退出由 `atexit.register(close_shared_clients)` 统一关闭；
- **同步调试层 wrappers.py**：每次调用后显式关闭，防泄漏。

### 5.4 调用级观测（V2 #10）

`_instrument` 装饰器记录每次调用（走 stderr，不污染 stdio stdout）：
```
2026-08-20 10:00:00 INFO mcp_server_tools [call] name=amap_route_planning elapsed_ms=812.3 status=OK
2026-08-20 10:00:01 INFO mcp_server_tools [call] name=py12306_query_train_schedule elapsed_ms=45.1 status=ERR error_code=NO_TRAINS
```

### 5.5 结构化返回（V2 #6）

FastMCP `ToolResult(content=[TextContent(摘要)], structured_content=完整数据)`。`convert_result` 对 `ToolResult` 原样透传（output_schema 不自动生成），服务端同时下发 `content` 与 `structuredContent`——LLM 直接读摘要，需要字段时读结构化数据，不再自行重组 JSON。

---

## 六、一次完整调用的数据流（示例：`amap_route_planning(北京→上海, mode=auto)`）

```
Client(route_agent) ──tools/call──▶ mcp_server_stdio.py
  │ ① mode 校验通过
  ▼
async_wrappers.route_planning
  │ ② 取共享 RoutePlanner（首次创建：UnifiedAMapClient + Py12306Client）
  ▼
RoutePlanner.plan_route
  │ ③ auto：haversine 距离 1067.9km > 120 → 火车模式
  ▼
_plan_train_route
  │ ④ 北京 → CITY_STATION_MAP → 北京南（geocode 重试链: 北京南→北京南站 ✓）
  │ ⑤ 驾车 route1: 北京→北京南站 7km/25min
  │ ⑥ 上海虹桥→上海 驾车 8km/30min
  │ ⑦ _get_train_schedule(北京南→上海虹桥)  ──▶ Py12306Client.query_train_schedule
  │       init握手 → queryG → 解析3趟 → 前3趟补票价(553) → 写SCHEDULE_CACHE
  │ ⑧ 取最优车次 G1，_resolve_train_cost → 553（真实票价）
  ▼
组装三段式 + 总览（593 = 553 + 20 + 20；324min = 25+269+30）
  ▼
ToolResult{ text=摘要, structuredContent=完整结果 } ──▶ Client
```

---

## 七、配置与运行

### 7.1 配置（`config/settings.py` + `config/.env`）

```env
AMAP_API_KEY=xxx            # 高德 Web 服务 Key
TAVILY_API_KEY=xxx          # Tavily Key
TAVILY_MAX_RESULTS=5        # 默认搜索结果数
```

`settings.MCP_CLIENT`（`MultiServerMCPClient`）配置了 stdio 子进程启动方式：`command=MCP_PYTHON_PATH`（默认当前解释器，可环境变量覆盖）、`args=[mcp_server_stdio.py]`。

### 7.2 启动/调试

```bash
# 独立启动 MCP 服务器（stdio）
python langgraph_demo/mcp_server/mcp_server_stdio.py

# 端到端调试（tools/list + 5 工具逐个调用）
python mcp_tool_test/test_mcp_tools.py

# 模拟数据评估（离线 13 场景报告）
python mcp_tool_test/simulate_tools.py
```

### 7.3 回归测试

```bash
# 离线（含模拟回归 S1~S13 + V2 行为，不联网）
python -m pytest tests -p no:cacheprovider

# 真实网络 e2e（5 工具全量 + 损坏缓存自愈）
python -m pytest tests/test_mcp_tools.py --run-network -p no:cacheprovider
```

当前基线：**离线 67 passed + 2 skipped（network）｜ 网络 13 passed ｜ 模拟报告 13/13**。

---

## 八、设计要点与已知限制

### 设计要点
1. **单一数据源**：城市→车站映射只保留 `Py12306Client.CITY_STATION_MAP` 一份（4 处合并），避免多处漂移。
2. **余票≠票价**：`seats`（余票状态）与 `price`（票价）语义分离，成本计算绝不使用余票数字。
3. **降级不吞原因**：任何失败都带 `error_code` + 真实原因 + `hint`，LLM 可自我纠错或转述给用户。
4. **缓存分层**：成功 300s / 失败 30s / 地理 24h / 车站表 7 天，各司其职。
5. **向后兼容**：V1→V2 字段只增不改（如 `total_matched`、`segment_cost_note`、`hint`、`estimated`），旧消费方不破坏。

### 已知限制 / 后续建议
| 限制 | 建议 |
|------|------|
| 12306 `queryAllPrice` 真实票价需浏览器会话，直连不可用 | 换第三方票务 API 或 12306 官方小程序协议；当前靠 V2 升级的估算兜底 |
| 12306 车站表抓取依赖 kyfw 可达性（偶发 TLS/反爬失败） | 已有 7 天缓存 + 损坏自愈，可加多源镜像 |
| FastMCP 3.2.4 较旧 | 可选升级 3.4.7（工具签名/任务支持更完善） |
| `route_planning` 市内段费用统一按驾车费率估算 | 短途场景可改用 AMap 公交实际费用，进一步贴近真实 |
