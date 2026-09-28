"""Adapter for the native OCI Generative AI API (on-demand chat), with the Generative AI API key.

Why not the OpenAI-compatible endpoint: there, with tools, Gemini answers empty and Cohere gives "Unsupported OpenAI
operation" (verified on 24/9 on /openai/v1 and /20231130/actions/v1); the Responses API for non-OpenAI models wants
an OCI project. The native API calls tools with every on-demand model and accepts the same API key
(Authorization: Bearer): only the compartment is needed.

Formats (Python SDK oci.generative_ai_inference, September 2026):
- GENERIC (gpt-oss, Llama, Gemini): SYSTEM/USER/ASSISTANT/TOOL messages with content = [{type: TEXT, text}],
  toolCalls [{type: FUNCTION, id, name, arguments}], ToolMessage with toolCallId; tools [{type: FUNCTION, name,
  description, parameters}]. Answering a tool call, the assistant's content is null (not "").
- COHEREV2 (Cohere Command A): messages with toolCalls [{id, type: FUNCTION, function: {name, arguments}}],
  tools [{type: FUNCTION, function: {name, description, parameters}}]; at most 4,000 output tokens.
Assistant messages are sent back as OCI returned them (raw field), as with the Responses API: any model field (e.g.
reasoning) is not lost between steps.
"""
import json

import httpx

from liveinsight.llm.base import Message, Reply, Tool, ToolCall, Usage

STOP = {"stop": "end", "tool_calls": "tool_calls", "length": "length", "COMPLETE": "end", "TOOL_CALL": "tool_calls",
        "MAX_TOKENS": "length", "content_filter": "refusal"}


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(c.get("text") or "" for c in content or [] if (c.get("type") or "").upper() == "TEXT")


class OCINative:
    def __init__(self, model, api_key, compartment, region, price=None, max_tokens=16000, timeout=300):
        self.provider, self.model, self.price = "oci", model, price
        self.compartment, self.api_key, self.timeout = compartment, api_key, timeout
        self.url = f"https://inference.generativeai.{region}.oci.oraclecloud.com/20231130/actions/chat"
        self.format = "COHEREV2" if model.startswith("cohere.") else "GENERIC"
        # on-demand: Cohere and Llama at most 4,000 output tokens (model docs)
        self.max_tokens = 4000 if model.startswith(("cohere.", "meta.")) else max_tokens
        # Llama: "Tool calling for this Llama model requires the first message to be a user message" (OCI, 24/9)
        self.system_in_user = model.startswith("meta.")

    # -- request -------------------------------------------------------------------------
    def _text(self, text):
        return [{"type": "TEXT", "text": text}]

    def _assistant(self, m: Message) -> dict:
        if m.provider == self.provider and m.raw is not None:
            return m.raw
        calls = [(c.id, c.name, json.dumps(c.arguments, ensure_ascii=False)) for c in m.tool_calls]
        if self.format == "COHEREV2":
            out = {"role": "ASSISTANT", "content": self._text(m.content) if m.content else None}
            if calls:
                out["toolCalls"] = [{"id": i, "type": "FUNCTION", "function": {"name": n, "arguments": a}}
                                    for i, n, a in calls]
            return out
        out = {"role": "ASSISTANT", "content": self._text(m.content) if m.content else None}
        if calls:
            out["toolCalls"] = [{"type": "FUNCTION", "id": i, "name": n, "arguments": a} for i, n, a in calls]
        return out

    def messages(self, system, messages):
        out = [] if self.system_in_user else [{"role": "SYSTEM", "content": self._text(system)}]
        for m in messages:
            if m.role == "tool":
                out.append({"role": "TOOL", "toolCallId": m.tool_call_id, "content": self._text(m.content)})
            elif m.role == "assistant":
                out.append(self._assistant(m))
            else:
                out.append({"role": "USER", "content": self._text(m.content)})
        if self.system_in_user and out and out[0]["role"] == "USER":
            out[0] = {"role": "USER", "content": self._text(f"{system}\n\n---\n\n{text_of(out[0]['content'])}")}
        return out

    def request(self, system, messages, tools: list[Tool]) -> dict:
        if self.format == "COHEREV2":
            tool_defs = [{"type": "FUNCTION", "function": {"name": t.name, "description": t.description,
                                                           "parameters": t.parameters}} for t in tools]
        else:
            tool_defs = [{"type": "FUNCTION", "name": t.name, "description": t.description,
                          "parameters": t.parameters} for t in tools]
        chat = {"apiFormat": self.format, "messages": self.messages(system, messages), "maxTokens": self.max_tokens}
        if tool_defs:
            chat["tools"] = tool_defs
        return {"compartmentId": self.compartment,
                "servingMode": {"servingType": "ON_DEMAND", "modelId": self.model}, "chatRequest": chat}

    # -- response --------------------------------------------------------------------------
    def chat(self, system, messages, tools: list[Tool]) -> Reply:
        r = httpx.post(self.url, json=self.request(system, messages, tools), timeout=self.timeout,
                       headers={"Authorization": f"Bearer {self.api_key}"})
        if r.status_code != 200:                            # never the key in the message: only OCI's answer
            raise RuntimeError(f"OCI Generative AI HTTP {r.status_code}: {r.text[:500]}")
        return self.parse(r.json()["chatResponse"])

    def parse(self, resp: dict) -> Reply:
        if self.format == "COHEREV2":
            msg, finish = resp.get("message") or {}, resp.get("finishReason")
        else:
            choice = (resp.get("choices") or [{}])[0]
            msg, finish = choice.get("message") or {}, choice.get("finishReason")
        calls = []
        for c in msg.get("toolCalls") or []:
            fn = c.get("function") or c                      # COHEREV2: {function: {name, arguments}}; GENERIC: flat
            raw_args = fn.get("arguments")
            try:
                args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
                calls.append(ToolCall(c.get("id") or "", fn.get("name") or "", args))
            except json.JSONDecodeError as e:
                calls.append(ToolCall(c.get("id") or "", fn.get("name") or "", {}, error=f"argomenti non validi (JSON): {e}"))
        u = resp.get("usage") or {}
        cached = (u.get("promptTokensDetails") or {}).get("cachedTokens") or 0
        reasoning = (u.get("completionTokensDetails") or {}).get("reasoningTokens") or 0
        # completionTokens excludes reasoning (Gemini: 41 + 6 + 83 = 130 in total): it is counted too
        usage = Usage(input_tokens=(u.get("promptTokens") or u.get("inputTokens") or 0) - cached,
                      cached_input_tokens=cached,
                      output_tokens=(u.get("completionTokens") or u.get("outputTokens") or 0) + reasoning)
        stop = "tool_calls" if calls else STOP.get(finish, finish or "end")
        return Reply(Message("assistant", text_of(msg.get("content")), calls, raw=msg, provider=self.provider),
                     usage, stop)
