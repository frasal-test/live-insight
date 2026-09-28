"""M2 — The downloadable .dva, without embedded data.

Skeleton: a .dva exported from OAC "without data" (datasetexportdata=false). Visual templates: read from the catalog
via MCP. Specification: TEST6. Builds two variants to import into OAC:

  TEST8A  the skeleton as it is: dataset definitions (metadata.json) without data (empty data.xlsx)
  TEST8B  the workbook only: no dataset file. If OAC accepts it, it is the safe form, because the import cannot
          touch the catalog dataset (it does: see docs/SPIKE-MCP.md, TEST8)

Usage:  uv run python spikes/m2_dva.py path/to/skeleton.dva
"""
import json
import os
import re
import sys
import zipfile
import zlib
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "spikes")]
from liveinsight.platforms.oac.arc_codec import parse                                                     # noqa: E402
from liveinsight.platforms.oac.li_dva import LOCAL, Workbook                                              # noqa: E402
from liveinsight.platforms.oac.auth import OacToken                                       # noqa: E402
from liveinsight.platforms.oac.mcp import OacMcp, payload_of                              # noqa: E402
from m2_catalog_workbook import build_test6, workbook_json                       # noqa: E402

VARIANTS = {"TEST8A": ("Live Insight - TEST8A skeleton without data", True),
            "TEST8B": ("Live Insight - TEST8B workbook only", False)}


def structural_check(path, name, skeleton_name):
    z = zipfile.ZipFile(path)
    assert z.testzip() is None
    raw = zlib.decompress(z.read(next(n for n in z.namelist() if n.endswith(".arc"))))
    defn = json.loads(next(i for i in parse(raw)["items"] if i["header"]["ItemName"] == "_projectdefn")["body"])
    cols = {c["columnID"] for c in defn["criteria"]["columns"]["children"]}
    orphans = set()
    for v in defn["views"]["children"]:
        used = set(re.findall(r'"(?:columnID|valueColumnID)":\s*"([^"]+)"', json.dumps(v)))
        orphans |= {u for u in used - cols if not u.startswith((LOCAL, "__"))}
    blob = raw + b"".join(z.read(n) for n in z.namelist() if n.endswith(".MF"))
    datasets = [n for n in z.namelist() if n.startswith(("datasets/embedded", "datasets/datamodel")) and not n.endswith("/")]
    data = sum(z.getinfo(n).file_size for n in datasets if n.endswith("data.xlsx"))
    print(f"  {Path(path).name}: {len(z.namelist())} entries, dataset files {len(datasets)} (data {data} bytes), "
          f"orphan columns {sorted(orphans) or 'none'}, leftovers of '{skeleton_name}' {blob.count(skeleton_name.encode())}, "
          f"name in the manifests {blob.count(name.encode())}")
    return not orphans and data == 0 and blob.count(skeleton_name.encode()) == 0


def main(skeleton):
    load_dotenv(ROOT / ".env")
    token = OacToken.from_file(os.environ["OAC_URL"], ROOT / os.environ["OAC_TOKENS"])
    with OacMcp(os.environ["OAC_URL"], token) as mcp:
        templates = [workbook_json(mcp, "Examples"), workbook_json(mcp, "Example 2")]
        ds = next(i for i in payload_of(mcp.call("search_catalog", search="Retail Orders", types=["datasets"]))["items"]
                  if i.get("xsaExpr"))
        table = payload_of(mcp.call("describe_data", datamodelName=ds["xsaExpr"], tablesOnly=True))["tables"][0]["tableName"]

    out = ROOT / "out"
    out.mkdir(exist_ok=True)
    ok = True
    for code, (name, include_datasets) in VARIANTS.items():
        wb = Workbook(skeleton, extra_templates=templates, skeleton_visuals=False)
        skeleton_name = wb.name
        wb.set_source(ds["xsaExpr"], table)
        build_test6(wb)
        wb.rename(name)
        path = out / f"{code}.dva"
        wb.save(path, include_datasets=include_datasets)
        ok &= structural_check(path, name, skeleton_name)
    print("\nExpected (TEST6): bar Corporate 1,380.58 (max), Small Business 1,334.10 (min); line 16 quarters, "
          "min 2014 Q1 257,949.71, max 2016 Q4 1,012,865.72; pivot High 2016 684,133.07, Critical 2013 397,093.94")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
