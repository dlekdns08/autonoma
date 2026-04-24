# Autonoma

**자기조직화 에이전트 군집 — 직접 지켜볼 수 있는 라이브 캐스트.**

[English README](./README.md)

```
     ╔═╗╦ ╦╔╦╗╔═╗╔╗╔╔═╗╔╦╗╔═╗
     ╠═╣║ ║ ║ ║ ║║║║║ ║║║║╠═╣
     ╩ ╩╚═╝ ╩ ╚═╝╝╚╝╚═╝╩ ╩╩ ╩
     Self-Organizing Agent Swarm
```

만들고 싶은 것을 설명하세요. **디렉터(Director)** 에이전트가 목표를
작업 단위로 쪼개고, 전문 에이전트들을 소환하고, 작업을 라우팅하고,
전부 끝나면 다같이 기뻐합니다. 진행 과정은 터미널 대시보드, 사이버
HUD 픽셀 맵, 또는 3D VTuber 캐릭터(립싱크·무드·제스처가 실제 에이전트
행동으로 구동됨)로 실시간 관전 가능합니다.

## 인터페이스

| 인터페이스 | 설명 | 추천 용도 |
| --- | --- | --- |
| **터미널 TUI** | Rich 기반 애니메이션 대시보드 + ASCII 스프라이트 | 로컬 실행, CI, 헤드리스 데모 |
| **픽셀 스테이지** (web) | WebSocket 기반 2D 사이버 HUD 뷰 | 일상적인 브라우저 사용 |
| **VTuber 스테이지** (web) | VRoid Hub 모델 + TTS + 립싱크의 3D 스포트라이트 | 스트리밍, 캐릭터 중심 데모 |
| **OBS 모드** (`/obs`) | 크로마키 합성용 클린 VTuber 피드 | 라이브 스트리밍 / 합성 |

## 주요 기능

- **자율 계획 수립** — 디렉터가 목표를 분해하고, 작업을 배정하고, 필요하면 새 에이전트를 소환합니다.
- **에이전트 자율 작업 선택** — 각 에이전트는 관찰 → 결정 → 실행 루프를 돌립니다: `work_on_task`, `create_file`, `send_message`, `request_help`, `spawn_agent`, `complete_task`, `celebrate`.
- **영속 캐릭터** — SQLite 기반 캐릭터 레지스트리로, 반복 출연하는 에이전트는 세션을 넘나들며 이름·성격·레벨을 유지합니다.
- **라이브 VTuber 퍼포먼스** — 무드 기반 블렌드쉐입, 5모음 립싱크, contrapposto 체중 이동, 발화 중 비트 제스처, 크로스페이드 상태 전환.
- **멀티뷰어 룸** — 호스트가 세션을 시작하고, 짧은 코드로 다른 사람이 참여해 동기화된 화면을 함께 시청.
- **LLM 백엔드 플러그** — Anthropic, OpenAI, 또는 OpenAI 호환 엔드포인트(vLLM).
- **선택적 TTS** — 자체 호스팅 OmniVoice zero-shot 클로닝 (MPS/CUDA/CPU), 에이전트별 음성 배정 + 예산 상한.
- **샌드박스 코드 실행** — 에이전트가 작성한 코드를 bubblewrap 샌드박스에서 CPU/실행시간/메모리 제한하에 실행.

## 빠른 시작

### 사전 준비

