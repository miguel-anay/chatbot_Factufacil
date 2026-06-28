"""
Adaptador RAG — FAISS local + embeddings HuggingFace.
Implementa RAGPort. Para swapear a OpenSearch: crear OpenSearchAdapter, no tocar el core.
"""
import shutil
from pathlib import Path
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS

from core.domain import RetrievedDocument
from core.ports import RAGPort
from infrastructure.config import Config


class FAISSAdapter(RAGPort):

    def __init__(self) -> None:
        print("Cargando embeddings (primera vez ~30 s)...")
        self._embeddings = HuggingFaceEmbeddings(
            model_name=Config.EMBEDDING_MODEL,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        self._store: FAISS | None = None
        self._load_or_build()

    def retrieve(self, query: str, k: int = None) -> List[RetrievedDocument]:
        if not self._store:
            return []
        results = self._store.similarity_search_with_score(query, k=k or Config.TOP_K)
        return [
            RetrievedDocument(
                content=d.page_content,
                category=d.metadata.get("category", ""),
                topic=d.metadata.get("topic", ""),
            )
            for d, score in results
            if score <= Config.OFF_TOPIC_THRESHOLD
        ]

    def reindex(self) -> None:
        index_path = Path(Config.FAISS_INDEX_PATH)
        if index_path.exists():
            shutil.rmtree(str(index_path))
            print("Índice anterior eliminado.")
        self._build_index()

    def get_stats(self) -> dict:
        return {
            "indexed": self._store is not None,
            "index_path": Config.FAISS_INDEX_PATH,
            "embedding_model": Config.EMBEDDING_MODEL,
        }

    def _load_or_build(self) -> None:
        index_path = Path(Config.FAISS_INDEX_PATH)
        if (index_path / "index.faiss").exists():
            self._store = FAISS.load_local(
                str(index_path),
                self._embeddings,
                allow_dangerous_deserialization=True,
            )
            print(f"✓ Índice FAISS cargado desde {index_path}")
        else:
            self._build_index()

    def _build_index(self) -> None:
        from infrastructure.knowledge_base import FACTUFACIL_DOCUMENTS

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=Config.CHUNK_SIZE,
            chunk_overlap=Config.CHUNK_OVERLAP,
            separators=["\n\n", "\n", ". ", ", ", " "],
        )
        raw_docs = [
            Document(page_content=item["content"], metadata=item["metadata"])
            for item in FACTUFACIL_DOCUMENTS
        ]
        chunks = splitter.split_documents(raw_docs)
        print(f"  {len(raw_docs)} documentos → {len(chunks)} chunks")

        self._store = FAISS.from_documents(chunks, self._embeddings)

        index_path = Path(Config.FAISS_INDEX_PATH)
        index_path.mkdir(parents=True, exist_ok=True)
        self._store.save_local(str(index_path))
        print(f"✓ Índice guardado en {index_path}")
