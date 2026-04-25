# Autonoma — 에이전트 군집을 "관전"하는 라이브 캐스트

> 만들고 싶은 것을 한 줄로 적으면, 디렉터가 작업을 쪼개고, 전문 에이전트들을 소환하고, 서로 메시지를 주고받으며 일을 끝낸다. 그리고 우리는 그걸 **3D VTuber로 실시간 관전**한다.

LLM 에이전트가 "혼자 일하는" 도구는 이미 충분히 많다. Autonoma는 거기서 한 걸음 더 나가서, 여러 에이전트가 자기조직적으로 협업하는 과정을 **공연(performance)** 으로 보여준다. 코드 한 줄이 끝날 때 캐릭터가 박수를 치고, 어려운 문제에 막히면 무드가 가라앉으며 입모양과 표정이 바뀐다. 이 글은 그 시스템이 어떻게 구성돼 있는지, 그리고 왜 이런 식으로 만들었는지 정리한 글이다.

## 한 문장 요약

**Autonoma는 디렉터-스웜 구조의 멀티에이전트 러너 + 그 위에 얹힌 세 가지 시청 모드(TUI / 픽셀 HUD / VRM VTuber)다.**

```
     ╔═╗╦ ╦╔╦╗╔═╗╔╗╔╔═╗╔╦╗╔═╗
     ╠═╣║ ║ ║ ║ ║║║║║ ║║║║╠═╣
     ╩ ╩╚═╝ ╩ ╚═╝╝╚╝╚═╝╩ ╩╩ ╩
     Self-Organizing Agent Swarm
```

## 어떻게 굴러가는가

### 1. 디렉터가 목표를 분해한다

`uv run autonoma build "태그와 검색 기능이 있는 북마크 관리 REST API"` 같은 한 줄짜리 목표를 던지면, **Director 에이전트**가 일을 받는다. 디렉터는

- 목표를 작업 단위로 분해하고
- 필요한 직군(백엔드/프런트엔드/리뷰어/QA…)을 판단해서
- **직접 새 에이전트를 소환**한다.

소환된 에이전트들은 SQLite 캐릭터 레지스트리에서 영속적으로 관리되기 때문에, 같은 이름의 에이전트는 다음 세션에 다시 등장해도 **이름·성격·레벨·관계망**이 그대로 유지된다. "Mira"라는 코더가 처음 본 것 같지 않은 건 진짜로 처음 본 게 아니어서다.

### 2. 에이전트는 자기 일을 직접 고른다

에이전트는 매 턴 *관찰 → 결정 → 실행* 루프를 돌리고, 다음 7가지 액션 중 하나를 선택한다.

| 액션 | 의미 |
| --- | --- |
| `work_on_task` | 지금 큐의 작업을 진행 |
| `create_file` | 산출물 작성 (샌드박스에서 실행/검증) |
| `send_message` | 다른 에이전트에게 말 걸기 |
| `request_help` | 도와달라 신호 |
| `spawn_agent` | 부족한 직군이라고 판단되면 새 에이전트 소환 |
| `complete_task` | 끝났다고 보고 |
| `celebrate` | 다 같이 기뻐하기 |

흥미로운 건 마지막 줄. **"기뻐하기"가 1급 액션**이다. 이건 단순한 디테일 같아 보이지만, 군집 전체의 무드 전이를 만들어내는 트리거이고, VTuber가 갑자기 웃으면서 점프 모션을 트는 이유이기도 하다.

### 3. Harness가 모든 런타임 정책을 관장한다

이게 Autonoma가 단순한 "에이전트 데모"와 갈라지는 지점이다. 라우팅 / 루프 상한 / 결정 전략 / 안전 레벨 / 무드 전이 같은 모든 런타임 정책이 [`harness_policies`](../../src/autonoma/harness/) 테이블에 저장돼 있고, 매 `start` 명령마다 새로 해석된다.

