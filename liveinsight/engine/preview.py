"""Preview of a proposed visual: a Vega-Lite spec (or table, pivot, tile) from the proposal's data.

One theme for every chart, after Tufte (tufte-viz skill):
- no border, no grid except a very light grey on the value axis, minimal axes, few ticks;
- grey by default and ONE accent colour for what matters (the extreme value);
- direct labels instead of legend and value axis when bars are few (eraser test: axis and labels must not say the
  same thing twice);
- bars always from zero, horizontal text, no 3D, shadows or gradients.
The title sits outside the chart (in the card), so it does not compete with the data.

Fidelity: the preview uses the same numbers as the platform and OAC's category order (alphabetical). Where
Vega-Lite cannot reproduce the OAC visual (radar, box plot, narrative) it shows an honest alternative with a note,
instead of a chart that looks like OAC's but is not. The map is a real map, with approximate positions
(liveinsight/engine/geo.py); it becomes bars only if no place is found.
"""
from liveinsight.engine.geo import locate
from liveinsight.messages import msg, token

INK, GREY, LIGHT, ACCENT = "#333333", "#9b9b9b", "#ececec", "#c0502e"
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"
FEW, FEW_H = 12, 25          # up to this many bars (vertical, horizontal): direct labels, no value axis
VALUE_FORMAT = ",.3~s"       # 3.04M · 314k · 1.38k (the frontend applies the UI language's locale)
LAND_URL = "/land-110m.json"  # land, TopoJSON served by the frontend (web/public, liveinsight/engine/data/README.md)
MAX_CIRCLE = 500             # area in pixels of the map's largest circle
MAP_HEIGHT = 210             # with the 56° S - 78° N band (about 2.7:1) the card width fills the map

THEME = {
    "background": None,
    "font": FONT,
    "view": {"stroke": None},
    "padding": {"left": 4, "right": 12, "top": 8, "bottom": 4},
    "axis": {"domain": False, "ticks": False, "grid": False, "labelColor": "#666", "titleColor": "#666",
             "labelFontSize": 11, "titleFontSize": 11, "titleFontWeight": "normal", "labelPadding": 6,
             "labelAngle": 0, "labelOverlap": True, "labelLimit": 180},
    "axisQuantitative": {"grid": True, "gridColor": LIGHT, "tickCount": 4, "labelFlush": True},
    "bar": {"color": GREY, "binSpacing": 2},
    "line": {"color": INK, "strokeWidth": 1.5},
    "area": {"color": LIGHT, "line": {"color": INK, "strokeWidth": 1.2}},
    "point": {"color": GREY, "filled": True, "size": 40},
    "text": {"font": FONT, "fontSize": 11, "color": "#555"},
    "legend": {"disable": True},
    "title": {"anchor": "start", "fontSize": 13, "fontWeight": "normal"},
}


def _col(p, cid):
    return next(c for c in p["columns"] if c["id"] == cid)


def _extreme(rows, field):
    """Index of the value farthest from zero: what the accent underlines."""
    values = [(abs(r.get(field) or 0), i) for i, r in enumerate(rows)]
    return max(values)[1] if values else None


def _outward(enc, measure, horizontal, gap=4):
    """Value labels outwards from zero: past the end of the bar or point, above (or right) for positives, below
    (or left) for negatives. Collision test: never inside the mark."""
    text = {"field": measure, "type": "quantitative", "format": VALUE_FORMAT}
    if horizontal:
        pos = {"align": "left", "dx": gap, "baseline": "middle"}
        neg = {"align": "right", "dx": -gap, "baseline": "middle"}
    else:
        pos = {"align": "center", "baseline": "bottom", "dy": -gap}
        neg = {"align": "center", "baseline": "top", "dy": gap}
    return [{"transform": [{"filter": f"datum['{measure}'] >= 0"}], "mark": {"type": "text", **pos},
             "encoding": {**enc, "text": text}},
            {"transform": [{"filter": f"datum['{measure}'] < 0"}], "mark": {"type": "text", **neg},
             "encoding": {**enc, "text": text}}]


