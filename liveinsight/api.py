"""The MVP web backend (M6): FastAPI, one user at a time locally, sessions in memory. Block 1 of
docs/ARCHITETTURA.md: it wires the engine (Session) to the platform (OacPlatform) for the UI.

Start:  uv run uvicorn liveinsight.api:app --port 8000     (`npm run dev` does it too)

Authentication: the OAC user's OAuth token, never a technical account.
- sign-in: GET /api/auth/login redirects to OAC with a redirect_uri shaped like oac-mcp-connect's,
  http://127.0.0.1:8000/oac-mcp-connect/callback/<nonce> (with localhost:5173/api/auth/callback OAC answers
  "service paused"). OAC POSTs the tokens to the backend; the backend answers with a page that leads to the UI
  (PUBLIC_URL) with a one-time code, and /api/auth/finish sets the cookie on the UI origin;
- development: if the OAC_TOKENS file exists and nobody signed in, it is used (user "dev").
- renewal: OAC's refresh authenticates with the still-valid access token (60 minutes), so an hour without requests
  would lose the session. A backend thread renews tokens every minute (renew_tokens) and removes the users whose
  token expired or whose sign-in is older than the cookie (USER_MAX_AGE).

Errors the app shows are messages ({key, params}) the UI writes in its language (liveinsight.messages).

Endpoints (JSON, unless stated):
  GET  /api/settings                            settings (without keys: only whether they are set) and proposed models
  PUT  /api/settings {oac_url, model, api_key?, oci_compartment?}  saves them in .secrets/settings.json; new OAC URL = new sign-in
  POST /api/settings/verify {model, api_key?, oci_compartment?}   a test call: does the model use tools?
  GET  /api/me                                  user, token expiry, model (503 without an OAC URL)
  GET  /api/datasets?search=                    visible datasets: name, xsa, folder
  POST /api/sessions {xsa}                      new chat session on a dataset
  GET  /api/sessions/{sid}                      state: messages, proposed visuals, order of the pinned ones, cost
  POST /api/sessions/{sid}/ask {question}       SSE: event query|visual|followups|error, then done (or failed)
  PATCH /api/sessions/{sid}/visuals/{n} {pinned?, title?}
  PUT  /api/sessions/{sid}/order {order}        order of the pinned visuals
  POST /api/sessions/{sid}/spec {name}          the WorkbookSpec JSON of the pinned visuals (the engine's output)
  POST /api/sessions/{sid}/workbook {name}      the .dva to download (application/octet-stream)
  POST /api/sessions/{sid}/catalog {name}       saves the workbook in OAC, folder "Live Insight - test": {name, folder}
"""
import html
import json
import logging
import os
import queue
import re
import secrets
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from liveinsight.engine.platform import StructuralError
from liveinsight.engine.session import NUDGE, Session
from liveinsight.llm import KEY_OPTIONAL, PROVIDERS, make_model, models_for, oci_region, probe
from liveinsight.messages import UiError, msg
from liveinsight.platforms.oac.auth import OacToken, TokenExpired, jwt_claims, login_url
from liveinsight.platforms.oac.catalog import safe_filename
from liveinsight.platforms.oac.mcp import McpError, OacMcp
from liveinsight.platforms.oac.platform import OacPlatform
from liveinsight.platforms.oac.target import SpecError
from liveinsight.settings import Settings, SettingsError

ROOT = Path(__file__).resolve().parents[1]
COOKIE = "li_user"
USER_MAX_AGE = 12 * 3600                                     # sign-in cookie lifetime
RENEW_EVERY = 60                                             # seconds between two token renewal rounds
LOGIN_TIMEOUT = 600                                          # seconds to complete the sign-in on OAC
HANDOFF_TIMEOUT = 60                                         # seconds to go from the callback to the UI
log = logging.getLogger("liveinsight.api")


@dataclass
class User:
    token: OacToken
    mcp: OacMcp
    name: str
    dev: bool = False
    sessions: dict = field(default_factory=dict)             # sid -> Session
    locks: dict = field(default_factory=dict)                # sid -> Lock (one question at a time)
    platform: OacPlatform | None = None                      # both faces of the OAC adapter, on this user's MCP
    since: float = field(default_factory=time.time)          # sign-in time


