"""The architecture of docs/ARCHITETTURA.md, §1, kept by tests: the engine talks to platforms only through the
DataSource and WorkbookTarget protocols. No network, no tokens."""
import ast
import json
from pathlib import Path

from liveinsight.engine.platform import DataSource, QueryError, WorkbookTarget
from liveinsight.engine.session import Session
from liveinsight.engine.spec import DatasetRef, DateColumn
from liveinsight.llm.base import ToolCall
from liveinsight.platforms.oac.platform import OacPlatform
from liveinsight.platforms.oac.source import OacSource
from liveinsight.platforms.oac.target import OacTarget

ENGINE = Path(__file__).parent.parent / "liveinsight" / "engine"


def test_the_engine_imports_no_platform():
    for path in ENGINE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not any(n.startswith("liveinsight.platforms") for n in names), f"{path.name} imports {names}"


def members(protocol) -> set[str]:
    return {n for n in vars(protocol) if not n.startswith("_")} | set(protocol.__annotations__)


def test_the_oac_adapter_has_both_faces():
    assert members(DataSource) <= set(dir(OacSource)) | {"ref", "dataset"}      # ref and dataset: set in __init__
    assert members(WorkbookTarget) <= set(dir(OacTarget))


class MemorySource:
    """A DataSource that is not OAC: rows in memory, a toy query language ("rows" returns them all)."""
    query_tool = "Returns the rows of the table. Write: rows"
    query_guide = "# How to query: run_query\nWrite: rows"

    def __init__(self, dataset, rows):
        self.dataset, self.rows = dataset, rows
        self.ref = DatasetRef(platform="oac", id=dataset.xsa, name="Memory")   # "oac" only because the spec allows it

    def query(self, text):
        if text.strip() != "rows":
            raise QueryError("write: rows")
        return self.rows

    def visual_rows(self, columns, filters, defined):
        return "rows", [{c.id: r.get(c.source if not isinstance(c, DateColumn) else "") for c in columns}
                        for r in self.rows]


def test_the_engine_runs_on_another_platform(dataset):
    from test_session import ScriptedModel, reply
    rows = [{"Customer Segment": "Corporate", "Sales": 3.0}, {"Customer Segment": "Consumer", "Sales": 2.0}]
    visual = {"kind": "bar", "title": "Corporate sells the most", "roles": {"measures": ["S"], "detail": ["Seg"]},
              "columns": [{"kind": "column", "id": "S", "source": "Sales"},
                          {"kind": "column", "id": "Seg", "source": "Customer Segment"}]}
    model = ScriptedModel([reply(calls=[ToolCall("1", "run_query", {"query": "SELECT"})]),       # wrong dialect
                           reply(calls=[ToolCall("2", "run_query", {"query": "rows"})]),
                           reply(calls=[ToolCall("3", "propose_visual", visual)]),
                           reply("Corporate sells the most.")])
    s = Session(MemorySource(dataset, rows), model)
    assert "Write: rows" in s.system and s.tools[0].description.startswith("Returns the rows")
    turn = s.ask("Who sells the most?")
    assert turn.text == "Corporate sells the most." and [e.kind for e in turn.events][:2] == ["error", "query"]
    assert s.proposals[0].rows == [{"S": 3.0, "Seg": "Corporate"}, {"S": 2.0, "Seg": "Consumer"}]
    s.pin(1)
    spec = json.loads(s.pinned_spec("x").model_dump_json())                 # the contract, from another platform
    assert spec["dataset"]["name"] == "Memory" and spec["canvases"][0]["visuals"][0]["title"] == "Corporate sells the most"


class CountingMcp:
    def __init__(self, describe):
        self.describe, self.calls = describe, []

    def call(self, tool, **args):
        self.calls.append(tool)
        return {"content": [{"type": "text", "text": json.dumps(self.describe)}]}


def test_oac_platform_describes_a_dataset_once(dataset):
    from conftest import ROOT, XSA
    describe = json.loads((ROOT / "tests/fixtures/retail_orders_describe.json").read_text())
    mcp = CountingMcp(describe)
    platform = OacPlatform(mcp, ROOT / "no-skeleton.dva", [])
    source = platform.source(XSA, "Retail Orders")
    assert source.ref == DatasetRef(platform="oac", id=XSA, name="Retail Orders") and source.dataset == dataset
    assert platform.target().describe(XSA) is source.dataset                 # shared: no second describe_data
    assert mcp.calls == ["describe_data"]
