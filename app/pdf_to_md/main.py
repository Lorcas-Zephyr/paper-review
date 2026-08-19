"""
增强版PDF转Markdown API
集成新的PDF解析API，返回完整结果集
"""
import os
import json
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
import shutil
import traceback

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:
    pass

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from local_model_config import MINERU_CONFIG_FILE, enable_offline_model_mode

enable_offline_model_mode()

from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

# 创建FastAPI应用
app = FastAPI(
    title="增强版PDF转Markdown API",
    description="集成高性能PDF解析API，支持Markdown、content_list.json、layout.pdf输出",
    version="2.0.0"
)

# 启用CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 配置
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True)

# Local MinerU configuration
MINERU_BACKEND = os.getenv("MINERU_BACKEND", "pipeline")
MINERU_PARSE_METHOD = os.getenv("MINERU_PARSE_METHOD", "auto")
MINERU_FORMULA_ENABLE = os.getenv("MINERU_FORMULA_ENABLE", "true").lower() == "true"
MINERU_TABLE_ENABLE = os.getenv("MINERU_TABLE_ENABLE", "true").lower() == "true"
MINERU_MODEL_SOURCE = os.getenv("MINERU_MODEL_SOURCE", "local")

try:
    from mineru.cli.common import do_parse as _mineru_do_parse
    MINERU_AVAILABLE = True
    MINERU_IMPORT_ERROR = None
except Exception as _exc:  # Optional local model dependency.
    _mineru_do_parse = None
    MINERU_AVAILABLE = False
    MINERU_IMPORT_ERROR = str(_exc)

def _run_local_mineru(file_path: str, output_dir: Path, config: Dict[str, Any]) -> None:
    """Run MinerU in-process.  This function is called from a worker thread."""
    if _mineru_do_parse is None:
        raise RuntimeError(
            "MinerU local runtime is unavailable. Install mineru[core] and its model dependencies. "
            f"Import error: {MINERU_IMPORT_ERROR}"
        )

    with open(file_path, "rb") as pdf_file:
        _mineru_do_parse(
            output_dir=str(output_dir),
            pdf_file_names=[Path(file_path).name],
            pdf_bytes_list=[pdf_file.read()],
            p_lang_list=config.get("lang_list", ["ch"]),
            backend=config.get("backend", MINERU_BACKEND),
            parse_method=config.get("parse_method", MINERU_PARSE_METHOD),
            formula_enable=config.get("formula_enable", MINERU_FORMULA_ENABLE),
            table_enable=config.get("table_enable", MINERU_TABLE_ENABLE),
        )


