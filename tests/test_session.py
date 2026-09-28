"""Sessione, loop e adattatori, senza rete: MCP e modello finti, nessun token speso."""
import json
from types import SimpleNamespace as NS

import pytest

from liveinsight.llm.anthropic_chat import AnthropicChat
from liveinsight.llm.base import Message, Reply, ToolCall, Usage
from liveinsight.llm.openai_responses import OpenAIResponses
from liveinsight.engine.session import (QueryError, Session, followups_in_text, followups_list_in_text,
                                        visual_rows_for_model)
from liveinsight.platforms.oac.source import OacSource, expand, format_period


class FakeMcp:
    def __init__(self, rows=()):
        self.rows, self.queries = list(rows), []

    def call(self, tool, **args):
        self.queries.append(args["query"])
        return {"content": [{"type": "text", "text": json.dumps(
            {"batches": [{"data": self.rows}], "status": {"error": False}})}]}


class ScriptedModel:
    provider, model, price = "fake", "fake", None

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def chat(self, system, messages, tools):
        self.seen.append(list(messages))
        return self.replies.pop(0)


def new_session(mcp, model, dataset, name="Retail Orders"):
    """A chat session on the test dataset, with a fake OAC and a scripted model."""
    return Session(OacSource(mcp, dataset, name), model)


def reply(text="", calls=()):
    return Reply(Message("assistant", text, list(calls), provider="fake"), Usage(10, 0, 0, 5),
                 "tool_calls" if calls else "end")


def test_expand(dataset):
    sql = expand("SELECT {Customer Segment}, {Sales} FROM {dataset}", dataset)
    assert sql == (f'SELECT {dataset.xsa}."Retail Data final"."Customer Segment", '
                   f'{dataset.xsa}."Retail Data final"."Sales" FROM {dataset.xsa}')
    with pytest.raises(QueryError, match=r"\{dataset\} for the source"):
        expand("SELECT {Sales} FROM {Retail Orders FS}", dataset)
    # il nome del dataset vale come {dataset}
    assert expand("SELECT {Sales} FROM {Retail Orders FS}", dataset, "Retail Orders FS").endswith(f"FROM {dataset.xsa}")


@pytest.mark.parametrize("value, grain, out", [
    ("2013-04-01 00:00:00.0", "quarter", "2013 Q2"), ("2013-11-01 00:00:00.0", "month", "2013-11"),
    ("2015-01-01 00:00:00.0", "year", "2015"), ("Corporate", "year", "Corporate")])
def test_format_period(value, grain, out):
    assert format_period(value, grain) == out


def test_propose_visual_usa_le_espressioni_del_workbook(dataset):
    mcp = FakeMcp([{"c1": 361638.7, "c2": "2013-04-01 00:00:00.0"}])       # c1 = Sales, c2 = trimestre
    s = new_session(mcp, ScriptedModel([]), dataset)
    p = s.propose_visual("line", "Vendite per trimestre", {"measures": ["Sales"], "detail": ["Q"]},
                         [{"kind": "column", "id": "Sales", "source": "Sales"},
                          {"kind": "date", "id": "Q", "source": "Order Date", "grain": "quarter"}])
    assert f'ExtractQuarter({dataset.xsa}."Retail Data final"."Order Date") AS c2' in mcp.queries[0]
    assert mcp.queries[0].endswith("ORDER BY 2")            # ordinato per l'attributo, come OAC
    assert p.rows == [{"Sales": 361638.7, "Q": "2013 Q2"}]


def test_kind_mancante_si_deduce(dataset):
    """Nel golden set il modello ometteva "kind" nelle colonne: un passo sprecato su un errore di validazione."""
    s = new_session(FakeMcp([{"c1": 1.0, "c2": "2016-01-01 00:00:00.0", "c3": 2.0}]), ScriptedModel([]), dataset, "R")
    p = s.propose_visual("line", "t", {"measures": ["M"], "detail": ["Q"]},
                         [{"id": "M", "caption": "Margine", "expression": "{Profit} / {Sales}"},
                          {"id": "Q", "source": "Order Date", "grain": "quarter"}])
    assert [type(c).__name__ for c in p.columns] == ["Calculation", "DateColumn"]
    p = s.propose_visual("bar", "t", {"measures": ["S"], "detail": ["Seg"]},
                         [{"id": "S", "source": "Sales"}, {"id": "Seg", "source": "Customer Segment"}])
    assert [type(c).__name__ for c in p.columns] == ["SourceColumn", "SourceColumn"]