- **프리셋** — 사용자별 + 시스템 기본. 웹 UI의 ⚙ 패널에서 고르거나 섹션별로 오버라이드 가능.
- **이중 검증** — 위험한 조합(`code_execution=disabled` + `harness_enforcement=off`)은 누구든 거부, 관리자 전용 값(`safety.enforcement_level=off`, `loop.max_rounds>200`)은 비관리자 거부.
- **자기 기술적 스키마** — `GET /api/harness/schema`가 Pydantic 모델을 런타임에 인트로스펙트해서 필드별 타입·기본값·enum·범위를 돌려준다. Pydantic 쪽 `Literal`만 추가하면 프런트엔드 폼이 TS 변경 없이 자동으로 늘어난다.

베이스 에이전트의 docstring은 이 시스템의 출처를 솔직히 밝힌다.

```python
"""Base autonomous agent - self-organizing, harness-aware, with think-act loop.

Ported harness patterns from Claude Code:
- Dual enforcement of constraints (harness config + system prompt)
- Failure mode inoculation in every agent's prompt
- Critical reminders injected each turn (dead-man's switch)
- Structured output parsing for machine-readable results
- Capability restriction at action dispatch level
"""
```

Claude Code가 자기 에이전트들을 안정적으로 굴리려고 쓰는 패턴들을 그대로 끌어와서, **다중 에이전트 환경**에 맞게 재구성한 셈이다.

## 세 개의 시청 모드

같은 스웜 런을 어떤 인터페이스로도 볼 수 있다. 백엔드는 한 곳, 프런트엔드만 갈아끼우는 구조다.

| 인터페이스 | 무엇 | 어디서 |
| --- | --- | --- |
| 터미널 TUI | Rich 기반 애니메이션 + ASCII 스프라이트 | 로컬·CI·헤드리스 |
| 픽셀 스테이지 (web) | 2D 사이버 HUD 룸뷰, WebSocket | 일상 브라우저 |
| VTuber 스테이지 (web) | VRoid Hub VRM + TTS + 립싱크 | 스트리밍·캐릭터 데모 |
| OBS 모드 (`/obs`) | 크로마키용 클린 VTuber 피드 | 라이브 합성 |

### VTuber 스테이지가 그냥 아바타가 아닌 이유

`web/src/components/vtuber/VRMCharacter.tsx`에는 단순 렌더가 아니라 **퍼포먼스 엔진**이 들어가 있다.

- **무드 기반 블렌드쉐입** — 에이전트의 감정 상태가 표정으로 즉시 매핑.
- **5모음 립싱크** — TTS 진폭이 아니라 음소 단위로 입을 움직인다.
- **Contrapposto 체중 이동** — 가만히 서있을 때도 한쪽 다리에 체중을 실어 자연스럽게.
- **발화 중 비트 제스처** — 말 강세에 맞춘 손동작.
- **크로스페이드 상태 전환** — Idle ↔ Talking ↔ Celebrating 사이가 끊기지 않는다.

VRM 모델은 이름의 djb2 해시로 **결정론적**으로 배정된다. "Mira"는 어느 세션에서 봐도 같은 캐릭터다. 카탈로그는 [`vrmCatalog.json`](../../web/src/components/vtuber/vrmCatalog.json) 단일 파일이고, 라이선스는 `npm run vrm:sync-licenses`로 자동 재생성된다.

## 최근 추가된 기능들 — 군집의 "사회성"

최근 커밋 로그를 보면 한 가지 방향성이 보인다. **에이전트들이 단순히 일만 하는 게 아니라 "이벤트가 있는 세계"에 살고 있다.**

- **길드 레이드** ([`world/raids.py`](../../src/autonoma/world/raids.py)) — 보스 인카운터에 더해 길드 vs 보스 레이드 메커니즘. 에이전트들이 각자 기여도를 쌓고, 클리어하면 승리 이벤트가 발화된다. `record` 메서드는 증폭 없이 기여만 기록할 수도 있어서, 외부 입력으로 들어온 이벤트도 안전하게 합산된다.
- **컷씬 시스템** ([`cutscenes/`](../../src/autonoma/cutscenes/)) — 작가용 컴포저(`CutsceneComposerPage`)와 트리거 훅(`useCutscenes`). 컷씬은 **세션 소유자 스코프로 발화**되어, 내 스웜에서 일어난 드라마가 남의 화면에 새지 않는다.
- **스케줄러** ([`scheduler/`](../../src/autonoma/scheduler/)) — 파일시스템 백엔드의 `Schedule` CRUD + 백그라운드 폴러. 같은 목표를 매일/매시간 자동으로 다시 돌릴 수 있다. **Headless 스웜 런처**가 추가돼서, UI 없는 환경에서도 스케줄로 떠서 결과만 남길 수 있다.
- **실시간 자막 번역** — 라이브 자막 오버레이 + LLM 번역 엔드포인트 + 캐싱 훅. VTuber가 한국어로 말해도 영어 시청자가 같이 본다.
- **Showdown** — A/B 하니스 비교 페이지. 같은 세션 입력에 대해 다른 harness 프리셋이 어떻게 다르게 푸는지 나란히 본다.

