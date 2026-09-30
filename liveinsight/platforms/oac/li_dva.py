"""Generator of Oracle Analytics workbooks (.dva) from a template workbook.

Usage:
    wb = Workbook("templates/Examples.dva")
    wb.reset()
    wb.column("Sales", "Sales")
    wb.date_column("OrderYear", "Order Date", "year")
    wb.calculation("AOV", "Avg Order Value", "{Sales} / (COUNT(DISTINCT {Order ID}))")
    c = wb.canvas("Sales")
    wb.visual(c, "bar", "Title", {"measures": ["AOV"], "detail": ["OrderYear"]}, (0, 0, 600, 400))
    wb.rename("New workbook")
    wb.save("new.dva")

Rules worked out by reverse engineering (see FORMATO-DVA.md):
- every visual has its binding in the logical and in the physical model: rebind() rewires both, never by hand;
- new ids are checked against the whole JSON, because deleted visuals leave references behind;
- the workbook name sits in the archive headers and in the two MANIFEST.MF;
- a visual's filters sit in its filter bar (filterControlCollections, one per visual, named like the visual),
  linked from the layout with filterControlCollectionName: workbooks "With Filters" and "More Filters" made by
  hand in OAC (24/9).
"""
import copy
import json
import re
import uuid
import zipfile
import zlib

from liveinsight.platforms.oac.arc_codec import parse, serialize

PLUGINS = {
    # templates/Examples.dva
    "bar": "oracle.bi.tech.chart.bar",
    "hbar": "oracle.bi.tech.chart.horizontalbar",
    "line": "oracle.bi.tech.chart.line",
    "table": "oracle.bi.tech.table",
    "pivot": "oracle.bi.tech.pivot",
    "scatter": "oracle.bi.tech.chart.scatter",        # X/Y = tag obitech-scatterchart#x/#y on the measures
    # templates/Example 2.dva
    "area": "oracle.bi.tech.chart.nonstackedarea",
    "radar": "oracle.bi.tech.chart.radar",
    "boxplot": "oracle.bi.tech.chart.boxplot",        # role "item" = local column BIN(... BY ROWID ...)
    "map": "oracle.bi.tech.map",                      # roles detail (place) and size
    "narrative": "oracle.bi.tech.narrative",
    "tile": "oracle.bi.tech.ngperformancetile",
}
LOCAL = "vizColumn."                    # columns defined inside a visual: vizColumn.<viewName>.<name>
GRAINS = {"year": "ExtractYear", "quarter": "ExtractQuarter", "month": "ExtractMonth", "day": "ExtractDay"}
HEADER_SEPARATORS = (",", " : ")        # format of the OAC catalog archive headers

# Filters of the visual's filter bar ("With Filters", "More Filters"): list of values for attributes and periods,
# range for measures and days (calendar)
LIST_FILTER = "obitech-listfilter/listfilter.ListFilterModel"
NUMBER_RANGE = "obitech-numberrangefilter/numberrangefilter.NumberRangeFilterModel"
DATE_RANGE = "obitech-daterangefilter/daterangefilter.DateRangeFilterModel"
MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December"]
# period labels as OAC writes them (English instance): "2016", "Q1 2016", "August 2015", "01/20/2013"
PERIOD_CAPTION = {"year": lambda y, m, d: f"{y}", "quarter": lambda y, m, d: f"Q{(m - 1) // 3 + 1} {y}",
                  "month": lambda y, m, d: f"{MONTHS_EN[m - 1]} {y}", "day": lambda y, m, d: f"{m:02d}/{d:02d}/{y}"}
PERIOD_FORMAT = {"year": (r"(\d{4})", "'2016'"), "quarter": (r"(\d{4}) ?Q([1-4])", "'2016 Q1'"),
                 "month": (r"(\d{4})-(\d{2})", "'2016-11'"), "day": (r"(\d{4})-(\d{2})-(\d{2})", "'2016-11-05'")}


def _number_text(v) -> str:
    """Bounds of a filter on a measure as OAC writes them: "126000", "-92680" (no .0 for integers)."""
    v = float(v)
    return str(int(v)) if v.is_integer() else repr(v)


