---
name: bigquery-query
description: BigQuery에서 실제 데이터를 조회하거나 COUNT, SUM, AVG 등의 집계,
            GROUP BY 분석, 기간별 실적 비교, 증감률 계산이 필요한 경우 사용한다.
            SELECT 또는 WITH ... SELECT 형태의 조회 전용 SQL을 작성하고
            run_sql_query Tool을 사용해야 하는 작업에 적용한다.
---

# BigQuery 데이터 조회

BigQuery 환경에서 데이터 집계, 통계 계산 및 조회를 위한 Standard SQL을 작성하고 실행합니다.

## 언제 사용하나요?
- 실제 DB 데이터 조회 및 결과 확인
- 데이터 집계(COUNT, SUM, GROUP BY 등) 및 통계 계산
- 특정 조건을 만족하는 샘플 데이터 탐색

## SQL 작성 규칙
1. **조회 전용(SELECT) 문만 허용**
   - 반드시 `SELECT` 또는 `WITH ... SELECT`로 시작하는 single statement여야 합니다.
   - `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP` 등 데이터/스키마 변경 쿼리는 거부됩니다.
   - 세미콜론(`;`)으로 여러 문장을 이어 붙일 수 없습니다.

2. **테이블 경로 표기**
   - `get_table_schema`로 확인한 전체 경로(`project.dataset.table`)를 백틱(` ` `)으로 감싸서 작성합니다.
   - 예: `` `project.dataset.table_name` ``

3. **결과 행 제한 (LIMIT)**
   - 쿼리에 `LIMIT` 절이 명시되지 않으면 자동으로 기본 행 제한(`row_limit`, 기본값 200행)이 적용됩니다.

## 예시 쿼리

```sql
WITH summary AS (
  SELECT 
    category,
    COUNT(*) AS total_count,
    AVG(price) AS avg_price
  FROM `my_project.my_dataset.orders`
  WHERE order_date >= '2026-01-01'
  GROUP BY category
)
SELECT * FROM summary
ORDER BY total_count DESC
LIMIT 50;