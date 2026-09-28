"""Impostazioni dell'app: precedenze, file, validazione, chiavi mai al browser. Nessuna rete, nessun token."""
import json
import stat

import pytest
from fastapi.testclient import TestClient

from liveinsight.api import Config, create_app
from liveinsight.llm import make_model, models_for, probe
from liveinsight.llm.base import Message, Reply, ToolCall, Usage
from liveinsight.messages import UiError
from liveinsight.settings import Settings, SettingsError
from test_api import FakeMcp, ScriptedModel, fake_jwt

SEGRETA = "sk-segreta-1234567890"


def test_load_env_poi_file(tmp_path):
    env = {"OAC_URL": "https://da-env.example.com", "OPENAI_API_KEY": "k-env", "LLM_MODEL": "openai:gpt-5.6-luna@low"}
    path = tmp_path / "settings.json"
    assert Settings.load(path, env) == Settings("https://da-env.example.com", "openai:gpt-5.6-luna@low", {"openai": "k-env"})
    path.write_text(json.dumps({"oac_url": "https://da-file.example.com", "model": "anthropic:claude-opus-5",
                                "keys": {"anthropic": "k-file", "oci": ""}}))
    s = Settings.load(path, env)                               # il file vince; .env resta per ciò che manca
    assert s.oac_url == "https://da-file.example.com" and s.model == "anthropic:claude-opus-5"
    assert s.keys == {"openai": "k-env", "anthropic": "k-file"}


