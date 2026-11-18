"""Анализ сырых слоёв PDF → семантический план квартиры.

Шаги:
  1. стены — объединение заливок (несущие и перегородки отдельно);
  2. остекление — белые прямоугольники: полосы окон в толще наружной стены
     и тонкое ограждение балкона;
  3. двери — по дугам открывания: петля, ширина, сторона, проём в стене;
  4. окна — створки внутри оконных полос;
  5. помещения — свободное пространство внутри замкнутого контура,
     марки «номер / площадь» привязываются по попаданию внутрь;
  6. самопроверка — вычисленная площадь против напечатанной на чертеже.

Чего на чертеже нет (имена помещений, граница открытой зоны без стены,
высоты проёмов), берётся из файла поправок corrections.json и помечается
в плане как ручной ввод.
"""

from __future__ import annotations

import math
import re
from typing import Any

from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import split, unary_union

from .read_pdf import FILL_BEARING, FILL_PARTITION, FILL_WHITE, RawPlan, walls_union

# Допущения по умолчанию (на плане этих величин нет). Переопределяются поправками.
DEFAULTS: dict[str, float] = {
    "door_height": 2100.0,
    "entrance_door_height": 2100.0,
    "window_sill": 800.0,
    "window_height": 1500.0,
    "balcony_door_height": 2200.0,
    "ceiling_height": 2700.0,  # если в помещении нет марки H
}

TOLERANCE_PCT = 2.0  # допустимое расхождение площади с напечатанной на чертеже


def _rect_dims(poly: Polygon) -> tuple[float, float, bool] | None:
    """(короткая сторона, длинная сторона, горизонтален ли) для прямоугольника по осям."""
    x0, y0, x1, y1 = poly.bounds
    w, h = x1 - x0, y1 - y0
    if poly.area < 0.97 * w * h:
        return None
    return (min(w, h), max(w, h), w >= h)


def _polys(geom: BaseGeometry) -> list[Polygon]:
    if geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]  # type: ignore[list-item]
    return [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]


def _coords(poly: Polygon) -> list[list[float]]:
    return [[round(x, 1), round(y, 1)] for x, y in poly.exterior.coords[:-1]]


# --------------------------------------------------------------- остекление


def find_glazing(raw: RawPlan, walls: BaseGeometry):
    """Возвращает (оконные полосы, тонкое ограждение балкона, белые прямоугольники)."""
    rects = []
    for p in raw.fills.get(FILL_WHITE, []):
        dims = _rect_dims(p)
        if dims:
            rects.append((p, *dims))

    bands: list[Polygon] = []
    thin: list[Polygon] = []
    for p, short, long, horiz in rects:
        if 150 <= short <= 500 and long >= 400:
            # Полоса окна продолжает стену: за обоими торцами — стена той же толщины.
            x0, y0, x1, y1 = p.bounds
            if horiz:
                ends = [box(x0 - 50, y0, x0, y1), box(x1, y0, x1 + 50, y1)]
            else:
                ends = [box(x0, y0 - 50, x1, y0), box(x0, y1, x1, y1 + 50)]
            if all(e.intersection(walls).area > 0.9 * e.area for e in ends):
                bands.append(p)
        elif 30 <= short <= 80 and long >= 1500:
            thin.append(p)
    # Одинаковые прямоугольники в PDF часто продублированы.
    bands = _polys(unary_union(bands))
    return bands, unary_union(thin), rects


# -------------------------------------------------------------------- двери


