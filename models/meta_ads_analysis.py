from __future__ import annotations
from pydantic import BaseModel, Field

class CampaignInfo(BaseModel):
    campaign_id: str
    campaign_name: str
    objective: str | None = None
    optimization_goal: str | None = None
    conversion_event: str | None = None


class CampaignMetrics(BaseModel):
    spend: float | None = None
    purchases: float | None = None
    cost_per_purchase: float | None = None
    purchase_value: float | None = None
    purchase_roas: float | None = None

    impressions: float | None = None
    reach: float | None = None
    frequency: float | None = None

    clicks: float | None = None
    link_click: float | None = None

    ctr: float | None = None
    cpc: float | None = None
    cpm: float | None = None

    cvr: float | None = None
    aov: float | None = None

class AnalysisPeriod(BaseModel):
    start_date: str
    end_date: str

class PerformanceAnalysis(BaseModel):
    overview: str = Field(
        description=(
            "분석 기간과 비교 기간의 핵심 성과를 서술형으로 비교한다. "
            "광고비, 구매수, 구매매출, ROAS, CPA의 실제 수치와 증감률을 "
            "근거로 전체 성과가 어떻게 변화했는지 설명한다. "
            "단순 수치 나열이 아니라 구매 볼륨과 효율의 관계까지 연결하여 "
            "충분한 문장으로 작성한다."
        )
    )

    efficiency: str = Field(
        description=(
            "ROAS와 CPA를 중심으로 광고 효율 변화를 상세히 설명한다. "
            "광고비 변화, 구매수, 구매매출과의 관계를 함께 분석하고 "
            "어떤 지표가 효율 악화 또는 개선에 영향을 주었는지 "
            "실제 조회 수치를 근거로 서술한다."
        )
    )

    volume: str = Field(
        description=(
            "광고비, 구매수, 구매매출 등의 변화로 성과 볼륨을 분석한다. "
            "효율만 좋아지고 볼륨이 감소한 경우 등 지표 간 관계를 "
            "함께 고려하여 서술한다."
        )
    )


class FunnelAnalysis(BaseModel):
    delivery: str = Field(
        description=(
            "노출, 도달, Frequency, CPM을 이용하여 광고 전달 단계의 "
            "변화를 분석한다. 각 지표의 변화가 이후 유입 및 전환 성과와 "
            "어떤 관계가 있는지 연결하여 설명한다."
        )
    )

    traffic: str = Field(
        description=(
            "클릭, CTR, CPC 등의 실제 데이터를 이용해 유입 단계의 "
            "성과 변화를 분석한다. 소재 반응과 유입 비용 변화가 "
            "전체 성과에 미친 영향을 함께 설명한다."
        )
    )

    conversion: str = Field(
        description=(
            "클릭 또는 링크 클릭 대비 구매수와 CVR을 이용하여 "
            "전환 단계의 변화를 분석한다. CVR 하락이 확인되는 경우 "
            "귀속기간 특성을 우선 고려하고, 데이터만으로 확정할 수 없는 "
            "딥링크 또는 결제 문제 등은 가설로 명확히 구분한다."
        )
    )

    value: str = Field(
        description=(
            "구매매출, 구매수, AOV 및 ROAS의 관계를 분석한다. "
            "객단가 변화가 전체 매출과 ROAS 변화에 어느 정도 "
            "연결되어 있는지 실제 수치를 근거로 설명한다."
        )
    )

class SegmentAnalysis(BaseModel):
    adset: str = Field(
        description=(
            "광고세트별 데이터가 조회된 경우 iOS와 Android 등 "
            "광고세트별 광고비, 구매수, CPA, ROAS 등의 차이를 비교하고 "
            "전체 캠페인 성과에 미친 영향을 서술한다. "
            "데이터가 없다면 추정하지 말고 확인할 수 없다고 명시한다."
        )
    )

    creative: str = Field(
        description=(
            "광고 또는 소재별 데이터가 조회된 경우 성과가 좋은 소재와 "
            "악화된 소재를 실제 지표를 근거로 분석한다. "
            "소재 데이터가 없다면 임의로 원인을 만들지 않는다."
        )
    )


class ActionAnalysis(BaseModel):
    campaign: str = Field(
        description=(
            "앞선 분석 결과와 직접 연결되는 캠페인 수준의 조치 사항을 "
            "근거와 함께 구체적으로 서술한다."
        )
    )

    adset: str = Field(
        description=(
            "광고세트 수준의 성과 근거가 확보된 경우에만 "
            "예산, 타겟 등의 조치 방향을 구체적으로 서술한다."
        )
    )

    creative: str = Field(
        description=(
            "소재 수준의 성과 근거가 확보된 경우에만 "
            "유지, 교체, 신규 테스트 등의 조치를 구체적으로 서술한다."
        )
    )

    product: str = Field(
        description=(
            "전환 단계에서 기술 또는 프로덕트 문제가 의심될 충분한 "
            "근거가 있는 경우 딥링크, 결제, 쿠폰, 프로모션 등의 "
            "확인 사항을 구체적으로 서술한다."
        )
    )
class CampaignAnalysis(BaseModel):
    period: AnalysisPeriod = Field(
        description="실제 분석 대상 기간"
    )

    comparison_period: AnalysisPeriod = Field(
        description="성과 비교에 사용한 이전 기간"
    )

    summary: str = Field(
        description=(
            "전체 분석의 핵심 결론을 서술형으로 작성한다."
        )
    )

    performance: PerformanceAnalysis
    funnel: FunnelAnalysis
    segment: SegmentAnalysis

    diagnosis: str

    limitations: list[str] = Field(
        default_factory=list,
    )

    actions: ActionAnalysis


class CampaignAnalysisSnapshot(BaseModel):
    analysis_date: str

    campaign: CampaignInfo

    # 기간 분석 결과
    analysis: CampaignAnalysis