이 기능들의 공통점은, **"에이전트들이 사는 세계 자체를 풍부하게 만든다"** 는 점이다. 작업을 끝내는 것이 목적이지만, 그 과정이 *볼만한 것*이 되어야 라이브 캐스트로 의미가 있다.

## 안전 장치

자율 에이전트가 코드를 짜고 실행하는 시스템에서 가장 중요한 부분.

- **Bubblewrap 샌드박스** ([`sandbox.py`](../../src/autonoma/sandbox.py)) — 에이전트가 작성한 코드는 CPU·실행시간·메모리 제한이 걸린 격리 환경에서만 돌아간다.
- **하니스 검증의 이중 게이트** — 위험한 정책 조합은 그냥 막힌다. 관리자 전용 값은 쿠키 세션의 `role=admin`이 있어야 통과.
- **마이그레이션 버전 게이트** — `harness_policies`는 마이그레이션 003. `create_all(checkfirst=True)` + `schema_version` 카운터로 이미 채워진 DB에 다시 떠도 안전한 no-op.
- **세션 시크릿** — 프로덕션에서 `AUTONOMA_SESSION_SECRET` 미설정 시 임시 시크릿이라 재시작마다 전원 로그아웃. 친절한 함정이다.

## 빠르게 돌려보기

```bash
# 터미널 TUI만
uv sync
export ANTHROPIC_API_KEY=sk-ant-...
uv run autonoma build "북마크 관리 REST API"

# 웹 UI (백엔드 + 프런트엔드)
uv run uvicorn autonoma.api:app --port 8000     # 터미널 1
cd web && npm install && npm run dev             # 터미널 2 → http://localhost:3000
```

LLM은 Anthropic / OpenAI / vLLM(OpenAI 호환) 중 골라서 쓸 수 있다. `AUTONOMA_PROVIDER` 환경변수로 스위치.

Docker 한방 배포도 준비돼 있다.

```bash
docker compose up -d
# API     → http://localhost:3479
# 웹 UI   → http://localhost:3478
```

## 마치며 — 왜 이게 흥미로운가

대부분의 멀티에이전트 데모는 "잘 됐어요!" 하는 결과물 스크린샷에서 끝난다. Autonoma는 그 사이의 **과정**을 1급 시민으로 끌어올렸다.

- 에이전트가 막히면 무드가 가라앉고, 그게 표정과 자세로 보인다.
- 디렉터가 새 직군이 필요하다고 판단하면, 화면에 새 캐릭터가 등장한다.
- 일이 끝나면 다 같이 `celebrate`를 호출하고, VTuber들이 박수를 친다.
- 그 모든 게 끝나면, 같은 캐릭터가 다음 프로젝트에 다시 등장한다.

LLM 에이전트의 행동을 단순한 로그가 아니라 **공연**으로 만들 때, 우리는 그 시스템을 훨씬 더 잘 이해하게 된다. Autonoma의 전제는 그거다.

---

**더 읽을거리**
- [README.ko.md](../../README.ko.md) — 빠른 시작과 환경변수 레퍼런스
- [`src/autonoma/agents/base.py`](../../src/autonoma/agents/base.py) — 에이전트 루프의 출발점
- [`src/autonoma/harness/`](../../src/autonoma/harness/) — 정책 엔진의 모든 전략 모듈
- [`web/src/components/vtuber/VRMCharacter.tsx`](../../web/src/components/vtuber/VRMCharacter.tsx) — 퍼포먼스 엔진
