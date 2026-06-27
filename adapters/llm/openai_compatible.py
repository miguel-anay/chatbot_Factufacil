"""
Adaptador LLM — OpenAI y cualquier API compatible (Alibaba DashScope / Qwen).
Implementa LLMPort. Para swapear a otro proveedor: crear otro adaptador, no tocar el core.
"""
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from core.ports import LLMPort
from infrastructure.config import Config


class OpenAICompatibleAdapter(LLMPort):
    """
    Funciona con OpenAI y con cualquier endpoint compatible:
    Alibaba DashScope, Ollama, Together, Groq, etc.
    Solo se necesita cambiar LLM_BASE_URL y LLM_API_KEY en .env.
    """

    def __init__(self) -> None:
        kwargs: dict = {
            "model": Config.LLM_MODEL,
            "api_key": Config.LLM_API_KEY,
            "temperature": Config.LLM_TEMPERATURE,
            "max_tokens": Config.MAX_TOKENS,
        }
        if Config.LLM_BASE_URL:
            kwargs["base_url"] = Config.LLM_BASE_URL
        self._client = ChatOpenAI(**kwargs)

    def generate(self, prompt: str) -> str:
        response = self._client.invoke([HumanMessage(content=prompt)])
        return response.content.strip()
