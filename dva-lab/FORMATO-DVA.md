# The Oracle Analytics .dva format — reverse-engineering notes

**Date:** 23 September 2026
**Sample:** a workbook exported from OAC (DV `26.07`, catalog `23.3.0.0.0`), without password, with data included.
**Status:** container format decoded 100% and rebuilt bit-identical. Modified files import into OAC (see "Tests").

---

## 1. The container: a ZIP

```text
META-INF/MANIFEST.MF                 OSGi-like bundle: name, version, SHA-384, AES
META-INF/compatibility/              version.txt (dv=26.07-...), platform.json (DV5.0), metadataCoreVersion
META-INF/creation/                   export options (data yes, credentials no, ACLs preserved)
content/MANIFEST.MF                  exported catalog objects and their paths
content/serviceinstance<ts>.arc      THE WORKBOOK: zlib catalog archive (see §2)
datasets/embedded2/, embedded4/      datasets loaded from files: metadata.json + data.xlsx
datasets/datamodel1/, datamodel3/    data model metadata
datasets/*.json                      connections, providers, dataflows (empty in this sample)
```

`Bundle-HashAlgorithm: SHA-384` and `Bundle-EncryptionAlgorithm: AES` in the manifest do not match any signature in
the archive: no `.SF`/`.RSA` file, no per-entry digest. AES probably concerns credentials when exporting with a
password. The import tests confirm there is no integrity check on the content.

## 2. The .arc file: a catalog archive

zlib (`78 9c`), no encryption. Decompressed, a sequence of little-endian records:

```text
u32 magic = 16
4 x [u32 len + bytes]       preamble: catalog version, OAC host, CatalogPhysicalPath (JSON), account (JSON)
repeated:
  u16 0 + u32 len + JSON    object header: ACL, ItemName, ItemType, ObjectSignature, OriginalPath, ...
  u16 1 + u64 len + bytes   object body (absent for folders)
u16 2                       end
```

Codec: `liveinsight/platforms/oac/arc_codec.py` (`parse` / `serialize`). Round trip verified byte by byte.
Note the preamble: it holds the instance host and the exporting account, which is why a `.dva` must not be published.

### Objects in the sample

| ItemName | ItemType | Signature | Content |
|---|---|---|---|
| `<workbook name>` | 1 (folder) | `projectfolder1` | **A workbook is a catalog folder** |
| 5 × GUID | 4 | `externalbinary1` | Images as `data:image/png;base64,...` (banner, logo, screenshot) |
| `screenshots` | 1 | — | Folder |
| `project_thumbnail.png` | 4 | `externalbinary1` | Thumbnail |
| `_projectdefn` | 4 | `projectinternaljson1` | **The workbook definition: compact JSON, UTF-8** |

## 3. The workbook definition (`_projectdefn`)

JSON of ~210 KB in the sample. Main sections:

| Key | Role |
|---|---|
| `datasources` | Sources: `XSA('<dataset guid>'.'<Name>#-#<suffix>#-#')` |
| `criteria.columns` | **Column dictionary**: `columnID` → **Logical SQL** expression |
| `views.children` | The visuals: kind (`pluginType`), column binding per role, formatting |
| `layouts.children` | The canvases: absolute position and size of every visual in pixels |
| `filterControlCollections`, `parameters`, `dataActions` | Filters, parameters, actions |
| `reportConfig`, `stories`, `snapshots`, `eventWiring` | Configuration, stories, events |

### Columns: Logical SQL

```text
SALES               XSA('<guid>'.'Retail Orders#-#<suffix>#-#')."Retail Data final"."Sales"
ORDER_DATE          ExtractMonth(XSA(...)."Retail Data final"."Order Date")
ofCustomers         COUNT(DISTINCT XSA(...)."Retail Data final"."Customer Name")
OrderPriorPeriod    AGO(XSA(...)."Retail Data final"."Sales", ...)
```

It is the logical language of OBIEE/OAC: documented, with time functions (`AGO`, `TODATE`), `FILTER`, `RANK`,
`CLUSTER`. It is the natural target of generated queries.

### Visuals: a grammar of graphics

Example — a "Sales Trend" line (`view!1`):

```text
pluginType   oracle.bi.tech.chart.line
detail       ORDER_DATE            (x axis)
measures     SALES                 (y axis)
color        PRODUCT_CATEGORY      (series)
tooltip      PROFIT, QUANTITY_ORDERED, DISCOUNT, SHIPPING_COST
viewConfig   forecast seasonalArima 6 periods; trend LINEAR dashed; axis title "Order Month"
```

