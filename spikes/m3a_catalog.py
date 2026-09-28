"""M3a — The catalog of the 12 visual kinds generated from a WorkbookSpec, saved in the test folder and exported as
PNG. The spec is the one of tests/test_compile.py.

Usage:  uv run python spikes/m3a_catalog.py [folder-for-the-pngs]
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "spikes"), str(ROOT / "tests")]
from liveinsight.platforms.oac.checks import definition_problems                              # noqa: E402
from liveinsight.platforms.oac.target import compile_workbook                                # noqa: E402
from liveinsight.platforms.oac.li_dva import Workbook                                         # noqa: E402
from liveinsight.platforms.oac.auth import OacToken                                       # noqa: E402
from liveinsight.platforms.oac.mcp import OacMcp                                          # noqa: E402
from liveinsight.engine.spec import DatasetInfo                                        # noqa: E402
from test_compile import all_visuals                                            # noqa: E402
from testfolder import catalog_json, dataset_by_xsa, export_png, save_in_test_folder  # noqa: E402

NAME = "M3a - Visual kinds from a spec"


def main(out_dir):
    load_dotenv(ROOT / ".env")
    token = OacToken.from_file(os.environ["OAC_URL"], ROOT / os.environ["OAC_TOKENS"])
    with OacMcp(os.environ["OAC_URL"], token, timeout=600) as mcp:
        templates = [catalog_json(mcp, "Examples"), catalog_json(mcp, "Example 2")]
        xsa = templates[0]["datasources"]["children"][0]["subjectArea"]
        dataset = DatasetInfo.from_describe(xsa, dataset_by_xsa(mcp, xsa))
        wb = compile_workbook(all_visuals(NAME), Workbook(templates[0], extra_templates=templates[1:]), dataset)
        problems = definition_problems(wb.definition())
        print("structural checks:", problems or "no problem")
        if problems:
            return 1
        wid = save_in_test_folder(mcp, NAME, wb.definition())
        for p in export_png(mcp, wid, out_dir, "m3a"):
            print("PNG:", p)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "out"))
