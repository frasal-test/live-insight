# Spike M1 — The Oracle Analytics Cloud MCP server from a backend

**Date:** 23 September 2026 · **Instance:** OAC eu-frankfurt-1, September 2026 release · **Server:** "Oracle Analytics MCP Server" 1.0.0, MCP protocol `2025-06-18`
**Outcome:** the Python backend talks to the MCP server over HTTP, with the user's OAuth token. 5 Logical SQL queries out of 5 match, to the cent, the values computed from the dataset (`spikes/m1_mcp_probe.py`).

## 1. Transport: direct HTTP, no bridge

```text
POST <OAC_URL>/api/mcp
Authorization: Bearer <the user's access token>
Accept: application/json, text/event-stream
initialize -> Mcp-Session-Id header -> notifications/initialized -> tools/call, resources/read
```

The `oac-mcp-connect` connector (Node) is only for desktop clients that speak stdio. A web backend uses the
endpoint directly: `liveinsight/platforms/oac/mcp.py` (~130 lines, httpx only).

## 2. Authentication: the token is the user's

The token downloaded from *Profile → Access token* is a JWT issued by the Identity Domain for the OAC instance's app
(`client_name: ANALYTICSINST_<instance>`), scope `urn:opc:resource:consumer::all offline_access`, lifetime
**60 minutes**. Queries run with the user's permissions: no technical account.

The code of the official connector (1.4.0) shows the two endpoints a web app needs, **without registering an OAuth
app of our own**:

| Operation | Call |
|---|---|
| **Sign-in** | the user's browser opens `<OAC_URL>/ui/dv/ui/api/v1/tokens/token?redirect_uri=<callback>`; OAC (with the user's SSO session) POSTs a **form-encoded** body to the callback with `access_token`, `refresh_token`, `expires_in` |
| **Refresh** | `POST <OAC_URL>/api/dv/api/v1/tokens/token/refresh`, `Authorization: Bearer <access token still valid>`, `Content-Type: text/plain`, body = refresh token → JSON `{accessToken, refreshToken, expiresIn}` |

The refresh is **verified**. It authenticates with the current access token, so it must happen before expiry
(`liveinsight/platforms/oac/auth.py` renews 5 minutes before the end). The sign-in works with a callback shaped like
the connector's, `http://127.0.0.1:<port 3000-9000>/oac-mcp-connect/callback/<nonce>` (verified later, in the
backend milestone; with a different redirect_uri OAC answered "service paused").

Note: these are internal endpoints used by Oracle's connector, not documented APIs. In Preview they can change.

## 3. The tools (11)

| Tool | Use for us |
|---|---|
| `search_catalog` | **Replaces `discoverData`** (deprecated). For datasets it returns `xsaExpr` |
| `find_matching_datasources` | Sources that fit a natural-language question |
| `describe_data` | Columns of a dataset (`datamodelName` = `xsaExpr`) or of a subject area |
| `execute_logical_sql` | The chat's queries. The tool description (82 KB) holds the full Logical SQL grammar |
| `save_catalog_content` | *Experimental.* Creates or replaces a workbook from `content.json` = the `_projectdefn`. **Writes to OAC**: needs `userApproved` |
| `export_workbook` | *Experimental.* PNG/PDF of a workbook (useful to check automatically what OAC draws) |
| `create_catalog_folder`, `copy/move/delete_catalog_item`, `update_catalog_acl` | Catalog management, not needed |

`execute_oac_ansi_sql`, mentioned in the documentation, does not appear on this instance.

## 4. Identifiers

For the dataset "Retail Orders FS", `search_catalog` returns:

```text
objectId  '<guid>'.'Retail Orders#-#<suffix>#-#'
xsaExpr   XSA('<guid>'.'Retail Orders#-#<suffix>#-#')
```

It is **exactly** the `subjectArea` of the workbooks' `datasources`, and for every column `describe_data` returns a
`fullyQualifiedName` identical to the reference `li_dva.ref()` writes:

```text
XSA('<guid>'.'Retail Orders#-#<suffix>#-#')."Retail Data final"."Sales"
```

So **the workbook's source and table come from the MCP metadata**, not from the template.

