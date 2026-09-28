"""What the model reads, frozen: the system prompt and the tool definitions for the test dataset.

A refactoring must not change them by accident (25/9, architecture step 2). When a change is deliberate, rewrite
the snapshot with `uv run python tests/test_model_snapshot.py` and review the diff: it is exactly what the model
will read differently.
"""
import json
import sys
from pathlib import Path

SNAPSHOT = Path(__file__).parent / "fixtures" / "model_snapshot.json"


def current(dataset) -> dict:
    from test_session import FakeMcp, ScriptedModel, new_session
    s = new_session(FakeMcp([]), ScriptedModel([]), dataset)
    return {"system": s.system,
            "tools": [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in s.tools]}


def test_the_model_reads_what_the_snapshot_says(dataset):
    assert current(dataset) == json.loads(SNAPSHOT.read_text()), \
        "what the model reads changed: if deliberate, run `uv run python tests/test_model_snapshot.py`"


if __name__ == "__main__":
    sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parent.parent)]
    from conftest import ROOT, XSA
    from liveinsight.engine.spec import DatasetInfo
    ds = DatasetInfo.from_describe(XSA, json.loads((ROOT / "tests/fixtures/retail_orders_describe.json").read_text()))
    SNAPSHOT.write_text(json.dumps(current(ds), indent=2, ensure_ascii=False) + "\n")
    print(f"written {SNAPSHOT}")
