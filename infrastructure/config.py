import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # LLM — Alibaba DashScope (Qwen) o OpenAI
    ALIBABA_API_KEY: str = os.getenv("ALIBABA_API_KEY", "")
    ALIBABA_BASE_URL: str = os.getenv(
        "ALIBABA_BASE_URL",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    )
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # Si hay clave Alibaba se usa Qwen; si no, OpenAI
    LLM_API_KEY: str = ALIBABA_API_KEY or OPENAI_API_KEY
    LLM_BASE_URL: str = ALIBABA_BASE_URL if ALIBABA_API_KEY else ""
    LLM_MODEL: str = os.getenv(
        "LLM_MODEL",
        "qwen-plus" if ALIBABA_API_KEY else "gpt-3.5-turbo"
    )
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))

    # Embeddings locales (sin costo, sin API key)
    EMBEDDING_MODEL: str = os.getenv(
        "EMBEDDING_MODEL",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )

    # RAG — chunking y recuperación
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "500"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "50"))
    TOP_K: int = int(os.getenv("TOP_K", "2"))
    FAISS_INDEX_PATH: str = os.getenv("FAISS_INDEX_PATH", "data/faiss_index")

    # Memoria conversacional (últimos N turnos)
    MEMORY_K: int = int(os.getenv("MEMORY_K", "4"))

    # Límite de tokens en la respuesta del LLM (presales)
    MAX_TOKENS: int = int(os.getenv("MAX_TOKENS", "300"))

    # Score máximo de distancia L2 (FAISS, embeddings normalizados) para
    # considerar un doc relevante. Por encima de este valor la query se
    # trata como off-topic y no se llama al LLM.
    OFF_TOPIC_THRESHOLD: float = float(os.getenv("OFF_TOPIC_THRESHOLD", "1.0"))

    # Servidor
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))

    # CORS — orígenes permitidos (separados por coma en .env)
    CORS_ORIGINS: list[str] = [
        o.strip()
        for o in os.getenv(
            "CORS_ORIGINS",
            "https://landingbot.qhipa.org.pe,https://factufacil.pe"
        ).split(",")
        if o.strip()
    ]

    # Datos de la empresa (usados en el prompt del sistema)
    COMPANY_NAME: str = "FactuFácil"
    COMPANY_PHONE: str = "+51 936327402"
    COMPANY_EMAIL: str = "ventas@factufacil.pe"
    COMPANY_DEMO_URL: str = "demo.factufacil.pe"

    @classmethod
    def validate(cls) -> None:
        if not cls.LLM_API_KEY:
            raise ValueError(
                "No se encontró API key.\n"
                "Configurá ALIBABA_API_KEY o OPENAI_API_KEY en el archivo .env"
            )

    @classmethod
    def print_config(cls) -> None:
        provider = "Alibaba DashScope" if cls.ALIBABA_API_KEY else "OpenAI"
        print(f"  Proveedor LLM : {provider}")
        print(f"  Modelo        : {cls.LLM_MODEL}")
        print(f"  Embeddings    : {cls.EMBEDDING_MODEL}")
        print(f"  FAISS index   : {cls.FAISS_INDEX_PATH}")
        print(f"  Chunk size    : {cls.CHUNK_SIZE} / overlap {cls.CHUNK_OVERLAP}")
        print(f"  Top-K RAG     : {cls.TOP_K}")
        print(f"  Memoria K     : {cls.MEMORY_K} turnos")
