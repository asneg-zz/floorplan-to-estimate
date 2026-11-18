"""Сборка 3D-модели из плана.

Модель — плоский список «призм»: контур в плане + отметки низа и верха.
Из одного списка получаются оба файла:
  * model.json — по нему скрипт tools/blender_build.py собирает сцену Blender
    (.blend) с коллекциями, материалами и камерой;
  * .glb — нейтральный формат для любого просмотрщика.
"""

from __future__ import annotations

import json
from typing import Any

from shapely.geometry import Polygon, box
from shapely.ops import unary_union

FLOOR_COLORS = {"плитка": "#b9c4cc", "ламинат": "#c9a36b"}
# Цвет мебели по виду предмета: ищется первое совпадение начала названия.
FURNITURE_COLORS = [
    ("Диван", "#3f6fb5"),
    ("Кресло", "#3fa58a"),
    ("Кровать", "#8a6fc4"),
    ("Шкаф", "#7a5230"),
    ("Комод", "#b5651d"),
    ("ТВ-тумба", "#3a3a44"),
    ("Тумба прикроватная", "#d9a066"),
    ("Тумба с раковиной", "#4fb3d0"),
    ("Кухонный гарнитур (верх)", "#e8c95a"),
    ("Кухонный гарнитур", "#e0903a"),
    ("Холодильник", "#c4ccd4"),
    ("Обеденный стол", "#a8672b"),
    ("Письменный стол", "#c98d4b"),
    ("Журнальный столик", "#6b4a2f"),
    ("Столик", "#6b4a2f"),
    ("Стул", "#d9534f"),
    ("Ванна", "#f2f6f8"),
    ("Унитаз", "#f2f6f8"),
    ("Раковина", "#dfe9ee"),
    ("Стиральная машина", "#9aa7b1"),
]
FURNITURE_DEFAULT = "#5f8fc4"


def furniture_color(name: str) -> str:
    return next((c for prefix, c in FURNITURE_COLORS if name.startswith(prefix)), FURNITURE_DEFAULT)


def _polys(geom) -> list[Polygon]:
    # simplify убирает совпадающие и лежащие на одной прямой точки: нулевые
    # рёбра в эскизе ломают выдавливание в геометрическом ядре.
    parts = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
    return [g.simplify(0.5) for g in parts if g.geom_type == "Polygon" and g.area > 100]


def _thin(poly: Polygon, thickness: float, to: float = 40.0) -> Polygon:
    """Сжимает прямоугольник проёма до толщины полотна/стекла."""
    shrunk = poly.buffer(-(thickness - to) / 2, join_style="mitre")
    return shrunk if not shrunk.is_empty else poly


def build_solids(plan: dict[str, Any], furniture: list[dict[str, Any]]) -> list[dict[str, Any]]:
    g = plan["_geom"]
    d = plan["defaults"]
    top = max(r["height"] for r in plan["rooms"])  # отметка низа перекрытия
    head = d["window_sill"] + d["window_height"]
    solids: list[dict[str, Any]] = []

    def add(name, group, geom, z0, z1, color):
        for i, p in enumerate(_polys(geom), 1):
            many = len(_polys(geom)) > 1
            solids.append({"name": f"{name} {i}" if many else name, "group": group, "geom": p, "z0": z0, "z1": z1, "color": color})

    for r in plan["rooms"]:
        add(f"Пол — {r['name']}", "пол", r["_geom"], -100.0, 0.0, FLOOR_COLORS[r["covering"]])
    add("Стена несущая", "стена", g["bearing"], 0.0, top, "#8c8c8c")
    add("Перегородка", "стена", g["partition"], 0.0, top, "#c8b79a")

    door_cut = unary_union([x["_geom"] for x in plan["doors"]])
    # Створка на чертеже тоньше стены. Вырезаем окно на всю толщину, иначе
    # в проёме остаётся сплошная тонкая стенка.
    def through(w):
        x0, y0, x1, y1 = w["_geom"].bounds
        return box(x0, y0 - 100, x1, y1 + 100) if abs(w["axis"][0]) > 0.5 else box(x0 - 100, y0, x1 + 100, y1)

    win_cut = unary_union([through(x) for x in plan["windows"]])
    for i, band in enumerate(g["bands"], 1):
        add(f"Подоконная часть {i}", "стена", band.difference(door_cut), 0.0, d["window_sill"], "#8c8c8c")
        add(f"Простенки окон {i}", "стена", band.difference(door_cut).difference(win_cut), d["window_sill"], head, "#f2f2f2")
        add(f"Надоконная часть {i}", "стена", band, head, top, "#8c8c8c")
    for w in plan["windows"]:
        add(f"Окно {w['id']}", "окно", _thin(w["_geom"], w["wall_thickness"] - 30), w["sill"], w["sill"] + w["height"], "#9fd3f0")
    for x in plan["doors"]:
        if x["type"] != "балконная":
            add(f"Перемычка {x['id']}", "стена", x["_geom"], x["height"], top, "#c8b79a")
        add(f"Порог {x['id']}", "пол", x["_geom"], -100.0, 0.0, "#a8a8a8")  # пол в проёме, иначе там дыра
        glazed = x["type"] == "балконная"
        add(f"Дверь {x['id']} ({x['type']})", "окно" if glazed else "дверь", _thin(x["_geom"], x["wall_thickness"]), 0.0,
            min(x["height"], head), "#9fd3f0" if glazed else "#8b5a2b")
    add("Ограждение балкона (парапет)", "стена", g["thin"], 0.0, 1100.0, "#b0b0b0")
    add("Остекление балкона", "окно", g["thin"], 1100.0, d["ceiling_height"], "#9fd3f0")

    for f in furniture:
        solids.append({"name": f"{f['id']} {f['name']}", "group": "мебель", "geom": f["_geom"].simplify(0.5), "z0": f["z0"], "z1": f["z0"] + f["h"], "color": furniture_color(f["name"])})
    return solids


def write_model_json(solids: list[dict[str, Any]], path: str) -> int:
    """Список призм для сборки сцены в Blender (tools/blender_build.py)."""
    doc = [
        {
            "name": s["name"],
            "group": s["group"],
            "color": s["color"],
            "z0": s["z0"],
            "z1": s["z1"],
            "rings": [[[round(x, 1), round(y, 1)] for x, y in ring.coords[:-1]] for ring in [s["geom"].exterior, *s["geom"].interiors]],
        }
        for s in solids
    ]
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)
    return len(doc)


def write_glb(solids: list[dict[str, Any]], path: str) -> None:
    import numpy as np
    import trimesh

    scene = trimesh.Scene()
    for s in solids:
        mesh = trimesh.creation.extrude_polygon(s["geom"], s["z1"] - s["z0"])
        mesh.apply_translation((0, 0, s["z0"]))
        rgb = [int(s["color"][i : i + 2], 16) for i in (1, 3, 5)]
        alpha = 30 if s["group"] == "окно" else 255
        mesh.visual = trimesh.visual.TextureVisuals(
            material=trimesh.visual.material.PBRMaterial(
                baseColorFactor=[*rgb, alpha], alphaMode="BLEND" if alpha < 255 else "OPAQUE", roughnessFactor=0.9, metallicFactor=0.0
            )
        )
        scene.add_geometry(mesh, node_name=s["name"], geom_name=s["name"])
    # мм → м, Z вверх → Y вверх (соглашение glTF)
    m = np.diag([0.001, 0.001, 0.001, 1.0])
    rot = trimesh.transformations.rotation_matrix(-np.pi / 2, (1, 0, 0))
    scene.apply_transform(rot @ m)
    scene.export(path)
