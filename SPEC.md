# claude-review — Claude 협업 데일리 리뷰

## 1. 목표

Claude와의 협업(Claude Code, claude.ai 웹, 데스크톱 앱)에서 반복되는 실패 패턴을 찾아내서,
사용자 쪽과 모델 쪽 모두 고쳐 낭비되는 턴을 줄인다.

- 파악: 어제 대화 중 어디서 틀어졌고 누구 책임이었는지 근거와 함께 안다.
- 교정: 프롬프트 잘못이면 사용자 습관을, 모델 잘못이면 CLAUDE.md·프로젝트 지침을 고친다.
- 추적: 같은 유형의 실패가 시간이 지나며 줄어드는지 수치로 확인한다.

목표가 아닌 것: 대화 아카이브·검색(부산물), 실시간 감시, 옵시디언 연동.
실행 주기는 하루 1회. 실시간성 불필요.

## 2. 형태

로컬 파이썬 프로젝트 + SQLite + 정적 HTML + (5단계) 크롬 확장. 서버·외부 DB 없음.

```
claude-review/
  review.py            # 진입점: 수집 → 분석 → 리포트 → 브라우저 오픈
  db.py                # SQLite 연결 + 스키마 초기화 + upsert 헬퍼
  requirements.txt     # judge용 anthropic SDK (선택)
  collectors/
    claude_code.py     # ~/.claude/projects/**/*.jsonl 파서
    export_json.py     # claude.ai 데이터 내보내기 conversations.json 파서
    config_snapshot.py # CLAUDE.md·프로젝트 지침 스냅샷 (해시 버전 관리)
    receiver.py        # (5단계) 확장이 POST하는 웹 대화 수신 로컬 서버
  analyzers/
    detect.py          # 사건·라운드 후보 감지 (규칙 기반)
    judge.py           # Claude API 판정
    phrases.py         # 자주 한 말 집계
  report/
    build.py           # 정적 HTML 생성
    template.html
  extension/           # (5단계) 크롬 확장, 동기화 모드만
  review.db
  feedback.json        # 사용자가 뒤집은 판정 기록
  out/YYYY-MM-DD.html
```

매일 아침 cron/launchd가 `review.py` 실행. 확장은 브라우저 시작 시 알람으로 먼저 전송.

## 3. 데이터 소스

| 소스 | 방법 | 비고 |
|---|---|---|
| Claude Code | `~/.claude/projects/<proj>/<session>.jsonl` 스캔. 전날 이후 mtime만 파싱 | 훅 불필요. 파일 스캔이 누락 없음 |
| 웹 + 데스크톱 | 1~4단계: 설정 > 개인정보 > 데이터 내보내기 ZIP의 `conversations.json` 파싱 (날짜 범위 지정 가능) | 웹·데스크톱은 같은 클라우드 대화 |
| 웹 + 데스크톱 | 5단계: 크롬 확장이 하루 1회 claude.ai 대화 목록 스캔, 마지막 동기화 이후 갱신된 대화만 열어 receiver로 POST | 내부 API 사용 금지, DOM만 읽기 |
| 설정 | 세션 시각의 글로벌·프로젝트 CLAUDE.md, 프로젝트 지침, 활성 스킬을 해시로 버전 저장 | 판정 시 당시 설정 전문 필요 |

중복 제거: Claude Code는 session_id, 웹은 conversation uuid 기준 upsert.
월 1회 공식 내보내기로 누락 대조.

## 4. 공통 스키마 (SQLite)

```
sessions(id, source[code|web|desktop], project, started_at, ended_at, model, config_version_id)
messages(id, session_id, idx, role, ts, text, tool_calls_json)
config_versions(id, hash, captured_at, global_md, project_md, instructions, skills_json)
incidents(id, session_id, start_idx, end_idx, blame, type, confidence, evidence, counter_evidence,
          wasted_turns, suggestion, heat, status[pending|approved|overridden])
rounds(id, incident_id, outcome, turns, end_utterance)
phrases(id, side[user|model], canonical, count, week, examples_json)
```

## 5. 분석

### 5.1 사건(incident) 감지 — 규칙 기반, detect.py

