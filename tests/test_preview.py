"""Anteprime: un renderer per ogni tipo, tema Tufte, accento sul valore estremo, note dove l'anteprima non è OAC."""
import pytest

from liveinsight.engine.preview import ACCENT, THEME, build
from liveinsight.engine.spec import ROLES

COLS = {"Seg": {"id": "Seg", "label": "Customer Segment", "type": "nominal"},
        "City": {"id": "City", "label": "City", "type": "nominal"},
        "Q": {"id": "Q", "label": "Order Date (trimestre)", "type": "ordinal", "grain": "quarter"},
        "Sales": {"id": "Sales", "label": "Sales", "type": "quantitative"},
        "Profit": {"id": "Profit", "label": "Profit", "type": "quantitative"},
        "Qty": {"id": "Qty", "label": "Quantity Ordered", "type": "quantitative"}}
SEG_ROWS = [{"Seg": "Consumer", "Sales": 1722719.78, "Profit": -204754.48, "Qty": 26387},
            {"Seg": "Corporate", "Sales": 3040035.94, "Profit": -352871.93, "Qty": 44191},
            {"Seg": "Home Office", "Sales": 2097629.77, "Profit": -244560.93, "Qty": 32448}]
ROLES_FOR = {"bar": {"measures": ["Sales"], "detail": ["Seg"]}, "hbar": {"measures": ["Sales"], "detail": ["Seg"]},
             "line": {"measures": ["Sales"], "detail": ["Q"]}, "area": {"measures": ["Sales"], "detail": ["Q"]},
             "table": {"row": ["Seg", "Sales", "Qty"]}, "pivot": {"row": ["Seg"], "col": ["Q"], "measures": ["Sales"]},
             "scatter": {"measures": ["Sales", "Qty"], "detail": ["Seg"]}, "radar": {"measures": ["Sales"], "detail": ["Seg"]},
             "boxplot": {"measures": ["Sales"], "detail": ["Seg"]}, "map": {"detail": ["City"], "size": ["Qty"]},
             "narrative": {"measures": ["Sales"], "row": ["Seg"]}, "tile": {"measures": ["Qty"], "detail": ["Seg"]}}


def proposal(kind, rows=SEG_ROWS, roles=None):
    roles = roles or ROLES_FOR[kind]
    ids = list(dict.fromkeys(c for cols in roles.values() for c in cols))
    return {"n": 1, "kind": kind, "title": "t", "roles": roles, "columns": [COLS[i] for i in ids], "rows": rows}


def test_ogni_tipo_ha_un_anteprima():
    assert set(ROLES_FOR) == set(ROLES)
    for kind in ROLES:
        rows = [dict(r, Q=f"2016 Q{i + 1}", City=f"C{i}") for i, r in enumerate(SEG_ROWS)]
        out = build(proposal(kind, rows))
        assert out["renderer"] in ("vega", "table", "pivot", "tile"), kind
        if out["renderer"] == "vega":
            assert out["spec"]["config"] is THEME and out["spec"]["data"]["values"]


def test_barre_tufte():
    spec = build(proposal("bar"))["spec"]
    bars, pos, neg = spec["layer"]                             # positivi: niente linea dello zero
    assert bars["encoding"]["y"]["axis"] is None               # poche barre: etichette dirette, niente asse dei valori
    assert pos["mark"]["baseline"] == "bottom" and neg["mark"]["baseline"] == "top"   # sempre fuori dalla barra
    assert bars["encoding"]["x"]["title"] is None              # niente titolo delle categorie (lo dice la card)
    assert bars["encoding"]["y"]["scale"]["zero"] is True      # barre da zero
    assert bars["encoding"]["x"]["sort"] is None               # ordine di OAC
    assert [r["_accent"] for r in spec["data"]["values"]] == [False, True, False]   # accento: Corporate
    assert bars["encoding"]["color"]["condition"]["value"] == ACCENT


def test_negativi_accento_zero_e_categorie_sullo_zero():
    spec = build(proposal("bar", roles={"measures": ["Profit"], "detail": ["Seg"]}))["spec"]
    assert [r["_accent"] for r in spec["data"]["values"]] == [False, True, False]   # -352.871: la perdita maggiore
    assert any(l["mark"]["type"] == "rule" for l in spec["layer"])                   # linea dello zero
    assert spec["layer"][0]["encoding"]["x"]["axis"]["orient"] == "top"              # categorie accanto allo zero
    assert spec["padding"]["bottom"] == 20


def test_etichette_lunghe_diventano_orizzontali():
    rows = [{"Seg": "Telephones and Communication", "Sales": 1.0}, {"Seg": "Paper", "Sales": 2.0}]   # > 20 caratteri
    spec = build(proposal("bar", rows))["spec"]
    assert spec["layer"][0]["encoding"]["y"]["field"] == "Seg"


def test_molte_categorie_tengono_l_asse():
    rows = [{"Seg": f"S{i}", "Sales": float(i)} for i in range(30)]
    bars = build(proposal("bar", rows))["spec"]["layer"]
    assert len(bars) == 1 and bars[0]["encoding"]["y"]["axis"] == {"format": ",.3~s"}
    rows = [{"Seg": f"Sottocategoria {i}", "Sales": float(i)} for i in range(20)]     # orizzontali: fino a 25 dirette
    layers = build(proposal("hbar", rows))["spec"]["layer"]
    assert len(layers) == 3 and layers[0]["encoding"]["x"]["axis"] is None


