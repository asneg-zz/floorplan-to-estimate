"""Укрупнённая смета: объёмы из модели × примерные цены за единицу.

Цены лежат в отдельном JSON и редактируются без правки кода. В Excel сумма
по строке и итог записаны формулами, поэтому цену можно поменять прямо в
таблице — итог пересчитается.
"""

from __future__ import annotations

from typing import Any

WET = ("Санузел", "Ванная")

# раздел, наименование, единица, ключ цены, основание
EstRow = tuple[str, str, str, float, float, str]


def build_estimate(plan: dict[str, Any], q: dict[str, Any], prices: dict[str, float]) -> list[EstRow]:
    rooms = q["per_room"]
    inner = [r for r in rooms if r["name"] != "Балкон"]

    def total(key: str, sel) -> float:
        return round(sum(r[key] for r in rooms if sel(r)), 2)

    lam = total("floor", lambda r: r["covering"] == "ламинат")
    tile = total("floor", lambda r: r["covering"] == "плитка")
    plinth = total("plinth", lambda r: r["covering"] == "ламинат")
    dry_walls = total("walls_net", lambda r: r["name"] not in WET and r["name"] != "Балкон")
    wet_walls = total("walls_net", lambda r: r["name"] in WET)
    ceilings = round(sum(r["ceiling"] for r in inner), 2)
    interior = sum(1 for d in plan["doors"] if d["type"] == "межкомнатная")
    entrance = sum(1 for d in plan["doors"] if d["type"] == "входная")
    glazing = round(
        sum(w["width"] * w["height"] for w in plan["windows"]) / 1e6
        + sum(d["width"] * d["height"] for d in plan["doors"] if d["type"] == "балконная") / 1e6,
        2,
    )
    dry = ", ".join(r["name"] for r in inner if r["name"] not in WET)

    spec = [
        ("Полы", "Укладка ламината с подложкой", "м²", lam, "floor_laminate_work", "площадь пола: " + dry),
        ("Полы", "Ламинат и подложка (материал)", "м²", lam, "floor_laminate_material", "то же"),
        ("Полы", "Укладка напольной плитки", "м²", tile, "floor_tile_work", "площадь пола: санузел, ванная, балкон"),
        ("Полы", "Плитка напольная, клей, затирка (материал)", "м²", tile, "floor_tile_material", "то же"),
        ("Полы", "Монтаж плинтуса", "м", plinth, "plinth_work", "длина стен минус двери, помещения с ламинатом"),
        ("Полы", "Плинтус с крепежом (материал)", "м", plinth, "plinth_material", "то же"),
        ("Стены", "Штукатурка стен", "м²", dry_walls, "wall_plaster_work", "стены за вычетом проёмов: " + dry),
        ("Стены", "Шпаклёвка и окраска стен", "м²", dry_walls, "wall_paint_work", "то же"),
        ("Стены", "Шпаклёвка, грунт, краска (материал)", "м²", dry_walls, "wall_paint_material", "то же"),
        ("Стены", "Облицовка стен плиткой", "м²", wet_walls, "wall_tile_work", "стены за вычетом проёмов: санузел, ванная"),
        ("Стены", "Плитка настенная, клей, затирка (материал)", "м²", wet_walls, "wall_tile_material", "то же"),
        ("Потолки", "Устройство потолка", "м²", ceilings, "ceiling_work", "площадь потолков без балкона"),
        ("Потолки", "Материалы потолка", "м²", ceilings, "ceiling_material", "то же"),
        ("Двери", "Установка межкомнатной двери", "шт", interior, "door_interior_work", "число межкомнатных дверей"),
        ("Двери", "Дверь межкомнатная с коробкой и фурнитурой", "шт", interior, "door_interior_material", "то же"),
        ("Двери", "Установка входной двери", "шт", entrance, "door_entrance_work", "число входных дверей"),
        ("Двери", "Дверь входная", "шт", entrance, "door_entrance_material", "то же"),
        ("Окна", "Окна и балконный блок с монтажом", "м²", glazing, "window_per_m2", "площадь окон и балконной двери"),
    ]
    return [(s, name, unit, qty, float(prices[key]), basis) for s, name, unit, qty, key, basis in spec]


def estimate_total(rows: list[EstRow]) -> float:
    return sum(r[3] * r[4] for r in rows)
