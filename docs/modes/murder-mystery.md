# 머더 미스터리 모드

게임 전에 에이전트들이 시나리오 한 편을 설계하고 검증한다. 8명은 그 이야기 속 용의자를 맡아 범인을 찾는다(또는 범인으로서 빠져나간다).

## 1. 원칙

**진실을 아는 자는 말하지 않고, 말하는 자는 진실을 모른다.**

| 주체 | 아는 것 | 출력이 가는 곳 |
|---|---|---|
| `mystery-architect` | 전부 (직접 만든다) | 엔진(MCP)만. Agent 결과는 봉인 |
| `mystery-auditor` | 전부 | 엔진만. 봉인 |
| `mystery-solver` | 공개 텍스트 + 주어진 단서 카드만 (진상 모름) | 엔진만. 봉인 |
| `mystery-narrator` | 공개 정보만. 게임 종료 후 에필로그 요청에서만 진상 | 공개 내레이션 이벤트 |
| `seat-N` | 자기 캐릭터 시트 + 손에 든 카드 + 공개 로그 | 자기 행동 제출 |
| 메인 세션 | 공개 정보 + 사람 플레이어의 시트 | 채팅 화면 |

품질은 프롬프트가 아니라 **구조로 보증한다**: 스키마 → 코드 검증 → 적대적 감수 → 블라인드 풀이 → 수정 루프.

## 2. 게임 규칙

### 구성
- 용의자 8명 = 플레이어 8명. 피해자는 NPC.
- 범인은 정확히 1명. 사람이 범인을 맡을 수도 있다. `--human-role random|innocent|culprit`, 기본은 random.
- 시나리오의 인물 8명을 좌석에 랜덤 배정한다. 설계자는 누가 사람인지 모르고, 정해지지도 않은 상태에서 쓴다.

### 진행 (예상 60~90분)

| 페이즈 | 내용 | 좌석 요청 |
|---|---|---|
| 프롤로그 | 내레이션, 장소 소개, 인물 공개 소개 | — |
| 시트 열람 | 각자 비공개 캐릭터 시트와 시작 카드 1장 | — |
| 첫 진술 | 전원 1회: 자기소개와 그날 밤 행적 | `statement` (순차) |
| 조사 1 | 장소 1·2지망 선택 → 카드 1장 비공개 획득 | `investigate` (병렬) |
| 토론 1 | N라운드. 발언과 함께 카드 공개 가능 | `speak` (순차) |
| 중간 사건 | 시나리오의 midgame_twist 내레이션 | — |
| 조사 2 | 조사 1과 같음 (2라운드 풀 + 1라운드 미발견 카드 이월) | `investigate` (병렬) |
| 토론 2 | N라운드 | `speak` (순차) |
| 최종 변론 | 전원 1회 | `statement` (순차) |
| 범인 지목 | 비밀 투표 후 동시 공개. 최다 득표자가 지목됨. 동률이면 결선 1회, 또 동률이면 지목 실패 | `accuse` (병렬) |
| 결말 | 검거 또는 도주 결말 → 진상 공개 → 인물별 에필로그 → 개인 목표 정산 | — |

### 조사
- 장소 6곳. 장소마다 라운드마다 카드 2장, 즉 라운드당 12장이 있다.
- 8명이 라운드마다 1장씩 가져가므로 **매 라운드 4장은 미발견**이다. 1라운드에서 못 찾은 카드는 2라운드 풀로 이월한다.
- 같은 장소를 고른 사람이 카드 수보다 많으면 seed 기반 우선순위로 정하고, 밀린 사람은 2지망으로 간다.
- 시작 카드: 인물마다 1장(주머니 속 편지 등), 총 8장. 따라서 전체 카드는 최소 32장이다.
- `unlock_condition`이 있는 카드는 조건이 충족될 때만 풀에 들어간다. 예: 카드 C07이 공개된 뒤.

### 공개와 주장
- **공개(reveal)**: 엔진이 카드 원문을 🔎 표시와 함께 모두에게 보인다. 위조할 수 없다.
- **주장(claim)**: 발언으로 카드 내용을 말하는 것. 거짓일 수 있다.
- 사람은 `:show C12`로 공개하고, 좌석은 `speak` 응답의 `reveal: [카드 id]`로 공개한다.

### 승패와 점수
- 팀 승패: 범인이 지목되면 무고한 7명이 이기고, 아니면 범인이 이긴다.
- 개인 목표: 인물마다 1~2개. **엔진이 판정할 수 있는 술어로만** 정의한다. 발언 내용은 판정할 수 없으므로 술어에서 제외한다.

| 술어 | 의미 |
|---|---|
| `hold_clue(C)` | 게임 종료 시 카드 C를 갖고 있다 |
| `clue_not_revealed(C)` | 카드 C가 끝까지 공개되지 않았다 (비밀은 카드로 구현한다) |
| `clue_revealed(C)` | 카드 C가 공개되었다 |
| `accused_is(X)` / `accused_is_not(X)` | 지목 결과 |
| `culprit_caught` / `culprit_escaped` | 팀 결과 |
| `voted_for(X)` | 본인이 X에게 투표했다 |

