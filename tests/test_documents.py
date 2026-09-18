import io
import json
import tempfile
import sqlite3
from pydantic import SecretStr
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.api.v1.documents import get_document_service
from app.core.config import Settings
from app.main import create_app
from app.services.documents import DocumentService, chunk_text


class DocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, document_db_path=Path(self.temp.name) / "docs.db",
                                 chunk_size=100, chunk_overlap=20, embedding_batch_size=2)
        self.service = DocumentService(self.settings)
        app = create_app()
        app.dependency_overrides[get_document_service] = lambda: self.service
        self.client = TestClient(app)
        self.project = str(uuid4())

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def upload(self, data=b"hello", name="test.txt"):
        return self.client.post("/api/v1/documents", data={"project_id": self.project},
                                files={"file": (name, data, "application/octet-stream")})

    @staticmethod
    def embeddings(_client, url, **kwargs):
        assert url.endswith("/api/embed")
        assert kwargs["json"]["truncate"] is False
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2] for _ in kwargs["json"]["input"]]},
                              request=httpx.Request("POST", url))

    def test_upload_batches_persists_and_scopes_project(self):
        with patch.object(httpx.Client, "post", autospec=True, side_effect=self.embeddings) as embed:
            # Call service directly to avoid patching the TestClient's own post method.
            doc = self.service.ingest(self.project, "한글.md", ("한글 문서 " * 100).encode())
            self.assertGreater(embed.call_count, 1)
        listed = self.client.get("/api/v1/documents", params={"project_id": self.project}).json()
        self.assertEqual(listed[0]["id"], doc.id)
        self.assertEqual(self.client.get("/api/v1/documents", params={"project_id": str(uuid4())}).json(), [])
        connection = self.service.connect()
        try:
            rows = connection.execute("SELECT * FROM chunks").fetchall()
            self.assertEqual(len(rows), doc.chunk_count)
            self.assertEqual(json.loads(rows[0]["embedding"]), [0.1, 0.2])
        finally:
            connection.close()
        self.assertEqual(DocumentService(self.settings).list_documents(self.project)[0].id, doc.id)

    def test_multipart_upload(self):
        with patch.object(self.service, "embed", side_effect=lambda chunks: [[0.1, 0.2] for _ in chunks]):
            response = self.upload("한국어 테스트".encode())
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["status"], "completed")

    def test_delete_cascades_chunks_and_preserves_other_documents(self):
        with patch.object(self.service, "embed", side_effect=lambda chunks: [[0.1, 0.2] for _ in chunks]):
            doc = self.service.ingest(self.project, "delete.txt", b"x" * 250)
            kept = self.service.ingest(self.project, "keep.txt", b"keep")
        self.assertGreater(doc.chunk_count, 1)
        url = f"/api/v1/documents/{doc.id}"
        response = self.client.delete(url, params={"project_id": self.project})
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.content, b"")
        self.assertEqual([item.id for item in DocumentService(self.settings).list_documents(self.project)], [kept.id])
        connection = self.service.connect()
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM chunks WHERE document_id = ?", (doc.id,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM chunks WHERE document_id = ?", (kept.id,)).fetchone()[0], kept.chunk_count)
        finally:
            connection.close()
        self.assertEqual(self.client.get(f"{url}/chunks", params={"project_id": self.project}).status_code, 404)
        self.assertEqual(self.client.delete(url, params={"project_id": self.project}).status_code, 404)

    def test_delete_scopes_project_and_validates_ids(self):
        with patch.object(self.service, "embed", return_value=[[0.1, 0.2]]):
            doc = self.service.ingest(self.project, "keep.txt", b"keep")
        url = f"/api/v1/documents/{doc.id}"
        self.assertEqual(self.client.delete(url, params={"project_id": str(uuid4())}).status_code, 404)
        self.assertEqual(self.client.delete(f"/api/v1/documents/{uuid4()}", params={"project_id": self.project}).status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 422)
        self.assertEqual(self.client.delete(url, params={"project_id": "invalid"}).status_code, 422)
        self.assertEqual(self.client.delete("/api/v1/documents/invalid", params={"project_id": self.project}).status_code, 422)
        page = self.client.get(f"{url}/chunks", params={"project_id": self.project})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.json()["total"], 1)

    def test_invalid_files(self):
        for name, data, status in [("x.exe", b"x", 415), ("x.txt", b"", 422),
                                   ("x.txt", b"\xff", 422), ("x.pdf", b"invalid", 422),
                                   ("x.md", b"\x00binary", 422)]:
            with self.subTest(name=name, data=data):
                self.assertEqual(self.upload(data, name).status_code, status)
        self.assertEqual(self.service.list_documents(self.project), [])

    def test_limits(self):
        self.settings.max_upload_bytes = 4
        self.assertEqual(self.upload(b"12345").status_code, 413)
        self.settings.max_upload_bytes = 100
        self.settings.max_document_chars = 4
        self.assertEqual(self.upload(b"12345").status_code, 413)

    def test_blank_pdf(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        buffer = io.BytesIO()
        writer.write(buffer)
        self.assertEqual(self.upload(buffer.getvalue(), "blank.pdf").status_code, 422)

    def test_embedding_failure_does_not_store(self):
        with patch("app.services.documents.httpx.Client") as provider:
            provider.return_value.__enter__.return_value.post.side_effect = httpx.ConnectError("offline")
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.service.list_documents(self.project), [])

    def test_bad_vector_rejected(self):
        with patch("app.services.documents.httpx.Client") as provider:
            provider.return_value.__enter__.return_value.post.return_value.json.return_value = {"embeddings": [[]]}
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.service.list_documents(self.project), [])

    def test_project_validation(self):
        self.project = "invalid"
        self.assertEqual(self.upload().status_code, 422)
        self.assertEqual(self.client.get("/api/v1/documents").status_code, 422)

    def test_chunk_overlap_and_tail(self):
        text = "abcdefghijklmnopqrstuvwxyz"
        chunks = chunk_text(text, 10, 3)
        self.assertEqual(chunks, [text[:10], text[7:17], text[14:24], text[21:]])
        self.assertEqual(chunks[0] + "".join(chunk[3:] for chunk in chunks[1:]), text)
        self.assertEqual(chunk_text("1234567890", 10, 3), ["1234567890"])
        with self.assertRaises(ValueError):
            chunk_text(text, 10, 10)

    def test_openai_selection_and_order(self):
        self.settings.openai_api_key = SecretStr("test-key")
        with patch("app.services.documents.httpx.Client") as provider:
            post = provider.return_value.__enter__.return_value.post
            post.return_value.json.return_value = {"data": [
                {"index": 1, "embedding": [0.3, 0.4]},
                {"index": 0, "embedding": [0.1, 0.2]},
            ]}
            response = self.client.post("/api/v1/documents", data={
                "project_id": self.project, "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-large",
            }, files={"file": ("x.txt", b"x" * 150)})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(post.call_args.args[0], "https://api.openai.com/v1/embeddings")
            self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer test-key"})
            self.assertEqual(post.call_args.kwargs["json"]["model"], "text-embedding-3-large")
            self.assertEqual(post.call_args.kwargs["json"]["encoding_format"], "float")
        self.assertEqual(response.json()["embedding_provider"], "openai")
        self.assertEqual(self.settings.embedding_provider, "ollama")
        connection = self.service.connect()
        try:
            vectors = connection.execute("SELECT embedding FROM chunks ORDER BY chunk_index").fetchall()
            self.assertEqual(json.loads(vectors[0][0]), [0.1, 0.2])
        finally:
            connection.close()
        self.assertEqual(self.service.list_documents(self.project)[0].embedding_provider, "openai")

    def test_openai_missing_key_and_invalid_selection(self):
        self.settings.openai_api_key = SecretStr("")
        for provider_name, model, expected in [
            ("openai", "text-embedding-3-small", 503),
            ("openai", "invalid", 422), ("unknown", "invalid", 422),
            ("ollama", "text-embedding-3-small", 422),
        ]:
            with self.subTest(provider=provider_name, model=model):
                response = self.client.post("/api/v1/documents", data={
                    "project_id": self.project, "embedding_provider": provider_name, "embedding_model": model,
                }, files={"file": ("x.txt", b"hello")})
                self.assertEqual(response.status_code, expected, response.text)
        self.assertEqual(self.service.list_documents(self.project), [])

    def test_openai_default_and_errors(self):
        self.settings.embedding_provider = "openai"
        self.settings.openai_api_key = SecretStr("test-key")
        for status in [401, 429, 500]:
            with self.subTest(status=status), patch("app.services.documents.httpx.Client") as provider:
                request = httpx.Request("POST", "https://api.openai.com/v1/embeddings")
                provider.return_value.__enter__.return_value.post.return_value = httpx.Response(
                    status, request=request, json={"error": {"message": "secret upstream detail"}})
                response = self.upload()
                self.assertEqual(response.status_code, 503)
                self.assertNotIn("secret upstream detail", response.text)
                self.assertNotIn("test-key", response.text)
        self.assertEqual(self.service.list_documents(self.project), [])

    def test_openai_credit_exhausted_message(self):
        self.settings.embedding_provider = "openai"
        self.settings.openai_api_key = SecretStr("test-key")
        with patch("app.services.documents.httpx.Client") as provider:
            provider.return_value.__enter__.return_value.post.return_value = httpx.Response(
                429, request=httpx.Request("POST", "https://api.openai.com/v1/embeddings"),
                json={"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted"}})
            response = self.upload()
        self.assertEqual(response.status_code, 503)
        self.assertIn("크레딧", response.json()["detail"])
        self.assertEqual(self.service.list_documents(self.project), [])

    def test_openai_duplicate_indices_rejected(self):
        self.settings.embedding_provider = "openai"
        self.settings.openai_api_key = SecretStr("test-key")
        with patch("app.services.documents.httpx.Client") as provider:
            provider.return_value.__enter__.return_value.post.return_value.json.return_value = {
                "data": [{"index": 0, "embedding": [0.1]}, {"index": 0, "embedding": [0.2]}]}
            self.assertEqual(self.upload(b"x" * 150).status_code, 503)
        self.assertEqual(self.service.list_documents(self.project), [])

    def test_old_database_migration(self):
        connection = sqlite3.connect(self.settings.document_db_path)
        connection.execute("""CREATE TABLE documents (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, filename TEXT NOT NULL,
            size_bytes INTEGER NOT NULL, chunk_count INTEGER NOT NULL,
            embedding_model TEXT NOT NULL, embedding_dimensions INTEGER NOT NULL, created_at TEXT NOT NULL
        )""")
        connection.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           ("old", self.project, "old.txt", 10, 1, "embeddinggemma", 2, "2026-09-09"))
        connection.commit()
        connection.close()
        for _ in range(2):
            records = self.service.list_documents(self.project)
            self.assertEqual(records[0].embedding_provider, "ollama")
            self.assertEqual(records[0].id, "old")

    def test_cors(self):
        response = self.client.options("/api/v1/documents", headers={
            "Origin": "http://localhost:5174", "Access-Control-Request-Method": "POST"})
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5174")


if __name__ == "__main__":
    unittest.main()
