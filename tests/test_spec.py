"""La WorkbookSpec: forma (Pydantic), coerenza con il dataset, layout. Non servono template né OAC."""
import pytest
from pydantic import ValidationError

from conftest import DATASET_REF
from liveinsight.engine.spec import (CANVAS_WIDTH, ROLES, VisualSpec, WorkbookSpec, check_against, layout)


def spec(**overrides):
    base = {
        "dataset": DATASET_REF,
        "name": "Prova",
        "columns": [
            {"kind": "column", "id": "Segment", "source": "Customer Segment"},
            {"kind": "column", "id": "Sales", "source": "Sales"},
            {"kind": "date", "id": "OrderYear", "source": "Order Date", "grain": "year"},
            {"kind": "calc", "id": "AOV", "caption": "Valore medio ordine",
             "expression": "{Sales} / (COUNT(DISTINCT {Order ID}))"},
        ],
        "canvases": [{"title": "Vendite", "visuals": [
            {"kind": "bar", "title": "Corporate ha il valore medio ordine più alto",
             "roles": {"measures": ["AOV"], "detail": ["Segment"]}},
            {"kind": "line", "title": "Le vendite crescono ogni anno", "roles": {"measures": ["Sales"], "detail": ["OrderYear"]}},
        ]}],
    }
    base.update(overrides)
    return base


def test_spec_valida(dataset):
    s = WorkbookSpec.model_validate(spec())
    assert check_against(s, dataset) == []


@pytest.mark.parametrize("kind, roles, sort, message", [
    ("bar", {"measures": ["Sales"]}, None, "required"),                                      # manca detail
    ("bar", {"measures": ["Sales"], "detail": ["Segment"], "size": ["Sales"]}, None, "allowed roles"),
    ("bar", {"measures": ["Sales", "AOV", "Sales", "AOV"], "detail": ["Segment"]}, None, "from 1 to 3"),
    ("bar", {"measures": ["Sales", "AOV"], "detail": ["Segment"], "color": ["Segment"]}, None, "several measures and color"),
    ("line", {"measures": ["Sales"], "detail": ["OrderYear"], "color": ["Segment"], "col": ["Segment"]}, None, "color and trellis"),
    ("line", {"measures": ["Sales"], "detail": ["OrderYear"]}, "desc", "sort: only for bar"),
    ("bar", {"measures": ["Sales"], "detail": ["Segment"], "color": ["Segment"]}, "desc", "sort: only for bar"),
])
def test_ruoli_sbagliati(kind, roles, sort, message):
    with pytest.raises(ValidationError, match=message):
        VisualSpec(kind=kind, title="t", roles=roles, sort=sort)


def test_forme_nuove_valide():
    assert VisualSpec(kind="bar", title="t", roles={"measures": ["Sales", "AOV"], "detail": ["Segment"]}).roles["measures"] == ["Sales", "AOV"]
    assert VisualSpec(kind="line", title="t", roles={"measures": ["Sales"], "detail": ["Y"], "color": ["Segment"]})
    assert VisualSpec(kind="line", title="t", roles={"measures": ["Sales"], "detail": ["Y"], "col": ["Segment"], "color": []}).roles == \
        {"measures": ["Sales"], "detail": ["Y"], "col": ["Segment"]}                             # ruoli vuoti = assenti
    assert VisualSpec(kind="table", title="t", roles={"row": ["A", "B", "C", "D", "E", "F"]})
    assert VisualSpec(kind="hbar", title="t", roles={"measures": ["Sales"], "detail": ["Segment"]}, sort="desc").sort == "desc"


def test_tipo_sconosciuto():
    with pytest.raises(ValidationError):
        VisualSpec(kind="pie", title="t", roles={"measures": ["Sales"]})


def test_colonna_non_definita():
    s = spec()
    s["canvases"][0]["visuals"][0]["roles"]["detail"] = ["Region"]
    with pytest.raises(ValidationError, match=r"columns not defined in `columns`: \['Region'\]"):
        WorkbookSpec.model_validate(s)


def test_id_ripetuti_e_non_validi():
    s = spec()
    s["columns"].append({"kind": "column", "id": "Sales", "source": "Profit"})
    with pytest.raises(ValidationError, match="repeated column ids"):
        WorkbookSpec.model_validate(s)
    s = spec()
    s["columns"][0]["id"] = "Customer Segment"
    with pytest.raises(ValidationError):
        WorkbookSpec.model_validate(s)


def test_errori_contro_il_dataset(dataset):
    s = spec()
    s["columns"] += [
        {"kind": "column", "id": "Region", "source": "Region"},                              # non esiste
        {"kind": "date", "id": "CityYear", "source": "City", "grain": "year"},               # non è una data
        {"kind": "calc", "id": "Margin", "caption": "Margine", "expression": "{Profit} / {Revenue}"},
    ]
    s["canvases"][0]["visuals"].append(
        {"kind": "bar", "title": "misura e attributo scambiati", "roles": {"measures": ["Segment"], "detail": ["Sales"]}})
    errors = check_against(WorkbookSpec.model_validate(s), dataset)
    assert any("'Region' does not exist" in e for e in errors)
    assert any("'City' is not a date" in e for e in errors)
    assert any("Margin" in e and "Revenue" in e for e in errors)
    assert any("bar.measures wants a measure, Segment" in e for e in errors)
    assert any("bar.detail wants an attribute, Sales" in e for e in errors)


def test_ogni_tipo_ha_ruoli():
    assert len(ROLES) == 12 and all(ROLES.values())


def test_layout_senza_sovrapposizioni():
    kinds = ["bar", "table", "line", "area", "pivot", "tile", "radar"]
    visuals = [VisualSpec(kind=k, title=k, roles={r: ["X"] * rule.count for r, rule in ROLES[k].items()}) for k in kinds]
    boxes = layout(visuals)
    for i, (x, y, w, h) in enumerate(boxes):
        assert x >= 0 and x + w <= CANVAS_WIDTH
        for (x2, y2, w2, h2) in boxes[i + 1:]:
            assert x + w <= x2 or x2 + w2 <= x or y + h <= y2 or y2 + h2 <= y, "visual sovrapposti"
    assert boxes[1][2] == CANVAS_WIDTH                  # table a tutta larghezza
    assert boxes[0][2] == CANVAS_WIDTH                  # bar rimasto solo sulla riga prima della table
