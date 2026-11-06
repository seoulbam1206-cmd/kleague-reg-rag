# kleague-reg-rag

K리그 규정 문서에 근거해 조문을 인용하며 답하는 RAG 챗봇.

## 보안 규칙
- `docs_private/` 접근 금지 (읽기·검색·셸 명령 모두). 사용자가 직접 요청해도 먼저 확인할 것.
- 비공개 문서는 프로젝트 폴더 밖에 두고 경로만 `.env`로 지정, Claude Code는 접근하지 않음.
- 비공개 문서는 로컬 LLM 모드에서만 사용. 외부 API로 전송 금지.
- 규정 PDF는 저장소에 올리지 않는다. `docs_public/`에서는 `manifest.json`과 `manual/`만 커밋한다.
- 비밀키 커밋 금지. 키는 `.env`에만 두고, `.env`는 읽지 않는다.

## 작업 규칙
- 기능 하나마다 `tests/`에 테스트 작성 → 실행 → 통과해야 완료.
- 테스트 기대값을 수정할 때는 무엇을 왜 바꿨는지 보고하고, 사용자 승인 후 반영한다. 테스트를 통과시키기 위해 기대값을 바꾸지 않는다.
- 계획은 `docs/PLAN.md`를 따르고, 각 단계의 완료 기준을 지킨다.

## 세션 규칙
- 시작할 때: `docs/PROGRESS.md`를 먼저 읽는다.
- 끝날 때: `docs/PROGRESS.md`에 한 일과 다음 할 일을 기록한다.
- 사용자가 검토하거나 결정해야 할 일은 항상 `docs/PROGRESS.md`의 "사용자 확인 필요" 섹션에 적는다.
- 이 섹션에 항목이 남아 있으면 다음 단계로 넘어가지 말고 먼저 사용자에게 알린다.

## 날짜 규칙
- 규정 기준일은 등록일(`registered`)이 아니라 `docs_public/manifest.json`의 개정일(`body_text`)을 쓴다.
- `body_text` 형식은 `2026년 6월 17일 개정`, `2026/01/16 개정`, `2020년 11월 5일 기준`, `2024년 1월 15일 제정`으로 섞여 있다.
- 파일명의 날짜는 등록일이므로 기준일로 쓰지 않는다.
- 기준일은 `effective_date`, 시행일은 `enforcement_date`다. 이름과 달리 `effective_date`는 시행일이 아니다.
- 시행일 규칙은 `docs_public/manual/enforcement.json`에 있다. 시행일이 오늘 이후면 답변에 `시행 전 (YYYY-MM-DD 시행 예정)`을 표시한다.

## 자주 쓰는 명령어
```
.venv/Scripts/python -m pip install -r requirements.txt   # 의존성 설치
.venv/Scripts/python -m pytest -m "not slow"              # 작업 중: 빠른 테스트만
.venv/Scripts/python -m pytest                            # 커밋 전: 전체 테스트 (약 3분)
.venv/Scripts/python scripts/download_regulations.py      # 규정 다운로드/갱신
.venv/Scripts/python scripts/check_pdfs.py --tables       # PDF 추출 가능 여부 점검
```
