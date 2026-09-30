"""The specification of a workbook: what the engine produces, small and validated.

The model never writes OAC JSON. The engine produces a WorkbookSpec (the contract, docs/ARCHITETTURA.md §1):
- columns: dataset columns, date columns with a grain, calculations;
- canvases with visuals: kind, title, roles -> column ids, sort, filters.

Validation has two levels:
- the shape (Pydantic): types, ids, references between columns and roles, number of columns per role;
- the dataset (`check_against`): columns exist, measures and attributes sit in the right roles, dates are dates,
  calculations name existing columns.
Errors are English sentences meant to go back to the model, which fixes the spec (25/9: the model reads English).

In calculation expressions dataset columns are written {Column name}: the platform expands them (OAC: into the
full reference XSA(...)."Table"."Column name").
"""
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

Kind = Literal["bar", "hbar", "line", "table", "pivot", "scatter", "area", "radar", "boxplot", "map", "narrative", "tile"]
Content = Literal["measure", "attribute", "any"]
ColumnId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,62}$")]
PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


@dataclass(frozen=True)
class Role:
    min: int                # columns allowed in the role: from min to max (0 = optional role)
    max: int
    content: Content
    help: str

    @property
    def count(self):        # compatibility: the number of columns when it is fixed
        return self.min


# Shapes verified in OAC: templates Examples/Example 2 (one column per role) and the "Varianti" workbook
# (line with color and with trellis, bar with two measures and with color, five-column table, sorted hbar).
# the library reproduces exactly those structures.
ROLES: dict[str, dict[str, Role]] = {
    "bar":       {"measures": Role(1, 3, "measure", "values; several measures = side-by-side bars"),
                  "detail": Role(1, 1, "attribute", "categories"),
                  "color": Role(0, 1, "attribute", "optional: side-by-side bars per value (few categories)")},
    "hbar":      {"measures": Role(1, 3, "measure", "values; several measures = side-by-side bars"),
                  "detail": Role(1, 1, "attribute", "categories (long labels)"),
                  "color": Role(0, 1, "attribute", "optional: side-by-side bars per value")},
    "line":      {"measures": Role(1, 1, "measure", "value"),
                  "detail": Role(1, 1, "attribute", "time axis"),
                  "color": Role(0, 1, "attribute", "optional: one line per value (few series)"),
                  "col": Role(0, 1, "attribute", "optional: trellis, one panel per value with the same scale (small multiples)")},
    "area":      {"measures": Role(1, 1, "measure", "value"), "detail": Role(1, 1, "attribute", "time axis")},
    "table":     {"row": Role(1, 12, "any", "table columns, in order: attributes first, then measures")},
    "pivot":     {"row": Role(1, 1, "attribute", "rows"), "col": Role(1, 1, "attribute", "columns"),
                  "measures": Role(1, 1, "measure", "values")},
    "scatter":   {"measures": Role(2, 2, "measure", "[X axis, Y axis]"), "detail": Role(1, 1, "attribute", "one point per value")},
    "radar":     {"measures": Role(1, 1, "measure", "value"), "detail": Role(1, 1, "attribute", "spokes")},
    "boxplot":   {"measures": Role(1, 1, "measure", "distributed value"), "detail": Role(1, 1, "attribute", "one box per value")},
    "map":       {"detail": Role(1, 1, "attribute", "place (city, country)"), "size": Role(1, 1, "measure", "point size")},
    "narrative": {"measures": Role(1, 1, "measure", "described value"), "row": Role(1, 1, "attribute", "categories")},
    "tile":      {"measures": Role(1, 1, "measure", "shown value"), "detail": Role(1, 1, "attribute", "category")},
}
SORTABLE = ("bar", "hbar")


class SourceColumn(BaseModel):
    kind: Literal["column"] = "column"
    id: ColumnId
    source: str = Field(description="column name in the dataset, as in describe_data")


class DateColumn(BaseModel):
    kind: Literal["date"] = "date"
    id: ColumnId
    source: str = Field(description="date column of the dataset")
    grain: Literal["year", "quarter", "month", "day"]


