"""
리포트 실행기 (report_runner.py)

[컬렉션]
  boa_members
    - /chat DM(space_type="DM") 수신 시 자동 upsert
    - settings.report_daily: 대시보드 토글로 관리

[발송 로직]
  settings.report_daily=True이고 space_name 있는 구성원에게 DM 발송
"""
import traceback
from datetime import datetime

from src.core.bigquery_client import get_all_report_data
from src.agent.schedule.daily_sales_report.report_builder import generate_report
from src.core.google_chat import send_message
from src.core.mongo_client import boa_members_collection
from src.core.members_manager import upsert_member


# ══════════════════════════════════════════════════════════════════════════════
# 메인 파이프라인
# ══════════════════════════════════════════════════════════════════════════════
async def run_daily_report(target_date: str = None) -> None:
    """
    일일 영업 리포트 생성 및 발송.
    APScheduler에서 매일 08:00 KST 호출.
    """
    started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"[ReportRunner] 일일 리포트 시작: {started_at}")
    print(f"{'='*60}")

    try:
        # ── Step 1. 발송 대상 조회 ────────────────────────────────────
        cursor  = boa_members_collection.find({"settings.report_daily": True})
        targets = await cursor.to_list(length=None)

        if not targets:
            print("[ReportRunner] settings.report_daily=True 구성원 없음 — 종료")
            return

        sendable = [t for t in targets if t.get("space_name")]
        skipped  = [t for t in targets if not t.get("space_name")]

        print(f"[ReportRunner] 대상 {len(targets)}명 "
              f"(발송: {len(sendable)} / skip: {len(skipped)})")
        for t in skipped:
            print(f"  ⚠ skip: {t['email']} — space_name 없음")

        if not sendable:
            print("[ReportRunner] 발송 가능한 구성원 없음 — 종료")
            return

        # ── Step 2. BigQuery 데이터 조회 ──────────────────────────────
        report_data = get_all_report_data(target_date)
        if not report_data.get("summary"):
            print("[ReportRunner] 조회된 데이터 없음 — 종료")
            return

        # ── Step 3. LLM 리포트 생성 ───────────────────────────────────
        report_text = await generate_report(report_data)

        # ── Step 4. 개인 DM 발송 ─────────────────────────────────────
        success_count = 0
        fail_count    = 0

        for member in sendable:
            ok    = await send_message(member["space_name"], report_text)
            label = member.get("name") or member["email"]
            if ok:
                print(f"  ✓ [개인] {label}")
                success_count += 1
            else:
                print(f"  ✗ [개인] {label}")
                fail_count += 1

        # ── Step 5. 스페이스/단체방 발송 ─────────────────────────────
        space_cursor  = boa_spaces_collection.find({"settings.report_daily": True})
        target_spaces = await space_cursor.to_list(length=None)

        for space in target_spaces:
            ok    = await send_message(space["space_name"], report_text)
            label = space.get("display_name") or space["space_name"]
            if ok:
                print(f"  ✓ [스페이스] {label}")
                success_count += 1
            else:
                print(f"  ✗ [스페이스] {label}")
                fail_count += 1

        print(f"\n[ReportRunner] 완료 — "
              f"성공: {success_count} / 실패: {fail_count} / skip: {len(skipped)}")

    except Exception as e:
        print(f"[ReportRunner] 파이프라인 오류: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    import asyncio
    asyncio.run(run_daily_report())