"""Светильники: по одному на каждые ~18 м², в самой свободной точке своей части помещения.

В жилых комнатах — люстра на подвесе, в остальных — потолочный плафон.
"""

from __future__ import annotations

import math
from typing import Any

from shapely.geometry import box
from shapely.ops import polylabel

AREA_PER_LIGHT = 18.0  # м² на один светильник
CHANDELIER_ROOMS = ("Кухня-гостиная", "Спальня")


def _largest(geom):
    parts = [geom] if geom.geom_type == "Polygon" else [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]
    return max(parts, key=lambda p: p.area) if parts else None


def place_lights(plan: dict[str, Any]) -> list[dict[str, Any]]:
    lights: list[dict[str, Any]] = []
    for r in plan["rooms"]:
        geom = r["_geom"]
        n = max(1, round(geom.area / 1e6 / AREA_PER_LIGHT))
        x0, y0, x1, y1 = geom.bounds
        horizontal = (x1 - x0) >= (y1 - y0)
        chandelier = r["name"] in CHANDELIER_ROOMS
        for k in range(n):
            # Помещение режется на n полос вдоль длинной стороны, светильник — в центре своей полосы.
            if horizontal:
                strip = box(x0 + (x1 - x0) * k / n, y0, x0 + (x1 - x0) * (k + 1) / n, y1)
            else:
                strip = box(x0, y0 + (y1 - y0) * k / n, x1, y0 + (y1 - y0) * (k + 1) / n)
            part = _largest(geom.intersection(strip))
            if part is None or part.area < 2e5:
                continue
            pt = polylabel(part, tolerance=50)
            lights.append(
                {
                    "name": "Люстра" if chandelier else "Светильник потолочный",
                    "kind": "chandelier" if chandelier else "plafond",
                    "room": r["number"],
                    "room_name": r["name"],
                    "x": round(pt.x),
                    "y": round(pt.y),
                    "z": r["height"],  # отметка потолка
                    "power": 180.0 if chandelier else 70.0,  # мощность источника в Blender, Вт
                }
            )
    for i, lt in enumerate(lights, 1):
        lt["id"] = f"L{i}"
    return lights


def circle(x: float, y: float, r: float, n: int = 20) -> list[tuple[float, float]]:
    return [(x + r * math.cos(2 * math.pi * i / n), y + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
