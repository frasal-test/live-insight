"""The OAC WorkbookTarget: from the WorkbookSpec JSON to the OAC workbook, with li_dva. Deterministic code, no LLM.

build_definition and build_dva read only the WorkbookSpec JSON (spec.load_spec): the contract between the engine
and the platform (docs/ARCHITETTURA.md, §1). The dataset description comes from OAC itself (describe_data), for
the dataset the spec names.
"""
import json
from pathlib import Path

from liveinsight.engine.platform import StructuralError
from liveinsight.engine.spec import (Calculation, DatasetInfo, DateColumn, WorkbookSpec, check_against, column_contents,
                                     layout, load_spec, visual_attributes)
from liveinsight.messages import UiError
from liveinsight.platforms.oac.catalog import TEST_FOLDER, save_workbook, workbook_json
from liveinsight.platforms.oac.checks import definition_problems, dva_problems
from liveinsight.platforms.oac.li_dva import Workbook
from liveinsight.platforms.oac.mcp import OacMcp


class SpecError(ValueError):
    """The spec does not match the dataset: the message lists the errors."""


def compile_workbook(spec: WorkbookSpec, wb: Workbook, dataset: DatasetInfo) -> Workbook:
    """Fills `wb` (skeleton + visual templates) as the spec says, on the given dataset.

    Returns the same `wb`: then `wb.definition()` for the catalog, or `rename` + `save` for the .dva.
    """
    if spec.dataset.platform != "oac" or spec.dataset.id != dataset.xsa:
        raise SpecError(f"the spec reads dataset {spec.dataset.platform}:{spec.dataset.id}, "
                        f"the workbook is being built on oac:{dataset.xsa}")
    errors = check_against(spec, dataset)
    if errors:
        raise SpecError("; ".join(errors))
    wb.set_source(dataset.xsa, dataset.table)
    wb.reset()
    for c in spec.columns:
        if isinstance(c, Calculation):
            wb.calculation(c.id, c.caption, c.expression, c.description)
        elif isinstance(c, DateColumn):
            wb.date_column(c.id, c.source, c.grain)
        else:
            wb.column(c.id, c.source)
    content = column_contents(spec.columns, dataset)
    for canvas in spec.canvases:
        cid = wb.canvas(canvas.title)
        for v, box in zip(canvas.visuals, layout(canvas.visuals)):
            filters = [{"column": f.column, "op": f.op, "values": f.values,
                        "by": visual_attributes(v, content) if content.get(f.column) == "measure" else []}
                       for f in v.filters]
            wb.visual(cid, v.kind, v.title, v.roles, box, sort=v.sort, filters=filters)
    return wb


def read_spec(spec_json: str | bytes) -> WorkbookSpec:
    """The spec from its JSON; an invalid or unsupported one is a SpecError (TypeError if it is not JSON text)."""
    try:
        return load_spec(spec_json)
    except ValueError as e:                              # pydantic's ValidationError included
        raise SpecError(str(e)) from e


def build_definition(spec_json: str | bytes, skeleton, templates, dataset: DatasetInfo) -> dict:
    """The definition (_projectdefn) for save_catalog_content: the same as in the .dva, without the package."""
    wb = Workbook(skeleton, extra_templates=templates, skeleton_visuals=False)
    return compile_workbook(read_spec(spec_json), wb, dataset).definition()


def build_dva(spec_json: str | bytes, skeleton, templates, dataset: DatasetInfo, path):
    """The downloadable .dva: skeleton without data + visual templates, only the workbook in the package."""
    spec = read_spec(spec_json)
    wb = Workbook(skeleton, extra_templates=templates, skeleton_visuals=False)
    compile_workbook(spec, wb, dataset)
    wb.rename(spec.name)
    wb.save(path)
    return wb


BUNDLED = Path(__file__).parent / "templates"
BUNDLED_TEMPLATES = ("examples.json", "example2.json", "varianti.json")


def bundled(name: str) -> dict:
    """A workbook definition shipped with Live Insight (templates/, on a placeholder dataset: the code rebinds it)."""
    return json.loads((BUNDLED / name).read_text())


class OacTarget:
    """The WorkbookTarget (liveinsight.engine.platform) for OAC: the catalog ("Live Insight - test" only) and .dva.

    Visual templates: the definitions bundled in templates/ (examples, example2, varianti), or workbooks read from
    the user's catalog when `template_names` is given (TEMPLATE_WORKBOOKS). The container of a catalog workbook is
    the bundled base.json; a downloadable .dva also needs a skeleton .dva exported from the user's OAC (`skeleton`,
    optional: without it package() refuses with a message).

    describe(xsa) gives the dataset a spec names (OacPlatform shares its cache with the sources, so building a
    workbook for a dataset already in use costs no MCP call).
    """
    platform = "oac"

    def __init__(self, mcp: OacMcp, describe, skeleton: Path | None = None, template_names=()):
        self.mcp, self.describe = mcp, describe
        self.skeleton = Path(skeleton) if skeleton else None
        self.template_names = list(template_names)
        self._templates = None

    @property
    def can_package(self) -> bool:
        return bool(self.skeleton and self.skeleton.exists())

    def templates(self) -> list:
        if self._templates is None:
            self._templates = [workbook_json(self.mcp, n) for n in self.template_names] if self.template_names \
                else [bundled(n) for n in BUNDLED_TEMPLATES]
        return self._templates

    def publish(self, spec_json: str) -> dict:
        spec = read_spec(spec_json)
        definition = build_definition(spec_json, bundled("base.json"), self.templates(), self.describe(spec.dataset.id))
        problems = definition_problems(definition)
        if problems:
            raise StructuralError(problems)
        return {"name": save_workbook(self.mcp, spec.name, definition), "folder": TEST_FOLDER}

    def package(self, spec_json: str, path: Path) -> Path:
        if not self.can_package:
            raise UiError(f"no skeleton .dva at {self.skeleton}: export an empty workbook from OAC (see README)",
                          "errors.dvaSkeletonMissing")
        spec = read_spec(spec_json)
        build_dva(spec_json, str(self.skeleton), self.templates(), self.describe(spec.dataset.id), path)
        problems = dva_problems(path, spec.name, forbidden=[Workbook(str(self.skeleton)).name])
        if problems:
            raise StructuralError(problems)
        return path
