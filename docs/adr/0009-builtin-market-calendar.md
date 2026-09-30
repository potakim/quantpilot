# 0009 · 시장 캘린더는 코어 내장 표로 두고 exchange_calendars는 선택 어댑터로 쓴다

상태: 승인 (2026-09-30)

## 맥락
04 §1은 `core/clock.py`가 `exchange_calendars`(XKRX, XNYS)로 휴장일·서머타임을 처리한다고 적었다. 그런데 CLAUDE.md 코딩 규칙은 코어(`core` 등)에 pandas·numpy 외 의존성을 금지하고, 외부 라이브러리는 어댑터에서 `try/except ImportError`로만 import하게 한다. 또 `exchange_calendars`는 `infra` extra에만 있어서 CI(`.[dev]`)와 기본 설치에는 없다. 코어가 이 라이브러리에 직접 기대면 테스트가 설치 상태에 따라 달라진다.

`exchange_calendars` 4.13.2로 확인한 사실:
- 기본 범위가 "오늘 + 1년"이라, 같은 코드가 날짜에 따라 다른 결과를 낸다.
- 미래 연도의 임시 공휴일(예: 2026-06-03 제9회 전국동시지방선거일)이 빠져 있다.
- KRX 수능일 지연 개장(10:00~16:30)은 2020년만 들어 있다.

## 결정
1. 서머타임·현지시간 변환은 표준 라이브러리 `zoneinfo`로 한다 (`Asia/Seoul`, `America/New_York`). 외부 의존이 아니다.
2. 휴장일과 특수 세션(KRX 새해 첫 거래일 10:00 개장, NYSE 13:00 조기 폐장)은 `core/calendars.py`에 **2020-01-01 ~ 2027-12-31 내장 표**로 둔다. 표는 `exchange_calendars` 4.13.2에서 뽑고, 빠진 임시 공휴일은 출처를 적어 손으로 더한다. 결과가 설치 상태·실행 날짜와 무관하게 결정적이다.
3. 표 범위 밖 날짜를 물으면 조용히 평일로 치지 않고 `CalendarOutOfRange`를 던진다.
4. `data/calendars.py`는 `exchange_calendars`를 `try/except ImportError`로 감싼 선택 어댑터다. 범위를 넘는 백테스트가 필요하면 이 어댑터를 `MarketClock(market, calendar=...)`에 주입한다. `python -m quantpilot.data.calendars`는 내장 표를 다시 만들 때 쓸 원본을 출력한다.
5. 업비트는 24시간 연중무휴다. `session_bounds`는 업비트 일봉 경계와 같은 **09:00 KST ~ 다음 날 09:00 KST**를 한 세션으로 본다. 거래소 점검은 P1-06 `events.py`(이벤트 캘린더)에서 다룬다.
6. ADR 0008 §5의 임시 처리(tz-naive = UTC)를 교체한다. `db/mappers.py`는 tz-naive 시각을 **그 행의 시장 현지시간**으로 보고 `clock.to_utc`로 UTC에 저장하며, 읽을 때 `clock.to_local`로 현지 tz-naive로 돌려준다. `judgments`에는 `market` 컬럼이 없으므로, 연결된 `signals` 행의 시장을 쓴다.

## 결과
- 매년 말 다음 해 표를 추가해야 한다. 내장 표와 `exchange_calendars`를 대조하는 테스트가 있어서(라이브러리가 설치됐을 때만 실행), 라이브러리를 올리면 차이가 바로 드러난다. 손으로 더한 날짜는 대조 대상에서 뺀다.
- KRX 수능일 지연 개장은 2020년 말고는 표에 없다. 해당 날 09:00~10:00은 실제로는 휴장인데 `is_open`이 True를 돌려준다. 확인된 날짜가 생기면 표에 더한다.
