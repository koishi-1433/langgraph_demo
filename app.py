"""旅游助手 FastAPI 应用 - 统一入口"""
import asyncio
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 双路径注入，保证两种运行方式都可用：
#   1. 包方式（python -m uvicorn langgraph_demo.app:app）→ 依赖父目录可导入 langgraph_demo 包
#   2. 脚本方式（python langgraph_demo/app.py）→ 相对导入不可用，app.py 内部必须用绝对导入
# 同时注入 langgraph_demo 目录本身，mcp_server 子模块依赖 `from config.settings` 绝对导入
_root = Path(__file__).resolve().parent  # langgraph_demo/
_parent = _root.parent                   # 项目父目录
for _p in (_parent, _root):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

# 包限定绝对导入（langgraph_demo.* 前缀）：
#   - 锚点唯一，不会与环境中其他名为 config/workflow/mcp_server 的库冲突
#   - 支持 `python langgraph_demo/app.py` 直接运行（父目录已注入 sys.path）
from langgraph_demo.config.logger import get_logger
from langgraph_demo.workflow.graph import TravelPlanWorkflow

logger = get_logger(__name__)

# ── FastAPI 应用 ──────────────────────────────────────────────
app = FastAPI(title="旅游助手智能体", version="1.0.0")

# CORS：允许任意来源（含 file:// 打开时的 origin "null"、本地开发其他端口）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 对话内存（JSON 文件持久化） ──────────────────────────────
_MEMORY_DIR = Path(__file__).parent / ".chat_memory"
_MEMORY_DIR.mkdir(exist_ok=True)


def _session_file(sid: str) -> Path:
    return _MEMORY_DIR / f"{sid}.json"


