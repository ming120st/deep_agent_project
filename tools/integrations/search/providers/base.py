from __future__ import annotations

from typing import (
    Any,
    Protocol,
)

from models.search_model import (
    SearchProfile,
    SearchQuery,
)


class SearchProvider(Protocol):
    """
    검색 Provider 공통 인터페이스.

    Tavily, Naver 등 외부 검색 API는
    이 인터페이스를 구현한다.
    """

    @property
    def name(self) -> str:
        ...

    async def search(
        self,
        query: SearchQuery,
        profile: SearchProfile,
    ) -> list[dict[str, Any]]:
        """
        하나의 SearchQuery를 실행하고
        공통 Raw Result 형식으로 반환한다.
        """
        ...