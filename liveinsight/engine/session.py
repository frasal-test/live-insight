"""A chat session with the data: state in memory, the model's tools, the agentic loop.

The model never writes platform JSON. It has three tools:
- run_query: a query in the platform's dialect with {Column name} and {dataset}, run by the DataSource;
- propose_visual: a VisualSpec with its columns. The code validates it, and the DataSource runs the preview query
  with the SAME expressions the workbook will have, keeping the rows;
- suggest_followups: 2-3 imperative follow-ups, ready to send: buttons in the UI.
The user pins the visuals they like; the workbook comes from those (WorkbookSpec JSON), with no LLM.
"""
import json
import re
import time
from dataclasses import dataclass, field

from pydantic import TypeAdapter, ValidationError

from liveinsight.engine.platform import DataSource, QueryError
from liveinsight.engine.prompt import system_prompt
from liveinsight.engine.spec import (ROLES, Calculation, CanvasSpec, ColumnSpec, DatasetRef, DateColumn, VisualSpec,
                                     WorkbookSpec, check_against)
from liveinsight.llm import ChatModel, Message, Tool, Usage
from liveinsight.messages import UiError, token

MAX_STEPS = 12              # model calls for one question
EMPTY_RETRIES = 2           # empty answers (no text, no tools) retried: Gemini on OCI, 24/9
# What the model reads is in English (25/9); the answer stays in the user's language.
NUDGE = "(The previous answer was empty: answer the user with the numbers you found, in the user's language.)"
# Notes kept in the history when a turn ends badly; the UI shows its own text for Turn.notice.
NOTICES = {"empty": "(the model did not write an answer)", "tooManySteps": "(stopped: too many steps for one question)",
           "refused": "(the model refused the request)", "truncated": "(answer cut: token limit reached)"}
FOLLOWUPS_NUDGE = ("(System reminder: call suggest_followups with 2-3 follow-ups for the answer you just gave. "
                   "Do not rewrite the answer.)")
ROWS_TO_MODEL = 50          # rows of a result sent back to the model

COLUMN = TypeAdapter(ColumnSpec)


def portable_schema(schema: dict) -> dict:
    """The tool schema without $ref, discriminator, const, title and anyOf with null.

    OpenAI resolves references before the model; the gpt-oss renderer (harmony, json_schema_to_typescript in
    openai/harmony) does not: a {"$ref": …} becomes `any` and the model invents the column fields (OCI, 24/9: 43
    propose_visual errors out of 45). The expanded schema says the same to all; validation stays Pydantic.
    """
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(defs[node["$ref"].rsplit("/", 1)[1]])
        if "anyOf" in node:                               # Pydantic's Optional[X]: X stays, the field is optional
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            if len(options) == 1:
                return walk({**options[0], **{k: v for k, v in node.items() if k not in ("anyOf", "default")}})
        out = {}
        for k, v in node.items():
            if k in ("$defs", "discriminator", "title") or (k == "default" and v is None):
                continue
            if k == "const":                              # the gpt-oss renderer reads enum, not const
                out["enum"] = [v]
            elif k == "properties":                       # field names are not schemas: no cleaning on the keys
                out[k] = {name: walk(sub) for name, sub in v.items()}
            else:
                out[k] = walk(v)
        return out
    return walk(schema)


NOT_IMPERATIVE = {"vuoi", "volete", "posso", "possiamo", "potrei", "potresti", "desideri", "ti", "sarebbe", "hai",
                  "preferisci", "che", "quale", "quali", "come", "quanto", "quanti", "perché",
                  "would", "do", "does", "can", "could", "shall", "should", "may", "want", "what", "which", "how",
                  "why", "is", "are"}


def check_followups(items) -> list[str]:
    """Valid follow-ups: 2-3 short sentences in the user's voice, imperative, ready to send; no questions.
    Italian and English words that start a question or an offer are refused."""
    if not isinstance(items, list) or not 2 <= len(items) <= 3:
        raise QueryError("2 or 3 follow-ups are needed")
    out = []
    for raw in items:
        text = str(raw).strip().rstrip(".")
        first = text.split()[0].casefold().strip("«\"'") if text else ""
        if not 8 <= len(text) <= 140 or "?" in text or first in NOT_IMPERATIVE:
            raise QueryError(f"invalid follow-up: {text!r}. Write the user's requests in the imperative, ready to "
                             f"send, without '?' and at most 140 characters (e.g. 'Show the monthly trend of "
                             f"Technology in 2016'), in the user's language")
        out.append(text)
    return out