def test_propose_visual_rifiuta_spec_incoerenti(dataset):
    s = new_session(FakeMcp(), ScriptedModel([]), dataset)
    sales = {"kind": "column", "id": "Sales", "source": "Sales"}
    seg = {"kind": "column", "id": "Seg", "source": "Customer Segment"}
    with pytest.raises(QueryError, match="wants a measure"):
        s.propose_visual("bar", "t", {"measures": ["Seg"], "detail": ["Sales"]}, [sales, seg])
    s.propose_visual("bar", "t", {"measures": ["Sales"], "detail": ["Seg"]}, [sales, seg])
    with pytest.raises(QueryError, match="already used"):
        s.propose_visual("bar", "t", {"measures": ["Sales"], "detail": ["Seg"]},
                         [{"kind": "column", "id": "Sales", "source": "Profit"}, seg])


def test_loop_con_errore_e_correzione(dataset):
    model = ScriptedModel([
        reply(calls=[ToolCall("1", "run_query", {"query": "SELECT {Regione} FROM {dataset}"})]),
        reply(calls=[ToolCall("2", "run_query", {"query": "SELECT {Customer Segment} FROM {dataset}"})]),
        reply("Quattro segmenti."),
    ])
    s = new_session(FakeMcp([{"s": "Consumer"}]), model, dataset)
    turn = s.ask("quanti segmenti?")
    assert turn.text == "Quattro segmenti." and turn.steps == 3
    assert [e.kind for e in turn.events] == ["error", "query"]
    tool_msgs = [m for m in s.messages if m.role == "tool"]
    assert tool_msgs[0].is_error and "Regione" in tool_msgs[0].content and not tool_msgs[1].is_error
    assert turn.usage.input_tokens == 30


def test_pinned_spec(dataset):
    s = new_session(FakeMcp([{"c1": "Consumer", "c2": 1.0}]), ScriptedModel([]), dataset)
    cols = [{"kind": "column", "id": "Sales", "source": "Sales"}, {"kind": "column", "id": "Seg", "source": "Customer Segment"}]
    s.propose_visual("bar", "a", {"measures": ["Sales"], "detail": ["Seg"]}, cols)
    s.propose_visual("tile", "b", {"measures": ["Sales"], "detail": ["Seg"]}, cols)
    with pytest.raises(ValueError, match="no pinned visuals"):
        s.pinned_spec("x")
    s.pin(2)
    spec = s.pinned_spec("x")
    assert [v.title for v in spec.canvases[0].visuals] == ["b"] and {c.id for c in spec.columns} == {"Sales", "Seg"}


# -- adattatori: traduzione dei messaggi, senza rete --------------------------------------

HISTORY = [
    Message("user", "domanda"),
    Message("assistant", "", [ToolCall("c1", "run_query", {"query": "q1"}), ToolCall("c2", "run_query", {"query": "q2"})],
            raw=[{"type": "reasoning", "encrypted_content": "xyz"}, {"type": "function_call", "call_id": "c1"}],
            provider="openai"),
    Message("tool", "r1", tool_call_id="c1"),
    Message("tool", "errore", tool_call_id="c2", is_error=True),
]


def test_openai_rimanda_gli_elementi_originali():
    items = OpenAIResponses("gpt-5.6-luna", api_key="x").input_items(HISTORY)
    assert items[1] == {"type": "reasoning", "encrypted_content": "xyz"}       # ragionamento cifrato, tale e quale
    assert items[-1] == {"type": "function_call_output", "call_id": "c2", "output": "errore"}


