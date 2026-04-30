"""
src/core/rag_engine.py

FAISS 기반 RAG 엔진

[구조]
  - 임베딩: Gemini Embedding API (gemini-embedding-001)
  - 벡터 저장: FAISS (메모리 상주)
  - 인덱스 영구화: Azure Blob Storage
  - 검색: Hybrid (FAISS Vector + MongoDB 텍스트 fallback)
  - 품질 레이어: HyDE + Reranking (Claude)

[인스턴스 동기화]
  - 문서 저장/수정/삭제 시 → FAISS 업데이트 → Blob에 업로드
  - 각 인스턴스는 TRIGGER_REGISTRY로 10분마다 Blob 버전 체크 → 변경 시 reload

[주의]
  - 모든 Blob/FAISS 작업은 동기 → asyncio.to_thread()로 감쌈
  - faiss, azure-storage-blob, google-generativeai 패키지 필요
    pip install faiss-cpu azure-storage-blob google-generativeai
"""

import os
import io
import json
import time
import asyncio
import logging
import pickle
from datetime import datetime, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ── 패키지 임포트 (없으면 명시적 에러) ────────────────────────────────────────
try:
    import faiss
except ImportError:
    raise ImportError("faiss-cpu 패키지가 필요합니다: pip install faiss-cpu")

try:
    from azure.storage.blob import BlobServiceClient
except ImportError:
    raise ImportError("azure-storage-blob 패키지가 필요합니다: pip install azure-storage-blob")

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    raise ImportError("google-generativeai 패키지가 필요합니다: pip install google-generativeai")

from src.core.mongo_client import boa_knowledge_collection