def test_mappa_tile_e_note():
    rows = [{"City": f"C{i}", "Qty": i} for i in range(40)]
    out = build(proposal("map", rows))
    assert out["note"] == [{"key": "preview.mapNoCoordinates", "params": {"count": 40, "shown": 20}}]
    assert len(out["spec"]["data"]["values"]) == 20
    assert out["spec"]["data"]["values"][0]["City"] == "C39"
    tile = build(proposal("tile"))
    assert tile["value"] == 26387 + 44191 + 32448 and tile["label"] == "Quantity Ordered"
    assert build(proposal("radar"))["note"][0]["key"] == "preview.radar"
    assert build(proposal("boxplot"))["note"][0]["key"] == "preview.boxplot"
    assert out["stand_in"] and build(proposal("radar"))["stand_in"]            # nessun luogo noto: barre dichiarate
    assert "stand_in" not in tile and "stand_in" not in build(proposal("bar"))


def test_mappa_vera_con_posizioni_indicative():
    rows = [{"City": "Hong Kong", "Qty": 101}, {"City": "New York", "Qty": 60}, {"City": "Frankfurt", "Qty": 40},
            {"City": "Las Plumas", "Qty": 5}, {"City": "Albany", "Qty": 0}]
    out = build(proposal("map", rows))
    assert "stand_in" not in out                                                # è una mappa, non un sostituto
    land, points, label = out["spec"]["layer"]
    assert land["mark"]["type"] == "geoshape" and land["data"]["format"] == {"type": "topojson", "feature": "land"}
    assert land["projection"] == points["projection"] == label["projection"]    # una proiezione per tutti i livelli
    assert land["projection"]["type"] == "equalEarth" and land["mark"]["clip"] is True
    values = points["data"]["values"]
    assert [v["City"] for v in values] == ["Hong Kong", "New York", "Frankfurt"]    # i grandi sotto, i piccoli sopra
    assert (values[1]["_lat"], values[1]["_lon"]) == pytest.approx((40.71, -74.01), abs=0.1)
    assert points["encoding"]["size"]["scale"]["zero"] is True                 # area proporzionale al valore
    assert [v["_accent"] for v in values] == [True, False, False] and label["data"]["values"][0]["City"] == "Hong Kong"
    assert out["note"] == [{"key": "preview.mapShown", "params": {"count": 3, "total": 5, "measure": "Quantity Ordered"}},
                           {"key": "preview.mapMissing", "params": {"places": "Las Plumas"}},
                           {"key": "preview.mapSkipped", "params": {"count": 1}}]


@pytest.mark.parametrize("kind", ["line", "area"])
def test_serie_etichetta_estremo_e_ultimo(kind):
    rows = [{"Q": f"2016 Q{i}", "Sales": v} for i, v in enumerate([5.0, 9.0, 3.0, 7.0], 1)]
    values = build(proposal(kind, rows))["spec"]["data"]["values"]
    assert [r["_mark"] for r in values] == [False, True, False, True] and [r["_accent"] for r in values] == [False, True, False, False]


# -- M3c: forme con più colonne ------------------------------------------------------------

def test_due_misure_barre_affiancate():
    p = proposal("bar", roles={"measures": ["Sales", "Profit"], "detail": ["Seg"]})
    spec = build(p)["spec"]
    bars = spec["layer"][0]
    assert bars["encoding"]["xOffset"]["field"] == "_serie"
    assert {r["_serie"] for r in spec["data"]["values"]} == {"Sales", "Profit"} and len(spec["data"]["values"]) == 6
    assert any(l["mark"]["type"] == "rule" for l in spec["layer"])                 # Profit negativo: linea dello zero
    assert bars["encoding"]["color"]["legend"]["orient"] == "top"


def test_ordinamento_come_nel_workbook():
    p = dict(proposal("bar"), sort="desc")
    assert [r["Seg"] for r in build(p)["spec"]["data"]["values"]] == ["Corporate", "Home Office", "Consumer"]
    p = dict(proposal("bar"), sort="asc")
    assert [r["Seg"] for r in build(p)["spec"]["data"]["values"]] == ["Consumer", "Home Office", "Corporate"]


def test_linee_con_colore_etichette_dirette_distanziate():
    rows = [{"Q": q, "Seg": s, "Sales": v} for q in ("2016 Q1", "2016 Q2")
            for s, v in (("A", 100.0), ("B", 99.0), ("C", 50.0))]
    spec = build(proposal("line", rows, {"measures": ["Sales"], "detail": ["Q"], "color": ["Seg"]}))["spec"]
    labels = spec["layer"][1]["data"]["values"]
    ys = sorted(l["_y"] for l in labels)
    assert [l["_testo"] for l in labels] == ["A", "B", "C"]
    assert all(b - a >= 100 * 0.07 - 1e-9 for a, b in zip(ys, ys[1:]))              # A e B non si sovrappongono
    assert spec["layer"][0]["encoding"]["color"]["legend"] is None                   # etichette dirette, niente legenda


def test_trellis_piccoli_multipli():
    rows = [{"Q": q, "Seg": s, "Sales": 1.0} for q in ("2016", "2017") for s in ("A", "B", "C")]
    spec = build(proposal("line", rows, {"measures": ["Sales"], "detail": ["Q"], "col": ["Seg"]}))["spec"]
    assert spec["facet"]["column"]["field"] == "Seg" and "resolve" not in spec      # scala condivisa (default)
    assert isinstance(spec["spec"]["width"], int)                                    # "container" non vale nei facet


def test_accento_sulla_serie_piu_grande():
    rows = [{"Q": "2016", "Seg": s, "Sales": v} for s, v in (("A", 10.0), ("B", 90.0), ("C", 50.0))]
    spec = build(proposal("line", rows, {"measures": ["Sales"], "detail": ["Q"], "color": ["Seg"]}))["spec"]
    rng = spec["layer"][0]["encoding"]["color"]["scale"]["range"]
    assert rng[1] == ACCENT and rng.count(ACCENT) == 1
