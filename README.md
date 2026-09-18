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
- `DELETE /api/v1/documents/{document_id}?project_id=<UUID>`: 문서와 관련 청크·임베딩을 함께 영구 삭제합니다. 성공 시 본문 없이 204, 해당 프로젝트에 문서가 없으면 404를 반환합니다. 인덱스는 유지됩니다. Documents 목록과 문서 상세의 삭제 버튼에서 확인 후 실행할 수 있습니다.
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

## 프로젝트 저장

- `POST /api/v1/projects`: JSON `name`(공백 제거 후 1~80자), `description`(선택, 최대 200자)으로 생성합니다.
- `GET /api/v1/projects`: 저장된 프로젝트를 생성 순서로 조회합니다.
- 프로젝트는 문서와 같은 SQLite 파일(`RAG_DOCUMENT_DB_PATH`, 기본 `data/documents.sqlite3`)의 `projects` 테이블에 저장됩니다. 테이블은 처음 사용할 때 자동 생성됩니다.
- 프론트는 로그인 후와 헤더 프로젝트 드롭다운을 열 때 목록을 조회하고, 생성 성공 후 새 프로젝트를 선택합니다. 마지막 선택 ID만 브라우저에 보관합니다.
- 이전 localStorage 프로젝트는 자동 이관하지 않습니다. 기존 브라우저 데이터는 그대로 남지만 새 목록에는 SQLite에 저장된 프로젝트만 표시됩니다.

## 설정에서 임베딩 모델 등록

설정 → 모델 등록에서 프로젝트별 이름, 공급자, 임베딩 모델 ID와 API 키를 저장합니다.
OpenAI는 `text-embedding-3-small`, `text-embedding-3-large`, Ollama는 설치된 임베딩 모델 ID를 지원합니다.
새 모델을 학습하거나 다운로드하는 기능은 아닙니다. 등록 후 Documents에서 모델을 선택하면 서버 재시작 없이 적용됩니다.
등록 시 외부 연결은 호출하지 않으며 키 유효성·모델 설치 여부는 실제 업로드할 때 확인합니다.

- `GET/POST /api/v1/projects/{project_id}/models`: 목록·생성.
- `PUT/DELETE /api/v1/projects/{project_id}/models/{model_id}`: 수정·삭제.
- 생성/수정 JSON: `name`, `provider`, `model`, `api_key`(OpenAI 생성 시 필수, 수정 시 생략하면 기존 키 유지).
- 업로드 multipart의 `model_config_id`로 등록 모델을 선택합니다. 해당 프로젝트의 모델만 사용할 수 있습니다.
- 모델 설정은 SQLite `model_configs`에 저장하며 API 키는 Fernet 암호문으로 저장합니다.
  암호화 키 파일은 DB와 같은 경로에서 확장자를 `.key`로 바꾼 파일이며 소유자만 읽고 쓸 수 있습니다(0600).
  DB 백업 시 이 키 파일도 안전하게 보관해야 합니다. 키 파일을 잃으면 설정에서 API 키를 다시 등록해야 합니다.
- 응답에는 키 원문·암호문을 포함하지 않으며 `has_api_key`만 반환합니다. 브라우저 저장소에는 키를 보관하지 않습니다.
- 기존 `.env` 기반 API 호출은 호환을 위해 유지됩니다. 화면에서는 등록한 모델을 사용합니다.
- 현재 서버 인증은 구현되지 않아 프로젝트 범위는 사용자별 접근 제어가 아닙니다. 기존 로컬 실행 범위에서 사용하는 기능입니다.

의존성 추가 후 `pip install -r requirements.txt`를 실행하고 백엔드를 재시작하세요.

## 프로젝트 사용자 등록

