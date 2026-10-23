from __future__ import annotations

from pydantic import BaseModel, Field


class ContentSection(BaseModel):
    """콘텐츠의 개별 섹션."""

    heading: str
    body: str
    tips: list[str] = Field(
        default_factory=list
    )
    source_urls: list[str] = Field(
        default_factory=list
    )

class ArticleContent(BaseModel):
    """HTML 생성 전 확정된 콘텐츠."""

    title: str
    subtitle: str = ""
    category: str = "AI 업무 활용"

    introduction: str

    sections: list[ContentSection] = Field(
        min_length=1
    )

    conclusion: str = ""

    references: list[str] = Field(
        default_factory=list
    )

    youtube_videos: list[str] = Field(
        default_factory=list,
        description="HTML에서 iframe으로 출력할 YouTube URL 목록",
    )