def _extract_text(content) -> str:
    """Gemini/LangChain 응답 content가 str 또는 list일 수 있음. 항상 str로 반환."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)

# ── 환경변수 ──────────────────────────────────────────────────────────────────
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
CONTAINER_NAME                  = os.getenv("CONTAINER_NAME", "boa-rag")
GEMINI_API_KEY                  = os.getenv("GEMINI_API_KEY", "")
GEMINI_EMBEDDING_MODEL          = "gemini-embedding-2-preview"  # 최신 멀티모달 임베딩 모델

BLOB_INDEX_NAME    = "faiss/knowledge.index"   # FAISS 인덱스 파일
BLOB_META_NAME     = "faiss/knowledge_meta.pkl"  # doc_id ↔ FAISS id 매핑
BLOB_VERSION_NAME  = "faiss/version.txt"         # 버전 타임스탬프 (인스턴스 동기화용)

EMBEDDING_DIM = 3072  # gemini-embedding-2-preview 차원

# ── Gemini 클라이언트 ──────────────────────────────────────────────────────────
_genai_client = genai.Client(api_key=GEMINI_API_KEY)

# ── Azure Blob 클라이언트 ──────────────────────────────────────────────────────
_blob_service: Optional[BlobServiceClient] = None

def _get_blob_service() -> BlobServiceClient:
    global _blob_service
    if _blob_service is None:
        _blob_service = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
    return _blob_service

def _get_blob_client(blob_name: str):
    return _get_blob_service().get_blob_client(container=CONTAINER_NAME, blob=blob_name)


# ── RAGEngine ─────────────────────────────────────────────────────────────────

class RAGEngine:
    """
    싱글톤 패턴으로 사용. rag_engine.get_engine() 으로 접근.

    내부 상태:
      index      : faiss.IndexFlatIP  (내적 기반 코사인 유사도, L2 정규화 후 사용)
      meta       : list[dict]         [{"doc_id": ..., "title": ..., "service": ..., "category": ...}]
      _version   : str                Blob에 저장된 버전 타임스탬프
    """

    def __init__(self):
        self.index: Optional[faiss.Index] = None
        self.meta: list = []          # FAISS 행 번호 → 문서 메타
        self._version: str = ""
        self._lock = asyncio.Lock()   # 동시 rebuild 방지

    # ── 임베딩 생성 ────────────────────────────────────────────────────────────

    def _embed_sync(self, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> np.ndarray:
        """동기. asyncio.to_thread()로 호출할 것."""
        embeddings = []
        # Gemini API는 배치당 최대 100개
        batch_size = 100
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            response = _genai_client.models.embed_content(
                model=GEMINI_EMBEDDING_MODEL,
                contents=batch,
                config=genai_types.EmbedContentConfig(task_type=task_type),
            )
            for emb in response.embeddings:
                embeddings.append(emb.values)

        arr = np.array(embeddings, dtype=np.float32)
        # L2 정규화 (내적 = 코사인 유사도)
        faiss.normalize_L2(arr)
        return arr

    async def embed(self, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> np.ndarray:
        return await asyncio.to_thread(self._embed_sync, texts, task_type)

    async def embed_query(self, text: str) -> np.ndarray:
        """쿼리 임베딩 (task_type이 다름)"""
        return await self.embed([text], task_type="RETRIEVAL_QUERY")

    # ── Azure Blob 저장/로드 ────────────────────────────────────────────────────

    def _upload_blob_sync(self, blob_name: str, data: bytes) -> None:
        client = _get_blob_client(blob_name)
        client.upload_blob(data, overwrite=True)

    def _download_blob_sync(self, blob_name: str) -> Optional[bytes]:
        try:
            client = _get_blob_client(blob_name)
            return client.download_blob().readall()
        except Exception:
            return None

    def _get_blob_version_sync(self) -> str:
        data = self._download_blob_sync(BLOB_VERSION_NAME)
        return data.decode() if data else ""

    def _save_index_to_blob_sync(self) -> None:
        """FAISS 인덱스 + 메타 + 버전을 Blob에 저장"""
        # FAISS 인덱스 직렬화
        buf = io.BytesIO()
        faiss.write_index(self.index, faiss.PyCallbackIOWriter(buf.write))
        self._upload_blob_sync(BLOB_INDEX_NAME, buf.getvalue())

        # 메타 직렬화
        meta_bytes = pickle.dumps(self.meta)
        self._upload_blob_sync(BLOB_META_NAME, meta_bytes)

        # 버전 타임스탬프
        version = datetime.now(timezone.utc).isoformat()
        self._upload_blob_sync(BLOB_VERSION_NAME, version.encode())
        self._version = version

        logger.info(f"[RAGEngine] Blob 저장 완료 | 문서 수: {len(self.meta)} | 버전: {version}")

    def _load_index_from_blob_sync(self) -> bool:
        """Blob에서 FAISS 인덱스 로드. 성공하면 True."""
        index_bytes = self._download_blob_sync(BLOB_INDEX_NAME)
        meta_bytes  = self._download_blob_sync(BLOB_META_NAME)
        if not index_bytes or not meta_bytes:
            return False

        reader = io.BytesIO(index_bytes)
        self.index = faiss.read_index(faiss.PyCallbackIOReader(reader.read))
        self.meta  = pickle.loads(meta_bytes)
        self._version = self._get_blob_version_sync()
        logger.info(f"[RAGEngine] Blob 로드 완료 | 문서 수: {len(self.meta)} | 버전: {self._version}")
        return True

    # ── 인덱스 빌드 ────────────────────────────────────────────────────────────

    async def build_from_mongo(self) -> None:
        """
        앱 시작 시 또는 강제 rebuild 시 호출.
        MongoDB에서 published 문서 전체를 읽어서 FAISS 인덱스 빌드.
        """
        async with self._lock:
            logger.info("[RAGEngine] MongoDB 전체 빌드 시작")
            start = time.time()

            cursor = boa_knowledge_collection.find(
                {"schema_version": 2},
                {"_id": 1, "title": 1, "description": 1, "tags": 1, "content": 1, "status": 1, "domain": 1}
            )
            docs = await cursor.to_list(length=None)

            if not docs:
                logger.info("[RAGEngine] 문서 없음 → 빈 인덱스 생성")
                self.index = faiss.IndexFlatIP(EMBEDDING_DIM)
                self.meta  = []
                await asyncio.to_thread(self._save_index_to_blob_sync)
                return

            # 임베딩 생성 (title + description + tags + content)
            def _make_embed_text(d: dict) -> str:
                title = d.get("title", "")
                desc = d.get("description", "")
                tags = d.get("tags") or []
                tags_str = " ".join(tags) if tags else ""
                content = d.get("content", "")
                
                parts = []
                if title: parts.append(f"제목: {title}")
                if desc: parts.append(f"설명: {desc}")
                if tags_str: parts.append(f"태그: {tags_str}")
                if content: parts.append(f"본문: {content}")
                
                return "\n".join(parts)

            texts = [_make_embed_text(d) for d in docs]
            embeddings = await self.embed(texts)

            # FAISS 인덱스 구성
            index = faiss.IndexFlatIP(EMBEDDING_DIM)
            index.add(embeddings)

            self.index = index
            self.meta  = [
                {
                    "_id":         str(d["_id"]),
                    "title":       d.get("title", ""),
                    "description": d.get("description", ""),
                    "tags":        d.get("tags", []),
                    "status":      d.get("status", ""),
                    "domain":      d.get("domain"),
                }
                for d in docs
            ]

            # Blob 저장
            await asyncio.to_thread(self._save_index_to_blob_sync)
            logger.info(f"[RAGEngine] 빌드 완료 | 문서 수: {len(docs)} | 소요: {time.time()-start:.1f}s")

    # ── 단일 문서 업데이트 ─────────────────────────────────────────────────────

    async def upsert_doc(self, doc_id: str, title: str, content: str,
                          service: str = "", category: str = "",
                          status: str = "", tags: list = None,
                          description: str = "", domain: str = None) -> None:
        """
        문서 저장/수정 시 호출. 기존 항목 제거 후 재추가.
        FAISS는 단일 삭제가 안 돼서 IDMap2로 감싸는 방식 사용.
        """
        async with self._lock:
            if self.index is None:
                await self.build_from_mongo()
                return

            tags = tags or []
            
            # 임베딩 생성 (title + description + tags + content 전체 반영)
            tags_str = " ".join(tags) if tags else ""
            parts = []
            if title: parts.append(f"제목: {title}")
            if description: parts.append(f"설명: {description}")
            if tags_str: parts.append(f"태그: {tags_str}")
            if content: parts.append(f"본문: {content}")
            embed_text = "\n".join(parts)
            
            embedding = await self.embed([embed_text])  # (1, DIM)

            # 기존 항목 제거 (meta에서 찾아서 해당 FAISS 행 마킹)
            existing_idx = next(
                (i for i, m in enumerate(self.meta) if m["_id"] == doc_id), None
            )

            if existing_idx is not None:
                # FAISS IndexFlatIP는 단일 삭제 미지원 → 전체 rebuild가 가장 안전
                # 문서 수가 적으므로 성능 영향 없음
                self.meta.pop(existing_idx)
                # 인덱스 재구성
                await self._rebuild_index_sync()

            # 새 항목 추가
            self.index.add(embedding)
            self.meta.append({
                "_id":         doc_id,
                "title":       title,
                "description": description,
                "tags":        tags,
                "status":      status,
                "domain":      domain,
            })

            await asyncio.to_thread(self._save_index_to_blob_sync)
            logger.info(f"[RAGEngine] upsert 완료: {doc_id}")

    async def remove_doc(self, doc_id: str) -> None:
        """문서 삭제/아카이브 시 호출."""
        async with self._lock:
            if self.index is None:
                return

            existing_idx = next(
                (i for i, m in enumerate(self.meta) if m["_id"] == doc_id), None
            )
            if existing_idx is None:
                return

            self.meta.pop(existing_idx)
            await self._rebuild_index_sync()
            await asyncio.to_thread(self._save_index_to_blob_sync)
            logger.info(f"[RAGEngine] remove 완료: {doc_id}")

    async def _rebuild_index_sync(self) -> None:
        """meta 기준으로 MongoDB에서 임베딩 재생성하여 인덱스 재구성."""
        if not self.meta:
            self.index = faiss.IndexFlatIP(EMBEDDING_DIM)
            return

        topic_ids = [m["_id"] for m in self.meta]
        cursor = boa_knowledge_collection.find(
            {"_id": {"$in": topic_ids}},
            {"_id": 1, "title": 1, "description": 1, "tags": 1}
        )
        docs = await cursor.to_list(length=None)
        doc_map = {str(d["_id"]): d for d in docs}

        texts = []
        valid_meta = []
        for m in self.meta:
            d = doc_map.get(m["_id"])
            if d:
                desc = d.get("description") or d.get("title", "")
                tags = d.get("tags") or []
                texts.append(desc + (" " + " ".join(tags) if tags else ""))
                valid_meta.append(m)

        if not texts:
            self.index = faiss.IndexFlatIP(EMBEDDING_DIM)
            self.meta  = []
            return

        embeddings = await self.embed(texts)
        index = faiss.IndexFlatIP(EMBEDDING_DIM)
        index.add(embeddings)
        self.index = index
        self.meta  = valid_meta

    # ── 인스턴스 동기화 (TRIGGER_REGISTRY용) ──────────────────────────────────

    async def check_and_reload(self) -> None:
        """
        10분마다 TRIGGER_REGISTRY에서 호출.
        Blob 버전이 현재 버전과 다르면 reload.
        """
        blob_version = await asyncio.to_thread(self._get_blob_version_sync)
        if blob_version and blob_version != self._version:
            logger.info(f"[RAGEngine] 버전 변경 감지 → reload | {self._version[:19]} → {blob_version[:19]}")
            async with self._lock:
                success = await asyncio.to_thread(self._load_index_from_blob_sync)
                if not success:
                    logger.warning("[RAGEngine] Blob reload 실패 → MongoDB rebuild")
                    await self.build_from_mongo()
        else:
            logger.debug("[RAGEngine] 버전 동일 → 스킵")

    # ── 검색 ───────────────────────────────────────────────────────────────────

    async def vector_search(
        self,
        query: str,
        top_k: int = 10,
        service: Optional[str] = None,
        audience: Optional[str] = None,   # 추후 확장용
    ) -> list[dict]:
        """
        FAISS 벡터 검색 + 메타데이터 필터.
        반환: [{"doc_id", "title", "service", "category", "status", "score"}, ...]
        """
        if self.index is None or self.index.ntotal == 0:
            return []

        query_emb = await self.embed_query(query)   # (1, DIM)
        scores, indices = await asyncio.to_thread(
            self.index.search, query_emb, min(top_k * 3, self.index.ntotal)
        )

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.meta):
                continue
            m = self.meta[idx]

            # 메타데이터 필터
            if service and m.get("service") != service:
                continue
            if m.get("status") == "archived":
                continue

            results.append({**m, "score": float(score)})
            if len(results) >= top_k:
                break

        return results


# ── 싱글톤 ────────────────────────────────────────────────────────────────────
_engine: Optional[RAGEngine] = None

def get_engine() -> RAGEngine:
    global _engine
    if _engine is None:
        _engine = RAGEngine()
    return _engine


# ── 앱 시작 시 초기화 (lifespan에서 호출) ─────────────────────────────────────
async def init_rag_engine() -> None:
    """
    main.py lifespan에서 호출.
    Blob에 인덱스 있으면 로드, 없으면 MongoDB에서 빌드.
    """
    engine = get_engine()
    logger.info("[RAGEngine] 초기화 시작")

    success = await asyncio.to_thread(engine._load_index_from_blob_sync)
    if success:
        logger.info("[RAGEngine] Blob에서 로드 완료")
    else:
        logger.info("[RAGEngine] Blob 없음 → MongoDB에서 빌드")
        await engine.build_from_mongo()


# ── 인스턴스 동기화 트리거 함수 (TRIGGER_REGISTRY에 등록) ─────────────────────
async def reload_rag_index_if_updated() -> None:
    """TRIGGER_REGISTRY에서 10분마다 호출되는 함수."""
    await get_engine().check_and_reload()


# ── HyDE (Hypothetical Document Embeddings) ───────────────────────────────────
def _extract_text(content) -> str:
    """Gemini response.content가 list일 때 텍스트 추출."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content)


