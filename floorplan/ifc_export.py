"""Экспорт модели в IFC4 — открытый формат обмена BIM-моделями.

В файл попадает то же, что в сцену Blender, но как объекты с типами:
стены (IfcWall), полы (IfcSlab), двери (IfcDoor), окна (IfcWindow),
мебель (IfcFurniture) и помещения (IfcSpace). У помещений записаны
количества из расчёта объёмов (Qto_SpaceBaseQuantities) — их может
прочитать сметная программа.

Упрощения: геометрия — призмы; проёмы вырезаны из стен заранее, поэтому
связей «стена — проём — дверь» (IfcOpeningElement) нет; классификаторов и
наборов атрибутов для экспертизы нет.
"""

from __future__ import annotations

from typing import Any

import numpy as np

CLASS_BY_PREFIX = [
    ("Пол", "IfcSlab", "FLOOR"),
    ("Порог", "IfcSlab", "FLOOR"),
    ("Потолок", "IfcCovering", "CEILING"),
    ("Окно", "IfcWindow", "WINDOW"),
    ("Остекление", "IfcCurtainWall", None),
    ("Дверь", "IfcDoor", "DOOR"),
]
CLASS_BY_GROUP = {"стена": ("IfcWall", None), "мебель": ("IfcFurniture", None), "свет": ("IfcLightFixture", None)}


def _ifc_class(solid: dict[str, Any]) -> tuple[str, str | None]:
    for prefix, cls, ptype in CLASS_BY_PREFIX:
        if solid["name"].startswith(prefix):
            return cls, ptype
    return CLASS_BY_GROUP.get(solid["group"], ("IfcBuildingElementProxy", None))


