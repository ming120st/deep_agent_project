from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
)


def find_previous_day_snapshot(
    snapshots: list[dict],
    latest_date: str,
) -> dict | None:
    """
    최신 분석일 기준 정확히 하루 전 날짜의
    Snapshot을 찾아 반환한다.

    해당 날짜의 Snapshot이 없으면 None을 반환한다.
    """

    target_date = (
        datetime.strptime(
            latest_date,
            "%Y-%m-%d",
        ).date()
        - timedelta(days=1)
    ).isoformat()

    return next(
        (
            snapshot
            for snapshot in snapshots
            if snapshot["analysis_date"]
            == target_date
        ),
        None,
    )


def calculate_change_rate(
    current: float | int | None,
    previous: float | int | None,
) -> float | None:
    """
    이전 값 대비 현재 값의 변화율을 계산한다.

    값이 없거나 이전 값이 0이면
    변화율을 계산하지 않고 None을 반환한다.
    """

    if current is None or previous is None:
        return None

    if previous == 0:
        return None

    return (
        current - previous
    ) / previous