Roles seen: `row`, `detail`, `measures`, `color`, `tooltip`, `conditionalFormatting`. It is the same model as
Vega-Lite's `encoding`: **a visual spec translates almost 1:1**.

### Beware: the binding is there twice

Every visual has **two** data models, and they must stay consistent:

| Level | Where | Who uses it |
|---|---|---|
| **Logical** | `dataModels.children[].logicalDataModel.settings.logicalDataModel.logicalEdges` | The editor's grammar panel |
| **Physical** | `dataModels.children[].measuresList`, `edges`, and the `nestedViews[].view.dataModels` with their `propertyAdditions` (`min.<COL>`, `max.<COL>`, `grandTotal.<COL>`) | The query and the rendering |

Found with TEST3: changing only the logical level, OAC imports without errors, the panel shows the new measure, but
**the chart still draws the old data** (sales percentages) and the total in the donut is **0**, because it looks for
a total the query did not fetch. A silent error: no message, wrong data.

Rule for the generator: every column change goes through `rebind()`, which moves `columnID`, `valueColumnID` and the
ids of the `propertyAdditions` across the whole visual. The acceptance test of every generated visual compares **the
drawn numbers** with the expected ones, not only the configuration.

`ldm_generation: 1` and `physicalDataModelVersion: 2.5` suggest OAC derives the physical level from the logical one
when editing in the editor, but on import it trusts the saved one.

Visual kinds in the sample (41 visuals on 4 canvases): `ngperformancetile` (17), `image` (5), `table` (4),
`chart.donut` (3), `textbox` (3), `chart.line`, `chart.bar`, `chart.stackbar`, `chart.horizontalstackbar`,
`chart.horizontalBoxplot`, `chart.comboMultiLayerChart`, `butterfly`, `pivot`, `narrative`.

### Workbook calculations ("My Calculations")

A calculation is a column of `criteria.columns` with two more fields:

```json
{"columnID": "ofOrders", "userExpression": true, "type": "saw:regularColumn",
 "columnFormula": {"expr": {"expression": "COUNT(DISTINCT XSA(...).\"Retail Data final\".\"Order ID\")", ...}},
 "columnHeading": {"caption": {"text": "# of Orders"}},
 "columnDescription": {"caption": {"text": "..."}}}
```

The "My Calculations" folder does not exist in the file: the editor gathers there the columns with
`userExpression: true`. `columnDescription` is optional. It is the way to add derived quantities without touching the
customer's semantic model.

Syntax: measures are used without explicit aggregation when the default one is fine
(`"Profit" / (COUNT(DISTINCT "Order ID"))`). Per-column properties in
`criteria.criteriaConfig.settings.columnPropertyMap` matter only for dates (time level).

### Canvases, layout and stories

- **Canvas** = a `saw:canvas` view (`canvas!N`) with `rootLayoutName`, the tab name in `viewCaption`,
  `masterViewName`. **The tab order is the order of the canvas views in `views.children`.**
- **Layout** = `layouts.children[i]` (`oracle.bi.tech.layout.split`), with for every visual `viewName`, `left`, `top`,
  `width`, `height`, `zIndex`.
- **Stories**: every canvas has a page in `snapshots` (`snapshot!canvas!N`, with a UUID `hash`) listed in
  `stories.children[0].children`. A new canvas also needs its page.
- `filterControlCollections` is optional per visual.

### The minimal skeleton of a workbook

From a template with 6 visuals on one canvas and no customisation: `_projectdefn` has only **7 sections** —
`criteria`, `datasources`, `views`, `layouts`, `parameters`, `reportConfig`, `projectVersion`. So `stories`,
`snapshots`, `filterControlCollections`, `dataActions`, `eventWiring` are **optional**: needed only when used. A
canvas can be minimal too: `type`, `viewName`, `rootLayoutName`, `canvasConfig` (without `viewCaption` it gets the
default name).

Clean visuals, ~2 KB each:

| pluginType | Logical roles |
|---|---|
| `chart.bar` | `measures`, `detail` |
| `chart.horizontalbar` | `measures`, `detail` |
| `chart.line` | `measures`, `detail` (date at month grain) |
| `table` | `row` (dimensions and measures together, in column order) |
| `pivot` | `row`, `col`, `measures` |
| `chart.scatter` | `measures` with **tags** `obitech-scatterchart#x` / `#y`, `detail` |

