"""The real provider adapter.

Kept in its own module so the orchestrator never imports the SDK and the test
suite never needs an API key.

Verified against `anthropic` 1.15.0's surface: `messages.create` accepts
`output_config` with `effort` and `format`, and tool definitions accept
`strict`. Whether the server accepts this exact `output_config.format` schema
for the configured model is what the authenticated smoke test confirms — until
that has run, this module is unverified against a live endpoint and the README
says so.
"""

import logging

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
)

from app.services.ai.client import (
    LLMTimeout,
    LLMTurn,
    LLMUnavailable,
    StopReason,
    ToolInvocation,
)

logger = logging.getLogger(__name__)

# The provider's stop_reason values we care about. Anything else is reported as
# `other` so the orchestrator treats it as an unusable response rather than
# silently assuming the turn ended normally.
_STOP_REASONS: dict[str, StopReason] = {
    "tool_use": "tool_use",
    "end_turn": "end_turn",
    "refusal": "refusal",
    "max_tokens": "max_tokens",
}


class AnthropicLLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float,
        max_retries: int,
        effort: str,
        max_output_tokens: int,
    ) -> None:
        # The SDK defaults to a 600s read timeout and 2 retries, which would let
        # one analysis hold a background task for 30 minutes. Both are overridden.
        self._client = AsyncAnthropic(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )
        self._model = model
        self._effort = effort
        self._max_output_tokens = max_output_tokens

    @property
    def model_name(self) -> str:
        return self._model

    async def create_turn(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        output_schema: dict,
    ) -> LLMTurn:
        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=self._max_output_tokens,
                system=system,
                messages=messages,
                tools=tools,
                # Forced tool choice returns 400 on current models (ADR-005):
                # the tool is offered and the prompt asks for it.
                tool_choice={"type": "auto"},
                output_config={
                    "effort": self._effort,
                    "format": {"type": "json_schema", "schema": output_schema},
                },
            )
        except APITimeoutError as error:
            raise LLMTimeout(str(type(error).__name__)) from error
        except (APIStatusError, APIConnectionError) as error:
            # The message can echo request content, so only the type is logged.
            logger.warning("LLM provider error (%s)", type(error).__name__)
            raise LLMUnavailable(type(error).__name__) from error

        return self._to_turn(message)

    @staticmethod
    def _to_turn(message) -> LLMTurn:
        stop_reason = _STOP_REASONS.get(message.stop_reason or "", "other")

        # stop_details is populated only for refusals, so it is read defensively.
        refusal_category = None
        details = getattr(message, "stop_details", None)
        if stop_reason == "refusal" and details is not None:
            refusal_category = getattr(details, "category", None)

        text_blocks = [
            block.text for block in message.content if getattr(block, "type", None) == "text"
        ]
        invocations = tuple(
            ToolInvocation(
                id=block.id,
                name=block.name,
                # Always parsed, never string-matched: escaping in tool inputs
                # varies between models.
                arguments=dict(block.input) if isinstance(block.input, dict) else {},
            )
            for block in message.content
            if getattr(block, "type", None) == "tool_use"
        )

        return LLMTurn(
            stop_reason=stop_reason,
            text="\n".join(text_blocks) if text_blocks else None,
            tool_invocations=invocations,
            # Handed back unchanged on the next request.
            assistant_content=message.content,
            refusal_category=refusal_category,
        )
