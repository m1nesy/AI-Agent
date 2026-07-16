import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from volcenginesdkarkruntime import Ark
from app_config import settings

SYSTEM_PROMPT = """
# 角色
你是一个智能客服助手。

# 回答规则
1. 如果有【参考知识库】内容，优先用知识库内容回答，保持原意不编造。
2. 如果知识库没有相关内容，可以用你自己的知识回答，但要简短精炼。
3. 回答时先说"根据知识库："或"根据我的了解："，让用户知道信息来源。
""".strip()


class LLMService:
    def __init__(self):
        self.client = Ark(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key=settings.ARK_API_KEY,
            timeout=600,
            max_retries=1,
        )

    def chat_stream(self, history_messages: list, rag_context: str = ""):
        system_blocks = [SYSTEM_PROMPT]
        if rag_context:
            system_blocks.append(f"### 参考知识库（绝对准则）\n{rag_context.strip()}")

        messages = [{"role": "system", "content": "\n\n".join(system_blocks)}]
        messages.extend(history_messages)

        try:
            print(f"[INFO] 发起流式调用 (Endpoint: {settings.ARK_ENDPOINT_ID})")
            stream = self.client.chat.completions.create(
                model=settings.ARK_ENDPOINT_ID,
                messages=messages,
                temperature=0.3,
                stream=True,
                stream_options={"include_usage": True},
                thinking={"type": "disabled"},
            )
            for chunk in stream:
                yield chunk
        except Exception as e:
            print(f"[ERROR] LLM 调用失败: {e}")
            yield None


llm_service = LLMService()