Beware of the scatter: X and Y are not distinct roles but **tags** on the measures. The generator writes the tags too.

From a second template:

| pluginType | Logical roles | Notes |
|---|---|---|
| `chart.nonstackedarea` | `measures`, `detail` | |
| `chart.radar` | `measures`, `detail` | |
| `chart.boxplot` | `measures`, `detail`, `item` | `item` = a **local column** of the visual (see below) |
| `map` | `detail` (place), `size` | No geographic configuration: OAC recognises cities by itself. The physical model uses the internal placeholder `__EmbeddedVizDummyMeasureLink__` |
| `narrative` | `measures`, `row` | |
| `ngperformancetile` | `measures`, `detail` | |

These are the templates bundled in `liveinsight/platforms/oac/templates/` (anonymised).

### Local columns of visuals (`viz:columns`)

A visual can define its own columns in `viewConfig.settings["viz:columns"].columns`, with ids
`vizColumn.<viewName>.<name>`. The boxplot uses them for the `item` role:
`BIN("Shipping Cost" BY ROWID(...) INTO 15 BINS RETURNING NUMBER)`, with
`advancedAnalyticsType: oracle.bi.tech.binby`, and derived references `.RANGE_LO` / `.RANGE_HI`.

Two consequences for the generator (handled by `localize()` in `li_dva.py`):
- **the id contains the visual's name**: cloning `view!3` as `view!9`, every `vizColumn.view!3.` becomes
  `vizColumn.view!9.`;
- **OAC does not clean up**: the template had 6 local columns, 5 of them leftovers of edits in the editor. The clone
  keeps only the referenced ones.

The other 11 visual kinds do not contain their own name.

### Filter on a single visual

In `filterControlCollections.children[]`, an entry with `name` = the visual's name:

```json
{"name": "view!5", "filterControls": {"children": [{
  "filterID": "<uuid>", "columnID": "OrderDate_49", "type": "saw:columnFilterControl",
  "filterControlConfig": {"settings": {"filterModelClassName": "obitech-listfilter/listfilter.ListFilterModel", "location": "filter_bar"}},
  "formula": {"expr": {"expression": "ExtractYear(XSA(...).\"Retail Data final\".\"Order Date\")"}},
  "filterOperator": {"op": "in"}, "filterControlDefaultValues": {...}}]}}
```

Supported by the generator since 24/9 (`Workbook.filter_control`): list filters on attributes and periods, number
ranges on measures, date ranges on days, reproduced exactly from workbooks made by hand
(`tests/test_filters.py`, fixtures in `tests/fixtures/oac/`).

**Dates:** the grain is in the expression (`ExtractMonth(...)`, `ExtractYear(...)`) plus an entry in
`criteriaConfig.settings.columnPropertyMap` with `timeLevel` (`month`, `year`, `quarter`) and `parentExpression` =
the original date column. One column per grain used.

### Allocating ids

New ids (`view!N`, `canvas!N`, `layoutN`) are chosen by searching the **whole** JSON, not only live views: deleted
visuals leave references behind, for example colour assignments in `reportConfig` (`vizColumn.view!58.…`). Reusing
that id would inherit someone else's configuration. See `_free_id()` in `li_dva.py`.

## 4. What it means for generation

```text
columns    -> criteria.columns   (Logical SQL expressions on existing datasets or subject areas)
visuals    -> views.children[]   (pluginType + logicalEdges per role + viewConfig)
layout     -> layouts.children[] (grid -> pixels)
workbook   -> _projectdefn -> .arc (arc_codec) -> zlib -> ZIP with MANIFEST
```

Method: **template + substitution**. Start from a workbook exported from the target environment (version
compatibility by construction) and change only the variable parts.

## 5. Open questions (not pursued)

1. **Workbooks on a subject area** (RPD/semantic model, typical of OAS) instead of a dataset: what does the reference
   look like instead of `XSA(...)`? Needs a second sample.
2. **OAS**: same structure? Needs an export from OAS.

Renaming is solved: the name appears in `ItemName`, `OriginalPath`, `content/MANIFEST.MF` (lines folded at 72 bytes)
and `Bundle-Application-Name` (`Workbook.rename`).

## 6. Tests

Files generated by `build_tests.py` from a sample workbook:

