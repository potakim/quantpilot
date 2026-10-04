# 0030 · 관문 G1·G2 증거를 운영 DB와 화면에 연결한다

상태: 승인 (2026-10-04)

## 맥락
2026-10-04 배포 전 검토에서 관문 증거가 화면·API에 닿지 않는 것을 확인했다.

- **G1**: `api/gates.py`는 settings `gate_report.g1.<전략>`을 읽는다. 그런데 이 값을 쓰는 코드가 없다. `scripts/verify_g1.py`는 결과를 출력만 한다. 게다가 `scripts/`는 이미지에 들어가지 않는다(`Dockerfile`은 `quantpilot/`만 복사). 그래서 README는 "G1 통과"인데, VPS 대시보드는 "verify_g1 결과 없음 → G1 미달"을 보여 준다.
- **G2**: `create_app()`이 `CalibrationSource`를 받지 않으므로 `/judgments/calibration`·`/judgments/ab`는 늘 503이고, `/reports/gates` G2는 늘 "calibration 미연결"이다. 계산 로직(`judgment/calibration.py`, `judgment/ab.py`, `cli.build_ab_report`)은 이미 있고 `qp report ab`로만 쓸 수 있다. 4주 페이퍼 운영의 목적이 G2 판정인데, 화면에서 진행 상황을 볼 수 없다.

## 결정
1. **G1을 패키지로 옮긴다**: 판정 로직을 `quantpilot/ops/g1.py`로 옮긴다.
   - CLI `qp gate g1 [--write] [--db-url URL]`을 추가한다. `--write`면 전략별로 `gate_report.g1.<전략>`에 다음을 쓴다: `{within_20pct, basis, metrics…, checked_at}`.
   - `within_20pct`는 지표 비교 결과만 담는다. 시도 횟수(≤7)는 지금처럼 API가 `AttemptTracker`로 따로 본다. 키 이름은 기존 API 호환을 위해 유지한다. 실제 허용폭은 `basis`에 적는다(GEM = 공개 수치 ±20%, 변동성 돌파 = 독립 기준 구현 ±10%·진입 ±3%, ADR 0024).
   - `scripts/verify_g1.py`는 같은 함수를 부르는 얇은 진입점으로 남긴다.
   - 이 경로는 settings 키를 쓰는 유일한 곳이다. `PATCH /settings`로는 여전히 쓸 수 없다(화면에서 관문을 통과시킬 수 없다).
2. **G2를 DB에 연결한다**:
   - `build_ab_report`를 `quantpilot/db/reports.py`로 옮긴다. cli는 그것을 다시 내보낸다.
   - `quantpilot/api/calibration.py::DbCalibration`이 `CalibrationSource`를 구현한다. 시장은 업비트, 결과는 주(weeks)별로 60초 캐시한다(대시보드 폴링 부담).
   - 모듈 수준 `app = create_app(db_calibration=True)`로 운영 앱에만 연결한다. 테스트용 `create_app()` 기본값은 그대로 `None`이다.
   - G2 판정 규칙(`judgment/ab.py::g2_verdict`)은 바꾸지 않는다: 표본 20건 미만은 "판정 보류", MDD_ON < MDD_OFF, Brier < 0.25.

## 결과
- VPS에서 다음을 실행하면 대시보드와 전략 카드의 G1이 실제 값으로 바뀐다:
  `docker compose run --rm api qp fetch …` → `qp gate g1 --write`
- `/judgments/calibration`·`/judgments/ab`·`/reports/gates` G2가 페이퍼 운영 데이터로 채워진다. 4주 동안 진행 상황(표본 수, MDD ON/OFF, Brier)을 화면에서 볼 수 있다.
- 계산은 `qp report ab`와 같은 코드다. 그래서 CLI 결과와 화면이 어긋나지 않는다.
