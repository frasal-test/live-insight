"""Backend (M6): il flusso completo via HTTP, con MCP e modello finti. Nessuna rete, nessun token."""
import base64
import io
import json
import re
import threading
import time
import urllib.parse
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import SKELETON, XSA, needs_skeleton
from liveinsight.platforms.oac import auth as oac_auth
from liveinsight.api import COOKIE, USER_MAX_AGE, Config, User, create_app, renew_tokens
from liveinsight.platforms.oac.target import bundled
from liveinsight.llm.base import Message, Reply, ToolCall, Usage
from liveinsight.platforms.oac.auth import OacToken
from liveinsight.settings import Settings
from liveinsight.engine.spec import load_spec

DESCRIBE = json.loads(open("tests/fixtures/retail_orders_describe.json").read())


def fake_jwt(name="Test User", minutes=60):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'none'})}.{enc({'exp': time.time() + minutes * 60, 'user_displayname': name})}.x"


class FakeMcp:
    saved = []                                            # save_catalog_content ricevute (condivise fra istanze)
    in_folder = ["Varianti", "Vendite per segmento"]      # workbook già presenti nella cartella di test

    def __init__(self, oac_url, token):
        self.token = token

    def initialize(self):
        pass

    def call(self, tool, **args):
        if tool == "search_catalog" and args.get("types") == ["folders"]:
            body = {"items": [{"name": "Live Insight - test", "id": "cartella-test", "type": "folders"},
                              {"name": "Live Insight - test (vecchia)", "id": "altra", "type": "folders"}]}
        elif tool == "search_catalog" and args.get("rootFolder"):
            assert args["rootFolder"] == "cartella-test"
            body = {"items": [{"name": n, "type": "workbooks"} for n in self.in_folder]}
        elif tool == "save_catalog_content":
            FakeMcp.saved.append(args)
            body = {"status": "ok", "mode": "create"}
        elif tool == "search_catalog" and args.get("types") == ["datasets"]:
            body = {"items": [{"name": "Retail Orders FS", "xsaExpr": XSA, "type": "datasets",
                               "path": "/@Catalog/users/someone@example.com/Retail Orders FS"}]}
        elif tool == "search_catalog":
            body = {"items": [{"name": args["search"], "type": "workbooks",
                               "contentResource": f"content://catalog/workbooks/{args['search']}"}]}
        elif tool == "describe_data":
            body = DESCRIBE
        elif tool == "execute_logical_sql":
            body = {"batches": [{"data": [{"c1": 1722719.78, "c2": "Consumer"}, {"c1": 3040035.94, "c2": "Corporate"}]}],
                    "status": {"error": False}}
        else:
            raise AssertionError(tool)
        return {"content": [{"type": "text", "text": json.dumps(body)}]}

    def read_resource(self, uri):
        name = uri.rsplit("/", 1)[1]                      # TEMPLATE_WORKBOOKS: templates read from the catalog
        file = {"Examples": "examples.json", "Example 2": "example2.json", "Varianti": "varianti.json"}[name]
        return [{"text": json.dumps({"json": bundled(file)})}]


class ScriptedModel:
    provider, model, price = "fake", "fake", None

    def __init__(self):
        self.replies = [
            Reply(Message("assistant", "", [ToolCall("1", "propose_visual", {
                "kind": "bar", "title": "Corporate vende di più", "roles": {"measures": ["Sales"], "detail": ["Seg"]},
                "columns": [{"kind": "column", "id": "Sales", "source": "Sales"},
                            {"kind": "column", "id": "Seg", "source": "Customer Segment"}]})], provider="fake"),
                  Usage(100, 0, 0, 10), "tool_calls"),
            Reply(Message("assistant", "Corporate vende 3,04 milioni.", provider="fake"), Usage(120, 0, 0, 20), "end"),
        ]

    def chat(self, system, messages, tools):
        return self.replies.pop(0)


@pytest.fixture
def client(tmp_path):
    tokens = tmp_path / "tokens.json"
    tokens.write_text(json.dumps({"accessToken": fake_jwt(), "refreshToken": "r", "expiresIn": 3600}))
    config = Config(settings=Settings(oac_url="https://oac.example.com"), dev_tokens=tokens, skeleton=SKELETON)
    return TestClient(create_app(config, make_mcp=FakeMcp, model_factory=lambda *a, **k: ScriptedModel()))


def sse(text):
    return [(b.split("\n")[0][len("event: "):], json.loads(b.split("\n")[1][len("data: "):]))
            for b in text.strip().split("\n\n")]


