"""
check_stale_docs.py — 지식베이스 문서 검토 요청 트리거

[트리거 조건]
  1. status == "published"
  2. last_reviewed_at + review_cycle_days < 지금  (검토 기한 초과)
  3. stale_notified_at 이 없거나, 마지막 알림으로부터 7일 이상 지남  (중복 알림 방지)

[실행 주기]
  매일 오전 9시 (TRIGGER_REGISTRY cron 설정)

[알림 대상]
  owner_id (담당자) → Google Chat DM
  owner_id 가 없으면 created_by 에게 발송
"""

from datetime import datetime, timezone, timedelta

from src.core.mongo_client import boa_knowledge_collection, boa_members_collection
from src.core.google_chat import send_message

# 마지막 알림 후 재알림까지 최소 대기 기간
RE_NOTIFY_DAYS = 7


async def check_stale_docs() -> None:
    now = datetime.now(timezone.utc)
    print(f"\n[StaleDocsTrigger] 검토 기한 체크: {now.strftime('%Y-%m-%d %H:%M')}")

    # ── 조건 1: published 문서 중 last_reviewed_at 이 있는 것 ──────────────────
    # review_cycle_days 는 문서마다 다를 수 있어서 Python에서 필터링
    cursor = boa_knowledge_collection.find({
        "status": "published",
        "last_reviewed_at": {"$exists": True, "$ne": None},
    })
    docs = await cursor.to_list(length=None)

    if not docs:
        print("[StaleDocsTrigger] published 문서 없음")
        return

    stale_docs = []
    for doc in docs:
        review_cycle = doc.get("review_cycle_days", 90)
        last_reviewed = doc.get("last_reviewed_at")

        # timezone-aware 변환
        if last_reviewed and last_reviewed.tzinfo is None:
            last_reviewed = last_reviewed.replace(tzinfo=timezone.utc)

        if not last_reviewed:
            continue

        # 조건 2: 검토 기한 초과
        deadline = last_reviewed + timedelta(days=review_cycle)
        if now < deadline:
            continue

        # 조건 3: 중복 알림 방지 (7일 이내 이미 알렸으면 스킵)
        last_notified = doc.get("stale_notified_at")
        if last_notified:
            if last_notified.tzinfo is None:
                last_notified = last_notified.replace(tzinfo=timezone.utc)
            if now - last_notified < timedelta(days=RE_NOTIFY_DAYS):
                continue

        stale_docs.append(doc)

    if not stale_docs:
        print("[StaleDocsTrigger] 검토 필요 문서 없음")
        return

    print(f"[StaleDocsTrigger] 검토 필요 문서 {len(stale_docs)}건 발견")

    for doc in stale_docs:
        # 알림 대상: owner_id → 없으면 created_by
        target_email = doc.get("owner_id") or doc.get("created_by")
        if not target_email:
            print(f"  ✗ 담당자 없음: {doc.get('title')}")
            continue

        # 담당자 space_name 조회
        member = await boa_members_collection.find_one({"user_id": target_email})
        if not member:
            # user_id 로 못 찾으면 email 로 재시도
            member = await boa_members_collection.find_one({"email": target_email})
        if not member or not member.get("space_name"):
            print(f"  ✗ space_name 없음: {target_email}")
            continue

        # 경과일 계산
        last_reviewed = doc.get("last_reviewed_at")
        if last_reviewed and last_reviewed.tzinfo is None:
            last_reviewed = last_reviewed.replace(tzinfo=timezone.utc)
        days_overdue = (now - last_reviewed).days if last_reviewed else "?"

        # 유효기간 만료 여부
        valid_until = doc.get("valid_until")
        expiry_line = ""
        if valid_until:
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=timezone.utc)
            days_to_expire = (valid_until - now).days
            if days_to_expire < 0:
                expiry_line = f"\n⚠️ 유효기간이 {abs(days_to_expire)}일 전에 *만료*되었습니다."
            elif days_to_expire <= 7:
                expiry_line = f"\n⚠️ 유효기간이 *{days_to_expire}일 후* 만료됩니다."

        service_map = {
            "wifi": "📡 와이파이 도시락",
            "usim": "💳 유심",
            "esim": "📲 이심",
            "insurance": "🛡️ 보험",
            "other": "📁 기타",
        }
        service_label = service_map.get(doc.get("service", ""), doc.get("service", ""))

        msg = (
            f"📋 *[지식베이스 검토 요청]*\n"
            f"담당 문서의 검토 기한이 지났습니다. 내용을 확인하고 최신화해주세요.\n\n"
            f"• 문서: *{doc.get('title', '(제목 없음)')}*\n"
            f"• 서비스: {service_label} > {doc.get('category', '')}\n"
            f"• 마지막 검토: {days_overdue}일 전\n"
            f"• 검토 주기: {doc.get('review_cycle_days', 90)}일{expiry_line}\n\n"
            f"👉 대시보드에서 확인: /knowledge"
        )

        ok = await send_message(member["space_name"], msg)

        if ok:
            # 알림 발송 시각 기록 (중복 방지용)
            await boa_knowledge_collection.update_one(
                {"_id": doc["_id"]},
                {"$set": {"stale_notified_at": now}}
            )
            print(f"  ✓ 알림 발송: {target_email} → {doc.get('title')}")
        else:
            print(f"  ✗ 발송 실패: {target_email} → {doc.get('title')}")