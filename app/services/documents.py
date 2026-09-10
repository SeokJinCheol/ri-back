import io
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from pypdf import PdfReader

from app.core.config import Settings
from app.schemas.documents import DocumentResponse


class DocumentError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def extract_text(filename: str, data: bytes, max_chars: int) -> str:
    try:
        if Path(filename).suffix.lower() == ".pdf":
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise DocumentError(422, "암호화된 PDF는 지원하지 않습니다.")
            parts = []
            length = 0
            for page in reader.pages:
                part = page.extract_text() or ""
                length += len(part) + 1
                if length > max_chars:
                    raise DocumentError(413, "문서의 추출 텍스트가 너무 큽니다.")
                parts.append(part)
            text = "\n".join(parts)
        else:
            text = data.decode("utf-8-sig")
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError(422, "파일을 읽을 수 없습니다. UTF-8 텍스트 또는 정상 PDF를 사용하세요.") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text or "\x00" in text:
        raise DocumentError(422, "추출 가능한 텍스트가 없습니다. 스캔 PDF는 OCR이 필요합니다.")
    if len(text) > max_chars:
        raise DocumentError(413, "문서의 추출 텍스트가 너무 큽니다.")
    return text


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    if not 0 <= overlap < size:
        raise ValueError("Invalid chunk size/overlap")
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end == len(text):
            break
        start = end - overlap
    return chunks


