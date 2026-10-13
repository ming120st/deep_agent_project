from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SearchProfile(BaseModel):
    """범용 검색 파이프라인 위에 에이전트별 맞춤 정책(검색 기준 및 임계값)을 설정하는 프로필 모델."""

    preferred_domains: list[str] = Field(default_factory=list)  # 우선적으로 참조할 도메인 목록 (예: ["reuters.com", "korea.kr"])
    excluded_domains: list[str] = Field(default_factory=list)   # 검색 결과에서 배제할 도메인 목록 (예: ["spam.com"])
    official_sources_first: bool = False                        # 공식 출처/기관 도메인 우선 가중치 적용 여부
    max_queries: int = Field(default=4, ge=1, le=5)             # 생성할 수 있는 최대 검색 쿼리 개수 (1~5개)
    freshness_days: int | None = Field(default=None, ge=1)      # 최신성 기준 일수 (None: 제한 없음, N: 최근 N일 이내 문서 대상)
    max_results_per_query: int = Field(default=5, ge=1, le=20)  # 쿼리당 가져올 최대 검색 결과 수 (1~20개)
    min_results: int = Field(default=3, ge=1, le=20)            # 최종 확보해야 하는 최소 검색 결과 수 (미달 시 추가 검색 고려)
    min_quality_score: float = Field(default=0.55, ge=0, le=1)  # 검색 결과 최소 품질 점수 임계값 (0.0~1.0, 미달 시 추가 검색 트리거)
    search_depth: Literal["basic", "advanced", "fast", "ultra-fast"] = "basic"  # Tavily 검색 깊이 ("basic": 기본, "advanced": 심층)
    allow_additional_search: bool = True                        # 결과 품질이 낮거나 부족할 때 1회 추가 보완 검색 허용 여부

    @field_validator("preferred_domains", "excluded_domains")
    @classmethod
    def normalize_domains(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            domain = value.strip().lower()
            domain = domain.removeprefix("https://").removeprefix("http://")
            domain = domain.split("/", 1)[0].removeprefix("www.")
            if domain and domain not in result:
                result.append(domain)
        return result


class SearchQuery(BaseModel):
    """실제 검색 엔진(Tavily)에 전달되는 개별 검색 쿼리 모델."""

    query: str                   # 검색 키워드/질의문
    category: str = "general"    # 쿼리 분류/목적 (예: "general", "official", "follow_up" 등)


class SearchPlan(BaseModel):
    """LLM 또는 휴리스틱 플래너가 수립한 종합 검색 실행 계획."""

    purpose: str                                           # 검색 목적 및 사용자 의도 분석 내용
    keywords: list[str] = Field(default_factory=list)      # 핵심 키워드 리스트
    freshness_important: bool = False                      # 최신 정보 조회가 필수적인지 여부
    official_sources_first: bool = False                   # 공공기관/공식 문서 우선 여부
    scope: Literal["domestic", "international", "both"] = "both"  # 검색 범위 ("domestic": 국내, "international": 해외, "both": 전체)
    perspectives: list[str] = Field(default_factory=list)  # 다각도 조회를 위한 관점 목록
    queries: list[SearchQuery] = Field(min_length=1, max_length=5)  # 실행할 세부 쿼리 목록 (최소 1개, 최대 5개)
    include_domains: list[str] = Field(default_factory=list)  # 검색 대상에 포함/우선할 도메인 목록
    exclude_domains: list[str] = Field(default_factory=list)  # 검색 대상에서 제외할 도메인 목록
    topic: Literal["general", "news", "finance"] = "general"  # Tavily 검색 토픽 카테고리


class SearchResult(BaseModel):
    """검색 결과에서 추출 및 정제된 개별 문서/페이지 정보."""

    title: str                    # 웹페이지 제목
    url: str                      # 원본 웹페이지 URL
    content: str                  # 발췌된 본문 내용 요약
    published_date: str | None = None  # 문서 발행/게시 날짜 (알 수 없는 경우 None)
    source_domain: str            # 출처 도메인 (예: "naver.com", "bbc.com")
    query: str                    # 해당 결과를 가져온 검색 쿼리
    category: str                 # 쿼리 카테고리
    score: float = Field(ge=0, le=1)  # 결과 관련도 및 품질 평가 점수 (0.0 ~ 1.0)


class QualityReport(BaseModel):
    """검색 결과의 완성도와 품질을 평가한 보고서."""

    overall_score: float = Field(ge=0, le=1)     # 전체 검색 결과의 평균 품질 점수 (0.0 ~ 1.0)
    result_count: int                            # 중복 제거 후 확보된 유효 결과 개수
    query_coverage: float = Field(ge=0, le=1)    # 계획된 쿼리들이 유효 결과를 얻은 비율 (0.0 ~ 1.0)
    needs_more_search: bool                      # 점수 미달 또는 결과 부족으로 추가 검색이 필요한지 여부
    reasons: list[str] = Field(default_factory=list)  # 품질 평가 결과 사유 또는 부족한 점 리스트


class SearchResponse(BaseModel):
    """검색 서비스 파이프라인의 최종 반환 객체."""

    request: str                  # 사용자가 전달한 최초 검색 요청 원문
    plan: SearchPlan              # 수립된 검색 계획
    results: list[SearchResult]   # 최종 정렬 및 중복 제거된 검색 결과 리스트
    quality: QualityReport        # 검색 품질 평가 결과
    errors: list[str] = Field(default_factory=list)  # 검색 실행 중 발생한 오류 메시지 리스트
