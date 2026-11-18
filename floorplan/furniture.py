"""Автоматическая расстановка мебели по правилам.

Никакого «дизайна»: у каждого предмета есть габарит, зона подхода спереди и
набор предпочтений (дальше от двери, ближе к окну, у стояка и т. п.).
Расстановщик перебирает положения вдоль стен с шагом 50 мм, отбрасывает
недопустимые и берёт положение с лучшей оценкой.

Жёсткие ограничения (нарушать нельзя):
  * предмет целиком внутри помещения и не пересекает другие предметы;
  * предмет не попадает в зону открывания дверей и в проход открытой зоны;
  * высокий предмет не закрывает окно;
  * зона подхода перед предметом свободна от мебели.

После расстановки отдельный валидатор проверяет результат заново, не доверяя
расстановщику, и дополнительно — что к каждой двери остаётся проход 700 мм.
"""

from __future__ import annotations

import math
from typing import Any

from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

from .read_pdf import FILL_WHITE, RawPlan

PASSAGE = 700.0  # минимальная ширина прохода, мм
STEP = 50.0

# name, ширина вдоль стены, глубина, высота, зона подхода спереди, предпочтения
# Предпочтения: (критерий, вес). Критерии описаны в _score().
PROGRAMS: dict[str, list[dict[str, Any]]] = {
    "Кухня-гостиная": [
        {"name": "Кухонный гарнитур", "w": 3600, "d": 620, "h": 900, "front": 1000, "kind": "kitchen",
         "prefer": [("shaft_near", 3.0), ("snug", 1.0), ("window_far", 0.3)]},
        {"name": "ТВ-тумба", "w": 1800, "d": 420, "h": 500, "front": 600, "kind": "tv",
         "prefer": [("far:Кухонный гарнитур", 1.0), ("window_far", 1.0), ("door_far", 0.5), ("centered", 0.5)]},
        {"name": "Обеденный стол", "w": 1600, "d": 900, "h": 750, "kind": "table", "chairs": 6,
         "prefer": [("near:Кухонный гарнитур", 0.5), ("window_near", 2.0), ("far:Диван", 1.0)]},
    ],
    "Спальня": [
        {"name": "Кровать", "w": 1600, "d": 2050, "h": 500, "front": 700, "kind": "bed",
         "prefer": [("door_far", 1.5), ("centered", 1.0), ("window_side", 1.0)]},
        {"name": "Комод", "w": 1000, "d": 450, "h": 850, "front": 700, "kind": "wall",
         "prefer": [("far:Кровать", 0.5), ("snug", 1.0), ("door_far", 0.3)]},
        {"name": "Письменный стол", "w": 1200, "d": 600, "h": 750, "front": 800, "kind": "desk",
         "prefer": [("window_near", 2.0), ("snug", 0.5)]},
    ],
    "Прихожая": [
        {"name": "Шкаф для одежды", "w": 900, "d": 400, "h": 2200, "front": 600, "kind": "wall",
         "prefer": [("snug", 1.0), ("door_far", 0.5)]},
    ],
    "Санузел": [
        {"name": "Унитаз", "w": 380, "d": 650, "h": 400, "front": 500, "kind": "wall",
         "prefer": [("shaft_near", 2.0), ("door_far", 1.0)]},
        {"name": "Раковина малая", "w": 400, "d": 280, "h": 850, "front": 450, "kind": "wall",
         "prefer": [("door_near", 0.5), ("snug", 1.0)]},
    ],
    "Ванная": [
        {"name": "Ванна", "w": 1700, "d": 700, "h": 600, "front": 600, "kind": "wall",
         "prefer": [("door_far", 1.5), ("snug", 2.0)]},
        {"name": "Тумба с раковиной", "w": 700, "d": 480, "h": 850, "front": 600, "kind": "wall",
         "prefer": [("door_near", 0.3), ("snug", 0.5), ("far:Ванна", 0.3)]},
        {"name": "Стиральная машина", "w": 600, "d": 600, "h": 850, "front": 600, "kind": "wall",
         "prefer": [("snug", 1.5), ("near:Тумба с раковиной", 0.5)]},
    ],
    "Балкон": [
        {"name": "Кресло", "w": 700, "d": 700, "h": 750, "front": 300, "kind": "wall",
         "prefer": [("snug", 2.0), ("door_far", 1.0)]},
        {"name": "Столик", "w": 500, "d": 500, "h": 450, "front": 0, "kind": "wall",
         "prefer": [("near:Кресло", 2.0), ("door_far", 0.5)]},
    ],
}