def period_parts(period: str, grain: str) -> tuple[int, int, int]:
    """(year, month, day) of the first day of the period: "2016", "2016 Q1", "2016-11", "2016-11-05"."""
    pattern, example = PERIOD_FORMAT[grain]
    m = re.fullmatch(pattern, str(period).strip())
    if not m:
        raise ValueError(f"period {period!r} not valid for grain {grain} (e.g. {example})")
    g = [int(x) for x in m.groups()]
    if grain == "quarter":
        return g[0], (g[1] - 1) * 3 + 1, 1
    year, month, day = g[0], g[1] if len(g) > 1 else 1, g[2] if len(g) > 2 else 1
    if not (1 <= month <= 12 and 1 <= day <= 31):
        raise ValueError(f"period {period!r} not valid")
    return year, month, day


def period_filter_value(period: str, grain: str) -> tuple[str, str]:
    """("2016-11-01T00:00:00", "November 2016"): the first day of the period, as ExtractYear/Quarter/Month/Day
    return it and as OAC writes it in the filter, with the filter bar label."""
    y, m, d = period_parts(period, grain)
    return f"{y}-{m:02d}-{d:02d}T00:00:00", PERIOD_CAPTION[grain](y, m, d)


def rebind(view, mapping):
    """Replaces the columns of `mapping` at every level of the visual, in one go.

    One visit only, so a chain A->B, B->C is not applied twice.
    """
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("columnID", "valueColumnID") and v in mapping:
                    o[k] = mapping[v]
                elif k == "id" and isinstance(v, str) and "." in v:
                    prefix, col = v.rsplit(".", 1)
                    if col in mapping:
                        o[k] = f"{prefix}.{mapping[col]}"
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(view)


def logical_roles(view):
    """role -> [columnID] from the visual's logical model."""
    roles = {}
    for dm in view.get("dataModels", {}).get("children", []):
        edges = dm["logicalDataModel"]["settings"]["logicalDataModel"]["logicalEdges"]
        for role, edge in edges.items():
            cols = [l["columnID"] for l in edge.get("logicalEdgeLayers", []) if l.get("columnID")]
            if cols:
                roles[role] = cols
    return roles


def template_roles(view):
    """role -> [columnID] of the template, without local columns (e.g. the boxplot item): those stay its own."""
    roles = {r: [c for c in cols if not c.startswith(LOCAL)] for r, cols in logical_roles(view).items()}
    return {r: cols for r, cols in roles.items() if cols}


def _refers(item, col):
    """A list element stands for column `col` (logical level, axis level, measure, …)."""
    return isinstance(item, dict) and (item.get("columnID") == col or item.get("valueColumnID") == col)


def _map_lists(o, fn):
    """Applies fn to every list nested in o (inner lists first), replacing it with the result."""
    if isinstance(o, dict):
        for k, v in o.items():
            o[k] = _map_lists(v, fn)
        return o
    if isinstance(o, list):
        return fn([_map_lists(x, fn) for x in o])
    return o


def resize_roles(view, tpl_roles, roles):
    """Moves the visual from the template's columns to the requested ones, even in a different number.

    Every column of a role is an element of its own in a list, both in the logical model (logicalEdgeLayers) and
    in the physical one (measuresList, edgeLayers): seen by comparing the templates with the "Varianti" workbook
    (FORMATO-DVA.md, §M3c). So for every role the template's first column is the prototype: in ALL the lists that
    name it, it becomes one copy per requested column, and the template's other columns are removed. The two
    levels stay consistent by construction.
    - As OAC does, min./max. (propertyAdditions) stay on the first measure only.
    - "view" entries (a measure pointing to the nested view) are not duplicated: they are rewired.
    - A requested empty role (0 columns) removes the template's columns from that role.
    """
    unknown = set(roles) - set(tpl_roles)
    if unknown:
        raise NotImplementedError(f"roles not in the template: {sorted(unknown)}")
    # unique placeholders: requested columns can have the same id as template columns
    ph = {c: f"__tpl{i}__" for i, c in enumerate(dict.fromkeys(c for cols in tpl_roles.values() for c in cols))}
    rebind(view, ph)
    drop, protos = set(), {}
    for role, tcols in tpl_roles.items():
        want = roles.get(role, [])
        names = [ph[c] for c in tcols]
        drop |= set(names[1:]) | (set() if want else {names[0]})
        if want:
            protos[names[0]] = want
    _map_lists(view, lambda lst: [x for x in lst if not any(_refers(x, d) for d in drop)])

    def proto_of(x):
        return next((p for p in protos if _refers(x, p)), None)

    def clone(lst):
        # several consecutive elements of the same column (e.g. min. and max. of a measure) are copied in groups,
        # one column at a time: [min.A, max.A, min.B, max.B], as OAC saves them
        out, i = [], 0
        while i < len(lst):
            x, proto = lst[i], proto_of(lst[i])
            if proto is None:
                out.append(x)
                i += 1
                continue
            run = [x]
            while i + len(run) < len(lst) and proto_of(lst[i + len(run)]) == proto:
                run.append(lst[i + len(run)])
            i += len(run)
            if x.get("type") == "view":
                for y in run:
                    rebind(y, {proto: protos[proto][0]})
                out += run
                continue
            for n, col in enumerate(protos[proto]):
                for y in run:
                    c = copy.deepcopy(y)
                    if n:
                        c.pop("propertyAdditions", None)
                    rebind(c, {proto: col})
                    out.append(c)
        return out
    _map_lists(view, clone)
    rebind(view, {p: cols[0] for p, cols in protos.items()})       # leftover references outside the lists


