"""
product_search_subagent 전용 tools.

esim_resolve_reissue_product가 "needs_product_search"를 리턴했을 때,
또는 사용자가 명시적으로 상품 변경(이름/코드 지정 포함)을 요청했을 때
이심재발급 딥에이전트가 product_search_subagent에게 위임하면서 쓰게 될 tool.

설계 원칙:
  - 자동탐색이든 이름/코드 지정 검색이든 tool은 하나(search_products_by_country)로 통일하고,
    어떤 필터를 조합해서 부르느냐는 서브에이전트(LLM)의 판단에 맡긴다.
  - 손실 방지용 단가 하한선(min_daily_price)과 "개통희망일"/"실명인증" 문구 제외는
    LLM 판단에 맡기지 않고 tool 내부에 항상 하드코딩으로 적용한다 (재량 여지 없음).
  - 통신사/5G/무제한 여부처럼 사용자가 자연어로 지정할 수 있는 조건은 tool 파라미터로
    받지 않는다. 대신 원본 필드(agencyInfo, isPossible5G, unlimited, dataType)를 후보
    dict에 그대로 실어서 반환하고, 그 조건 판단은 서브에이전트(LLM)에게 맡긴다.
  - 국가명 → ISO 3166-1 Alpha-3 코드 변환은 별도 tool 없이 서브에이전트 자신의 LLM 추론으로
    처리한다.
  - 결과 개수 제한(MAX_CANDIDATES)은 가격 오름차순 정렬 후 자르기 때문에, 필터 없이
    국가 전체를 조회하는 경우 무제한/5G처럼 상대적으로 비싼 상품이 컷 밖으로 밀려날 수
    있다. 그래서 request_days/name_query/code_query로 이미 좁혀진 조회는 낮은 한도,
    조건 없는 전체 조회는 넉넉한 한도를 쓴다.
"""
from __future__ import annotations

from langchain_core.tools import tool

from tools.common.internal_esim_client import esim_client

AGENT_NAME = "esim_fault_replacement.product_search"

MAX_CANDIDATES_NARROWED = 20  # request_days/name_query/code_query 중 하나라도 준 경우
MAX_CANDIDATES_BROAD = 50     # 아무 조건 없이 국가 전체를 조회하는 경우