def _list_sessions() -> list[dict]:
    sessions = []
    for p in sorted(_MEMORY_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            sessions.append({
                "id": data["id"],
                "title": data.get("title", ""),
                "msg_count": len(data.get("messages", [])),
                "updated_at": data.get("updated_at", ""),
            })
        except Exception:
            pass
    return sessions


def _save_session(sid: str, title: str, messages: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    data = {"id": sid, "title": title, "messages": messages, "updated_at": now}
    _session_file(sid).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _load_session(sid: str) -> dict | None:
    p = _session_file(sid)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _append_message(sid: str, role: str, content: str, intent: str = "") -> None:
    """追加一条消息到会话文件（原子读写）。"""
    data = _load_session(sid) or {"id": sid, "title": "", "messages": [], "updated_at": ""}
    msg: dict[str, Any] = {"role": role, "content": content, "timestamp": datetime.now(timezone.utc).isoformat()}
    if intent:
        msg["intent"] = intent
    data["messages"].append(msg)
    if not data["title"] and role == "user":
        data["title"] = content[:50]
    _save_session(sid, data["title"], data["messages"])


# ── Pydantic 请求模型 ─────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    gps_lat: float | None = None
    gps_lng: float | None = None
    address: dict[str, str] = {}


# ── Intent 定义 ───────────────────────────────────────────────
INTENTS = [
    {
        "id": "chat",
        "name": "普通聊天",
        "description": "友好闲聊，回答日常问题，不需要调用外部工具。",
        "example": "你好、介绍一下自己、天气怎么样",
    },
    {
        "id": "route",
        "name": "路线规划",
        "description": "查询两地之间的驾车/公交/火车路线，支持起点到终点规划。",
        "example": "北京到上海的驾车路线、杭州到南京怎么走",
    },
    {
        "id": "search",
        "name": "旅游搜索",
        "description": "搜索特定城市的旅游攻略、景点、美食、住宿等信息。",
        "example": "杭州西湖有什么好玩的、西安美食推荐",
    },
    {
        "id": "travel",
        "name": "综合规划",
        "description": "同时生成旅游攻略和交通路线，由 LLM 整合为完整旅行方案。",
        "example": "北京到上海的路线，顺便推荐旅游攻略",
    },
]


# ── 项目根目录（用于定位 index.html） ────────────────────────
_ui_dir = Path(__file__).parent

# 全局工作流实例（首次请求时懒加载）
_workflow: TravelPlanWorkflow | None = None


async def _get_workflow() -> TravelPlanWorkflow:
    global _workflow
    if _workflow is None:
        _workflow = await TravelPlanWorkflow().__aenter__()
    return _workflow


@app.get("/")
async def root():
    """根路径重定向到 UI。"""
    return FileResponse(str(_ui_dir / "index.html"))


@app.get("/health")
async def health():
    """健康检查。"""
    return {"status": "ok", "version": "1.0.0"}


@app.get("/intents")
async def get_intents():
    """返回意图定义列表。"""
    return {"intents": INTENTS}


@app.get("/location")
async def reverse_geocode(lat: float, lng: float):
    """逆地理编码：经纬度 -> 地址信息。"""
    try:
        from langgraph_demo.mcp_server.tool_amap.async_wrappers import regeocode as _regeocode

        # 异步封装直接 await，不再需要 to_thread
        raw = await _regeocode(f"{lng},{lat}")
        parsed = json.loads(raw)

        if parsed.get("status") == "success":
            city = parsed.get("city", "")
            if isinstance(city, list):
                city = city[0] if city else parsed.get("province", "")
            city = city or parsed.get("district", "")

            street_number = parsed.get("street_number", "")
            if isinstance(street_number, dict):
                street_number = street_number.get("number", "")

            return {
                "status": "success",
                "province": parsed.get("province", ""),
                "city": city,
                "district": parsed.get("district", ""),
                "township": parsed.get("township", ""),
                "street": parsed.get("street", ""),
                "street_number": street_number,
                "full_address": parsed.get("formatted_address", ""),
                "detailed_address": parsed.get("formatted_address", ""),
            }
        return {"status": "failed", "error": parsed.get("reason", parsed.get("error", "未知错误"))}
    except Exception as e:
        logger.error(f"逆地理编码失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """流式聊天接口，SSE 推送 step / partial / done / error 事件。"""
    async def event_generator():
        try:
            # 构建 GPS 坐标字符串
            gps_coords = None
            if req.gps_lat is not None and req.gps_lng is not None:
                gps_coords = f"{req.gps_lng},{req.gps_lat}"

            # 构建带地址上下文的 user_input
            user_input = req.message
            if req.address:
                city = req.address.get("city", "")
                district = req.address.get("district", "")
                if city and city not in user_input:
                    user_input = f"[位于{city}{district}] {user_input}"

            workflow = await _get_workflow()
            # 复用前端传入的 session_id；新会话才生成新 UUID
            session_id = req.session_id or str(uuid.uuid4())

            # 加载该会话的历史（用于多轮记忆）
            chat_history: list[dict] = []
            existing = _load_session(session_id)
            if existing:
                chat_history = [
                    {"role": m.get("role", ""), "content": m.get("content", "")}
                    for m in existing.get("messages", [])
                ]

            # 追加用户消息到历史
            _append_message(session_id, "user", req.message)

            # 发送 intent 事件
            yield f"event: step\ndata: {json.dumps({'message': '正在分析...', 'phase': 'analyzing'}, ensure_ascii=False)}\n\n"

            # 运行工作流（携带历史上下文）
            final_state = await workflow.run(user_input, gps_coords=gps_coords, history=chat_history)

            final_output = final_state.get("final_output", "")
            intent = final_state.get("intent", "unknown")
            execution_path = final_state.get("execution_path", [])

            # SSE partial 流式输出（按段落拆分）
            if final_output:
                # 将长文本按语义分段推送
                segments = re.split(r'([\n]{2,}|(?<=。)|(?<=！)|(?<=？))', final_output)
                for seg in segments:
                    if seg:
                        yield f"event: partial\ndata: {json.dumps({'text': seg}, ensure_ascii=False)}\n\n"
                        await asyncio.sleep(0.05)  # 小延迟模拟逐字效果

            # done 事件（回传 session_id，前端新会话时保存以便续接）
            yield f"event: done\ndata: {json.dumps({'intent': intent, 'session_id': session_id}, ensure_ascii=False)}\n\n"

            # 追加助手回复到历史
            _append_message(session_id, "assistant", final_output, intent)

        except Exception as e:
            logger.error(f"chat_stream 错误: {e}", exc_info=True)
            yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/memory/sessions")
async def list_sessions():
    """列出所有对话会话。"""
    return {"sessions": _list_sessions()}


@app.get("/memory/sessions/{session_id}")
async def get_session(session_id: str):
    """获取单个会话详情。"""
    data = _load_session(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return data


@app.delete("/memory/sessions")
async def clear_all_sessions():
    """清空所有对话记录。"""
    for p in _MEMORY_DIR.glob("*.json"):
        p.unlink()
    return {"status": "ok", "message": "已清空所有会话"}


@app.delete("/memory/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除单个会话。"""
    p = _session_file(session_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail="会话不存在")
    p.unlink()
    return {"status": "ok"}

if __name__ == '__main__':
    import uvicorn

    uvicorn.run("langgraph_demo.app:app", host="0.0.0.0", port=8000)