후보 구간을 자르는 신호:
- 정정 발화: "아니", "다시", "그게 아니라", "말했잖아", "왜 자꾸" 등 (정규화 사전 유지)
- 같은 요청 반복
- Claude Code: Edit 직후 되돌림, 같은 파일 단기간 반복 수정, 테스트 실패 후 재시도 루프
- 답변 직후 사용자가 처음에 없던 조건 추가
- 결론 없는 세션 종료, 잦은 컴팩션

사건 = 시작 신호부터 해결(사용자가 다음 주제로 넘어감 또는 세션 종료)까지.
낭비 턴 = 그 구간의 턴 수.

### 5.2 귀책 판정 — judge.py, 별도 Claude API 호출

귀책 넷:
- 모델: 지시 무시, 잘못된 사실/코드, 확인 없이 추측 진행, 범위 초과
- 프롬프트: 모호한 요청, 빠진 맥락, 중간 목표 변경, 한 메시지에 여러 요청
- 설정: 지시 없음 / 모호 / 충돌 / 낡음 / 과다
- 환경: 도구 오류, 권한, 네트워크

판정 프롬프트 입력: 사건 구간 원문 + 당시 설정 전문 + feedback.json의 뒤집힌 판정 예시.
판정 순서: "이 실패를 막을 지시가 설정에 있었는가"를 먼저 묻는다.
있었는데 안 따름 → 모델. 없거나 이상함 → 설정 또는 프롬프트.
같은 유형이 여러 세션에서 반복되면 설정 쪽으로 기울인다 (3회 이상이면 규칙화 대상).

출력(JSON): blame, type, confidence, evidence(원문 인용 필수), counter_evidence(반대로 볼 근거 한 줄),
wasted_turns, suggestion(대안 프롬프트 또는 CLAUDE.md 규칙), heat(0~10).

편향 대책:
- 판정자는 대화 당사자가 아닌 별도 인스턴스
- 프롬프트 귀책으로 분류하려면 원 요청의 부족한 부분을 반드시 인용
- 반대 근거 한 줄 강제
- 사용자가 뒤집은 라벨은 feedback.json에 저장, 다음 판정의 few-shot으로 사용
- 주 1회 표본 직접 검토

### 5.3 라운드와 승부 결과

라운드 = 사용자와 모델이 서로 다른 것을 원한 대치 구간.
모델이 정정을 받자마자 따르면 라운드 아님(단순 정정). 모델이 최소 한 번 다른 입장을 유지해야 라운드.

결과 넷 (옳고 그름이 아니라 "어느 입장으로 끝났나"):
- 내가 이김: 최종 결과가 사용자 원래 요구와 일치
- 너가 이김: 사용자가 조건을 바꾸거나 모델 제안 수용. 종료 발화가 체념형("아 몰라 그냥 해")
- 좋은 승부: 결론이 양쪽 최초 입장보다 나아짐. 제3안 합의, 또는 한쪽이 상대 근거를 명시 인정("그래 그게 낫겠다", "그 지적이 맞습니다")
- 미결: 결론 없이 종료. 환경 원인은 태그로 표시. 미결은 어느 쪽 승리로도 세지 않음

프로그램의 실제 성적은 좋은 승부 비율.

### 5.4 열기(heat) 점수

규칙으로 후보 구간을 잡고 말투 강도만 API로 채점. 0~10.
약: "아니", "다시" / 강: "왜 자꾸", "말했잖아", 욕설, 느낌표 연타.
세션 열기 = 구간 최댓값. 하루 라벨: 싸움 없음 / 소소한 마찰 / 한판 붙은 날.

### 5.5 자주 한 말 — phrases.py

- 사용자: 정정 발화만 집계
- 모델: 대치 구간 안의 발화만 집계
- 동의어 정규화는 API로 ("같은 뜻끼리 묶고 대표 문구 하나"). 대표 문구는 실제 최다 원문.
- 주 단위 top 5. 각 항목에 CLAUDE.md 제안 한 줄 연결.

## 6. 리포트 화면 (정적 HTML, 위에서 아래로)

