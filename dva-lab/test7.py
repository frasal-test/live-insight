"""TEST7: the catalog of the 12 visual kinds of the "basic" library, generated from a specification.

Skeleton: templates/Examples.dva. Visual templates also from templates/Example 2.dva (local files, not in git).
Every visual uses columns different from its template's, to test the rebinding.
"""
import json
import re
import zipfile
import zlib
from collections import defaultdict

import sys; from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # package liveinsight
from liveinsight.platforms.oac.arc_codec import parse
from liveinsight.platforms.oac.dva_data import load_rows
from liveinsight.platforms.oac.li_dva import Workbook, logical_roles, LOCAL

OUT = "TEST7_catalogo.dva"
NAME = "Live Insight - Visual catalog"

wb = Workbook("templates/Examples.dva", extra_templates=["templates/Example 2.dva"])
wb.reset()
for cid, src in [("CustomerSegment", "Customer Segment"), ("ProductCategory", "Product Category"),
                 ("ProductSubCategory", "Product Sub Category"), ("OrderPriority", "Order Priority"),
                 ("City", "City"), ("Sales", "Sales"), ("Profit", "Profit"), ("Quantity", "Quantity Ordered")]:
    wb.column(cid, src)
wb.date_column("OrderMonth", "Order Date", "month")
wb.date_column("OrderYear", "Order Date", "year")

W, H = 460, 380
grid = [(0, 0), (470, 0), (940, 0), (0, 390), (470, 390), (940, 390)]

c1 = wb.canvas("Base")
spec1 = [
    ("bar", "Bar: Profit by segment", {"measures": ["Profit"], "detail": ["CustomerSegment"]}),
    ("hbar", "HBar: Quantity by sub-category", {"measures": ["Quantity"], "detail": ["ProductSubCategory"]}),
    ("line", "Line: Sales by month", {"measures": ["Sales"], "detail": ["OrderMonth"]}),
    ("table", "Table: segment, sales, quantity", {"row": ["CustomerSegment", "Sales", "Quantity"]}),
    ("pivot", "Pivot: Profit by segment and year", {"row": ["CustomerSegment"], "col": ["OrderYear"], "measures": ["Profit"]}),
    ("scatter", "Scatter: Sales (X) vs Quantity (Y)", {"measures": ["Sales", "Quantity"], "detail": ["ProductSubCategory"]}),
]
c2 = wb.canvas("Base 2")
spec2 = [
    ("area", "Area: Profit by month", {"measures": ["Profit"], "detail": ["OrderMonth"]}),
    ("radar", "Radar: Sales by priority", {"measures": ["Sales"], "detail": ["OrderPriority"]}),
    ("boxplot", "Boxplot: Sales by category", {"measures": ["Sales"], "detail": ["ProductCategory"]}),
    ("map", "Map: Quantity by city", {"detail": ["City"], "size": ["Quantity"]}),
    ("narrative", "Narrative: Sales by segment", {"measures": ["Sales"], "row": ["CustomerSegment"]}),
    ("tile", "Tile: Quantity by segment", {"measures": ["Quantity"], "detail": ["CustomerSegment"]}),
]
for canvas, spec in ((c1, spec1), (c2, spec2)):
    for (kind, title, roles), (x, y) in zip(spec, grid):
        wb.visual(canvas, kind, title, roles, (x, y, W, H))
wb.rename(NAME)
wb.save(OUT)
print("written", OUT)

# --- structural check ------------------------------------------------------
z = zipfile.ZipFile(OUT)
assert z.testzip() is None
raw = zlib.decompress(z.read(next(n for n in z.namelist() if n.endswith(".arc"))))
defn = json.loads(next(i for i in parse(raw)["items"] if i["header"]["ItemName"] == "_projectdefn")["body"])
cols = {c["columnID"] for c in defn["criteria"]["columns"]["children"]}
views = [v for v in defn["views"]["children"] if v["type"] == "saw:pluginView"]
problems = []
for v in views:
    local = {c["columnID"] for c in v.get("viewConfig", {}).get("settings", {}).get("viz:columns", {}).get("columns", [])}
    used = set(re.findall(r'"(?:columnID|valueColumnID)":\s*"([^"]+)"', json.dumps(v)))
    orphan = {u for u in used - cols - local if not u.startswith(LOCAL) and not u.startswith("__")}  # __X__ = OAC internal placeholders
    foreign = {u for u in used if u.startswith(LOCAL) and not u.startswith(f'{LOCAL}{v["viewName"]}.')}
    if orphan or foreign:
        problems.append((v["viewName"], orphan, foreign))
    print(f'  {v["viewName"]:8s} {v["pluginType"].split(".")[-1]:18s} {logical_roles(v)}' + (f"  local: {len(local)}" if local else ""))
print("leftovers of 'Examples'/'Example 2':", raw.count(b"Example"), "| column problems:", problems or "none")

# --- expected values --------------------------------------------------------------
rows = load_rows("templates/Examples.dva")      # the generated .dva no longer embeds the data
def total(key, measure):
    acc = defaultdict(float)
    for r in rows:
        acc[key(r)] += r[measure] or 0
    return acc

print("\nEXPECTED")
seg_profit = total(lambda r: r["Customer Segment"], "Profit")
print("  Bar Profit by segment:", {k: round(v) for k, v in sorted(seg_profit.items())})
sub_q = total(lambda r: r["Product Sub Category"], "Quantity Ordered")
top = sorted(sub_q.items(), key=lambda kv: -kv[1])
print(f"  HBar first bar: {top[0][0]} {top[0][1]:,.0f} | last: {top[-1][0]} {top[-1][1]:,.0f}")
seg_sales, seg_q = total(lambda r: r["Customer Segment"], "Sales"), total(lambda r: r["Customer Segment"], "Quantity Ordered")
print("  Table:", {k: (round(seg_sales[k]), round(seg_q[k])) for k in sorted(seg_sales)})
pv = total(lambda r: (r["Customer Segment"], r["Order Date"].year), "Profit")
print("  Pivot Corporate:", {y: round(pv[("Corporate", y)]) for y in sorted({y for _, y in pv})})
print("  Radar:", {k: round(v) for k, v in sorted(total(lambda r: r["Order Priority"], "Sales").items())})
city_q = sorted(total(lambda r: r["City"], "Quantity Ordered").items(), key=lambda kv: -kv[1])
print(f"  Map: {len(city_q)} cities; the largest: {city_q[0][0]} {city_q[0][1]:,.0f}")
print("  Tile: total quantity", f"{sum(seg_q.values()):,.0f}")