## 3. 사전 파이프라인

```
brief(코드) ─▶ architect ─섹션별 제출─▶ 정적 검증(코드) ─▶ auditor ─▶ solver ×4 ─▶ 판정
                  ▲                                                          │
                  └──────── revision_packet (감수 보고서 + 풀이 결과) ◀──── 불합격 (최대 3회)
합격 → runs/scenarios/<id>/ (라이브러리)
```

### 3.1 브리프 (코드, `modes/mystery/brief.py`)
LLM은 그대로 두면 "외딴 저택·집사·유산·독살"로 수렴한다. 그래서 다양성은 코드가 강제한다. `config/mystery/brief_pools.toml`의 풀에서 seed로 뽑는다.
- 배경 (시대 × 장소). 예: 1930년대 경성 백화점, 남극 월동 기지, 마감 전날 웹툰 스튜디오, 조선 후기 객주, 대학 연극부 합숙
- 톤: 정통 본격 / 코지 / 누아르 / 고딕 / 블랙코미디
- 반전 유형: 시간 착각, 신원 혼동, 사고로 위장한 살인, 은폐자는 범인이 아님, 피해자의 계획이 역이용됨 …
- 금지 클리셰 목록, 수위 제한, 분량 기준

사용자가 `/mafia new mystery <자유 요청>`으로 배경이나 톤을 지정하면 브리프에 우선 반영한다.

### 3.2 설계 (`mystery-architect`, opus, effort high)
- 진상부터 역설계한다. 섹션 순서대로 `mystery_submit_section`을 호출한다: concept → truth → facts → characters → clues → narration.
- 섹션마다 즉시 스키마와 교차 검증을 하고, 오류는 도구 결과로 돌려준다. 오류는 그 자리에서 고친다.
- 마지막에 `mystery_check`(전체 정적 검증)의 오류가 0이어야 제출로 인정한다.

### 3.3 정적 검증 (코드, `modes/mystery/validate.py`)
- 인물 정확히 8명, 범인 정확히 1명, 장소 6곳, 라운드별·장소별 카드 2장, 시작 카드 인물당 1장
- 시간표: 모든 슬롯 × 모든 인물에 위치가 있음(빈칸 없음). 위치 id가 유효함
- 인물 시트의 행적이 진짜 시간표와 다르면 해당 항목이 `lies`에 있어야 함
- 사실 그래프: `decisive` 사실마다 **서로 다른 장소·소지자의 카드 2장 이상**이 증명함
- 무고한 7명 각자에 대해 `excludes`가 그 인물을 가리키는 카드가 1장 이상 있음
- `key_deductions`의 모든 단계가 실존 카드·사실 id를 인용함
- `is_red_herring` 카드에는 `red_herring_resolution`(해명 카드 id)이 있음. 미끼 대상 무고자가 3명 이상
- 개인 목표가 허용 술어만 쓰고, 참조 id가 유효함
- `unlock_condition`이 순환하지 않고 도달 가능함
- 텍스트 길이 상하한

### 3.4 감수 (`mystery-auditor`, opus, effort high)
2단계로 읽는다. **먼저 플레이어처럼 읽고**(공개 텍스트와 카드만 보고 풀어 봄), **그다음 진상을 읽고 대조한다.** 대체 범인 테스트, 시트 자족성 테스트, 누출 검사, 범인 생존성, 재미와 균형을 본다. 결함마다 심각도(blocker/major/minor), 필드 경로, 수정 제안을 남긴다.

### 3.5 블라인드 풀이 (`mystery-solver`, 진상 모름)

| 솔버 | 받는 카드 | 모델 | 합격 조건 |
|---|---|---|---|
| full ×2 | 전체 | sonnet | 둘 다 범인을 맞힘, 확신도 60 이상 |
| partial ×1 | 라운드마다 4장 무작위 제거 (실제 미발견 시뮬레이션) | sonnet | 범인을 맞힘 |
| round1 ×1 | 시작 카드 + 1라운드 카드만 | haiku (Agent `model` 지정) | 확신도 80 이상으로 맞히면 **불합격**(너무 쉬움) |

솔버는 공개 텍스트와 카드만 받는다. 실제 게임에서는 인물들의 증언도 정보가 되므로, 이 테스트는 보수적인 하한선이다.

### 3.6 판정과 수정
- 합격 조건: 정적 오류 0 + 감수 blocker 0 / major 2 이하 + 솔버 조건 전부 충족
- 불합격이면 새 architect를 띄우고, `mystery_revision_packet`으로 이전 초안, 감수 보고서, 풀이 결과(솔버의 추론 과정 포함)를 준다. 최대 3회.
- 3회 후에도 불합격이면 점수가 가장 좋은 판을 "주의" 표시와 함께 라이브러리에 넣을지 버릴지 설정으로 정한다(기본: 버림).