def _rect(o, t, n, w: float, d: float) -> Polygon:
    return Polygon(
        [
            o,
            (o[0] + t[0] * w, o[1] + t[1] * w),
            (o[0] + t[0] * w + n[0] * d, o[1] + t[1] * w + n[1] * d),
            (o[0] + n[0] * d, o[1] + n[1] * d),
        ]
    )


class RoomCtx:
    """Геометрия одного помещения и всё, что в нём нельзя заслонять."""

    def __init__(self, room: dict[str, Any], plan: dict[str, Any]):
        self.room = room
        self.geom: Polygon = room["_geom"]
        self.number: int = room["number"]
        self.doors = [d for d in plan["doors"] if self.number in d["rooms"]]
        self.windows = [w for w in plan["windows"] if self.number in w["rooms"]]
        self.shafts = [Polygon(v["polygon"]) for v in plan["voids"]]
        blocked = [d["_swing"].buffer(50) for d in self.doors]
        # Граница открытой зоны (без стены) — это проход, его держим свободным.
        for other in plan["rooms"]:
            if other is room:
                continue
            shared = self.geom.boundary.intersection(other["_geom"].boundary.buffer(5))
            if shared.length > 300:
                blocked.append(shared.buffer(PASSAGE))
        self.blocked = unary_union(blocked) if blocked else Polygon()
        self.window_zone = unary_union([w["_geom"].buffer(150) for w in self.windows]) if self.windows else Polygon()
        self.items: list[dict[str, Any]] = []

    # --- кандидаты вдоль стен

    def wall_edges(self):
        ring = list(self.geom.simplify(1.0).exterior.coords)
        for a, b in zip(ring, ring[1:]):
            length = math.dist(a, b)
            if length < 300:
                continue
            t = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
            n = (-t[1], t[0])
            mid = ((a[0] + b[0]) / 2 + n[0] * 20, (a[1] + b[1]) / 2 + n[1] * 20)
            if not self.geom.contains(Point(mid)):
                n = (-n[0], -n[1])
            # Открытую границу зоны стеной не считаем.
            if self.blocked.buffer(-PASSAGE + 30).intersects(LineString([a, b]).buffer(5)) and not any(
                d["_swing"].intersects(LineString([a, b])) for d in self.doors
            ):
                continue
            yield a, t, n, length

    def occupied(self, z0: float = 0.0, z1: float = 1e9):
        fps = [i["_geom"] for i in self.items if i["z0"] < z1 and i["z0"] + i["h"] > z0]
        return unary_union(fps) if fps else Polygon()

    def valid(self, fp: Polygon, h: float, front: Polygon | None = None, z0: float = 0.0) -> bool:
        if not self.geom.buffer(1).contains(fp):
            return False
        if fp.intersection(self.blocked).area > 1:
            return False
        occ = self.occupied(z0, z0 + h)
        if fp.intersection(occ).area > 1:
            return False
        if h > 1000 and fp.intersects(self.window_zone):
            return False
        if front is not None:
            fr = front.intersection(self.geom)
            if fr.area < 0.9 * front.area or fr.intersection(self.occupied()).area > 1:
                return False
        # Не заставляем чужие зоны подхода.
        for i in self.items:
            if i.get("_front") is not None and fp.intersection(i["_front"]).area > 1:
                return False
        return True

    def find(self, name: str):
        return next((i for i in self.items if i["name"].startswith(name)), None)

    def add(self, name: str, fp: Polygon, h: float, front: Polygon | None = None, z0: float = 0.0, source="авто"):
        item = {
            "name": name,
            "room": self.number,
            "room_name": self.room["name"],
            "polygon": [[round(x), round(y)] for x, y in fp.exterior.coords[:-1]],
            "z0": z0,
            "h": h,
            "source": source,
            "_geom": fp,
            "_front": front,
        }
        self.items.append(item)
        return item

    # --- оценка положения

    def _score(self, fp: Polygon, prefer, t, edge_len: float, s: float, w: float) -> float:
        total = 0.0
        c = fp.centroid
        for crit, weight in prefer:
            v = 0.0
            if crit in ("door_far", "door_near") and self.doors:
                dist = min(fp.distance(d["_geom"]) for d in self.doors) / 1000
                v = dist if crit == "door_far" else -dist
            elif crit in ("window_near", "window_far") and self.windows:
                dist = min(fp.distance(wd["_geom"]) for wd in self.windows) / 1000
                v = -dist if crit == "window_near" else min(dist, 3.0)
            elif crit == "window_side" and self.windows:
                # Изголовье не под окном, но окно сбоку — свет не в лицо.
                v = -1.0 if fp.buffer(100).intersects(self.window_zone) else 0.0
            elif crit == "shaft_near" and self.shafts:
                v = -min(fp.distance(sh) for sh in self.shafts) / 1000
            elif crit == "snug":
                # Доля периметра, прижатая к стенам: угол лучше середины стены.
                v = fp.buffer(30).intersection(self.geom.boundary).length / fp.length * 2
            elif crit == "centered":
                v = -abs((s + w / 2) - edge_len / 2) / 1000
            elif crit.startswith(("near:", "far:")):
                other = self.find(crit.split(":", 1)[1])
                if other:
                    dist = c.distance(other["_geom"]) / 1000
                    v = -dist if crit.startswith("near:") else min(dist, 5.0)
            total += weight * v
        return total

    def place_on_wall(self, spec: dict[str, Any]):
        best = None
        w, d, h = spec["w"], spec["d"], spec["h"]
        for a, t, n, length in self.wall_edges():
            s = 0.0
            while s + w <= length + 0.5:
                o = (a[0] + t[0] * s, a[1] + t[1] * s)
                fp = _rect(o, t, n, w, d)
                front = _rect((o[0] + n[0] * d, o[1] + n[1] * d), t, n, w, spec["front"]) if spec.get("front") else None
                if self.valid(fp, h, front):
                    sc = self._score(fp, spec["prefer"], t, length, s, w)
                    if best is None or sc > best[0] + 1e-9:
                        best = (sc, fp, front, o, t, n)
                s += STEP
        return best

    def place_free(self, spec: dict[str, Any], margin: float):
        """Отдельно стоящий предмет: перебор центров по сетке, две ориентации."""
        best = None
        x0, y0, x1, y1 = self.geom.bounds
        for w, d in ((spec["w"], spec["d"]), (spec["d"], spec["w"])):
            y = y0 + d / 2 + margin
            while y < y1:
                x = x0 + w / 2 + margin
                while x < x1:
                    fp = box(x - w / 2, y - d / 2, x + w / 2, y + d / 2)
                    zone = fp.buffer(margin, join_style="mitre")
                    if self.geom.contains(zone) and zone.intersection(self.blocked).area < 1 and zone.intersection(
                        self.occupied()
                    ).area < 1 and all(i.get("_front") is None or zone.intersection(i["_front"]).area < 1 for i in self.items):
                        sc = self._score(fp, spec["prefer"], (1, 0), 0, 0, w)
                        if best is None or sc > best[0] + 1e-9:
                            best = (sc, fp, w >= d)
                    x += 100
                y += 100
        return best