- Python 3.12+, [uv](https://docs.astral.sh/uv/)
- Node.js 20+ (웹 UI 사용 시)
- `ANTHROPIC_API_KEY` *또는* `OPENAI_API_KEY` *또는* vLLM 엔드포인트

### 터미널 TUI만 사용

```bash
uv sync
export ANTHROPIC_API_KEY=sk-ant-...

# 뭔가 만들기
uv run autonoma build "태그와 검색 기능이 있는 북마크 관리 REST API"

# 대화형 모드
uv run autonoma interactive

# 미리 준비된 데모
uv run autonoma demo
```

### 웹 UI (백엔드 + 프론트엔드)

```bash
# 터미널 1 — API + WebSocket 서버
uv sync
export ANTHROPIC_API_KEY=sk-ant-...
uv run uvicorn autonoma.api:app --port 8000

# 터미널 2 — Next.js 프론트엔드
cd web
npm install
npm run dev          # http://localhost:3000
```

브라우저에서 열고 프로젝트 목표를 붙여넣으면 디렉터가 알아서 굴립니다.

## 아키텍처

```
src/autonoma/
├── cli.py              # Click CLI — build / interactive / demo
├── api.py              # 웹 UI용 FastAPI + WebSocket 브리지
├── config.py           # pydantic-settings 설정 (AUTONOMA_* 환경변수)
├── event_bus.py        # 와일드카드 구독을 지원하는 비동기 pub/sub
├── models.py           # 핵심 데이터 모델 (Persona, Task, Message...)
├── world.py            # Mood enum, 방 배치, 월드 상태
├── llm.py              # 프로바이더 추상화 (Anthropic / OpenAI / vLLM)
├── tts.py, tts_worker.py
├── agents/
│   ├── base.py         # AutonomousAgent — 관찰→결정→실행 루프
│   ├── director.py     # 목표 분해, 전문 에이전트 소환
│   └── swarm.py        # 생명주기, 라우팅, 포춘쿠키, 관계망
├── tui/                # Rich 기반 애니메이션 대시보드
├── engine/             # swarm + TUI + workspace 통합 러너
├── db/                 # SQLite 영속 캐릭터 레지스트리
└── sandbox.py          # Bubblewrap 코드 실행 샌드박스

web/src/
├── app/
│   ├── page.tsx                # 메인 대시보드 (픽셀 + VTuber + 채팅)
│   ├── obs/                    # 크로마키용 VTuber 전용 피드
│   └── chibi-gallery/          # 프로시저럴 치비 얼굴 갤러리
├── components/
│   ├── Stage.tsx               # 2D 픽셀 사이버 HUD 룸
│   ├── vtuber/
│   │   ├── VTuberStage.tsx     # 3D 스포트라이트 + 갤러리
│   │   ├── VRMCharacter.tsx    # VRM 렌더 + 제스처/표정 엔진
│   │   ├── vrmCatalog.json     # VRM 모델 단일 소스
│   │   └── vrmCredits.ts       # 카탈로그의 타입 API
│   └── stage/                  # 배경, 파티클, 미니맵
└── hooks/
    ├── useSwarm.ts             # WebSocket 상태 머신
    └── useAgentVoice.ts        # 에이전트별 TTS 재생 + 립싱크 진폭
```

## 설정

환경변수 (`AUTONOMA_*` 접두사) 또는 실행 디렉터리의 `.env` 파일에서 로드됩니다.
자주 쓰는 것들:

| 변수 | 용도 | 기본값 |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | 프로바이더 자격 증명 (접두사 없이도 인식) | — |
| `AUTONOMA_PROVIDER` | `anthropic` / `openai` / `vllm` | `anthropic` |
| `AUTONOMA_MODEL` | 모델 ID | `claude-sonnet-4-6` |
| `AUTONOMA_VLLM_BASE_URL` / `AUTONOMA_VLLM_API_KEY` | 자체 호스팅 OpenAI 호환 서버용 | — |
| `AUTONOMA_ADMIN_PASSWORD` | 설정 시 웹 UI에서 서버 키 어드민 로그인 활성화 | — |
| `AUTONOMA_TTS_ENABLED` / `AUTONOMA_TTS_PROVIDER` | 토글 + 백엔드: `omnivoice` / `none` | `false` / `none` |
| `AUTONOMA_MAX_AGENTS` | 동시 에이전트 상한 | `8` |
| `AUTONOMA_OUTPUT_DIR` | 에이전트 생성 파일 출력 경로 | `./output` |
| `AUTONOMA_DATA_DIR` | SQLite 캐릭터 DB 위치 | `./data` |

전체 목록은 [`src/autonoma/config.py`](./src/autonoma/config.py) 참조.

## VRM 모델 추가

VRM 메타데이터는 JSON 한 파일에 모여 있습니다. 엔트리 추가 → 동기화 스크립트 실행 → 끝.

1. `yourmodel.vrm` 파일을 `web/public/vrm/`에 배치.
2. [`web/src/components/vtuber/vrmCatalog.json`](./web/src/components/vtuber/vrmCatalog.json)에 엔트리 추가:

   ```json
   "yourmodel.vrm": {
     "character": "표시할 이름",
     "title": "LICENSES.md용 긴 제목 (선택)",
     "author": "저자 핸들",
     "url": "https://hub.vroid.com/...",
     "uploaded": "2026-04-21",
     "license": {
       "avatarUse": "Allow",
       "violentActs": "Allow",
       "sexualActs": "Allow",
       "corporateUse": "Allow",
       "individualCommercialUse": "Allow",
       "redistribution": "Allow",
       "alterations": "Allow",
       "attribution": "Not required"
     }
   }
   ```

3. `cd web && npm run vrm:sync-licenses` — `public/vrm/LICENSES.md` 자동 재생성.

에이전트는 이름의 djb2 해시로 VRM에 결정론적으로 배정되므로, 같은
에이전트는 세션을 바꿔도 항상 같은 캐릭터로 나옵니다.

## Harness 엔지니어링

각 스웜 런의 런타임 정책 — 라우팅, 루프 상한, 결정 전략, 안전
레벨, 무드 전이 등 — 은 `harness_policies` 테이블에 저장되며
매 `start` 명령마다 해석됩니다. 사용자는 Idle 화면의 ⚙ 패널에서
프리셋을 고르거나 특정 섹션만 오버라이드할 수 있고, 병합된 정책은
스웜이 부팅되기 전에 검증됩니다.

- **프리셋** — 사용자별 프리셋 + 시스템 기본값. CRUD는
  `/api/harness/presets`. 기본값은 읽기 전용이며, 사용자는 조정값을
  새 프리셋으로 저장할 수 있습니다.
- **검증** — Pydantic 위에 두 개의 직교하는 레이어:
  위험한 조합(예: `code_execution=disabled` +
  `harness_enforcement=off`)은 전체 사용자 거부, 관리자 전용 값
  (예: `safety.enforcement_level=off`, `loop.max_rounds>200`)은
  비관리자 거부. 상세는
  [`src/autonoma/harness/validation.py`](./src/autonoma/harness/validation.py).
- **옵저버빌리티** — 각 런은 `session_id`, `preset_id`, 오버라이드된
  섹션, 유효 정책, 전략 선택을 기록합니다. 조회는
  `GET /api/session/{id}/metadata`, 전역 롤업은
  `GET /api/harness/metrics` (관리자). 런 종료 시
  `session.metadata`로 WS 이벤트 버스에 발행됩니다.
- **UI용 스키마** — `GET /api/harness/schema`는 런타임에
  `HarnessPolicyContent`를 내부 조사해 필드별 타입 / 기본값 /
  enum 옵션 / 숫자 범위를 반환합니다. Pydantic 모델에 `Literal`
  값을 추가하면 프론트엔드 폼이 TS 변경 없이 자동으로 반영합니다.

### 배포 관련 주의

- 마이그레이션은 버전 게이트가 있어 기동 시 자동 적용됩니다
  ([`src/autonoma/db/engine.py`](./src/autonoma/db/engine.py) 참고).
  `harness_policies`는 마이그레이션 003이며, 프레임워크는
  `create_all(checkfirst=True)` + `schema_version` 카운터를 써서
  이미 채워진 DB에 프로세스를 다시 띄워도 안전한 no-op입니다.
- 프로덕션에서는 `AUTONOMA_SESSION_SECRET`을 설정하세요. 없으면
  쿠키 세션이 프로세스마다 바뀌는 임시 시크릿으로 서명되어
  재시작할 때마다 모든 사용자가 로그아웃됩니다.
- 관리자 전용 harness 정책은 `role=admin`인 쿠키 세션 사용자가
  필요합니다. 레거시 WS 관리자 비밀번호 경로도 해당 연결의
  `start` 명령에 한해 `is_admin`을 부여합니다.

## Docker 배포

```bash
docker compose up -d
# API     → http://localhost:3479
# 웹 UI   → http://localhost:3478
```

`autonoma.koala.ai.kr`용 Nginx 리버스 프록시 설정은 [`nginx/`](./nginx/)에,
프로덕션 배포는 [`docker-compose.prod.yml`](./docker-compose.prod.yml)에 있습니다.

## 테스트

```bash
uv run pytest tests/ -v
# 영문/한글 README의 구조적 일치 검사:
python scripts/check_readme_drift.py
```

## 라이선스

VRM 에셋은 VRoid Hub 약관에 따라 개별 라이선스됩니다 —
[`web/public/vrm/LICENSES.md`](./web/public/vrm/LICENSES.md) 참고. 그 외
프로젝트 코드는 아직 라이선스가 공개되지 않았습니다. 명시가 필요하면
이슈를 열어주세요.
