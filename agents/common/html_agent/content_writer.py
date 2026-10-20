from __future__ import annotations

from langchain_openai import ChatOpenAI

from models.search_model import SearchResponse
from models.article_content import ArticleContent
import json

class ContentWriter:
    """검색 결과를 기반으로 콘텐츠를 작성한다."""

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
    ) -> ArticleContent:
        """
        검색 결과를 분석해 구조화된 콘텐츠를 생성한다.
        """
        search_data = [
            {
                "title": item.title,
                "url": item.url,
                "content": item.content,
                "published_date": (
                    item.published_date
                ),
            }
            for item in response.results
        ]

        search_json = json.dumps(
            search_data,
            ensure_ascii=False,
            separators=(",", ":"),
        )

        if not response.results:
            raise ValueError(
                "검색 결과가 없어 콘텐츠를 생성할 수 없습니다."
            )

        
        prompt = f"""
        당신은 사내 인트라넷에 게시할 AI 업무 활용 콘텐츠를
        작성하는 전문 에디터입니다.

        독자는 개발자뿐 아니라 영업, 마케팅, 인사, 재무,
        경영지원 등 다양한 부서의 직원입니다.

        목표는 정확한 정보를 전달하면서도
        직원들이 제목을 클릭하고 끝까지 읽으며,
        실제 업무에 적용해 보고 싶도록 만드는 것입니다.

        [글쓰기 스타일]

        1. 제목은 독자의 호기심을 자극하고
        읽었을 때 얻을 수 있는 가치를 드러냅니다.
        2. 도입부는 질문, 공감할 만한 업무 상황,
        흥미로운 사실 중 주제에 적합한 방식으로 시작합니다.
        3. 첫 문단에서 이 글이 독자에게 왜 필요한지 설명합니다.
        4. 전문용어는 쉬운 말로 설명하고,
        필요하면 익숙한 업무 상황에 비유합니다.
        5. 단순한 정보 나열보다 문제 → 해결 방법 →
        실무 적용의 흐름으로 구성합니다.
        6. 각 섹션의 제목도 핵심 내용을 전달하면서
        다음 내용을 읽고 싶게 작성합니다.
        7. 문장은 짧고 명확하게 작성합니다.
        8. 설명과 실무 팁을 적절히 섞어 지루하지 않게 합니다.
        9. 독자가 바로 활용할 수 있는 예시,
        체크리스트 또는 프롬프트를 포함합니다.
        10. 친근하고 자연스럽지만
            전문성과 신뢰감을 유지하는 문체를 사용합니다.

        [콘텐츠 구성]

        - title:
        관심을 끌면서도 내용을 정확히 나타내는 제목

        - subtitle:
        이 글을 읽었을 때 얻을 수 있는 핵심 가치

        - introduction:
        흥미로운 질문이나 현실적인 업무 상황으로 시작하고,
        독자가 계속 읽어야 하는 이유를 설명

        - sections:
        논리적인 흐름으로 구성하고,
        각 섹션마다 이해하기 쉬운 설명과
        실무에 적용할 수 있는 팁을 포함

        - conclusion:
        핵심 메시지를 간결하게 정리하고,
        독자가 바로 시도할 수 있는 행동을 제안

        [사실성과 출처]

        1. 제공된 검색 결과에서 확인할 수 있는 사실만 사용합니다.
        2. 검색 결과에 없는 통계, 사례, 성과 수치를 만들어내지 않습니다.
        3. 가상의 업무 상황은 설명용 예시임을 명확히 합니다.
        4. 확인되지 않은 사실을 실제 사례처럼 표현하지 않습니다.
        5. 각 섹션에 근거가 되는 출처 URL을 포함합니다.
        6. 광고성 콘텐츠와 관련성이 낮은 자료는 제외합니다.
        7. 클릭을 유도하기 위해 사실을 과장하지 않습니다.
        8. 발행일이 확인되지 않은 자료를 최신 뉴스로 단정하지 않습니다.

        [피해야 할 스타일]

        - 논문이나 기술 보고서처럼 딱딱한 서술
        - 전문용어만 길게 나열하는 설명
        - 모든 섹션에서 비슷한 문장을 반복하는 구성
        - 과장된 제목이나 근거 없는 주장
        - 불필요하게 긴 서론
        - 매번 똑같은 질문형 제목과 도입부

        [검색 데이터]
        {search_json}
        """


        result = await self.llm.ainvoke(prompt)

        # 생성된 ArticleContent
        article = result["parsed"]

        # 원본 LLM 응답
        raw = result["raw"]

        # 토큰 사용량
        usage = raw.usage_metadata or {}

        input_tokens = usage.get(
            "input_tokens", 0
        )

        output_tokens = usage.get(
            "output_tokens", 0
        )

        total_tokens = usage.get(
            "total_tokens", 0
        )

        print("\n========== LLM TOKEN USAGE ==========")
        print(f"Input Tokens  : {input_tokens:,}")
        print(f"Output Tokens : {output_tokens:,}")
        print(f"Total Tokens  : {total_tokens:,}")
        print("=====================================\n")

        if article is None:
            raise ValueError(
                f"콘텐츠 구조화 출력 실패: {result['parsing_error']}"
            )

        return article

    