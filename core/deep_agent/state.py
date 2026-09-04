from __future__ import annotations

from typing import Annotated, Any, NotRequired

from deepagents import DeepAgentState


def _keep_latest_non_empty(
    current: Any,
    update: Any,
) -> Any:
    if update is None:
        return current

    if isinstance(update, str) and not update:
        return current

    return update


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