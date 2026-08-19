"""
增强版PDF转Markdown API
集成新的PDF解析API，返回完整结果集
"""
import os
import json
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
import shutil
import traceback

import requests
import fitz
from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
import uvicorn
from pydantic import BaseModel

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

# PDF解析API配置
PDF_PARSE_API_URL = os.getenv("PDF_PARSE_API_URL", "http://127.0.0.1:8000/file_parse")

class PDFParseRequest(BaseModel):
    """PDF解析请求模型"""
    # lang_list: List[str] = ["ch"]
    backend: str = "hybrid-auto-engine"
    parse_method: str = "auto"
    formula_enable: bool = True
    table_enable: bool = True
    return_md: bool = True
    return_middle_json: bool = False
    return_model_output: bool = False
    return_content_list: bool = True
    return_images: bool = False
    response_format_zip: bool = True
    start_page_id: Optional[int] = None
    end_page_id: Optional[int] = None
    server_url: Optional[str] = None

async def call_pdf_parse_api(file_path: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    调用PDF解析API

    Args:
        file_path: 本地PDF文件路径
        config: API配置参数

    Returns:
        解析结果字典
    """
    print(f"[http] 调用PDF解析API: {PDF_PARSE_API_URL}")
    print(f"[file] 解析文件: {file_path}")

    try:
        # 准备文件
        with open(file_path, 'rb') as f:
            files = {'files': f}

            # 准备参数
            data = {k: str(v) for k, v in config.items() if v is not None}

            # 设置超时（大文件可能需要更长时间）
            timeout = 3000  # 5分钟

            # 发送请求
            response = requests.post(
                PDF_PARSE_API_URL,
                files=files,
                data=data,
                timeout=timeout
            )

            print(f"[http] API响应状态: {response.status_code}")

            if response.status_code == 200:
                # 检查响应类型
                content_type = response.headers.get('Content-Type', '')

                if 'application/zip' in content_type or config.get('response_format_zip'):
                    # ZIP格式响应
                    return {
                        'success': True,
                        'content_type': 'zip',
                        'data': response.content,
                        'headers': dict(response.headers)
                    }
                elif 'application/json' in content_type:
                    # JSON格式响应
                    return {
                        'success': True,
                        'content_type': 'json',
                        'data': response.json(),
                        'headers': dict(response.headers)
                    }
                else:
                    # 其他格式
                    return {
                        'success': True,
                        'content_type': content_type,
                        'data': response.content,
                        'headers': dict(response.headers)
                    }
            else:
                print(f"[err] API调用失败: {response.status_code}")
                print(f"错误信息: {response.text[:500]}")
                return {
                    'success': False,
                    'status_code': response.status_code,
                    'error': response.text
                }

    except requests.exceptions.Timeout:
        print("[err] API调用超时")
        return {
            'success': False,
            'error': 'API调用超时，请稍后重试'
        }
    except requests.exceptions.ConnectionError:
        print("[err] 无法连接到PDF解析API")
        return {
            'success': False,
            'error': '无法连接到PDF解析API，请检查网络连接'
        }
    except Exception as e:
        print(f"[err] API调用异常: {str(e)}")
        return {
            'success': False,
            'error': f'API调用异常: {str(e)}'
        }

def extract_zip_result(zip_content: bytes, output_dir: Path) -> Dict[str, Any]:
    """
    提取ZIP格式的结果

    Args:
        zip_content: ZIP文件内容
        output_dir: 输出目录

    Returns:
        提取结果
    """
    # 创建临时文件保存ZIP
    temp_zip = output_dir / f"temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"

    with open(temp_zip, 'wb') as f:
        f.write(zip_content)

    extracted_files = []

    try:
        # 解压ZIP文件
        with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
            # 获取ZIP文件列表
            file_list = zip_ref.namelist()
            print(f"[zip] ZIP中包含文件: {file_list}")

            # 提取所有文件
            for file_name in file_list:
                # 安全地提取文件
                safe_name = file_name.replace('..', '').replace('/', '_')
                extract_path = output_dir / safe_name

                with zip_ref.open(file_name) as source, open(extract_path, 'wb') as target:
                    shutil.copyfileobj(source, target)

                extracted_files.append({
                    'name': file_name,
                    'path': str(extract_path),
                    'size': os.path.getsize(extract_path)
                })

        # 清理临时ZIP文件
        temp_zip.unlink()

        return {
            'success': True,
            'files': extracted_files,
            'file_list': file_list
        }

    except zipfile.BadZipFile:
        print("❌ ZIP文件损坏")
        return {
            'success': False,
            'error': 'ZIP文件损坏'
        }
    except Exception as e:
        print(f"[err] 解压ZIP失败: {str(e)}")
        return {
            'success': False,
            'error': f'解压ZIP失败: {str(e)}'
        }

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
                    result['markdown'] = {
                        'content': f.read(),
                        'path': str(file_path),
                        'size': len(f.read())
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
        'backend': 'hybrid-auto-engine',
        'parse_method': 'auto',
        'formula_enable': True,
        'table_enable': True,
        'return_md': True,
        'return_content_list': True,
        'response_format_zip': True
    }

    # 合并配置
    if config:
        default_config.update(config)

    # 创建输出目录
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_dir = OUTPUT_DIR / f"parse_{timestamp}_{Path(pdf_path).stem}"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[file] 输出目录: {output_dir}")

    # 1. 调用PDF解析API
    api_result = await call_pdf_parse_api(pdf_path, default_config)

    if not api_result.get('success'):
        return {
            'success': False,
            'error': api_result.get('error', '未知错误'),
            'output_dir': str(output_dir)
        }

    # 2. 处理API响应
    if api_result.get('content_type') == 'zip':
        # 处理ZIP格式响应
        zip_result = extract_zip_result(api_result['data'], output_dir)

        if not zip_result.get('success'):
            return {
                'success': False,
                'error': zip_result.get('error', 'ZIP处理失败'),
                'output_dir': str(output_dir)
            }

        # 处理提取的文件
        file_result = process_extracted_files(zip_result['files'], output_dir)

        # 检查是否获取到必要文件
        if not file_result['markdown']:
            # 如果没有直接获取到markdown，尝试从其他文件中提取
            markdown_content = await extract_markdown_from_other_files(output_dir)
            if markdown_content:
                file_result['markdown'] = {
                    'content': markdown_content,
                    'path': str(output_dir / 'extracted.md'),
                    'size': len(markdown_content)
                }

                # 保存提取的markdown
                with open(output_dir / 'extracted.md', 'w', encoding='utf-8') as f:
                    f.write(markdown_content)

        return {
            'success': True,
            'output_dir': str(output_dir),
            'files': file_result,
            'zip_files': zip_result.get('file_list', [])
        }

    elif api_result.get('content_type') == 'json':
        # 处理JSON格式响应
        json_data = api_result['data']

        result = {
            'success': True,
            'output_dir': str(output_dir),
            'files': {
                'markdown': None,
                'content_list': None,
                'layout_pdf': None,
                'other_files': []
            }
        }

        # 提取Markdown
        if 'md_content' in json_data:
            markdown_content = json_data['md_content']
            md_path = output_dir / 'output.md'
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(markdown_content)

            result['files']['markdown'] = {
                'content': markdown_content,
                'path': str(md_path),
                'size': len(markdown_content)
            }

        # 提取content_list
        if 'content_list' in json_data:
            content_list = json_data['content_list']
            cl_path = output_dir / 'content_list.json'
            with open(cl_path, 'w', encoding='utf-8') as f:
                json.dump(content_list, f, ensure_ascii=False, indent=2)

            result['files']['content_list'] = {
                'content': content_list,
                'path': str(cl_path),
                'size': os.path.getsize(cl_path)
            }

        return result

    else:
        return {
            'success': False,
            'error': f'不支持的响应格式: {api_result.get("content_type")}',
            'output_dir': str(output_dir)
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
        "pdf_parse_api": PDF_PARSE_API_URL
    }

@app.get("/health")
async def health_check():
    """健康检查"""
    # 测试PDF解析API连通性
    try:
        response = requests.get(f"{PDF_PARSE_API_URL.replace('/file_parse', '')}/health", timeout=5)
        pdf_api_status = "healthy" if response.status_code == 200 else "unhealthy"
    except:
        pdf_api_status = "unreachable"

    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "pdf_parse_api_status": pdf_api_status
    }

@app.post("/convert/upload")
async def convert_from_upload(
    file: UploadFile = File(...),
    lang_list: str = Form("ch"),
    backend: str = Form("hybrid-auto-engine"),
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
            'parse_method': 'auto',
            'formula_enable': formula_enable,
            'table_enable': table_enable,
            'return_md': True,
            'return_content_list': True,
            'response_format_zip': True
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
    print(f"PDF解析API: {PDF_PARSE_API_URL}")
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
