"""Construit le fond de carte des régions (radar/assets/france-regions.json) à partir d'un GeoJSON.

Source : contours simplifiés des régions de France métropolitaine, projet france-geojson
(https://github.com/gregoiredavid/france-geojson, fichier regions-version-simplifiee.geojson), données IGN Admin Express
sous Licence Ouverte (Etalab). À relancer seulement si les régions changent :

    python -m radar.build_map regions-version-simplifiee.geojson
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "assets" / "france-regions.json"
LAT0 = 46.5          # latitude de référence de la projection (équirectangulaire, suffisante à l'échelle de la France)
SCALE = 50           # pixels par degré de latitude
PAD = 6


def projection(features) -> dict:
    lons = [p[0] for f in features for ring in rings(f) for p in ring]
    lats = [p[1] for f in features for ring in rings(f) for p in ring]
    return {"lon_min": min(lons), "lat_max": max(lats), "cos": math.cos(math.radians(LAT0)), "scale": SCALE, "pad": PAD,
            "width": round((max(lons) - min(lons)) * math.cos(math.radians(LAT0)) * SCALE + 2 * PAD),
            "height": round((max(lats) - min(lats)) * SCALE + 2 * PAD)}


def project(lon: float, lat: float, proj: dict) -> tuple[float, float]:
    return ((lon - proj["lon_min"]) * proj["cos"] * proj["scale"] + proj["pad"],
            (proj["lat_max"] - lat) * proj["scale"] + proj["pad"])


def rings(feature) -> list:
    g = feature["geometry"]
    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    return [poly[0] for poly in polys]           # contour extérieur seulement (pas de trous à cette échelle)


def path(ring, proj) -> str:
    pts, last = [], None
    for lon, lat in ring:
        x, y = (round(v, 1) for v in project(lon, lat, proj))
        if last is None or math.dist(last, (x, y)) >= 0.8:     # points trop proches : invisibles, on les retire
            pts.append((x, y))
            last = (x, y)
    if len(pts) < 3:
        return ""
    return "M" + "L".join(f"{x:g},{y:g}" for x, y in pts) + "Z"


def main(src: str) -> None:
    features = json.loads(Path(src).read_text(encoding="utf-8"))["features"]
    proj = projection(features)
    regions = []
    for f in features:
        rs = rings(f)
        d = "".join(p for r in rs if (p := path(r, proj)))
        big = max(rs, key=len)
        xs, ys = zip(*(project(lon, lat, proj) for lon, lat in big))
        regions.append({"name": f["properties"]["nom"], "d": d,
                        "label": [round((min(xs) + max(xs)) / 2), round((min(ys) + max(ys)) / 2)]})
    OUT.write_text(json.dumps({"source": "IGN Admin Express via france-geojson, Licence Ouverte", "proj": proj,
                               "regions": regions}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(regions)} régions, {OUT.stat().st_size // 1024} Ko -> {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
