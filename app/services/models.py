import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

from app.core.config import Settings
from app.schemas.models import ModelResponse, ModelWrite
from app.services.documents import DocumentError
from app.services.projects import ProjectService


class ModelService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connect(self):
        connection = ProjectService(self.settings).connect()
        connection.execute("""CREATE TABLE IF NOT EXISTS model_configs (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL,
            provider TEXT NOT NULL, model TEXT NOT NULL, encrypted_api_key TEXT,
            created_at TEXT NOT NULL
        )""")
        connection.execute("CREATE INDEX IF NOT EXISTS model_configs_project ON model_configs(project_id)")
        return connection

    def cipher(self) -> Fernet:
        path = self.settings.document_db_path.with_suffix('.key')
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic creation prevents readers from observing a partially written key.
        if not path.exists():
            import tempfile
            descriptor, temporary = tempfile.mkstemp(dir=path.parent)
            try:
                with os.fdopen(descriptor, 'wb') as output:
                    output.write(Fernet.generate_key())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            finally:
                os.unlink(temporary)
        try:
            return Fernet(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise DocumentError(503, "API 키 암호화 파일을 확인하세요.") from exc

    @staticmethod
    def public(row) -> ModelResponse:
        return ModelResponse(**{key: row[key] for key in ('id', 'project_id', 'name', 'provider', 'model', 'created_at')},
                             has_api_key=bool(row['encrypted_api_key']))

    def list_models(self, project_id: str) -> list[ModelResponse]:
        with closing(self.connect()) as connection:
            return [self.public(row) for row in connection.execute(
                'SELECT * FROM model_configs WHERE project_id = ? ORDER BY created_at, id', (project_id,))]

    def save(self, project_id: str, payload: ModelWrite, model_id: str | None = None) -> ModelResponse:
        with closing(self.connect()) as connection, connection:
            if not connection.execute('SELECT id FROM projects WHERE id = ?', (project_id,)).fetchone():
                raise DocumentError(404, "프로젝트를 찾을 수 없습니다.")
            previous = None
            if model_id:
                previous = connection.execute('SELECT * FROM model_configs WHERE id = ? AND project_id = ?',
                                              (model_id, project_id)).fetchone()
                if previous is None:
                    raise DocumentError(404, "모델 설정을 찾을 수 없습니다.")
            encrypted = None
            if payload.provider == 'openai':
                if payload.api_key is not None:
                    encrypted = self.cipher().encrypt(payload.api_key.get_secret_value().encode()).decode()
                elif previous and previous['provider'] == 'openai':
                    encrypted = previous['encrypted_api_key']
                if not encrypted:
                    raise DocumentError(422, "OpenAI API 키를 입력하세요.")
            model_id = model_id or str(uuid4())
            created_at = previous['created_at'] if previous else datetime.now(timezone.utc).isoformat()
            connection.execute('''INSERT INTO model_configs VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, provider=excluded.provider,
                model=excluded.model, encrypted_api_key=excluded.encrypted_api_key''',
                (model_id, project_id, payload.name, payload.provider, payload.model, encrypted, created_at))
            row = connection.execute('SELECT * FROM model_configs WHERE id = ?', (model_id,)).fetchone()
            return self.public(row)

    def delete(self, project_id: str, model_id: str):
        with closing(self.connect()) as connection, connection:
            if connection.execute('DELETE FROM model_configs WHERE id = ? AND project_id = ?',
                                  (model_id, project_id)).rowcount == 0:
                raise DocumentError(404, "모델 설정을 찾을 수 없습니다.")

    def embedding_settings(self, project_id: str, model_id: str) -> Settings:
        with closing(self.connect()) as connection:
            row = connection.execute('SELECT * FROM model_configs WHERE id = ? AND project_id = ?',
                                     (model_id, project_id)).fetchone()
        if row is None:
            raise DocumentError(404, "모델 설정을 찾을 수 없습니다. 설정에서 모델을 등록하세요.")
        updates = {'embedding_provider': row['provider']}
        if row['provider'] == 'openai':
            try:
                key = self.cipher().decrypt(row['encrypted_api_key'].encode()).decode()
            except InvalidToken as exc:
                raise DocumentError(503, "API 키를 읽을 수 없습니다. 설정에서 키를 다시 입력하세요.") from exc
            updates.update(openai_api_key=SecretStr(key), openai_embedding_model=row['model'])
        else:
            updates['embedding_model'] = row['model']
        return self.settings.model_copy(update=updates)
