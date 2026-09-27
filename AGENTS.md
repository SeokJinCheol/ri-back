# 백엔드 작업 가이드 (초안)

이 문서는 `ri-back` 전체에 적용하는 작업 규칙 초안이다. 실제 구현과 설정 파일을 우선 확인하고, 구조나 명령이 바뀌면 이 문서도 갱신한다.

## 기술과 구조

- Python 3.11 이상, FastAPI, Pydantic v2, pydantic-settings, Uvicorn을 사용한다.
- SQLite에 프로젝트·서비스·모델 설정·문서·청크·임베딩·대화를 저장한다.
- 외부 모델 통신은 HTTPX, PDF 텍스트 추출은 pypdf, 저장 API 키 암호화는 cryptography의 Fernet을 사용한다.
- 정확한 의존성 범위는 `requirements.txt`, 개발 도구는 `requirements-dev.txt`를 기준으로 한다.
- `app/main.py`: 앱 생성, CORS, 라우터와 API 문서 경로 구성. 루트 `main.py`는 실행 경로 호환용이다.
- `app/api/router.py`, `app/api/v1`: `/api/v1` 라우터와 HTTP 요청·응답 처리.
- `app/schemas`: Pydantic 요청·응답 스키마. `app/services`: 업무 규칙, 저장소 접근, 문서 처리와 모델 호출.
- `app/core/config.py`: 환경변수와 설정. `app/core/exceptions.py`: 공통 도메인 예외.
- `app/rag/interfaces.py`, `app/api/dependencies.py`: RAG 추상 계약과 의존성 연결 지점.
- `tests`: unittest 기반 API·서비스 테스트. `test_main.http`: 수동 HTTP 요청 예제.
- 일반 RAG 질의 확장 지점과 `chat`의 검색·답변 구현을 구분한다. 기능 지원 여부는 해당 라우터와 서비스 코드를 확인한다.

## 구현 규칙

- 라우터는 입력·의존성·HTTP 응답 처리를 맡고, 업무 로직은 기존 서비스 계층에 둔다.
- 요청과 응답은 Pydantic 스키마로 정의한다. 필수값, 길이·범위, UUID, 공급자·모델 제한을 기존 계약과 일관되게 검증한다.
- API 경로·필드·상태 코드가 바뀌면 프런트엔드 `src/api`와 관련 화면에 미치는 영향을 함께 확인한다.
- 데이터 조회·수정 시 프로젝트와 서비스 범위, 사용자 접근 조건을 유지한다. ID만으로 다른 범위의 데이터에 접근하지 않게 한다.
- `X-User-Email` 등 현재 사용자 헤더는 임시 식별 수단이다. 이를 검증된 인증으로 간주하지 않는다.
- SQLite 값은 SQL 매개변수로 전달한다. 관련 쓰기는 트랜잭션으로 묶고 연결을 확실히 닫는다.
- 스키마 변경은 기존 DB를 보존하며 반복 실행 가능한 방식으로 처리한다. 기존 데이터와 신규 DB 모두를 확인한다.
- 문서 처리 실패 시 일부 청크나 임베딩이 남지 않도록 한다. 문서 삭제 시 관련 데이터 정리 동작도 유지한다.
- 검색 대상의 임베딩 공급자·모델·차원 호환성을 확인한다. 서로 다른 벡터 공간을 그대로 비교하지 않는다.
- 외부 HTTP 요청에는 타임아웃과 실패 처리를 둔다. 공급자 오류를 기존 API 오류 계약에 맞게 변환한다.
- 테스트에서는 외부 모델 응답을 모의 처리하고 임시 DB를 사용한다. 실제 외부 호출 검증은 별도로 명시한다.
- 업로드 크기, 파일 형식, 추출 길이, 청킹·배치 제한을 임의로 완화하지 않는다.

## 설정과 민감정보

- 설정은 `Settings`와 환경변수로 관리한다. 새 환경변수는 실제 비밀값 없이 `.env.example`에 사용 방법을 반영한다.
- 기존 `.env`, SQLite 데이터, 암호화 키 파일을 덮어쓰거나 커밋하지 않는다.
- API 키는 서버에서만 관리한다. 평문 키를 API 응답·오류·로그에 노출하지 않는다.
- Fernet 키와 암호화된 DB 값의 대응 관계를 유지한다. 키 재생성으로 기존 값이 복호화 불가능해지지 않게 한다.
- CORS를 변경할 때 웹 개발 주소와 Electron의 `null` origin 요구를 확인한다.
- 직접 접속용 `/docs`와 Nginx 경유 `/api/docs`, `/api/openapi.json`의 경로 호환성을 유지한다.

## 코드 가독성과 포맷

Python 포맷 기준은 `pyproject.toml`의 Black 설정이다. 프런트엔드의 JSX 전용 규칙은 Python에 적용하지 않는다.

- 들여쓰기는 스페이스 4칸, 기본 줄 너비는 100자를 사용한다.
- 연산자 양옆에 공백을 두고 여러 실행문을 한 줄에 붙이지 않는다.
- `skip-string-normalization` 설정에 따라 기존 따옴표 표기를 유지한다.
- 긴 문자열, SQL, 프롬프트를 줄 너비만을 이유로 의미가 바뀌도록 나누지 않는다.
- Python 중괄호와 컬렉션 배치는 Black을 따른다. JavaScript용 중괄호 공백 규칙을 강제로 적용하지 않는다.
- 포맷 변경과 동작 변경은 구분하며, 포맷 과정에서 응답 문구나 공백의 의미를 바꾸지 않는다.

## 실행과 검증

명령은 `ri-back` 루트에서 프로젝트용 가상환경을 활성화한 뒤 실행한다.

```sh
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
python -m black .
python -m black --check .
python -m unittest discover -s tests -v
```

- Python 환경이 없으면 먼저 `python -m venv .venv`로 생성한다. 활성화 방식은 운영체제에 맞게 선택한다.
- 기존 테스트를 우선 활용하고, 동작 변경 시 정상 경로와 관련된 실패·권한·범위 검증을 추가한다.
- DB 변경 시 트랜잭션과 기존 데이터 호환성, 외부 공급자 변경 시 실패 응답과 민감정보 노출 여부를 검증한다.
- 서버 상태는 `/api/v1/health`로 확인한다. 헬스 체크 성공이 DB·모델 연결 성공을 의미하지는 않는다.
- 가상환경, 캐시, 빌드 결과, `data/`와 로컬 환경 파일은 커밋하지 않는다.
- 커밋 전 `git diff --check`와 변경 범위를 확인하고, 실행한 검증과 남은 제약을 작업 결과에 기록한다.
