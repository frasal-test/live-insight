"""Chat Completions adapter: OpenAI and every compatible endpoint (OCI Generative AI, your own server).

OCI exposes /openai/v1 with Chat Completions and tool calling, and so do Ollama, vLLM, LM Studio…: only base_url and
key change, so the same adapter serves them all (see liveinsight/llm/__init__.py).
"""
import json

from openai import OpenAI

from liveinsight.llm.base import Message, Price, Reply, Tool, ToolCall, Usage

STOP = {"stop": "end", "tool_calls": "tool_calls", "length": "length", "content_filter": "refusal"}


class OpenAIChat:
    def __init__(self, model, api_key=None, base_url=None, price=None, provider="openai",
                 reasoning_effort=None, max_tokens=16000, default_headers=None):
        self.provider, self.model, self.price = provider, model, price
        self.reasoning_effort, self.max_tokens = reasoning_effort, max_tokens
        self.client = OpenAI(api_key=api_key, base_url=base_url, default_headers=default_headers)

    def _messages(self, system, messages):
        # Llama on OCI: "Tool calling for this Llama model requires the first message to be a user message"
        # (OCI error 400, 24/9). The system prompt goes at the top of the user's first message.
        system_in_user = self.provider == "oci" and self.model.startswith("meta.")
        out = [] if system_in_user else [{"role": "system", "content": system}]
        for m in messages:
            if m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
            elif m.role == "assistant":
                msg = {"role": "assistant", "content": m.content or None}
                if m.tool_calls:
                    msg["tool_calls"] = [{"id": c.id, "type": "function", "function": {
                        "name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)}} for c in m.tool_calls]
                out.append(msg)
            else:
                out.append({"role": "user", "content": m.content})
        if system_in_user and out and out[0]["role"] == "user":
            out[0] = {"role": "user", "content": f"{system}\n\n---\n\n{out[0]['content']}"}
        return out

    def chat(self, system, messages, tools: list[Tool]):
        kwargs = {}
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        r = self.client.chat.completions.create(
            model=self.model, messages=self._messages(system, messages), max_completion_tokens=self.max_tokens,
            tools=[{"type": "function", "function": {"name": t.name, "description": t.description,
                                                     "parameters": t.parameters}} for t in tools] or None,
            **kwargs)
        choice = r.choices[0]
        calls = []
        for c in choice.message.tool_calls or []:
            try:
                calls.append(ToolCall(c.id, c.function.name, json.loads(c.function.arguments or "{}")))
            except json.JSONDecodeError as e:
                calls.append(ToolCall(c.id, c.function.name, {}, error=f"argomenti non validi (JSON): {e}"))
        u = r.usage
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        usage = Usage(input_tokens=u.prompt_tokens - cached, cached_input_tokens=cached, output_tokens=u.completion_tokens)
        return Reply(Message("assistant", choice.message.content or "", calls, provider=self.provider),
                     usage, STOP.get(choice.finish_reason, choice.finish_reason))