1. 스코어보드
   - 날짜, 하루 라벨, 세션 수(소스별), 설정 버전
   - 내가 이김 / 너가 이김 / 좋은 승부 / 미결
   - 최고 열기, 정정 발화 수, 가장 긴 대치, 평화로운 세션 수
   - 세션별 열기 스트립 (너비=세션 길이, 높이·색=열기)
   - 메인 이벤트: 가장 뜨거운 세션의 텐션 곡선 + 라운드 목록 + "판정 보기" 링크
2. 자주 한 말 top 5 (나 / 너), 각각 코멘트 한 줄
3. 사건 분석
   - 필터(귀책 넷), 정렬(낭비 턴)
   - 좌: 사건 카드 목록 / 우: 문제 구간 원문, 판정 근거, 반대 근거, 제안, 판정 수정 버튼(→ feedback.json)
4. 추세 (접힘)
   - 주별 승부 결과 누적 막대 (좋은 승부 비율 추이)
   - 일별 사건 비율(사건 수 / 총 턴 수) 선 차트 + 설정 버전 변경 세로선
   - (데이터 쌓인 뒤) 요일×시간 열기 히트맵, 대치 길이 분포

드랍한 것: 채널별 테니스 스코어보드, 옵시디언 출력, 세션 타임라인 뷰.

## 7. 구현 순서

1. youbad 흡수 + 파서. `~/.claude/scripts/comm_analyze.py`(youbad)의 JSONL 파싱과 한국어 화행 분류를
   `claude_code.py` / `detect.py`로 옮긴다. `export_json.py`, `config_snapshot.py` 추가.
   파싱 결과를 review.db에 저장. 실제 로그로 확인.
2. `detect.py` 사건·라운드 후보 감지. youbad의 friction/affect 축은 버리고 새로 설계. 실제 로그로 오탐/누락 조정.
3. `judge.py` 판정 프롬프트. 표본 20건 직접 검토해 정확도 확인.
4. `build.py` + `template.html`. 브라우저 자동 오픈.
5. `extension/` + `receiver.py`. 동기화 모드만.
6. 판정 수정 피드백 루프, 히트맵·분포 차트.

## 8. youbad와의 관계

github.com/oochii-c/youbad 는 이 기획의 전신. 살릴 것과 버릴 것:

- 살림: JSONL 파싱, 화행 분류 우선순위(`redirect > WH질문 > 명령 > probe > 질문 > 진술`)와
  검증된 한국어 오탐 규칙("커밋해줄래?"=명령, "삭제하면 어케됨?"=질문, 선언형 `아니야`≠probe, `왜냐하면`≠질문).
  redirect 샘플을 근거로 인용하는 방식.
- 맥락으로만 살림: comm-profile.md의 "누구와 일하는가" 절. 판정 프롬프트에 배경으로 넣되 규칙으로 쓰지 않는다.
- 버림: friction/affect 정규식 축(미검토, 짜증 0.2%는 못 잡는 것), 28일 집계로 성향 규칙을 뽑아
  comm-profile을 생성하는 로직, CLAUDE.md 자동 반영.
- 방향 차이: youbad는 Claude가 사용자를 프로파일링해 스스로 맞추는 것. claude-review는 양쪽을 대칭으로 판정하고
  사람이 읽고 사람이 고치는 것. 제안까지만, 반영은 사람.

## 9. 제약·주의

- claude.ai 내부 API는 사용하지 않는다. 확장은 화면에 보이는 DOM만 읽는다.
- 세션 원문은 SQLite에만. 리포트에는 사건 구간만 노출.
- 사건 비율은 반드시 세션 수 또는 총 턴 수로 정규화.
- 판정 API 비용 관리: 후보 구간만 전송, 설정 전문은 해시 캐시.
- 툴 호출 결과(파일 내용, 명령 출력)는 본문의 대부분을 차지하지만 판정에 거의 안 쓴다. 일정 길이에서 잘라 저장하는
  옵션을 둔다(기본 2KB). 용량 자체는 문제 아님(헤비 사용도 연 수 GB).
- 원문 보관 규칙은 첫 버전에 넣지 않는다. 필요해지면 "판정 끝난 세션 90일 후 원문 삭제, 사건 구간만 보존".