async def call_local_mineru(file_path: str, output_dir: Path, config: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a PDF locally and normalize MinerU's generated files."""
    print(f"[mineru] Parsing locally: {file_path}")
    try:
        await asyncio.to_thread(_run_local_mineru, file_path, output_dir, config)
    except Exception as exc:
        traceback.print_exc()
        return {"success": False, "error": f"Local MinerU parsing failed: {exc}"}

    generated_files = [
        {
            "name": str(path.relative_to(output_dir)),
            "path": str(path),
            "size": path.stat().st_size,
        }
        for path in output_dir.rglob("*")
        if path.is_file()
    ]
    file_result = process_extracted_files(generated_files, output_dir)
    if not file_result["markdown"]:
        return {
            "success": False,
            "error": "Local MinerU completed without producing a Markdown file",
            "files": file_result,
        }
    return {"success": True, "files": file_result}


def process_extracted_files(files: List[Dict[str, Any]], output_dir: Path) -> Dict[str, Any]:
    """
    处理提取的文件，查找需要的文件

    Args:
        files: 提取的文件列表
        output_dir: 输出目录

    Returns:
        处理结果
    """
    result = {
        'markdown': None,
        'content_list': None,
        'layout_pdf': None,
        'other_files': []
    }

    for file_info in files:
        file_path = Path(file_info['path'])

        # 检查文件类型
        if file_path.suffix.lower() in ['.md', '.markdown']:
            # Markdown文件
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    result['markdown'] = {
                        'content': content,
                        'path': str(file_path),
                        'size': len(content)
                    }
            except:
                pass

        elif file_path.suffix.lower() == '.json' and 'content' in file_path.name.lower():
            # content_list.json文件
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    result['content_list'] = {
                        'content': json.load(f),
                        'path': str(file_path),
                        'size': os.path.getsize(file_path)
                    }
            except:
                pass

        elif file_path.suffix.lower() == '.pdf' and 'layout' in file_path.name.lower():
            # layout.pdf文件
            result['layout_pdf'] = {
                'path': str(file_path),
                'size': os.path.getsize(file_path)
            }

        else:
            # 其他文件
            result['other_files'].append({
                'name': file_path.name,
                'path': str(file_path),
                'size': os.path.getsize(file_path)
            })

    return result

async def parse_pdf_enhanced(pdf_path: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    增强版PDF解析函数

    Args:
        pdf_path: PDF文件路径
        config: 解析配置

    Returns:
        解析结果
    """
    # 默认配置
    default_config = {
        # 'lang_list': ['ch'],
        'backend': MINERU_BACKEND,
        'parse_method': MINERU_PARSE_METHOD,
        'formula_enable': MINERU_FORMULA_ENABLE,
        'table_enable': MINERU_TABLE_ENABLE,
        'return_md': True,
        'return_content_list': True,
        'response_format_zip': False
    }

    # 合并配置
    if config:
        default_config.update(config)

    # 创建输出目录
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = OUTPUT_DIR / f"parse_{timestamp}_{Path(pdf_path).stem}"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[file] 输出目录: {output_dir}")

    # MinerU runs locally; keep the old response-processing code below only
    # for source compatibility with clients that imported these helpers.
    local_result = await call_local_mineru(pdf_path, output_dir, default_config)
    if not local_result.get("success"):
        return {
            "success": False,
            "error": local_result.get("error", "Local MinerU parsing failed"),
            "output_dir": str(output_dir),
            "files": local_result.get("files", {}),
        }

    return {
        "success": True,
        "output_dir": str(output_dir),
        "files": local_result["files"],
    }

async def extract_markdown_from_other_files(output_dir: Path) -> Optional[str]:
    """
    从其他文件中提取Markdown内容

    Args:
        output_dir: 输出目录

    Returns:
        Markdown内容
    """
    # 查找可能的Markdown文件
    for file_path in output_dir.glob("*"):
        if file_path.suffix.lower() in ['.txt', '.text', '.html']:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 简单判断是否为Markdown
                    if any(marker in content for marker in ['# ', '## ', '**', '*', '```']):
                        return content
            except:
                continue
    return None

def create_layout_pdf_from_original(pdf_path: str, output_dir: Path) -> str:
    """
    从原始PDF创建layout.pdf（如果API没有提供）

    Args:
        pdf_path: 原始PDF路径
        output_dir: 输出目录

    Returns:
        layout.pdf路径
    """
    layout_path = output_dir / "layout.pdf"

    try:
        # 简单复制原始PDF作为layout.pdf
        shutil.copy2(pdf_path, layout_path)
        return str(layout_path)
    except Exception as e:
        print(f"[err] 创建layout.pdf失败: {str(e)}")
        return None

@app.get("/")
async def root():
    """根路径"""
    return {
        "service": "增强版PDF转Markdown API",
        "version": "2.0.0",
        "description": "集成高性能PDF解析API，支持Markdown、content_list.json、layout.pdf输出",
        "endpoints": {
            "GET /": "API信息",
            "GET /health": "健康检查",
            "POST /convert/upload": "上传PDF并解析",
            "POST /convert/from-path": "通过文件路径解析",
            "GET /download/{task_id}/{file_type}": "下载结果文件"
        },
        "parser": {
            "engine": "mineru",
            "mode": "local",
            "backend": MINERU_BACKEND,
            "parse_method": MINERU_PARSE_METHOD,
            "model_source": MINERU_MODEL_SOURCE,
            "config_file": str(MINERU_CONFIG_FILE),
        }
    }

@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy" if MINERU_AVAILABLE else "degraded",
        "timestamp": datetime.now().isoformat(),
        "mineru": {
            "available": MINERU_AVAILABLE,
            "backend": MINERU_BACKEND,
            "parse_method": MINERU_PARSE_METHOD,
            "model_source": MINERU_MODEL_SOURCE,
            "config_file": str(MINERU_CONFIG_FILE),
            "import_error": MINERU_IMPORT_ERROR,
        },
    }

@app.post("/convert/upload")
async def convert_from_upload(
    file: UploadFile = File(...),
    lang_list: str = Form("ch"),
    backend: str = Form("pipeline"),
    formula_enable: bool = Form(True),
    table_enable: bool = Form(True)
):
    """
    方式1：上传PDF文件并解析
    """
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="只支持PDF文件")

    # 生成唯一文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"upload_{timestamp}_{file.filename}"
    file_path = UPLOAD_DIR / filename

    try:
        # 保存上传的文件
        content = await file.read()
        file_size = len(content)

        with open(file_path, "wb") as f:
            f.write(content)

        # 解析配置
        config = {
            'lang_list': lang_list.split(',') if ',' in lang_list else [lang_list],
            'backend': backend,
            'parse_method': MINERU_PARSE_METHOD,
            'formula_enable': formula_enable,
            'table_enable': table_enable,
            'return_md': True,
            'return_content_list': True,
            'response_format_zip': False
        }

        # 解析PDF
        result = await parse_pdf_enhanced(str(file_path), config)

        if not result["success"]:
            file_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=result.get("error", "解析失败"))

        # 确保有layout.pdf
        if not result['files']['layout_pdf']:
            layout_path = create_layout_pdf_from_original(str(file_path), Path(result['output_dir']))
            if layout_path:
                result['files']['layout_pdf'] = {
                    'path': layout_path,
                    'size': os.path.getsize(layout_path)
                }

        # 构建响应
        response_data = {
            "success": True,
            "filename": file.filename,
            "file_size": file_size,
            "mode": "direct_upload",
            "timestamp": datetime.now().isoformat(),
            "output_dir": result["output_dir"],
            "files": {
                "markdown": bool(result['files']['markdown']),
                "content_list": bool(result['files']['content_list']),
                "layout_pdf": bool(result['files']['layout_pdf']),
                "other_files_count": len(result['files']['other_files'])
            },
            "download_urls": {
                "markdown": f"/download/{Path(result['output_dir']).name}/markdown",
                "content_list": f"/download/{Path(result['output_dir']).name}/content_list",
                "layout_pdf": f"/download/{Path(result['output_dir']).name}/layout_pdf"
            }
        }

        # 添加Markdown预览
        if result['files']['markdown']:
            response_data["markdown_preview"] = result['files']['markdown']['content'][:500] + "..." if len(result['files']['markdown']['content']) > 500 else result['files']['markdown']['content']

        return JSONResponse(content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")

@app.post("/convert/from-path")
async def convert_from_path(data: dict = Body(...)):
    """
    方式2：通过文件路径解析
    """
    file_path = data.get("file_path")
    config = data.get("config", {})

    if not file_path:
        raise HTTPException(status_code=400, detail="必须提供file_path参数")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")

    if not file_path.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="只支持PDF文件")

    try:
        # 解析PDF
        result = await parse_pdf_enhanced(file_path, config)

        if not result["success"]:
            raise HTTPException(status_code=500, detail=result.get("error", "解析失败"))

        # 确保有layout.pdf
        if not result['files']['layout_pdf']:
            layout_path = create_layout_pdf_from_original(file_path, Path(result['output_dir']))
            if layout_path:
                result['files']['layout_pdf'] = {
                    'path': layout_path,
                    'size': os.path.getsize(layout_path)
                }

        # 构建响应 - 修改为与前端匹配的格式
        response_data = {
            "success": True,
            "filename": os.path.basename(file_path),
            "file_path": file_path,
            "mode": "direct_path",
            "timestamp": datetime.now().isoformat(),
            "output_dir": result["output_dir"],
            "files": {
                "markdown": bool(result['files']['markdown']),
                "content_list": bool(result['files']['content_list']),
                "layout_pdf": bool(result['files']['layout_pdf']),
                "other_files_count": len(result['files']['other_files'])
            },
            "download_urls": {
                "markdown": f"/download/{Path(result['output_dir']).name}/markdown",
                "content_list": f"/download/{Path(result['output_dir']).name}/content_list",
                "layout_pdf": f"/download/{Path(result['output_dir']).name}/layout_pdf"
            }
        }
        # 添加Markdown内容
        if result['files']['markdown']:
            response_data["markdown"] = result['files']['markdown']['content']
            response_data["markdown_length"] = len(result['files']['markdown']['content'])

        return JSONResponse(content=response_data)

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")

@app.get("/download/{task_id}/{file_type}")
async def download_result_file(task_id: str, file_type: str):
    """
    下载结果文件
    """
    # 查找任务目录
    task_dir = None
    for dir_path in OUTPUT_DIR.iterdir():
        if dir_path.is_dir() and dir_path.name.startswith(f"parse_{task_id}") or dir_path.name == task_id:
            task_dir = dir_path
            break

    if not task_dir or not task_dir.exists():
        raise HTTPException(status_code=404, detail=f"任务目录不存在: {task_id}")

    # 根据文件类型查找文件
    file_path = None

    if file_type == "markdown":
        for ext in ['.md', '.markdown', '.txt']:
            for file in task_dir.glob(f"*{ext}"):
                if 'extracted' in file.name or 'output' in file.name or 'md_content' in file.name:
                    file_path = file
                    break
            if file_path:
                break

    elif file_type == "content_list":
        for file in task_dir.glob("*content_list*"):
            if file.suffix.lower() == '.json':
                file_path = file
                break

    elif file_type == "layout_pdf":
        for file in task_dir.glob("*layout*"):
            if file.suffix.lower() == '.pdf':
                file_path = file
                break
        # 如果没有layout.pdf，使用第一个PDF
        if not file_path:
            for file in task_dir.glob("*.pdf"):
                file_path = file
                break

    if not file_path or not file_path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_type}")

    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type='application/octet-stream'
    )

@app.get("/list-tasks")
async def list_tasks():
    """列出所有任务"""
    tasks = []

    for task_dir in OUTPUT_DIR.iterdir():
        if task_dir.is_dir():
            task_info = {
                "task_id": task_dir.name,
                "created_at": datetime.fromtimestamp(task_dir.stat().st_ctime).isoformat(),
                "files": []
            }

            for file in task_dir.glob("*"):
                if file.is_file():
                    task_info["files"].append({
                        "name": file.name,
                        "size": file.stat().st_size,
                        "type": file.suffix.lower()
                    })

            tasks.append(task_info)

    return {
        "total_tasks": len(tasks),
        "tasks": tasks
    }

@app.delete("/task/{task_id}")
async def delete_task(task_id: str):
    """删除任务目录"""
    task_dir = OUTPUT_DIR / task_id

    if not task_dir.exists():
        raise HTTPException(status_code=404, detail=f"任务目录不存在: {task_id}")

    try:
        shutil.rmtree(task_dir)
        return {"success": True, "message": f"任务 {task_id} 已删除"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除失败: {str(e)}")

if __name__ == "__main__":
    print("增强版PDF转Markdown API启动中...")
    print(f"上传目录: {UPLOAD_DIR.absolute()}")
    print(f"输出目录: {OUTPUT_DIR.absolute()}")
    print(f"服务地址: http://localhost:8002")
    print(f"MinerU: local ({MINERU_BACKEND}/{MINERU_PARSE_METHOD})")
    print(f"API文档: http://localhost:8002/docs")
    print("")
    print("功能特性:")
    print("   1. 集成高性能PDF解析API")
    print("   2. 返回Markdown、content_list.json、layout.pdf")
    print("   3. 支持ZIP格式解析")
    print("   4. 完整的文件管理")

    uvicorn.run(
        "main:app",  # 保存为 enhanced_pdf_api.py
        host="0.0.0.0",
        port=8002,
        reload=True
    )