def _builtin_wardrobes(raw: RawPlan, ctx: RoomCtx) -> None:
    """Встроенные шкафы уже нарисованы на чертеже: белые прямоугольники глубиной ~600."""
    for p in raw.fills.get(FILL_WHITE, []):
        x0, y0, x1, y1 = p.bounds
        short, long = sorted((x1 - x0, y1 - y0))
        if 550 <= short <= 650 and long >= 1000 and p.area > 0.97 * short * long and ctx.geom.buffer(5).contains(p):
            if p.intersection(ctx.occupied()).area < 1:
                ctx.add("Шкаф встроенный", box(x0, y0, x1, y1), 2400.0, source="чертёж")


def furnish(plan: dict[str, Any], raw: RawPlan) -> tuple[list[dict[str, Any]], list[str]]:
    placed: list[dict[str, Any]] = []
    skipped: list[str] = []
    for room in plan["rooms"]:
        ctx = RoomCtx(room, plan)
        _builtin_wardrobes(raw, ctx)
        for spec in PROGRAMS.get(room["name"], []):
            kind = spec["kind"]
            if kind == "table":
                res = ctx.place_free(spec, margin=750)
                if not res:
                    skipped.append(f"{room['name']}: {spec['name']}")
                    continue
                _, fp, horiz = res
                ctx.add(spec["name"], fp, spec["h"])
                _chairs(ctx, fp, horiz, spec.get("chairs", 0))
                continue
            res = ctx.place_on_wall(spec)
            if not res:
                skipped.append(f"{room['name']}: {spec['name']}")
                continue
            _, fp, front, o, t, n = res
            if kind == "kitchen":
                _kitchen(ctx, spec, o, t, n, front)
            else:
                ctx.add(spec["name"], fp, spec["h"], front)
            if kind == "bed":
                _bedside(ctx, spec, o, t, n)
            if kind == "tv":
                _sofa_group(ctx, fp, t, n, skipped)
            if kind == "desk":
                c = fp.centroid
                chair = box(-225, -225, 225, 225)
                ch = affinity.translate(chair, c.x + n[0] * (spec["d"] / 2 + 260), c.y + n[1] * (spec["d"] / 2 + 260))
                # Стул стоит в зоне подхода своего же стола — это и есть его место.
                if ctx.geom.contains(ch) and ch.intersection(ctx.blocked).area < 1 and ch.intersection(ctx.occupied()).area < 1:
                    ctx.add("Стул", ch, 900.0)
        placed += ctx.items
    for i, it in enumerate(placed, 1):
        it["id"] = f"F{i}"
    return placed, skipped