def _bars(p, horizontal, detail, measure, rows, note=None):
    m = _col(p, measure)
    i_ext = _extreme(rows, measure)
    rows = [dict(r, _accent=(i == i_ext)) for i, r in enumerate(rows)]
    values = [r.get(measure) or 0 for r in rows]
    negative, all_negative = min(values, default=0) < 0, max(values, default=0) <= 0
    few = len(rows) <= (FEW_H if horizontal else FEW)
    # categories: no title (the card says it) and horizontal text; with only negatives they sit on zero
    cat_axis = {"labelLimit": 220, "orient": ("right" if horizontal else "top") if all_negative else None}
    cat = {"field": detail, "type": "nominal", "title": None, "sort": None,
           "axis": {k: v for k, v in cat_axis.items() if v is not None}}
    val = {"field": measure, "type": "quantitative", "title": None if few else m["label"],
           "scale": {"zero": True}, "axis": None if few else {"format": VALUE_FORMAT}}
    color = {"condition": {"test": "datum._accent", "value": ACCENT}, "value": GREY}
    enc = {"y": cat, "x": val} if horizontal else {"x": cat, "y": val}
    layers = [{"mark": {"type": "bar"}, "encoding": {**enc, "color": color}}]
    if negative:                                        # the zero line is data when there are negatives
        zero = {"datum": 0, "type": "quantitative", "axis": None, "title": None}   # same axis as the bars, or Vega-Lite fails
        layers.append({"mark": {"type": "rule", "color": "#888", "strokeWidth": 1},
                       "encoding": {"x": zero} if horizontal else {"y": zero}})
    if few:
        layers += _outward(enc, measure, horizontal)
    spec = {"data": {"values": rows}, "layer": layers, "width": "container",
            "height": max(24 * len(rows), 120) if horizontal else 260}
    if few:                                             # room for the labels past the longest bars
        pad = {"right": 52, **({"left": 52} if negative else {})} if horizontal else \
              {"top": 20, **({"bottom": 20} if negative else {})}
        spec["padding"] = {**THEME["padding"], **pad}
    return _vega(spec, note)


def _series(p, kind, detail, measure, rows):
    """Line or area on a time axis: direct label on the extreme value and on the last one, outwards."""
    d, m = _col(p, detail), _col(p, measure)
    x = {"field": detail, "type": "ordinal", "title": None, "sort": None,
         "axis": {"labelOverlap": "greedy", "labelSeparation": 12}}
    y = {"field": measure, "type": "quantitative", "title": None, "scale": {"zero": True},
         "axis": {"format": VALUE_FORMAT}}
    i_ext = _extreme(rows, measure)
    marks = [dict(r, _mark=(i == i_ext or i == len(rows) - 1), _accent=(i == i_ext)) for i, r in enumerate(rows)]
    # right of the point, at the same height: above or below, a series' extreme touches the border (and the axis)
    labels = [{"transform": [{"filter": "datum._mark"}],
               "mark": {"type": "text", "align": "left", "dx": 7, "baseline": "middle"},
               "encoding": {"x": x, "y": y, "text": {"field": measure, "type": "quantitative", "format": VALUE_FORMAT}}}]
    mark = ({"type": "area", "fill": "#f1f1f1", "fillOpacity": 1, "line": {"color": INK, "strokeWidth": 1.2}}
            if kind == "area" else {"type": "line"})
    layers = [{"mark": mark, "encoding": {"x": x, "y": y}},
              {"transform": [{"filter": "datum._mark"}],
               "mark": {"type": "point", "size": 36, "filled": True, "opacity": 1},
               "encoding": {"x": x, "y": y, "color": {"condition": {"test": "datum._accent", "value": ACCENT}, "value": INK}}},
              *labels]
    return _vega({"data": {"values": marks}, "layer": layers, "width": "container", "height": 240,
                  "padding": {**THEME["padding"], "top": 12, "right": 52},
                  "description": f"{m['label']} {token('preview.by')} {d['label']}"})


def _scatter(p, x_id, y_id, detail, rows):
    """Direct labels on the two extremes (the highest, accented, and the rightmost); Y axis with a horizontal title."""
    x, y, d = _col(p, x_id), _col(p, y_id), _col(p, detail)
    i_y, i_x = _extreme(rows, y_id), _extreme(rows, x_id)
    rows = [dict(r, _accent=(i == i_y), _label=(i in (i_y, i_x))) for i, r in enumerate(rows)]
    enc = {"x": {"field": x_id, "type": "quantitative", "title": x["label"], "axis": {"format": VALUE_FORMAT}},
           "y": {"field": y_id, "type": "quantitative", "title": y["label"],
                 "axis": {"format": VALUE_FORMAT, "titleAngle": 0, "titleAlign": "left", "titleAnchor": "end",
                          "titleX": 0, "titleY": -12, "titleBaseline": "bottom"}}}
    return _vega({"data": {"values": rows}, "width": "container", "height": 260,
                  "padding": {**THEME["padding"], "top": 24, "right": 24}, "layer": [
        {"mark": {"type": "point", "filled": True, "opacity": 0.8},
         "encoding": {**enc, "color": {"condition": {"test": "datum._accent", "value": ACCENT}, "value": GREY},
                      "tooltip": [{"field": detail, "title": d["label"]},
                                  {"field": x_id, "title": x["label"], "format": ",.2f"},
                                  {"field": y_id, "title": y["label"], "format": ",.2f"}]}},
        {"transform": [{"filter": "datum._label"}], "mark": {"type": "text", "align": "right", "dx": -7, "baseline": "middle"},
         "encoding": {**enc, "text": {"field": detail}}}]})