def test_save_solo_per_il_proprietario(tmp_path):
    path = tmp_path / ".secrets" / "settings.json"
    Settings("https://oac.example.com", "openai:gpt-5.6-luna@low", {"openai": SEGRETA}).save(path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert Settings.load(path, {}).keys == {"openai": SEGRETA}


def test_validazione():
    s = Settings(keys={"openai": "k"}, oci_compartment="ocid1.compartment.oc1..aaaaexample")
    new = s.updated(" https://oac.example.com/ui/dv/?pageid=home ", "openai:gpt-5.6-luna@low")
    assert new.oac_url == "https://oac.example.com" and new.keys == {"openai": "k"}   # chiave vuota: resta quella
    assert s.updated("https://oac.example.com", "oci:openai.gpt-oss-120b", "k-oci").keys == {"openai": "k", "oci": "k-oci"}
    for url, model, key, error in [
        ("http://oac.example.com", "openai:gpt-5.6-luna@low", None, "https://"),
        ("oac.example.com", "openai:gpt-5.6-luna@low", None, "https://"),
        ("https://oac.example.com", "mistral:x", None, "unknown provider"),
        ("https://oac.example.com", "anthropic:claude-opus-5", "  ", "Anthropic API key missing"),
        ("https://oac.example.com", "oci:", "k", "model missing"),
        ("https://oac.example.com", "oci:modello con spazi", "k", "model missing"),
    ]:
        with pytest.raises(SettingsError, match=error):
            s.updated(url, model, key)


COMP = "ocid1.compartment.oc1..aaaaexample"


def test_modelli_oci_per_regione(monkeypatch):
    monkeypatch.setenv("OCI_REGION", "eu-frankfurt-1")
    ids = [m["id"] for m in models_for("oci")]
    assert {"oci:openai.gpt-oss-120b", "oci:google.gemini-2.5-flash", "oci:cohere.command-a-03-2025"} <= set(ids)
    model = make_model("oci:openai.gpt-oss-120b", {"oci": "k"}, oci_compartment=COMP)
    assert model.url == "https://inference.generativeai.eu-frankfurt-1.oci.oraclecloud.com/20231130/actions/chat"
    monkeypatch.setenv("OCI_REGION", "us-chicago-1")                # regione senza elenco: modello libero
    assert models_for("oci") == []
    s = Settings(keys={"oci": "k"}, oci_compartment=COMP)
    assert s.updated("https://oac.example.com", "oci:xai.grok-4.6").model == "oci:xai.grok-4.6"
    with pytest.raises(SettingsError, match="model missing"):
        s.updated("https://oac.example.com", "oci:")


def test_modelli_liberi_e_compartment_oci(monkeypatch):
    monkeypatch.delenv("OCI_COMPARTMENT_ID", raising=False)
    s = Settings(keys={"oci": "k", "openai": "k"})
    assert s.with_model("openai:gpt-5.7@low").model == "openai:gpt-5.7@low"          # non in elenco: si può scrivere
    with pytest.raises(SettingsError, match=r"compartment \(OCID\) missing"):
        s.with_model("oci:openai.gpt-oss-120b")
    new = s.with_model("oci:google.gemini-2.5-flash", oci_compartment=f" {COMP} ")
    assert new.oci_compartment == COMP
    assert new.with_model("oci:cohere.command-a-03-2025").oci_compartment == COMP     # resta quello salvato
    assert new.with_model("openai:gpt-5.6-luna@low").oci_compartment == COMP         # cambiare provider non lo perde
    assert s.with_model("oci:x.y", oci_compartment="ocid1.tenancy.oc1..radice").oci_compartment.startswith("ocid1.tenancy")
    with pytest.raises(SettingsError, match="ocid1.compartment"):
        s.with_model("oci:openai.gpt-oss-120b", oci_compartment="ocid1.generativeaiproject.oc1.x")
    with pytest.raises(ValueError, match=r"compartment \(OCID\) missing"):
        make_model("oci:openai.gpt-oss-120b", {"oci": "k"})


def test_oci_api_nativa_o_compatibile_openai():
    native = make_model("oci:google.gemini-2.5-flash", {"oci": "k"}, oci_compartment=COMP)
    assert type(native).__name__ == "OCINative" and native.format == "GENERIC" and native.price.currency == "EUR"
    assert make_model("oci:cohere.command-a-03-2025", {"oci": "k"}, oci_compartment=COMP).max_tokens == 4000
    compat = make_model("oci-chat:openai.gpt-oss-120b", {"oci": "k"})                  # solo per confronti
    assert type(compat).__name__ == "OpenAIChat" and str(compat.client.base_url).endswith("/openai/v1/")


class ProbeModel:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.price = reply, error, None

    def chat(self, system, messages, tools):
        if self.error:
            raise self.error
        return self.reply


def test_probe():
    reply = lambda msg: Reply(msg, Usage(30, 0, 0, 11), "end")
    assert probe(ProbeModel(reply(Message("assistant", "", [ToolCall("1", "add", {"a": 17, "b": 25})]))))["ok"]
    llama = probe(ProbeModel(reply(Message("assistant", "", [ToolCall("1", "add", {"a": "17", "b": "25"})]))))
    assert llama["ok"] and llama["detail"]["key"] == "probe.usesToolsNumbersAsText"
    empty = probe(ProbeModel(reply(Message("assistant", ""))))                        # il caso Gemini su Chat Completions
    assert not empty["ok"] and empty["detail"] == {"key": "probe.empty", "params": {"tokens": 11}}
    text = probe(ProbeModel(reply(Message("assistant", "Fa 42."))))
    assert not text["ok"] and text["detail"]["key"] == "probe.noTool"
    err = probe(ProbeModel(error=RuntimeError("401 chiave non valida")))
    assert not err["ok"] and err["detail"]["params"]["error"].startswith("RuntimeError")


def test_verifica_dal_form(tmp_path):
    c = app_client(tmp_path, Settings("https://oac.example.com", keys={"openai": SEGRETA}))
    r = c.post("/api/settings/verify", json={"model": "oci:openai.gpt-oss-120b"})
    assert r.status_code == 422 and r.json()["detail"] == {"key": "settings.errors.apiKey", "params": {"provider": "OCI Generative AI"}}
    assert not (tmp_path / "settings.json").exists()                     # la verifica non salva nulla


def test_make_model_senza_chiave(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Anthropic API key missing"):
        make_model("anthropic:claude-opus-5", {})
    assert make_model("anthropic:claude-opus-5", {"anthropic": "k"}).model == "claude-opus-5"


def app_client(tmp_path, settings: Settings, dev=True, opened=None):
    tokens = tmp_path / "tokens.json"
    if dev:
        tokens.write_text(json.dumps({"accessToken": fake_jwt(), "refreshToken": "r", "expiresIn": 3600}))
    config = Config(settings=settings, settings_path=tmp_path / "settings.json", dev_tokens=tokens)

    def make_mcp(oac_url, token):                             # registra su quale istanza si apre l'MCP
        (opened if opened is not None else []).append(oac_url)
        return FakeMcp(oac_url, token)
    return TestClient(create_app(config, make_mcp=make_mcp, model_factory=lambda *a, **k: ScriptedModel()))


def test_primo_avvio_senza_url_di_oac(tmp_path):
    c = app_client(tmp_path, Settings(), dev=False)
    assert c.get("/api/me").status_code == 503                 # la UI apre le Impostazioni
    assert c.get("/api/auth/login", follow_redirects=False).headers["location"] == "http://localhost:5173/"
    view = c.get("/api/settings").json()                      # senza login: non c'è ancora un OAC a cui accedere
    assert view["oac_url"] is None and view["keys_set"] == {"openai": False, "anthropic": False, "oci": False, "custom": False}
    assert [p["id"] for p in view["providers"]] == ["openai", "anthropic", "oci", "custom"]

    r = c.put("/api/settings", json={"oac_url": "https://oac.example.com", "model": "openai:gpt-5.6-luna@low"})
    assert r.status_code == 422 and r.json()["detail"]["key"] == "settings.errors.apiKey"
    r = c.put("/api/settings", json={"oac_url": "https://oac.example.com", "model": "openai:gpt-5.6-luna@low",
                                     "api_key": SEGRETA})
    assert r.status_code == 200 and r.json()["keys_set"]["openai"] is True and SEGRETA not in r.text
    assert json.loads((tmp_path / "settings.json").read_text())["keys"] == {"openai": SEGRETA}

    # configurato: ora le Impostazioni sono solo per chi accede all'app
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/settings").status_code == 401
    assert c.put("/api/settings", json={"oac_url": "https://altro.example.com", "model": "openai:gpt-5.6-luna@low"}
                 ).status_code == 401


def test_modello_e_cambio_di_istanza(tmp_path):
    opened = []
    c = app_client(tmp_path, Settings("https://oac.example.com", keys={"openai": SEGRETA}), opened=opened)
    assert c.get("/api/me").json()["model"] is None                           # no default: the UI opens Settings
    assert SEGRETA not in c.get("/api/settings").text
    c.put("/api/settings", json={"oac_url": "https://oac.example.com", "model": "anthropic:claude-sonnet-5",
                                 "api_key": "k-anthropic"})
    assert c.get("/api/me").json()["model"] == "anthropic:claude-sonnet-5"
    assert opened == ["https://oac.example.com"]              # stessa istanza: l'utente resta collegato

    c.put("/api/settings", json={"oac_url": "https://nuova.example.com", "model": "anthropic:claude-sonnet-5"})
    c.get("/api/me")                                          # i token valgono per un'istanza: si riapre tutto
    assert opened == ["https://oac.example.com", "https://nuova.example.com"]


def test_custom_openai_compatible_server(tmp_path):
    s = Settings("https://oac.example.com")
    with pytest.raises(SettingsError, match="server URL missing"):
        s.with_model("custom:qwen2.5:14b")
    with pytest.raises(SettingsError, match="http:// or https://"):
        s.with_model("custom:qwen2.5:14b", custom_base_url="localhost:11434")
    new = s.with_model("custom:qwen2.5:14b", custom_base_url="http://localhost:11434/v1/")
    assert new.model == "custom:qwen2.5:14b" and new.custom_base_url == "http://localhost:11434/v1"    # no key needed
    assert new.with_model("custom:meta-llama/Llama-3.1-8B-Instruct").custom_base_url == new.custom_base_url
    model = make_model(new.model, new.keys, base_url=new.custom_base_url)
    assert (model.provider, model.model, str(model.client.base_url)) == ("custom", "qwen2.5:14b", "http://localhost:11434/v1/")


def test_no_model_no_default(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(UiError, match="no model chosen"):
        make_model(None, {})
