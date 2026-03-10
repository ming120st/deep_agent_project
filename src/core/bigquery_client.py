"""
BigQuery 클라이언트 (bigquery_client.py)

esim_sales 테이블에서 일별 영업 리포트용 데이터를 조회합니다.
Service Account 인증 — 기존 BOA와 동일한 SA 사용 (BQ 권한만 추가 필요)
"""
import os
import json
from datetime import datetime, timedelta
from typing import Optional
from google.cloud import bigquery
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

# ── 클라이언트 초기화 ──────────────────────────────────────────────────────────
def _build_bq_client() -> bigquery.Client:
    """Service Account JSON으로 BigQuery 클라이언트 생성"""
    sa_info = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_PATH_GBQ"))
    credentials = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/bigquery"]
    )
    return bigquery.Client(
        credentials=credentials,
        project=os.getenv("BQ_PROJECT_ID")
    )

bq_client = _build_bq_client()

BQ_DATASET = os.getenv("BQ_DATASET", "dataset")
BQ_TABLE   = os.getenv("BQ_TABLE",   "esim_sales")


# ── 날짜 헬퍼 ─────────────────────────────────────────────────────────────────
def _yesterday() -> str:
    """어제 날짜 (KST 기준) → YYYY-MM-DD"""
    from pytz import timezone
    kst = timezone("Asia/Seoul")
    return (datetime.now(kst) - timedelta(days=1)).strftime("%Y-%m-%d")

def _day_before(date_str: str) -> str:
    """특정 날짜의 전날 → YYYY-MM-DD"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d - timedelta(days=1)).strftime("%Y-%m-%d")


# ── 공통 쿼리 실행 헬퍼 ───────────────────────────────────────────────────────
def _run_query(sql: str) -> list[dict]:
    """BigQuery 쿼리 실행 → dict 리스트 반환"""
    query_job = bq_client.query(sql)
    rows = query_job.result()
    return [dict(row) for row in rows]


# ══════════════════════════════════════════════════════════════════════════════
# 리포트용 쿼리 함수들
# ══════════════════════════════════════════════════════════════════════════════

def get_daily_summary(target_date: Optional[str] = None) -> dict:
    """
    [1] 일별 핵심 요약
    - 주문완료 건수, 총 매출, 평균 결제금액
    - 전일 대비 증감 포함
    """
    date      = target_date or _yesterday()
    prev_date = _day_before(date)

    sql = f"""
    WITH
      today AS (
        SELECT
          COUNT(*)                           AS order_count,
          COALESCE(SUM(Payment_Amount), 0)   AS total_revenue,
          COALESCE(AVG(Payment_Amount), 0)   AS avg_payment
        FROM `{BQ_DATASET}.{BQ_TABLE}`
        WHERE DATE(Order_Date) = '{date}'
          AND Order_Status = '주문완료'
      ),
      yesterday AS (
        SELECT
          COUNT(*)                           AS order_count,
          COALESCE(SUM(Payment_Amount), 0)   AS total_revenue
        FROM `{BQ_DATASET}.{BQ_TABLE}`
        WHERE DATE(Order_Date) = '{prev_date}'
          AND Order_Status = '주문완료'
      )
    SELECT
      t.order_count,
      t.total_revenue,
      ROUND(t.avg_payment, 0)                                  AS avg_payment,
      t.order_count - y.order_count                            AS order_count_diff,
      t.total_revenue - y.total_revenue                        AS revenue_diff
    FROM today t, yesterday y
    """
    rows = _run_query(sql)
    return rows[0] if rows else {}


def get_channel_breakdown(target_date: Optional[str] = None) -> list[dict]:
    """
    [2] 채널별 매출 현황
    - Sales_Channel별 건수 / 매출 / 비중
    """
    date = target_date or _yesterday()
    sql = f"""
    SELECT
      Sales_Channel,
      COUNT(*)                         AS order_count,
      SUM(Payment_Amount)              AS revenue,
      ROUND(
        SUM(Payment_Amount) * 100.0 /
        NULLIF(SUM(SUM(Payment_Amount)) OVER (), 0),
        1
      )                                AS revenue_pct
    FROM `{BQ_DATASET}.{BQ_TABLE}`
    WHERE DATE(Order_Date) = '{date}'
      AND Order_Status = '주문완료'
    GROUP BY Sales_Channel
    ORDER BY revenue DESC
    """
    return _run_query(sql)


def get_top_products(target_date: Optional[str] = None, limit: int = 5) -> list[dict]:
    """
    [3] 상품별 판매 순위 TOP N
    - Product_Name별 건수 / 매출
    """
    date = target_date or _yesterday()
    sql = f"""
    SELECT
      Product_Name,
      Data_Type,
      Data_Amount,
      COUNT(*)            AS order_count,
      SUM(Payment_Amount) AS revenue
    FROM `{BQ_DATASET}.{BQ_TABLE}`
    WHERE DATE(Order_Date) = '{date}'
      AND Order_Status = '주문완료'
    GROUP BY Product_Name, Data_Type, Data_Amount
    ORDER BY order_count DESC
    LIMIT {limit}
    """
    return _run_query(sql)


def get_data_type_breakdown(target_date: Optional[str] = None) -> list[dict]:
    """
    [4] 데이터 타입별 판매 분포
    - 매일제공 / 무제한제공 / 총량제공
    """
    date = target_date or _yesterday()
    sql = f"""
    SELECT
      Data_Type,
      COUNT(*)                         AS order_count,
      ROUND(
        COUNT(*) * 100.0 / NULLIF(SUM(COUNT(*)) OVER (), 0),
        1
      )                                AS pct
    FROM `{BQ_DATASET}.{BQ_TABLE}`
    WHERE DATE(Order_Date) = '{date}'
      AND Order_Status = '주문완료'
    GROUP BY Data_Type
    ORDER BY order_count DESC
    """
    return _run_query(sql)


def get_order_status_summary(target_date: Optional[str] = None) -> list[dict]:
    """
    [5] 주문 상태별 현황
    - 주문완료 / 주문취소 / 주문대기 건수
    """
    date = target_date or _yesterday()
    sql = f"""
    SELECT
      Order_Status,
      COUNT(*) AS count
    FROM `{BQ_DATASET}.{BQ_TABLE}`
    WHERE DATE(Order_Date) = '{date}'
    GROUP BY Order_Status
    ORDER BY count DESC
    """
    return _run_query(sql)


def get_all_report_data(target_date: Optional[str] = None) -> dict:
    """
    리포트 생성에 필요한 모든 데이터를 한 번에 조회합니다.
    report_builder.py에서 이 함수만 호출하면 됩니다.
    """
    date = target_date or _yesterday()
    print(f"[BigQuery] 리포트 데이터 조회 시작: {date}")

    data = {
        "report_date": date,
        "summary":          get_daily_summary(date),
        "channel_breakdown": get_channel_breakdown(date),
        "top_products":      get_top_products(date),
        "data_type_breakdown": get_data_type_breakdown(date),
        "order_status":      get_order_status_summary(date),
    }

    print(f"[BigQuery] 조회 완료")
    return data
