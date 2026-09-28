"""The OAC adapter as one object per user: both faces (docs/ARCHITETTURA.md, §1) on the user's MCP connection."""
from pathlib import Path

from liveinsight.engine.spec import DatasetInfo
from liveinsight.platforms.oac.catalog import describe, list_datasets
from liveinsight.platforms.oac.mcp import OacMcp
from liveinsight.platforms.oac.source import OacSource
from liveinsight.platforms.oac.target import OacTarget


class OacPlatform:
    name = "oac"

    def __init__(self, mcp: OacMcp, skeleton: Path | None = None, template_names=()):
        self.mcp, self.skeleton, self.template_names = mcp, skeleton, list(template_names)
        self._described: dict[str, DatasetInfo] = {}
        self._target: OacTarget | None = None

    def datasets(self, search: str = "*") -> list[dict]:
        """Datasets the user can see: name, xsaExpr (the identifier, never the name), folder."""
        return list_datasets(self.mcp, search)

    def describe(self, xsa: str) -> DatasetInfo:
        if xsa not in self._described:
            self._described[xsa] = describe(self.mcp, xsa)
        return self._described[xsa]

    def source(self, xsa: str, name: str) -> OacSource:
        """The DataSource on one dataset."""
        return OacSource(self.mcp, self.describe(xsa), name)

    def target(self) -> OacTarget:
        """The WorkbookTarget: bundled templates unless template_names names catalog workbooks; the skeleton .dva
        (optional) enables the downloadable .dva."""
        if self._target is None:
            self._target = OacTarget(self.mcp, self.describe, self.skeleton, self.template_names)
        return self._target
