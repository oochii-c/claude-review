# claude-review

Claude와의 협업(Claude Code, 웹, 데스크톱)에서 어제 대화의 실패 패턴을 찾아
귀책(모델/프롬프트/설정/환경)과 함께 하루 1회 리포트로 낸다. 기획은 `SPEC.md`,
인수인계는 `HANDOFF.md`.

## 실행

```
python review.py            # 어제 이후 세션(mtime), 리포트 생성 후 브라우저 오픈
python review.py --all      # 전체 세션
python review.py --judge    # + Claude API 귀책 판정 (anthropic SDK + 자격증명 필요)
python review.py --no-open  # 브라우저 안 열기
```

## 상태 (구현 단계, SPEC 7절 기준)

- [x] 1. 수집 — `collectors/claude_code.py` JSONL 파서 → `review.db`, `config_snapshot.py`
- [x] 2. 감지 — `analyzers/detect.py` 규칙 기반 사건/라운드 후보
- [x] 4. 리포트 — `report/build.py` + `template.html`, 브라우저 오픈
- [~] 3. 판정 — `analyzers/judge.py` 준비됨. `--judge`로 실행. `pip install anthropic`
      + `ANTHROPIC_API_KEY`(또는 `ant auth login` 프로필) 필요. 없으면 자동 skip.
- [ ] 5. 확장(웹/데스크톱 실시간), 6. 피드백 루프·추세 차트

`analyzers/phrases.py`는 사용자 정정 발화만 규칙 집계(모델 측·동의어 정규화는 판정 API 필요).

## 데이터

- Claude Code: `~/.claude/projects/**/*.jsonl` 자동 스캔.
- 웹/데스크톱: claude.ai 설정 > 개인정보 > 데이터 내보내기 `conversations.json`
  (파서 `export_json.py`는 5절, 아직 미구현).

`review.db`, `feedback.json`, `out/`, `conversations.json`은 대화 원문이라 `.gitignore` 처리.
