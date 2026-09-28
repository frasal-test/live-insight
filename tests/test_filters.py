"""Filtri dei visual. I riferimenti sono i workbook fatti a mano da Francesco in OAC (24/9): "With Filters" (filtro
su attributo, su trimestri, su valori di una misura, su anno + valori) e "More Filters" (mesi, giorni, intervallo
di giorni). Il generatore deve produrre ESATTAMENTE le barre filtri che OAC ha salvato (a parte l'id casuale)."""
import copy
import json

import pytest

from conftest import FIXTURES
from liveinsight.platforms.oac.li_dva import Workbook, period_filter_value

WITH_FILTERS = FIXTURES / "oac/with_filters.json"
MORE_FILTERS = FIXTURES / "oac/more_filters.json"               # months, days, range of days


@pytest.fixture(scope="module")
def saved():
    return json.loads(WITH_FILTERS.read_text())


def normalized(collections):
    """Le barre filtri senza gli id casuali; le colonne di valutazione di un filtro su misura come insieme
    (un solo esempio con due colonne: l'ordine che usa OAC non si può ancora dedurre)."""
    out = copy.deepcopy(collections)
    for fc in out["children"]:
        for f in fc["filterControls"]["children"]:
            f.pop("filterID")
            if "filterByColumns" in f:
                f["filterByColumns"]["children"].sort(key=lambda c: c["expression"])
    return out


def rebuild(saved):
    wb = Workbook(saved)                                   # visual-template dallo stesso workbook
    wb.reset()
    wb.date_column("OrderDate", "Order Date", "quarter")
    wb.column("Sales", "Sales")
    wb.column("ProductCategory", "Product Category")
    wb.date_column("OrderDate_8", "Order Date", "month")
    wb.column("CustomerSegment", "Customer Segment")
    wb.date_column("OrderDate_14", "Order Date", "year")
    wb.column("Profit", "Profit")
    c = wb.canvas("Filtri")
    box = (0, 0, 354, 351)
    wb.visual(c, "line", "Filter on Attribute", {"measures": ["Sales"], "detail": ["OrderDate"]}, box,
              filters=[{"column": "ProductCategory", "op": "in", "values": ["Technology"]}])
    wb.visual(c, "bar", "Filter on Date", {"measures": ["Sales"], "detail": ["ProductCategory"]}, box,
              filters=[{"column": "OrderDate", "op": "in", "values": ["2013 Q1", "2014 Q1", "2015 Q1", "2016 Q1"]}])
    wb.visual(c, "table", "Filter on Values", {"row": ["OrderDate_8", "Sales"]}, box,
              filters=[{"column": "Sales", "op": "between", "values": [126000, 167800], "by": ["OrderDate_8"]}])
    wb.visual(c, "bar", "Filter on two items (date + values)",
              {"measures": ["Sales"], "detail": ["ProductCategory"], "color": ["CustomerSegment"]}, box,
              filters=[{"column": "OrderDate_14", "op": "in", "values": ["2016"]},
                       {"column": "Profit", "op": "between", "values": [-92680, -85460],
                        "by": ["ProductCategory", "CustomerSegment"]}])
    return wb.definition()


def test_barre_filtri_come_with_filters(saved):
    built = rebuild(saved)
    assert normalized(built["filterControlCollections"]) == normalized(saved["filterControlCollections"])


def test_colonne_del_filtro_nei_criteria_anche_se_il_grafico_non_le_mostra(saved):
    built = rebuild(saved)
    assert built["criteria"]["columns"] == saved["criteria"]["columns"]
    assert built["criteria"]["criteriaConfig"]["settings"]["columnPropertyMap"] == \
        saved["criteria"]["criteriaConfig"]["settings"]["columnPropertyMap"]


def test_il_layout_collega_ogni_visual_alla_sua_barra_filtri(saved):
    built = rebuild(saved)
    items = built["layouts"]["children"][0]["children"]
    assert [i["filterControlCollectionName"] for i in items] == [i["content"]["viewName"] for i in items]
    assert [i["filterControlCollectionName"] for i in items] == \
        [i["filterControlCollectionName"] for i in saved["layouts"]["children"][0]["children"]]


def test_visual_senza_filtri_senza_barra(saved):
    wb = Workbook(saved)
    wb.reset()
    wb.column("Sales", "Sales")
    wb.column("ProductCategory", "Product Category")
    wb.visual(wb.canvas("x"), "bar", "t", {"measures": ["Sales"], "detail": ["ProductCategory"]}, (0, 0, 1, 1))
    d = wb.definition()
    assert "filterControlCollections" not in d
    assert "filterControlCollectionName" not in d["layouts"]["children"][0]["children"][0]


def test_periodi():
    assert period_filter_value("2016", "year") == ("2016-01-01T00:00:00", "2016")
    assert period_filter_value("2014 Q3", "quarter") == ("2014-07-01T00:00:00", "Q3 2014")
    assert period_filter_value("2015-08", "month") == ("2015-08-01T00:00:00", "August 2015")      # "More Filters"
    assert period_filter_value("2013-01-20", "day") == ("2013-01-20T00:00:00", "01/20/2013")
    with pytest.raises(ValueError, match="2016 Q1"):
        period_filter_value("2016", "quarter")


def test_mesi_giorni_e_intervallo_come_more_filters():
    saved = json.loads(MORE_FILTERS.read_text())
    discount = saved["criteria"]["columns"]["children"][0]                  # un calcolo su Discount, id di OAC
    wb = Workbook(saved)
    wb.reset()
    wb.calculation(discount["columnID"], discount["columnHeading"]["caption"]["text"], "{Discount}")
    wb.date_column("OrderDate_3", "Order Date", "month")
    wb.date_column("OrderDate_7", "Order Date", "day")
    c, box = wb.canvas("Filtri"), (0, 0, 400, 300)
    m = discount["columnID"]
    wb.visual(c, "line", "filter some months", {"measures": [m], "detail": ["OrderDate_3"]}, box,
              filters=[{"column": "OrderDate_3", "op": "in",
                        "values": ["2015-08", "2015-07", "2015-06", "2015-10", "2015-09"]}])
    wb.visual(c, "line", "filter interval of days", {"measures": [m], "detail": ["OrderDate_7"]}, box,
              filters=[{"column": "OrderDate_7", "op": "between", "values": ["2015-09-01", "2015-11-30"]}])
    wb.visual(c, "line", "filter some specific days", {"measures": [m], "detail": ["OrderDate_7"]}, box,
              filters=[{"column": "OrderDate_7", "op": "in", "values": ["2013-01-20", "2013-01-24", "2013-02-11",
                                                                         "2013-02-28", "2013-03-13", "2013-04-08"]}])
    built = wb.definition()
    assert normalized(built["filterControlCollections"]) == normalized(saved["filterControlCollections"])
    # le colonne dei filtri (mese e giorno); la prima è una colonna Discount rinominata a mano in OAC, fuori tema
    assert built["criteria"]["columns"]["children"][1:] == saved["criteria"]["columns"]["children"][1:]
    assert built["criteria"]["criteriaConfig"]["settings"]["columnPropertyMap"] == \
        saved["criteria"]["criteriaConfig"]["settings"]["columnPropertyMap"]
