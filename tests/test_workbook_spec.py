"""The WorkbookSpec as a contract (docs/ARCHITETTURA.md, §1): the engine writes one JSON, the OAC target reads only
that JSON, and the published JSON Schema always matches the code. No network, no tokens."""
import json
import re
from pathlib import Path

import pytest

from conftest import DATASET_REF, SKELETON, XSA, needs_skeleton
from liveinsight.platforms.oac.checks import definition_problems
from liveinsight.platforms.oac.target import SpecError, build_definition, build_dva, bundled
from liveinsight.engine.spec import SCHEMA_PATH, SPEC_VERSION, DatasetInfo, json_schema, load_spec

EXAMPLE = Path(__file__).parent / "fixtures" / "workbook_spec.example.json"


def test_the_published_schema_matches_the_code():
    assert SCHEMA_PATH.read_text() == json_schema(), "run: uv run python -m liveinsight.engine.spec"


def test_schema_header_and_required_fields():
    schema = json.loads(SCHEMA_PATH.read_text())
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith("docs/workbook-spec.schema.json") and schema["title"] == "Live Insight WorkbookSpec"
    assert set(schema["required"]) == {"dataset", "name", "columns", "canvases"}
    assert schema["properties"]["version"]["const"] == SPEC_VERSION


def test_the_example_is_a_valid_spec_on_the_dataset(dataset):
    from liveinsight.engine.spec import check_against
    spec = load_spec(EXAMPLE.read_text())
    assert spec.version == SPEC_VERSION and spec.dataset.id == XSA
    assert check_against(spec, dataset) == []


def test_json_round_trip():
    spec = load_spec(EXAMPLE.read_text())
    assert load_spec(spec.model_dump_json()) == spec


def test_load_spec_reads_json_only_and_known_versions():
    spec = load_spec(EXAMPLE.read_text())
    with pytest.raises(TypeError, match="reads the WorkbookSpec JSON"):
        load_spec(spec)                                                     # an engine object: refused
    with pytest.raises(TypeError):
        load_spec(json.loads(EXAMPLE.read_text()))                          # a dict too
    newer = json.loads(EXAMPLE.read_text()) | {"version": 2}
    with pytest.raises(ValueError, match="version 2 not supported"):
        load_spec(json.dumps(newer))
    without_dataset = {k: v for k, v in json.loads(EXAMPLE.read_text()).items() if k != "dataset"}
    with pytest.raises(ValueError, match="dataset"):
        load_spec(json.dumps(without_dataset))


def test_a_spec_without_version_is_version_1():
    data = {k: v for k, v in json.loads(EXAMPLE.read_text()).items() if k != "version"}
    assert load_spec(json.dumps(data)).version == 1


def test_the_oac_target_builds_the_example(templates, dataset):
    definition = build_definition(EXAMPLE.read_text(), bundled("base.json"), templates, dataset)
    assert definition_problems(definition) == []


@needs_skeleton
def test_the_bundled_base_builds_what_the_skeleton_builds(templates, dataset, tmp_path):
    # the bundled base.json is the skeleton .dva's own definition, anonymised: same workbook for the catalog
    def built(container):
        text = json.dumps(build_definition(EXAMPLE.read_text(), container, templates, dataset))
        return json.loads(re.sub(r'"filterID": "[0-9a-f-]{36}"', '"filterID": "x"', text))      # random ids
    assert built(bundled("base.json")) == built(str(SKELETON))
    wb = build_dva(EXAMPLE.read_bytes(), str(SKELETON), templates, dataset, tmp_path / "example.dva")
    assert wb.name == "Retail Orders - example"


def test_no_trace_of_the_template_dataset(templates, dataset):
    # the bundled templates sit on a placeholder dataset: a workbook built on another one must not name it
    other = DatasetInfo(xsa="XSA('11111111-1111-1111-1111-111111111111'.'Other#-#x#-#')", table=dataset.table,
                        columns=dataset.columns)
    spec = json.loads(EXAMPLE.read_text()) | {"dataset": DATASET_REF | {"id": other.xsa}}
    text = json.dumps(build_definition(json.dumps(spec), bundled("base.json"), templates, other))
    assert XSA not in text and "00000000-0000" not in text and text.count(other.xsa) > 10


def test_the_oac_target_reads_only_json(templates, dataset):
    spec = load_spec(EXAMPLE.read_text())
    with pytest.raises(TypeError):
        build_definition(spec, bundled("base.json"), templates, dataset)   # not through the contract
    with pytest.raises(SpecError, match="validation error"):
        build_definition('{"version": 1, "name": "x"}', bundled("base.json"), templates, dataset)


def test_the_oac_target_refuses_another_dataset(templates, dataset):
    other = json.loads(EXAMPLE.read_text()) | {"dataset": DATASET_REF | {"id": "XSA('other'.'Other')"}}
    with pytest.raises(SpecError, match="the spec reads dataset oac:XSA\\('other'"):
        build_definition(json.dumps(other), bundled("base.json"), templates, dataset)


def test_session_specs_name_their_dataset(dataset):
    from test_session import FakeMcp, ScriptedModel, new_session
    s = new_session(FakeMcp([]), ScriptedModel([]), dataset)
    assert s.dataset_ref().model_dump() == DATASET_REF
