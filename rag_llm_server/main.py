import uuid
import time
import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, Any

from app_config import settings
from services.llm_service import llm_service
from services.token_build import AccessToken, PRIVILEGES
from services.utils import Signer  # 确保 utils.py 已移动到 services 目录

from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
import json
from services.rag_service import rag_service  # <--- 新增这行

# 在你的 settings.py 或 main.py 顶部
from dotenv import load_dotenv

load_dotenv()  # 必须先执行这一行，后面的 settings 才能读到值

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- 1. 获取场景 (前端展示用) ---
@app.post("/getScenes")
async def get_scenes(request: Request):
    # 生成随机 ID
    room_id = "ChatRoom01"
    user_id = "Huoshan01"

    # 签发 RTC Token
    token_builder = AccessToken(
        settings.RTC_APP_ID, settings.RTC_APP_KEY, room_id, user_id
    )
    token_builder.add_privilege(PRIVILEGES["PrivSubscribeStream"], 0)
    token_builder.add_privilege(PRIVILEGES["PrivPublishStream"], 0)
    token_builder.expire_time(int(time.time()) + 3600 * 24)
    token = token_builder.serialize()

    # 构造返回结构
    return {
        "ResponseMetadata": {"Action": "getScenes"},
        "Result": {
            "scenes": [
                {
                    "scene": {
                        # --- 补全的核心字段 ---
                        "id": "Custom",  # 建议改为 Custom，通常前端会根据这个 ID 做特殊处理
                        "name": "自定义助手",
                        "botName": "AiAgent",
                        "icon": "https://lf3-rtc-demo.volccdn.com/obj/rtc-aigc-assets/DoubaoAvatar.png",  # 补全图标
                        # --- 功能开关 ---
                        "isInterruptMode": True,  # 是否支持打断
                        "isVision": False,  # 补全：是否开启视觉（摄像头）
                        "isScreenMode": False,  # 补全：是否开启屏幕共享
                        # --- 数字人相关 (无数字人时设为 None/null) ---
                        "isAvatarScene": None,
                        "avatarBgUrl": None,
                    },
                    "rtc": {
                        "AppId": settings.RTC_APP_ID,
                        "RoomId": room_id,
                        "UserId": user_id,
                        "Token": token,
                    },
                    # 这里的配置主要是为了兼容前端透传，实际生效主要看 proxy
                    "VoiceChat": {},
                }
            ]
        },
    }


# --- 2. 拦截前端的 StartVoiceChat 请求 (核心配置下发) ---
# main.py 核心修改
# rag_llm_server/main.py


@app.post("/proxy")
async def proxy(request: Request):
    """
    代理 AIGC OpenAPI 请求
    """
    action = request.query_params.get("Action")
    version = request.query_params.get("Version", "2024-12-01")

    try:
        incoming_body = await request.json()
    except Exception:
        incoming_body = {}

    target_app_id = settings.RTC_APP_ID
    target_room_id = "ChatRoom01"
    target_user_id = "Huoshan01"

    request_body = {}

    if action == "StartVoiceChat":
        request_body = {
            "AppId": target_app_id,
            "RoomId": target_room_id,
            "TaskId": "ChatTask01",
            "AgentConfig": {
                "TargetUserId": [target_user_id],
                "WelcomeMessage": "我是懂小智，你的专属课程顾问，有什么问题尽管问我吧，我比懂王更强",
                "UserId": "AiAgent",
                "EnableConversationStateCallback": True,
            },
            "Config": {
                "ASRConfig": {
                    "Provider": "volcano",
                    "ProviderParams": {
                        "Mode": "smallmodel",
                        "AppId": "1729106152",
                        "Cluster": "volcengine_streaming_common",
                    },
                },
                "TTSConfig": {
                    "Provider": "volcano",
                    "ProviderParams": {
                        "app": {"appid": "4277655316", "cluster": "volcano_tts"},
                        "audio": {
                            "voice_type": "BV001_streaming",
                            "speed_ratio": 1,
                            "pitch_ratio": 1,
                            "volume_ratio": 1,
                        },
                    },
                },
                "LLMConfig": {
                    "Mode": "CustomLLM",
                    "Url": f"{settings.SERVER_URL}/api/chat_callback",
                    "Method": "POST",
                    "ApiType": "https" if str(settings.SERVER_URL).startswith("https") else "http",
                },
                "InterruptMode": 0,
            },
        }
    elif action == "StopVoiceChat":
        request_body = {
            "AppId": target_app_id,
            "RoomId": target_room_id,
            "TaskId": "ChatTask01",
        }
    else:
        request_body = incoming_body

    host = "rtc.volcengineapi.com"
    open_api_request_data = {
        "method": "POST",
        "path": "/",
        "params": {"Action": action, "Version": version},
        "headers": {"Host": host, "Content-Type": "application/json"},
        "body": request_body,
    }

    account_config = {"accessKeyId": settings.VOLC_AK, "secretKey": settings.VOLC_SK}
    signer = Signer(open_api_request_data, "rtc")
    signer.add_authorization(account_config)

    url = f"https://{host}?Action={action}&Version={version}"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            headers=open_api_request_data["headers"],
            json=request_body,
            timeout=30.0,
        )
        result = resp.json()
        print(f"DEBUG: 火山引擎返回结果: {result}")
        return result


# --- 3. 业务回调接口 (RTC -> 这里) ---


# ... 其他代码 ...


