from __future__ import annotations

import asyncio
from typing import Annotated

from langchain_core.tools import tool

from tools.common.mos_client import mos_client


@tool
async def inquire_wifi_usage(
    imei_list: Annotated[
        list[str],
        "사용량을 조회할 단말기 IMEI(또는 ICCID) 번호 목록. 여러 대 동시 조회 가능.",
    ],
    start_date: Annotated[
        str,
        "조회 시작일을 %Y%m%d 형식으로 전달. 예: 20251201",
    ],
    end_date: Annotated[
        str,
        "조회 종료일을 %Y%m%d 형식으로 전달. 예: 20251231",
    ],
) -> dict:
    """
    와이파이도시락 단말기의 기간별 데이터 사용량을 조회한다.

    imei_list, start_date, end_date가 모두 확보되면 바로 이 Tool을 호출한다.
    확보되지 않았다면 먼저 사용자에게 IMEI 번호와 조회 기간(시작일/종료일)을 물어봐야 한다.

    반환값:
    - success=True: usage_report(일자별 사용량 및 합계가 포함된 텍스트)를 사용자에게 그대로 보여준다.
      사용자가 결과를 이메일로 받고 싶어하면 이메일 주소를 물어본 뒤, 이 결과를 별도의 메일 발송 Tool에
      전달해서 보내야 한다. 이 Tool은 조회만 수행하며 이메일 전송은 하지 않는다.
    - success=False: reason을 사용자에게 안내한다. IMEI 번호나 조회 기간이 잘못되었을 가능성이 있으니
      필요하면 다시 확인해서 재시도한다.
    """
    try:
        usage_report = await asyncio.to_thread(
            mos_client.get_usage_by_date_range,
            imei_list,
            start_date,
            end_date,
        )
    except Exception as e:
        return {
            "success": False,
            "reason": (
                "사용량 조회 중 오류가 발생했습니다. 원인은 다음 중 하나일 수 있습니다: "
                "1) 잘못된 IMEI 번호, 2) 잘못된 기간 설정, 3) 시스템 오류. "
                f"상세: {e}"
            ),
        }

    return {
        "success": True,
        "usage_report": usage_report,
    }
