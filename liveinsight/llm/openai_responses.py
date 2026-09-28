"""OpenAI Responses API adapter: tool calling with reasoning.

Choices from the OpenAI documentation (September 2026, guides "Function calling", "Reasoning", "Prompt caching"):
- reasoning models use tools through the Responses API (Chat Completions does not support them with reasoning);
- store=False (stateless): reasoning comes back encrypted automatically; ALL output items are sent back
  (reasoning, function_call, message), without null fields, plus the function_call_output items;
- reasoning.context: on GPT-5.6 the default is "all_turns", the default is kept;
- max_output_tokens: at least 25,000 reserved for reasoning and answer; if not enough, status="incomplete";
- cache: usage.input_tokens_details.cached_tokens (0.1x) and cache_write_tokens (1.25x on GPT-5.6+).
"""
import json

from openai import OpenAI

from liveinsight.llm.base import Message, Reply, Tool, ToolCall, Usage


class OpenAIResponses:
    def __init__(self, model, api_key=None, base_url=None, price=None, provider="openai",
                 reasoning_effort=None, max_tokens=25000):
        self.provider, self.model, self.price = provider, model, price
        self.reasoning_effort, self.max_tokens = reasoning_effort, max_tokens
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def input_items(self, messages):
        items = []
        for m in messages:
            if m.role == "tool":
                items.append({"type": "function_call_output", "call_id": m.tool_call_id, "output": m.content})
            elif m.role == "assistant":
                if m.provider == self.provider and m.raw is not None:
                    items.extend(m.raw)
                else:                                    # conversation started with another provider
                    if m.content:
                        items.append({"role": "assistant", "content": m.content})
                    items += [{"type": "function_call", "call_id": c.id, "name": c.name,
                               "arguments": json.dumps(c.arguments, ensure_ascii=False)} for c in m.tool_calls]
            else:
                items.append({"role": "user", "content": m.content})
        return items

    def chat(self, system, messages, tools: list[Tool]):
        kwargs = {"reasoning": {"effort": self.reasoning_effort}} if self.reasoning_effort else {}
        r = self.client.responses.create(
            model=self.model, instructions=system, input=self.input_items(messages),
            max_output_tokens=self.max_tokens, store=False,
            tools=[{"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters,
                    "strict": False} for t in tools],
            **kwargs)
        return self.parse(r)

    def parse(self, r):
        calls, texts, raw = [], [], []
        for item in r.output:
            # exclude_none: with a bare model_dump() (the guide's example) reasoning comes back with "status": null,
            # which the API refuses as input ("Unknown parameter: input[n].status")
            raw.append(item.model_dump(exclude_none=True))
            if item.type == "function_call":
                try:
                    calls.append(ToolCall(item.call_id, item.name, json.loads(item.arguments or "{}")))
                except json.JSONDecodeError as e:
                    calls.append(ToolCall(item.call_id, item.name, {}, error=f"argomenti non validi (JSON): {e}"))
            elif item.type == "message":
                texts += [c.text for c in item.content if c.type == "output_text"]
        u = r.usage
        details = u.input_tokens_details
        cached = getattr(details, "cached_tokens", 0) or 0
        written = getattr(details, "cache_write_tokens", 0) or 0
        usage = Usage(input_tokens=u.input_tokens - cached - written, cached_input_tokens=cached,
                      cache_write_tokens=written, output_tokens=u.output_tokens)
        if r.status == "incomplete":
            stop = "length" if getattr(r.incomplete_details, "reason", "") == "max_output_tokens" else "incomplete"
        else:
            stop = "tool_calls" if calls else "end"
        return Reply(Message("assistant", "".join(texts), calls, raw=raw, provider=self.provider), usage, stop)