def renew_tokens(users: dict, now: float | None = None) -> list[str]:
    """One renewal round: renews the tokens close to expiry, removes the users no longer valid.

    A failed refresh with the token still valid (network, OAC briefly down) is retried at the next round; the user
    is removed only when the token expired, and then a new sign-in is needed. Returns the removed ids.
    """
    now = time.time() if now is None else now
    dropped = []
    for uid, user in list(users.items()):
        if not user.dev and now - user.since > USER_MAX_AGE:
            dropped.append(uid)
            continue
        try:
            user.token.current()
        except Exception as e:                               # never the token in the logs: only the error type
            log.warning("rinnovo del token OAC di %s non riuscito (%s)", user.name, type(e).__name__)
            if user.token.seconds_left() <= 0:
                dropped.append(uid)
    for uid in dropped:
        users.pop(uid, None)
    return dropped


def default_skeleton() -> Path | None:
    """SKELETON_DVA, else .secrets/skeleton.dva, else the development one in dva-lab/templates (if present)."""
    if os.environ.get("SKELETON_DVA"):
        return ROOT / os.environ["SKELETON_DVA"]
    for path in (ROOT / ".secrets/skeleton.dva", ROOT / "dva-lab/templates/M2 - senza dati.dva"):
        if path.exists():
            return path
    return None


@dataclass
class Config:
    settings: Settings = field(default_factory=Settings)     # OAC URL, model, keys (Settings page)
    settings_path: Path | None = None                        # where they are saved; None = not saved (tests)
    public_url: str = "http://localhost:5173"                # UI origin (Vite proxy)
    callback_url: str = "http://127.0.0.1:8000"              # the backend as the browser reaches it (port 3000-9000)
    dev_tokens: Path | None = None
    skeleton: Path | None = None                             # empty .dva from the user's OAC: enables the download
    template_names: tuple = ()                               # () = bundled templates; else catalog workbook names

    @property
    def oac_url(self) -> str | None:
        return self.settings.oac_url

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env")
        tokens = os.environ.get("OAC_TOKENS")
        path = ROOT / os.environ.get("SETTINGS_FILE", ".secrets/settings.json")
        return cls(settings=Settings.load(path), settings_path=path,
                   public_url=os.environ.get("PUBLIC_URL", cls.public_url),
                   callback_url=os.environ.get("CALLBACK_URL", cls.callback_url),
                   dev_tokens=ROOT / tokens if tokens else None,
                   skeleton=default_skeleton(),
                   template_names=tuple(n.strip() for n in os.environ.get("TEMPLATE_WORKBOOKS", "").split(",") if n.strip()))


class ModelChoice(BaseModel):
    model: str = Field(max_length=120)
    api_key: str | None = Field(default=None, max_length=500)   # of the model's provider; empty = unchanged
    oci_compartment: str | None = Field(default=None, max_length=200)
    custom_base_url: str | None = Field(default=None, max_length=300)   # the custom provider's server


class SettingsUpdate(ModelChoice):
    oac_url: str = Field(max_length=300)


class NewSession(BaseModel):
    xsa: str