@tool
async def search_products_by_country(
    country_id: str,
    min_daily_price: float,
    request_days: int | None = None,
    name_query: str | None = None,
    code_query: str | None = None,
) -> dict:
    """
    국가ID로 판매 중인 이심 상품 목록을 조회하고 조건에 맞게 필터링한다.

    country_id는 ISO 3166-1 Alpha-3 코드다 (예: 일본→JPN, 대만→TWN, 미국→USA).
    여러 국가를 함께 조회하려면 콤마로 이어서 넣어라 (예: "JPN,TWN").
    국가명을 이 코드로 변환하는 건 네가 직접 판단해서 넣어라. 변환이 애매하거나 확신이
    없으면 이 tool을 호출하지 말고, 상위 에이전트에게 국가명을 다시 확인해달라고 요청해라.

    필터링 규칙 (아래 항목은 tool이 기계적으로 항상 적용 — 재량 여지 없음):
    - min_daily_price 이하 단가(가격/사용일수) 상품은 항상 제외한다. (손실 방지)
    - 설명에 "개통희망일" 문구가 있거나 이름에 "실명인증" 문구가 있는 상품은 항상 제외한다.
    - request_days를 주면 사용일수가 정확히 일치하는 상품만 남긴다.
      (자동 대체탐색 — 원래 상품과 같은 조건으로 찾을 때 사용)
    - name_query를 주면 상품명에 해당 문자열이 포함된 것만 남긴다.
      (사용자가 상품명으로 특정 상품을 지정했을 때 사용, 부분일치)
    - code_query를 주면 상품코드에 해당 문자열이 포함된 것만 남긴다.
      (사용자가 상품코드로 특정 상품을 지정했을 때 사용, 부분일치)

    통신사(예: "소프트뱅크", "도코모"), 5G 가능여부, 무제한 여부는 이 tool의 파라미터로
    받지 않는다. 대신 후보마다 carriers(통신사 목록, 영문), is_5g(5G 가능여부),
    unlimited(무제한 여부), data_type(요금제 유형: daily/data/unlimited)를 실어서
    반환하니, 이런 조건이 위임에 포함돼 있으면 tool 호출 후 네가 직접 후보 리스트를
    보고 골라라.

    request_days/name_query/code_query는 상황에 맞게 필요한 것만 조합해서 넘겨라.
    셋 다 생략하면 단가 조건만 적용된 국가 전체 상품이 반환된다 — 이 경우 결과가 많을
    수 있으니, 응답의 truncated=True가 오면 결과가 더 있다는 뜻이다. 통신사/5G/무제한
    같은 조건으로 사용자가 특정 상품을 찾는 상황이면, 가능하면 request_days나
    name_query로 먼저 좁혀서 다시 호출하는 걸 권장한다.
    """
    try:
        response = await esim_client.call_esim_api_post(
            api_url="https://wapi.wifidosirak.com/api/v1/product/detail/country",
            json={"countryId": country_id},
        )
    except Exception as e:
        return {"ok": False, "error": f"국가 상품 조회 오류: {e}"}

    if response.get("resultCode") is not True:
        return {"ok": False, "error": "국가 상품 조회 실패 (resultCode=False)"}

    products = response["resultValue"].get("products", [])
    candidates = []
    # 탈락 사유별 통계 집계 (로깅/디버깅용)
    skipped = {"price": 0, "kaetong": 0, "silmyeong": 0, "useday": 0, "name": 0, "code": 0}

    data_type_map = {
        "4301001": "daily",
        "4301002": "data",
        "4301003": "unlimited",
    }

    for p in products:
        # [필터 1] 설명에 '개통희망일' 문구가 있는 상품 제외 (특수/예약 개통 상품 배제)
        if "개통희망일" in (p.get("description") or ""):
            skipped["kaetong"] += 1
            continue

        # [필터 2] 상품명에 '실명인증' 문구가 있는 상품 제외 (여권 등 실명 등록 필요 상품 배제)
        if "실명인증" in (p.get("name") or ""):
            skipped["silmyeong"] += 1
            continue

        # [필터 3] 손실 방지 하한선: 1일당 단가(가격/사용일수)가 기준치 이하인 상품 제외
        use_day = p.get("useDay") or 1
        if p.get("price", 0) / use_day <= min_daily_price:
            skipped["price"] += 1
            continue

        # [필터 4] 사용일수 일치: 요청된 일수(request_days)와 다른 상품 제외 (기존 조건과 동일 기간 탐색 시)
        if request_days is not None and p.get("useDay") != request_days:
            skipped["useday"] += 1
            continue

        # [필터 5] 상품명 검색: 지정한 검색어가 상품명에 포함되지 않으면 제외 (대소문자 무시 부분일치)
        if name_query and name_query.lower() not in (p.get("name") or "").lower():
            skipped["name"] += 1
            continue

        # [필터 6] 상품코드 검색: 지정한 코드가 상품코드(code / productCode)에 포함되지 않으면 제외 (대소문자 무시 부분일치)
        code = str(p.get("code", p.get("productCode", "")))
        if code_query and code_query.lower() not in code.lower():
            skipped["code"] += 1
            continue

        # agencyInfo: [{"JPN": "KDDI/SoftBank"}, {"TWN": "Chunghwa"}, ...] → 통신사명만 펼침
        agency_info = p.get("agencyInfo") or []
        carriers = [v for d in agency_info for v in d.values() if v]

        # 모든 하드 필터를 통과한 후보. 통신사/5G/무제한 판단에 필요한 원본 필드도 그대로 실어둔다.
        candidates.append({
            "product_code": code,
            "product_name": p.get("name", ""),
            "price": p.get("price", 0),
            "days": p.get("useDay", 0),
            "carriers": carriers,
            "is_5g": p.get("isPossible5G") == "Y",
            "unlimited": p.get("unlimited") == "Y",
            "data_type": data_type_map.get(p.get("dataType"), p.get("dataType")),
        })

    # 최저가순(가격 오름차순) 정렬
    candidates.sort(key=lambda p: p["price"])

    # request_days/name_query/code_query 중 하나라도 있으면 이미 좁혀진 조회이므로 낮은 한도,
    # 없으면(국가 전체 조회) 무제한/5G 같은 상대적으로 비싼 상품이 컷 밖으로 밀리지 않도록 넉넉한 한도 사용
    narrowed = any([request_days is not None, name_query, code_query])
    limit = MAX_CANDIDATES_NARROWED if narrowed else MAX_CANDIDATES_BROAD
    truncated = len(candidates) > limit

    print(
        f"[{AGENT_NAME}] country={country_id} 필터결과 통과={len(candidates)} "
        f"탈락={skipped} truncated={truncated}"
    )

    return {
        "ok": True,
        "candidates": candidates[:limit],
        "total_before_filter": len(products),
        "truncated": truncated,
    }