def _kitchen(ctx: RoomCtx, spec, o, t, n, front) -> None:
    """Линейная кухня: холодильник с того края, что упирается в стену, низ и верхние шкафы."""
    w, d = spec["w"], spec["d"]
    fridge_w = 600.0
    start = _rect(o, t, n, fridge_w, d + 30)
    end = _rect((o[0] + t[0] * (w - fridge_w), o[1] + t[1] * (w - fridge_w)), t, n, fridge_w, d + 30)

    def side_contact(fp):
        return fp.buffer(40).intersection(ctx.geom.boundary).length

    if side_contact(start) >= side_contact(end):
        fridge, base_o = start, (o[0] + t[0] * fridge_w, o[1] + t[1] * fridge_w)
    else:
        fridge, base_o = end, o
    base = _rect(base_o, t, n, w - fridge_w, d)
    ctx.add("Холодильник", fridge, 2000.0)
    ctx.add("Кухонный гарнитур (низ)", base, 900.0, front)
    ctx.add("Кухонный гарнитур (верх)", _rect(base_o, t, n, w - fridge_w, 350), 700.0, z0=1450.0)


def _bedside(ctx: RoomCtx, spec, o, t, n) -> None:
    tw, td = 450.0, 400.0
    for origin in ((o[0] - t[0] * tw, o[1] - t[1] * tw), (o[0] + t[0] * spec["w"], o[1] + t[1] * spec["w"])):
        fp = _rect(origin, t, n, tw, td)
        if ctx.valid(fp, 500):
            ctx.add("Тумба прикроватная", fp, 500.0)