def write_ifc(solids: list[dict[str, Any]], plan: dict[str, Any], q: dict[str, Any], furniture: list[dict[str, Any]], path: str, name: str) -> dict[str, int]:
    import ifcopenshell.api.aggregate as aggregate
    import ifcopenshell.api.context as context
    import ifcopenshell.api.geometry as geometry
    import ifcopenshell.api.project as project
    import ifcopenshell.api.pset as pset
    import ifcopenshell.api.root as root
    import ifcopenshell.api.spatial as spatial
    import ifcopenshell.api.style as style
    import ifcopenshell.api.unit as unit

    f = project.create_file(version="IFC4")
    proj = root.create_entity(f, ifc_class="IfcProject", name=name)
    unit.assign_unit(f)  # по умолчанию: миллиметры, м², м³
    model_ctx = context.add_context(f, context_type="Model")
    body = context.add_context(f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model_ctx)

    site = root.create_entity(f, ifc_class="IfcSite", name="Участок")
    building = root.create_entity(f, ifc_class="IfcBuilding", name="Дом")
    storey = root.create_entity(f, ifc_class="IfcBuildingStorey", name="Этаж")
    aggregate.assign_object(f, products=[site], relating_object=proj)
    aggregate.assign_object(f, products=[building], relating_object=site)
    aggregate.assign_object(f, products=[storey], relating_object=building)

    origin = f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None)
    up = f.createIfcDirection((0.0, 0.0, 1.0))

    def ring(coords):
        pts = [f.createIfcCartesianPoint((float(x), float(y))) for x, y in coords]
        return f.createIfcPolyline(pts + [pts[0]])

    def prism(product, geom, z0: float, z1: float, color: str | None = None, transparency: float = 0.0) -> None:
        geom = geom.simplify(0.5)
        outer = ring(list(geom.exterior.coords)[:-1])
        holes = [ring(list(r.coords)[:-1]) for r in geom.interiors]
        prof = (
            f.createIfcArbitraryProfileDefWithVoids("AREA", None, outer, holes)
            if holes
            else f.createIfcArbitraryClosedProfileDef("AREA", None, outer)
        )
        solid = f.createIfcExtrudedAreaSolid(prof, origin, up, float(z1 - z0))
        rep = f.createIfcShapeRepresentation(body, "Body", "SweptSolid", [solid])
        geometry.assign_representation(f, product=product, representation=rep)
        m = np.eye(4)
        m[2, 3] = z0
        geometry.edit_object_placement(f, product=product, matrix=m, is_si=False)
        if color:
            style.assign_representation_styles(f, shape_representation=rep, styles=[surface_style(color, transparency)])

    styles: dict[tuple[str, float], Any] = {}

    def surface_style(color: str, transparency: float):
        key = (color, transparency)
        if key not in styles:
            st = style.add_style(f, name=color)
            rgb = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
            style.add_surface_style(
                f, style=st, ifc_class="IfcSurfaceStyleShading",
                attributes={"SurfaceColour": {"Name": None, "Red": rgb[0], "Green": rgb[1], "Blue": rgb[2]}, "Transparency": transparency},
            )
            styles[key] = st
        return styles[key]

    # --- помещения с количествами
    spaces: dict[int, Any] = {}
    per_room = {p["number"]: p for p in q["per_room"]}
    for r in plan["rooms"]:
        sp = root.create_entity(f, ifc_class="IfcSpace", predefined_type="INTERNAL", name=str(r["number"]))
        sp.LongName = r["name"]
        aggregate.assign_object(f, products=[sp], relating_object=storey)
        prism(sp, r["_geom"], 0.0, r["height"])
        p = per_room[r["number"]]
        qto = pset.add_qto(f, product=sp, name="Qto_SpaceBaseQuantities")
        pset.edit_qto(f, qto=qto, properties={
            "Height": p["height"] * 1000,  # длины — в единицах проекта, мм
            "GrossPerimeter": p["perimeter"] * 1000,
            "NetPerimeter": (p["perimeter"] - p["open"]) * 1000,
            "GrossFloorArea": p["floor"],
            "NetFloorArea": p["floor"],
            "GrossCeilingArea": p["ceiling"],
            "NetCeilingArea": p["ceiling"],
            "GrossWallArea": p["walls_gross"],
            "NetWallArea": p["walls_net"],
            "GrossVolume": p["volume"],
            "NetVolume": p["volume"],
        })
        extra = pset.add_pset(f, product=sp, name="Floorplan_Estimate")
        pset.edit_pset(f, pset=extra, properties={
            "FloorCovering": p["covering"],
            "SkirtingLength_m": round(p["plinth"], 2),
            "Doors": int(p["doors"]),
            "Windows": int(p["windows"]),
            "AreaOnDrawing_m2": float(r["area_printed"]),
        })
        spaces[r["number"]] = sp

    # --- элементы
    openings = {x["id"]: x for x in plan["doors"] + plan["windows"]}
    room_of = {f"{it['id']} {it['name']}": it["room"] for it in furniture}
    counts: dict[str, int] = {"IfcSpace": len(spaces)}
    by_container: dict[int, list[Any]] = {}
    for s in solids:
        cls, ptype = _ifc_class(s)
        el = root.create_entity(f, ifc_class=cls, predefined_type=ptype, name=s["name"])
        prism(el, s["geom"], s["z0"], s["z1"], s["color"], {"окно": 0.8, "потолок": 0.9}.get(s["group"], 0.0))
        if cls in ("IfcDoor", "IfcWindow"):
            mark = s["name"].split()[1]
            if mark in openings:
                el.OverallWidth = float(openings[mark]["width"])
                el.OverallHeight = float(openings[mark]["height"])
        room = room_of.get(s["name"])
        by_container.setdefault(room or 0, []).append(el)
        counts[cls] = counts.get(cls, 0) + 1
    for room, products in by_container.items():
        # Мебель лежит в своём помещении, конструкции — на этаже.
        spatial.assign_container(f, products=products, relating_structure=spaces[room] if room else storey)

    f.write(path)
    return counts
