"""Anthropic adapter (Messages API), manual loop.

- system and tool definitions are stable: the prefix is cached (cache_control on the last system block, plus the
  automatic cache on the last message block);
- assistant messages are sent back with their original content (`raw`), thinking included;
- the tool results of one turn go in ONE user message;
- `fallbacks: "default"`: if the classifiers refuse the request, the API runs it again on another model.
"""
import anthropic

from liveinsight.llm.base import Message, Reply, Tool, ToolCall, Usage

STOP = {"end_turn": "end", "tool_use": "tool_calls", "max_tokens": "length", "refusal": "refusal"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicChat:
    def __init__(self, model, api_key=None, price=None, effort=None, max_tokens=16000, fallbacks=True):
        self.provider, self.model, self.price = "anthropic", model, price
        self.effort, self.max_tokens, self.fallbacks = effort, max_tokens, fallbacks
        self.client = anthropic.Anthropic(api_key=api_key)

    def _messages(self, messages):
        out = []
        for m in messages:
            if m.role == "tool":
                block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
                if m.is_error:
                    block["is_error"] = True
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                    out[-1]["content"].append(block)        # all the results of the turn together
                else:
                    out.append({"role": "user", "content": [block]})
            elif m.role == "assistant":
                if m.provider == self.provider and m.raw is not None:
                    content = m.raw
                else:                                        # conversation started with another provider
                    content = ([{"type": "text", "text": m.content}] if m.content else []) + \
                              [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments} for c in m.tool_calls]
                out.append({"role": "assistant", "content": content})
            else:
                out.append({"role": "user", "content": m.content})
        return out

    def chat(self, system, messages, tools: list[Tool]):
        kwargs = {}
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        if self.fallbacks:
            kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")
        r = self.client.beta.messages.create(
            model=self.model, max_tokens=self.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools],
            messages=self._messages(messages), cache_control={"type": "ephemeral"}, **kwargs)
        text = "".join(b.text for b in r.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in r.content if b.type == "tool_use"]
        u = r.usage
        usage = Usage(input_tokens=u.input_tokens, cached_input_tokens=u.cache_read_input_tokens or 0,
                      cache_write_tokens=u.cache_creation_input_tokens or 0, output_tokens=u.output_tokens)
        raw = [b.model_dump(exclude_none=True) for b in r.content]
        return Reply(Message("assistant", text, calls, raw=raw, provider=self.provider), usage,
                     STOP.get(r.stop_reason, r.stop_reason))
