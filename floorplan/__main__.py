"""Конвейер: PDF планировки → план → мебель → 3D (Blender) → объёмы → Excel.

    python -m floorplan input/projectfortest.pdf --corrections input/corrections.json --out out
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .analyze import analyze, strip_private
from .estimate import build_estimate, estimate_total
from .excel import write_excel
from .furniture import furnish, validate
from .model3d import build_solids, write_glb, write_model_json
from .quantities import assign_coverings, compute
from .read_pdf import read_pdf
from .render import render_plan


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf")
    ap.add_argument("--corrections", help="JSON с ручными данными (имена, границы зон, допущения)")
    ap.add_argument("--prices", help="JSON с примерными ценами за единицу — добавляет лист «Смета»")
    ap.add_argument("--out", default="out")
    ap.add_argument("--name", default="apartment")
    ap.add_argument("--blender", default="blender", help="путь к Blender; пустая строка — не собирать .blend")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    corrections = json.loads(Path(args.corrections).read_text(encoding="utf-8")) if args.corrections else {}

    raw = read_pdf(args.pdf)
    plan = analyze(raw, corrections)
    assign_coverings(plan)
    furniture, skipped = furnish(plan, raw)
    problems = validate(plan, furniture)
    solids = build_solids(plan, furniture)
    q = compute(plan, furniture)

    doc = strip_private(plan)
    doc["furniture"] = strip_private(furniture)
    (out / "plan.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    render_plan(plan, str(out / "plan_check.png"), furniture)
    n = write_model_json(solids, str(out / "model.json"))
    write_glb(solids, str(out / f"{args.name}.glb"))
    estimate = None
    if args.prices:
        prices = json.loads(Path(args.prices).read_text(encoding="utf-8"))
        estimate = build_estimate(plan, q, prices)
    write_excel(str(out / "volumes.xlsx"), plan, q, furniture, problems, skipped, estimate)

    blend_note = "не собирался"
    if args.blender:
        exe = shutil.which(args.blender)
        if exe is None:
            blend_note = f"Blender не найден ({args.blender})"
        else:
            script = Path(__file__).resolve().parent.parent / "tools" / "blender_build.py"
            # Blender записывает в файл полный путь, по которому тот сохранён.
            # Собираем во временном каталоге, чтобы в .blend не попал путь
            # домашней папки, и копируем готовое в out/.
            with tempfile.TemporaryDirectory(prefix="floorplan-", dir="/tmp" if Path("/tmp").is_dir() else None) as tmp:
                names = [f"{args.name}.blend", "render.jpg"]
                shutil.copy(out / "model.json", Path(tmp) / "model.json")
                cmd = [exe, "-b", "--factory-startup", "-P", str(script), "--", "model.json", *names]
                # HOME тоже подменяем: Blender запоминает домашний каталог в настройках окна файлов.
                res = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp, env={**os.environ, "HOME": tmp})
                ok = res.returncode == 0 and (Path(tmp) / names[0]).exists()
                if ok:
                    for name in names:
                        if (Path(tmp) / name).exists():
                            shutil.copy(Path(tmp) / name, out / name)
            blend_note = "собран" if ok else "ОШИБКА: " + (res.stderr or res.stdout)[-600:]
    print(f"Blender: {blend_note}")
    print(f"масштаб {plan['source']['scale']} (совпало {plan['source']['scale_votes']})")
    print(f"помещений {len(plan['rooms'])}, дверей {len(plan['doors'])}, окон {len(plan['windows'])}, тел в модели {n}")
    for c in plan["checks"]:
        print(f"  {c['number']}. {c['name']:<16} {c['area']:>6.2f} м²  на чертеже {c['printed']:>6.2f}  {c['deviation_pct']:+.2f} %  {'ok' if c['ok'] else 'ВНЕ ДОПУСКА'}")
    print(f"мебели {len(furniture)}; не поместилось: {', '.join(skipped) or '—'}")
    if estimate:
        print(f"смета по примерным ценам: {estimate_total(estimate):,.0f} ₽".replace(",", " "))
    print("замечания валидатора: " + ("; ".join(problems) or "нет"))


if __name__ == "__main__":
    main()
