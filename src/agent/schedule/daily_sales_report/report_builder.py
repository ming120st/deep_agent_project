"""
리포트 생성기 (report_builder.py)

BigQuery 데이터를 받아 LLM으로 읽기 좋은 리포트 텍스트를 생성합니다.
Google Chat 메시지 포맷에 맞게 작성합니다.
"""
import json
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from src.core.llm_config import LLM_LIGHT


# ── 프롬프트 ──────────────────────────────────────────────────────────────────
REPORT_SYSTEM_PROMPT = """당신은 eSIM 영업 데이터를 분석하여 영업팀을 위한 일일 리포트를 작성하는 AI입니다.

[작성 규칙]
- Google Chat 메시지로 전송되므로 이모지와 줄바꿈을 적극 활용하세요.
- 숫자는 천단위 콤마(,)로 포맷하세요.
- 금액은 원(₩) 단위로 표시하세요.
- 전일 대비 증감은 화살표(▲▼)와 부호로 표현하세요.
- 분석 코멘트는 간결하게, 데이터 기반으로 작성하세요.
- 과도한 수식어나 불필요한 내용 없이 핵심만 담으세요.

[리포트 구조]
1. 헤더 (날짜 + 타이틀)
2. 핵심 요약 (주문건수, 총매출, 전일대비)
3. 채널별 현황
4. TOP 상품
5. 데이터 타입 분포
6. 주문 상태 현황
7. 한 줄 코멘트
"""

REPORT_USER_PROMPT = """아래 데이터를 바탕으로 일일 영업 리포트를 작성해주세요.

<report_data>
{report_data}
</report_data>
"""

report_chain = (
    ChatPromptTemplate.from_messages([
        ("system", REPORT_SYSTEM_PROMPT),
        ("user",   REPORT_USER_PROMPT),
    ])
    | LLM_LIGHT
    | StrOutputParser()
)


# ── 메인 함수 ─────────────────────────────────────────────────────────────────
async def generate_report(data: dict) -> str:
    """
    BigQuery 데이터 dict → LLM 리포트 텍스트 반환

    Args:
        data: bigquery_client.get_all_report_data()의 반환값

    Returns:
        Google Chat 전송용 리포트 텍스트
    """
    print(f"[ReportBuilder] 리포트 생성 중: {data.get('report_date')}")

    report_data_str = json.dumps(data, ensure_ascii=False, indent=2, default=str)

    report_text = await report_chain.ainvoke({
        "report_data": report_data_str
    })

    print(f"[ReportBuilder] 생성 완료 ({len(report_text)}자)")
    return report_text
