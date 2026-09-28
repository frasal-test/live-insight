"""The minimal interface to a model: chat + tool calling. No framework.

The agent loop speaks only these types; every adapter translates them into the provider's format. An assistant
message keeps the provider's original content in `raw`, so the same adapter can send it back unchanged (needed, for
example, for Anthropic's thinking blocks).
"""
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    error: str | None = None            # arguments that are not JSON: the error is sent back, the model retries


@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None     # for role="tool"
    is_error: bool = False              # for role="tool"
    raw: Any = None                     # the provider's original content (assistant only)
    provider: str | None = None


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict                    # JSON Schema of the input


@dataclass
class Usage:
    input_tokens: int = 0               # not from the cache
    cached_input_tokens: int = 0        # read from the cache
    cache_write_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, o):
        return Usage(self.input_tokens + o.input_tokens, self.cached_input_tokens + o.cached_input_tokens,
                     self.cache_write_tokens + o.cache_write_tokens, self.output_tokens + o.output_tokens)


@dataclass(frozen=True)
class Price:
    """Price per million tokens, in the price list's currency (OpenAI and Anthropic in dollars, OCI in euro)."""
    input: float
    cached_input: float
    output: float
    cache_write: float | None = None    # default: as the input
    currency: str = "USD"               # "USD" or "EUR": no invented exchange rates, the price list's currency is shown

    def format(self, amount: float) -> str:
        return f"{amount:.4f} €" if self.currency == "EUR" else f"${amount:.4f}"

    def cost(self, u: Usage) -> float:
        write = self.cache_write if self.cache_write is not None else self.input
        return (u.input_tokens * self.input + u.cached_input_tokens * self.cached_input
                + u.cache_write_tokens * write + u.output_tokens * self.output) / 1e6


@dataclass
class Reply:
    message: Message
    usage: Usage
    stop: str                           # "end", "tool_calls", "length", "refusal", other


class ChatModel(Protocol):
    provider: str
    model: str
    price: Price | None

    def chat(self, system: str, messages: list[Message], tools: list[Tool]) -> Reply: ...
