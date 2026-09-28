"""Approximate coordinates of places, for the map preview.

Giving an idea of the map OAC will draw is enough (a design choice, 24/9/2026): for a name, the most populous
city with that name. Source and reduction of the data in liveinsight/engine/data/README.md.
"""
import gzip
from functools import cache
from pathlib import Path

CITIES = Path(__file__).parent / "data" / "cities.tsv.gz"


@cache
def _cities() -> dict[str, tuple[float, float]]:
    table = {}
    with gzip.open(CITIES, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.startswith("#"):
                name, lat, lon = line.rstrip("\n").split("\t")
                table[name] = (float(lat), float(lon))
    return table


def locate(place) -> tuple[float, float] | None:
    """(latitude, longitude) of a place by name, case-insensitive; None if unknown."""
    if not isinstance(place, str) or not place.strip():
        return None
    return _cities().get(place.strip().lower())