class Calculation(BaseModel):
    kind: Literal["calc"] = "calc"
    id: ColumnId
    caption: str = Field(min_length=1, max_length=80, description="name shown in the workbook")
    expression: str = Field(min_length=1, description="Logical SQL, dataset columns as {Column name}")
    description: str | None = None
    is_measure: bool = True


ColumnSpec = Annotated[Union[SourceColumn, DateColumn, Calculation], Field(discriminator="kind")]


PERIODS = {"year": (r"\d{4}", "'2016'"), "quarter": (r"\d{4} ?Q[1-4]", "'2016 Q1'"),
           "month": (r"\d{4}-\d{2}", "'2016-11'"), "day": (r"\d{4}-\d{2}-\d{2}", "'2016-11-05'")}


class FilterSpec(BaseModel):
    """A filter of the visual's filter bar (workbook "With Filters"): the platform applies it, and so does the preview."""
    column: ColumnId = Field(description="id of a column defined in columns (even if the visual does not show it)")
    op: Literal["in", "between"] = Field(
        description="in: values of an attribute, or periods of a date column ('2016', '2016 Q1', '2016-11', "
                    "'2016-11-05'); between: [minimum, maximum] of a measure, computed over the visual's attributes, "
                    "or [from, to] of a date column with grain day ('2015-09-01', '2015-11-30')")
    values: list[str | float] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def _values(self):
        if self.op == "between":
            if len(self.values) != 2:
                raise ValueError("between filter: two values are needed, [minimum, maximum] (or [from, to] for days)")
            try:                                            # "126000" -> 126000.0: the tool schema says strings
                self.values = [float(v) for v in self.values]
            except (TypeError, ValueError):
                self.values = [str(v).strip() for v in self.values]   # dates: filter_problems checks them
            if self.values[0] > self.values[1]:
                raise ValueError("between filter: the first value is greater than the second")
        return self


class VisualSpec(BaseModel):
    kind: Kind
    title: str = Field(min_length=1, max_length=120, description="says what the data show, not the chart type")
    roles: dict[str, list[ColumnId]]
    sort: Literal["desc", "asc"] | None = Field(default=None, description="bar/hbar: categories ordered by value")
    filters: list[FilterSpec] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _roles(self):
        rules = ROLES[self.kind]
        self.roles = {r: cols for r, cols in self.roles.items() if cols}          # empty roles = absent
        unknown = set(self.roles) - set(rules)
        missing = {r for r, rule in rules.items() if rule.min and r not in self.roles}
        if unknown or missing:
            raise ValueError(f"{self.kind}: allowed roles {sorted(rules)} (required "
                             f"{sorted(r for r, x in rules.items() if x.min)}), received {sorted(self.roles)}")
        for role, cols in self.roles.items():
            rule = rules[role]
            if not rule.min <= len(cols) <= rule.max:
                n = f"exactly {rule.min}" if rule.min == rule.max else f"from {rule.min} to {rule.max}"
                raise ValueError(f"{self.kind}.{role}: {n} columns are needed ({rule.help}), received {len(cols)}")
        # combinations not verified in OAC yet
        if self.kind in SORTABLE and len(self.roles["measures"]) > 1 and "color" in self.roles:
            raise ValueError(f"{self.kind}: several measures and color together are not supported; choose one")
        if self.kind == "line" and "color" in self.roles and "col" in self.roles:
            raise ValueError("line: color and trellis together are not supported; choose one")
        if self.sort and (self.kind not in SORTABLE or len(self.roles["measures"]) > 1 or "color" in self.roles):
            raise ValueError("sort: only for bar and hbar with one measure and no color")
        return self


class CanvasSpec(BaseModel):
    title: str = Field(min_length=1, max_length=60)
    visuals: list[VisualSpec] = Field(min_length=1, max_length=8)


SPEC_VERSION = 1
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "workbook-spec.schema.json"