FOLLOWUPS_IN_TEXT = re.compile(r'(suggest_followups\s*\(\s*)?\{\s*"followups"\s*:')


def followups_in_text(content: str) -> tuple[list[str] | None, str]:
    """Follow-ups written in the answer text, as {"followups": [...]} or suggest_followups({...}), possibly in a
    ```json fence: (valid follow-ups, text without them), or (None, content) if there are none or they are invalid."""
    m = FOLLOWUPS_IN_TEXT.search(content)
    if not m:
        return None, content
    try:
        obj, end = json.JSONDecoder().raw_decode(content, content.index("{", m.start()))
        items = check_followups(obj.get("followups"))
    except (ValueError, AttributeError):                   # QueryError is a ValueError
        return None, content
    if m.group(1):                                          # the closing parenthesis of suggest_followups(...)
        end += len(re.match(r"\s*\)?", content[end:]).group())
    text = content[:m.start()] + content[end:]
    text = re.sub(r"```(?:json)?\s*```", "", text)             # the fence left empty
    text = re.sub(r"(\s*\n\s*[—–-]+\s*)+$", "", text.rstrip())   # a separator line left at the end
    return items, text.strip()


FOLLOWUPS_HEADING = re.compile(
    r"\n(?:[ \t]*[-—–_*]{3,}[ \t]*\n)?[ \t]*(?:#+[ \t]*|\*\*)?[^\n]{0,40}?"
    r"\b(?:suggeriment\w*|spunt[io]\w*|approfondiment\w*|suggerisco|suggestions?|follow[- ]?ups?|next steps?)\b"
    r"[^\n]{0,40}\n", re.I)
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.+?)\s*$")


def followups_list_in_text(content: str) -> tuple[list[str] | None, str | None]:
    """A section of follow-ups written at the end of the answer as a list ("**Suggerimenti**" and bullets), besides
    or instead of the tool (gpt-oss, golden set 25/9). Returns (valid follow-ups or None, text without the section),
    or (None, None) when the answer does not end with such a section."""
    matches = list(FOLLOWUPS_HEADING.finditer(content))
    if not matches:
        return None, None
    m = matches[-1]
    lines = [line for line in content[m.end():].splitlines() if line.strip()]
    bullets = [BULLET.match(line) for line in lines]
    closing = lines[-1].strip().startswith("(") if lines else False    # "(Se vuoi un altro approfondimento…)"
    if not lines or not all(bullets[:-1]) or not (bullets[-1] or closing) or not any(bullets):
        return None, None
    items = [b.group(1).strip().strip('“”"«»').rstrip(".").strip() for b in bullets if b]
    try:
        valid = check_followups(items[:3])
    except QueryError:
        valid = None
    text = re.sub(r"(\s*\n\s*[-—–_*]{3,}\s*)+$", "", content[:m.start()].rstrip())    # a separator left before it
    return valid, text.rstrip()


def visual_rows_for_model(p) -> dict:
    """The visual rows sent back to the model. The query orders them by attribute (alphabetically, as OAC shows
    them): cut at ROWS_TO_MODEL, the model answered "which city sells the most" with the best of the first 50
    in alphabetical order (golden set 25/9). Beyond the limit it gets the rows with the highest values of the
    first measure (lowest if the visual sorts ascending), and a note says so."""
    rows = p.rows
    note = "preview shown to the user; the user can pin it to the workbook"
    measure = (p.visual.roles.get("measures") or p.visual.roles.get("size") or [None])[0]
    if len(rows) <= ROWS_TO_MODEL:
        return {"rows": rows, "note": note}
    if measure is None:
        return {"rows": rows[:ROWS_TO_MODEL], "note": f"{note}; first {ROWS_TO_MODEL} rows of {len(rows)}"}
    ascending = p.visual.sort == "asc"
    ranked = sorted((r for r in rows if isinstance(r.get(measure), (int, float))),
                    key=lambda r: r[measure], reverse=not ascending)
    which = "lowest" if ascending else "highest"
    return {"rows": ranked[:ROWS_TO_MODEL],
            "note": f"{note}; {len(rows)} rows: here the {ROWS_TO_MODEL} with the {which} {measure}"}


