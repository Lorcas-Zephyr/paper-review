"""
极简PDF上传API
只做一件事：上传PDF，返回文件路径
"""
import os
import uuid
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional
import requests
from fastapi import FastAPI, UploadFile, File, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import PyPDF2
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 配置
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 200 * 1024 * 1024))  # 默认200MB
ALLOWED_TYPES = ["application/pdf", "application/octet-stream"]
API_PORT = int(os.getenv("PORT", 5000))

# 创建FastAPI应用
app = FastAPI(
    title="极简PDF上传API",
    description="上传PDF文件，返回文件路径",
    version="1.0.0"
)

# 启用CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境请指定具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 数据模型
class UploadResponse(BaseModel):
    success: bool
    file_path: str
    file_name: str
    file_size: int
    file_url: str
    message: str = "上传成功"

class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    code: int

# 验证PDF文件
def is_valid_pdf(file_path: Path) -> bool:
    """验证是否为有效的PDF文件"""
    try:
        with open(file_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            # 尝试读取第一页
            if len(reader.pages) > 0:
                _ = reader.pages[0]
            return True
    except Exception:
        return False

# 生成唯一文件名
def generate_filename(original_filename: str) -> str:
    """生成唯一文件名：时间戳+UUID+原文件名"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = str(uuid.uuid4())[:8]
    ext = Path(original_filename).suffix.lower()
    if not ext or ext != '.pdf':
        ext = '.pdf'
    return f"{timestamp}_{unique_id}{ext}"

# 健康检查
@app.get("/")
async def root():
    return {
        "message": "PDF上传API服务运行中",
        "version": "1.0.0",
        "upload_dir": str(UPLOAD_DIR.absolute()),
        "max_file_size": f"{MAX_FILE_SIZE / 1024 / 1024}MB"
    }

@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

# 上传PDF接口
@app.post("/api/upload", response_model=UploadResponse)
async def upload_pdf(file: UploadFile = File(...)):
    """
    上传PDF文件

    参数:
    - file: PDF文件

    返回:
    - 文件在服务器上的绝对路径
    - 文件URL
    """

    # 1. 验证文件类型
    if file.content_type not in ALLOWED_TYPES:
        # 也检查文件扩展名
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="只允许上传PDF文件"
            )

    # 2. 读取文件内容（验证大小）
    try:
        contents = await file.read()
        file_size = len(contents)

        if file_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"文件大小超过限制 ({MAX_FILE_SIZE / 1024 / 1024}MB)"
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"读取文件失败: {str(e)}"
        )

    # 3. 生成唯一文件名
    filename = generate_filename(file.filename)
    file_path = UPLOAD_DIR / filename

    try:
        # 4. 保存文件
        await file.seek(0)  # 重置文件指针
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 5. 验证PDF文件
        if not is_valid_pdf(file_path):
            # 删除无效文件
            file_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="上传的文件不是有效的PDF"
            )

        # 6. 构建响应
        absolute_path = str(file_path.absolute())

        # 生成访问URL（假设通过Nginx代理到/uploads目录）
        file_url = f"/uploads/{filename}"

        return UploadResponse(
            success=True,
            file_path=absolute_path,
            file_name=filename,
            file_size=file_size,
            file_url=file_url,
            message=f"文件上传成功，大小: {file_size / 1024 / 1024:.2f}MB"
        )

    except HTTPException:
        raise
    except Exception as e:
        # 清理可能已创建的文件
        file_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"文件保存失败: {str(e)}"
        )

# 添加缓存检查端点
@app.get("/api/check-markdown-cache")
async def check_markdown_cache(filename: str, filepath: str = None):
    """
    检查是否存在已转换的Markdown缓存
    """
    try:
        # 1. 从文件名生成可能的缓存文件名
        import re
        from pathlib import Path

        # 移除文件扩展名
        base_name = Path(filename).stem

        # 2. 查找所有可能的缓存目录
        cache_dirs = []

        # 查找uploads目录中对应的缓存
        uploads_dir = Path("uploads")
        if uploads_dir.exists():
            for item in uploads_dir.glob("*.pdf"):
                if item.stem == base_name:
                    # 找到对应的PDF文件
                    # 查找对应的Markdown缓存
                    cache_dirs.append(item)

        # 3. 在outputs目录中查找最新的Markdown文件
        outputs_dir = Path("outputs")
        if outputs_dir.exists():
            # 查找所有包含文件名的目录
            for dir_path in outputs_dir.iterdir():
                if dir_path.is_dir() and base_name in dir_path.name:
                    # 在这个目录中查找Markdown文件
                    for file in dir_path.glob("*.md"):
                        with open(file, 'r', encoding='utf-8') as f:
                            content = f.read()
                            return {
                                "success": True,
                                "filename": filename,
                                "markdown_content": content,
                                "cache_path": str(file),
                                "cache_size": len(content)
                            }

        # 4. 如果没有找到，尝试直接从文件路径推断8002端口的输出目录
        if filepath and "uploads" in filepath:
            # 提取时间戳部分
            pattern = r'uploads\\(\d{8}_\d{6}_[a-f0-9]+)\.pdf'
            match = re.search(pattern, filepath.replace('/', '\\'))
            if match:
                file_id = match.group(1)
                # 在8002端口的输出目录中查找
                potential_cache_dir = Path(f"outputs/parse_{file_id}_{base_name}")
                if potential_cache_dir.exists():
                    for file in potential_cache_dir.glob("*.md"):
                        with open(file, 'r', encoding='utf-8') as f:
                            content = f.read()
                            return {
                                "success": True,
                                "filename": filename,
                                "markdown_content": content,
                                "cache_path": str(file),
                                "cache_size": len(content)
                            }

        return {
            "success": False,
            "message": "未找到缓存文件"
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"检查缓存失败: {str(e)}"
        }

@app.post("/api/save-markdown-cache")
async def save_markdown_cache(data: dict):
    """
    保存Markdown缓存
    """
    try:
        filename = data.get("filename")
        markdown_content = data.get("markdown_content")

        if not filename or not markdown_content:
            return {"success": False, "error": "参数缺失"}

        # 创建缓存目录
        cache_dir = Path("markdown_cache")
        cache_dir.mkdir(exist_ok=True)

        # 生成缓存文件名
        import hashlib
        import json
        from datetime import datetime

        # 使用文件名和内容生成唯一标识
        cache_key = f"{filename}_{len(markdown_content)}"
        cache_hash = hashlib.md5(cache_key.encode()).hexdigest()[:8]

        cache_file = cache_dir / f"{Path(filename).stem}_{cache_hash}.md"

        # 保存Markdown文件
        with open(cache_file, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        # 保存元数据
        metadata = {
            "filename": filename,
            "cache_file": str(cache_file),
            "saved_at": datetime.now().isoformat(),
            "size": len(markdown_content)
        }

        metadata_file = cache_dir / f"{Path(filename).stem}_{cache_hash}.json"
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        return {
            "success": True,
            "message": "缓存保存成功",
            "cache_file": str(cache_file),
            "cache_size": len(markdown_content)
        }

    except Exception as e:
        return {
            "success": False,
            "error": f"保存缓存失败: {str(e)}"
        }

# 批量上传接口（可选）
@app.post("/api/upload/batch")
async def upload_multiple_pdfs(files: list[UploadFile] = File(...)):
    """
    批量上传多个PDF文件
    """
    results = []

    for file in files:
        try:
            result = await upload_pdf(file)
            results.append(result.dict())
        except HTTPException as e:
            results.append({
                "success": False,
                "filename": file.filename,
                "error": e.detail
            })

    return {
        "success": True,
        "count": len(results),
        "results": results
    }

# 文件列表接口（可选）
@app.get("/api/files")
async def list_files():
    """
    获取已上传的文件列表
    """
    files = []

    for file_path in UPLOAD_DIR.glob("*.pdf"):
        stats = file_path.stat()
        files.append({
            "name": file_path.name,
            "size": stats.st_size,
            "uploaded_at": datetime.fromtimestamp(stats.st_ctime).isoformat(),
            "path": str(file_path.absolute()),
            "url": f"/uploads/{file_path.name}"
        })

    return {
        "success": True,
        "count": len(files),
        "files": files
    }

@app.post("/api/parse-pdf")
async def parse_pdf_proxy(data: dict):
    """
    代理调用PDF解析API
    """
    try:
        file_path = data.get("file_path")
        config = data.get("config", {})

        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="文件不存在")

        # 1. 读取PDF文件
        with open(file_path, 'rb') as f:
            files = {'files': f}

            # 2. 准备参数
            form_data = {k: str(v) for k, v in config.items()}

            # 3. 调用PDF解析API
            pdf_parse_url = os.getenv("PDF_PARSE_API_URL", "http://127.0.0.1:8000/file_parse")
            response = requests.post(
                pdf_parse_url,
                files=files,
                data=form_data,
                timeout=300
            )

            if response.status_code == 200:
                # 4. 处理响应
                content_type = response.headers.get('Content-Type', '')

                if 'application/zip' in content_type or config.get('response_format_zip'):
                    # 处理ZIP响应
                    import zipfile
                    import io
                    import json

                    zip_bytes = io.BytesIO(response.content)

                    with zipfile.ZipFile(zip_bytes, 'r') as zip_ref:
                        # 查找Markdown文件
                        md_content = None
                        content_list = None

                        for file_name in zip_ref.namelist():
                            if file_name.endswith('.md'):
                                with zip_ref.open(file_name) as f:
                                    md_content = f.read().decode('utf-8')
                            elif 'content_list' in file_name and file_name.endswith('.json'):
                                with zip_ref.open(file_name) as f:
                                    content_list = json.load(f)

                        if md_content:
                            return {
                                "success": True,
                                "markdown_content": md_content,
                                "content_list": content_list,
                                "file_name": os.path.basename(file_path)
                            }
                        else:
                            raise HTTPException(status_code=500, detail="未找到Markdown内容")
                else:
                    # JSON响应
                    result = response.json()
                    if 'md_content' in result:
                        return {
                            "success": True,
                            "markdown_content": result.get('md_content'),
                            "content_list": result.get('content_list'),
                            "file_name": os.path.basename(file_path)
                        }
                    else:
                        raise HTTPException(status_code=500, detail="API返回格式错误")
            else:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"PDF解析API错误: {response.text}"
                )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析失败: {str(e)}")

# 删除文件接口（可选）
@app.delete("/api/file/{filename}")
async def delete_file(filename: str):
    """
    删除指定文件
    """
    file_path = UPLOAD_DIR / filename

    # 安全验证：确保文件在uploads目录内
    try:
        file_path.relative_to(UPLOAD_DIR)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效的文件名"
        )

    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文件不存在"
        )

    try:
        file_path.unlink()
        return {
            "success": True,
            "message": f"文件 {filename} 删除成功"
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"删除文件失败: {str(e)}"
        )

# 错误处理
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=exc.detail,
            code=exc.status_code
        ).dict()
    )

if __name__ == "__main__":
    import uvicorn

    print(f"PDF上传服务启动中...")
    print(f"上传目录: {UPLOAD_DIR.absolute()}")
    print(f"文件大小限制: {MAX_FILE_SIZE / 1024 / 1024}MB")
    print(f"服务地址: http://localhost:{API_PORT}")
    print(f"API文档: http://localhost:{API_PORT}/docs")

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=API_PORT,
        reload=True
    )
