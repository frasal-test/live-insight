"""Builds the test .dva files (round trip in stages) from a workbook exported from OAC."""
import sys, json, zlib, zipfile
import sys; from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # package liveinsight
from liveinsight.platforms.oac.arc_codec import parse, serialize

SRC = sys.argv[1]

def load(src):
    zin = zipfile.ZipFile(src)
    arc_name = next(n for n in zin.namelist() if n.startswith("content/") and n.endswith(".arc"))
    arc = parse(zlib.decompress(zin.read(arc_name)))
    return zin, arc_name, arc

def projectdefn(arc):
    return next(it for it in arc["items"] if it["header"]["ItemName"] == "_projectdefn")

def write(zin, arc_name, arc, dst):
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for src in zin.infolist():                       # same order and same entries as the original
            data = zlib.compress(serialize(arc)) if src.filename == arc_name else zin.read(src.filename)
            info = zipfile.ZipInfo(src.filename, date_time=src.date_time)
            info.external_attr = src.external_attr
            info.compress_type = zipfile.ZIP_STORED if src.is_dir() else zipfile.ZIP_DEFLATED
            zout.writestr(info, data)
    print("written", dst)

def dumps(wb):
    return json.dumps(wb, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

# Stage 1: identical content, only recompression and repackaging
zin, arc_name, arc = load(SRC)
body = projectdefn(arc)["body"]
wb = json.loads(body)
assert dumps(wb) == body, "compact JSON serialization does not reproduce the original"
write(zin, arc_name, arc, "TEST1_identico.dva")

# Stage 2: visible texts only
views = {v["viewName"]: v for v in wb["views"]["children"]}
cap = views["view!28"]["viewConfig"]["settings"]["viz:chart"]["textContents"]["caption"]
cap["text"] = cap["text"].replace("Business Overview", "Business Overview (TEST Live Insight)")
views["view!1"]["viewCaption"]["caption"]["text"] = "Sales Trend (TEST)"
projectdefn(arc)["body"] = dumps(wb)
write(zin, arc_name, arc, "TEST2_testi.dva")

def rebind(view, old, new):
    """Moves a column onto another at EVERY level of the visual.

    A visual has its binding twice: the logical model (logicalEdges, the one shown in the grammar panel) and the
    physical model (measuresList, nestedViews, propertyAdditions such as min./max./grandTotal.) that is actually
    queried and drawn. Changing only the logical one gives a correct panel and a chart with the old data.
    """
    n = 0
    def walk(o):
        nonlocal n
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("columnID", "valueColumnID") and v == old:
                    o[k] = new; n += 1
                elif k == "id" and isinstance(v, str) and v.endswith("." + old):
                    o[k] = v[: -len(old)] + new; n += 1
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(view)
    return n

# Stage 3: binding change — the donut goes from Sales to Profit (logical model ONLY: incomplete)
dm = views["view!3"]["dataModels"]["children"][0]["logicalDataModel"]["settings"]["logicalDataModel"]["logicalEdges"]
for layer in dm["measures"]["logicalEdgeLayers"]:
    if layer.get("columnID") == "SALES":
        layer["columnID"] = "PROFIT"
views["view!3"]["viewCaption"]["caption"]["text"] = "Profit by Customer Segment (TEST)"
projectdefn(arc)["body"] = dumps(wb)
write(zin, arc_name, arc, "TEST3_binding.dva")

# Stage 4: COMPLETE binding change — the donut goes from Sales to Quantity Ordered at every level
zin, arc_name, arc = load(SRC)
wb = json.loads(projectdefn(arc)["body"])
views = {v["viewName"]: v for v in wb["views"]["children"]}
cap = views["view!28"]["viewConfig"]["settings"]["viz:chart"]["textContents"]["caption"]
cap["text"] = cap["text"].replace("Business Overview", "Business Overview (TEST4 Live Insight)")
print("references moved:", rebind(views["view!3"], "SALES", "QUANTITY_ORDERED"))
views["view!3"]["viewCaption"]["caption"]["text"] = "Quantity by Customer Segment (TEST4)"
projectdefn(arc)["body"] = dumps(wb)
write(zin, arc_name, arc, "TEST4_binding_completo.dva")

# ---------------------------------------------------------------------------
# Stage 5: CREATE, not modify — new column + new calculation (My Calculations)
#          + new bar chart cloned from a template + new canvas with a story page
# ---------------------------------------------------------------------------
import copy, re, uuid

def free_id(wb, prefix):
    """First free id, searching the WHOLE JSON: deleted visuals leave references too (e.g. colours)."""
    used = {int(n) for n in re.findall(re.escape(prefix) + r"(\d+)", json.dumps(wb))}
    return f"{prefix}{max(used) + 1}"

def drop(obj, col):
    """Removes from the lists every element that references the column (an extra measure of the template)."""
    if isinstance(obj, dict):
        for v in obj.values():
            drop(v, col)
    elif isinstance(obj, list):
        obj[:] = [x for x in obj if not (isinstance(x, dict) and col in (x.get("columnID"), x.get("valueColumnID")))]
        for x in obj:
            drop(x, col)

def column(cid, expr, caption=None, description=None):
    c = {"columnID": cid, "type": "saw:regularColumn",
         "columnFormula": {"expr": {"children": [], "expression": expr, "type": "sawx:sqlExpression"}}}
    if caption:                                   # workbook calculation -> shows in "My Calculations"
        c["userExpression"] = True
        c["columnHeading"] = {"caption": {"text": caption}}
        if description:
            c["columnDescription"] = {"caption": {"text": description}}
    return c

zin, arc_name, arc = load(SRC)
wb = json.loads(projectdefn(arc)["body"])
RO = wb["datasources"]["children"][0]["subjectArea"] + '."Retail Data final"'     # the sample's dataset

# 1. criteria: a dataset column not used yet and a new calculation
wb["criteria"]["columns"]["children"] += [
    column("ORDER_PRIORITY", f'{RO}."Order Priority"'),
    column("LI_AvgOrderValue", f'{RO}."Sales" / (COUNT(DISTINCT {RO}."Order ID"))',
           caption="Avg Order Value (TEST5)",
           description="Created by Live Insight: sales divided by the number of distinct orders."),
]

# 2. visual: clone of bar chart view!43, cleaned and rebound
view_id, canvas_id, layout_id = free_id(wb, "view!"), free_id(wb, "canvas!"), free_id(wb, "layout")
bar = copy.deepcopy(next(v for v in wb["views"]["children"] if v["viewName"] == "view!43"))
bar["viewName"] = view_id
bar["viewCaption"] = {"caption": {"text": "Avg Order Value by Order Priority (TEST5)"}}
settings = bar["viewConfig"]["settings"]
for k in [k for k in settings["viz:chart"] if k.startswith("bidvtchart_number_format_")]:
    del settings["viz:chart"][k]                   # formats tied to the template's columns
settings.pop("viz:barlineareachart", None)         # second measure on the Y2 axis
settings.pop("containerProperties", None)          # filter exclusion of the Inventory canvas
drop(bar, "InventoryAvailableQty")
rebind(bar, "InventoryValue", "LI_AvgOrderValue")
rebind(bar, "PRODUCT_CATEGORY", "ORDER_PRIORITY")

# 3. canvas + layout + story page
wb["views"]["children"] += [
    {"type": "saw:canvas", "viewName": canvas_id, "viewCaption": {"caption": {"text": "Live Insight (TEST5)"}},
     "rootLayoutName": layout_id, "filterControlCollectionRef": {"name": canvas_id},
     "masterViewName": view_id, "canvasConfig": {"_version": "1.0.6", "settings": {}}},
    bar,
]
wb["layouts"]["children"].append({
    "layoutProps": copy.deepcopy(next(l for l in wb["layouts"]["children"] if l["name"] == "layout8")["layoutProps"]),
    "name": layout_id, "type": "oracle.bi.tech.layout.split",
    "children": [{"content": {"viewName": view_id, "type": "view"}, "left": "0", "top": "0",
                  "displayFormat": {"formatSpec": {"width": "900px", "height": "450px"}},
                  "zIndex": 1, "filterControlCollectionName": view_id}],
})
wb["snapshots"]["children"].append({
    "id": f"snapshot!{canvas_id}", "hash": str(uuid.uuid4()), "modifiedDate": "2026-09-23T12:00:00",
    "description": "", "name": "Live Insight (TEST5)",
    "formattedName": '<p><span style="font-size: 22px">Live Insight (TEST5)</span></p>',
    "canvasRef": canvas_id, "isDuplicateStoryPage": False, "storyPageConfig": {"_version": "1.0.5", "settings": {}},
})
wb["stories"]["children"][0]["children"].append({"storyPageID": f"snapshot!{canvas_id}", "isEnabled": True})

projectdefn(arc)["body"] = dumps(wb)
print(f"TEST5: {view_id} on {canvas_id} / {layout_id}")
write(zin, arc_name, arc, "TEST5_nuovo_canvas.dva")
