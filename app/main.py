from fastapi import FastAPI,Depends, Request, Response,status,HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import logging
from contextlib import asynccontextmanager
import asyncio
import os
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.core.datetime_utils import BEIJING_TZ

from app.api import auth,user
from app.core.exception_handler import register_exception_handlers
from app.core.log_middleware import LogMiddleware
from app.core.config import settings
from app.db.base import engine,Base,redis,AsyncSessionLocal
from app.core.cleantask import create_cleanup_task

# 确保 logs 文件夹存在
os.makedirs("logs", exist_ok=True)

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/app.log", encoding="utf-8"),  # 写入到文件
        logging.StreamHandler()  # 控制台同时输出
    ]
)
logger = logging.getLogger(__name__)

# 全局变量存储清理任务和调度器
cleanup_task = None
scheduler = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global cleanup_task, scheduler

    try:
        # 仅检测 base.py 中的全局 Redis 连接，不在此处重复创建实例
        try:
            await asyncio.wait_for(redis.ping(), timeout=2)
            logger.info(" Redis connected successfully")
        except Exception as e:
            logger.critical(f" Redis connection failed: {e}")
            raise

        # 初始化数据库表（必要时）
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # 启动 APScheduler 并注册候补队列同步任务（强制使用北京时间时区）
        scheduler = AsyncIOScheduler(timezone=BEIJING_TZ)
        # TODO: 添加具体定时任务
        scheduler.start()
        logger.info("✓ APScheduler 已启动")

        logger.info(" Application startup complete")
        yield  # 应用正常运行

    except Exception as e:
        logger.critical(f" Application startup failed: {e}")
        raise

    finally:
        logger.info("✓ 缺勤检测定时任务已停止")
        
        # 停止 APScheduler
        if scheduler and scheduler.running:
            scheduler.shutdown()
            logger.info("✓ APScheduler 已停止")
        
        # 清理 Redis
        try:
            await asyncio.wait_for(redis.aclose(), timeout=3)
            logger.info(" Redis connection closed")
        except asyncio.TimeoutError:
            logger.warning(" Redis close timed out")
        except Exception as e:
            logger.error(f"Redis close failed: {e}")

        # 关闭数据库引擎（可选）
        try:
            await engine.dispose()
            logger.info("DB engine disposed")
        except Exception as e:
            logger.warning(f"DB engine dispose failed: {e}")

        # 停止清理任务（如果有）
        if cleanup_task:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                logger.info(" Cleanup task cancelled")

        logger.info("Application shutdown complete")

app = FastAPI(title=settings.PROJECT_NAME,lifespan=lifespan)

# 挂载静态文件目录 (用于访问上传的图片)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
        
# 注册全局异常处理器
register_exception_handlers(app)

app.add_middleware(
    LogMiddleware
)

#中间件解决跨域(后续需扩展)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5000", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


#引用子路由
try:
    app.include_router(router=auth.router, prefix="/auth", tags=["authentication"])
    app.include_router(router=user.router, prefix="/user", tags=["user-related"])
    logger.info("All routers registered successfully")
except Exception as e:
    logger.error(f"Failed to register routers: {e}", exc_info=True)
    raise

#默认
@app.get("/")
async def root():
    return {"message": "Welcome to HONGUO-BUDDY API"}