def _sofa_group(ctx: RoomCtx, tv: Polygon, t, n, skipped: list[str]) -> None:
    """Диван напротив ТВ, по одной оси с ним; между ними журнальный столик."""
    sw, sd = 2400.0, 950.0
    c = tv.centroid
    tv_d = 420.0
    for dist in (3000, 2800, 3200, 2600, 3400, 2400):
        base = (c.x + n[0] * (tv_d / 2 + dist), c.y + n[1] * (tv_d / 2 + dist))
        o = (base[0] - t[0] * sw / 2, base[1] - t[1] * sw / 2)
        sofa = _rect(o, t, n, sw, sd)
        behind = _rect((o[0] + n[0] * sd, o[1] + n[1] * sd), t, n, sw, PASSAGE)
        if ctx.valid(sofa, 850) and behind.intersection(ctx.geom).area > 0.9 * behind.area and behind.intersection(
            ctx.occupied()
        ).area < 1:
            ctx.add("Диван", sofa, 850.0, _rect((o[0] - n[0] * 500, o[1] - n[1] * 500), t, n, sw, 500))
            tc = (base[0] - n[0] * 800, base[1] - n[1] * 800)
            table = _rect((tc[0] - t[0] * 500, tc[1] - t[1] * 500), t, (-n[0], -n[1]), 1000, 600)
            if ctx.geom.contains(table) and table.intersection(ctx.occupied()).area < 1:
                ctx.add("Журнальный столик", table, 450.0)
            return
    skipped.append(f"{ctx.room['name']}: Диван")


def _chairs(ctx: RoomCtx, table: Polygon, horiz: bool, count: int) -> None:
    x0, y0, x1, y1 = table.bounds
    per_side = count // 2
    cs = 450.0
    long = (x1 - x0) if horiz else (y1 - y0)
    pitch = long / per_side
    for k in range(per_side):
        along = (x0 if horiz else y0) + pitch * (k + 0.5) - cs / 2
        for side in (0, 1):
            if horiz:
                yy = y0 - cs - 30 if side == 0 else y1 + 30
                fp = box(along, yy, along + cs, yy + cs)
            else:
                xx = x0 - cs - 30 if side == 0 else x1 + 30
                fp = box(xx, along, xx + cs, along + cs)
            if ctx.geom.contains(fp) and fp.intersection(ctx.occupied()).area < 1:
                ctx.add("Стул", fp, 900.0)


# ---------------------------------------------------------------- валидатор


def validate(plan: dict[str, Any], furniture: list[dict[str, Any]]) -> list[str]:
    """Независимая проверка расстановки. Пустой список — замечаний нет."""
    problems: list[str] = []
    for room in plan["rooms"]:
        geom = room["_geom"]
        items = [f for f in furniture if f["room"] == room["number"]]
        doors = [d for d in plan["doors"] if room["number"] in d["rooms"]]
        for f in items:
            if not geom.buffer(2).contains(f["_geom"]):
                problems.append(f"{f['id']} {f['name']}: выходит за пределы помещения «{room['name']}»")
            for d in doors:
                if f["_geom"].intersection(d["_swing"]).area > 1:
                    problems.append(f"{f['id']} {f['name']}: в зоне открывания двери {d['id']}")
            if f["h"] + f["z0"] > 1000 and f["z0"] < 2000:
                for w in plan["windows"]:
                    if room["number"] in w["rooms"] and f["_geom"].intersects(w["_geom"].buffer(100)):
                        problems.append(f"{f['id']} {f['name']}: закрывает окно {w['id']}")
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                z_overlap = a["z0"] < b["z0"] + b["h"] and b["z0"] < a["z0"] + a["h"]
                if z_overlap and a["_geom"].intersection(b["_geom"]).area > 1:
                    problems.append(f"{a['id']} {a['name']} пересекается с {b['id']} {b['name']}")
        # Проход: сжимаем свободный пол на половину ширины прохода. Все двери
        # должны выходить в один и тот же связный кусок.
        floor_items = [f["_geom"] for f in items if f["z0"] < 1000]
        free = geom.difference(unary_union(floor_items)) if floor_items else geom
        core = free.buffer(-PASSAGE / 2 + 1)
        parts = [core] if core.geom_type == "Polygon" else list(getattr(core, "geoms", []))
        parts = [p.buffer(PASSAGE / 2) for p in parts if not p.is_empty]
        reach = [next((k for k, p in enumerate(parts) if p.distance(d["_geom"]) < 150), None) for d in doors]
        if any(r is None for r in reach):
            bad = [d["id"] for d, r in zip(doors, reach) if r is None]
            problems.append(f"«{room['name']}»: нет прохода {PASSAGE:.0f} мм к двери {', '.join(bad)}")
        elif len(set(reach)) > 1:
            problems.append(f"«{room['name']}»: двери не связаны проходом {PASSAGE:.0f} мм")
    return problems