프로젝트 생성 화면에서 **사용자 추가**를 눌러 이름·이메일을 입력할 수 있습니다(선택, 최대 100명).
`POST /api/v1/projects`의 `members` 배열로 전달하며, 프로젝트와 `project_members`를 같은 SQLite 트랜잭션에서 저장합니다.
이메일은 앞뒤 공백 제거 및 소문자 변환 후 프로젝트 안에서 중복을 검사합니다.
프로젝트 조회 응답에도 `members`가 포함되며 기존 프로젝트는 빈 목록으로 유지합니다.
설정에서 등록 사용자를 확인하고 서비스 생성·수정의 사용자 선택 목록에서 선택할 수 있습니다.
프로젝트 참여자 등록 기능이며 로그인 계정 생성, 이메일 초대 발송, 서버 접근 권한 부여는 포함하지 않습니다.

## 인덱스 관리와 문서 연결

Index 메뉴에서 인덱스 목록·상세를 확인하고 이름, 설명, 서비스를 선택해 생성할 수 있습니다.
서비스 상세에서도 하위 인덱스로 이동하거나 해당 서비스를 선택한 상태로 인덱스를 생성할 수 있습니다.
인덱스 상세의 문서 업로드 버튼은 Documents 화면에 인덱스를 미리 선택합니다.
Documents 화면에서는 업로드할 인덱스를 반드시 선택합니다.

- `GET/POST /api/v1/projects/{project_id}/indices`: 목록(문서 수 포함)·생성.
- 생성 JSON: `name`(1~80자), `description`(최대 500자), `service_id`(UUID), `service_name`(1~80자).
- `GET/DELETE /api/v1/projects/{project_id}/indices/{index_id}`: 상세·삭제.
- `GET /api/v1/documents?project_id=...&index_id=...`: 인덱스별 문서 목록.
- 업로드 multipart의 `index_id`로 문서를 연결합니다. 서버에서 프로젝트 일치와 인덱스 존재 여부를 검증합니다.
- 인덱스 삭제 시 문서와 청크·임베딩은 보존하고 문서의 인덱스 연결만 해제합니다.
- SQLite `indices` 테이블과 `documents.index_id` 열은 자동 생성/추가됩니다.
  기존 문서는 인덱스 미지정으로 보존되며, 기존 API 클라이언트의 `index_id` 생략도 허용합니다.
- 서비스 목록과 설정은 서버 DB에서 조회합니다. 서비스 이름을 변경하면 연결된 인덱스의 서비스 이름도 갱신합니다.
  서비스 삭제 시 서비스 설정과 사용자 등록만 삭제하며, 인덱스와 문서는 유지됩니다. 인덱스 API 자체의 서비스 사용자 권한 검증은 별도 적용이 필요합니다.

## 날짜, 서비스 한도, 청크 조회