def test_flusso_completo(client):
    assert client.get("/api/me").json()["name"] == "Test User"
    datasets = client.get("/api/datasets").json()
    assert datasets == [{"name": "Retail Orders FS", "xsa": XSA, "folder": "Le mie cartelle"}]   # niente email

    sid = client.post("/api/sessions", json={"xsa": XSA}).json()["id"]
    events = sse(client.post(f"/api/sessions/{sid}/ask", json={"question": "chi vende di più?"}).text)
    assert [k for k, _ in events] == ["visual", "done"]
    visual = events[0][1]
    assert visual["kind"] == "bar" and visual["rows"][1] == {"Sales": 3040035.94, "Seg": "Corporate"}
    assert [c["type"] for c in visual["columns"]] == ["quantitative", "nominal"]
    assert events[1][1]["text"] == "Corporate vende 3,04 milioni."

    r = client.patch(f"/api/sessions/{sid}/visuals/1", json={"pinned": True, "title": "Corporate: 36% delle vendite"})
    assert r.json()["order"] == [1] and r.json()["visual"]["title"] == "Corporate: 36% delle vendite"
    assert client.put(f"/api/sessions/{sid}/order", json={"order": [1, 2]}).status_code == 422

    state = client.get(f"/api/sessions/{sid}").json()
    assert [m["role"] for m in state["messages"]] == ["user", "assistant"] and state["order"] == [1]

    # the engine's output: the WorkbookSpec JSON of the pinned visuals, readable by any WorkbookTarget
    r = client.post(f"/api/sessions/{sid}/spec", json={"name": "Vendite per segmento"})
    assert r.status_code == 200 and "workbook-spec.json" in r.headers["content-disposition"]
    spec = load_spec(r.content)
    assert spec.version == 1 and spec.dataset.id == XSA and spec.name == "Vendite per segmento"
    assert [v.title for c in spec.canvases for v in c.visuals] == ["Corporate: 36% delle vendite"]

    # "Salva in OAC": solo nella cartella di test, mai sopra un workbook esistente
    FakeMcp.saved.clear()
    r = client.post(f"/api/sessions/{sid}/catalog", json={"name": "Vendite per segmento"})
    assert r.json() == {"name": "Vendite per segmento (2)", "folder": "Live Insight - test"}
    saved = FakeMcp.saved[0]
    assert saved["parentId"] == "cartella-test" and "id" not in saved and saved["userApproved"] is True
    assert saved["name"] == "Vendite per segmento (2)" and saved["type"] == "workbooks"
    views = [v for v in saved["content"]["json"]["views"]["children"] if v["type"] == "saw:pluginView"]
    assert [v["viewCaption"]["caption"]["text"] for v in views] == ["Corporate: 36% delle vendite"]
    assert client.post(f"/api/sessions/{sid}/catalog", json={"name": "Nuovo"}).json()["name"] == "Nuovo"


def test_errori(client):
    assert client.get("/api/sessions/nope").status_code == 404
    assert client.post("/api/sessions", json={"xsa": "XSA('altro'.'x')"}).status_code == 404
    sid = client.post("/api/sessions", json={"xsa": XSA}).json()["id"]
    assert client.post(f"/api/sessions/{sid}/workbook", json={"name": "x"}).status_code == 422   # nessun visual fissato
    assert client.post(f"/api/sessions/{sid}/spec", json={"name": "x"}).json()["detail"]["key"] == "errors.noPinnedVisuals"
    assert client.patch(f"/api/sessions/{sid}/visuals/9", json={"pinned": True}).status_code == 404