QUIET = ["#3f5d6b", "#9bb5bf", "#8c8c8c", "#5f7f5a", "#c8c8c8"]     # series: sober, well-distinct tints


def _palette(series, totals):
    """One accent only, on the series that matters (total farthest from zero); the others in sober tints."""
    top = max(series, key=lambda s: abs(totals.get(s, 0))) if series else None
    quiet = iter(QUIET * 3)
    return [ACCENT if s == top else next(quiet) for s in series]
CELL_WIDTH = 170            # small multiples: fixed width per panel ("container" does not work in facets)


def _series_legend():
    """Legend on top, horizontal and without title: with side-by-side bars direct labels would collide."""
    return {"disable": False, "orient": "top", "title": None, "symbolType": "square", "labelFontSize": 11}


def _grouped_bars(p, horizontal, detail, long_rows, n_series):
    """Side-by-side bars (as OAC draws them for color and for several measures): _serie, _valore in long format."""
    d = _col(p, detail)
    cat = {"field": detail, "type": "nominal", "title": None, "sort": None, "axis": {"labelLimit": 220}}
    val = {"field": "_valore", "type": "quantitative", "title": None, "scale": {"zero": True},
           "axis": {"format": VALUE_FORMAT}}
    series = list(dict.fromkeys(r["_serie"] for r in long_rows))
    totals = {}
    for r in long_rows:
        totals[r["_serie"]] = totals.get(r["_serie"], 0) + (r["_valore"] or 0)
    color = {"field": "_serie", "type": "nominal", "sort": series,
             "scale": {"domain": series, "range": _palette(series, totals)}, "legend": _series_legend()}
    offset = {"field": "_serie", "type": "nominal", "sort": series}
    enc = ({"y": cat, "x": val, "yOffset": offset} if horizontal else {"x": cat, "y": val, "xOffset": offset})
    layers = [{"mark": {"type": "bar"}, "encoding": {**enc, "color": color,
                                                   "tooltip": [{"field": detail, "title": d["label"]},
                                                               {"field": "_serie", "title": token("preview.series")},
                                                               {"field": "_valore", "title": token("preview.value"), "format": ",.2f"}]}}]
    if any((r["_valore"] or 0) < 0 for r in long_rows):
        zero = {"datum": 0, "type": "quantitative", "axis": val["axis"], "title": None}   # same axis as the bars
        layers.append({"mark": {"type": "rule", "color": "#888", "strokeWidth": 1},
                       "encoding": {"x": zero} if horizontal else {"y": zero}})
    height = max(14 * n_series * len({r[detail] for r in long_rows}), 160) if horizontal else 260
    return {"data": {"values": long_rows}, "layer": layers, "width": "container", "height": height}


def _multi_measure_bars(p, horizontal, detail, measures, rows, note=None):
    labels = {m: _col(p, m)["label"] for m in measures}
    long_rows = [{detail: r.get(detail), "_serie": labels[m], "_valore": r.get(m)} for r in rows for m in measures]
    return _vega(_grouped_bars(p, horizontal, detail, long_rows, len(measures)), note)


def _color_bars(p, horizontal, detail, color, measure, rows, note=None):
    long_rows = [{detail: r.get(detail), "_serie": r.get(color), "_valore": r.get(measure)} for r in rows]
    return _vega(_grouped_bars(p, horizontal, detail, long_rows, len({r.get(color) for r in rows})), note)


