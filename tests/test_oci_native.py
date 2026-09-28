"""Adattatore per l'API nativa di OCI Generative AI: richieste e risposte nei formati visti il 24/9. Nessuna rete."""
import json

from liveinsight.llm import PROBE_TOOL
from liveinsight.llm.base import Message
from liveinsight.llm.oci_native import OCINative

COMP = "ocid1.compartment.oc1..aaaaexample"


def model(name):
    return OCINative(name, "k", COMP, "eu-frankfurt-1")


def test_richiesta_generic():
    m = model("google.gemini-2.5-flash")
    body = m.request("SISTEMA", [Message("user", "Quanto fa 17 + 25?")], [PROBE_TOOL])
    assert body["compartmentId"] == COMP and body["servingMode"] == {"servingType": "ON_DEMAND",
                                                                      "modelId": "google.gemini-2.5-flash"}
    chat = body["chatRequest"]
    assert chat["apiFormat"] == "GENERIC" and chat["maxTokens"] == 16000
    assert chat["messages"][0] == {"role": "SYSTEM", "content": [{"type": "TEXT", "text": "SISTEMA"}]}
    assert chat["tools"][0] == {"type": "FUNCTION", "name": "add", "description": PROBE_TOOL.description,
                                "parameters": PROBE_TOOL.parameters}


def test_ciclo_generic_con_risposta_reale():
    # risposta vera di Gemini 2.5 Flash (24/9): toolCalls piatti, content assente, ragionamento a parte
    m = model("google.gemini-2.5-flash")
    reply = m.parse({"apiFormat": "GENERIC", "choices": [{"index": 0, "finishReason": "stop", "message": {
        "role": "ASSISTANT", "toolCalls": [{"type": "FUNCTION", "id": "call_1", "name": "add",
                                            "arguments": "{\"a\":17,\"b\":25}"}]}}],
        "usage": {"completionTokens": 6, "promptTokens": 41, "totalTokens": 130,
                  "completionTokensDetails": {"reasoningTokens": 83}}})
    assert reply.stop == "tool_calls" and reply.message.tool_calls[0].arguments == {"a": 17, "b": 25}
    assert reply.usage.input_tokens == 41 and reply.usage.output_tokens == 89          # ragionamento compreso
    msgs = m.messages("S", [Message("user", "q"), reply.message, Message("tool", "42", tool_call_id="call_1")])
    assert msgs[2] == reply.message.raw                                                 # rimandato com'era
    assert msgs[3] == {"role": "TOOL", "toolCallId": "call_1", "content": [{"type": "TEXT", "text": "42"}]}


def test_cohere_v2():
    m = model("cohere.command-a-03-2025")
    chat = m.request("S", [Message("user", "q")], [PROBE_TOOL])["chatRequest"]
    assert chat["apiFormat"] == "COHEREV2" and chat["maxTokens"] == 4000
    assert chat["tools"][0] == {"type": "FUNCTION", "function": {"name": "add", "description": PROBE_TOOL.description,
                                                                 "parameters": PROBE_TOOL.parameters}}
    reply = m.parse({"apiFormat": "COHEREV2", "finishReason": "TOOL_CALL", "message": {
        "role": "ASSISTANT", "content": [{"type": "TEXT", "text": "Uso add."}],
        "toolCalls": [{"id": "t1", "type": "FUNCTION", "function": {"name": "add", "arguments": "{\"a\": 17.0, \"b\": 25.0}"}}]},
        "usage": {"promptTokens": 41, "completionTokens": 33}})
    assert reply.message.content == "Uso add." and reply.message.tool_calls[0].name == "add"
    assert reply.usage.output_tokens == 33


def test_llama_prompt_di_sistema_nel_primo_messaggio_utente():
    msgs = model("meta.llama-3.3-70b-instruct").messages("SISTEMA", [Message("user", "domanda")])
    assert [m["role"] for m in msgs] == ["USER"] and msgs[0]["content"][0]["text"].startswith("SISTEMA")


def test_conversazione_iniziata_con_un_altro_provider():
    from liveinsight.llm.base import ToolCall
    other = Message("assistant", "", [ToolCall("c9", "add", {"a": 1, "b": 2})], provider="openai", raw=[{"x": 1}])
    msg = model("openai.gpt-oss-120b").messages("S", [Message("user", "q"), other])[2]
    assert msg == {"role": "ASSISTANT", "content": None, "toolCalls": [
        {"type": "FUNCTION", "id": "c9", "name": "add", "arguments": json.dumps({"a": 1, "b": 2})}]}
