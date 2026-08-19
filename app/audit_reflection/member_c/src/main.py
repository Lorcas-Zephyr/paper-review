from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from src.database import Database
from src.reflection_agent import ReflectionJudgeAgent
from src.models import ReflectionTask
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db = Database()
agent = ReflectionJudgeAgent(db)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时连接数据库
    await db.connect()
    yield
    # 关闭时清理
    await db.close()

app = FastAPI(lifespan=lifespan)

@app.post("/reflect")
async def reflect(task: ReflectionTask):
    try:
        result = await agent.process_reflection_task(task.dict())
        return result
    except Exception as e:
        logger.exception("Reflection failed")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "ok"}
