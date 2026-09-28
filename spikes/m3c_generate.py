"""M3c — The new shapes generated from the spec, saved in OAC and exported as PNG, with the expected values.

Columns different from those of "Varianti", to test the rebinding too. Templates from the catalog: Examples,
Example 2, Varianti.

Usage:  uv run python spikes/m3c_generate.py [folder-for-the-pngs]
"""
import os
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "spikes")]
from liveinsight.platforms.oac.catalog import describe, workbook_json                         # noqa: E402
from liveinsight.platforms.oac.checks import definition_problems                              # noqa: E402
from liveinsight.platforms.oac.target import compile_workbook                                # noqa: E402
from liveinsight.platforms.oac.dva_data import load_rows                                       # noqa: E402
from liveinsight.platforms.oac.li_dva import Workbook                                         # noqa: E402
from liveinsight.platforms.oac.auth import OacToken                                       # noqa: E402
from liveinsight.platforms.oac.mcp import OacMcp                                          # noqa: E402
from liveinsight.engine.spec import WorkbookSpec                                       # noqa: E402
from testfolder import export_png, save_in_test_folder                          # noqa: E402

NAME = "M3c - Varianti generate"
COLUMNS = [{"kind": "column", "id": c.replace(" ", ""), "source": c} for c in
           ["Customer Segment", "Product Category", "Product Sub Category", "Order Priority",
            "Sales", "Profit", "Shipping Cost", "Quantity Ordered"]] + \
          [{"kind": "date", "id": "OrderYear", "source": "Order Date", "grain": "year"}]
V = [
    ("line", "Line color: sales by year and segment", {"measures": ["Sales"], "detail": ["OrderYear"], "color": ["CustomerSegment"]}, None),
    ("bar", "Bar with three measures by category", {"measures": ["Sales", "Profit", "ShippingCost"], "detail": ["ProductCategory"]}, None),
    ("bar", "Bar color: quantity by priority and category", {"measures": ["QuantityOrdered"], "detail": ["OrderPriority"], "color": ["ProductCategory"]}, None),
    ("hbar", "Sorted hbar: sales by sub-category", {"measures": ["Sales"], "detail": ["ProductSubCategory"]}, "desc"),
    ("bar", "Bar sorted ascending: profit by segment", {"measures": ["Profit"], "detail": ["CustomerSegment"]}, "asc"),
    ("line", "Line trellis: profit by year, one panel per category", {"measures": ["Profit"], "detail": ["OrderYear"], "col": ["ProductCategory"]}, None),
    ("table", "Six-column table", {"row": ["ProductCategory", "CustomerSegment", "OrderYear", "Sales", "Profit", "QuantityOrdered"]}, None),
    ("table", "Two-column table", {"row": ["CustomerSegment", "Sales"]}, None),
]


def expected():
    rows = load_rows(str(ROOT / "dva-lab/templates/Examples.dva"))
    def tot(key, m):
        acc = defaultdict(float)
        for r in rows:
            acc[key(r)] += r[m] or 0
        return acc
    y = lambda r: r["Order Date"].year
    ly = tot(lambda r: (y(r), r["Customer Segment"]), "Sales")
    print("EXPECTED")
    print("  line color 2016:", {s: round(ly[(2016, s)]) for s in sorted({k[1] for k in ly})})
    for m in ("Sales", "Profit", "Shipping Cost"):
        print(f"  bar three measures, {m}:", {k: round(v) for k, v in sorted(tot(lambda r: r["Product Category"], m).items())})
    q = tot(lambda r: (r["Order Priority"], r["Product Category"]), "Quantity Ordered")
    print("  bar color High:", {c: round(q[("High", c)]) for c in ("Furniture", "Office Supplies", "Technology")})
    sub = sorted(tot(lambda r: r["Product Sub Category"], "Sales").items(), key=lambda kv: -kv[1])
    print(f"  sorted hbar: first {sub[0][0]} {sub[0][1]:,.0f}, last {sub[-1][0]} {sub[-1][1]:,.0f}")
    seg = sorted(tot(lambda r: r["Customer Segment"], "Profit").items(), key=lambda kv: kv[1])
    print("  bar ascending (order):", [(k, round(v)) for k, v in seg])
    tp = tot(lambda r: (r["Product Category"], y(r)), "Profit")
    print("  trellis Technology:", {yy: round(tp[("Technology", yy)]) for yy in range(2013, 2017)})
    print("  two-column table:", {k: round(v, 2) for k, v in sorted(tot(lambda r: r["Customer Segment"], "Sales").items())})


def main(out_dir):
    load_dotenv(ROOT / ".env")
    token = OacToken.from_file(os.environ["OAC_URL"], ROOT / os.environ["OAC_TOKENS"])
    with OacMcp(os.environ["OAC_URL"], token, timeout=600) as mcp:
        templates = [workbook_json(mcp, n) for n in ("Examples", "Example 2", "Varianti")]
        xsa = templates[0]["datasources"]["children"][0]["subjectArea"]
        spec = WorkbookSpec.model_validate({"dataset": {"platform": "oac", "id": xsa, "name": "Retail Orders"},
                                            "name": NAME, "columns": COLUMNS, "canvases": [
            {"title": "Varianti generate", "visuals": [{"kind": k, "title": t, "roles": r, "sort": s} for k, t, r, s in V]}]})
        wb = compile_workbook(spec, Workbook(templates[0], extra_templates=templates[1:]), describe(mcp, xsa))
        problems = definition_problems(wb.definition())
        print("structural checks:", problems or "no problem")
        if problems:
            return 1
        wid = save_in_test_folder(mcp, NAME, wb.definition(), "Generated by Live Insight (M3c): roles with several columns and sorting")
        for p in export_png(mcp, wid, out_dir, "m3c", height=1400):
            print("PNG:", p)
    expected()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "out"))
