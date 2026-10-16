"""LLM transport.

The orchestrator talks to this interface, never to the SDK, so every failure
path — timeout, provider error, refusal, truncation, invalid JSON — can be
tested without spending a token.

The message and tool shapes are the Anthropic ones rather than an invented
neutral format. That is a deliberate leak: a second format would have to be
translated twice and would make the fake less like the real thing, which is the
opposite of what a test double is for.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

# Mapped from the provider's stop_reason. `refusal` and `max_tokens` are
# separate outcomes because they call for different handling: one is a safety
# decision, the other a truncated response.
StopReason = Literal["tool_use", "end_turn", "refusal", "max_tokens", "other"]


@dataclass(frozen=True)
class ToolInvocation:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class LLMTurn:
    stop_reason: StopReason
    text: str | None = None
    tool_invocations: tuple[ToolInvocation, ...] = ()
    # Passed back verbatim on the next request. Opaque on purpose.
    assistant_content: Any = None
    refusal_category: str | None = None


class LLMTimeout(Exception):
    """The provider did not answer within the configured timeout."""


class LLMUnavailable(Exception):
    """The provider returned an error or could not be reached."""


class LLMClient(Protocol):
    @property
    def model_name(self) -> str: ...

    async def create_turn(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        output_schema: dict,
    ) -> LLMTurn: ...


# ------------------------------------------------------------------ the fake ---


@dataclass
class FakeLLMClient:
    """Replays a scripted sequence of turns, or raises a scripted exception.

    Used by every orchestrator test. It records the requests it received so a
    test can assert what the model was actually asked — for example that the
    user's text was wrapped as untrusted data, or that the tool was offered.
    """

    script: list[LLMTurn | Exception] = field(default_factory=list)
    model_name_value: str = "fake-model-1"
    received: list[dict] = field(default_factory=list)

    @property
    def model_name(self) -> str:
        return self.model_name_value

    async def create_turn(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        output_schema: dict,
    ) -> LLMTurn:
        self.received.append(
            {
                "system": system,
                "messages": [dict(message) for message in messages],
                "tools": tools,
                "output_schema": output_schema,
            }
        )
        if not self.script:
            raise AssertionError(
                "FakeLLMClient ran out of scripted turns: the orchestrator made "
                f"{len(self.received)} call(s) but only "
                f"{len(self.received) - 1} were scripted."
            )

        next_turn = self.script.pop(0)
        if isinstance(next_turn, Exception):
            raise next_turn
        return next_turn

    @property
    def call_count(self) -> int:
        return len(self.received)