- 서비스 생성·수정 시 `PUT /api/v1/projects/{project_id}/services/{service_id}`로 설정을 저장하고 서버가 반환한 `created_at`, `updated_at`을 사용합니다.
- `GET /api/v1/projects/{project_id}/services`는 요청 사용자가 관리자 또는 멤버인 서비스를 반환합니다. 대시보드·서비스 화면·인덱스 생성 화면에서 이 목록을 사용하며 브라우저 로컬 저장소에 의존하지 않습니다. 서버에 저장되지 않은 과거 미리보기 항목은 표시되지 않습니다.
- `DELETE /api/v1/projects/{project_id}/services/{service_id}`는 서비스 관리자만 실행할 수 있으며 성공 시 204를 반환합니다. 프론트엔드는 서버 성공 응답 후 목록에서 제거하고 실패 시 오류와 재시도를 제공합니다.
- 서비스 설정에 `index_limit`을 추가했습니다(기본 5개, 1~10000). 서비스 관리자 수정 화면에서 변경하며 현재 인덱스 수보다 낮출 수 없습니다. 서버는 생성·이동 시 트랜잭션 안에서 한도를 검사하므로 동시 요청도 한도를 초과할 수 없습니다.
- 현재 임시 로그인 구조에 맞춰 `X-User-Email`을 저장된 서비스 관리자 목록과 대조합니다. 이 헤더는 인증 토큰이 아니며 실제 사용자 인증·배포용 접근 제어를 대신하지 않습니다. 서비스 최초 저장 시 생성자가 전달된 관리자 목록에 포함되어야 합니다.
- 인덱스 `PUT /api/v1/projects/{project_id}/indices/{index_id}`와 문서 `PUT /api/v1/documents/{document_id}?project_id=...` 수정 시 생성일은 보존하고 수정일을 갱신합니다. 문서 수정 JSON은 `filename`, `index_id`이며 청크 내용과 임베딩은 그대로 보존합니다.
- 기존 인덱스·문서는 수정일을 생성일로 초기화합니다. 인덱스 삭제로 문서 연결이 해제될 때도 문서 수정일을 갱신합니다.
- 문서당 임베딩 결과 최대 크기는 **100,000,000바이트(100MB)**입니다. DB에 저장하는 임베딩 벡터 JSON의 UTF-8 바이트 합계를 사용하며 원본·텍스트·DB 파일 크기와 구분합니다. 초과 시 413을 반환하고 문서·청크를 저장하지 않습니다. 원본 파일의 기존 10MiB 업로드 제한은 별도로 유지됩니다.
- `GET /api/v1/documents/{document_id}/chunks?project_id=...&offset=0&limit=50`: 개별 저장된 청크를 순서대로 조회합니다. `limit`은 1~100, 기본 50입니다. 각 항목은 청크 번호, 본문, 임베딩 크기, 차원을 반환합니다. 벡터 전체는 목록 응답에 포함하지 않습니다.
- Documents에서 문서 이름 또는 청크 개수를 누르면 문서 상세의 청크 목록을 볼 수 있습니다. 본문을 펼쳐 확인하고 이전·다음으로 페이지를 이동할 수 있습니다.

## 프로젝트 설정과 관리자

헤더 프로젝트 드롭다운의 **프로젝트 설정** 또는 Setting의 **프로젝트 정보·관리자 설정**에서
`/projects/settings`로 이동합니다. 프로젝트 이름·설명, 사용자 추가·제거, 관리자 지정·해제를 지원합니다.
메뉴는 현재 프로젝트 관리자에게 표시하며 직접 URL로 접근해도 서버에서 권한을 확인한 뒤 편집 화면을 표시합니다.

- 생성 시 현재 사용자를 `creator_email`과 관리자 멤버로 자동 저장합니다. 사용자는 생성자를 포함해 최대 100명입니다.
- 프로젝트 생성 화면에서 추가 사용자의 **프로젝트 관리자로 지정**을 선택할 수 있습니다.
- 생성자는 영구 관리자이며 설정 API로 제거·강등·이메일 변경할 수 없습니다. 지정 관리자도 다른 사용자의 관리자 역할을 변경할 수 있습니다.
- `GET/PUT /api/v1/projects/{project_id}/settings`: 생성자 또는 저장된 `admin` 역할 사용자만 조회·수정할 수 있습니다.
- 수정 JSON: `name`, `description`, `members`(`name`, `email`, `role`: `admin` 또는 `member`). 권한 확인과 수정은 같은 트랜잭션으로 처리합니다.
- 프로젝트 생성 API는 `X-User-Email`을 요구합니다. `X-User-Name`은 URL 인코딩한 표시 이름입니다.
- 기존 임시 로그인에 맞춰 이메일 헤더를 사용합니다. 헤더 자체는 사용자 신원을 인증하지 않으므로 실제 인증·배포용 보안은 별도 서버 로그인 연동이 필요합니다.
- 기존 프로젝트는 생성자 기록을 알 수 없어 `creator_email = NULL`로 유지합니다. 기존 사용자도 자동 승격하지 않으며 최초 관리자를 별도로 지정하기 전에는 설정을 변경할 수 없습니다.
- DB에는 프로젝트 생성자·수정일과 사용자 역할을 자동 추가합니다. 기존 생성일과 사용자 데이터는 보존합니다.
