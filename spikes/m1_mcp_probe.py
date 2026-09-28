"""M1 — MCP spike: from the Python backend to the OAC MCP server, with the user's OAuth identity.

Checks, with no LLM:
1. HTTP connection to <OAC>/api/mcp with the user's token (automatic refresh);
2. search_catalog -> the "Retail Orders" dataset and its xsaExpr;
3. describe_data -> columns and full names, compared with the reference li_dva writes;
4. execute_logical_sql -> a few queries whose results are compared with the dataset embedded in the template
   (dva_data.py, with spaces trimmed as OAC does);
5. resources/read -> the JSON of a catalog workbook (a template read through MCP, without .dva).

Usage:  uv run python spikes/m1_mcp_probe.py
Needs .env with OAC_URL and OAC_TOKENS (file downloaded from Profile > Access token), and a sample .dva with data in
dva-lab/templates/Examples.dva (not in git).
"""
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from liveinsight.platforms.oac.dva_data import load_rows                                                   # noqa: E402
from liveinsight.platforms.oac.li_dva import Workbook                                                     # noqa: E402
from liveinsight.platforms.oac.auth import OacToken                                       # noqa: E402
from liveinsight.platforms.oac.mcp import OacMcp, payload_of, rows_of                     # noqa: E402

TEMPLATE = ROOT / "dva-lab/templates/Examples.dva"
TOLERANCE = 0.005                    # to the cent


def check(label, got, expected):
    """Compares two dicts key -> number; prints the outcome and returns True if they match."""
    bad = {k: (got.get(k), v) for k, v in expected.items()
           if got.get(k) is None or abs(got[k] - v) > TOLERANCE}
    extra = set(got) - set(expected)
    ok = not bad and not extra
    print(f"  [{'OK' if ok else 'KO'}] {label}: {len(expected)} values" + ("" if ok else f"  different={bad} extra={extra}"))
    return ok


def main():
    load_dotenv(ROOT / ".env")
    oac_url = os.environ["OAC_URL"]
    token = OacToken.from_file(oac_url, ROOT / os.environ["OAC_TOKENS"])
    print(f"token: {token.seconds_left() / 60:.0f} minutes to expiry (it renews by itself)")

    with OacMcp(oac_url, token) as mcp:
        print(f"MCP: {mcp.server_info['serverInfo']['name']} {mcp.server_info['serverInfo']['version']}, "
              f"protocol {mcp.server_info['protocolVersion']}, {len(mcp.list_tools())} tools")

        # 1. the dataset and its identifier
        # copies with the same name can exist (importing a .dva with the dataset metadata creates one):
        # take the one the template points to, whose data are in dva_data.py
        wb = Workbook(str(TEMPLATE))
        found = payload_of(mcp.call("search_catalog", search="Retail Orders", types=["datasets"]))["items"]
        ds = next(i for i in found if i.get("xsaExpr") == wb.source)
        xsa = ds["xsaExpr"]
        print(f"\ndataset: {ds['name']}  ->  {xsa}")

        # 2. metadata
        meta = payload_of(mcp.call("describe_data", datamodelName=xsa))
        table = meta["tables"][0]
        cols = {c["name"]: c for c in table["columns"]}
        measures = [n for n, c in cols.items() if c["columnType"] == "measure"]
        print(f"table: {table['fullQualifiedName'][len(xsa) + 1:]}, {len(cols)} columns, measures: {measures}")
        same_ref = all(cols[n]["fullyQualifiedName"] == wb.ref(n) for n in cols) and wb.source == xsa
        print(f"  [{'OK' if same_ref else 'KO'}] describe_data's full names are the references li_dva writes")

        # 3. Logical SQL queries against the values expected from the dataset
        rows = load_rows(str(TEMPLATE))
        T = table["fullQualifiedName"]

        def q(select, order="1"):
            t0 = time.time()
            out = rows_of(mcp.call("execute_logical_sql", query=f"SELECT {select} FROM {xsa} ORDER BY {order}",
                                   maxRows=1000))
            q.elapsed.append(time.time() - t0)
            return out
        q.elapsed = []

        def total(key, measure):
            acc = defaultdict(float)
            for r in rows:
                acc[key(r)] += r[measure] or 0
            return dict(acc)

        print("\nquery:")
        oks = []
        got = {r["s_1"]: r["s_2"] for r in q(f'{T}."Customer Segment" s_1, {T}."Profit" s_2')}
        oks.append(check("Profit by segment", got, total(lambda r: r["Customer Segment"], "Profit")))

        got = {r["s_1"]: r["s_2"] for r in q(
            f'{T}."Customer Segment" s_1, {T}."Sales" / COUNT(DISTINCT {T}."Order ID") s_2')}
        sales = total(lambda r: r["Customer Segment"], "Sales")
        orders = defaultdict(set)
        for r in rows:
            orders[r["Customer Segment"]].add(r["Order ID"])
        oks.append(check("Average order value by segment (calculation)", got, {k: sales[k] / len(orders[k]) for k in sales}))

        got = {(int(r["s_1"]), int(r["s_2"])): r["s_3"] for r in q(
            f'YEAR({T}."Order Date") s_1, QUARTER_OF_YEAR({T}."Order Date") s_2, {T}."Sales" s_3', "1, 2")}
        oks.append(check("Sales by year and quarter", got,
                         total(lambda r: (r["Order Date"].year, (r["Order Date"].month - 1) // 3 + 1), "Sales")))

        got = {r["s_1"]: r["s_2"] for r in q(f'{T}."Order Priority" s_1, {T}."Sales" s_2')}
        oks.append(check("Sales by priority (spaces trimmed)", got, total(lambda r: r["Order Priority"], "Sales")))

        top = q(f'{T}."City" s_1, {T}."Quantity Ordered" s_2', "2 DESC, 1")
        city = sorted(total(lambda r: r["City"], "Quantity Ordered").items(), key=lambda kv: (-kv[1], kv[0]))
        ok = len(top) == len(city) and (top[0]["s_1"], top[0]["s_2"]) == city[0]
        print(f"  [{'OK' if ok else 'KO'}] Quantity by city: {len(top)} cities, first {top[0]['s_1']} {top[0]['s_2']:,.0f}")
        oks.append(ok)
        print(f"  times: {', '.join(f'{t:.1f}s' for t in q.elapsed)}")

        # 4. a workbook read from the catalog as JSON
        wbs = payload_of(mcp.call("search_catalog", search="*", types=["workbooks"], limit=200))["items"]
        print(f"\nworkbooks in the catalog: {len(wbs)}")
        item = next((w for w in wbs if w.get("contentResource")), None)
        if item:
            contents = mcp.read_resource(item["contentResource"])
            body = contents[0].get("text") if contents else None
            parsed = json.loads(body) if body else {}
            defn = parsed.get("json", parsed)
            print(f"  '{item['name']}': {len(body or '')} characters, sections {sorted(defn)[:12]}")
            views = defn.get("views", {})
            n = len(views.get("children", [])) if isinstance(views, dict) else len(views)
            print(f"  views: {n}, datasources: {[d.get('subjectArea') for d in defn.get('datasources', {}).get('children', [])]}")

    print(f"\nOUTCOME: {sum(oks)}/{len(oks)} queries match the expected values")
    return 0 if all(oks) else 1


if __name__ == "__main__":
    sys.exit(main())
