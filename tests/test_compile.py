"""Dalla spec al workbook e al .dva: ogni tipo di visual supera i controlli strutturali."""
import copy

import pytest

from conftest import DATASET_REF, SKELETON, needs_skeleton
from liveinsight.platforms.oac.checks import definition_problems, dva_problems
from liveinsight.platforms.oac.target import SpecError, build_dva, compile_workbook
from liveinsight.platforms.oac.li_dva import LOCAL, PLUGINS, Workbook, logical_roles
from liveinsight.engine.spec import ROLES, WorkbookSpec


COLUMNS = [
    {"kind": "column", "id": "CustomerSegment", "source": "Customer Segment"},
    {"kind": "column", "id": "ProductCategory", "source": "Product Category"},
    {"kind": "column", "id": "ProductSubCategory", "source": "Product Sub Category"},
    {"kind": "column", "id": "OrderPriority", "source": "Order Priority"},
    {"kind": "column", "id": "City", "source": "City"},
    {"kind": "column", "id": "Sales", "source": "Sales"},
    {"kind": "column", "id": "Profit", "source": "Profit"},
    {"kind": "column", "id": "Quantity", "source": "Quantity Ordered"},
    {"kind": "date", "id": "OrderMonth", "source": "Order Date", "grain": "month"},
    {"kind": "date", "id": "OrderYear", "source": "Order Date", "grain": "year"},
    {"kind": "calc", "id": "AvgOrderValue", "caption": "Valore medio ordine",
     "expression": "{Sales} / (COUNT(DISTINCT {Order ID}))", "description": "Vendite / ordini distinti"},
]
# un visual per tipo, con colonne diverse da quelle del suo template (come TEST7)
VISUALS = {
    "bar": {"measures": ["Profit"], "detail": ["CustomerSegment"]},
    "hbar": {"measures": ["Quantity"], "detail": ["ProductSubCategory"]},
    "line": {"measures": ["Sales"], "detail": ["OrderMonth"]},
    "table": {"row": ["CustomerSegment", "Sales", "Quantity"]},
    "pivot": {"row": ["CustomerSegment"], "col": ["OrderYear"], "measures": ["Profit"]},
    "scatter": {"measures": ["Sales", "Quantity"], "detail": ["ProductSubCategory"]},
    "area": {"measures": ["Profit"], "detail": ["OrderMonth"]},
    "radar": {"measures": ["AvgOrderValue"], "detail": ["OrderPriority"]},
    "boxplot": {"measures": ["Sales"], "detail": ["ProductCategory"]},
    "map": {"detail": ["City"], "size": ["Quantity"]},
    "narrative": {"measures": ["Sales"], "row": ["CustomerSegment"]},
    "tile": {"measures": ["Quantity"], "detail": ["CustomerSegment"]},
}


def one_visual(kind):
    return WorkbookSpec.model_validate({"dataset": DATASET_REF, "name": f"Test {kind}", "columns": COLUMNS, "canvases": [
        {"title": kind, "visuals": [{"kind": kind, "title": f"{kind} di prova", "roles": VISUALS[kind]}]}]})


def all_visuals(name="Live Insight - catalogo dei tipi"):
    kinds = list(VISUALS)
    return WorkbookSpec.model_validate({"dataset": DATASET_REF, "name": name, "columns": COLUMNS, "canvases": [
        {"title": f"Tipi {i + 1}", "visuals": [{"kind": k, "title": f"{k} di prova", "roles": VISUALS[k]}
                                               for k in kinds[i * 6:(i + 1) * 6]]} for i in range(2)]})


def template_columns(templates):
    return {c["columnID"] for t in templates for c in t["criteria"]["columns"]["children"]}


def test_ruoli_della_spec_coincidono_con_i_template(templates):
    """Ogni tipo ha un template la cui forma rientra nelle regole della spec (ruoli e numero di colonne)."""
    from liveinsight.platforms.oac.li_dva import template_roles
    wb = Workbook(templates[0], extra_templates=templates[1:])
    for kind, rules in ROLES.items():
        shapes = [{r: len(c) for r, c in template_roles(v).items()} for v in wb.variants[PLUGINS[kind]]]
        ok = [sh for sh in shapes if set(sh) <= set(rules) and all(rules[r].min <= n <= max(rules[r].max, n) for r, n in sh.items())]
        assert ok, (kind, shapes)
        assert all(r in rules for sh in shapes for r in sh), (kind, shapes)


@pytest.mark.parametrize("kind", list(VISUALS))
def test_ogni_tipo_compila_senza_problemi(kind, templates, dataset):
    wb = compile_workbook(one_visual(kind), Workbook(templates[0], extra_templates=templates[1:]), dataset)
    assert definition_problems(wb.definition(), template_columns(templates)) == []


def test_spec_incoerente_non_compila(templates, dataset):
    s = one_visual("bar")
    s.canvases[0].visuals[0].roles = {"measures": ["CustomerSegment"], "detail": ["Profit"]}
    with pytest.raises(SpecError, match="wants a measure"):
        compile_workbook(s, Workbook(templates[0], extra_templates=templates[1:]), dataset)


@needs_skeleton
def test_dva_con_tutti_i_tipi(tmp_path, templates, dataset):
    spec = all_visuals()
    path = tmp_path / "catalogo.dva"
    skeleton_name = Workbook(str(SKELETON)).name
    build_dva(spec.model_dump_json(), str(SKELETON), templates, dataset, path)
    assert dva_problems(path, spec.name, forbidden=[skeleton_name, "Examples", "Example 2"]) == []


def test_i_controlli_vedono_un_binding_solo_logico(templates, dataset):
    """Lo scenario di TEST3: cambiare la misura solo nel modello logico. OAC non dà errori, i numeri sono sbagliati."""
    wb = compile_workbook(one_visual("bar"), Workbook(templates[0], extra_templates=templates[1:]), dataset)
    defn = copy.deepcopy(wb.definition())
    view = next(v for v in defn["views"]["children"] if v["type"] == "saw:pluginView")
    edges = view["dataModels"]["children"][0]["logicalDataModel"]["settings"]["logicalDataModel"]["logicalEdges"]
    edges["measures"]["logicalEdgeLayers"][0]["columnID"] = "Sales"
    assert any("inconsistent binding" in p for p in definition_problems(defn))


@needs_skeleton
def test_i_controlli_vedono_i_dataset_incorporati(tmp_path, templates, dataset):
    wb = Workbook(str(SKELETON), extra_templates=templates, skeleton_visuals=False)
    compile_workbook(one_visual("bar"), wb, dataset)
    wb.rename("Con dataset")
    wb.save(tmp_path / "x.dva", include_datasets=True)
    assert any("dataset definitions" in p for p in dva_problems(tmp_path / "x.dva", "Con dataset"))
