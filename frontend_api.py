"""
Frontend Backend API - FastAPI服务
提供旅游助手智能体的REST API接口（支持流式输出）
"""

import json
import os
import asyncio
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, AsyncGenerator
from contextlib import asynccontextmanager

from MCP.config.settings import settings
from MCP.core.logger import get_logger
from MCP.integration.tool_adapter import get_mcp_adapter
from MCP.workflow.graph import TravelPlanWorkflow
from MCP.tool_amap.client import UnifiedAMapClient
from memory import memory_router, get_memory_manager

logger = get_logger(__name__)

# ==================== 请求/响应模型 ====================

class ChatRequest(BaseModel):
    """聊天请求模型"""
    message: str
    session_id: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None


class ChatResponse(BaseModel):
    """聊天响应模型"""
    success: bool
    answer: str
    intent: Optional[str] = None
    execution_path: Optional[list] = None
    error: Optional[str] = None
    # 数据质量标注（DEF-04）：仅 travel 意图有值
    data_quality: Optional[str] = None
    is_realtime: Optional[bool] = None
    warnings: Optional[list] = None


# ==================== 全局工作流 ====================

workflow_instance: Optional[TravelPlanWorkflow] = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用生命周期管理"""
    global workflow_instance
    try:
        logger.info("正在初始化工作流...")

        mcp_adapter = get_mcp_adapter()
        workflow_instance = TravelPlanWorkflow()

        await workflow_instance.__aenter__()

        logger.info("工作流初始化完成")
        yield
    except Exception as e:
        logger.error(f"工作流初始化失败: {str(e)}")
        raise
    finally:
        if workflow_instance:
            try:
                await workflow_instance.__aexit__(None, None, None)
                logger.info("工作流资源已清理")
            except Exception as e:
                logger.error(f"工作流清理失败: {str(e)}")


# ==================== 创建应用 ====================

app = FastAPI(
    title="旅游助手智能体API",
    description="基于MCP架构的旅游助手智能体系统API接口",
    version="1.2.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册记忆模块路由
app.include_router(memory_router)


# ==================== API 路由 ====================

@app.get("/")
async def root():
    """根路径 - 健康检查"""
    return {
        "status": "ok",
        "message": "旅游助手智能体API服务运行中",
        "version": "1.2.0",
    }


@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {
        "status": "healthy" if workflow_instance else "unhealthy",
        "workflow_initialized": workflow_instance is not None,
    }


@app.get("/location")
async def get_location(lat: float = 0, lng: float = 0):
    """
    逆地理编码：接收浏览器 GPS 坐标，返回城市名（替代高德 IP 定位）
    浏览器端调用 navigator.geolocation.getCurrentPosition() 获取精确坐标后传参
    """
    if not lat or not lng:
        return {"status": "error", "error": "缺少坐标参数", "city": "当前位置"}
    try:
        amap = UnifiedAMapClient()
        location_str = f"{lng},{lat}"
        result = await amap.regeocode(location_str)
        if result and result.get("status") == "success":
            # 防御：即使上游 SDK 返回了 list/None，也不能拼出「[]东城区」（DEF-02）
            city = result.get("city")
            district = result.get("district")
            city = city if isinstance(city, str) else ""
            district = district if isinstance(district, str) else ""
            formatted = result.get("formatted_address", "")
            city_name = f"{city}{district}" if district else city
            logger.info(f"📡 浏览器GPS定位: ({lat},{lng}) → {city_name}")
            return {
                "status": "success",
                "city": city_name or city or "当前位置",
                "lat": lat,
                "lng": lng,
                "formatted_address": formatted,
                "accuracy": "browser_gps",
            }
        return {"status": "error", "error": "逆地理编码失败", "city": "当前位置"}
    except Exception as e:
        logger.error(f"GPS定位转换失败: {e}")
        return {"status": "error", "error": str(e), "city": "当前位置"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """聊天接口（一次性返回）"""
    global workflow_instance

    if not workflow_instance:
        raise HTTPException(status_code=500, detail="工作流未初始化")

    try:
        user_input = request.message.strip()
        if not user_input:
            raise HTTPException(status_code=400, detail="消息不能为空")

        logger.info(f"收到用户输入: {user_input}")

        # GPS坐标独立传入，不混入user_input（避免干扰意图识别）
        gps_coords = None
        if request.gps_lat is not None and request.gps_lng is not None:
            gps_coords = f"{request.gps_lat},{request.gps_lng}"
            logger.info(f"前端传入GPS坐标: ({request.gps_lat}, {request.gps_lng})")

        final_state = await workflow_instance.run(user_input, gps_coords=gps_coords)

        answer = final_state.get("final_output") or final_state.get("answer", "抱歉，我无法处理您的请求")
        intent = final_state.get("intent", "unknown")
        execution_path = final_state.get("execution_path", [])
        error = final_state.get("error")

        logger.info(f"智能体回复(长度:{len(answer)}) 意图:{intent} 路径:{execution_path}")

        # 保存到记忆模块
        try:
            session_id = request.session_id or "default"
            mgr = get_memory_manager()
            mgr.save_exchange(session_id, user_input, answer, intent)
        except Exception as e:
            logger.warning(f"保存对话失败: {e}")

        return ChatResponse(
            success=error is None,
            answer=answer,
            intent=intent,
            execution_path=execution_path,
            error=error,
            data_quality=final_state.get("data_quality"),
            is_realtime=final_state.get("is_realtime"),
            warnings=final_state.get("warnings"),
        )

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"处理请求时发生错误: {str(e)}"
        logger.error(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    聊天接口（SSE 流式输出）
    逐步推送：step（阶段消息）→ partial（内容片段）→ done（完成）
    """
    global workflow_instance

    if not workflow_instance:
        raise HTTPException(status_code=500, detail="工作流未初始化")

    user_input = request.message.strip()
    if not user_input:
        raise HTTPException(status_code=400, detail="消息不能为空")

    logger.info(f"收到流式请求: {user_input}")

    # GPS坐标独立传入，不混入user_input（避免干扰意图识别）
    gps_coords = None
    if request.gps_lat is not None and request.gps_lng is not None:
        gps_coords = f"{request.gps_lat},{request.gps_lng}"
        logger.info(f"前端传入GPS坐标: ({request.gps_lat}, {request.gps_lng})")

    async def event_generator():
        """SSE 事件生成器 — 真正的流式推送"""
        queue: asyncio.Queue = asyncio.Queue()
        partial_accumulated = ""  # 累积 partial 文本用于保存

        async def on_message(msg_type: str, data):
            """工作流回调：推送到队列"""
            nonlocal partial_accumulated
            if msg_type == "step":
                await queue.put(f"event: step\ndata: {json.dumps({'message': data}, ensure_ascii=False)}\n\n")
            elif msg_type == "partial":
                partial_accumulated += data
                await queue.put(f"event: partial\ndata: {json.dumps({'text': data}, ensure_ascii=False)}\n\n")
            elif msg_type == "done":
                await queue.put(f"event: done\ndata: {json.dumps(data, ensure_ascii=False)}\n\n")
                # 保存对话到记忆模块
                try:
                    mgr = get_memory_manager()
                    final_text = partial_accumulated or data.get("final_text", "")
                    mgr.save_exchange("default", user_input, final_text, data.get("intent"))
                except Exception as e:
                    logger.warning(f"记忆保存失败: {e}")
                await queue.put(None)  # 结束信号
            elif msg_type == "error":
                await queue.put(f"event: error\ndata: {json.dumps({'error': data}, ensure_ascii=False)}\n\n")
                await queue.put(None)  # 结束信号

        async def run_workflow():
            try:
                await workflow_instance.run_stream(user_input, on_message, gps_coords=gps_coords)
            except Exception as e:
                logger.error(f"流式执行异常: {e}")
                await queue.put(f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n")
                await queue.put(None)

        task = asyncio.create_task(run_workflow())

        # 从队列中逐个 yield，实现真正流式
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event

        task.cancel()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/intents")
async def get_intents():
    """获取支持的意图类型"""
    return {
        "intents": [
            {
                "id": "chat",
                "name": "💬 普通聊天",
                "description": "日常闲聊对话，回答一般性问题",
                "example": "你好、介绍一下自己",
            },
            {
                "id": "route",
                "name": "🗺️ 路线规划",
                "description": "查询两地之间的驾车/公交/火车路线，基于高德地图API",
                "example": "北京到上海的路线、从杭州到南京怎么走",
            },
            {
                "id": "search",
                "name": "🔍 旅游搜索",
                "description": "搜索旅游景点、美食、攻略信息，基于Tavily深度搜索引擎",
                "example": "杭州西湖有什么好玩的、西安美食推荐",
            },
            {
                "id": "travel",
                "name": "🌍 综合规划",
                "description": "结合路线规划 + 旅游攻略 + RAG智能问答的完整旅行方案",
                "example": "北京到上海的路线，顺便推荐旅游攻略",
            },
        ]
    }


# ==================== 静态文件服务 ====================

_web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
if os.path.isdir(_web_dir):
    app.mount("/ui", StaticFiles(directory=_web_dir, html=True), name="web")
    logger.info(f"前端静态文件已挂载: {_web_dir} → /ui")


# ==================== 启动入口 ====================

if __name__ == "__main__":
    import uvicorn

    host = getattr(settings, "SERVER_HOST", "0.0.0.0")
    port = getattr(settings, "SERVER_PORT", 8000)

    logger.info(f"启动FastAPI服务: http://{host}:{port}")
    logger.info(f"前端界面: http://{host}:{port}/ui")

    uvicorn.run(
        "frontend_api:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )
