"""M3c: ruoli con più colonne e ordinamento. Il riferimento è il workbook "Varianti" fatto a mano in OAC:
partendo dai template a una colonna per ruolo, resize_roles e set_sort devono produrre ESATTAMENTE
la struttura che OAC ha salvato (modello logico e fisico)."""
import copy
import json

import pytest

from liveinsight.platforms.oac.li_dva import PLUGINS, logical_roles, resize_roles, set_sort, template_roles
from liveinsight.platforms.oac.target import bundled



@pytest.fixture(scope="module")
def varianti():
    views = [v for v in bundled("varianti.json")["views"]["children"] if v["type"] == "saw:pluginView"]
    return {f"V{v['viewName'].split('!')[1]}": v for v in views}                 # view!2 = "V2 bar due misure"


@pytest.fixture(scope="module")
def base():
    return {v["pluginType"]: v for v in bundled("examples.json")["views"]["children"] if v["type"] == "saw:pluginView"}


def models(view):
    """Le parti che contano per i dati: modello logico e fisico, vista annidata."""
    return {"dataModels": view["dataModels"], "nestedViews": view.get("nestedViews")}


def test_due_misure_come_v2(base, varianti):
    view = copy.deepcopy(base[PLUGINS["bar"]])
    resize_roles(view, template_roles(view), {"measures": ["Sales", "Profit"], "detail": ["CustomerSegment"]})
    assert models(view) == models(varianti["V2"])


def test_tabella_a_cinque_colonne_come_v4(base, varianti):
    view = copy.deepcopy(base[PLUGINS["table"]])
    cols = ["CustomerSegment", "ProductCategory", "OrderPriority", "Sales", "QuantityOrdered"]
    resize_roles(view, template_roles(view), {"row": cols})
    got, want = models(view), models(varianti["V4"])
    got_edges = got["dataModels"]["children"][0]["logicalDataModel"]["settings"]["logicalDataModel"]["logicalEdges"]
    want_edges = want["dataModels"]["children"][0]["logicalDataModel"]["settings"]["logicalDataModel"]["logicalEdges"]
    assert got_edges["row"] == want_edges["row"]
    assert got["dataModels"]["children"][0]["edges"] == want["dataModels"]["children"][0]["edges"]


def test_ordinamento_come_v5(base, varianti):
    view = copy.deepcopy(base[PLUGINS["hbar"]])
    resize_roles(view, template_roles(view), {"measures": ["QuantityOrdered"], "detail": ["ProductSubCategory"]})
    set_sort(view, "ProductSubCategory", "QuantityOrdered", "desc")
    assert models(view) == models(varianti["V5"])


def test_togliere_il_colore_riporta_alla_line_base(base, varianti):
    view = copy.deepcopy(varianti["V1"])
    resize_roles(view, template_roles(view), {"measures": ["Sales"], "detail": ["OrderDate"]})
    line = base[PLUGINS["line"]]
    edges = lambda v: v["dataModels"]["children"][0]["logicalDataModel"]["settings"]["logicalDataModel"]["logicalEdges"]
    assert edges(view)["color"] == edges(line)["color"]               # resta solo il segnaposto delle misure
    assert view["nestedViews"] == line["nestedViews"]


def test_colore_con_altre_colonne_e_serie(varianti):
    view = copy.deepcopy(varianti["V1"])
    resize_roles(view, template_roles(view), {"measures": ["Profit"], "detail": ["OrderYear"], "color": ["CustomerSegment"]})
    assert logical_roles(view) == {"measures": ["Profit"], "color": ["CustomerSegment"], "detail": ["OrderYear"]}
    col_axis = view["nestedViews"]["children"][0]["view"]["dataModels"]["children"][0]["edges"]["children"][1]
    assert [l.get("columnID") for l in col_axis["edgeLayers"]["children"]] == ["CustomerSegment", None]   # prima del segnaposto
    assert "min.Profit" in json.dumps(view) and "Sales" not in json.dumps(view)


def test_colonne_con_lo_stesso_id_del_template(base):
    """Le colonne richieste possono chiamarsi come colonne del template, in altri ruoli: niente confusione."""
    view = copy.deepcopy(base[PLUGINS["bar"]])
    tpl = template_roles(view)                                       # {"measures": ["Sales"], "detail": ["ProductCategory"]}
    resize_roles(view, tpl, {"measures": ["ProductCategory"], "detail": ["Sales"]})
    assert logical_roles(view) == {"measures": ["ProductCategory"], "detail": ["Sales"]}
