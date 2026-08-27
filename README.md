# claude-review

Claude와 협업(Claude Code / claude.ai 웹 / 데스크톱)하면서 **어제 대화 어디서
틀어졌고 누구 책임이었는지**를 근거와 함께 찾아, 하루 1회 정적 HTML 리포트로 낸다.

- **파악** — 정정 발화·반복 요청·파일 churn 같은 신호로 마찰 구간(사건)을 자른다.
- **판정** — 별도 Claude 호출이 귀책을 넷 중 하나로 매긴다: 모델 / 프롬프트 / 설정 / 환경. 근거 인용 + 반대 근거 한 줄 필수.
- **추적** — 같은 유형의 실패가 시간이 지나며 줄어드는지 본다.

목표가 아닌 것: 대화 아카이브·검색, 실시간 감시. 실행은 하루 1회 배치.

전신 프로젝트: [oochii-c/youbad](https://github.com/oochii-c/youbad) — 화행 분류와
한국어 오탐 규칙을 여기로 흡수했다. 설계 전문은 [`SPEC.md`](./SPEC.md).

## 실행

```bash
python review.py            # 어제 이후 세션(mtime) → 리포트 생성 후 브라우저 오픈
python review.py --all      # 전체 세션
python review.py --judge    # + 귀책 판정 (아래 백엔드)
python review.py --judge --judge-limit 20   # 가장 뜨거운 20건만 판정
python review.py --no-open  # 브라우저 안 열기
```

**열기(heat)는 섭씨 체온.** 36.0 = 평온, 36~37.5 = 정상(소소한 마찰), 그 위는
발열. 하루 라벨: 싸움 없음 / 소소한 마찰 / 한판 붙은 날.

## 판정 백엔드 (`--judge`)

API 키가 없어도 된다.

1. `ANTHROPIC_API_KEY` 있으면 → `anthropic` SDK 직접 호출 (`pip install anthropic`).
   키는 gitignore된 `.env`에 `ANTHROPIC_API_KEY=...` 로 둔다.
2. 없으면 → 로그인된 `claude` CLI(`claude -p`)로 판정. **별도 API 과금 없이 구독으로 감.**
3. 둘 다 없으면 판정은 건너뛰고 수집·감지·리포트만 돈다(리포트엔 "미판정" 표기).

## 구현 상태 (SPEC 7절 기준)

- [x] 1. 수집 — `collectors/claude_code.py`(JSONL 파서) → `review.db`, `config_snapshot.py`
- [x] 2. 감지 — `analyzers/detect.py` 규칙 기반 사건/라운드 후보
- [x] 3. 판정 — `analyzers/judge.py` (SDK 또는 `claude` CLI 백엔드)
- [x] 4. 리포트 — `report/build.py` + `template.html`, 브라우저 오픈
- [ ] 5. 확장(웹/데스크톱 실시간 수집), 6. 피드백 루프·추세 차트

`analyzers/phrases.py`의 사용자 정정 발화 집계는 규칙 기반. 모델 측 집계와
동의어 정규화는 판정 백엔드가 붙을 때 확장.

## 데이터

- **Claude Code**: `~/.claude/projects/**/*.jsonl` 자동 스캔.
- **웹/데스크톱**: claude.ai 설정 > 개인정보 > 데이터 내보내기 `conversations.json`
  (`export_json.py`는 5절, 미구현).

대화 원문이 들어가므로 `review.db` · `feedback.json` · `out/` · `conversations.json` ·
`.env` 는 커밋하지 않는다(`.gitignore`).