### 3.7 라이브러리
- 파이프라인은 수십 분이 걸릴 수 있다. `/mafia prepare mystery [N]`으로 미리 만들어 두고, `/mafia new mystery`는 아직 플레이하지 않은 시나리오를 꺼낸다. 라이브러리가 비어 있으면 그 자리에서 생성한다.
- `runs/scenarios/<id>/`: `scenario.json`, `audit.json`, `solves.json`, `meta.json`(플레이 여부, 생성 seed).
- `mafia scenarios list`는 제목과 톤만 보여 준다. 진상은 `--spoil`을 붙일 때만 보인다.
- `tail --god`도 준비 단계에서는 진행 상황만 보여 주고, 진상은 `--spoil`일 때만 보여 준다.

## 4. 시나리오 스키마 (요약)

정본은 `src/mafia/modes/mystery/scenario.py`(pydantic)이다. 가시성은 P(공개), S(해당 인물 시트), G(GodOnly)로 표시한다.

```
Scenario
├─ concept: title(P), logline(P), tone, era, setting, twist_type, content_notes        (G, 제목·로그라인만 P)
├─ world: setting_description(P), locations[6]{id, name, description(P)}
├─ victim: name(P), public_profile(P), true_background(G)
├─ truth (G)
│   ├─ culprit_id, motive, means, opportunity, summary(800~1500자)
│   ├─ time_slots[]                    # 예: "21:00", "21:20", … 핵심 시간대 6~12칸
│   ├─ timeline[slot][character_id] → {location_id, action, witnessed_by[]}
│   └─ key_deductions[]{step, claim, evidence: [clue_id|fact_id]}   # 정답 풀이
├─ facts[]{id, statement, decisive: bool, proven_by: [clue_id], excludes: character_id?}  (G)
├─ characters[8]
│   ├─ id, name, age, occupation, public_profile(P), public_relation_to_victim(P)
│   └─ sheet (S)
│       ├─ background, relation_to_victim_truth
│       ├─ secret{what, why_hide, exposed_by: [clue_id]}
│       ├─ motive                       # 무고한 자도 그럴듯한 동기를 하나씩 갖는다
│       ├─ night{slot → 내가 실제로 한 일}  # 진짜 시간표와 일치
│       ├─ knowledge[]{slot?, what_i_saw_or_heard, fact_id?}
│       ├─ lies[]{about, false_version, why, broken_by: [clue_id]}
│       ├─ say_freely[], hide[]         # 연기 지침: 말할 것, 숨길 것
│       ├─ relationships{character_id: 태도와 사연}
│       ├─ goals[1~2]{description, predicate, points}
│       ├─ play_guide{temperament, speech_style, sample_lines[2]}
│       └─ culprit_kit?                 # 범인만: cover_story, dangerous_clues 힌트, deflection 재료
├─ clues[≥32]{id, title, text, kind, location_id|starting_holder, round(0|1|2),
│            unlock_condition?, supports: [fact_id], is_red_herring, red_herring_resolution?}  (카드 원문은 획득자·공개 시 P)
└─ narration
    ├─ prologue(P), character_intros{id: 한 줄}(P), round_intros[](P), midgame_twist(P)
    ├─ accusation_intro(P), ending_caught, ending_escaped        # 결말 시 P
    └─ truth_reveal(1500~3000자), epilogues{character_id: 한 단락}   # 게임 종료 후 P
```

## 5. 하네스 추가분

- **MCP 도구 패밀리**
  - architect: `mystery_brief`, `mystery_revision_packet`, `mystery_submit_section`, `mystery_check`
  - auditor: `mystery_review_packet(stage=blind|full)`, `mystery_submit_audit`
  - solver: `mystery_solver_packet`, `mystery_submit_solution`
  - narrator: `narration_packet`, `submit_narration`
- `guard_mcp`가 agent_type과 도구 패밀리를 대응시킨다. 다른 패밀리를 호출하면 deny한다.
- `guard_spawn`은 엔진의 pending 요청에 해당 mystery 에이전트가 있을 때만 생성을 허용한다. solver의 `model` 인자가 요청과 일치하는지도 확인한다.
- `seal_agent`는 mystery 에이전트 결과도 고정 문구로 봉인한다. 메인은 "시나리오 준비 중 (검증 2/3)" 같은 진행 상황만 안다.
- 좌석의 `my_turn` (머더 미스터리 모드): 자기 시트, 손에 든 카드, 공개 로그, 공개된 카드, 지난 메모, 지금 할 일. **다른 인물의 시트나 진상은 절대 포함하지 않는다.**

## 6. 열린 질문

- 공범이나 은폐자를 둔 2인 구조를 옵션으로 둘지 (스키마는 확장 가능하게)
- 토론 중 "1:1 밀담"(두 좌석만 보는 대화)을 넣을지. 가시성 `Pair(a, b)` 추가가 필요
- 사람이 범인일 때 AI들의 압박이 너무 세지 않은지 → M4에서 플레이 테스트로 조정
