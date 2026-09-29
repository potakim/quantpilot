# 0006 · 저장소는 TimescaleDB + Redis, 단일 VPS Docker Compose

상태: 승인 (2026-09-29)

## 맥락
1인 사용자, 초당 수십 이벤트, 캔들·원장 시계열. 관리형 클라우드는 비용 대비 과하고, 업비트 API 키가 고정 IP를 요구한다.

## 결정
PostgreSQL 16 + timescaledb 확장(캔들·원장·스냅샷을 hypertable), Redis(실시간 상태·pub/sub·큐·레이트리밋). 서울 리전 소형 VPS 1대에 Docker Compose. 페이퍼와 실전은 별도 compose 프로젝트·별도 DB.

## 결과
- 단일 장애점. 엔진 다운 시 스케줄러의 시간 청산 백업과 알림으로 보완.
- 다중 사용자 서비스화 시 재검토(4단계).
