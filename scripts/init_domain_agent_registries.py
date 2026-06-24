import asyncio
from datetime import datetime

from src.agent.registry import AGENT_REGISTRY
from src.core.mongo_client import get_collection


DOMAINS = [
    {
        "domain_id": "wifi",
        "name": "와이파이 에이전트",
        "description": "와이파이 도시락 사용량 조회, 회선 변경, SIM 교체 등 와이파이 운영 업무를 처리합니다.",
        "keywords": ["와이파이", "wifi", "WiFi", "도시락", "사용량", "회선변경", "SIM"],
        "owner_teams": ["operations"],
        "enabled": True,
    },
    {
        "domain_id": "esim",
        "name": "이심 에이전트",
        "description": "eSIM 주문 취소, QR 재발송, 불량 교체 등 eSIM 운영 업무를 처리합니다.",
        "keywords": ["이심", "esim", "eSIM", "QR", "재발송", "주문취소", "불량교체"],
        "owner_teams": ["operations"],
        "enabled": True,
    },
    {
        "domain_id": "usim",
        "name": "유심 에이전트",
        "description": "USIM 상품과 회선, 배송, 개통 관련 운영 업무를 처리합니다.",
        "keywords": ["유심", "usim", "USIM", "심카드", "개통", "배송"],
        "owner_teams": ["operations"],
        "enabled": True,
    },
    {
        "domain_id": "analytics",
        "name": "데이터분석 에이전트",
        "description": "BigQuery 기반 판매, 매출, 운영 데이터 분석과 리포트 생성을 처리합니다.",
        "keywords": ["데이터분석", "분석", "BigQuery", "리포트", "매출", "판매"],
        "owner_teams": ["data"],
        "enabled": True,
    },
    {
        "domain_id": "hr",
        "name": "HR/총무 에이전트",
        "description": "인사, 총무, 사내 운영 지원 업무를 처리합니다.",
        "keywords": ["HR", "인사", "총무", "휴가", "근태", "비품"],
        "owner_teams": ["hr", "general_affairs"],
        "enabled": False,
    },
    {
        "domain_id": "finance",
        "name": "회계/정산 에이전트",
        "description": "회계, 정산, 매입매출, 비용 처리 관련 업무를 처리합니다.",
        "keywords": ["회계", "정산", "매출", "매입", "비용", "세금계산서"],
        "owner_teams": ["finance"],
        "enabled": False,
    },
    {
        "domain_id": "commerce",
        "name": "마켓/OTA 에이전트",
        "description": "마켓, OTA, 판매 채널과 상품 운영 업무를 처리합니다.",
        "keywords": ["마켓", "OTA", "커머스", "채널", "상품", "판매"],
        "owner_teams": ["commerce"],
        "enabled": False,
    },
    {
        "domain_id": "system",
        "name": "시스템 유지보수",
        "description": "시스템 인프라 및 RAG 지식베이스 동기화 등 백그라운드 작업을 처리합니다.",
        "keywords": ["시스템", "인덱스", "동기화", "백그라운드"],
        "owner_teams": ["ai-team"],
        "enabled": True,
    },
]

# 새로운 워크플로우(서브에이전트)가 추가될 때마다 AGENT_REGISTRY에 메타데이터를 등록하고, AGENT_DOMAIN_MAP에 도메인 매핑을 추가해야 한다.
AGENT_DOMAIN_MAP = {
    "wifi_usage_inquiry": "wifi",
    "wifi_line_change": "wifi",
    "esim_fault_replacement": "esim",
    "esim_order_cancellation": "esim",
    "esim_resend": "esim",
    "esim_early_termination": "esim",
    "bigquery_agent": "analytics",
    "rag_index_reload": "system",
}


SCHEDULE_AGENT_DOCS = [
    {
        "domain_id": "analytics",
        "agent_id": "bq_analysis_schedule",
        "agent_type": "schedule",
        "name": "데이터 분석 스케줄",
        "description": "BigQuery 분석 요청을 주기적으로 실행하고 결과를 전송합니다.",
        "parameters": [
            {"name": "query", "type": "str", "required": True, "description": "주기적으로 실행할 분석 요청"},
            {"name": "interval", "type": "str", "required": True, "description": "실행 주기"},
            {"name": "hour", "type": "int", "required": False, "description": "실행 시각"},
            {"name": "minute", "type": "int", "required": False, "description": "실행 분"},
        ],
        "enabled": True,
    },
]


def _parameter_list(parameters: dict | None) -> list[dict]:
    if not parameters:
        return []
    return [
        {"name": name, **spec}
        for name, spec in parameters.items()
    ]


async def upsert_domain_registry() -> None:
    collection = get_collection("boa_domain_registry")
    now = datetime.utcnow()
    for domain in DOMAINS:
        await collection.update_one(
            {"domain_id": domain["domain_id"]},
            {
                "$set": {**domain, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
    await collection.create_index("domain_id", unique=True)
    await collection.create_index([("enabled", 1), ("domain_id", 1)])


async def upsert_agent_registry() -> None:
    collection = get_collection("boa_agent_registry")
    now = datetime.utcnow()
    from src.agent.registry import ALL_REGISTRIES
    for agent_id, domain_id in AGENT_DOMAIN_MAP.items():
        meta = ALL_REGISTRIES[agent_id]
        doc = {
            "domain_id": domain_id,
            "agent_id": agent_id,
            "agent_type": meta["type"],
            "name": meta["display_name"],
            "description": meta["description"],
            "parameters": _parameter_list(meta.get("parameters")),
            "enabled": meta.get("enabled", True),
            "updated_at": now,
        }
        await collection.update_one(
            {"domain_id": domain_id, "agent_id": agent_id},
            {
                "$set": doc,
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    for doc in SCHEDULE_AGENT_DOCS:
        await collection.update_one(
            {"domain_id": doc["domain_id"], "agent_id": doc["agent_id"]},
            {
                "$set": {**doc, "updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    await collection.create_index([("domain_id", 1), ("agent_id", 1)], unique=True)
    await collection.create_index([("domain_id", 1), ("enabled", 1), ("agent_type", 1)])


async def main() -> None:
    await upsert_domain_registry()
    await upsert_agent_registry()
    print("boa_domain_registry and boa_agent_registry initialization complete.")


if __name__ == "__main__":
    asyncio.run(main())
