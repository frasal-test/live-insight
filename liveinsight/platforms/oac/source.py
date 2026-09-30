"""The OAC DataSource: the model's Logical SQL and the visuals' queries, run through the MCP server.

The query guide says the Logical SQL traps met on OAC (27/9, profiling benchmark): AVG of a measure is its default
aggregate unless OVERRIDEAGGR, expressions apply after aggregation, NOT IN (subquery) is silently wrong.

Everything here is Oracle Analytics Logical SQL: placeholders expanded into full XSA references, date grains,
filter conditions with the same expressions the workbook will use, and OAC's semantics for a filter on a measure.
Logical SQL rules checked against the Oracle reference ("Logical SQL Reference", Display Functions):
REPORT_SUM/REPORT_AVG/REPORT_AGGREGATE are allowed only in the SELECT list (ORDER BY on them: nQSError 42036),
empty BY = grand total.
"""
import re

from liveinsight.engine.platform import QueryError
from liveinsight.engine.spec import PLACEHOLDER, Calculation, DatasetInfo, DatasetRef, DateColumn
from liveinsight.platforms.oac.li_dva import GRAINS, period_filter_value
from liveinsight.platforms.oac.mcp import McpError, OacMcp, rows_of

QUERY_MAX_ROWS = 1000
PREVIEW_MAX_ROWS = 5000

QUERY_TOOL = "Runs a Logical SQL query on the dataset. Columns as {Column name}, source {dataset}."
# The system-prompt section on queries, as the model reads it.
QUERY_GUIDE = """# How to query: run_query
Write Oracle Analytics Logical SQL. In queries write columns as {Column name} and the source as {dataset}: \
the system expands them into full references. Example:
  SELECT {Customer Segment} AS segment, {Sales} AS sales FROM {dataset} ORDER BY 2 DESC FETCH FIRST 20 ROWS ONLY
Logical SQL rules:
- no GROUP BY and no JOIN: measures aggregate by themselves, with their default rule (usually a sum), over the \
attributes in the SELECT;
- measures aggregate before any expression around them: AVG({Discount}) is the default aggregate (a sum), not the \
average: for the average of a measure write AVG(OVERRIDEAGGR({Discount})). A sum is meaningless for prices, rates \
and percentages: average them this way;
- SUM({Gross Unit Price} * {Quantity Ordered}) is SUM(price) x SUM(quantity): a calculation per row (a product, the days between two \
dates) goes in a subquery at the row grain, then aggregates outside. The subquery must select the row key \
({Order Line ID}) or OAC aggregates the rows before the calculation; the outer query needs GROUP BY for its \
attributes: SELECT AVG(t.d) FROM (SELECT {Order Line ID} AS k, TIMESTAMPDIFF(SQL_TSI_DAY, {Order Date}, \
{Ship Date}) AS d FROM {dataset}) t;
- never NOT IN (subquery): it returns wrong counts without an error. To find keys missing in a period, compute a \
FILTER measure per key in a subquery and test IS NULL outside;
- distinct count: COUNT(DISTINCT {Order ID}); ratios between measures: {Profit} / {Sales};
- time: YEAR({Order Date}), QUARTER_OF_YEAR(...), MONTH(...); truncations: ExtractYear, ExtractQuarter, \
ExtractMonth (they return the first day of the period);
- filters: WHERE on attributes; filtered measure: FILTER({Sales} USING {Customer Segment} = 'Corporate');
- share of total: {Sales} / REPORT_SUM({Sales} BY ) (empty BY = grand total); REPORT_SUM goes only in the \
SELECT: never ORDER BY that column, order by the measure; ranking: RANK({Sales});
- always ORDER BY and, if there can be many rows, FETCH FIRST N ROWS ONLY.
If a query fails, read the error and fix it.
"""


def expand(text: str, ds: DatasetInfo, dataset_name: str | None = None) -> str:
    """{dataset} -> XSA(...), {Column name} -> full reference. Unknown columns: error.

    {<dataset name>} works as {dataset} too: models tend to write the name (e.g. {Retail Orders FS}) and the
    intent is clear, better not to waste a step on the error.
    """
    def sub(m):
        name = m.group(1).strip()                       # Cohere also writes {\r\ndataset}: spaces and newlines do not count
        if name == "dataset" or (dataset_name and name.strip().casefold() == dataset_name.strip().casefold()):
            return ds.xsa
        if name not in ds.columns:
            raise QueryError(f"unknown column {{{name}}}: use only the dataset columns, "
                             f"and {{dataset}} for the source in the FROM")
        return f'{ds.xsa}."{ds.table}"."{name}"'
    return PLACEHOLDER.sub(sub, text)


def column_expression(c, ds: DatasetInfo) -> str:
    if isinstance(c, Calculation):
        return expand(c.expression, ds)
    ref = f'{ds.xsa}."{ds.table}"."{c.source}"'
    return f"{GRAINS[c.grain]}({ref})" if isinstance(c, DateColumn) else ref


def sql_literal(v) -> str:
    return str(v) if isinstance(v, (int, float)) else "'" + str(v).replace("'", "''") + "'"


