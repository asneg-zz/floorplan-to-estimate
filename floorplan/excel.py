"""Excel с объёмами: основной лист в формате ТЗ плюс справочные листы."""

from __future__ import annotations

from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEAD = PatternFill("solid", fgColor="DDE6F0")


def _sheet(wb: Workbook, title: str, header: list[str], rows: list[list[Any]], widths: list[int], first: bool = False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = HEAD
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for row in rows:
        ws.append(row)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if isinstance(c.value, float):
                c.number_format = "0.00"
    return ws


def _estimate_sheet(wb: Workbook, rows, floor_area: float) -> None:
    ws = wb.create_sheet("Смета")
    ws.append(["Раздел", "Работа / материал", "Ед. изм.", "Количество", "Цена за ед., ₽", "Сумма, ₽", "Откуда количество"])
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = HEAD
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for i, (section, name, unit, qty, price, basis) in enumerate(rows, 2):
        ws.append([section, name, unit, qty, price, f"=D{i}*E{i}", basis])
        ws[f"E{i}"].fill = PatternFill("solid", fgColor="FFF8D6")  # цену можно менять
    last = len(rows) + 1
    r = last + 2
    for section in dict.fromkeys(x[0] for x in rows):
        ws.append([]) if ws.max_row < r - 1 else None
        ws.cell(r, 2, f"Итого: {section}")
        ws.cell(r, 6, f'=SUMIF(A2:A{last},"{section}",F2:F{last})')
        r += 1
    ws.cell(r, 2, "ИТОГО по смете").font = Font(bold=True)
    ws.cell(r, 6, f"=SUM(F2:F{last})").font = Font(bold=True)
    total_row = r
    ws.cell(r + 1, 2, "Площадь квартиры без балкона, м²")
    ws.cell(r + 1, 6, round(floor_area, 2))
    ws.cell(r + 2, 2, "Стоимость на 1 м² площади")
    ws.cell(r + 2, 6, f"=F{total_row}/F{r + 1}")
    ws.cell(r + 4, 2, "Цены примерные (жёлтые ячейки) — замените своими, суммы пересчитаются.").font = Font(italic=True)
    for row in ws.iter_rows(min_row=2, min_col=4, max_col=6):
        for c in row:
            c.number_format = "#,##0.00"
    for i, w in enumerate([12, 46, 10, 13, 16, 16, 62], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"


def write_excel(path: str, plan: dict[str, Any], q: dict[str, Any], furniture: list[dict[str, Any]], problems: list[str], skipped: list[str], estimate=None) -> None:
    wb = Workbook()
    _sheet(wb, "Объёмы", ["Помещение", "Показатель", "Ед. изм.", "Количество", "Примечание"],
           [[r[0], r[1], r[2], round(r[3], 2) if r[2] != "шт" else int(r[3]), r[4]] for r in q["rows"]],
           [20, 36, 10, 13, 60], first=True)

    if estimate:
        _estimate_sheet(wb, estimate, sum(p["floor"] for p in q["per_room"] if p["name"] != "Балкон"))

    _sheet(wb, "Сводка",
           ["№", "Помещение", "Пол, м²", "Потолок, м²", "Периметр, м", "Высота, м", "Стены брутто, м²", "Проёмы, м²",
            "Стены нетто, м²", "Плинтус, м", "Объём, м³", "Дверей", "Окон", "Покрытие"],
           [[p["number"], p["name"], round(p["floor"], 2), round(p["ceiling"], 2), round(p["perimeter"], 2), round(p["height"], 3),
             round(p["walls_gross"], 2), round(p["openings"], 2), round(p["walls_net"], 2), round(p["plinth"], 2),
             round(p["volume"], 2), p["doors"], p["windows"], p["covering"]] for p in q["per_room"]],
           [5, 20, 10, 12, 12, 10, 12, 11, 12, 11, 10, 8, 7, 12])

    names = {r["number"]: r["name"] for r in plan["rooms"]}
    op = [[d["id"], "дверь", d["type"] + (", двустворчатая" if d["kind"] == "double" else ""), d["width"], d["height"],
           round(d["width"] * d["height"] / 1e6, 2), d["wall_thickness"], " ↔ ".join(names[n] for n in d["rooms"]) + (" ↔ снаружи" if len(d["rooms"]) == 1 else ""),
           "ширина — с чертежа, высота — допущение"] for d in plan["doors"]]
    op += [[w["id"], "окно", "", w["width"], w["height"], round(w["width"] * w["height"] / 1e6, 2), w["wall_thickness"],
            " ↔ ".join(names[n] for n in w["rooms"]), f"ширина — с чертежа; высота и подоконник {w['sill']:.0f} — допущение"] for w in plan["windows"]]
    _sheet(wb, "Проёмы", ["Марка", "Вид", "Тип", "Ширина, мм", "Высота, мм", "Площадь, м²", "Толщина стены, мм", "Помещения", "Источник"],
           op, [8, 9, 28, 12, 12, 12, 12, 34, 46])

    fr = []
    for f in furniture:
        xs = [p[0] for p in f["polygon"]]
        ys = [p[1] for p in f["polygon"]]
        fr.append([f["id"], f["room_name"], f["name"], max(xs) - min(xs), max(ys) - min(ys), f["h"], min(xs), min(ys), f["z0"], f["source"]])
    _sheet(wb, "Мебель", ["Марка", "Помещение", "Предмет", "Размер X, мм", "Размер Y, мм", "Высота, мм", "X, мм", "Y, мм", "Низ, мм", "Источник"],
           fr, [8, 20, 28, 12, 12, 11, 9, 9, 9, 10])

    ch = [[c["number"], c["name"], c["area"], c["printed"], c["deviation_pct"], "да" if c["ok"] else "нет", c["note"]] for c in plan["checks"]]
    _sheet(wb, "Проверка", ["№", "Помещение", "Площадь по модели, м²", "Площадь на чертеже, м²", "Отклонение, %", "В допуске ±2 %", "Пояснение"],
           ch, [5, 20, 14, 14, 13, 12, 90])

    d = plan["defaults"]
    notes = [
        ["Масштаб", plan["source"]["scale"], f"определён по размерным надписям: совпало {plan['source']['scale_votes']}"],
        ["Имена помещений", "вручную", "экспликация на листе не соответствует чертежу (7 помещений против 6), имена заданы в corrections.json"],
        ["Граница прихожей", "вручную", "на чертеже стены нет; линия зоны задана в corrections.json"],
        ["Высота помещений", "чертёж", "ближайшая к марке помещения отметка H; если отметки нет — у соседней зоны или допущение"],
        ["Высота дверей", f"{d['door_height']:.0f} мм", "допущение, на плане не указана"],
        ["Высота балконной двери", f"{d['balcony_door_height']:.0f} мм", "допущение"],
        ["Окна: подоконник / высота", f"{d['window_sill']:.0f} / {d['window_height']:.0f} мм", "допущение"],
        ["Высота балкона", f"{d['ceiling_height']:.0f} мм", "допущение, отметки на чертеже нет"],
        ["Потолки", "", "площадь равна площади пола; уровни и короба ГКЛ не разделены"],
        ["Мебель: не поместилось", "; ".join(skipped) or "—", "расстановщик не нашёл допустимого места"],
        ["Мебель: замечания валидатора", "; ".join(problems) or "нет", "пересечения, зоны дверей, окна, проход 700 мм"],
    ]
    _sheet(wb, "Допущения", ["Что", "Значение", "Откуда"], notes, [30, 34, 100])
    wb.save(path)
