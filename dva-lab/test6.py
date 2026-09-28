"""TEST6: a NEW workbook, with its own name, generated from a specification from the Examples template."""
import io
import json
import re
import zipfile
import zlib
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta

import sys; from pathlib import Path; sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # package liveinsight
from liveinsight.platforms.oac.arc_codec import parse
from liveinsight.platforms.oac.li_dva import Workbook, logical_roles

TEMPLATE = "templates/Examples.dva"
OUT = "TEST6_nuovo_workbook.dva"
NAME = "Live Insight - Sales analysis"

# --- the specification: what the chat would produce one day ------------------------
wb = Workbook(TEMPLATE)
wb.reset()
wb.column("CustomerSegment", "Customer Segment")
wb.column("OrderPriority", "Order Priority")
wb.column("Sales", "Sales")
wb.date_column("OrderQuarter", "Order Date", "quarter")
wb.date_column("OrderYear", "Order Date", "year")
wb.calculation("AvgOrderValue", "Average order value",
               "{Sales} / (COUNT(DISTINCT {Order ID}))",
               "Created by Live Insight: sales divided by the number of distinct orders.")
c = wb.canvas("Sales")
wb.visual(c, "bar", "Average order value by segment",
          {"measures": ["AvgOrderValue"], "detail": ["CustomerSegment"]}, (0, 0, 560, 380))
wb.visual(c, "line", "Sales by quarter",
          {"measures": ["Sales"], "detail": ["OrderQuarter"]}, (570, 0, 810, 380))
wb.visual(c, "pivot", "Sales by priority and year",
          {"row": ["OrderPriority"], "col": ["OrderYear"], "measures": ["Sales"]}, (0, 390, 1380, 300))
wb.rename(NAME)
wb.save(OUT)
print("written", OUT)

# --- structural check ------------------------------------------------------
z = zipfile.ZipFile(OUT)
assert z.testzip() is None
arc = parse(zlib.decompress(z.read(next(n for n in z.namelist() if n.endswith(".arc")))))
blob = b"".join(z.read(n) for n in z.namelist() if not n.endswith("/") and not n.endswith(".arc"))
blob += zlib.decompress(z.read(next(n for n in z.namelist() if n.endswith(".arc"))))
defn = json.loads(next(i for i in arc["items"] if i["header"]["ItemName"] == "_projectdefn")["body"])
cols = {c["columnID"] for c in defn["criteria"]["columns"]["children"]}
used = set(re.findall(r'"(?:columnID|valueColumnID)":\s*"([^"]+)"', json.dumps(defn["views"])))
print("leftovers of the name 'Examples':", blob.count(b"Examples"))
print("columns used by visuals but not defined:", used - cols or "none")
print("template columns left:", used & {"ProductCategory", "ProductSubCategory", "OrderDate", "OrderDate_1", "Profit"} or "none")
for v in defn["views"]["children"]:
    if v["type"] == "saw:pluginView":
        print(f'  {v["viewName"]} {v["pluginType"].split(".")[-1]:6s} "{v["viewCaption"]["caption"]["text"]}" {logical_roles(v)}')
print("name in the manifests:", [l for n in ("content/MANIFEST.MF", "META-INF/MANIFEST.MF")
                              for l in z.read(n).decode().splitlines() if NAME[:20] in l])

# --- expected values from the embedded dataset (spaces trimmed, as OAC does) ----
x = zipfile.ZipFile(io.BytesIO(zipfile.ZipFile(TEMPLATE).read("datasets/embedded2/data.xlsx")))  # data from the template
ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
ss = ["".join(t.text or "" for t in si.iter("{%s}t" % ns["m"]))
      for si in ET.fromstring(x.read("xl/sharedStrings.xml")).findall("m:si", ns)]
rows = []
for _, el in ET.iterparse(x.open(next(n for n in x.namelist() if n.startswith("xl/worksheets/sheet")))):
    if el.tag.endswith("}row"):
        r = {}
        for cell in el.findall("m:c", ns):
            v = cell.find("m:v", ns)
            if v is not None:
                r[re.match(r"[A-Z]+", cell.get("r")).group()] = ss[int(v.text)] if cell.get("t") == "s" else v.text
        rows.append(r)
        el.clear()
idx = {v: k for k, v in rows[0].items()}
get = lambda r, c: r.get(idx[c])
seg_s, seg_o = defaultdict(float), defaultdict(set)
quarter = defaultdict(float)
pivot = defaultdict(float)
for r in rows[1:]:
    sales = float(get(r, "Sales") or 0)
    d = datetime(1899, 12, 30) + timedelta(days=float(get(r, "Order Date")))
    seg = get(r, "Customer Segment").strip()
    seg_s[seg] += sales
    seg_o[seg].add(get(r, "Order ID"))
    quarter[(d.year, (d.month - 1) // 3 + 1)] += sales
    pivot[(get(r, "Order Priority").strip(), d.year)] += sales

print("\nEXPECTED — Average order value by segment")
for s in sorted(seg_s):
    print(f"  {s:16s} {seg_s[s] / len(seg_o[s]):10,.2f}")
print("\nEXPECTED — Sales by quarter")
for (y, q) in sorted(quarter):
    print(f"  {y} Q{q}  {quarter[(y, q)]:14,.2f}")
years = sorted({y for _, y in pivot})
print("\nEXPECTED — Sales by priority and year")
print(f"  {'':14s}" + "".join(f"{y:>14d}" for y in years))
for p in sorted({p for p, _ in pivot}):
    print(f"  {p:14s}" + "".join(f"{pivot[(p, y)]:14,.2f}" for y in years))
