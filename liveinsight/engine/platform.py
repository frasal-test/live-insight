"""What the engine needs from an analytics platform (docs/ARCHITETTURA.md, §1): two faces of one adapter.

- DataSource: the data. The engine sends the model's queries and the visuals' columns; the platform runs them in
  its own dialect and answers with rows. It also tells the model how to write queries (its dialect).
- WorkbookTarget: the workbook. It reads only the WorkbookSpec JSON and builds the platform artifact.

The engine imports nothing from liveinsight.platforms: it talks to these protocols only.
"""
from pathlib import Path
from typing import Protocol

from liveinsight.engine.spec import ColumnSpec, DatasetInfo, DatasetRef, FilterSpec


class QueryError(ValueError):
    """A query or a visual the model can fix: the message goes back to the model."""


class StructuralError(Exception):
    """The artifact a target built failed the target's own checks: a bug, never the user's or the model's fault."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


class DataSource(Protocol):
    ref: DatasetRef                     # the dataset, as the WorkbookSpec names it
    dataset: DatasetInfo                # its columns: attribute, measure or date
    query_tool: str                     # description of the run_query tool, in the platform's dialect
    query_guide: str                    # the system-prompt section on how to write queries

    def query(self, text: str) -> list[dict]:
        """Runs a query the model wrote (dialect of the platform, {Column name} and {dataset} placeholders).
        Raises QueryError with a message the model can act on."""
        ...

    def visual_rows(self, columns: list[ColumnSpec], filters: list[FilterSpec],
                    defined: dict[str, ColumnSpec]) -> tuple[str, list[dict]]:
        """The rows of a visual, with the same expressions and filter semantics the workbook will have on the
        platform. `columns` are the shown ones, in order; `defined` resolves the filter columns. Returns the query
        text (for people) and the rows, keyed by column id; date columns as periods: '2016', '2016 Q1',
        '2016-11', '2016-11-05'."""
        ...


class WorkbookTarget(Protocol):
    platform: str

    def publish(self, spec_json: str) -> dict:
        """Saves the workbook on the platform; returns {"name": final name, "folder": where}."""
        ...

    def package(self, spec_json: str, path: Path) -> Path:
        """Writes the workbook as a file the user can download and import."""
        ...