class DocumentService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connect(self) -> sqlite3.Connection:
        self.settings.document_db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.settings.document_db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, filename TEXT NOT NULL,
                size_bytes INTEGER NOT NULL, chunk_count INTEGER NOT NULL,
                embedding_model TEXT NOT NULL, embedding_dimensions INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS documents_project ON documents(project_id);
            CREATE TABLE IF NOT EXISTS chunks (
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL, content TEXT NOT NULL, embedding TEXT NOT NULL,
                PRIMARY KEY (document_id, chunk_index)
            );
        """)
        # Serialize the additive migration so concurrent requests can open older databases.
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
            if "embedding_provider" not in columns:
                connection.execute("ALTER TABLE documents ADD COLUMN embedding_provider TEXT NOT NULL DEFAULT 'ollama'")
        return connection

    def list_documents(self, project_id: str) -> list[DocumentResponse]:
        connection = self.connect()
        try:
            rows = connection.execute(
                "SELECT * FROM documents WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
            return [DocumentResponse(**dict(row)) for row in rows]
        finally:
            connection.close()

    def for_embedding(self, provider: str | None, model: str | None) -> "DocumentService":
        provider = provider or self.settings.embedding_provider
        if provider not in {"ollama", "openai"}:
            raise DocumentError(422, "지원하지 않는 임베딩 공급자입니다.")
        if provider == "openai":
            model = model or self.settings.openai_embedding_model
            if model not in {"text-embedding-3-small", "text-embedding-3-large"}:
                raise DocumentError(422, "지원하지 않는 OpenAI 임베딩 모델입니다.")
            if not self.settings.openai_api_key.get_secret_value().strip():
                raise DocumentError(503, "서버에 RAG_OPENAI_API_KEY를 설정한 후 재시작하세요.")
        else:
            if model and model != self.settings.embedding_model:
                raise DocumentError(422, "Ollama 모델은 서버의 RAG_EMBEDDING_MODEL 설정을 사용하세요.")
            model = self.settings.embedding_model
        settings = self.settings.model_copy(update={"embedding_provider": provider})
        if provider == "openai":
            settings.openai_embedding_model = model
        return DocumentService(settings)

    @property
    def model(self) -> str:
        return self.settings.openai_embedding_model if self.settings.embedding_provider == "openai" else self.settings.embedding_model

    def embed(self, chunks: list[str]) -> list[list[float]]:
        vectors = []
        is_openai = self.settings.embedding_provider == "openai"
        key = self.settings.openai_api_key.get_secret_value().strip()
        if is_openai and not key:
            raise DocumentError(503, "서버에 RAG_OPENAI_API_KEY를 설정한 후 재시작하세요.")
        try:
            with httpx.Client(timeout=self.settings.embedding_timeout_seconds) as client:
                for start in range(0, len(chunks), self.settings.embedding_batch_size):
                    batch = chunks[start:start + self.settings.embedding_batch_size]
                    if is_openai:
                        response = client.post(
                            "https://api.openai.com/v1/embeddings",
                            headers={"Authorization": f"Bearer {key}"},
                            json={"model": self.model, "input": batch, "encoding_format": "float"},
                        )
                    else:
                        response = client.post(
                            self.settings.embedding_base_url.rstrip("/") + "/api/embed",
                            json={"model": self.model, "input": batch, "truncate": False},
                        )
                    response.raise_for_status()
                    if is_openai:
                        items = response.json()["data"]
                        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                            raise ValueError("Invalid embedding response")
                        indices = [item["index"] for item in items]
                        if any(type(index) is not int for index in indices) or sorted(indices) != list(range(len(batch))):
                            raise ValueError("Invalid embedding indices")
                        result = [item["embedding"] for item in sorted(items, key=lambda item: item["index"])]
                    else:
                        result = response.json()["embeddings"]
                    if not isinstance(result, list) or len(result) != len(batch):
                        raise ValueError("Embedding count mismatch")
                    for vector in result:
                        if not isinstance(vector, list) or not vector or any(
                            isinstance(value, bool) or not isinstance(value, (int, float))
                            or not math.isfinite(value) for value in vector
                        ):
                            raise ValueError("Invalid embedding")
                        if vectors and len(vector) != len(vectors[0]):
                            raise ValueError("Embedding dimension mismatch")
                        vectors.append(vector)
        except httpx.HTTPStatusError as exc:
            if is_openai:
                try:
                    error = exc.response.json().get("error", {})
                    if not isinstance(error, dict):
                        error = {}
                except (ValueError, AttributeError):
                    error = {}
                if exc.response.status_code == 429 and (
                    error.get("type") == "insufficient_quota"
                    or error.get("code") in {"insufficient_quota", "credit_balance_exhausted"}
                ):
                    raise DocumentError(503, "OpenAI API 크레딧이 부족하거나 사용 한도를 초과했습니다. OpenAI Platform에서 결제·크레딧·프로젝트 사용 한도를 확인하세요.") from exc
                messages = {
                    400: "OpenAI가 입력을 거부했습니다. 청크 크기와 배치 크기를 줄여주세요.",
                    401: "OpenAI API 키가 유효하지 않습니다. 서버 설정을 확인하세요.",
                    403: "OpenAI 모델 접근 권한을 확인하세요.",
                    404: "OpenAI 모델을 사용할 수 없습니다. 모델 설정과 접근 권한을 확인하세요.",
                    429: "OpenAI 사용 한도 또는 요청 제한에 도달했습니다. 사용량을 확인하고 잠시 후 다시 시도하세요.",
                }
                raise DocumentError(503, messages.get(exc.response.status_code, "OpenAI 임베딩 서비스 오류입니다. 잠시 후 다시 시도하세요.")) from exc
            raise DocumentError(503, "Ollama 실행 상태와 임베딩 모델 설정을 확인하세요.") from exc
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            if is_openai:
                raise DocumentError(503, "OpenAI 임베딩 연결 또는 응답 오류입니다. 잠시 후 다시 시도하세요.") from exc
            raise DocumentError(503, "임베딩에 실패했습니다. Ollama 실행 상태와 임베딩 모델 설정을 확인하세요.") from exc
        return vectors

    def ingest(self, project_id: str, filename: str, data: bytes) -> DocumentResponse:
        if Path(filename).suffix.lower() not in {".txt", ".md", ".pdf"}:
            raise DocumentError(415, "TXT, MD, PDF 파일만 업로드할 수 있습니다.")
        if len(data) > self.settings.max_upload_bytes:
            raise DocumentError(413, "파일 업로드 용량 제한을 초과했습니다.")
        text = extract_text(filename, data, self.settings.max_document_chars)
        chunks = chunk_text(text, self.settings.chunk_size, self.settings.chunk_overlap)
        vectors = self.embed(chunks)
        document = DocumentResponse(
            id=str(uuid4()), project_id=project_id, filename=filename,
            size_bytes=len(data), chunk_count=len(chunks),
            embedding_provider=self.settings.embedding_provider,
            embedding_model=self.model, embedding_dimensions=len(vectors[0]),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        connection = self.connect()
        try:
            with connection:
                connection.execute(
                    "INSERT INTO documents (id, project_id, filename, size_bytes, chunk_count, embedding_model, embedding_dimensions, created_at, embedding_provider) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (document.id, document.project_id, document.filename, document.size_bytes,
                     document.chunk_count, document.embedding_model,
                     document.embedding_dimensions, document.created_at, document.embedding_provider),
                )
                connection.executemany(
                    "INSERT INTO chunks VALUES (?, ?, ?, ?)",
                    [(document.id, index, chunk, json.dumps(vector, allow_nan=False))
                     for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))],
                )
        finally:
            connection.close()
        return document
