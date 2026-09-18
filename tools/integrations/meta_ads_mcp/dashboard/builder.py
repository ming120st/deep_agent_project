from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.chart import (
    LineChart,
    Reference,
)
from openpyxl.chart.axis import (
    DateAxis,
)
from openpyxl.styles import (
    Alignment,
    Font,
    PatternFill,
)

from tools.integrations.meta_ads_mcp.dashboard.comparison import (
    calculate_change_rate,
    find_previous_day_snapshot,
)


OUTPUT_DIR = (
    Path(".runtime")
    / "reports"
    / "meta_ads"
)


def _to_number(
    value: Any,
) -> int | float | None:
    if value is None:
        return None

    if isinstance(
        value,
        bool,
    ):
        return int(value)

    if isinstance(
        value,
        (int, float),
    ):
        return value

    if isinstance(
        value,
        Decimal,
    ):
        return float(value)

    if hasattr(
        value,
        "to_decimal",
    ):
        try:
            return float(
                value.to_decimal()
            )
        except Exception:
            pass

    try:
        return float(value)

    except (
        TypeError,
        ValueError,
    ):
        return None


def _style_header(
    cell,
) -> None:
    cell.fill = PatternFill(
        fill_type="solid",
        fgColor="1F4E78",
    )

    cell.font = Font(
        color="FFFFFF",
        bold=True,
    )

    cell.alignment = Alignment(
        horizontal="center",
        vertical="center",
    )


def _style_section_title(
    cell,
) -> None:
    cell.fill = PatternFill(
        fill_type="solid",
        fgColor="D9EAF7",
    )

    cell.font = Font(
        bold=True,
    )


def _set_column_widths(
    sheet,
    widths: dict[str, float],
) -> None:
    for column, width in widths.items():
        sheet.column_dimensions[
            column
        ].width = width


def _create_daily_data_sheet(
    workbook: Workbook,
    snapshots: list[dict],
):
    sheet = workbook.create_sheet(
        "Daily_Data"
    )

    headers = [
        "날짜",
        "광고비",
        "구매",
        "CPA",
        "구매 전환값",
        "ROAS",
        "노출",
        "도달",
        "클릭",
        "CTR",
        "CPC",
        "CPM",
        "빈도",
    ]

    sheet.append(
        headers
    )

    for cell in sheet[1]:
        _style_header(
            cell
        )

    for snapshot in snapshots:
        metrics = snapshot.get(
            "metrics",
            {},
        )

        analysis_date = snapshot.get(
            "analysis_date"
        )

        if analysis_date:
            analysis_date = (
                datetime.strptime(
                    analysis_date,
                    "%Y-%m-%d",
                )
            )

        sheet.append(
            [
                analysis_date,
                _to_number(
                    metrics.get(
                        "spend"
                    )
                ),
                _to_number(
                    metrics.get(
                        "purchases"
                    )
                ),
                _to_number(
                    metrics.get(
                        "cost_per_purchase"
                    )
                ),
                _to_number(
                    metrics.get(
                        "purchase_value"
                    )
                ),
                _to_number(
                    metrics.get(
                        "purchase_roas"
                    )
                ),
                _to_number(
                    metrics.get(
                        "impressions"
                    )
                ),
                _to_number(
                    metrics.get(
                        "reach"
                    )
                ),
                _to_number(
                    metrics.get(
                        "clicks"
                    )
                ),
                _to_number(
                    metrics.get(
                        "ctr"
                    )
                ),
                _to_number(
                    metrics.get(
                        "cpc"
                    )
                ),
                _to_number(
                    metrics.get(
                        "cpm"
                    )
                ),
                _to_number(
                    metrics.get(
                        "frequency"
                    )
                ),
            ]
        )

    max_row = sheet.max_row

    for row in range(
        2,
        max_row + 1,
    ):
        # 날짜
        sheet.cell(
            row=row,
            column=1,
        ).number_format = "m/d"

        # 정수 / 금액
        for col in [
            2,
            3,
            4,
            5,
            7,
            8,
            9,
            11,
            12,
        ]:
            sheet.cell(
                row=row,
                column=col,
            ).number_format = "#,##0"

        # ROAS
        sheet.cell(
            row=row,
            column=6,
        ).number_format = "0.00"

        # CTR
        # DB의 0.35는 0.35% 의미
        sheet.cell(
            row=row,
            column=10,
        ).number_format = '0.00"%"'

        # 빈도
        sheet.cell(
            row=row,
            column=13,
        ).number_format = "0.00"

    sheet.freeze_panes = "A2"

    sheet.auto_filter.ref = (
        f"A1:M{max_row}"
    )

    _set_column_widths(
        sheet,
        {
            "A": 14,
            "B": 14,
            "C": 10,
            "D": 14,
            "E": 16,
            "F": 12,
            "G": 12,
            "H": 12,
            "I": 10,
            "J": 10,
            "K": 14,
            "L": 14,
            "M": 12,
        },
    )

    return sheet


