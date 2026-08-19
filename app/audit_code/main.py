from fastapi import FastAPI, UploadFile, File, HTTPException
import uvicorn
import asyncio
from crew import run_code_review
from dotenv import load_dotenv
from schemas import ReviewRequest, ReviewResponse,AgentInfo, AuditResult, Usage
from db import get_paper_code_chunk
load_dotenv()
app = FastAPI(title="智能体代码审查 API", description="基于 FastAPI 和 CrewAI 的代码审查接口")

@app.post("/api/review", response_model=ReviewResponse,summary="接收Orchestrator的代码审查请求")
async def process_paper_chunk(request:ReviewRequest):
   """
   接受来自中枢组传来的JSON数据，提取代码片段，智能体处理
   """
   try:
       paper_id = request.metadata.paper_id
       chunk_id = request.metadata.chunk_id
       section_name = "code"  # 假设我们只审查 section_name 为 "code" 的部分
       # 1.提取payload核心代码
       code_content = get_paper_code_chunk(paper_id, section_name, chunk_id)
       # 容错拦截：如果数据库里没查到对应切片，直接抛出 404 错误终止审查
       if not code_content or not code_content.strip():
            raise ValueError(f"在数据库中未找到目标代码片段！(paper_id: {paper_id}, chunk_id: {chunk_id})")
       # 2.调用crewai
       agent_output = run_code_review(code_content)
       # 3.返回JSON格式数据
       response = ReviewResponse(
           request_id=request.request_id,
           agent_info=AgentInfo(name="Code_Review_Agent", version="v1.0"),
           result = agent_output["result"],
           usage = agent_output["usage"]
       )
       return response
   except ValueError as ve:
        # 针对查不到数据的业务逻辑错误，返回 404
        raise HTTPException(status_code=404, detail=str(ve))
   except Exception as e:
        import traceback
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Agent内部执行错误：{str(e)}")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
