"""Объёмы работ по модели: одна строка = помещение, показатель, единица, количество."""

from __future__ import annotations

import math
from typing import Any

WET_ROOMS = ("Санузел", "Ванная", "Балкон")

Row = tuple[str, str, str, float, str]  # помещение, показатель, ед., количество, примечание


def assign_coverings(plan: dict[str, Any]) -> None:
    for r in plan["rooms"]:
        r["covering"] = "плитка" if r["name"] in WET_ROOMS else "ламинат"


def _strip_length(poly) -> tuple[float, float]:
    """Длина и толщина вытянутой фигуры по площади и периметру: A = L·t, P = 2(L+t)."""
    a, p = poly.area, poly.length
    disc = max(p * p - 16 * a, 0.0)
    t = (p - math.sqrt(disc)) / 4
    return (a / t if t > 0 else 0.0, t)


def compute(plan: dict[str, Any], furniture: list[dict[str, Any]], lights: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    lights = lights or []
    rows: list[Row] = []
    per_room: list[dict[str, Any]] = []
    for r in plan["rooms"]:
        n, name, geom = r["number"], r["name"], r["_geom"]
        h = r["height"] / 1000
        perimeter = geom.exterior.length / 1000
        # Граница с соседней зоной без стены: ни стены, ни плинтуса там нет.
        open_len = sum(
            geom.boundary.intersection(o["_geom"].boundary.buffer(5)).length for o in plan["rooms"] if o is not r
        ) / 1000
        open_len = open_len if open_len > 0.3 else 0.0
        wall_len = perimeter - open_len
        doors = [d for d in plan["doors"] if n in d["rooms"]]
        windows = [w for w in plan["windows"] if n in w["rooms"]]
        door_area = sum(d["width"] * min(d["height"], r["height"]) for d in doors) / 1e6
        win_area = sum(w["width"] * w["height"] for w in windows) / 1e6
        gross = wall_len * h
        net = gross - door_area - win_area
        plinth = wall_len - sum(d["width"] for d in doors) / 1000
        area = geom.area / 1e6
        q = {
            "number": n, "name": name, "floor": area, "ceiling": area, "perimeter": perimeter, "open": open_len,
            "height": h, "walls_gross": gross, "openings": door_area + win_area, "walls_net": net, "plinth": plinth,
            "volume": area * h, "doors": len(doors), "windows": len(windows), "covering": r["covering"],
        }
        per_room.append(q)
        hs = f"высота {r['height']:.0f} мм ({r['height_source']})"
        rows += [
            (name, "Площадь пола", "м²", area, f"покрытие: {r['covering']}"),
            (name, "Площадь потолка", "м²", area, "по площади пола, без учёта уровней ГКЛ"),
            (name, "Периметр помещения", "м", perimeter, f"в т. ч. открытая граница зоны {open_len:.2f} м" if open_len else ""),
            (name, "Высота помещения", "м", h, hs),
            (name, "Площадь стен (брутто)", "м²", gross, "длина стен × высота"),
            (name, "Площадь проёмов", "м²", door_area + win_area, f"двери {door_area:.2f} + окна {win_area:.2f}"),
            (name, "Площадь стен за вычетом проёмов", "м²", net, ""),
            (name, "Длина плинтуса", "м", plinth, "длина стен минус ширина дверей"),
            (name, "Объём помещения", "м³", area * h, ""),
            (name, "Количество дверей", "шт", len(doors), ", ".join(d["id"] for d in doors)),
            (name, "Количество окон", "шт", len(windows), ", ".join(w["id"] for w in windows)),
            (name, "Количество светильников", "шт", sum(1 for lt in lights if lt["room"] == n),
             ", ".join(f"{lt['id']} {lt['name'].lower()}" for lt in lights if lt["room"] == n)),
        ]

    top = max(r["height"] for r in plan["rooms"]) / 1000
    g = plan["_geom"]
    total: list[Row] = []
    inner = [q for q in per_room if q["name"] != "Балкон"]
    total.append(("Квартира", "Площадь пола без балкона", "м²", sum(q["floor"] for q in inner), ""))
    total.append(("Квартира", "Площадь пола с балконом", "м²", sum(q["floor"] for q in per_room), ""))
    for cov in sorted({q["covering"] for q in per_room}):
        total.append(("Квартира", f"Напольное покрытие: {cov}", "м²", sum(q["floor"] for q in per_room if q["covering"] == cov),
                      ", ".join(q["name"] for q in per_room if q["covering"] == cov)))
    total.append(("Квартира", "Площадь потолков", "м²", sum(q["ceiling"] for q in per_room), ""))
    total.append(("Квартира", "Площадь стен за вычетом проёмов", "м²", sum(q["walls_net"] for q in per_room), "сумма по помещениям"))
    total.append(("Квартира", "Длина плинтуса", "м", sum(q["plinth"] for q in per_room), ""))
    total.append(("Квартира", "Количество дверей", "шт", len(plan["doors"]), "каждая дверь один раз"))
    total.append(("Квартира", "Количество окон", "шт", len(plan["windows"]), "каждое окно один раз"))
    parts = [p for p in getattr(g["partition"], "geoms", [g["partition"]]) if p.geom_type == "Polygon"]
    plen = sum(_strip_length(p)[0] for p in parts) / 1000
    total.append(("Квартира", "Перегородки: длина", "м", plen, "оценка по площади и периметру в плане, без дверных проёмов"))
    total.append(("Квартира", "Перегородки: площадь (одна сторона)", "м²", plen * top, f"высота до перекрытия {top:.3f} м"))
    total.append(("Квартира", "Перегородки: объём", "м³", g["partition"].area / 1e6 * top, ""))
    total.append(("Квартира", "Несущие стены: площадь в плане", "м²", g["bearing"].area / 1e6, ""))
    total.append(("Квартира", "Количество светильников", "шт", len(lights), "один на ~18 м² помещения"))
    total.append(("Квартира", "Предметов мебели", "шт", len(furniture), ""))
    return {"rows": rows + total, "per_room": per_room}