def _dodge(values, gap):
    """Label positions in data units, at least `gap` apart: collision test on direct labels."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = list(values)
    for a, b in zip(order, order[1:]):
        if out[b] - out[a] < gap:
            out[b] = out[a] + gap
    return out


def _multi_line(p, detail, color, measure, rows):
    """One line per series with a direct label at the line end instead of the legend (few series)."""
    x = {"field": detail, "type": "ordinal", "title": None, "sort": None,
         "axis": {"labelOverlap": "greedy", "labelSeparation": 12}}
    y = {"field": measure, "type": "quantitative", "title": None, "scale": {"zero": True}, "axis": {"format": VALUE_FORMAT}}
    series = list(dict.fromkeys(r.get(color) for r in rows))
    last_x = list(dict.fromkeys(r.get(detail) for r in rows))[-1]
    last = {r.get(color): r.get(measure) or 0 for r in rows if r.get(detail) == last_x}
    colors = {"field": color, "type": "nominal", "sort": series, "legend": None,
              "scale": {"domain": series, "range": _palette(series, last)}}
    ends = [next((r for r in reversed(rows) if r.get(color) == s and r.get(detail) == last_x), None) for s in series]
    ends = [(s, e) for s, e in zip(series, ends) if e is not None]
    top = max((abs(r.get(measure) or 0) for r in rows), default=1) or 1
    placed = _dodge([e.get(measure) or 0 for _, e in ends], gap=top * 0.07)
    labels = [{detail: last_x, color: s, "_y": yy, "_testo": str(s)} for (s, _), yy in zip(ends, placed)]
    return _vega({"width": "container", "height": 260,
                  "padding": {**THEME["padding"], "right": 8 + 7 * max((len(str(s)) for s in series), default=4)},
                  "layer": [
                      {"data": {"values": rows}, "mark": {"type": "line"}, "encoding": {"x": x, "y": y, "color": colors}},
                      {"data": {"values": labels}, "mark": {"type": "text", "align": "left", "dx": 6, "baseline": "middle"},
                       "encoding": {"x": x, "y": {"field": "_y", "type": "quantitative"},
                                    "text": {"field": "_testo"}, "color": colors}}]})


def _trellis(p, detail, col, measure, rows):
    """Small multiples (OAC's trellis): one panel per value, same scale, same drawing."""
    x = {"field": detail, "type": "ordinal", "title": None, "sort": None,
         "axis": {"labelOverlap": "greedy", "labelSeparation": 8}}
    y = {"field": measure, "type": "quantitative", "title": None, "scale": {"zero": True}, "axis": {"format": VALUE_FORMAT}}
    panels = list(dict.fromkeys(r.get(col) for r in rows))
    return _vega({"data": {"values": rows},
                  "facet": {"column": {"field": col, "type": "nominal", "sort": panels, "title": None,
                                       "header": {"labelFontSize": 12, "labelColor": "#444"}}},
                  "spec": {"width": CELL_WIDTH, "height": 180, "mark": {"type": "line"}, "encoding": {"x": x, "y": y}},
                  "spacing": 12})


def _map(p, place, measure, rows):
    """Circles on the places, over the land in very light grey (equalEarth projection, as in the official Vega-Lite
    examples: every layer has its data and the same projection).

    Integrity (lie factor 1): the circle's AREA is proportional to the value, scale from zero. Null or negative
    values have no area: they stay out and the note says so. Accent and label on the largest value; large circles
    are drawn first, so the small ones stay visible on top."""
    m = _col(p, measure)
    located, unknown, not_positive = [], [], 0
    for r in rows:
        where, value = locate(r.get(place)), r.get(measure)
        if where is None:
            unknown.append(str(r.get(place)))
        elif value is None or value <= 0:
            not_positive += 1
        else:
            located.append({**r, "_lat": where[0], "_lon": where[1]})
    if not located:
        top = sorted(rows, key=lambda r: -(r.get(measure) or 0))[:20]
        return {**_bars(p, True, place, measure, top,
                        [msg("preview.mapNoCoordinates", count=len(rows), shown=len(top))]), "stand_in": True}
    located.sort(key=lambda r: -r[measure])
    located = [dict(r, _accent=(i == 0)) for i, r in enumerate(located)]
    # frames the inhabited land (56° S - 78° N): Antarctica carries no data (eraser test)
    frame = {"type": "Feature", "properties": {}, "geometry": {"type": "MultiPoint", "coordinates": [
        [-180, 0], [180, 0], [0, 78], [0, -56], [-180, 50], [180, 50], [-180, -45], [180, -45]]}}
    projection = {"type": "equalEarth", "fit": frame}
    where = {"longitude": {"field": "_lon", "type": "quantitative"},
             "latitude": {"field": "_lat", "type": "quantitative"}}
    label = f"datum['{place}'] + ' ' + format(datum['{measure}'], '{VALUE_FORMAT}')"
    layers = [
        {"data": {"url": LAND_URL, "format": {"type": "topojson", "feature": "land"}}, "projection": projection,
         "mark": {"type": "geoshape", "fill": LIGHT, "stroke": None, "clip": True}},
        {"data": {"values": located}, "projection": projection,
         "mark": {"type": "circle", "opacity": 0.8, "stroke": "white", "strokeWidth": 0.6},
         "encoding": {**where,
                      "size": {"field": measure, "type": "quantitative", "legend": None,
                               "scale": {"zero": True, "rangeMin": 0, "rangeMax": MAX_CIRCLE}},
                      "color": {"condition": {"test": "datum._accent", "value": ACCENT}, "value": GREY}}},
        # label right of the largest circle, past its radius (collision test)
        {"data": {"values": located[:1]}, "projection": projection, "transform": [{"calculate": label, "as": "_label"}],
         "mark": {"type": "text", "align": "left", "baseline": "middle", "dx": round((MAX_CIRCLE / 3.1416) ** 0.5) + 4,
                  "color": INK},
         "encoding": {**where, "text": {"field": "_label"}}},
    ]
    note = [msg("preview.mapShown", count=len(located), total=len(rows), measure=m["label"])]
    if unknown:
        note.append(msg("preview.mapMissingMore" if len(unknown) > 5 else "preview.mapMissing",
                        places=", ".join(unknown[:5])))
    if not_positive:
        note.append(msg("preview.mapSkipped", count=not_positive))
    return _vega({"layer": layers, "width": "container", "height": MAP_HEIGHT}, note)


def _vega(spec, note=None):
    return {"renderer": "vega", "spec": {"$schema": "https://vega.github.io/schema/vega-lite/v6.json", **spec,
                                         "config": THEME}, **({"note": note} if note else {})}


def _table(p, ids, note=None):
    return {"renderer": "table", "columns": [_col(p, c) for c in ids], "rows": p["rows"], **({"note": note} if note else {})}


STAND_IN = {"radar", "boxplot", "narrative"}          # stand-in preview: the UI says so before the chart


def build(p: dict) -> dict:
    """Preview of a proposal (Session.proposal_json): {"renderer": vega|table|pivot|tile, ..., "note"?, "stand_in"?}.
    "note" is a list of messages (liveinsight.messages): the frontend writes them in the UI language."""
    out = _build(p)
    return {**out, "stand_in": True} if p["kind"] in STAND_IN else out


def _build(p: dict) -> dict:
    kind, roles, rows = p["kind"], p["roles"], p["rows"]
    first = lambda role: roles[role][0]
    if p.get("sort") and kind in ("bar", "hbar"):          # as the sort saved in the workbook
        rows = sorted(rows, key=lambda r: r.get(first("measures")) or 0, reverse=p["sort"] == "desc")
    if kind in ("bar", "hbar"):
        long_labels = max((len(str(r.get(first("detail")) or "")) for r in rows), default=0) > 20
        horizontal = kind == "hbar" or long_labels
        if len(roles["measures"]) > 1:
            return _multi_measure_bars(p, horizontal, first("detail"), roles["measures"], rows)
        if roles.get("color"):
            return _color_bars(p, horizontal, first("detail"), first("color"), first("measures"), rows)
        return _bars(p, horizontal, first("detail"), first("measures"), rows)
    if kind == "line" and roles.get("color"):
        return _multi_line(p, first("detail"), first("color"), first("measures"), rows)
    if kind == "line" and roles.get("col"):
        return _trellis(p, first("detail"), first("col"), first("measures"), rows)
    if kind in ("line", "area"):
        return _series(p, kind, first("detail"), first("measures"), rows)
    if kind == "scatter":
        return _scatter(p, roles["measures"][0], roles["measures"][1], first("detail"), rows)
    if kind == "table":
        return _table(p, roles["row"])
    if kind == "pivot":
        return {"renderer": "pivot", "row": _col(p, first("row")), "col": _col(p, first("col")),
                "value": _col(p, first("measures")), "rows": rows}
    if kind == "tile":
        m = first("measures")
        return {"renderer": "tile", "label": _col(p, m)["label"], "value": sum(r.get(m) or 0 for r in rows),
                "note": [msg("preview.tileTotal", count=len(rows), of=_col(p, first("detail"))["label"])]}
    if kind == "radar":
        return _bars(p, True, first("detail"), first("measures"), rows,
                     [msg("preview.radar")])
    if kind == "map":
        return _map(p, first("detail"), first("size"), rows)
    if kind == "boxplot":
        return _bars(p, False, first("detail"), first("measures"), rows,
                     [msg("preview.boxplot")])
    if kind == "narrative":
        return _table(p, [first("row"), first("measures")], [msg("preview.narrative")])
    raise ValueError(f"visual kind without a preview: {kind}")
