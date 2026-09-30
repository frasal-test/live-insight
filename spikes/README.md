# spikes/

Throwaway scripts written while building Live Insight, kept as a record of how each piece was explored. They are
**not** part of the app and not maintained: they need a live Oracle Analytics Cloud instance, a token in `.env`
(`OAC_URL`, `OAC_TOKENS`) and, for some of them, a workbook template that is not in this repository.

| Script | What it explored |
|---|---|
| `m1_mcp_probe.py` | Connecting to the OAC MCP server with the user's OAuth identity: `search_catalog`, `describe_data`, `execute_logical_sql`, reading a catalog workbook as JSON. |
| `m2_catalog_workbook.py` | Generating a workbook that points to a catalog dataset (no embedded data), saving it in the catalog and exporting a PNG. |
| `m2_dva.py` | Building a downloadable `.dva` without dataset data, in two variants, to see what OAC accepts on import. |
| `m3c_generate.py` | Generating the different visual kinds from a `WorkbookSpec`, rebinding columns, and checking the values OAC draws. |
| `m7_render_previews.py` | Rendering the Vega-Lite previews to PNG with real data and no model, to review them before writing the UI. |
| `testfolder.py` | Helpers shared by the scripts: save a workbook in the test folder and export it as PNG. |

The findings are written up in [`docs/SPIKE-MCP.md`](../docs/SPIKE-MCP.md).