def set_sort(view, detail, measure, direction):
    """Sorts the categories by the measure's value (structure read from "V5 hbar ordinata" in Varianti):
    columnSort on the measure's logical level and columnOrder on the row axis of the nested view."""
    direction = {"desc": "descending", "asc": "ascending"}[direction]
    edges = view["dataModels"]["children"][0]["logicalDataModel"]["settings"]["logicalDataModel"]["logicalEdges"]
    layer = next(l for l in edges["measures"]["logicalEdgeLayers"] if l.get("columnID") == measure)
    layer["columnSort"] = {"measureSorts": [{"axis": "row", "direction": direction, "order": 0},
                                            {"axis": "column", "direction": direction, "order": 0}]}
    nested = view["nestedViews"]["children"][0]["view"]["dataModels"]["children"][0]["edges"]["children"]
    row = next(e for e in nested if e["axis"] == "row")
    row["columnOrder"] = {"children": [{"columnID": detail, "direction": direction, "QDR": {"children": [
        {"specialDimension": "grandTotal", "members": {"children": [{"text": "gt_column"}], "type": "saw:stringMembers"}},
        {"specialDimension": "measure", "members": {"children": [{"text": measure}], "type": "saw:specialValueMembers"}}]}}]}


def localize(view, new_name):
    """Moves a cloned visual to its new name.

    Local columns (viz:columns, e.g. the boxplot BINs) have ids containing the visual's name: vizColumn.view!3.X
    must become vizColumn.view!N.X. Local columns no model uses (leftovers of edits in the editor) are removed.
    """
    old = view["viewName"]
    view = json.loads(json.dumps(view).replace(f'"{LOCAL}{old}.', f'"{LOCAL}{new_name}.'))
    view["viewName"] = new_name
    vc = view.get("viewConfig", {}).get("settings", {}).get("viz:columns")
    if vc:
        rest = json.dumps({k: v for k, v in view.items() if k != "viewConfig"})
        vc["columns"] = [c for c in vc["columns"] if f'"{c["columnID"]}' in rest]
    return view


def fold_manifest(lines):
    """Writes a MANIFEST.MF back with lines folded at 72 bytes (continuations with a space)."""
    out = []
    for key, value in lines:
        raw = f"{key}: {value}".encode("utf-8")
        out.append(raw[:72])
        raw = raw[72:]
        while raw:
            out.append(b" " + raw[:71])
            raw = raw[71:]
    return out


def unfold_manifest(data):
    """MANIFEST.MF -> ([(key, value)], line separator, final bytes)."""
    sep = b"\r\n" if b"\r\n" in data else b"\n"
    body = data.rstrip(b"\r\n")
    tail = data[len(body):]
    lines = []
    for line in body.split(sep):
        if line.startswith(b" "):
            lines[-1][1] += line[1:]
        else:
            k, v = line.split(b": ", 1)
            lines.append([k, v])
    return [(k.decode(), v.decode()) for k, v in lines], sep, tail