def value_literal(v, c) -> str:
    """A value returned by OAC as a Logical SQL literal (truncated dates come back as '2013-01-01 00:00:00.0')."""
    if isinstance(c, DateColumn):
        return f"TIMESTAMP '{str(v)[:19]}'"
    return sql_literal(v)


def filter_condition(f, c, ds: DatasetInfo) -> str:
    """A visual filter as a Logical SQL condition, on the same expression as the workbook: "in" on attributes and
    periods, "between" on a range of days. Filters on a measure have other semantics: OacSource._measure_filter."""
    expr = column_expression(c, ds)
    if f.op == "between":                                 # range of days (DateColumn with grain day)
        low, high = (f"TIMESTAMP '{period_filter_value(str(v), 'day')[0].replace('T', ' ')}'" for v in f.values)
        return f"{expr} BETWEEN {low} AND {high}"
    if isinstance(c, DateColumn):
        values = [f"TIMESTAMP '{period_filter_value(str(v), c.grain)[0].replace('T', ' ')}'" for v in f.values]
    else:
        values = [sql_literal(v) for v in f.values]
    return f"{expr} IN ({', '.join(values)})"


def format_period(value, grain):
    """'2013-04-01 00:00:00.0' -> '2013 Q2' (quarter), '2013-04' (month), '2013' (year)."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value))
    if not m:
        return value
    y, mo, d = m.groups()
    return {"year": y, "quarter": f"{y} Q{(int(mo) - 1) // 3 + 1}", "month": f"{y}-{mo}"}.get(grain, f"{y}-{mo}-{d}")


class OacSource:
    """A DataSource (liveinsight.engine.platform) on one OAC dataset, with the user's MCP connection."""
    query_tool = QUERY_TOOL
    query_guide = QUERY_GUIDE

    def __init__(self, mcp: OacMcp, dataset: DatasetInfo, name: str):
        self.mcp, self.dataset = mcp, dataset
        self.ref = DatasetRef(platform="oac", id=dataset.xsa, name=name)

    def _rows(self, sql: str, max_rows: int) -> list[dict]:
        try:
            return rows_of(self.mcp.call("execute_logical_sql", query=sql, maxRows=max_rows))
        except McpError as e:                             # the model reads it and fixes the query
            raise QueryError(str(e)) from e

    def query(self, text: str) -> list[dict]:
        return self._rows(expand(text, self.dataset, self.ref.name), QUERY_MAX_ROWS)

    def _is_measure(self, c) -> bool:
        return (isinstance(c, Calculation) and c.is_measure) or \
            self.dataset.columns.get(getattr(c, "source", ""), {}).get("columnType") == "measure"

    def _measure_filter(self, f, measure, by) -> str:
        """A filter on a measure as in OAC: the measure is computed on all data, at the level of the visual's
        attributes (filterByColumns), without the other filters; the combinations in the range stay, then the other
        filters apply. Checked on 24/9 on the "Filter on two items" visual of "With Filters": year 2016 + Profit
        between -92,680 and -85,460 shows the three combinations with that Profit over all years, with 2016 sales."""
        low, high = f.values
        exprs = [column_expression(c, self.dataset) for c in by]
        sql = ("SELECT " + ", ".join(f"{e} AS c{i + 1}"
                                     for i, e in enumerate(exprs + [column_expression(measure, self.dataset)]))
               + f" FROM {self.dataset.xsa}")
        rows = self._rows(sql, QUERY_MAX_ROWS)
        m = f"c{len(exprs) + 1}"
        keep = [r for r in rows if r.get(m) is not None and low <= r[m] <= high]
        if not keep:
            return "1 = 0"
        if not by:                                           # no attribute: the total is in the range
            return "1 = 1"
        if len(by) == 1:
            return f"{exprs[0]} IN ({', '.join(value_literal(r['c1'], by[0]) for r in keep)})"
        return "(" + " OR ".join("(" + " AND ".join(f"{e} = {value_literal(r[f'c{i + 1}'], c)}"
                                                    for i, (e, c) in enumerate(zip(exprs, by))) + ")" for r in keep) + ")"

    def visual_rows(self, columns, filters, defined) -> tuple[str, list[dict]]:
        ds = self.dataset
        attrs = [i + 1 for i, c in enumerate(columns) if not self._is_measure(c)]
        by = [c for c in columns if not self._is_measure(c)]
        where = [self._measure_filter(f, defined[f.column], by) if self._is_measure(defined[f.column])
                 else filter_condition(f, defined[f.column], ds) for f in filters]
        empty = "1 = 0" in where                             # no combination in the range: empty preview
        where = [w for w in where if w not in ("1 = 1", "1 = 0")]
        sql = ("SELECT " + ", ".join(f"{column_expression(c, ds)} AS c{i + 1}" for i, c in enumerate(columns))
               + f" FROM {ds.xsa}" + (" WHERE " + " AND ".join(where) if where else "")
               + (" ORDER BY " + ", ".join(map(str, attrs)) if attrs else ""))
        rows = [] if empty else self._rows(sql, PREVIEW_MAX_ROWS)
        data = [{c.id: format_period(r.get(f"c{i + 1}"), c.grain) if isinstance(c, DateColumn) else r.get(f"c{i + 1}")
                 for i, c in enumerate(columns)} for r in rows]
        return sql, data