@dataclass
class Proposal:
    n: int
    visual: VisualSpec
    columns: list                       # ColumnSpecs used, in query order
    query: str
    rows: list[dict]                    # keys = column ids
    pinned: bool = False
    filter_columns: list = field(default_factory=list)   # columns used only by filters: in the workbook, not in the SELECT


@dataclass
class Event:
    kind: str                           # "query", "visual", "error", "text"
    detail: dict = field(default_factory=dict)


@dataclass
class Turn:
    text: str
    events: list[Event]
    usage: Usage
    seconds: float
    steps: int
    followups: list[str] = field(default_factory=list)
    notice: str | None = None           # empty | tooManySteps | refused | truncated: the UI says it in its language


class Session:
    def __init__(self, source: DataSource, model: ChatModel):
        """source: the data, on whatever platform serves them (liveinsight.engine.platform)."""
        self.source, self.model = source, model
        self.ds, self.dataset_name = source.dataset, source.ref.name
        self.system = system_prompt(self.dataset_name, self.ds, source.query_guide)
        self.messages: list[Message] = []
        self.columns: dict[str, object] = {}
        self.proposals: list[Proposal] = []
        self.order: list[int] = []          # pinned visuals, in workbook order
        self.usage = Usage()
        self.followups: dict[int, list[str]] = {}   # index of the final assistant message -> follow-ups
        self._turn_followups: list[str] = []
        self.tools = [
            Tool("run_query", source.query_tool,
                 {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
            # The order follows the flow (query, visual, follow-ups): with follow-ups listed before the visual, gpt-oss
            # ended with query -> follow-ups -> answer and the comparison went into the follow-ups (golden set, 24/9).
            Tool("propose_visual", "Proposes a visual to the user: the system runs its query and shows the preview. "
                 "Call it before the final answer every time the answer compares two or more values (categories, "
                 "periods, rankings, trends), also when the answer names only the first or the last (\"which … the "
                 "most / the least\", \"the best\", \"the worst\"): the visual shows the comparison behind it. The "
                 "user builds the workbook only with the proposed visuals. Not needed for a single number (a total, "
                 "a count, a share).",
                 self._visual_schema()),
            Tool("suggest_followups", "Call it on every question, once, as the last tool before the final answer, "
                 "after propose_visual if needed (also when you decline an off-topic question). Shows the user 2-3 "
                 "follow-ups as buttons: deeper looks born from the data just seen, written as the user's requests "
                 "in the imperative, ready to send, in the user's language (e.g. 'Show the monthly trend of "
                 "Technology in 2016'). Never questions. A follow-up does not replace this answer's visual: if the "
                 "comparison serves the answer, propose it now with propose_visual. Follow-ups never go in the "
                 "answer text, only here.",
                 {"type": "object", "required": ["followups"], "properties": {"followups": {
                     "type": "array", "items": {"type": "string"}, "description": "2 or 3 requests in the imperative"}}}),
        ]

    @staticmethod
    def _column_schema():
        """A column as one object: kind + all the fields, each with the kinds it applies to.

        No oneOf: the gpt-oss renderer does not resolve $ref and Gemini's function declarations have no oneOf
        (24/9, native OCI API: 59 propose_visual errors out of 76 with invented fields, like gpt-oss).
        Everyone reads a flat object; validation of the three kinds stays Pydantic (COLUMN).
        """
        variants = portable_schema(TypeAdapter(ColumnSpec).json_schema())["oneOf"]
        kinds = {v["properties"]["kind"]["enum"][0]: v for v in variants}
        meaning = {"column": "dataset column", "date": "date column at a grain", "calc": "Logical SQL calculation"}
        props = {"kind": {"type": "string", "enum": list(kinds),
                          "description": "; ".join(f"{k}: {v}" for k, v in meaning.items())}}
        for name in dict.fromkeys(p for v in variants for p in v["properties"] if p != "kind"):
            used = [k for k, v in kinds.items() if name in v["properties"]]
            prop = dict(next(v["properties"][name] for v in variants if name in v["properties"]))
            prop.pop("default", None)
            required = [k for k in used if name in kinds[k].get("required", [])]
            note = ("required" if required == used else "optional") + (
                "" if len(used) == len(kinds) else f" for kind {'/'.join(used)}, absent in the others")
            prop["description"] = f"{prop['description']} ({note})" if prop.get("description") else note
            props[name] = prop
        props["id"]["description"] = "column identifier, used in roles (required)"
        return {"type": "object", "required": ["kind", "id"], "properties": props}

    @classmethod
    def _visual_schema(cls):
        column = cls._column_schema()
        return {"type": "object", "required": ["kind", "title", "roles", "columns"],
                "properties": {
                    "kind": {"type": "string", "enum": list(ROLES)},
                    "title": {"type": "string", "description": "says what the data show"},
                    "sort": {"type": "string", "enum": ["desc", "asc"],
                             "description": "bar/hbar with one measure and no color: categories ordered by value"},
                    "roles": {"type": "object", "description": "role -> list of column ids; optional roles are omitted",
                              "additionalProperties": {"type": "array", "items": {"type": "string"}}},
                    "columns": {"type": "array", "items": column,
                                "description": "definition of the columns used in roles and filters"},
                    "filters": {"type": "array", "description": "visual filters (optional): OAC applies them in the "
                                "workbook and the system in the preview", "items": {
                        "type": "object", "required": ["column", "op", "values"], "properties": {
                            "column": {"type": "string", "description": "id of a column defined in columns, "
                                       "even if the visual does not show it"},
                            "op": {"type": "string", "enum": ["in", "between"],
                                   "description": "in: values of an attribute or periods of a date column; "
                                                  "between: minimum and maximum of a measure, or from-to of a "
                                                  "date column with grain day"},
                            "values": {"type": "array", "items": {"type": "string"},
                                       "description": "in: the values ('Technology'; periods '2016', '2016 Q1', "
                                                      "'2016-11', '2016-11-05'); between: two numbers, minimum and "
                                                      "maximum, or two dates '2015-09-01', '2015-11-30'"}}}}}}

    # -- tools -------------------------------------------------------------------
    def run_query(self, query: str):
        rows = self.source.query(query)
        return {"rows": rows[:ROWS_TO_MODEL], "total_rows": len(rows),
                **({"note": f"first {ROWS_TO_MODEL} rows shown"} if len(rows) > ROWS_TO_MODEL else {})}

    def _register(self, raw_columns):
        defined = {}
        for raw in raw_columns:
            if isinstance(raw, dict) and "kind" not in raw:     # models often omit it: deduced from the fields
                raw = {**raw, "kind": "calc" if "expression" in raw else "date" if "grain" in raw else "column"}
            c = COLUMN.validate_python(raw)
            old = self.columns.get(c.id)
            if old is not None and old != c:
                raise QueryError(f"id {c.id} is already used for another column ({old.model_dump()}): "
                                 f"reuse the same definition or choose another id")
            defined[c.id] = c
        return defined

    def propose_visual(self, kind, title, roles, columns, sort=None, filters=None):
        visual = VisualSpec(kind=kind, title=title, roles=roles, sort=sort, filters=filters or [])
        defined = {**self.columns, **self._register(columns)}
        used = list(dict.fromkeys(c for cols in visual.roles.values() for c in cols))
        only_filter = list(dict.fromkeys(f.column for f in visual.filters if f.column not in used))
        missing = [c for c in used + only_filter if c not in defined]
        if missing:
            raise QueryError(f"undefined columns: {missing}")
        spec = WorkbookSpec(dataset=self.dataset_ref(), name="anteprima", columns=[defined[c] for c in used + only_filter],
                            canvases=[CanvasSpec(title="anteprima", visuals=[visual])])
        errors = check_against(spec, self.ds)
        if errors:
            raise QueryError("; ".join(errors))
        cols = [defined[c] for c in used]
        # the platform runs it with the same expressions and filter semantics as the workbook
        sql, data = self.source.visual_rows(cols, visual.filters, defined)
        extra = [defined[c] for c in only_filter]
        self.columns.update({c.id: c for c in cols + extra})
        p = Proposal(len(self.proposals) + 1, visual, cols, sql, data, filter_columns=extra)
        self.proposals.append(p)
        return p

    def _execute(self, call, emit):
        if call.error:
            raise QueryError(call.error)
        if call.name == "run_query":
            out = self.run_query(call.arguments["query"])
            emit(Event("query", {"query": call.arguments["query"], "rows": out["total_rows"]}))
            return out
        if call.name == "propose_visual":
            p = self.propose_visual(**call.arguments)
            emit(Event("visual", {"n": p.n, "kind": p.visual.kind, "title": p.visual.title, "rows": len(p.rows)}))
            return {"visual": p.n, **visual_rows_for_model(p), "total_rows": len(p.rows)}
        if call.name == "suggest_followups":
            self._turn_followups = check_followups(call.arguments.get("followups"))
            emit(Event("followups", {"items": self._turn_followups}))
            return {"ok": True, "note": "follow-ups shown to the user as buttons: do not repeat them in the text"}
        raise QueryError(f"unknown tool {call.name}")

    # -- loop ------------------------------------------------------------------------------
    def ask(self, text: str, on_event=None) -> Turn:
        """A question from the user. `on_event(Event)` gets queries, visuals and errors as they come (streaming)."""
        t0, usage, events = time.time(), Usage(), []

        def emit(e):
            events.append(e)
            if on_event:
                on_event(e)
        self.messages.append(Message("user", text))
        self._turn_followups = []
        empty, notice = 0, None
        for step in range(1, MAX_STEPS + 1):
            reply = self.model.chat(self.system, self.messages, self.tools)
            usage += reply.usage
            if not reply.message.tool_calls and not self._turn_followups:
                reply.message.content = self._followups_in_text(reply.message.content, emit)
            if not reply.message.tool_calls and not reply.message.content.strip() and reply.stop == "end" \
                    and empty < EMPTY_RETRIES:
                # a step closed with no text and no tools: not in the history; at the second try, a nudge
                empty += 1
                emit(Event("error", {"tool": "answer", "error": "empty answer from the model, retrying"}))
                if empty == EMPTY_RETRIES:
                    self.messages.append(Message("user", NUDGE))
                continue
            self.messages.append(reply.message)
            if not reply.message.tool_calls:
                break
            for call in reply.message.tool_calls:
                try:
                    result, err = json.dumps(self._execute(call, emit), ensure_ascii=False, default=str), False
                except (QueryError, ValidationError, TypeError, KeyError) as e:   # platform errors arrive as QueryError
                    result, err = f"ERROR: {e}", True
                    emit(Event("error", {"tool": call.name, "error": str(e)[:500]}))
                self.messages.append(Message("tool", result, tool_call_id=call.id, is_error=err))
        else:
            notice = "tooManySteps"
        if not reply.message.tool_calls and reply.message.content:
            # follow-ups also written as a list at the end: never twice in the UI; taken as buttons if the tool was not
            items, text = followups_list_in_text(reply.message.content)
            if text is not None and (self._turn_followups or items):
                if not self._turn_followups:
                    self._turn_followups = items
                    emit(Event("followups", {"items": items}))
                reply.message.content = text
        answer = reply.message.content
        if notice is None and reply.stop in ("length", "incomplete", "refusal") and not answer:
            notice = "refused" if reply.stop == "refusal" else "truncated"
        if notice is None and not answer.strip() and not reply.message.tool_calls:
            notice = "empty"
        if notice:                                          # the history keeps an English note for the model
            reply.message.content = (answer + "\n" if answer.strip() else "") + NOTICES[notice]
        if not self._turn_followups and reply.message.content.strip() and not reply.message.tool_calls \
                and notice is None and self.messages and self.messages[-1] is reply.message:
            usage += self._ask_followups(emit)
        self.usage += usage
        if self._turn_followups and self.messages and self.messages[-1] is reply.message:
            self.followups[len(self.messages) - 1] = self._turn_followups
        return Turn(answer.strip() if notice else reply.message.content, events, usage, time.time() - t0, step,
                    self._turn_followups, notice)

    def _followups_in_text(self, content: str, emit) -> str:
        """gpt-oss sometimes writes the follow-ups in the answer instead of calling the tool: as JSON
        ({"followups": [...]}) or as a call (suggest_followups({...})). Valid ones are taken as if they came from
        the tool and removed from the text; the rest of the text stays. Golden set 25/9: 4 answers out of 74.
        If nothing is left, the empty-answer retry of the loop asks for the answer."""
        items, text = followups_in_text(content)
        if items:
            self._turn_followups = items
            emit(Event("followups", {"items": items}))
            return text
        return content

    def _ask_followups(self, emit) -> Usage:
        """The answer came without follow-ups (gpt-oss on OCI forgets them in about a third of the questions): one
        more call to get suggest_followups. The answer stays the one already written; reminder, call and result are
        removed from the history, which stays as if the follow-ups had come at once."""
        keep = len(self.messages)
        self.messages.append(Message("user", FOLLOWUPS_NUDGE))
        try:
            reply = self.model.chat(self.system, self.messages, self.tools)
            for call in reply.message.tool_calls:
                if call.name == "suggest_followups" and not call.error:
                    try:
                        self._execute(call, emit)
                        break
                    except QueryError:
                        pass
            return reply.usage
        except Exception:                                   # follow-ups are an extra: never fail the answer
            return Usage()
        finally:
            del self.messages[keep:]

    # -- workbook ------------------------------------------------------------------------
    def proposal(self, n: int) -> Proposal:
        if not 1 <= n <= len(self.proposals):
            raise UiError(f"visual {n} does not exist", "errors.visualNotFound", n=n)
        return self.proposals[n - 1]

    def pin(self, n: int, title: str | None = None):
        """Pins visual n to the workbook (at the end), with a new title if given."""
        p = self.proposal(n)
        if title:
            p.visual = p.visual.model_copy(update={"title": title})
        if not p.pinned:
            p.pinned = True
            self.order.append(n)
        return p

    def unpin(self, n: int):
        p = self.proposal(n)
        p.pinned = False
        self.order = [x for x in self.order if x != n]
        return p

    def reorder(self, order: list[int]):
        if sorted(order) != sorted(self.order):
            raise UiError(f"the order must contain exactly the pinned visuals {sorted(self.order)}", "errors.orderMismatch")
        self.order = list(order)

    def dataset_ref(self) -> DatasetRef:
        return self.source.ref

    def pinned_spec(self, name: str, canvas_title: str = "Live Insight") -> WorkbookSpec:
        pinned = [self.proposal(n) for n in self.order]
        if not pinned:
            raise UiError("no pinned visuals", "errors.noPinnedVisuals")
        cols = {c.id: c for p in pinned for c in p.columns + p.filter_columns}
        return WorkbookSpec(dataset=self.dataset_ref(), name=name, columns=list(cols.values()),
                            canvases=[CanvasSpec(title=canvas_title, visuals=[p.visual for p in pinned])])

    def column_info(self, c) -> dict:
        """How to show a column: label and type (quantitative | ordinal | nominal), for the preview."""
        if isinstance(c, Calculation):
            return {"id": c.id, "label": c.caption, "type": "quantitative" if c.is_measure else "nominal"}
        if isinstance(c, DateColumn):
            return {"id": c.id, "label": f"{c.source} ({token('grain.' + c.grain)})", "type": "ordinal", "grain": c.grain}
        measure = self.ds.columns.get(c.source, {}).get("columnType") == "measure"
        return {"id": c.id, "label": c.source, "type": "quantitative" if measure else "nominal"}

    def proposal_json(self, p: Proposal) -> dict:
        from liveinsight.engine.preview import build
        labels = {c.id: self.column_info(c)["label"] for c in p.columns + p.filter_columns}
        # the frontend writes the chip in the UI language ("between 1,000 and 2,000", "from … to …")
        filters = [{"column": labels.get(f.column, f.column), "op": f.op, "values": f.values} for f in p.visual.filters]
        out = {"n": p.n, "kind": p.visual.kind, "title": p.visual.title, "roles": p.visual.roles, "sort": p.visual.sort,
               "pinned": p.pinned, "columns": [self.column_info(c) for c in p.columns], "rows": p.rows,
               "filters": filters}
        return {**out, "preview": build(out)}

    def cost(self) -> float | None:
        return self.model.price.cost(self.usage) if self.model.price else None
