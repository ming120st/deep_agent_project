
from __future__ import annotations

import json

from langchain_openai import ChatOpenAI

from models.search_model import SearchResponse
from models.article_content import ArticleContent


class ContentWriter:
    """
    검색 결과와 사용자 작성 요구사항을 기반으로
    인트라넷 게시용 구조화 콘텐츠를 생성한다.
    """

    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-5.6",
            temperature=0,
        ).with_structured_output(
            ArticleContent,
            include_raw=True,
        )

    async def write(
        self,
        response: SearchResponse,
        content_requirements: str = "",
        youtube_urls: list[str] | None = None,
    ) -> ArticleContent:
        """
        검색 결과를 분석하여 ArticleContent를 생성한다.

        Args:
            response:
                Search Agent가 수집한 검색 결과

            content_requirements:
                사용자가 요청한 콘텐츠 주제, 목적,
                필수 포함 항목 및 작성 조건

            youtube_urls:
                Search Agent가 실제 검색한
                YouTube 영상 URL 목록
        """

        if not response.results:
            raise ValueError(
                "검색 결과가 없어 콘텐츠를 생성할 수 없습니다."
            )

        # 검색 결과 정규화
        search_data = [
            {
                "title": item.title,
                "url": item.url,
                "content": item.content,
                "published_date": item.published_date,
            }
            for item in response.results
        ]

        search_json = json.dumps(
            search_data,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )

        # 사용자 요구사항
        requirements = (
            content_requirements.strip()
            if content_requirements
            else ""
        )

        if not requirements:
            # 원래 요청이 전달되지 않은 경우
            # SearchPlan의 request를 보조 정보로 사용
            request = getattr(
                response,
                "request",
                "",
            )

            requirements = str(request or "").strip()

        # 실제 검색으로 확인된 YouTube URL만 전달
        verified_youtube_urls = list(
            dict.fromkeys(
                url.strip()
                for url in (youtube_urls or [])
                if isinstance(url, str)
                and url.strip().startswith(
                    (
                        "https://www.youtube.com/watch?v=",
                        "https://youtu.be/",
                    )
                )
            )
        )

        youtube_json = json.dumps(
            verified_youtube_urls,
            ensure_ascii=False,
        )

        prompt = f"""
당신은 사내 인트라넷의 생성형 AI 콘텐츠를 작성하는
전문 에디터입니다.

독자는 개발자, 영업, 마케팅, 인사, 재무,
경영지원 등 다양한 부서의 직원입니다.

정확한 정보를 쉽고 흥미롭게 전달하며,
카드뉴스형 HTML로 시각화하기 좋은
구조화된 콘텐츠를 작성하세요.

[사용자 요청 및 필수 작성 조건]

{requirements if requirements else "검색 결과를 기반으로 적절한 주제를 선정합니다."}

이 항목은 콘텐츠 주제와 구성의 최우선 기준입니다.

1. 사용자가 지정한 주제와 조사 범위를 유지합니다.
2. 요청한 필수 항목을 빠뜨리지 않습니다.
3. 일반적인 기술 설명보다 요청 주제에
   직접 관련된 사례를 우선합니다.
4. 실제 기업 사례와 일반적인 활용 가능성을
   명확하게 구분합니다.
5. 확인되지 않은 성과 지표를 생성하지 않습니다.
6. 근거가 부족한 항목은 확인되지 않았다고
   명시합니다.
7. 사용자 요청에 없는 주제로 임의 변경하지 않습니다.
8. 검색 결과에 포함된 근거만 사용합니다.

[콘텐츠 유형별 작성 원칙]

사용자 요청에 따라 적절한 구성 방식을 선택합니다.

1. 뉴스 및 기술 동향
   - 핵심 변화와 산업적 의미를 중심으로 작성합니다.
   - 주요 발표 내용과 시점을 구분합니다.

2. 기업 사례 비교 및 분석
   - 실제 도입 기업을 식별합니다.
   - 기업별 적용 기술과 활용 업무를 설명합니다.
   - 공개된 성과 지표가 있다면 출처와 함께 작성합니다.
   - 비교 가능한 정보와 비교 불가능한 정보를 구분합니다.

3. 실무 가이드
   - 적용 목적을 먼저 설명합니다.
   - 실제 수행 가능한 작업을 단계별로 작성합니다.
   - 필요한 도구와 선행 조건이 있다면 제시합니다.
   - 보안, 개인정보, 운영상 주의사항을 설명합니다.
   - 검증되지 않은 구현 효과를 단정하지 않습니다.

4. 복합 요청
   - 뉴스, 기업 사례, 실무 가이드 등
     사용자가 요구한 유형을 함께 반영합니다.
   - 특정 항목을 임의로 생략하지 않습니다.
   - 필요한 경우 섹션을 추가합니다.

[글쓰기 원칙]

1. 제목은 흥미를 유도하되 핵심 내용을 정확히 전달합니다.
2. 첫 문단에서 독자가 알아야 할 핵심 가치나 변화를 제시합니다.
3. 전문용어는 쉬운 말로 설명합니다.
4. 하나의 섹션에서는 하나의 핵심 메시지만 전달합니다.
5. 짧은 문장과 명확한 소제목을 사용합니다.
6. 배경 설명과 유사한 내용을 반복하지 않습니다.
7. 뉴스는 핵심 변화와 의미를, 가이드는 실행 방법을
   중심으로 구성합니다.
8. 실무 예시, 체크리스트, 프롬프트는
   주제에 적합하고 근거가 있을 때만 포함합니다.
9. 자연스럽되 전문성과 신뢰성을 유지합니다.

[콘텐츠 분량]

1. 일반적인 뉴스 요약은 공백 포함
   900~1,400자를 권장합니다.
2. 일반적인 뉴스 요약은 주요 섹션
   3~4개를 권장합니다.
3. 비교 분석이나 상세 실무 가이드 요청에는
   위 분량과 섹션 수 제한을 적용하지 않습니다.
4. introduction은 핵심 내용을 간결하게 전달합니다.
5. 각 섹션은 필요한 설명을 충분히 포함합니다.
6. conclusion은 핵심 결론과 적용 방향으로 마무리합니다.
7. 정확성과 사용자 필수 요구사항을 분량보다 우선합니다.
8. 분량을 채우기 위한 불필요한 내용을 생성하지 않습니다.

[콘텐츠 구성]

ArticleContent 스키마에 맞춰 작성합니다.

- title:
  사용자 요청의 핵심 주제가 드러나는 제목

- subtitle:
  콘텐츠의 핵심 가치를 전달하는 한 문장

- introduction:
  주제의 중요성과 콘텐츠의 목적

- sections:
  사용자 요청에 필요한 핵심 정보 영역

  각 섹션은 명확한 소제목과 설명으로 구성합니다.

  기업 사례 비교가 필요한 경우 기업별 차이를 설명합니다.

  실무 가이드가 필요한 경우 단계와 실행 방법을
  구체적으로 설명합니다.

- conclusion:
  핵심 결론과 실무 적용 방향

[사실성 및 출처]

1. 제공된 검색 결과에서 확인된 사실만 사용합니다.
2. 확인되지 않은 수치, 사례, 인과관계를 생성하지 않습니다.
3. 가상 예시는 실제 사례와 명확히 구분합니다.
4. 주요 주장과 연결되는 출처 URL을 유지합니다.
5. 출처가 없는 주장을 사실처럼 작성하지 않습니다.
6. 기사 발행일을 확인할 수 없다면 최신 뉴스로 단정하지 않습니다.
7. 광고성 자료와 관련성이 낮은 정보는 제외합니다.
8. 원문을 그대로 복제하지 않고 핵심 정보를 재구성합니다.
9. 발표 예정, 시험 운영, 실제 서비스 도입을 구분합니다.
10. 성과 지표는 측정 대상과 기간이 확인된 경우에만
    확정적인 수치로 작성합니다.

[YouTube 영상]

확인된 영상 URL 목록:

{youtube_json}

1. 제공된 URL 외에 새로운 YouTube URL을 생성하지 않습니다.
2. 사용자 요청에 영상 포함 조건이 있고
   URL이 제공됐다면 관련 영상 정보를 포함합니다.
3. 특정 기업의 실제 도입 성과를 입증하지 않는 영상은
   참고용 영상으로 구분합니다.
4. 영상 URL이 없는 경우 임의로 작성하지 않습니다.
5. YouTube 영상 URL은 별도의 youtube_videos 필드에서
   관리하므로 sections나 references에 중복 작성하지 않습니다.
6. 검증되지 않은 영상 제목, 채널명, 게시일을
   임의로 생성하지 않습니다.

[콘텐츠 작성 전 확인]

최종 콘텐츠를 구성하기 전에 다음 사항을 확인합니다.

1. 사용자 요청의 핵심 주제가 유지되었는가?
2. 필수 포함 항목이 반영되었는가?
3. 기업별 사례가 필요한 경우 실제 사례를 사용했는가?
4. 검색 결과에 없는 수치를 생성하지 않았는가?
5. 실무 가이드 요청에 단계별 실행 방법이 있는가?
6. 요청된 YouTube URL을 반영할 수 있는가?
7. 주요 주장에 출처가 연결되어 있는가?

근거가 없는 내용을 만들어 조건을 충족시키지 마세요.

[피해야 할 표현]

- 과장된 제목과 클릭 유도 문구
- 근거 없는 성공 사례 및 성과 수치
- 사용자 요청과 무관한 일반적인 AI 설명
- 동일한 의미를 반복하는 문단
- 불필요한 배경 설명
- 매번 동일한 도입부와 결론
- 억지로 추가한 업무 활용 사례

[검색 데이터]

{search_json}
"""

        result = await self.llm.ainvoke(
            prompt
        )

        # 생성된 ArticleContent
        article = result.get(
            "parsed"
        )

        # 원본 LLM 응답
        raw = result.get(
            "raw"
        )

        # 토큰 사용량
        usage = (
            raw.usage_metadata or {}
            if raw is not None
            else {}
        )

        input_tokens = usage.get(
            "input_tokens",
            0,
        )

        output_tokens = usage.get(
            "output_tokens",
            0,
        )

        total_tokens = usage.get(
            "total_tokens",
            0,
        )

        print(
            "\n========== LLM TOKEN USAGE =========="
        )
        print(
            f"Input Tokens  : {input_tokens:,}"
        )
        print(
            f"Output Tokens : {output_tokens:,}"
        )
        print(
            f"Total Tokens  : {total_tokens:,}"
        )
        print(
            "=====================================\n"
        )

        if article is None:
            raise ValueError(
                "콘텐츠 구조화 출력 실패: "
                f"{result.get('parsing_error')}"
            )

        # LLM 출력과 무관하게 검색된 영상 URL을 보존
        article.youtube_videos = verified_youtube_urls

        return article