def _configure_date_axis(
    chart: LineChart,
) -> None:
    date_axis = DateAxis(
        axId=10,
        crossAx=100,
    )

    date_axis.axPos = "b"
    date_axis.title = "날짜"
    date_axis.number_format = "m/d"
    date_axis.majorTimeUnit = "days"
    date_axis.tickLblPos = "low"

    chart.x_axis = date_axis

    chart.y_axis.axId = 100
    chart.y_axis.crossAx = 10


def _create_dashboard_sheet(
    workbook: Workbook,
    snapshots: list[dict],
    daily_sheet,
) -> None:
    dashboard = workbook.active
    dashboard.title = "Dashboard"

    latest = snapshots[-1]

    latest_date = latest.get(
        "analysis_date",
        "",
    )

    previous = (
        find_previous_day_snapshot(
            snapshots,
            latest_date,
        )
    )

    campaign = latest.get(
        "campaign",
        {},
    )

    metrics = latest.get(
        "metrics",
        {},
    )

    analysis = latest.get(
        "analysis",
        {},
    )

    # -----------------------------
    # 제목
    # -----------------------------
    dashboard.merge_cells(
        "A1:H2"
    )

    title = dashboard[
        "A1"
    ]

    title.value = (
        "Meta Ads Campaign Daily Dashboard"
    )

    title.fill = PatternFill(
        fill_type="solid",
        fgColor="1F4E78",
    )

    title.font = Font(
        color="FFFFFF",
        bold=True,
        size=18,
    )

    title.alignment = Alignment(
        horizontal="center",
        vertical="center",
    )

    # -----------------------------
    # 캠페인 정보
    # -----------------------------
    info_rows = [
        (
            "캠페인",
            campaign.get(
                "campaign_name",
                "",
            ),
        ),
        (
            "분석일",
            latest_date,
        ),
        (
            "목적",
            campaign.get(
                "objective",
                "",
            ),
        ),
        (
            "전환 이벤트",
            campaign.get(
                "conversion_event",
                "",
            ),
        ),
    ]

    for row, (
        label,
        value,
    ) in enumerate(
        info_rows,
        start=4,
    ):
        dashboard.cell(
            row=row,
            column=1,
            value=label,
        )

        dashboard.cell(
            row=row,
            column=2,
            value=value,
        )

        _style_section_title(
            dashboard.cell(
                row=row,
                column=1,
            )
        )

    # -----------------------------
    # KPI
    # -----------------------------
    kpi_headers = [
        "광고비",
        "구매",
        "CPA",
        "구매 전환값",
        "ROAS",
        "CTR",
    ]

    kpi_values = [
        _to_number(
            metrics.get(
                "spend"
            )
        ),
        _to_number(
            metrics.get(
                "purchases"
            )
        ),
        _to_number(
            metrics.get(
                "cost_per_purchase"
            )
        ),
        _to_number(
            metrics.get(
                "purchase_value"
            )
        ),
        _to_number(
            metrics.get(
                "purchase_roas"
            )
        ),
        _to_number(
            metrics.get(
                "ctr"
            )
        ),
    ]

    for col, header in enumerate(
        kpi_headers,
        start=1,
    ):
        cell = dashboard.cell(
            row=10,
            column=col,
            value=header,
        )

        _style_header(
            cell
        )

    for col, value in enumerate(
        kpi_values,
        start=1,
    ):
        cell = dashboard.cell(
            row=11,
            column=col,
            value=value,
        )

        cell.font = Font(
            bold=True,
            size=13,
        )

        cell.alignment = Alignment(
            horizontal="center",
        )

    for col in [
        1,
        2,
        3,
        4,
    ]:
        dashboard.cell(
            row=11,
            column=col,
        ).number_format = "#,##0"

    dashboard.cell(
        row=11,
        column=5,
    ).number_format = "0.00"

    dashboard.cell(
        row=11,
        column=6,
    ).number_format = '0.00"%"'

    # -----------------------------
    # 전일 대비
    # -----------------------------
    dashboard[
        "H4"
    ] = "전일 대비"

    _style_section_title(
        dashboard[
            "H4"
        ]
    )

    if previous:
        previous_metrics = previous.get(
            "metrics",
            {},
        )

        compare_items = [
            (
                "광고비",
                "spend",
            ),
            (
                "구매",
                "purchases",
            ),
            (
                "CPA",
                "cost_per_purchase",
            ),
            (
                "ROAS",
                "purchase_roas",
            ),
            (
                "CTR",
                "ctr",
            ),
        ]

        for row, (
            label,
            key,
        ) in enumerate(
            compare_items,
            start=5,
        ):
            current_num = _to_number(
                metrics.get(
                    key
                )
            )

            previous_num = _to_number(
                previous_metrics.get(
                    key
                )
            )

            change_rate = (
                calculate_change_rate(
                    current_num,
                    previous_num,
                )
            )

            dashboard.cell(
                row=row,
                column=8,
                value=label,
            )

            dashboard.cell(
                row=row,
                column=9,
                value=change_rate,
            ).number_format = (
                '+0.0%;-0.0%;0.0%'
            )

    else:
        dashboard[
            "H5"
        ] = "전일 데이터 없음"

    # -----------------------------
    # 분석 텍스트
    # -----------------------------
    analysis_sections = [
        (
            14,
            "요약",
            analysis.get(
                "summary",
                "분석 데이터 없음",
            ),
        ),
        (
            19,
            "변화 원인",
            analysis.get(
                "cause",
                "분석 데이터 없음",
            ),
        ),
        (
            24,
            "조치",
            analysis.get(
                "action",
                "분석 데이터 없음",
            ),
        ),
    ]

    for (
        title_row,
        section_title,
        content,
    ) in analysis_sections:
        title_cell = dashboard.cell(
            row=title_row,
            column=1,
            value=section_title,
        )

        _style_section_title(
            title_cell
        )

        dashboard.merge_cells(
            start_row=title_row + 1,
            start_column=1,
            end_row=title_row + 3,
            end_column=9,
        )

        content_cell = dashboard.cell(
            row=title_row + 1,
            column=1,
            value=content,
        )

        content_cell.alignment = Alignment(
            wrap_text=True,
            vertical="top",
        )

    # -----------------------------
    # 차트
    # -----------------------------
    if daily_sheet.max_row >= 3:
        dates = Reference(
            daily_sheet,
            min_col=1,
            min_row=2,
            max_row=daily_sheet.max_row,
        )

        # =========================
        # ROAS 차트
        # =========================
        roas_chart = LineChart()

        roas_chart.title = (
            "ROAS 추세"
        )

        roas_chart.y_axis.title = (
            "ROAS"
        )

        _configure_date_axis(
            roas_chart
        )

        roas_chart.height = 8
        roas_chart.width = 14

        roas_data = Reference(
            daily_sheet,
            min_col=6,
            min_row=1,
            max_row=daily_sheet.max_row,
        )

        roas_chart.add_data(
            roas_data,
            titles_from_data=True,
        )

        roas_chart.set_categories(
            dates
        )

        roas_chart.legend = None

        dashboard.add_chart(
            roas_chart,
            "K4",
        )

        # =========================
        # CPA 차트
        # =========================
        cpa_chart = LineChart()

        cpa_chart.title = (
            "CPA 추세"
        )

        cpa_chart.y_axis.title = (
            "CPA"
        )

        _configure_date_axis(
            cpa_chart
        )

        cpa_chart.height = 8
        cpa_chart.width = 14

        cpa_data = Reference(
            daily_sheet,
            min_col=4,
            min_row=1,
            max_row=daily_sheet.max_row,
        )

        cpa_chart.add_data(
            cpa_data,
            titles_from_data=True,
        )

        cpa_chart.set_categories(
            dates
        )

        cpa_chart.legend = None

        dashboard.add_chart(
            cpa_chart,
            "K20",
        )

    # -----------------------------
    # Dashboard Layout
    # -----------------------------
    _set_column_widths(
        dashboard,
        {
            "A": 18,
            "B": 22,
            "C": 16,
            "D": 16,
            "E": 16,
            "F": 16,
            "G": 8,
            "H": 16,
            "I": 14,
        },
    )

    dashboard.freeze_panes = (
        "A4"
    )


def build_meta_ads_dashboard(
    snapshots: list[dict],
) -> str:
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    workbook = Workbook()

    daily_sheet = (
        _create_daily_data_sheet(
            workbook=workbook,
            snapshots=snapshots,
        )
    )

    _create_dashboard_sheet(
        workbook=workbook,
        snapshots=snapshots,
        daily_sheet=daily_sheet,
    )

    latest_date = snapshots[-1].get(
        "analysis_date",
        "unknown",
    )

    output_file = (
        OUTPUT_DIR
        / (
            f"Meta_Ads_Daily_Report_"
            f"{latest_date}.xlsx"
        )
    )

    workbook.save(
        output_file
    )

    return str(
        output_file
    )