# Real Iron RAG Backend

Python 3.11+ / FastAPI / Pydantic v2 기반의 RAG 백엔드 기본 구조입니다.

## 디렉터리 구조

```text
ri-back/
├── app/
│   ├── main.py              # FastAPI 생성 및 라우터 등록
│   ├── api/
│   │   ├── dependencies.py  # 서비스와 외부 구현체 의존성 주입
│   │   ├── router.py        # /api/v1 라우터 구성
│   │   └── v1/
│   │       ├── health.py    # 프로세스 헬스 체크
│   │       └── rag.py       # 질의 API
│   ├── core/
│   │   ├── config.py        # 환경변수 및 .env 설정
│   │   └── exceptions.py    # 도메인 예외
│   ├── schemas/            # Pydantic 요청/응답 모델
│   ├── services/rag.py     # 검색 → 답변 생성 흐름
│   └── rag/interfaces.py  # Retriever / Generator 계약
├── main.py                # 기존 main:app 실행 경로 호환
├── .env.example
├── .gitignore
├── requirements.txt
└── test_main.http          # PyCharm HTTP 요청 예제
```

## 실행

프로젝트 루트에서 가상환경을 활성화한 후 실행합니다.
가상환경이 없다면 먼저 `python -m venv .venv`로 생성합니다.

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
# .env가 없을 때 최초 1회 복사 (기존 파일은 유지)
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python -m uvicorn app.main:app --reload
```

기존 `python -m uvicorn main:app --reload` 실행 방식도 지원합니다.
Swagger UI: <http://127.0.0.1:8000/docs>

## API

| 메서드 | 경로 | 동작 |
| --- | --- | --- |
| GET | `/` | 앱 정보 |
| GET | `/api/v1/health` | 프로세스 정상 실행 시 `{"status":"ok"}` |
| POST | `/api/v1/rag/query` | 질문과 top_k 검증 후 RAG 서비스 호출 |

질문은 공백 제거 후 1~4,000자, top_k는 정수 1~20이며 기본값은 5입니다.
잘못된 입력은 422, RAG 구현체 미연결 시에는 503을 반환합니다.
헬스 체크는 LLM이나 DB의 연결 상태를 검사하지 않습니다.

## RAG 확장 지점

1. `app/rag/`에 임베딩·벡터 검색을 수행하는 `Retriever` 구현체를 추가합니다.
2. 같은 위치에 선택한 LLM을 호출하는 `Generator` 구현체를 추가합니다.
3. `app/api/dependencies.py`에서 `RAGService(retriever=..., generator=...)`로 연결합니다.
4. 문서 수집은 `app/services/documents.py`에서 추출 → 청킹 → 선택한 공급자 임베딩 → SQLite 저장 순서로 처리합니다.

문서 수집과 임베딩 저장은 구현되어 있습니다. 답변 생성·벡터 검색 및 서버 인증은 아직 연결되지 않았습니다.
네트워크 클라이언트를 연결할 때는 앱 lifespan에서 생성·종료를 관리하고,
API 키는 `Settings`와 `.env`에 추가합니다. `.env`는 Git에서 제외됩니다.

구조 참고: [FastAPI 라우터 분리](https://fastapi.tiangolo.com/tutorial/bigger-applications/),
[환경변수 설정](https://fastapi.tiangolo.com/advanced/settings/).

## 문서 업로드

1. Ollama를 실행하고 `ollama pull embeddinggemma`로 기본 임베딩 모델을 준비합니다.
2. 위 실행 절차대로 의존성을 설치하고 FastAPI를 실행합니다.
3. `ri-front`에서 `npm install` 후 `npm run dev`를 실행합니다.
4. 프로젝트를 선택하고 Sidebar → Documents → 파일 선택 → Upload를 누릅니다.

프론트의 `.env.example`을 참고해 `VITE_API_BASE_URL`을 설정할 수 있습니다.
기본 주소는 `http://127.0.0.1:8000/api/v1`이며 Vite 재시작/재빌드 시 반영됩니다.
백엔드 설정은 `.env.example`을 참고하세요. 개발 서버 포트가 바뀌면 CORS 목록에도 추가합니다.
`null` origin은 Electron의 로컬 파일 화면용입니다. 현재 API는 로컬 개발용이며,
프로젝트 ID로 목록을 구분하지만 인증/소유권 검증을 제공하지 않습니다.

