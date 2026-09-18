from __future__ import annotations

from pydantic import BaseModel


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
    clicks: float | None = None
    ctr: float | None = None
    cpc: float | None = None
    cpm: float | None = None
    frequency: float | None = None


class CampaignAnalysis(BaseModel):
    summary: str
    cause: str
    action: str


class CampaignAnalysisSnapshot(BaseModel):
    analysis_date: str

    campaign: CampaignInfo
    metrics: CampaignMetrics

    analysis: CampaignAnalysis

    analysis_version: str = (
        "campaign-daily-analysis-v1"
    )