class Ask(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class VisualPatch(BaseModel):
    pinned: bool | None = None
    title: str | None = Field(default=None, min_length=1, max_length=120)


class Order(BaseModel):
    order: list[int]


class WorkbookRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def display_name(token: OacToken) -> str:
    claims = jwt_claims(token.access_token)
    return claims.get("user_displayname") or "utente OAC"


def create_app(config: Config, make_mcp=OacMcp, model_factory=make_model) -> FastAPI:
    users: dict[str, User] = {}

    @asynccontextmanager
    async def lifespan(_):
        stop = threading.Event()

        def loop():
            while not stop.wait(RENEW_EVERY):
                renew_tokens(users)
        thread = threading.Thread(target=loop, name="oac-token-renewal", daemon=True)
        thread.start()
        yield
        stop.set()
        thread.join(timeout=5)

    app = FastAPI(title="Live Insight", lifespan=lifespan)

    def detail(e: Exception):
        """What an error says to the app: a message for UiError, else its (English) text."""
        return e.message() if isinstance(e, UiError) else str(e)

    def ui_error(status: int, key: str, **params) -> HTTPException:
        """An error the app shows: a message the frontend writes in the UI language (liveinsight.messages)."""
        return HTTPException(status, msg(key, **params))

    def open_user(token: OacToken, dev=False) -> User:
        mcp = make_mcp(config.oac_url, token)
        mcp.initialize()
        return User(token, mcp, display_name(token), dev,
                    platform=OacPlatform(mcp, config.skeleton, config.template_names))

    def current_user(request: Request) -> User:
        if not config.oac_url:
            raise ui_error(503, "errors.oacUrlMissing")
        user = users.get(request.cookies.get(COOKIE, ""))
        if user:
            return user
        if config.dev_tokens and config.dev_tokens.exists():
            if "dev" not in users:
                users["dev"] = open_user(OacToken.from_file(config.oac_url, config.dev_tokens), dev=True)
            return users["dev"]
        raise ui_error(401, "errors.signInRequired")

    def session_of(sid: str, user: User = Depends(current_user)) -> Session:
        s = user.sessions.get(sid)
        if s is None:
            raise ui_error(404, "errors.sessionNotFound")
        return s

    @app.exception_handler(McpError)
    def mcp_error(_, e: McpError):
        status = 401 if "HTTP 401" in str(e) else 502
        text = str(e)
        if "<html" in text.lower():                          # an HTML page from OAC (25/9: "Instance Suspended")
            page = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S | re.I) or re.search(r"<title>(.*?)</title>", text, re.S | re.I)
            http = re.match(r"HTTP (\d+)", text)
            return JSONResponse({"detail": msg("errors.oacPage", status=http.group(1) if http else "",
                                               text=" ".join(page.group(1).split()) if page else "")},
                                status_code=status)
        return JSONResponse({"detail": f"OAC: {text}"}, status_code=status)

    @app.exception_handler(TokenExpired)
    def token_expired(_, e: TokenExpired):
        return JSONResponse({"detail": msg("errors.oacSessionExpired")}, status_code=401)

    # -- settings -----------------------------------------------------------------------
    def may_edit_settings(request: Request):
        """At first start (no OAC URL) anyone opening the app locally; then only who has access to the app."""
        if config.oac_url and request.cookies.get(COOKIE, "") not in users and not (
                config.dev_tokens and config.dev_tokens.exists()):
            raise ui_error(401, "errors.signInRequired")

    def settings_view() -> dict:
        return config.settings.public() | {
            "oci_region": oci_region(), "keyless": sorted(KEY_OPTIONAL),
            "providers": [{"id": p, "label": label, "models": models_for(p)} for p, label in PROVIDERS.items()]}

    @app.get("/api/settings", dependencies=[Depends(may_edit_settings)])
    def get_settings():
        return settings_view()

    @app.put("/api/settings", dependencies=[Depends(may_edit_settings)])
    def put_settings(body: SettingsUpdate):
        try:
            new = config.settings.updated(body.oac_url, body.model, body.api_key, body.oci_compartment,
                                          body.custom_base_url)
        except SettingsError as e:
            raise HTTPException(422, e.message())
        if config.settings_path:
            new.save(config.settings_path)
        if new.oac_url != config.oac_url:                    # tokens belong to one instance: new sign-in
            users.clear()
            pending.clear()
            handoffs.clear()
        config.settings = new
        return settings_view()

    @app.post("/api/settings/verify", dependencies=[Depends(may_edit_settings)])
    def verify_model(body: ModelChoice):
        """Tries the model chosen in the form (even if not saved yet): one call with a tool."""
        try:
            s = config.settings.with_model(body.model, body.api_key, body.oci_compartment, body.custom_base_url)
            model = model_factory(s.model, s.keys, oci_compartment=s.oci_compartment, base_url=s.custom_base_url)
        except UiError as e:                                 # SettingsError included
            raise HTTPException(422, e.message())
        api_name = {"OCINative": "OCI native API", "OpenAIResponses": "Responses API", "OpenAIChat": "Chat Completions"}
        return probe(model) | {"api": api_name.get(type(model).__name__, type(model).__name__)}

    # -- authentication -------------------------------------------------------------------
    pending: dict[str, float] = {}                           # nonces of started sign-ins -> time
    handoffs: dict[str, tuple[str, float]] = {}              # one-time code -> (user, time)

    def fresh(d: dict, key: str, timeout: int, when=lambda v: v):
        """Removes and returns the entry if it exists and has not expired; meanwhile clears the expired ones."""
        now = time.time()
        for k in [k for k, v in d.items() if now - when(v) > timeout]:
            d.pop(k, None)
        return d.pop(key, None)

    @app.get("/api/auth/login")
    def login():
        if not config.oac_url:
            return RedirectResponse(f"{config.public_url}/")    # the UI opens Settings
        nonce = secrets.token_hex(32)
        pending[nonce] = time.time()
        return RedirectResponse(login_url(config.oac_url, f"{config.callback_url}/oac-mcp-connect/callback/{nonce}"))

    @app.post("/oac-mcp-connect/callback/{nonce}")
    def callback(nonce: str, access_token: str = Form(), refresh_token: str = Form(), expires_in: int = Form()):
        if fresh(pending, nonce, LOGIN_TIMEOUT) is None:
            raise HTTPException(400, "sign-in not started by Live Insight, or expired: try again")   # a browser page, not the app
        user_id = secrets.token_urlsafe(24)
        users[user_id] = open_user(OacToken(config.oac_url, access_token, refresh_token))
        code = secrets.token_urlsafe(24)
        handoffs[code] = (user_id, time.time())
        # No redirect: OAC's page has the CSP "form-action <callback>", which Chrome also applies to redirects
        # after the form is posted, and it would block the 303 to the UI origin. A page with a meta refresh is a
        # new navigation, outside OAC's CSP.
        url = html.escape(f"{config.public_url}/api/auth/finish?code={code}")
        return HTMLResponse(f'<!doctype html><meta charset=utf-8><meta http-equiv="refresh" content="0;url={url}">'
                            f'<title>Live Insight</title><p style="font:16px system-ui;margin:3em">'
                            f'<a href="{url}">Live Insight →</a></p>')           # no words: the language is the app's

    @app.get("/api/auth/finish")
    def finish(code: str):
        found = fresh(handoffs, code, HANDOFF_TIMEOUT, when=lambda v: v[1])
        if found is None:
            raise HTTPException(400, "invalid or expired sign-in code: sign in again")   # a browser page, not the app
        response = RedirectResponse(f"{config.public_url}/", status_code=303)
        response.set_cookie(COOKIE, found[0], httponly=True, samesite="lax", max_age=USER_MAX_AGE)
        return response

    @app.post("/api/auth/logout")
    def logout(request: Request):
        users.pop(request.cookies.get(COOKIE, ""), None)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE)
        return response

    @app.get("/api/me")
    def me(user: User = Depends(current_user)):
        return {"name": user.name, "dev": user.dev, "token_minutes_left": round(user.token.seconds_left() / 60),
                "model": config.settings.model,                         # None: the UI opens Settings
                "dva_download": user.platform.target().can_package}           # the UI disables the button without it

    # -- datasets and sessions -----------------------------------------------------------------
    @app.get("/api/datasets")
    def datasets(search: str = "*", user: User = Depends(current_user)):
        return user.platform.datasets(search)

    @app.post("/api/sessions")
    def new_session(body: NewSession, user: User = Depends(current_user)):
        found = [d for d in user.platform.datasets() if d["xsa"] == body.xsa]
        if not found:
            raise ui_error(404, "errors.datasetNotVisible")
        try:                                                 # the model applies to new chats
            model = model_factory(config.settings.model, config.settings.keys,
                                  oci_compartment=config.settings.oci_compartment, base_url=config.settings.custom_base_url)
        except UiError as e:                                 # missing key, unknown provider
            raise HTTPException(422, e.message())
        source = user.platform.source(body.xsa, found[0]["name"])
        ds = source.dataset
        sid = uuid.uuid4().hex[:12]
        user.sessions[sid] = Session(source, model)
        user.locks[sid] = threading.Lock()
        return {"id": sid, "dataset": found[0], "columns": [
            {"name": n, "type": c.get("columnType"), "dataType": c.get("dataType")} for n, c in ds.columns.items()]}

    def state(s: Session) -> dict:
        chat = [{"role": m.role, "text": m.content, "followups": s.followups.get(i, [])} for i, m in enumerate(s.messages)
                if (m.role == "user" and m.content != NUDGE) or (m.role == "assistant" and m.content and not m.tool_calls)]
        return {"dataset": s.dataset_name, "messages": chat, "visuals": [s.proposal_json(p) for p in s.proposals],
                "order": s.order, "cost": s.cost(), "currency": s.model.price.currency if s.model.price else None,
                "usage": vars(s.usage)}

    @app.get("/api/sessions/{sid}")
    def get_session(s: Session = Depends(session_of)):
        return state(s)

    @app.post("/api/sessions/{sid}/ask")
    def ask(sid: str, body: Ask, s: Session = Depends(session_of), user: User = Depends(current_user)):
        lock = user.locks[sid]
        if not lock.acquire(blocking=False):
            raise ui_error(409, "errors.questionRunning")
        events = queue.Queue()

        def on_event(e):
            detail = s.proposal_json(s.proposal(e.detail["n"])) if e.kind == "visual" else e.detail
            events.put((e.kind, detail))

        def work():
            start = len(s.messages)                          # to cancel the question if the provider fails
            try:
                turn = s.ask(body.question, on_event=on_event)
                events.put(("done", {"text": turn.text, "seconds": round(turn.seconds, 1), "steps": turn.steps,
                                     "followups": turn.followups, "notice": turn.notice,
                                     "cost": s.model.price.cost(turn.usage) if s.model.price else None,
                                     "currency": s.model.price.currency if s.model.price else None}))
            except Exception as e:                           # provider error: the question is cancelled
                s.messages = s.messages[:start]
                events.put(("failed", {"error": f"{type(e).__name__}: {e}"}))
            finally:
                lock.release()
                events.put(None)

        threading.Thread(target=work, daemon=True).start()

        def stream():
            while (item := events.get()) is not None:
                kind, data = item
                yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    # -- visuals and workbook ------------------------------------------------------------------
    @app.patch("/api/sessions/{sid}/visuals/{n}")
    def patch_visual(n: int, body: VisualPatch, s: Session = Depends(session_of)):
        try:
            p = s.proposal(n)
            if body.title:
                p.visual = p.visual.model_copy(update={"title": body.title})
            if body.pinned is True:
                s.pin(n)
            elif body.pinned is False:
                s.unpin(n)
        except ValueError as e:
            raise HTTPException(404, detail(e))
        return {"visual": s.proposal_json(p), "order": s.order}

    @app.put("/api/sessions/{sid}/order")
    def put_order(body: Order, s: Session = Depends(session_of)):
        try:
            s.reorder(body.order)
        except ValueError as e:
            raise HTTPException(422, detail(e))
        return {"order": s.order}

    def spec_json(s: Session, name: str) -> str:
        """The engine's output for the platform: the WorkbookSpec JSON of the pinned visuals, nothing else."""
        try:
            return s.pinned_spec(name).model_dump_json()
        except ValueError as e:
            raise HTTPException(422, detail(e))

    @app.post("/api/sessions/{sid}/catalog")
    def save_to_catalog(body: WorkbookRequest, s: Session = Depends(session_of), user: User = Depends(current_user)):
        spec = spec_json(s, body.name)
        try:
            return user.platform.target().publish(spec)
        except SpecError as e:
            raise HTTPException(422, detail(e))
        except StructuralError as e:
            raise ui_error(500, "errors.structuralChecks", problems="; ".join(e.problems))
        except TokenExpired:                                 # the app asks for a new sign-in (handler above)
            raise
        except (UiError, LookupError, RuntimeError) as e:
            raise HTTPException(502, detail(e))

    @app.post("/api/sessions/{sid}/workbook")
    def workbook(body: WorkbookRequest, s: Session = Depends(session_of), user: User = Depends(current_user)):
        spec = spec_json(s, body.name)
        out = Path(tempfile.mkdtemp(prefix="li-")) / f"{safe_filename(body.name)}.dva"
        try:
            user.platform.target().package(spec, out)
        except (SpecError, UiError) as e:                   # UiError: no skeleton .dva configured
            raise HTTPException(422, detail(e))
        except StructuralError as e:
            raise ui_error(500, "errors.structuralChecks", problems="; ".join(e.problems))
        return FileResponse(out, filename=out.name, media_type="application/octet-stream")

    @app.post("/api/sessions/{sid}/spec")
    def workbook_spec(body: WorkbookRequest, s: Session = Depends(session_of)):
        """The WorkbookSpec JSON of the pinned visuals: what the engine hands to a platform (docs/ARCHITETTURA.md)."""
        try:
            spec = s.pinned_spec(body.name)
        except ValueError as e:
            raise HTTPException(422, detail(e))
        return Response(spec.model_dump_json(indent=2), media_type="application/json", headers={
            "Content-Disposition": f'attachment; filename="{safe_filename(body.name)}.workbook-spec.json"'})

    @app.get("/api/health")
    def health():
        return {"ok": True, "time": time.time()}

    return app


app = create_app(Config.from_env())
