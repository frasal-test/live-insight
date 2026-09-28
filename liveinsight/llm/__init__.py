"""The chat model from a string "provider:model[@effort]". There is no default: the user picks one in Settings
(or LLM_MODEL in .env).

    openai:gpt-5.6-luna@low        Responses API, with reasoning effort (the best on the golden set)
    openai:gpt-5.6-luna            without @: the model's default effort
    openai-chat:gpt-5.6-luna@none  Chat Completions (tools on luna only with effort "none")
    anthropic:claude-opus-5        also claude-sonnet-5, claude-haiku-4-5
    oci:<model>                    OCI Generative AI, native API (on-demand chat) with API key and compartment
    oci-chat:<model>               OCI, OpenAI-compatible endpoint (Chat Completions): only for comparisons
    custom:<model>                 your own OpenAI-compatible server (Ollama, vLLM, LM Studio…): base_url in Settings
                                   (CUSTOM_LLM_BASE_URL), API key optional; the model must support tool calling

Keys: passed by the caller (the app's Settings, liveinsight/settings.py) or else from .env: OPENAI_API_KEY,
ANTHROPIC_API_KEY, OCI_GENAI_API_KEY (+ OCI_REGION, default eu-frankfurt-1, and OCI_COMPARTMENT_ID),
CUSTOM_LLM_API_KEY.
"""
import os
import time

from liveinsight.llm.base import ChatModel, Message, Price, Reply, Tool, ToolCall, Usage  # noqa: F401
from liveinsight.messages import UiError, msg

PROVIDERS = {"openai": "OpenAI", "anthropic": "Anthropic", "oci": "OCI Generative AI",
             "custom": "OpenAI-compatible server"}
KEY_ENV = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "oci": "OCI_GENAI_API_KEY",
           "custom": "CUSTOM_LLM_API_KEY"}
KEY_OPTIONAL = {"custom"}               # local servers often need no key (Ollama: "required but ignored")

# Models proposed in Settings (a provider without a list takes any model ID).
MODELS = {
    "openai": [{"id": "openai:gpt-5.6-luna@low", "label": "GPT-5.6 Luna (effort low)"}],
    "anthropic": [{"id": "anthropic:claude-opus-5", "label": "Claude Opus 5"},
                  {"id": "anthropic:claude-sonnet-5", "label": "Claude Sonnet 5"},
                  {"id": "anthropic:claude-haiku-4-5", "label": "Claude Haiku 4.5"}],
}

# OCI per region: on-demand models (Oracle docs, September 2026: "Models by Region"), all tried on 24/9 with the
# native API (tool call + result + answer). On the OpenAI-compatible endpoint instead Gemini answers empty with tools
# and Cohere gives "Unsupported OpenAI operation": that is why oci: uses the native API.
# Llama and Cohere on-demand: at most 4,000 answer tokens. Gemini: served by Google, in an EU location.
# Other models: "Other model…" in Settings, then "Verify the model".
OCI_MODELS = {
    "eu-frankfurt-1": [
        {"id": "oci:openai.gpt-oss-120b", "label": "OpenAI gpt-oss-120b"},
        {"id": "oci:openai.gpt-oss-20b", "label": "OpenAI gpt-oss-20b"},
        # "note": a message key, written by the UI in its language (liveinsight.messages)
        {"id": "oci:google.gemini-2.5-pro", "label": "Google Gemini 2.5 Pro", "note": "models.processedByGoogleEU"},
        {"id": "oci:google.gemini-2.5-flash", "label": "Google Gemini 2.5 Flash", "note": "models.processedByGoogleEU"},
        {"id": "oci:google.gemini-2.5-flash-lite", "label": "Google Gemini 2.5 Flash-Lite", "note": "models.processedByGoogleEU"},
        {"id": "oci:cohere.command-a-03-2025", "label": "Cohere Command A", "note": "models.answersUpTo4k"},
        {"id": "oci:meta.llama-3.3-70b-instruct", "label": "Meta Llama 3.3 70B", "note": "models.answersUpTo4k"},
    ],
}


def oci_region() -> str:
    return os.environ.get("OCI_REGION", "eu-frankfurt-1")


def models_for(provider: str) -> list[dict]:
    """Models to propose; empty list = any model ID. OCI depends on OCI_REGION (read at every call)."""
    return OCI_MODELS.get(oci_region(), []) if provider == "oci" else MODELS.get(provider, [])


# Per million tokens (September 2026). Cache: read 0.1x, write 1.25x (OpenAI GPT-5.6+ and Anthropic).
# OCI: price list in euro as of 24/9 (text input and output; no cache price: counted as input). Estimates: check the
# providers' current prices. The custom provider has no price: its cost is not shown.
PRICES = {
    ("openai", "gpt-5.6-luna"): Price(0.20, 0.02, 1.20, 0.25),          # >272K input: 2x / 1.5x
    ("anthropic", "claude-opus-5"): Price(5.00, 0.50, 25.00, 6.25),
    ("anthropic", "claude-sonnet-5"): Price(2.00, 0.20, 10.00, 2.50),
    ("anthropic", "claude-haiku-4-5"): Price(1.00, 0.10, 5.00, 1.25),
    ("oci", "openai.gpt-oss-120b"): Price(0.1395, 0.1395, 0.558, currency="EUR"),
    ("oci", "openai.gpt-oss-20b"): Price(0.0651, 0.0651, 0.279, currency="EUR"),
    ("oci", "google.gemini-2.5-pro"): Price(1.1625, 1.1625, 9.30, currency="EUR"),       # >200K input: 2x / 1.5x
    ("oci", "google.gemini-2.5-flash"): Price(0.279, 0.279, 2.325, currency="EUR"),
    ("oci", "google.gemini-2.5-flash-lite"): Price(0.093, 0.093, 0.372, currency="EUR"),
    # Cohere and Meta on OCI are billed per "transaction" = characters (input and output), per 10,000: estimated in
    # tokens with 1 token ≈ 4 characters, so €/M tokens = €/10,000 characters × 400. Assumed price-list entries:
    # Command A = "Large Cohere" (€0.014508), Llama 3.3 70B = "Large Meta" (€0.001674; 3.3 is not named). Estimates.
    ("oci", "cohere.command-a-03-2025"): Price(5.8032, 5.8032, 5.8032, currency="EUR"),
    ("oci", "meta.llama-3.3-70b-instruct"): Price(0.6696, 0.6696, 0.6696, currency="EUR"),
}