def write_manifest(lines, sep, tail):
    return sep.join(fold_manifest(lines)) + tail


def table_of(definition) -> str:
    """The table of a workbook's dataset, from its first column ("Retail Data final")."""
    expr = definition["criteria"]["columns"]["children"][0]["columnFormula"]["expr"]["expression"]
    return re.search(r'\)\."([^"]+)"\."', expr).group(1)


def rebound(definition: dict, old: str, new: str) -> dict:
    """A definition with every reference `old` (XSA(...)."Table") replaced by `new`, as set_source does."""
    return json.loads(json.dumps(definition).replace(json.dumps(old)[1:-1], json.dumps(new)[1:-1]))


def read_definition(dva_path):
    """The _projectdefn of a .dva, as a dict."""
    z = zipfile.ZipFile(dva_path)
    items = parse(zlib.decompress(z.read(next(n for n in z.namelist() if n.endswith(".arc")))))["items"]
    return json.loads(next(it for it in items if it["header"]["ItemName"] == "_projectdefn")["body"])


class Workbook:
    def __init__(self, template_path, extra_templates=(), skeleton_visuals=True):
        """`template_path` gives the skeleton and its visuals; `extra_templates` other visual templates.

        With `skeleton_visuals=False` the skeleton gives only the container (e.g. a .dva exported without data) and
        all visual templates come from `extra_templates`.

        Templates can be .dva files or definitions already read (dicts, e.g. from the catalog via MCP). The other
        templates' visuals must be on the skeleton's dataset: local columns (viz:columns) hold expressions with the
        XSA reference to their dataset.
        """
        self.zin = None
        self.manifests = {}
        if isinstance(template_path, dict):
            self.arc, self.name = None, None
            self.wb = copy.deepcopy(template_path)
        else:
            self.zin = zipfile.ZipFile(template_path)
            self.arc_name = next(n for n in self.zin.namelist() if n.startswith("content/") and n.endswith(".arc"))
            self.arc = parse(zlib.decompress(self.zin.read(self.arc_name)))
            self.folder = next(it for it in self.arc["items"] if it["header"]["ItemType"] == 1
                               and it["header"].get("WCProperties", {}).get("compositeSignature") == "projectfolder1")
            self.name = self.folder["header"]["ItemName"]
            self.wb = json.loads(self._defn()["body"])
        # all visual templates per kind: the one with the right roles is chosen (e.g. the line with color)
        self.variants = {}
        if skeleton_visuals:
            for v in self.wb["views"]["children"]:
                if v["type"] == "saw:pluginView":
                    self.variants.setdefault(v["pluginType"], []).append(copy.deepcopy(v))
        self.source = self.wb["datasources"]["children"][0]["subjectArea"]           # XSA('guid'.'Name')
        self.table = table_of(self.wb)                                                 # e.g. "Retail Data final"
        for extra in extra_templates:
            other = extra if isinstance(extra, dict) else read_definition(extra)
            other_source = other["datasources"]["children"][0]["subjectArea"]
            if other_source != self.source:
                # a template made on another dataset: its local columns name that dataset; they follow the skeleton
                # (bundled templates use a placeholder dataset, a user's skeleton .dva names theirs)
                other = rebound(other, f'{other_source}."{table_of(other)}"', f'{self.source}."{self.table}"')
            for v in other["views"]["children"]:
                if v["type"] == "saw:pluginView":
                    self.variants.setdefault(v["pluginType"], []).append(copy.deepcopy(v))

    @property
    def templates(self):
        """The first template of every kind (compatibility with the scripts of tests 6-7)."""
        return {pt: views[0] for pt, views in self.variants.items()}

    def definition(self):
        """The workbook definition (_projectdefn), e.g. for save_catalog_content."""
        return self.wb

    # -- references -------------------------------------------------------
    def set_source(self, xsa, table):
        """Points the workbook to a catalog dataset: `xsa` and `table` come from the MCP metadata
        (search_catalog.xsaExpr and describe_data). The visual templates' local columns (e.g. the boxplot BINs)
        hold the reference too: they are updated together."""
        old = f'{self.source}."{self.table}"'
        new = f'{xsa}."{table}"'
        for pt, views in self.variants.items():
            self.variants[pt] = [json.loads(json.dumps(v).replace(json.dumps(old)[1:-1], json.dumps(new)[1:-1]))
                                 for v in views]
        self.wb["datasources"]["children"] = [{"subjectArea": xsa}]
        if "subjectArea" in self.wb.get("criteria", {}):     # the workbook's criteria name the dataset too (28/9)
            self.wb["criteria"]["subjectArea"] = xsa
        self.source, self.table = xsa, table

    def ref(self, source_column):
        return f'{self.source}."{self.table}"."{source_column}"'

    def _defn(self):
        return next(it for it in self.arc["items"] if it["header"]["ItemName"] == "_projectdefn")

    def _free_id(self, prefix):
        used = [int(n) for n in re.findall(re.escape(prefix) + r"(\d+)", json.dumps(self.wb))]
        return f"{prefix}{max(used, default=0) + 1}"

    # -- building -------------------------------------------------------
    def reset(self):
        """Empties the template's columns, canvases and visuals; resets the colours assigned to its columns."""
        self.wb["criteria"]["columns"]["children"] = []
        self.wb["criteria"].setdefault("criteriaConfig", {"_version": "1.0.2", "settings": {}})
        self.wb["criteria"]["criteriaConfig"]["settings"]["columnPropertyMap"] = {}
        self.wb["views"]["children"] = []
        self.wb["layouts"]["children"] = []
        self.wb.pop("filterControlCollections", None)
        colors = self.wb["reportConfig"]["settings"].get("oracle.bi.tech.colorSchemeService", {}).get("settings", {})
        if "colorDomains" in colors:
            colors["colorDomains"] = {
                '["obitech.colorcategory.value","categoricalSchemes","[]"]': {
                    "generation": 0, "colorMap": {}, "coloringType": "categoricalSchemes", "colorScheme": None,
                    "noRepeat": True, "hierarchical": False, "nextIndex": 0}}

    def _add_column(self, col):
        ids = {c["columnID"] for c in self.wb["criteria"]["columns"]["children"]}
        if col["columnID"] in ids:
            raise ValueError(f"column already present: {col['columnID']}")
        self.wb["criteria"]["columns"]["children"].append(col)

    def column(self, cid, source_column):
        self._add_column({"columnID": cid, "type": "saw:regularColumn",
                          "columnFormula": {"expr": {"children": [], "expression": self.ref(source_column),
                                                     "type": "sawx:sqlExpression"}}})

    def date_column(self, cid, source_column, grain):
        base = self.ref(source_column)
        self._add_column({"columnID": cid, "type": "saw:regularColumn",
                          "columnFormula": {"expr": {"children": [], "expression": f"{GRAINS[grain]}({base})",
                                                     "type": "sawx:sqlExpression"}}})
        self.wb["criteria"]["criteriaConfig"]["settings"]["columnPropertyMap"][cid] = {
            "parentExpression": base, "dateTimePreferences": {"timeLevel": grain}}

    def calculation(self, cid, caption, expression, description=None):
        """A workbook calculation (My Calculations). In the expression, {Column name} -> full reference."""
        expr = re.sub(r"\{([^}]+)\}", lambda m: self.ref(m.group(1)), expression)
        col = {"columnID": cid, "userExpression": True, "type": "saw:regularColumn",
               "columnFormula": {"expr": {"children": [], "expression": expr, "type": "sawx:sqlExpression"}},
               "columnHeading": {"caption": {"text": caption}}}
        if description:
            col["columnDescription"] = {"caption": {"text": description}}
        self._add_column(col)

    def canvas(self, title):
        cid, lid = self._free_id("canvas!"), self._free_id("layout")
        self.wb["views"]["children"].append({
            "type": "saw:canvas", "viewName": cid, "rootLayoutName": lid,
            "viewCaption": {"caption": {"text": title}}, "canvasConfig": {"_version": "1.0.6", "settings": {}}})
        self.wb["layouts"]["children"].append({
            "layoutProps": {"customProps": {"text": json.dumps(
                {"_version": "1.0.1", "oracle.bi.tech.layout.split": {
                    "layoutMinSize": {"width": "100%", "height": 700}, "_version": "1.0.0"}}, separators=(",", ":"))}},
            "name": lid, "type": "oracle.bi.tech.layout.split", "children": []})
        return cid

    def pick_template(self, kind, roles):
        """The template with exactly the requested roles; if missing, one that holds them all (the others are emptied)."""
        wanted = {r for r, cols in roles.items() if cols}
        views = self.variants.get(PLUGINS[kind], [])
        exact = [v for v in views if set(template_roles(v)) == wanted]
        wider = [v for v in views if set(template_roles(v)) >= wanted]
        if not (exact or wider):
            raise NotImplementedError(f"{kind}: no template with roles {sorted(wanted)}")
        return copy.deepcopy((exact or wider)[0])

    def filter_control(self, column, op, values, by=()):
        """A filter of the visual's filter bar, as OAC saves it ("With Filters", "More Filters").

        op "in": values of an attribute or periods of a date column ("2016", "2016 Q1", "2016-11", "2016-11-05");
        op "between" on a measure: [minimum, maximum], evaluated at the level of the visual's `by` attributes;
        op "between" on a date column with grain day: [from, to] ("2015-09-01", "2015-11-30"), calendar.
        """
        col = next((c for c in self.wb["criteria"]["columns"]["children"] if c["columnID"] == column), None)
        if col is None:
            raise ValueError(f"filter on a column not defined: {column}")
        if op not in ("in", "between"):
            raise ValueError(f"unknown filter operator: {op!r} (in, between)")
        expression = col["columnFormula"]["expr"]["expression"]
        grain = self.wb["criteria"]["criteriaConfig"]["settings"]["columnPropertyMap"].get(column, {}) \
            .get("dateTimePreferences", {}).get("timeLevel")
        model = LIST_FILTER if op == "in" else DATE_RANGE if grain else NUMBER_RANGE
        f = {"filterID": str(uuid.uuid4()), "columnID": column, "type": "saw:columnFilterControl",
             "filterControlConfig": {"_version": "1.0.11", "settings": {"filterModelClassName": model,
                                                                        "location": "filter_bar"}},
             "formula": {"expr": {"children": [], "type": "sawx:sqlExpression", "expression": expression}},
             "filterOperator": {"op": op}}
        if op == "between":
            if len(values) != 2:
                raise ValueError("between vuole due valori: [minimo, massimo]")
            if grain:
                if grain != "day":
                    raise NotImplementedError(f"date range only on grain day (not {grain}): use in with the periods")
                days = [period_parts(v, "day") for v in values]
                f["filterUIControl"] = {"displayTimeZone": "displayTimeZone", "type": "saw:fcCalendar"}
                f["filterControlDefaultValues"] = {"children": [{"text": f"{y}-{m:02d}-{d:02d}"} for y, m, d in days],
                                                   "type": "specificValue"}
                return f
            f["filterControlDefaultValues"] = {"children": [{"text": _number_text(v)} for v in values],
                                               "type": "specificValue"}
            f["filterByColumns"] = {"children": [
                {"expression": self._expression(b), "type": "sawx:sqlExpression"} for b in by]}
            return f
        if grain:
            pairs = [period_filter_value(str(v), grain) for v in values]
            choices = [{"value": {"text": t}, "caption": {"text": c}} for t, c in pairs]
            texts = [t for t, _ in pairs]
        else:
            texts = [str(v) for v in values]
            choices = [{"value": {"text": t}} for t in texts]
        f["filterControlDefaultValues"] = {"children": [{"text": t} for t in texts], "type": "specificValue"}
        f["filterControlSource"] = {"type": "saw:fcSpecificChoices", "filterControlChoices": {"children": choices}}
        return f

    def _expression(self, column):
        return next(c["columnFormula"]["expr"]["expression"] for c in self.wb["criteria"]["columns"]["children"]
                    if c["columnID"] == column)

    def visual(self, canvas_id, kind, title, roles, box, sort=None, filters=()):
        """Adds a visual: chooses the template with the right roles and moves it to the requested columns, even in
        a different number than the template (resize_roles). `sort`: "desc" | "asc" for bar and hbar.
        `filters`: dict(column, op, values[, by]) for the visual's filter bar (filter_control)."""
        known = {c["columnID"] for c in self.wb["criteria"]["columns"]["children"]}
        missing = [c for cols in roles.values() for c in cols if c not in known]
        if missing:
            raise ValueError(f"undefined columns: {missing}")
        controls = [self.filter_control(f["column"], f["op"], f["values"], f.get("by", ())) for f in filters]
        tpl = self.pick_template(kind, roles)
        resize_roles(tpl, template_roles(tpl), roles)
        if sort:
            if kind not in ("bar", "hbar") or len(roles.get("measures", [])) != 1 or roles.get("color"):
                raise NotImplementedError("sorting only for bar/hbar with one measure and no color")
            set_sort(tpl, roles["detail"][0], roles["measures"][0], sort)
        vid = self._free_id("view!")
        tpl = localize(tpl, vid)
        tpl["viewCaption"] = {"caption": {"text": title}}
        self.wb["views"]["children"].append(tpl)
        canvas = next(v for v in self.wb["views"]["children"] if v["viewName"] == canvas_id)
        layout = next(l for l in self.wb["layouts"]["children"] if l["name"] == canvas["rootLayoutName"])
        left, top, width, height = box
        item = {"content": {"viewName": vid, "type": "view"}, "left": str(left), "top": str(top),
                "displayFormat": {"formatSpec": {"width": f"{width}px", "height": f"{height}px"}},
                "zIndex": len(layout["children"]) + 1}
        if controls:                       # without filters OAC writes neither the bar nor the link
            item["filterControlCollectionName"] = vid
            self.wb.setdefault("filterControlCollections", {"children": []})["children"].append(
                {"name": vid, "filterControls": {"children": controls}})
        layout["children"].append(item)
        return vid

    def rename(self, new_name):
        """Renames the workbook: archive headers (ItemName, OriginalPath) and the two MANIFEST.MF."""
        if self.zin is None:
            raise ValueError("rename/save need a .dva template: a definition is saved with save_catalog_content")
        old_base = self.folder["header"]["OriginalPath"]
        new_base = old_base.rsplit("/", 1)[0] + "/" + new_name
        for it in self.arc["items"]:
            h = it["header"]
            p = h.get("OriginalPath", "")
            if p == old_base or p.startswith(old_base + "/"):
                h["OriginalPath"] = new_base + p[len(old_base):]
                it["header_raw"] = json.dumps(h, separators=HEADER_SEPARATORS, ensure_ascii=False).encode("utf-8")
        self.folder["header"]["ItemName"] = new_name
        self.folder["header_raw"] = json.dumps(self.folder["header"], separators=HEADER_SEPARATORS,
                                               ensure_ascii=False).encode("utf-8")
        for entry, updates in (("content/MANIFEST.MF", {"Object1": new_name, "Path1": new_base}),
                               ("META-INF/MANIFEST.MF", {"Bundle-Application-Name": new_name})):
            data = self.zin.read(entry)
            lines, sep, tail = unfold_manifest(data)
            assert write_manifest(lines, sep, tail) == data, f"{entry}: folding at 72 bytes does not reproduce the original"
            self.manifests[entry] = write_manifest([(k, updates.get(k, v)) for k, v in lines], sep, tail)
        self.name = new_name

    def save(self, path, include_datasets=False):
        """Writes the .dva with the workbook only, pointing to the catalog dataset (default).

        The skeleton's dataset definitions (datasets/embedded*, datasets/datamodel*) are removed: TEST8A showed
        that, once imported, OAC silently creates a COPY of the dataset in the import folder (empty, if the export
        had no data) and rewires the workbook to the copy. `include_datasets=True` reproduces the behaviour of
        tests 1-7, with embedded data."""
        if self.zin is None:
            raise ValueError("rename/save need a .dva template: a definition is saved with save_catalog_content")
        self._defn()["body"] = json.dumps(self.wb, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
            for src in self.zin.infolist():
                if not include_datasets and src.filename.startswith(("datasets/embedded", "datasets/datamodel")):
                    continue
                if src.filename == self.arc_name:
                    data = zlib.compress(serialize(self.arc))
                else:
                    data = self.manifests.get(src.filename) or self.zin.read(src.filename)
                info = zipfile.ZipInfo(src.filename, date_time=src.date_time)
                info.external_attr = src.external_attr
                info.compress_type = zipfile.ZIP_STORED if src.is_dir() else zipfile.ZIP_DEFLATED
                zout.writestr(info, data)
