"""Text-only generation adapters. Provider responses never become public error details."""
from contextlib import closing

import httpx
from cryptography.fernet import InvalidToken

from app.services.documents import DocumentError
from app.services.models import ModelService


class GenerationService:
    def __init__(self, settings):
        self.settings = settings

    def generate(self, project_id: str, model_id: str, instructions: str, prompt: str) -> str:
        models = ModelService(self.settings)
        with closing(models.connect()) as connection:
            row = connection.execute('SELECT * FROM model_configs WHERE project_id = ? AND id = ?',
                                     (project_id, model_id)).fetchone()
        if row is None or row['purpose'] != 'generation':
            raise DocumentError(422, '서비스 설정에서 답변 생성 모델을 선택하세요.')
        try:
            with httpx.Client(timeout=self.settings.embedding_timeout_seconds) as client:
                if row['provider'] == 'openai':
                    key = models.cipher().decrypt(row['encrypted_api_key'].encode()).decode()
                    response = client.post('https://api.openai.com/v1/responses',
                        headers={'Authorization': f'Bearer {key}'},
                        json={'model': row['model'], 'instructions': instructions, 'input': prompt,
                              'store': False, 'max_output_tokens': 4096})
                    response.raise_for_status()
                    data = response.json()
                    if data.get('status') != 'completed':
                        raise ValueError('Incomplete response')
                    text = '\n'.join(part['text'] for item in data['output'] if item.get('type') == 'message'
                                     for part in item.get('content', []) if part.get('type') == 'output_text')
                else:
                    response = client.post(self.settings.embedding_base_url.rstrip('/') + '/api/chat',
                        json={'model': row['model'], 'stream': False,
                              'messages': [{'role': 'system', 'content': instructions}, {'role': 'user', 'content': prompt}],
                              'options': {'num_predict': 2048}})
                    response.raise_for_status()
                    data = response.json()
                    if not data.get('done') or data.get('done_reason') == 'length':
                        raise ValueError('Incomplete response')
                    text = data['message']['content']
            if not isinstance(text, str) or not text.strip() or len(text) > 50000:
                raise ValueError('Invalid response')
            return text.strip()
        except InvalidToken as exc:
            raise DocumentError(503, 'API 키를 읽을 수 없습니다. 모델 설정에서 키를 다시 등록하세요.') from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            details = {401: 'API 키 인증에 실패했습니다.', 403: '모델 접근 권한이 없습니다.',
                       404: '모델을 찾을 수 없습니다. 모델 ID 또는 Ollama 설치 상태를 확인하세요.',
                       429: '모델 사용 한도 또는 요청 제한에 도달했습니다.'}
            raise DocumentError(503, details.get(status, '답변 모델 요청에 실패했습니다. 모델 설정을 확인하세요.')) from exc
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise DocumentError(503, '답변 모델 연결 또는 응답 오류입니다. 연결 테스트 후 다시 시도하세요.') from exc
