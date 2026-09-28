import json
from pathlib import Path

import pytest

from liveinsight.engine.spec import DatasetInfo
from liveinsight.platforms.oac.target import bundled

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
# An empty .dva exported from the OAC editor (not in git: it holds the instance's host and account). Only the tests of
# the downloadable .dva need it; they are skipped without it.
SKELETON = ROOT / "dva-lab/templates/M2 - senza dati.dva"
XSA = "XSA('00000000-0000-0000-0000-000000000000'.'Retail Orders#-#template#-#')"      # the bundled templates' dataset
DATASET_REF = {"platform": "oac", "id": XSA, "name": "Retail Orders"}                   # the dataset of a WorkbookSpec

needs_skeleton = pytest.mark.skipif(not SKELETON.exists(), reason="no local skeleton .dva (dva-lab/templates)")


@pytest.fixture(scope="session")
def dataset():
    return DatasetInfo.from_describe(XSA, json.loads((FIXTURES / "retail_orders_describe.json").read_text()))


@pytest.fixture(scope="session")
def templates():
    """The bundled visual templates made by hand in OAC ("Examples", "Example 2")."""
    return [bundled("examples.json"), bundled("example2.json")]
