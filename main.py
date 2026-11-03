"""
main.py
系统主入口
连接统一 MCPAdapter（默认同进程，无需单独启动 SSE 服务）→ 运行工作流
"""
import asyncio

from MCP.config.settings import settings
from MCP.core.logger import get_logger
from MCP.integration.tool_adapter import get_mcp_adapter
from MCP.workflow.graph import TravelPlanWorkflow

logger = get_logger(__name__)


async def main():
    """主函数：系统入口"""
    logger.info("=" * 60)
    logger.info(" 旅游助手智能体系统启动")
    logger.info(" MCP 客户端 + 工作流 全链路启动")
    logger.info("=" * 60)

    # 初始化 MCP 适配器
    mcp_adapter = get_mcp_adapter()

    try:
        # 建立 MCP 长连接
        async with mcp_adapter:
            # 获取所有 MCP 工具
            tools = await mcp_adapter.get_langgraph_tools()
            logger.info(f" 成功加载 {len(tools)} 个 MCP 工具")

            # 初始化工作流
            workflow = TravelPlanWorkflow()

            # ======================
            # 测试输入
            # ======================
            async with workflow:
                #user_input = "北京到上海的路线，顺便推荐旅游攻略"
                #user_input = "北京到上海的路线"
                user_input = "到上海的路线"

                logger.info(f"🗣️ 用户输入: {user_input}")

                # 执行工作流
                final_state = await workflow.run(user_input)
                # 输出最终回答
                logger.info("\n" + "=" * 60)
                logger.info(" 智能体回答:")
                logger.info(final_state.get("final_output", "未生成回答"))
                logger.info("=" * 60)

    except Exception as e:
        logger.error(f" 系统运行出错: {str(e)}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