@app.post("/api/chat_callback")
async def chat_callback(request: Request):
    try:
        data = await request.json()
    except:
        return {"text": ""}

    print(f"======================== 流式请求", data)

    messages = data.get("messages", [])

    # 校验逻辑 (保持不变)
    if not messages or messages[-1].get("role") != "user":
        print("[WARN] 忽略：非用户主动发言")
        return {"text": ""}

    # --- 定义 SSE 生成器 ---
    async def generate_sse():
        try:
            import sys
            last_msg = messages[-1].get("content", "") if messages else ""
            print(f"[RAG] 开始检索, query: {last_msg[:30]}", flush=True)
            rag_content = await rag_service.retrieve(last_msg)
            print(f"[RAG] 检索完成, 长度: {len(rag_content) if rag_content else 0}", flush=True)

            stream_iterator = llm_service.chat_stream(messages, rag_content)

            for chunk in stream_iterator:
                if chunk:
                    try:
                        chunk_json = chunk.model_dump_json()
                        yield f"data: {chunk_json}\n\n"
                    except Exception:
                        pass

            yield "data: [DONE]\n\n"
        except Exception as e:
            print(f"[ERROR] generate_sse 异常: {e}")
            yield "data: [DONE]\n\n"

    # --- 返回流式响应 ---
    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",  # <--- 必须是这个 Header
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 如果存在跨域问题，可以加上 Access-Control-Allow-Origin
            "Access-Control-Allow-Origin": "*",
        },
    )


from typing import List, Optional


# 1. 定义消息模型
class ChatMessage(BaseModel):
    role: str  # "user" 或 "assistant"
    content: str


class DebugRequest(BaseModel):
    history: Optional[List[ChatMessage]] = []
    question: str


# 2. 调试接口
@app.post("/debug/chat")
async def debug_chat(request: DebugRequest):


    # 构造当前发送给 LLM 的消息列表
    current_messages = []
    for msg in request.history:
        current_messages.append({"role": msg.role, "content": msg.content})

    # 放入用户最新问题
    current_messages.append({"role": "user", "content": request.question})

    async def generate_text():
        full_ai_response = ""
        total_usage = None

            # 1. 记录总开始时间
        start_t = time.time()
        # 查询知识库
        rag_content = await rag_service.retrieve(request.question)

        rag_duration = time.time() - start_t

        print(f"DEBUG: 知识库查询耗时: {rag_duration:.2f}s")
        # print(f"DEBUG: 知识库返回检索内容: {rag_content}")

        # 2. 记录 LLM 调用开始时间
        llm_start_t = time.time()

        # 调用 llm_service
        stream = llm_service.chat_stream(current_messages, rag_content)

        for chunk in stream:
            if chunk and chunk.choices:
                delta = chunk.choices[0].delta
                if delta.content:
                    content = delta.content
                    full_ai_response += content  # 累积 AI 的回答
                    yield content
            # 记录 Token 消耗
            if hasattr(chunk, "usage") and chunk.usage:
                total_usage = chunk.usage

        # 3. 记录 LLM 调用耗时
        llm_duration = time.time() - llm_start_t
        print(f"DEBUG: LLM 调用耗时: {llm_duration:.2f}s")

        if total_usage:
            print(
                f"[TOKEN] Token 统计: Total={total_usage.total_tokens} (P:{total_usage.prompt_tokens}, C:{total_usage.completion_tokens})"
            )

        # --- 重点：在流结束后构造并打印 history 结构 ---
        # 构造完整的 history 列表
        new_history = []
        # 添加旧历史
        for m in request.history:
            new_history.append({"role": m.role, "content": m.content})
        # 添加最新的一轮对话
        new_history.append({"role": "user", "content": request.question})
        new_history.append({"role": "assistant", "content": full_ai_response})

        # 打印到控制台，方便你直接复制
        print("\n" + "=" * 50)
        print("[DEBUG] 调试完成！以下是可用于下次请求的 history 结构：")
        print(json.dumps({"history": new_history}, ensure_ascii=False, indent=2))
        print("=" * 50 + "\n")

    return StreamingResponse(generate_text(), media_type="text/plain")


# ... 其他导入保持不变 ...
from services.rag_service import rag_service  # 确保已导入 rag_service


# --- 新增：知识库调试接口 ---
@app.get("/debug/rag")
async def debug_rag(query: str):
    """
    调试接口：直接返回知识库检索到的原始文本内容
    用法：浏览器访问 http://127.0.0.1:8000/debug/rag?query=你的问题
    """
    if not query:
        return {"error": "请提供 query 参数"}

    print(f"[DEBUG] [Debug] 正在检索知识库: {query}")

    # 调用我们在 rag_service.py 中实现的异步 retrieve 方法
    context = await rag_service.retrieve(query)

    return {
        "query": query,
        "retrieved_context": context,
        "length": len(context) if context else 0,
        "status": "success" if context else "no_results_or_error",
    }






# --- 4. 静态文件服务 (前端 build 产物) ---
import os as _os
from fastapi.staticfiles import StaticFiles

_BUILD_DIR = _os.path.join(_os.path.dirname(__file__), "..", "build")

if _os.path.exists(_BUILD_DIR):
    app.mount("/static", StaticFiles(directory=_os.path.join(_BUILD_DIR, "static")), name="static")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = _os.path.join(_BUILD_DIR, full_path)
        if full_path and _os.path.exists(file_path):
            return FileResponse(file_path)
        return FileResponse(_os.path.join(_BUILD_DIR, "index.html"))


if __name__ == "__main__":
    import uvicorn

    print(f"[INFO] Server running at {settings.SERVER_URL}")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=3001,
        reload=True,
        reload_dirs=[".", "services"],
        # 依然建议排除缓存文件，防止编译行为触发重启
        reload_excludes=[
            "*/__pycache__/*",
            "*.pyc",
            ".venv/*",  # 排除根目录下的虚拟环境
            "*/.venv/*",
        ],
    )
