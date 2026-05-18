"""
src/agent/schedule/bq_analysis_schedule/cards.py

BigQuery 분석 스케줄 등록용 Google Chat 카드 빌더.

[카드 구성]
  - TextInput   : 에이전트 이름 (중복 불가)
  - TextInput   : 분석 요청 내용 (자연어)
  - SelectionInput (DROPDOWN): 수행 간격 (매일/매주/매시간)
  - SelectionInput (DROPDOWN): 수행 시간 (00~23시)
  - SelectionInput (DROPDOWN): 수행 분 (00분/30분)
  - Button      : 등록 확인 (submitBqSchedule)
"""

from src.agent.analytics.subagents.schedule.bq_analysis_schedule.schedule_manager import INTERVAL_LABELS


def build_bq_schedule_card() -> dict:
    """/데이터분석스케줄 커맨드 응답 카드."""
    hour_items = [
        {"text": f"{h:02d}시", "value": str(h), "selected": h == 8}
        for h in range(24)
    ]

    return {
        "cardsV2": [{
            "cardId": "bq_schedule_register",
            "card": {
                "header": {
                    "title": "📊 데이터 분석 스케줄 등록",
                    "subtitle": "에이전트 이름과 분석 내용, 실행 주기를 설정하세요.",
                    "imageUrl": "https://fonts.gstatic.com/s/i/short-term/release/googlesymbols/analytics/default/48px.svg",
                    "imageType": "SQUARE",
                },
                "sections": [
                    {
                        "header": "에이전트 정보",
                        "widgets": [
                            {
                                "textInput": {
                                    "name": "bq_agent_name",
                                    "label": "에이전트 이름",
                                    "hintText": "예: 주간 매출 리포트 (중복 불가)",
                                    "type": "SINGLE_LINE",
                                }
                            },
                            {
                                "textInput": {
                                    "name": "bq_query",
                                    "label": "분석 요청",
                                    "hintText": "예: 지난 7일간 상품별 매출을 분석해줘",
                                    "type": "MULTIPLE_LINE",
                                }
                            },
                        ],
                    },
                    {
                        "header": "실행 주기",
                        "widgets": [
                            {
                                "selectionInput": {
                                    "name": "bq_interval",
                                    "label": "수행 간격",
                                    "type": "DROPDOWN",
                                    "items": [
                                        {
                                            "text": label,
                                            "value": key,
                                            "selected": key == "daily",
                                        }
                                        for key, label in INTERVAL_LABELS.items()
                                    ],
                                }
                            },
                            {
                                "columns": {
                                    "columnItems": [
                                        {
                                            "horizontalSizeStyle": "FILL_AVAILABLE_SPACE",
                                            "widgets": [{
                                                "selectionInput": {
                                                    "name": "bq_hour",
                                                    "label": "수행 시간",
                                                    "type": "DROPDOWN",
                                                    "items": hour_items,
                                                }
                                            }],
                                        },
                                        {
                                            "horizontalSizeStyle": "FILL_AVAILABLE_SPACE",
                                            "widgets": [{
                                                "selectionInput": {
                                                    "name": "bq_minute",
                                                    "label": "수행 분",
                                                    "type": "DROPDOWN",
                                                    "items": [
                                                        {"text": "00분", "value": "0", "selected": True},
                                                        {"text": "30분", "value": "30"},
                                                    ],
                                                }
                                            }],
                                        },
                                    ]
                                }
                            },
                        ],
                    },
                    {
                        "widgets": [
                            {
                                "buttonList": {
                                    "buttons": [
                                        {
                                            "text": "✅ 스케줄 등록",
                                            "onClick": {
                                                "action": {"function": "submitBqSchedule"}
                                            },
                                            "color": {
                                                "red": 0.25, "green": 0.55, "blue": 1.0, "alpha": 1.0,
                                            },
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                ],
            }
        }]
    }


def build_schedule_confirm_message(
    agent_name: str,
    query: str,
    interval: str,
    hour: int,
    minute: int,
    schedule_id: str,
) -> str:
    """스케줄 등록 완료 후 확인 메시지."""
    interval_label = INTERVAL_LABELS.get(interval, interval)
    time_str = f"{hour:02d}:{minute:02d}" if interval != "hourly" else "매시간 실행"
    short_id = schedule_id[-8:]

    return (
        f"✅ *'{agent_name}' 스케줄이 등록되었습니다.*\n\n"
        f"📋 분석 내용: _{query}_\n"
        f"🔄 수행 간격: {interval_label}\n"
        f"⏰ 수행 시간: {time_str}\n"
        f"🆔 스케줄 ID: `{short_id}`\n\n"
        f"_대시보드 에이전트 탭에서 확인 및 삭제가 가능합니다._"
    )