class DatasetRef(BaseModel):
    """The dataset the workbook reads, on the platform that serves it (see docs/ARCHITETTURA.md, §1)."""
    platform: Literal["oac"] = Field(description="the platform that serves the dataset")
    id: str = Field(min_length=1, description="the dataset identifier on that platform (OAC: its xsaExpr)")
    name: str = Field(min_length=1, description="the dataset name, for people: never used to find it")


class WorkbookSpec(BaseModel):
    """The contract between the engine and the platform adapters: one JSON with everything needed to build the
    workbook, visuals nested inside. The engine writes it, a WorkbookTarget (OAC today) reads only this.
    Published as docs/workbook-spec.schema.json (`uv run python -m liveinsight.engine.spec` rewrites it)."""
    model_config = ConfigDict(title="Live Insight WorkbookSpec", json_schema_extra={
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/frasal-test/live-insight/blob/main/docs/workbook-spec.schema.json"})

    version: Literal[1] = Field(default=SPEC_VERSION, description="version of this contract: bump it on changes "
                                "that old readers cannot understand")
    dataset: DatasetRef
    name: str = Field(min_length=1, max_length=120, description="workbook name")
    columns: list[ColumnSpec] = Field(min_length=1, description="every column the visuals and filters use")
    canvases: list[CanvasSpec] = Field(min_length=1, max_length=10, description="the workbook pages")

    @model_validator(mode="after")
    def _references(self):
        ids = [c.id for c in self.columns]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"repeated column ids: {dup}")
        for canvas in self.canvases:
            for v in canvas.visuals:
                missing = sorted(({c for cols in v.roles.values() for c in cols} | {f.column for f in v.filters})
                                 - set(ids))
                if missing:
                    raise ValueError(f"visual '{v.title}': columns not defined in `columns`: {missing}")
        return self


def load_spec(data: str | bytes) -> WorkbookSpec:
    """A WorkbookSpec from its JSON: the only way a WorkbookTarget reads one. An object from the engine is refused,
    so nothing reaches a target outside the contract."""
    if not isinstance(data, (str, bytes)):
        raise TypeError(f"a WorkbookTarget reads the WorkbookSpec JSON, not {type(data).__name__}")
    try:
        version = json.loads(data).get("version")
    except (ValueError, AttributeError):
        version = None
    if version is not None and version != SPEC_VERSION:
        raise ValueError(f"WorkbookSpec version {version!r} not supported (this reader: {SPEC_VERSION})")
    return WorkbookSpec.model_validate_json(data)


def json_schema() -> str:
    return json.dumps(WorkbookSpec.model_json_schema(), indent=2, ensure_ascii=False) + "\n"


# -- checks against the dataset ------------------------------------------------------

@dataclass(frozen=True)
class DatasetInfo:
    """What the engine needs of the dataset (OAC: from search_catalog + describe_data)."""
    xsa: str                            # XSA('<guid>'.'<Name>')
    table: str                          # e.g. "Retail Data final"
    columns: dict[str, dict]            # name -> {"columnType": "attribute"|"measure", "dataType": ...}

    @classmethod
    def from_describe(cls, xsa, describe):
        table = describe["tables"][0]
        name = table["fullQualifiedName"][len(xsa) + 1:].strip('"')
        return cls(xsa, name, {c["name"]: c for c in table["columns"]})


def column_contents(columns, ds: DatasetInfo) -> dict[str, str]:
    """column id -> "measure" | "attribute" (date columns are attributes)."""
    out = {}
    for c in columns:
        if isinstance(c, Calculation):
            out[c.id] = "measure" if c.is_measure else "attribute"
        elif isinstance(c, DateColumn):
            out[c.id] = "attribute"
        else:
            out[c.id] = ds.columns.get(c.source, {}).get("columnType", "attribute")
    return out


def visual_attributes(v: VisualSpec, content: dict[str, str]) -> list[str]:
    """The visual's attributes, in role order: the level at which a filter on a measure is evaluated."""
    return list(dict.fromkeys(c for cols in v.roles.values() for c in cols if content.get(c) != "measure"))


def check_against(spec: WorkbookSpec, ds: DatasetInfo) -> list[str]:
    """Errors of the spec against the dataset (empty list = ok)."""
    errors = []
    content = {}
    grains = {c.id: c.grain for c in spec.columns if isinstance(c, DateColumn)}
    for c in spec.columns:
        if isinstance(c, Calculation):
            unknown = sorted({n.strip() for n in PLACEHOLDER.findall(c.expression)} - set(ds.columns))
            if unknown:
                errors.append(f"calculation {c.id}: columns not in the dataset {unknown}")
            content[c.id] = "measure" if c.is_measure else "attribute"
            continue
        meta = ds.columns.get(c.source)
        if meta is None:
            errors.append(f"column {c.id}: '{c.source}' does not exist in the dataset")
            continue
        if isinstance(c, DateColumn):
            if meta.get("dataType") not in ("DATE", "TIMESTAMP", "DATETIME"):
                errors.append(f"column {c.id}: '{c.source}' is not a date ({meta.get('dataType')})")
            content[c.id] = "attribute"
        else:
            content[c.id] = meta.get("columnType", "attribute")
    for canvas in spec.canvases:
        for v in canvas.visuals:
            for role, cols in v.roles.items():
                want = ROLES[v.kind][role].content
                for cid in cols:
                    if want != "any" and content.get(cid, want) != want:
                        what = "a measure" if want == "measure" else "an attribute"
                        errors.append(f"visual '{v.title}': {v.kind}.{role} wants {what}, {cid} is not")
            for f in v.filters:
                errors += filter_problems(v.title, f, content.get(f.column), grains.get(f.column))
    return errors


def filter_problems(title: str, f: FilterSpec, content: str | None, grain: str | None) -> list[str]:
    where = f"visual '{title}', filter on {f.column}"
    if f.op == "between":
        if content == "measure":
            ok = all(isinstance(v, float) for v in f.values)
            return [] if ok else [f"{where}: on a measure between wants two numbers"]
        if grain == "day":
            bad = [v for v in f.values if not re.fullmatch(PERIODS["day"][0], str(v))]
            return [f"{where}: invalid dates {bad} (e.g. '2015-09-01')"] if bad else []
        if grain:
            return [f"{where}: date range only on a column with grain day; for years, quarters or months use "
                    f"in with the list of periods"]
        return [f"{where}: between applies to measures and days; for an attribute use in"]
    if content == "measure":
        return [f"{where}: on a measure use between [minimum, maximum]"]
    if grain:
        pattern, example = PERIODS[grain]
        bad = [v for v in f.values if not re.fullmatch(pattern, str(v).strip())]
        if bad:
            return [f"{where}: invalid periods {bad} (grain {grain}: e.g. {example})"]
    return []


# -- layout -----------------------------------------------------------------------------

CANVAS_WIDTH, GAP, HEIGHT = 1380, 10, 380
WIDE = {"table", "pivot"}


def layout(visuals: list[VisualSpec]) -> list[tuple[int, int, int, int]]:
    """Deterministic grid: two visuals per row, tables and pivots full width.
    A visual left alone on its row takes all of it."""
    rows, current = [], []
    for i, v in enumerate(visuals):
        if v.kind in WIDE:
            if current:
                rows.append(current)
                current = []
            rows.append([i])
        else:
            current.append(i)
            if len(current) == 2:
                rows.append(current)
                current = []
    if current:
        rows.append(current)
    boxes = [None] * len(visuals)
    for r, row in enumerate(rows):
        width = (CANVAS_WIDTH - GAP * (len(row) - 1)) // len(row)
        for j, i in enumerate(row):
            boxes[i] = (j * (width + GAP), r * (HEIGHT + GAP), width, HEIGHT)
    return boxes


if __name__ == "__main__":                  # uv run python -m liveinsight.engine.spec: rewrites the published schema
    SCHEMA_PATH.write_text(json_schema())
    print(f"written {SCHEMA_PATH.relative_to(Path.cwd()) if SCHEMA_PATH.is_relative_to(Path.cwd()) else SCHEMA_PATH}",
          file=sys.stderr)