async def generate_hyde_document(query: str) -> str:
    """
    질문에 대한 가상 답변 문서를 생성.
    이 가상 문서로 검색하면 실제 문서와의 시맨틱 매칭이 향상됨.
    특히 한국어 구어체 질문 → 문서체 검색어 변환에 효과적.
    """
    from src.core.llm_config import LLM_LIGHT

    prompt = f"""다음 질문에 대한 답변을 와이드모바일 지식베이스 문서 형식으로 작성해주세요.
실제 정보가 없어도 괜찮습니다. 검색을 위한 가상 문서입니다.
200자 이내로 간결하게 작성하세요.

질문: {query}

답변 문서:"""

    try:
        response = await LLM_LIGHT.ainvoke(prompt)
        return _extract_text(response.content)
    except Exception as e:
        logger.warning(f"[HyDE] 생성 실패, 원본 쿼리 사용: {e}")
        return query


# ── Reranking ─────────────────────────────────────────────────────────────────
async def rerank_results(query: str, candidates: list[dict], top_n: int = 3) -> list[dict]:
    """
    Claude(LLM_LIGHT)로 후보 문서를 재점수 매김.
    top_k(10개) → rerank → top_n(3개) 반환.
    """
    if not candidates:
        return []
    if len(candidates) <= top_n:
        return candidates

    from src.core.llm_config import LLM_LIGHT

    # 후보 문서 목록 구성
    candidates_text = "\n".join([
        f"[{i+1}] 제목: {c['title']} | 서비스: {c['service']} > {c['category']}"
        for i, c in enumerate(candidates)
    ])

    prompt = f"""다음 사용자 질문과 후보 문서 목록이 있습니다.
가장 관련성 높은 문서 번호를 {top_n}개 골라서 JSON 배열로만 답하세요.
예시: [2, 5, 1]

질문: {query}

후보 문서:
{candidates_text}

관련성 높은 순서로 {top_n}개 번호 (JSON 배열만):"""

    try:
        response = await LLM_LIGHT.ainvoke(prompt)
        content = _extract_text(response.content).strip()
        # JSON 파싱
        import re
        match = re.search(r'\[[\d,\s]+\]', content)
        if not match:
            return candidates[:top_n]

        indices = json.loads(match.group())
        reranked = []
        for idx in indices:
            if 1 <= idx <= len(candidates):
                reranked.append(candidates[idx - 1])
        return reranked[:top_n]

    except Exception as e:
        logger.warning(f"[Rerank] 실패, 원본 순서 사용: {e}")
        return candidates[:top_n]