On datasets `describe_data` gives `columnType` (attribute/measure) and `dataType`, but **`aggregation: null` even for
measures**: the default aggregation cannot be read there (Live Insight lists them as "measure", without assuming SUM).

## 5. Templates read from the catalog

Every workbook has a `contentResource` (`content://catalog/workbooks/<base64url id>`). `resources/read` returns the
JSON of the definition: for "Examples" and "Example 2" it is **identical** to the `_projectdefn` of the exported
`.dva` files.

Consequences:

- **visual templates** can be read from the user's instance via MCP, with no manual export (Live Insight also ships
  anonymised copies in `liveinsight/platforms/oac/templates/`);
- the `.dva` container (ZIP, MANIFEST, `.arc`) is needed only for the **downloadable file**;
- with `save_catalog_content` the import becomes a single call.

## 6. To remember

- Catalog paths contain the user's email (`/@Catalog/users/<email>/...`): never in logs or in git.
- `execute_logical_sql` answers come in batches (`batches[].data`, `hasMore`, `cursorId`): `rows_of()` flattens them.
  Always pass `maxRows`.
- Oracle's security guidance: bounded queries (`maxRows`, `FETCH FIRST`), deterministic `ORDER BY`, no write without
  explicit approval.

## 7. M2 — A generated workbook pointing to the catalog dataset (verified)

`spikes/m2_catalog_workbook.py`, 23 September 2026:

1. templates "Examples" and "Example 2" read from the catalog (`resources/read`), not from the `.dva` files;
2. `Workbook.set_source(xsaExpr, table)` with the values of `search_catalog` and `describe_data`;
3. the TEST6 specification (bar with a calculation, quarterly line, priority × year pivot);
4. `save_catalog_content` in the "Live Insight - test" folder: `{"type": "workbooks", "name", "parentId",
   "content": {"json": <definition>}, "userApproved": true}`. The answer gives `reportPath`, **not the id**: the id is
   found again with `search_catalog` (`rootFolder` = the folder's id). To replace: `id` instead of `name`/`parentId`;
5. the definition read back from the catalog is **identical** to the one sent: OAC does not rewrite it on save;
6. `export_workbook` (PNG, `screenWidth`/`screenHeight`) returns one image per canvas, in about a minute.

In the PNG the values match the expected ones (pivot High 2016 = 684,133.07, Critical 2013 = 397,093.94; line min
2014 Q1, max 2016 Q4; bar Corporate max, Small Business min).

**Conclusion:** a workbook pointing to an existing dataset is just the JSON definition with `datasources` =
`xsaExpr`. No embedded data. The downloadable `.dva` still needs the skeleton of an export without data.

### The .dva without data (TEST8)

- **"Without data" export: only from the Data Visualization editor.** Exported from the Catalog page, the `.dva`
  holds the data even with the option off (`META-INF/creation/options.txt`: `datasetexportdata=true`, a 3 MB
  `data.xlsx`). From the DV editor the option is honoured (`datasetexportdata=false`, a 0-byte `data.xlsx`). Probably
  an OAC bug, version 26.07.
- Even without data, the skeleton holds the **dataset definitions** (`datasets/embedded2/metadata.json`,
  `datasets/datamodel1/metadata.json`), with the same GUID as the catalog dataset. The risk: the import touching the
  user's dataset.
- `spikes/m2_dva.py` builds two variants with the TEST6 specification: **TEST8A**, the skeleton as it is; **TEST8B**,
  without any dataset file.

| Test | Import outcome (23 Sep 2026) |
|---|---|
| **TEST8B** workbook only | **Passed.** Imported without questions, points to the original dataset; numbers identical to the expected ones |
| **TEST8A** with the dataset metadata | **To avoid.** Imported without questions, but OAC **silently creates a copy of the dataset** ("Retail Orders FS", new GUID, 0 rows) in the import folder and **rewires the workbook to the copy**: "No Data Found" everywhere. The original stays intact |

**Rule:** the downloadable `.dva` holds only the workbook (`Workbook.save()` does it by default,
`include_datasets=False`). The skeleton gives only the container (MANIFEST, compatibility, `.arc`).

Consequence for tools: the catalog can hold datasets with the same name. A dataset is always identified by
`xsaExpr`, never by its name.
