"""Build the compact offline US gazetteer used for geocoding (no API calls at runtime).

Source: US Census Bureau 2024 Gazetteer files (public domain):
  places (cities/towns/CDPs), county subdivisions (townships) and ZCTAs (ZIP codes).

Usage:
    python scripts/build_gazetteer.py            # downloads the three zip files
    python scripts/build_gazetteer.py DIR        # uses already-extracted .txt files in DIR

Output: planner/data/us_gazetteer.csv.gz with columns kind,state,key,name,lat,lon
  kind  = place | zip     (county subdivisions are folded into "place")
  key   = normalised name (see planner/services/textnorm.py) or the 5-digit ZIP
"""
import csv
import gzip
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from planner.services.textnorm import US_STATES, alias_names, clean_census_name, normalize  # noqa: E402

BASE = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/"
FILES = {
    "place": "2024_Gaz_place_national",
    "cousub": "2024_Gaz_cousubs_national",
    "zip": "2024_Gaz_zcta_national",
}
OUT = ROOT / "planner" / "data" / "us_gazetteer.csv.gz"

# Lower number wins when two entries share the same (state, key).
PRIORITY = {"place": 0, "cousub": 1, "alias": 2}


def read_rows(kind: str, local_dir: Path | None):
    stem = FILES[kind]
    if local_dir:
        text = (local_dir / f"{stem}.txt").read_text(encoding="latin-1")
    else:
        print(f"downloading {stem} ...")
        with urllib.request.urlopen(f"{BASE}{stem}.zip", timeout=120) as resp:
            with zipfile.ZipFile(io.BytesIO(resp.read())) as zf:
                text = zf.read(f"{stem}.txt").decode("latin-1")
    lines = text.splitlines()
    header = [h.strip() for h in lines[0].split("\t")]
    for line in lines[1:]:
        yield dict(zip(header, (v.strip() for v in line.split("\t"))))


def main():
    local_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    best: dict[tuple[str, str], tuple] = {}  # (state, key) -> (priority, -aland, name, lat, lon)

    def offer(state, key, priority, aland, name, lat, lon):
        candidate = (priority, -aland, name, lat, lon)
        current = best.get((state, key))
        if current is None or candidate[:2] < current[:2]:
            best[(state, key)] = candidate

    for kind in ("place", "cousub"):
        for row in read_rows(kind, local_dir):
            state = row["USPS"]
            if state not in US_STATES:
                continue  # skip Puerto Rico etc. -- routes must be in the USA states/DC
            lat, lon, aland = float(row["INTPTLAT"]), float(row["INTPTLONG"]), int(row["ALAND"])
            name = clean_census_name(row["NAME"])
            offer(state, normalize(name), PRIORITY[kind], aland, name, lat, lon)
            for alias in alias_names(row["NAME"]):
                offer(state, normalize(alias), PRIORITY["alias"], aland, alias, lat, lon)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["kind", "state", "key", "name", "lat", "lon"])
        for (state, key), (_, _, name, lat, lon) in sorted(best.items()):
            writer.writerow(["place", state, key, name, f"{lat:.6f}", f"{lon:.6f}"])
        zips = 0
        for row in read_rows("zip", local_dir):
            writer.writerow(["zip", "", row["GEOID"], row["GEOID"],
                             f"{float(row['INTPTLAT']):.6f}", f"{float(row['INTPTLONG']):.6f}"])
            zips += 1
    print(f"wrote {len(best)} place keys and {zips} ZIP codes to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