def test_openai_rimanda_il_reasoning_senza_campi_nulli():
    """Regressione: un reasoning rimandato con "status": null fa rispondere l'API 400 (unknown parameter)."""
    from openai.types.responses import ResponseReasoningItem
    item = ResponseReasoningItem(id="rs_1", type="reasoning", summary=[], encrypted_content="xyz", status=None)
    r = NS(output=[item], status="completed", incomplete_details=None,
           usage=NS(input_tokens=10, output_tokens=5, input_tokens_details=NS(cached_tokens=0)))
    raw = OpenAIResponses("gpt-5.6-luna", api_key="x").parse(r).message.raw
    assert raw == [{"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "xyz"}]


def test_openai_parse_usage_e_incomplete():
    item = NS(type="function_call", call_id="c9", name="run_query", arguments='{"query": "q"}',
              model_dump=lambda **kw: {"type": "function_call"})
    r = NS(output=[item], status="completed", incomplete_details=None,
           usage=NS(input_tokens=1000, output_tokens=50, input_tokens_details=NS(cached_tokens=600, cache_write_tokens=100)))
    rep = OpenAIResponses("gpt-5.6-luna", api_key="x").parse(r)
    assert rep.stop == "tool_calls" and rep.message.tool_calls[0].arguments == {"query": "q"}
    assert (rep.usage.input_tokens, rep.usage.cached_input_tokens, rep.usage.cache_write_tokens) == (300, 600, 100)
    r.output, r.status, r.incomplete_details = [], "incomplete", NS(reason="max_output_tokens")
    assert OpenAIResponses("gpt-5.6-luna", api_key="x").parse(r).stop == "length"


def test_anthropic_raggruppa_i_risultati_e_converte_da_altri_provider():
    msgs = AnthropicChat("claude-opus-5", api_key="x")._messages(HISTORY)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]           # risultati del turno in un solo messaggio
    assert [b["tool_use_id"] for b in msgs[2]["content"]] == ["c1", "c2"] and msgs[2]["content"][1]["is_error"]
    assert [b["type"] for b in msgs[1]["content"]] == ["tool_use", "tool_use"]  # da OpenAI: niente ragionamento altrui


def test_expand_tollera_spazi_e_a_capo_tra_le_graffe(dataset):
    # Cohere su OCI, 24/9: "FROM {\r\ndataset}"
    assert expand("SELECT {\r\n Sales } FROM {\r\ndataset}", dataset) == expand("SELECT {Sales} FROM {dataset}", dataset)


def test_llama_su_oci_vuole_prima_un_messaggio_utente():
    from liveinsight.llm.openai_chat import OpenAIChat
    msgs = [Message("user", "domanda")]
    llama = OpenAIChat("meta.llama-3.3-70b-instruct", api_key="k", provider="oci")._messages("SISTEMA", msgs)
    assert [m["role"] for m in llama] == ["user"] and llama[0]["content"].startswith("SISTEMA")
    gpt = OpenAIChat("openai.gpt-oss-120b", api_key="k", provider="oci")._messages("SISTEMA", msgs)
    assert [m["role"] for m in gpt] == ["system", "user"]


def test_schema_degli_strumenti_leggibile_da_ogni_renderer():
    # gpt-oss (harmony) non risolve $ref e ignora const; Gemini non ha oneOf: la colonna è un oggetto piatto
    schema = Session._visual_schema()
    text = json.dumps(schema)
    assert not any(k in text for k in ('"$ref"', '"$defs"', '"discriminator"', '"const"', '"anyOf"', '"oneOf"'))
    column = schema["properties"]["columns"]["items"]
    assert column["required"] == ["kind", "id"] and column["properties"]["kind"]["enum"] == ["column", "date", "calc"]
    props = column["properties"]
    assert set(props) == {"kind", "id", "source", "grain", "caption", "expression", "description", "is_measure"}
    assert props["source"]["description"].endswith("(required for kind column/date, absent in the others)")
    assert props["grain"]["enum"] == ["year", "quarter", "month", "day"]
    assert "optional for kind calc" in props["is_measure"]["description"]


def test_gli_esempi_del_prompt_sono_visual_validi(dataset):
    # gli esempi insegnano la forma della chiamata ai modelli (soprattutto quelli su OCI): devono passare la spec
    import re
    from liveinsight.engine.prompt import system_prompt
    from liveinsight.platforms.oac.source import QUERY_GUIDE
    examples = re.findall(r"^  [^:\n]+: (\{\"kind\".*\})$", system_prompt("Retail Orders", dataset, QUERY_GUIDE), re.M)
    assert len(examples) == 3
    for text in examples:
        s = new_session(FakeMcp([]), ScriptedModel([]), dataset)
        p = s.propose_visual(**json.loads(text))
        assert p.visual.kind in ("bar", "line")
    assert p.visual.filters[0].column == "Year" and p.filter_columns[0].grain == "year"       # l'ultimo: col filtro


class SeqMcp(FakeMcp):
    """Risponde alle query nell'ordine: prima il filtro su misura, poi l'anteprima."""
    def __init__(self, *answers):
        super().__init__()
        self.answers = list(answers)

    def call(self, tool, **args):
        self.queries.append(args["query"])
        return {"content": [{"type": "text", "text": json.dumps(
            {"batches": [{"data": self.answers.pop(0)}], "status": {"error": False}})}]}


def test_filtri_nell_anteprima_e_nel_workbook(dataset):
    # come "Filter on two items" di "With Filters": Profit nel range su TUTTI gli anni, poi le vendite del 2016
    by_profit = [{"c1": "Furniture", "c2": "Home Office", "c3": -91273.0},       # nel range
                 {"c1": "Technology", "c2": "Corporate", "c3": -146954.0},       # fuori
                 {"c1": "Office's", "c2": "Corporate", "c3": -86825.0}]           # nel range (apostrofo)
    mcp = SeqMcp(by_profit, [{"c1": 188368.0, "c2": "Furniture", "c3": "Home Office"}])
    s = new_session(mcp, ScriptedModel([]), dataset)
    p = s.propose_visual(
        "bar", "Vendite 2016 dove il profitto è più negativo",
        {"measures": ["Vendite"], "detail": ["Categoria"], "color": ["Segmento"]},
        [{"kind": "column", "id": "Categoria", "source": "Product Category"},
         {"kind": "column", "id": "Segmento", "source": "Customer Segment"},
         {"kind": "column", "id": "Vendite", "source": "Sales"},
         {"kind": "date", "id": "Anno", "source": "Order Date", "grain": "year"},
         {"kind": "column", "id": "Profitto", "source": "Profit"}],
        filters=[{"column": "Anno", "op": "in", "values": ["2016"]},
                 {"column": "Profitto", "op": "between", "values": ["-92680", "-85460"]}])
    ref = lambda name: f'{dataset.xsa}."Retail Data final"."{name}"'
    measure_q, preview_q = mcp.queries
    assert measure_q == (f"SELECT {ref('Product Category')} AS c1, {ref('Customer Segment')} AS c2, "
                         f"{ref('Profit')} AS c3 FROM {dataset.xsa}")              # senza il filtro sull'anno
    assert preview_q.startswith(f"SELECT {ref('Sales')} AS c1, {ref('Product Category')} AS c2, "
                                f"{ref('Customer Segment')} AS c3 FROM")          # colonne dei filtri non in SELECT
    assert (f" WHERE ExtractYear({ref('Order Date')}) IN (TIMESTAMP '2016-01-01 00:00:00')"
            f" AND (({ref('Product Category')} = 'Furniture' AND {ref('Customer Segment')} = 'Home Office')"
            f" OR ({ref('Product Category')} = 'Office''s' AND {ref('Customer Segment')} = 'Corporate'))") in preview_q
    assert [c.id for c in p.filter_columns] == ["Anno", "Profitto"]
    assert s.proposal_json(p)["filters"] == [{"column": "Order Date (⟦grain.year⟧)", "op": "in", "values": ["2016"]},
                                             {"column": "Profit", "op": "between", "values": [-92680.0, -85460.0]}]
    s.pin(p.n)
    assert {c.id for c in s.pinned_spec("Con filtri").columns} == {"Categoria", "Segmento", "Vendite", "Anno", "Profitto"}


def test_filtro_su_misura_senza_combinazioni_nel_range(dataset):
    mcp = SeqMcp([{"c1": "2013-01-01 00:00:00.0", "c2": 1.0}])                   # una sola query: niente anteprima
    s = new_session(mcp, ScriptedModel([]), dataset)
    p = s.propose_visual("table", "t", {"row": ["Mese", "Vendite"]},
                         [{"kind": "date", "id": "Mese", "source": "Order Date", "grain": "month"},
                          {"kind": "column", "id": "Vendite", "source": "Sales"}],
                         filters=[{"column": "Vendite", "op": "between", "values": [126000, 167800]}])
    assert p.rows == [] and len(mcp.queries) == 1


@pytest.mark.parametrize("filters, error", [
    ([{"column": "Categoria", "op": "between", "values": ["1", "2"]}], "between applies to measures"),
    ([{"column": "Vendite", "op": "in", "values": ["10"]}], "use between"),
    ([{"column": "Mese", "op": "between", "values": ["2016-01", "2016-03"]}], "only on a column with grain day"),
    ([{"column": "Mese", "op": "in", "values": ["novembre 2016"]}], "invalid periods"),
    ([{"column": "Anno", "op": "in", "values": ["2016 Q1"]}], "invalid periods"),
    ([{"column": "Vendite", "op": "between", "values": ["molto", "poco"]}], "two numbers"),
    ([{"column": "Vendite", "op": "between", "values": ["1"]}], "two values"),
    ([{"column": "Nessuna", "op": "in", "values": ["x"]}], "undefined columns"),
])
def test_filtri_non_ammessi(dataset, filters, error):
    s = new_session(FakeMcp([]), ScriptedModel([]), dataset)
    cols = [{"kind": "column", "id": "Categoria", "source": "Product Category"},
            {"kind": "column", "id": "Vendite", "source": "Sales"},
            {"kind": "date", "id": "Anno", "source": "Order Date", "grain": "year"},
            {"kind": "date", "id": "Mese", "source": "Order Date", "grain": "month"}]
    with pytest.raises((QueryError, ValueError), match=error):
        s.propose_visual("bar", "t", {"measures": ["Vendite"], "detail": ["Categoria"]}, cols, filters=filters)


def test_risposta_vuota_si_ritenta(dataset):
    # Gemini su OCI (24/9): ogni tanto un passo chiuso senza testo né strumenti; la UI restava su "sto analizzando"
    model = ScriptedModel([reply(""), reply("  "), reply("Technology vende di più.")])
    s = new_session(FakeMcp([]), model, dataset)
    events = []
    t = s.ask("Chi vende di più?", on_event=events.append)
    assert t.text == "Technology vende di più." and t.steps == 3
    assert [e.kind for e in events] == ["error", "error"]
    assert [m.role for m in s.messages] == ["user", "user", "assistant"]            # i vuoti non restano, l'invito sì
    assert "empty" in s.messages[1].content


def test_risposta_sempre_vuota(dataset):
    s = new_session(FakeMcp([]), ScriptedModel([reply(""), reply(""), reply("")]), dataset)
    t = s.ask("?")
    assert t.text == "" and t.notice == "empty"                                   # the UI says it in its language
    assert s.messages[-1].content == "(the model did not write an answer)"       # the history is never empty


def test_intervallo_di_giorni_e_mesi_nell_anteprima(dataset):
    mcp = FakeMcp([])
    s = new_session(mcp, ScriptedModel([]), dataset)
    p = s.propose_visual("line", "Sconti in autunno", {"measures": ["Sconto"], "detail": ["Giorno"]},
                         [{"kind": "column", "id": "Sconto", "source": "Discount"},
                          {"kind": "date", "id": "Giorno", "source": "Order Date", "grain": "day"},
                          {"kind": "date", "id": "Mese", "source": "Order Date", "grain": "month"}],
                         filters=[{"column": "Giorno", "op": "between", "values": ["2015-09-01", "2015-11-30"]},
                                  {"column": "Mese", "op": "in", "values": ["2015-10"]}])
    ref = f'{dataset.xsa}."Retail Data final"."Order Date"'
    assert (f"WHERE ExtractDay({ref}) BETWEEN TIMESTAMP '2015-09-01 00:00:00' AND TIMESTAMP '2015-11-30 00:00:00'"
            f" AND ExtractMonth({ref}) IN (TIMESTAMP '2015-10-01 00:00:00')") in mcp.queries[-1]
    assert s.proposal_json(p)["filters"] == [
        {"column": "Order Date (⟦grain.day⟧)", "op": "between", "values": ["2015-09-01", "2015-11-30"]},
        {"column": "Order Date (⟦grain.month⟧)", "op": "in", "values": ["2015-10"]}]


@pytest.mark.parametrize("items, ok", [
    (["Mostra l'andamento mensile di Technology nel 2016", "Confronta i segmenti per profitto"], True),
    (["Vuoi vedere le vendite per mese", "Confronta i segmenti"], False),              # proposta, non richiesta
    (["Mostra le vendite per mese?", "Confronta i segmenti per profitto"], False),     # domanda
    (["Quali città vendono di più", "Confronta i segmenti per profitto"], False),      # domanda senza "?"
    (["Mostra le vendite per mese"], False),                                            # uno solo
    (["Mostra " + "x" * 140, "Confronta i segmenti per profitto"], False),             # troppo lungo
])
def test_spunti_all_imperativo(items, ok):
    from liveinsight.engine.session import check_followups
    if ok:
        assert check_followups(items) == items
    else:
        with pytest.raises(QueryError):
            check_followups(items)


def test_spunti_legati_alla_risposta(dataset):
    spunti = ["Mostra le vendite di Technology per mese", "Confronta i segmenti per profitto"]
    model = ScriptedModel([reply(calls=[ToolCall("1", "suggest_followups", {"followups": ["Vuoi altro?", "x"]})]),
                           reply(calls=[ToolCall("2", "suggest_followups", {"followups": spunti})]),
                           reply("Technology vende di più.")])
    s = new_session(FakeMcp([]), model, dataset)
    events = []
    t = s.ask("Chi vende di più?", on_event=events.append)
    assert t.followups == spunti and [e.kind for e in events] == ["error", "followups"]   # il primo tentativo è rifiutato
    assert s.followups == {len(s.messages) - 1: spunti}
    assert "invalid follow-up" in s.messages[2].content                                         # l'errore torna al modello


def test_spunti_dimenticati_si_chiedono_una_volta(dataset):
    spunti = ["Mostra le vendite di Technology per mese", "Confronta i segmenti per profitto"]
    model = ScriptedModel([reply("Technology vende di più."),                                   # niente spunti
                           reply(calls=[ToolCall("9", "suggest_followups", {"followups": spunti})])])
    s = new_session(FakeMcp([]), model, dataset)
    t = s.ask("Chi vende di più?")
    assert t.text == "Technology vende di più." and t.followups == spunti
    assert [m.role for m in s.messages] == ["user", "assistant"]                   # promemoria tolto dalla storia
    assert "suggest_followups" in model.seen[1][-1].content


def test_spunti_non_arrivati_la_risposta_resta(dataset):
    model = ScriptedModel([reply("Technology vende di più."), reply("Ecco di nuovo la risposta.")])
    s = new_session(FakeMcp([]), model, dataset)
    t = s.ask("Chi vende di più?")
    assert t.text == "Technology vende di più." and t.followups == [] and len(s.messages) == 2


FOLLOWUPS = ["Mostra le vendite di Technology per mese", "Confronta i segmenti per profitto"]


@pytest.mark.parametrize("text, rest", [
    # the four shapes gpt-oss wrote on 25/9 instead of calling the tool
    ('Hong Kong ordina più pezzi: **4 909**.\n\n{\n  "followups": %s\n}', "Hong Kong ordina più pezzi: **4 909**."),
    ('133 città diverse\n\nsuggest_followups({"followups":%s})', "133 città diverse"),
    ('Corporate: **-352.872**.\n\n—  \nsuggest_followups({"followups": %s})', "Corporate: **-352.872**."),
    ('```json\n{"followups": %s}\n```', ""),
])
def test_followups_written_in_the_text(text, rest):
    items, clean = followups_in_text(text % json.dumps(FOLLOWUPS, ensure_ascii=False))
    assert items == FOLLOWUPS and clean == rest


def test_invalid_or_missing_followups_in_the_text_stay():
    for text in ["Technology vende di più.", '{"followups": ["Vuoi altro?", "x"]}', '{"followups": [broken']:
        assert followups_in_text(text) == (None, text)


def test_followups_in_the_text_count_as_the_tool(dataset):
    text = 'Technology vende di più.\n\n{"followups": %s}' % json.dumps(FOLLOWUPS, ensure_ascii=False)
    model = ScriptedModel([reply(text)])
    s = new_session(FakeMcp([]), model, dataset)
    t = s.ask("Chi vende di più?")
    assert t.text == "Technology vende di più." and t.followups == FOLLOWUPS
    assert len(model.seen) == 1                                           # no extra call to ask for them


def test_only_followups_in_the_text_asks_for_the_answer(dataset):
    # 25/9, "trimestre-min": the whole answer was the follow-ups JSON; the empty-answer retry asks again
    only = '{"followups": %s}' % json.dumps(FOLLOWUPS, ensure_ascii=False)
    model = ScriptedModel([reply(only), reply("Il trimestre più basso è 2014 Q1.")])
    s = new_session(FakeMcp([]), model, dataset)
    t = s.ask("Qual è il trimestre peggiore?")
    assert t.text == "Il trimestre più basso è 2014 Q1." and t.followups == FOLLOWUPS


def test_visual_rows_for_the_model_keep_the_best_values():
    # 25/9: rows are alphabetical; cut at 50 the model saw Cancún, Canberra, Chicago and missed Hong Kong
    rows = [{"City": f"C{i:03d}", "Sales": float(i)} for i in range(120)]
    visual = NS(roles={"measures": ["Sales"], "detail": ["City"]}, sort="desc")
    out = visual_rows_for_model(NS(rows=rows, visual=visual))
    assert len(out["rows"]) == 50 and out["rows"][0] == {"City": "C119", "Sales": 119.0}
    assert "120 rows" in out["note"] and "highest Sales" in out["note"]
    asc = visual_rows_for_model(NS(rows=rows, visual=NS(roles=visual.roles, sort="asc")))
    assert asc["rows"][0]["Sales"] == 0.0 and "lowest" in asc["note"]
    few = visual_rows_for_model(NS(rows=rows[:10], visual=visual))
    assert few["rows"] == rows[:10]                                       # under the limit: the visual order


ANSWER = "Le vendite totali ammontano a **8,5 milioni**."


@pytest.mark.parametrize("tail", [
    # the shapes gpt-oss wrote in the golden set (25/9), on top of calling the tool
    "\n\n**Suggerimenti per approfondire**\n- Mostra le vendite per segmento cliente.\n- Confronta le vendite dei tre anni più recenti.",
    "\n\n**Prossimi approfondimenti**  \n- Mostra la distribuzione del valore degli ordini per segmento cliente.  \n"
    "- Confronta il valore medio e mediano degli ordini nel tempo (anno).",
    "\n\n---\n\n**Suggerimenti per approfondimenti**\n\n- Mostra la distribuzione del numero di ordini per città.\n"
    "- Confronta le vendite per segmento.\n\n(Se desideri un altro approfondimento, fammelo sapere.)",
    "\n\nSuggerisco approfondimenti:\n- “Mostra il numero di ordini per le 10 città con il maggior volume di vendite”\n"
    "- “Visualizza la distribuzione delle vendite per città in una mappa”",
])
def test_a_followups_list_at_the_end_is_found(tail):
    items, text = followups_list_in_text(ANSWER + tail)
    assert text == ANSWER and items and all(not i.endswith(".") and "“" not in i for i in items)


def test_text_that_is_not_a_followups_list_stays():
    for text in [ANSWER, ANSWER + "\n\nI suggerimenti del team sono stati accolti nel 2016.",
                 ANSWER + "\n\n**Spunti**\n- Mostra le vendite\n\nE poi un paragrafo che continua la risposta."]:
        assert followups_list_in_text(text) == (None, None)


def test_the_list_is_removed_when_the_tool_was_called(dataset):
    tail = "\n\n**Suggerimenti**\n- Mostra le vendite per anno\n- Confronta i segmenti"
    model = ScriptedModel([reply(calls=[ToolCall("1", "suggest_followups", {"followups": FOLLOWUPS})]),
                           reply(ANSWER + tail)])
    t = new_session(FakeMcp([]), model, dataset).ask("Quanto valgono le vendite?")
    assert t.text == ANSWER and t.followups == FOLLOWUPS                    # the tool's, shown once


def test_the_list_becomes_the_followups_when_the_tool_was_not_called(dataset):
    tail = "\n\n**Suggerimenti**\n- Mostra le vendite per anno\n- Confronta i segmenti per profitto"
    t = new_session(FakeMcp([]), ScriptedModel([reply(ANSWER + tail)]), dataset).ask("Quanto valgono le vendite?")
    assert t.text == ANSWER and t.followups == ["Mostra le vendite per anno", "Confronta i segmenti per profitto"]
