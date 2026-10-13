# Tavily 범용 검색 파이프라인

BOA의 공통 웹 검색 계층입니다.

```text
Agent -> search_web Tool -> SearchService -> Tavily
                         -> LLM SearchPlanner
```

`SearchService`가 계획 생성, Query 병렬 실행, URL/유사 제목 중복 제거,
품질 점수 계산과 한 차례의 보완 검색을 담당합니다. 기본 `web_search` Agent는
`search_web` Tool을 사용하며, 업무 Agent는 별도 검색 구현 없이 Profile을 바인딩한
Tool을 직접 주입할 수 있습니다.

```python
from tools.integrations.tavily_search import SearchProfile, create_search_tool

news_search = create_search_tool(
    SearchProfile(
        preferred_domains=["reuters.com", "korea.kr"],
        excluded_domains=["example.invalid"],
        official_sources_first=True,
        max_queries=4,
        freshness_days=7,
        max_results_per_query=5,
    ),
    name="search_recent_news",
)q
```

생성한 `news_search`를 기존 Agent의 `tools` 목록에 추가하면 됩니다. 런타임에는
`TAVILY_API_KEY`가 필요합니다. 검색 계획용 LLM을 사용할 수 없으면 휴리스틱 계획으로
자동 전환하며, Tavily Query 하나가 실패해도 다른 Query 결과는 유지합니다.