- `POST /api/v1/documents`: multipart 필드 `project_id`(UUID), `file`.
- `GET /api/v1/documents?project_id=<UUID>`: 프로젝트별 완료 문서 목록.
- 지원: UTF-8 TXT/MD 및 텍스트 PDF, 기본 10MiB. 암호화/스캔 PDF와 DOCX/HWP는 미지원.
- 기본 800 **문자** 단위, 100자 중복 청킹. 추출 텍스트 최대 100만 자.
- Ollama `/api/embed` 또는 OpenAI `/v1/embeddings`를 배치 호출하고 전체 성공 후 SQLite 트랜잭션으로 저장합니다.
- 원본 파일은 보관하지 않으며 메타데이터, 청크 텍스트, 임베딩 벡터(JSON)를
  `data/documents.sqlite3`에 영속 저장합니다. 검색용 벡터 인덱스는 아직 없습니다.
- 응답 201은 임베딩·저장 완료를 뜻합니다. 413 용량 초과, 415 형식 미지원,
  422 파일/요청 오류, 503 임베딩 실패. 실패한 문서는 저장하지 않습니다.
- 전송률 100% 이후 청킹·임베딩 처리 표시가 나타납니다. 처리는 동기 요청이므로
  완료까지 화면을 유지하세요. 동일 파일을 다시 업로드하면 별도 문서로 저장됩니다.

검증: `python -m unittest discover -s tests -v` (Ollama 응답을 모의 처리).
실제 임베딩 검증은 Ollama 모델 준비 후 Documents에서 문서를 업로드하세요.

구현 참고: [Ollama Embed API](https://docs.ollama.com/api/embed),
[FastAPI 파일 업로드](https://fastapi.tiangolo.com/tutorial/request-files/).

## OpenAI 임베딩 선택

Documents 화면의 **임베딩 모델**에서 OpenAI `text-embedding-3-small` 또는
`text-embedding-3-large`를 선택한 후 업로드합니다. Ollama와 서버 기본 설정도 선택할 수 있습니다.
OpenAI 선택 시 문서에서 추출한 청크가 OpenAI로 전송되며 API 비용이 발생합니다.

`ri-back/.env`에 아래 항목을 설정하고 백엔드를 재시작하세요.

```dotenv
RAG_OPENAI_API_KEY=여기에_실제_API_키
# 서버 기본값도 OpenAI로 사용하려면 설정
RAG_EMBEDDING_PROVIDER=openai
RAG_OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

키는 서버에서만 읽습니다. `OPENAI_API_KEY`도 지원하며 `RAG_OPENAI_API_KEY`가 우선합니다.
키를 프론트의 `VITE_` 변수에 넣지 마세요. Ollama 주소와 모델 설정은 별도로 유지됩니다.
OpenAI 주소는 `https://api.openai.com/v1/embeddings`로 고정되어 있습니다.

업로드 multipart에 선택적으로 `embedding_provider` (`ollama`/`openai`)와
`embedding_model`을 전달할 수 있습니다. 생략하면 서버 기본값을 사용합니다.
Ollama는 서버에 설정된 모델만, OpenAI는 위 두 모델만 허용합니다.
키 누락·인증 실패·요청 한도 오류는 503으로 안내하며 실패한 문서는 저장되지 않습니다.
청크 크기를 크게 늘려 OpenAI 토큰 제한을 넘기면 청크/배치 크기를 줄여야 합니다.

기존 SQLite에는 `embedding_provider` 열을 자동 추가하며 기존 문서는 Ollama로 유지합니다.
새 모델 선택은 새 업로드에 적용됩니다. 검색 구현 시 공급자·모델·차원이 같은 벡터끼리 비교해야 합니다.
기존 문서를 새 모델로 검색하려면 다시 임베딩하세요.

공식 참고: [OpenAI Embeddings API](https://developers.openai.com/api/reference/resources/embeddings/methods/create).
