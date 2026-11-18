"""Чтение векторного PDF планировки.

Достаёт из страницы «сырые» слои без какой-либо интерпретации: залитые
полигоны (по цвету заливки), отрезки (по толщине линии), дуги и надписи.
Все координаты переводятся в миллиметры, ось Y направлена вверх, начало —
левый нижний угол габарита залитых стен.

Масштаб не задаётся руками: он подбирается по размерным надписям чертежа
(число в мм рядом с отрезком той же ориентации) и берётся самым частым.
"""

from __future__ import annotations

import collections
import math
import re
from dataclasses import dataclass, field

import pymupdf
from shapely.geometry import Polygon
from shapely.ops import unary_union

Point = tuple[float, float]
Color = tuple[float, float, float]

# Цвета заливки ARCHICAD в этом чертеже. Проверено отрисовкой слоёв.
FILL_BEARING: Color = (0.31, 0.31, 0.31)  # несущие стены
FILL_PARTITION: Color = (0.55, 0.55, 0.55)  # перегородки
FILL_WHITE: Color = (1.0, 1.0, 1.0)  # окна, полотна дверей, остекление

PT_TO_MM = 25.4 / 72.0


@dataclass
class Segment:
    a: Point
    b: Point
    width: float  # толщина линии в пунктах PDF

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)


@dataclass
class Arc:
    """Кубическая кривая Безье (так PDF хранит дуги открывания дверей)."""

    pts: tuple[Point, Point, Point, Point]
    width: float


@dataclass
class Label:
    text: str
    pos: Point  # центр надписи
    vertical: bool


@dataclass
class RawPlan:
    scale: float  # знаменатель масштаба: 50 для 1:50
    scale_votes: int  # сколько размерных надписей подтвердили масштаб
    scale_total: int  # сколько надписей участвовало в голосовании
    fills: dict[Color, list[Polygon]] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    arcs: list[Arc] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)


def _color(c) -> Color | None:
    return tuple(round(x, 2) for x in c) if c else None  # type: ignore[return-value]


def _detect_scale(segs_pt, labels_pt) -> tuple[float, int, int]:
    """Голосование: «число мм» / «длина ближайшего параллельного отрезка»."""
    votes: collections.Counter[int] = collections.Counter()
    total = 0
    for text, (cx, cy), vertical in labels_pt:
        if not re.fullmatch(r"\d{1,2} \d{3}|\d{3}", text):
            continue
        value = int(text.replace(" ", ""))
        found = set()
        for (x1, y1), (x2, y2) in segs_pt:
            length = math.hypot(x2 - x1, y2 - y1)
            if length < 5 or vertical != (abs(x2 - x1) < 0.01):
                continue
            if math.hypot((x1 + x2) / 2 - cx, (y1 + y2) / 2 - cy) < 10:
                found.add(round(value / (length * PT_TO_MM)))
        if found:
            total += 1
            votes.update(found)
    if not votes:
        raise ValueError("не удалось определить масштаб: нет размерных надписей")
    scale, n = votes.most_common(1)[0]
    return float(scale), n, total


def read_pdf(path: str, page_index: int = 0) -> RawPlan:
    page = pymupdf.open(path)[page_index]

    fills_pt: dict[Color, list[list[Point]]] = collections.defaultdict(list)
    segs_pt: list[tuple[Point, Point, float]] = []
    arcs_pt: list[tuple[list[Point], float]] = []
    for d in page.get_drawings():
        fill = _color(d.get("fill"))
        width = round(d.get("width") or 0.0, 3)
        if fill:
            ring = [(p.x, p.y) for it in d["items"] if it[0] == "l" for p in (it[1], it[2])]
            for it in d["items"]:
                if it[0] == "re":
                    r = it[1]
                    ring += [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
            if len(ring) > 2:
                fills_pt[fill].append(ring)
            continue
        for it in d["items"]:
            if it[0] == "l":
                segs_pt.append(((it[1].x, it[1].y), (it[2].x, it[2].y), width))
            elif it[0] == "c":
                arcs_pt.append(([(p.x, p.y) for p in it[1:5]], width))

    labels_pt = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                x0, y0, x1, y1 = line["bbox"]
                labels_pt.append((text, ((x0 + x1) / 2, (y0 + y1) / 2), abs(line["dir"][1]) > 0.5))

    scale, votes, total = _detect_scale([(a, b) for a, b, _ in segs_pt], labels_pt)
    k = PT_TO_MM * scale

    wall_rings = fills_pt.get(FILL_BEARING, []) + fills_pt.get(FILL_PARTITION, [])
    if not wall_rings:
        raise ValueError("в PDF не найдено заливок стен")
    xs = [x for ring in wall_rings for x, _ in ring]
    ys = [y for ring in wall_rings for _, y in ring]
    ox, oy = min(xs), max(ys)

    def mm(p: Point) -> Point:
        return (round((p[0] - ox) * k, 1), round((oy - p[1]) * k, 1))

    raw = RawPlan(scale=scale, scale_votes=votes, scale_total=total)
    for color, rings in fills_pt.items():
        polys = [Polygon([mm(p) for p in ring]).buffer(0) for ring in rings]
        raw.fills[color] = [p for p in polys if not p.is_empty and p.area > 1.0]
    raw.segments = [Segment(mm(a), mm(b), w) for a, b, w in segs_pt]
    raw.arcs = [Arc(tuple(mm(p) for p in pts), w) for pts, w in arcs_pt]  # type: ignore[arg-type]
    raw.labels = [Label(t, mm(c), v) for t, c, v in labels_pt]
    return raw


def walls_union(raw: RawPlan, color: Color):
    return unary_union(raw.fills.get(color, []))