def test_senza_token_serve_il_login(tmp_path):
    config = Config(settings=Settings(oac_url="https://oac.example.com"), dev_tokens=tmp_path / "manca.json", public_url="http://localhost:5173")
    c = TestClient(create_app(config, make_mcp=FakeMcp, model_factory=lambda *a, **k: ScriptedModel()))
    assert c.get("/api/me").status_code == 401
    r = c.get("/api/auth/login", follow_redirects=False)
    location = urllib.parse.urlsplit(r.headers["location"])
    assert location.netloc == "oac.example.com" and location.path == "/ui/dv/ui/api/v1/tokens/token"
    redirect = urllib.parse.parse_qs(location.query)["redirect_uri"][0]         # la forma di oac-mcp-connect
    assert re.fullmatch(r"http://127\.0\.0\.1:8000/oac-mcp-connect/callback/[0-9a-f]{64}", redirect)

    tokens = {"access_token": fake_jwt("Utente Web"), "refresh_token": "r", "expires_in": 3600}
    assert c.post("/oac-mcp-connect/callback/" + "0" * 64, data=tokens).status_code == 400   # login mai avviato
    r = c.post(urllib.parse.urlsplit(redirect).path, data=tokens, follow_redirects=False)
    assert r.status_code == 200 and COOKIE not in r.cookies                    # il cookie va sull'origine della UI
    # pagina e non redirect: la CSP form-action della pagina di OAC bloccherebbe un 303 verso un'altra origine
    target = re.search(r'content="0;url=([^"]+)"', r.text).group(1)
    assert target.startswith("http://localhost:5173/api/auth/finish?code=")
    finish = urllib.parse.urlsplit(target)
    assert c.post(urllib.parse.urlsplit(redirect).path, data=tokens).status_code == 400      # nonce monouso
    r = c.get(f"{finish.path}?{finish.query}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "http://localhost:5173/" and COOKIE in r.cookies
    assert c.get(f"{finish.path}?{finish.query}", follow_redirects=False).status_code == 400  # codice monouso
    assert c.get("/api/me").json()["name"] == "Utente Web"
    c.post("/api/auth/logout")
    assert c.get("/api/me").status_code == 401


def test_rinnovo_dei_token(monkeypatch):
    refreshed = []

    def fake_refresh(url, content, headers, timeout):
        if content == "rifiutato":
            raise httpx.ConnectError("OAC non raggiungibile")
        refreshed.append(content)
        return httpx.Response(200, json={"accessToken": fake_jwt(minutes=60), "refreshToken": content + "+",
                                         "expiresIn": 3600}, request=httpx.Request("POST", url))
    monkeypatch.setattr(oac_auth.httpx, "post", fake_refresh)

    def user(minutes, refresh="r", age=0):
        token = OacToken("https://oac.example.com", fake_jwt(minutes=minutes), refresh)
        return User(token, FakeMcp(None, token), "Utente", since=time.time() - age)

    users = {"in scadenza": user(2), "fresco": user(50, refresh="f"), "scaduto": user(-1),
             "vecchio": user(50, age=USER_MAX_AGE + 1), "rete giù": user(2, refresh="rifiutato")}
    assert sorted(renew_tokens(users)) == ["scaduto", "vecchio"]
    assert refreshed == ["r"]                                  # solo quello a meno di 5 minuti dalla scadenza
    assert users["in scadenza"].token.seconds_left() > 55 * 60 and users["in scadenza"].token.refresh_token == "r+"
    assert "rete giù" in users                                  # si riprova al giro dopo, finché il token vale


def test_il_rinnovo_parte_e_si_ferma_con_l_app(client):
    running = lambda: any(t.name == "oac-token-renewal" for t in threading.enumerate())
    with client:
        assert running()
    assert not running()


def test_an_oac_html_page_becomes_a_message(client, monkeypatch):
    # 25/9: the instance paused by the administrator answers every MCP call with an HTML page
    from liveinsight.platforms.oac.mcp import McpError
    page = ('HTTP 403: <!DOCTYPE html>\n<html lang="en">\n  <head>\n    <title>Instance Suspended</title>\n  </head>\n'
            '  <body>\n\n    <h1>Your Analytics service is currently paused by your system administrator.</h1>\n')

    def paused(*_, **__):
        raise McpError(page)
    monkeypatch.setattr("liveinsight.platforms.oac.platform.list_datasets", paused)
    r = client.get("/api/datasets")
    assert r.status_code == 502 and r.json()["detail"] == {"key": "errors.oacPage", "params": {
        "status": "403", "text": "Your Analytics service is currently paused by your system administrator."}}


def test_an_expired_oac_token_asks_for_a_new_sign_in(client, monkeypatch):
    from liveinsight.platforms.oac.auth import TokenExpired

    def expired(*_, **__):
        raise TokenExpired("access token expired")
    monkeypatch.setattr("liveinsight.platforms.oac.platform.list_datasets", expired)
    r = client.get("/api/datasets")
    assert r.status_code == 401 and r.json()["detail"]["key"] == "errors.oacSessionExpired"


def pinned_session(client):
    sid = client.post("/api/sessions", json={"xsa": XSA}).json()["id"]
    sse(client.post(f"/api/sessions/{sid}/ask", json={"question": "chi vende di più?"}).text)
    client.patch(f"/api/sessions/{sid}/visuals/1", json={"pinned": True})
    return sid


@needs_skeleton
def test_the_dva_download_with_a_skeleton(client):
    assert client.get("/api/me").json()["dva_download"] is True
    r = client.post(f"/api/sessions/{pinned_session(client)}/workbook", json={"name": "Vendite per segmento"})
    assert r.status_code == 200 and "Vendite%20per%20segmento.dva" in r.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert not [n for n in names if n.startswith("datasets/embedded") and not n.endswith("/")]


def test_without_a_skeleton_the_dva_download_is_off(tmp_path):
    tokens = tmp_path / "tokens.json"
    tokens.write_text(json.dumps({"accessToken": fake_jwt(), "refreshToken": "r", "expiresIn": 3600}))
    config = Config(settings=Settings(oac_url="https://oac.example.com"), dev_tokens=tokens, skeleton=None)
    client = TestClient(create_app(config, make_mcp=FakeMcp, model_factory=lambda *a, **k: ScriptedModel()))
    assert client.get("/api/me").json()["dva_download"] is False
    sid = pinned_session(client)
    r = client.post(f"/api/sessions/{sid}/workbook", json={"name": "x"})
    assert r.status_code == 422 and r.json()["detail"]["key"] == "errors.dvaSkeletonMissing"
    FakeMcp.saved.clear()
    assert client.post(f"/api/sessions/{sid}/catalog", json={"name": "x"}).status_code == 200    # the catalog still works