| File | Change | If the import fails |
|---|---|---|
| `TEST1_identico.dva` | None: recompression and re-zip, bit-identical `.arc` | The block is in the ZIP container or the compression |
| `TEST2_testi.dva` | Canvas title and a visual title changed | There is a check on the content of `_projectdefn` |
| `TEST3_binding.dva` | As TEST2 + a donut from `SALES` to `PROFIT`, **only in the logical model** | The binding has cross references to update |
| `TEST4_binding_completo.dva` | Title changed + donut from `SALES` to `QUANTITY_ORDERED` **at every level** (`rebind`, 9 references) | The physical model has other constraints |
| `TEST5_nuovo_canvas.dva` | **Creation**: a never-used column `ORDER_PRIORITY`, a calculation "Avg Order Value (TEST5)", a bar chart cloned and cleaned, a new canvas with its story page | Something is missing in registering canvas, visual or calculation |

### Outcomes

| Test | Outcome (23 Sep 2026) |
|---|---|
| TEST1 | Imported |
| TEST2 | Imported, changed texts visible |
| TEST3 | Imported, the panel shows Profit, but **slices with the sales percentages and total 0**: logical-only binding, see §3 |
| TEST4 | **Passed.** Drawn: Consumer 20.67% · Corporate 34.61% · Home Office 25.4% · Small Business 19.3% · total 128K — identical to the expected values computed from the dataset |
| TEST5 | **Passed.** Fifth tab, new bar chart, new column and new calculation. Drawn: Critical 1,278.11 · High 1,485.84 · Low 1,390.50 · Medium 1,270.90 · Not Specified 1,355.03 — **identical to the cent** to the values recomputed with trimming. OAC **trims trailing spaces** when ingesting a dataset, and merges `'Critical '` (1 order) with `'Critical'` |
| TEST6 | **Passed.** A **new workbook** generated by `li_dva.py` + `test6.py` from a template: 3 visuals (bar, line, pivot) with columns, time grains and a calculation different from the template, all with the expected values |
| TEST7 | The 12 visual kinds on two canvases (`test7.py`), columns different from the templates |

Note for test harnesses: expected values must be computed with OAC's normalisation (at least trimming spaces on
datasets loaded from files).

**Conclusion:** a workbook modified outside OAC, with a consistent binding in the logical and physical models, is
imported and runs correctly. Generating workbooks is feasible.

Regenerate:

```bash
uv run python dva-lab/build_tests.py "/path/to/workbook.dva"
```

## M3c — Roles with several columns and sorting (verified, 23 September 2026)

Reference: a workbook made by hand in OAC on the Retail Orders dataset (line with color, bar with two measures, bar
with color, five-column table, sorted hbar, line with trellis), compared with the one-column-per-role templates.

**Every column of a role is an element of its own in a list**, both in the logical and in the physical model:

| Role | Logical | Physical |
|---|---|---|
| `measures` | `logicalEdges.measures.logicalEdgeLayers[]` | `nestedViews[].view…measuresList.children[]`; min./max. of **all** measures sit on the first, grouped per measure; the outer `type: view` entry (`MeasureView_0`) stays one |
| `detail`, `row` | `logicalEdgeLayers[]` | levels of the `row` axis (`edgeLayers.children[]`) |
| `color` | column **before** the placeholder `{type: measure, visibility: hidden}` | same position on the `column` axis of the nested view |
| `col` (line trellis) | `logicalEdges.col` | `column` axis of the **outer** model (`dataModels…edges[1].edgeLayers`) |

Hence `resize_roles()` in `liveinsight/platforms/oac/li_dva.py`: the template with exactly the requested roles is
chosen; for every role the template's first column is the prototype and in **all** the lists that name it, it becomes
one copy per requested column; the template's other columns are removed. `tests/test_variants.py` checks that the
one-column templates give **exactly** the structures OAC saved.

**Sorting** (`set_sort`): `columnSort.measureSorts` (row and column axes, `descending`/`ascending`) on the measure's
logical level, plus `columnOrder` on the `row` axis of the nested view with a QDR `grandTotal/gt_column` +
`measure/<measure>`.

**Checked in OAC** (`spikes/m3c_generate.py`): line with color, bar with three measures, bar with color, hbar sorted
descending, bar sorted ascending, line trellis, six- and two-column tables, with columns different from the
reference. All drawn with the expected values.

Note: the OAC editor saves titles as HTML (`<p>…</p>`); plain-text titles work too.
