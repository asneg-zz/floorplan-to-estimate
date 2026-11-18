"""Контрольная картинка: распознанный план в миллиметрах."""

from __future__ import annotations

from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402

ROOM_COLORS = ["#cfe8ff", "#ffe2b8", "#d8f5d0", "#f3d1f0", "#fff3a8", "#d6d6f5", "#f7c9c9"]


def render_plan(plan: dict[str, Any], path: str, furniture: list[dict[str, Any]] | None = None) -> None:
    fig, ax = plt.subplots(figsize=(13, 10))
    for i, r in enumerate(plan["rooms"]):
        ax.add_patch(MplPolygon(r["polygon"], fc=ROOM_COLORS[i % len(ROOM_COLORS)], ec="none"))
        for h in r.get("holes", []):
            ax.add_patch(MplPolygon(h, fc="white", ec="none"))
        c = r["_geom"].representative_point()
        ax.text(c.x, c.y, f"{r['number']}. {r['name']}\n{r['area']} м² (план {r['area_printed']})\nH={r['height']:.0f}",
                ha="center", va="center", fontsize=8)
    for w in plan["walls"]:
        ax.add_patch(MplPolygon(w["polygon"], fc="#333" if w["kind"] == "несущая" else "#8a6d3b", ec="none"))
        for h in w.get("holes", []):
            ax.add_patch(MplPolygon(h, fc="white", ec="none"))
    for g in plan["glazing"]:
        ax.add_patch(MplPolygon(g["polygon"], fc="#bfe9ff", ec="#39c", lw=0.5))
    for w in plan["windows"]:
        ax.add_patch(MplPolygon(w["polygon"], fc="#39c", ec="none"))
        ax.text(*w["center"], w["id"], color="white", ha="center", va="center", fontsize=7)
    for d in plan["doors"]:
        ax.add_patch(MplPolygon(d["polygon"], fc="#e33", ec="none"))
        sw = d["_swing"].intersection(_half_plane(d))
        for poly in getattr(sw, "geoms", [sw]):
            if poly.geom_type == "Polygon" and not poly.is_empty:
                ax.add_patch(MplPolygon(list(poly.exterior.coords), fc="#e33", alpha=0.12, ec="#e33", lw=0.5))
        ax.text(*d["center"], d["id"], color="white", ha="center", va="center", fontsize=7)
    for f in furniture or []:
        ax.add_patch(MplPolygon(f["polygon"], fc="#ffffff", ec="#205080", lw=1.2, alpha=0.9))
        cx = sum(p[0] for p in f["polygon"]) / len(f["polygon"])
        cy = sum(p[1] for p in f["polygon"]) / len(f["polygon"])
        ax.text(cx, cy, f["name"], ha="center", va="center", fontsize=6, color="#205080")
    ax.autoscale()
    ax.set_aspect("equal")
    ax.grid(True, lw=0.2)
    ax.set_xlabel("мм")
    fig.savefig(path, dpi=80, bbox_inches="tight")
    plt.close(fig)


def _half_plane(d: dict[str, Any]):
    """Полуплоскость со стороны открывания двери."""
    from shapely.geometry import Polygon

    (cx, cy), (ux, uy), (nx, ny) = d["center"], d["axis"], d["swing"]
    big = 5000
    a = (cx - ux * big, cy - uy * big)
    b = (cx + ux * big, cy + uy * big)
    return Polygon([a, b, (b[0] + nx * big, b[1] + ny * big), (a[0] + nx * big, a[1] + ny * big)])
