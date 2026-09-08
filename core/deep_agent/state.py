"""
메인 Deep Agent에서 사용하는 공통 State 스키마를 정의한다.

"""
from __future__ import annotations
from typing import Annotated, Any, NotRequired
from deepagents import DeepAgentState

# 업데이트할 state 가 없으면 유지, 있으면 새값으로 교체
def _keep_latest_non_empty(
    current: Any,
    update: Any,
) -> Any:
    if update is None:
        return current

    if isinstance(update, str) and not update:
        return current

    return update

# 기존 dict에 새 값을 병합하고, 중복 key는 최신 값으로 갱신한다.
def _merge_dict(
    current: dict[str, Any] | None,
    update: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        **(current or {}),
        **(update or {}),
    }

class MainDeepAgentState(DeepAgentState):

    session_id: NotRequired[
        Annotated[
            str,
            _keep_latest_non_empty,
        ]
    ]

    current_input: NotRequired[
        Annotated[
            str,
            _keep_latest_non_empty,
        ]
    ]
    user_email: NotRequired[
        Annotated[
            str | None,
            _keep_latest_non_empty,
        ]
    ]

    user_id: NotRequired[
        Annotated[
            str | None,
            _keep_latest_non_empty,
        ]
    ]

    context: NotRequired[
        Annotated[
            dict[str, Any],
            _merge_dict,
        ]
    ]

    subagent_candidates: NotRequired[
        Annotated[
            list[dict[str, Any]],
            _keep_latest_non_empty,
        ]
    ]