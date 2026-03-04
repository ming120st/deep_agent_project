"""
trigger_runner.py — 트리거 기반 에이전트 예시

[트리거란?]
  스케줄: 시간이 되면 무조건 실행  (cron)
  트리거: 주기적으로 조건 체크 → 조건 맞을 때만 실행

[예시: 기한 초과 태스크 알림]
  - 10분마다 조건 체크
  - 조건: due_date 지났고 status != "done"이고 아직 알림 안 보낸 것
  - 실행: 담당자에게 Google Chat DM 발송
"""
from datetime import datetime, timezone
from src.core.mongo_client import db
from src.proactive.dm_sender import send_dm_to_space

boa_tasks_collection   = db["boa_tasks"]
boa_members_collection = db["boa_members"]


async def check_overdue_tasks() -> None:
    """
    [트리거 조건]
      1. due_date < 지금
      2. status != "done"
      3. overdue_notified != True  ← 중복 발송 방지 핵심

    APScheduler에서 10분마다 호출.
    """
    now = datetime.now(timezone.utc)
    print(f"\n[OverdueTrigger] 조건 체크: {now.strftime('%H:%M')}")

    # 조건에 맞는 태스크 조회
    cursor = boa_tasks_collection.find({
        "due_date":         {"$lt": now},
        "status":           {"$ne": "done"},
        "overdue_notified": {"$ne": True},   # 아직 알림 안 보낸 것만
    })
    overdue_tasks = await cursor.to_list(length=None)

    if not overdue_tasks:
        print("[OverdueTrigger] 해당 태스크 없음")
        return

    print(f"[OverdueTrigger] 초과 태스크 {len(overdue_tasks)}건 발견")

    for task in overdue_tasks:
        assignee_email = task.get("assignee_email")
        if not assignee_email:
            continue

        # 담당자 space_name 조회
        member = await boa_members_collection.find_one({"email": assignee_email})
        if not member or not member.get("space_name"):
            continue

        # 메시지 구성
        due_str = task["due_date"].strftime("%m/%d %H:%M")
        msg = (
            f"⏰ *기한 초과 알림*\n"
            f"태스크: {task.get('title', '(제목 없음)')}\n"
            f"기한: {due_str}\n"
            f"현재 상태: {task.get('status', '-')}\n\n"
            f"처리가 필요합니다!"
        )

        ok = send_dm_to_space(member["space_name"], msg)

        if ok:
            # ★ 핵심: 알림 보냈으면 플래그 세우기 (중복 방지)
            await boa_tasks_collection.update_one(
                {"_id": task["_id"]},
                {"$set": {"overdue_notified": True, "notified_at": now}}
            )
            print(f"  ✓ 발송: {assignee_email} → {task.get('title')}")
        else:
            print(f"  ✗ 실패: {assignee_email}")