# ── 메인 검색 함수 (knowledge_router에서 호출) ────────────────────────────────
async def search_knowledge(
    query: str,
    top_k: int = 3,
    service: Optional[str] = None,
    use_hyde: bool = False,   # P2: description 기반 직접 검색으로 변경. HyDE 효과 재검증 후 결정.
    use_rerank: bool = False, # P2: TopicGraphRetriever가 자체 선택 로직 수행.
) -> dict:
    """
    FAISS 벡터 검색 → topic 후보 반환.

    반환:
    {
        "results": [{"topic_id", "title", "description", "score"}, ...],
        "pipeline": "vector" | "fallback" | "empty"
    }
    """
    engine = get_engine()
    pipeline_steps = []

    # ── Vector Search ─────────────────────────────────────────────────────────
    candidates = []
    if engine.index and engine.index.ntotal > 0:
        candidates = await engine.vector_search(
            query=query,
            top_k=top_k,
            service=service,
        )
        pipeline_steps.append("vector")
        logger.info(f"[RAG] 벡터 검색 후보: {len(candidates)}건")

    # ── MongoDB 텍스트 검색 fallback ──────────────────────────────────────────
    if not candidates:
        logger.info("[RAG] FAISS 결과 없음 → MongoDB 텍스트 검색 fallback")
        mongo_query: dict = {
            "schema_version": 2,
            "$or": [
                {"title":       {"$regex": query, "$options": "i"}},
                {"description": {"$regex": query, "$options": "i"}},
                {"tags":        {"$elemMatch": {"$regex": query, "$options": "i"}}},
            ]
        }
        cursor = boa_knowledge_collection.find(
            mongo_query,
            {"_id": 1, "title": 1, "description": 1, "tags": 1, "status": 1, "domain": 1}
        ).limit(top_k)
        docs = await cursor.to_list(length=None)
        candidates = [
            {
                "_id":         str(d["_id"]),
                "title":       d.get("title", ""),
                "description": d.get("description", ""),
                "tags":        d.get("tags", []),
                "status":      d.get("status", ""),
                "domain":      d.get("domain"),
                "score":       0.5,
            }
            for d in docs
        ]
        pipeline_steps.append("fallback")

    # ── 최종 결과 조합 ────────────────────────────────────────────────────────
    results = []
    for c in candidates:
        results.append({
            "topic_id":    c.get("_id", ""),
            "title":       c.get("title", ""),
            "description": c.get("description", ""),
            "score":       round(c.get("score", 0.0), 4),
        })

    return {
        "results":  results,
        "pipeline": "+".join(pipeline_steps) if pipeline_steps else "empty",
        "query":    query,
    }