def _arc_center(pts) -> tuple[float, float] | None:
    """Центр окружности: пересечение нормалей к касательным в концах кривой."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts
    t0 = (x1 - x0, y1 - y0)
    t3 = (x3 - x2, y3 - y2)
    # (C - P0)·t0 = 0, (C - P3)·t3 = 0
    det = t0[0] * t3[1] - t0[1] * t3[0]
    if abs(det) < 1e-6:
        return None
    b0 = x0 * t0[0] + y0 * t0[1]
    b3 = x3 * t3[0] + y3 * t3[1]
    return ((b0 * t3[1] - t0[1] * b3) / det, (t0[0] * b3 - t3[0] * b0) / det)


def _wall_span(solid: BaseGeometry, q: Point, n: tuple[float, float]):
    """Отрезок стены поперёк проёма возле точки q: (t0, t1) вдоль нормали n."""
    probe = LineString([(q.x - n[0] * 700, q.y - n[1] * 700), (q.x + n[0] * 700, q.y + n[1] * 700)])
    hit = probe.intersection(solid)
    lines = [hit] if hit.geom_type == "LineString" else list(getattr(hit, "geoms", []))
    best = None
    for ln in lines:
        if ln.geom_type != "LineString" or not 50 <= ln.length <= 600:
            continue
        d = ln.distance(q)
        if d < 80 and (best is None or d < best[0]):
            ts = [(x - q.x) * n[0] + (y - q.y) * n[1] for x, y in ln.coords]
            best = (d, min(ts), max(ts))
    return (best[1], best[2]) if best else None


def find_doors(raw: RawPlan, walls: BaseGeometry, bands: list[Polygon]) -> list[dict[str, Any]]:
    solid = unary_union([walls, *bands])
    thick = [s for s in raw.segments if s.width > 0.6]
    leaves = []
    for arc in raw.arcs:
        p0, p3 = arc.pts[0], arc.pts[3]
        if math.dist(p0, p3) < 300:
            continue  # кружки марок помещений и мелочь
        c = _arc_center(arc.pts)
        if c is None:
            continue
        r = math.dist(c, p0)
        if not 500 <= r <= 1300 or abs(r - math.dist(c, p3)) > 20:
            continue

        # Открытое положение — тот конец дуги, к которому от петли идёт полотно.
        def has_leaf(end) -> bool:
            return any(
                min(math.dist(s.a, c) + math.dist(s.b, end), math.dist(s.b, c) + math.dist(s.a, end)) < 60
                for s in thick
            )

        if has_leaf(p0) and not has_leaf(p3):
            open_end, closed_end = p0, p3
        elif has_leaf(p3) and not has_leaf(p0):
            open_end, closed_end = p3, p0
        else:  # запасное правило: закрытое положение лежит вдоль оси
            def skew(e):
                return min(abs(e[0] - c[0]), abs(e[1] - c[1]))

            closed_end, open_end = sorted((p0, p3), key=skew)
        leaves.append({"hinge": c, "closed": closed_end, "open": open_end, "leaf": r})

    # Две створки с общей точкой притвора — одна двустворчатая дверь.
    groups: list[list[dict]] = []
    for lf in leaves:
        for g in groups:
            if math.dist(g[0]["closed"], lf["closed"]) < 60:
                g.append(lf)
                break
        else:
            groups.append([lf])

    doors = []
    for g in groups:
        a = g[0]["hinge"]
        b = g[1]["hinge"] if len(g) > 1 else g[0]["closed"]
        length = math.dist(a, b)
        u = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
        n = (-u[1], u[0])
        # Нормаль направляем в сторону открывания.
        op = g[0]["open"]
        if (op[0] - a[0]) * n[0] + (op[1] - a[1]) * n[1] < 0:
            n = (-n[0], -n[1])
        span = None
        for q in (Point(a[0] - u[0] * 120, a[1] - u[1] * 120), Point(b[0] + u[0] * 120, b[1] + u[1] * 120)):
            span = _wall_span(solid, q, n)
            if span:
                break
        if span is None:
            continue
        t0, t1 = span

        def strip(ext: float) -> Polygon:
            p, q2 = (a[0] - u[0] * ext, a[1] - u[1] * ext), (b[0] + u[0] * ext, b[1] + u[1] * ext)
            return Polygon(
                [
                    (p[0] + n[0] * t0, p[1] + n[1] * t0),
                    (q2[0] + n[0] * t0, q2[1] + n[1] * t0),
                    (q2[0] + n[0] * t1, q2[1] + n[1] * t1),
                    (p[0] + n[0] * t1, p[1] + n[1] * t1),
                ]
            )

        # Проём — разрыв стены под полотном. В оконной полосе стены нет вовсе,
        # там проём равен полотну с коробкой.
        # Бывает и третий случай: заливка стены проходит сквозь проём, а дверь
        # нарисована поверх белой «маской» — тогда разрыва нет, берём полотно.
        gap_parts = _polys(strip(150).difference(walls))
        gap = max(gap_parts, key=lambda p: p.area) if gap_parts else strip(30)
        thickness = t1 - t0
        width = gap.area / thickness
        if width > length + 200 or width < 0.8 * length:
            gap = strip(30)
            width = gap.area / thickness
        cx, cy = gap.centroid.x, gap.centroid.y
        doors.append(
            {
                "kind": "double" if len(g) > 1 else "single",
                "width": round(width),
                "leaf": [round(lf["leaf"]) for lf in g],
                "wall_thickness": round(thickness),
                "center": [round(cx, 1), round(cy, 1)],
                "axis": [round(u[0], 3), round(u[1], 3)],
                "swing": [round(n[0], 3), round(n[1], 3)],
                "hinges": [[round(lf["hinge"][0], 1), round(lf["hinge"][1], 1)] for lf in g],
                "polygon": _coords(gap),
                "_geom": gap,
                # Зона открывания: мебель в неё ставить нельзя.
                "_swing": unary_union([Point(lf["hinge"]).buffer(lf["leaf"]) for lf in g]),
            }
        )
    return doors


# --------------------------------------------------------------------- окна


def find_windows(rects, bands: list[Polygon], doors) -> list[dict[str, Any]]:
    door_zone = unary_union([d["_geom"].buffer(20) for d in doors]) if doors else Polygon()
    windows: list[dict[str, Any]] = []
    for band in bands:
        bshort = min(band.bounds[2] - band.bounds[0], band.bounds[3] - band.bounds[1])
        sashes = []
        for p, short, long, horiz in rects:
            # Створка: чуть уже толщины стены, целиком внутри полосы.
            if long < 400 or not 0.6 * bshort <= short < bshort - 5:
                continue
            if p.intersection(band).area < 0.95 * p.area:
                continue
            if p.intersection(door_zone).area > 0.3 * p.area:
                continue
            sashes.append((p, long, horiz))
        # Убираем дубли и вложенные прямоугольники: оставляем самые крупные.
        sashes.sort(key=lambda s: -s[0].area)
        kept: list[tuple[Polygon, float, bool]] = []
        for s in sashes:
            if all(s[0].intersection(k[0]).area < 0.5 * s[0].area for k in kept):
                kept.append(s)
        for p, long, horiz in kept:
            windows.append(
                {
                    "width": round(long),
                    "wall_thickness": round(bshort),
                    "center": [round(p.centroid.x, 1), round(p.centroid.y, 1)],
                    "axis": [1.0, 0.0] if horiz else [0.0, 1.0],
                    "polygon": _coords(box(*p.bounds)),
                    "_geom": p,
                }
            )
    windows.sort(key=lambda w: (w["center"][0], w["center"][1]))
    return windows


# ---------------------------------------------------------------- помещения


def _room_marks(raw: RawPlan):
    """Марка помещения: номер и напечатанная площадь над ним."""
    nums = [l for l in raw.labels if re.fullmatch(r"\d{1,2}", l.text)]
    areas = [l for l in raw.labels if re.fullmatch(r"\d{1,3},\d{2}", l.text)]
    marks = []
    for n in nums:
        near = [a for a in areas if abs(a.pos[0] - n.pos[0]) < 150 and abs(a.pos[1] - n.pos[1]) < 500]
        if near:
            a = min(near, key=lambda a: math.dist(a.pos, n.pos))
            marks.append({"number": int(n.text), "pos": n.pos, "area_printed": float(a.text.replace(",", "."))})
    return marks


def find_rooms(raw: RawPlan, solid: BaseGeometry, corrections: dict[str, Any]):
    x0, y0, x1, y1 = solid.bounds
    hull = box(x0 - 500, y0 - 500, x1 + 500, y1 + 500)
    free = hull.difference(solid)
    cells = [p for p in _polys(free) if not p.touches(hull.boundary) and not p.intersects(hull.boundary)]

    # Ручные границы зон там, где стены нет (например, прихожая в открытой планировке).
    for sp in corrections.get("splits", []):
        line = LineString(sp["line"])
        out = []
        for c in cells:
            if c.intersects(line) and c.intersection(line).length > 100:
                out += _polys(split(c, line))
            else:
                out.append(c)
        cells = out

    marks = [m for m in _room_marks(raw) if any(c.contains(Point(m["pos"])) for c in cells)]
    heights = []
    for l in raw.labels:
        m = re.fullmatch(r"H(\d{4})", l.text)
        if m:
            heights.append((float(m.group(1)), l.pos))

    names = {int(k): v for k, v in corrections.get("room_names", {}).items()}
    rooms, voids = [], []
    for c in cells:
        inside = [m for m in marks if c.contains(Point(m["pos"]))]
        if not inside:
            voids.append(c)
            continue
        hs = [(h, pos) for h, pos in heights if c.contains(Point(pos))]
        for m in inside[:1]:
            main = min(hs, key=lambda hp: math.dist(hp[1], m["pos"]))[0] if hs else None
            rooms.append(
                {
                    "number": m["number"],
                    "name": names.get(m["number"], f"Помещение {m['number']}"),
                    "area_printed": m["area_printed"],
                    "height": main,
                    "height_source": "чертёж" if main else None,
                    "ceiling_marks": sorted({h for h, _ in hs}),
                    "polygon": _coords(c),
                    "holes": [[[round(x, 1), round(y, 1)] for x, y in r.coords[:-1]] for r in c.interiors],
                    "marks_inside": len(inside),
                    "_geom": c,
                }
            )
    rooms.sort(key=lambda r: r["number"])
    return rooms, voids


# ------------------------------------------------------------------- сборка


def analyze(raw: RawPlan, corrections: dict[str, Any] | None = None) -> dict[str, Any]:
    corrections = corrections or {}
    defaults = {**DEFAULTS, **corrections.get("defaults", {})}

    bearing = walls_union(raw, FILL_BEARING)
    partition = walls_union(raw, FILL_PARTITION)
    walls = unary_union([bearing, partition])

    bands, thin, rects = find_glazing(raw, walls)
    doors = find_doors(raw, walls, bands)
    windows = find_windows(rects, bands, doors)
    # Проёмы вырезаем из стен: там, где заливка шла сквозь дверь, стены нет.
    if doors:
        cut = unary_union([d["_geom"] for d in doors])
        bearing, partition = bearing.difference(cut), partition.difference(cut)
        walls = unary_union([bearing, partition])

    solid = unary_union([walls, thin, *bands, *[d["_geom"] for d in doors]])
    rooms, voids = find_rooms(raw, solid, corrections)

    # Помещение без своей марки высоты (зона, отрезанная ручной границей) берёт
    # высоту соседа по общей границе; если соседа нет — значение по умолчанию.
    for r in rooms:
        if r["height"] is None:
            neigh = [
                o for o in rooms if o is not r and o["height"] and o["_geom"].buffer(5).intersection(r["_geom"]).area > 50
            ]
            if neigh:
                r["height"], r["height_source"] = neigh[0]["height"], f"как у помещения {neigh[0]['number']}"
            else:
                r["height"], r["height_source"] = defaults["ceiling_height"], "допущение"

    # Какие помещения соединяет проём: с чем граничит его пятно.
    def touching(geom) -> list[int]:
        zone = geom.buffer(40)
        return sorted(r["number"] for r in rooms if r["_geom"].intersection(zone).area > 100)

    for i, d in enumerate(doors, 1):
        d["rooms"] = touching(d["_geom"])
        d["id"] = f"D{i}"
        in_band = any(d["_geom"].intersection(b).area > 0.5 * d["_geom"].area for b in bands)
        d["type"] = "балконная" if in_band else ("входная" if len(d["rooms"]) == 1 else "межкомнатная")
        d["height"] = defaults[
            {"балконная": "balcony_door_height", "входная": "entrance_door_height"}.get(d["type"], "door_height")
        ]
    for i, w in enumerate(windows, 1):
        w["rooms"] = touching(w["_geom"])
        w["id"] = f"W{i}"
        w["sill"] = defaults["window_sill"]
        w["height"] = defaults["window_height"]

    checks = []
    for r in rooms:
        area = r["_geom"].area / 1e6
        r["area"] = round(area, 2)
        r["perimeter"] = round(r["_geom"].length / 1000, 2)
        dev = (area - r["area_printed"]) / r["area_printed"] * 100
        checks.append(
            {
                "number": r["number"],
                "name": r["name"],
                "area": r["area"],
                "printed": r["area_printed"],
                "deviation_pct": round(dev, 2),
                "ok": abs(dev) <= TOLERANCE_PCT,
                "note": corrections.get("check_notes", {}).get(str(r["number"]), ""),
            }
        )

    def wall_parts(geom, kind):
        return [{"kind": kind, "polygon": _coords(p), "holes": [[[round(x, 1), round(y, 1)] for x, y in r.coords[:-1]] for r in p.interiors]} for p in _polys(geom)]

    plan = {
        "units": "mm",
        "source": {"scale": f"1:{raw.scale:g}", "scale_votes": f"{raw.scale_votes} из {raw.scale_total}"},
        "defaults": defaults,
        "walls": wall_parts(bearing, "несущая") + wall_parts(partition, "перегородка"),
        "glazing": [{"kind": "ограждение балкона", "polygon": _coords(p)} for p in _polys(thin)]
        + [{"kind": "оконная полоса", "polygon": _coords(b)} for b in bands],
        "doors": doors,
        "windows": windows,
        "rooms": rooms,
        "voids": [{"area": round(v.area / 1e6, 2), "polygon": _coords(v)} for v in voids if v.area > 1e4],
        "checks": checks,
        "manual": {
            "room_names": bool(corrections.get("room_names")),
            "splits": len(corrections.get("splits", [])),
            "defaults": sorted(corrections.get("defaults", {})),
        },
    }
    plan["_geom"] = {"walls": walls, "bearing": bearing, "partition": partition, "thin": thin, "bands": bands, "solid": solid}
    return plan


def strip_private(obj: Any) -> Any:
    """Убирает служебные поля с геометрией shapely перед записью в JSON."""
    if isinstance(obj, dict):
        return {k: strip_private(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [strip_private(v) for v in obj]
    return obj