def make_model(spec: str | None = None, keys: dict | None = None, oci_compartment: str | None = None,
               base_url: str | None = None) -> ChatModel:
    """base_url: the server of the "custom" provider (OpenAI-compatible)."""
    spec = spec or os.environ.get("LLM_MODEL")
    if not spec:
        raise UiError("no model chosen: pick one in Settings (or LLM_MODEL in .env)", "settings.errors.noModel")
    provider, _, rest = spec.partition(":")
    model, _, effort = rest.partition("@")
    key_provider = {"openai-chat": "openai", "oci-chat": "oci"}.get(provider, provider)
    price = PRICES.get((key_provider, model))
    if key_provider not in KEY_ENV:
        raise UiError(f"unknown provider: {provider!r} (openai, anthropic, oci)", "settings.errors.provider", provider=provider)
    api_key = (keys or {}).get(key_provider) or os.environ.get(KEY_ENV[key_provider])
    if provider == "custom":             # your own OpenAI-compatible server: Chat Completions with tools
        base_url = base_url or os.environ.get("CUSTOM_LLM_BASE_URL")
        if not base_url:
            raise UiError("server URL missing for the OpenAI-compatible provider", "settings.errors.baseUrl")
        from liveinsight.llm.openai_chat import OpenAIChat
        # the OpenAI client refuses to start without a key: a placeholder for servers that need none
        return OpenAIChat(rest, api_key=api_key or "not-needed", base_url=base_url, provider="custom")
    if not api_key:
        raise UiError(f"{PROVIDERS[key_provider]} API key missing: set it in Settings", "settings.errors.apiKey",
                      provider=PROVIDERS[key_provider])
    if provider == "openai":             # Responses API: tool calling with reasoning
        from liveinsight.llm.openai_responses import OpenAIResponses
        return OpenAIResponses(model, api_key=api_key, price=price, reasoning_effort=effort or None)
    if provider == "openai-chat":        # Chat Completions: on gpt-5.6-luna tools need effort "none"
        from liveinsight.llm.openai_chat import OpenAIChat
        return OpenAIChat(model, api_key=api_key, price=price, reasoning_effort=effort or None)
    if provider == "anthropic":
        from liveinsight.llm.anthropic_chat import AnthropicChat
        return AnthropicChat(model, api_key=api_key, price=price, effort=effort or None)
    if provider == "oci":                # native API: the only one where every on-demand model uses tools
        compartment = oci_compartment or os.environ.get("OCI_COMPARTMENT_ID")
        if not compartment:
            raise UiError("OCI compartment (OCID) missing: set it in Settings", "settings.errors.compartment")
        from liveinsight.llm.oci_native import OCINative
        return OCINative(model, api_key, compartment, oci_region(), price=price)
    from liveinsight.llm.openai_chat import OpenAIChat   # oci-chat: OpenAI-compatible endpoint
    return OpenAIChat(model, api_key=api_key, provider="oci", price=price, reasoning_effort=effort or None,
                      base_url=f"https://inference.generativeai.{oci_region()}.oci.oraclecloud.com/openai/v1")


PROBE_TOOL = Tool("add", "Adds two numbers and returns the result.",
                  {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                   "required": ["a", "b"]})


def probe(model: ChatModel) -> dict:
    """A test call (a few dozen tokens): does the model really use the tool?

    It finds at once cases like Gemini on OCI with Chat Completions: no error, but an empty answer.
    """
    t0 = time.time()
    try:
        r = model.chat("For calculations always use the add tool: never compute in your head.",
                       [Message("user", "What is 17 + 25?")], [PROBE_TOOL])
    except Exception as e:                              # provider error (key, policy, model that does not exist)
        return {"ok": False, "detail": msg("probe.error", error=f"{type(e).__name__}: {str(e)[:300]}"),
                "seconds": round(time.time() - t0, 1)}
    seconds = round(time.time() - t0, 1)
    cost = model.price.cost(r.usage) if model.price else None       # a number: the UI formats it
    call = next((c for c in r.message.tool_calls if c.name == "add"), None)

    def number(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    args = {number(call.arguments.get("a")), number(call.arguments.get("b"))} if call and not call.error else set()
    if args == {17, 25}:
        as_text = any(isinstance(call.arguments.get(k), str) for k in ("a", "b"))   # Llama on OCI: "17" instead of 17
        return {"ok": True, "seconds": seconds, "cost": cost, "currency": model.price.currency if model.price else None,
                "detail": msg("probe.usesToolsNumbersAsText" if as_text else "probe.usesTools")}
    if call:
        detail = msg("probe.wrongArguments", arguments=str(call.error or call.arguments))
    elif r.message.content.strip():
        detail = msg("probe.noTool", text=r.message.content.strip()[:120])
    else:
        detail = msg("probe.empty", tokens=r.usage.output_tokens)
    return {"ok": False, "detail": detail, "seconds": seconds, "cost": cost,
            "currency": model.price.currency if model.price else None}
