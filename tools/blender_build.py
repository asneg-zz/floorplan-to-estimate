"""Сборка сцены Blender из model.json. Запускается самим Blender:

    blender -b --factory-startup -P tools/blender_build.py -- out/model.json out/apartment.blend [out/render.jpg]

Каждая призма становится отдельным объектом с понятным именем; объекты
разложены по коллекциям. Единицы — метры, ось Z вверх.
"""
import json
import math
import os
import sys

import bmesh
import bpy
from mathutils import Vector
from mathutils.geometry import tessellate_polygon

args = sys.argv[sys.argv.index("--") + 1 :]
src, dst = os.path.abspath(args[0]), os.path.abspath(args[1])
render_path = os.path.abspath(args[2]) if len(args) > 2 else None
MM = 0.001
GLASS_ALPHA = 0.12
CEILING_ALPHA = 0.06

COLLECTIONS = {
    "пол": "Полы",
    "стена": "Стены",
    "окно": "Окна",
    "дверь": "Двери",
    "потолок": "Потолки",
    "свет": "Освещение",
    "мебель": "Мебель",
}
# Конструкции не выделяются щелчком, чтобы мышью бралась только мебель.
# Вернуть можно в Outliner: фильтр → значок стрелки у коллекции.
LOCKED = ("пол", "стена", "окно", "дверь", "потолок", "свет")

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.unit_settings.system = "METRIC"

collections = {}
for key, title in COLLECTIONS.items():
    col = bpy.data.collections.new(title)
    scene.collection.children.link(col)
    collections[key] = col

materials = {}


def material(color: str, kind: str = "plain"):
    """kind: plain — обычный, glass — стекло, ceiling — почти невидимый потолок, glow — светящийся плафон."""
    key = (color, kind)
    if key in materials:
        return materials[key]
    title = {"plain": "Цвет", "glass": "Стекло", "ceiling": "Потолок", "glow": "Плафон"}[kind]
    mat = bpy.data.materials.new(f"{title} {color}")
    rgb = [(int(color[i : i + 2], 16) / 255) ** 2.2 for i in (1, 3, 5)]  # sRGB → линейное
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.05 if kind == "glass" else 0.85
    alpha = {"glass": GLASS_ALPHA, "ceiling": CEILING_ALPHA}.get(kind, 1.0)
    # diffuse_color — то, что видно в режиме Solid; альфа там и даёт прозрачность.
    mat.diffuse_color = (*rgb, alpha)
    if alpha < 1.0:
        bsdf.inputs["Alpha"].default_value = alpha
        mat.blend_method = "BLEND"
        mat.shadow_method = "NONE"  # не отбрасывает тень
        mat.use_backface_culling = False
    if kind == "glow":
        bsdf.inputs["Emission Color"].default_value = (*rgb, 1.0)
        bsdf.inputs["Emission Strength"].default_value = 2.5
    materials[key] = mat
    return mat


def prism(solid):
    """Призма по контуру с отверстиями: дно, крышка и боковые грани."""
    rings = solid["rings"]
    z0, z1 = solid["z0"] * MM, solid["z1"] * MM
    # Точка привязки объекта — центр основания: вокруг неё он вращается и
    # за неё перемещается, а не вокруг начала координат сцены.
    xs, ys = [p[0] for p in rings[0]], [p[1] for p in rings[0]]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    bm = bmesh.new()
    flat = [p for ring in rings for p in ring]
    bottom = [bm.verts.new(((x - cx) * MM, (y - cy) * MM, 0.0)) for x, y in flat]
    top = [bm.verts.new(((x - cx) * MM, (y - cy) * MM, z1 - z0)) for x, y in flat]
    tris = tessellate_polygon([[Vector((x, y, 0.0)) for x, y in ring] for ring in rings])
    for a, b, c in tris:
        if len({a, b, c}) == 3:
            try:
                bm.faces.new((top[a], top[b], top[c]))
                bm.faces.new((bottom[c], bottom[b], bottom[a]))
            except ValueError:
                pass  # вырожденный треугольник
    start = 0
    for ring in rings:
        n = len(ring)
        for i in range(n):
            a, b = start + i, start + (i + 1) % n
            bm.faces.new((bottom[a], bottom[b], top[b], top[a]))
        start += n
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(solid["name"])
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(solid["name"], mesh)
    obj.location = (cx * MM, cy * MM, z0)
    group = solid["group"]
    obj["group"] = group
    obj["z0_mm"], obj["z1_mm"] = solid["z0"], solid["z1"]
    if group == "мебель":
        # Мебель скользит по полу и вращается только вокруг вертикали.
        obj.lock_location[2] = True
        obj.lock_rotation[0] = obj.lock_rotation[1] = True
        obj.lock_scale = (True, True, True)
    kind = {"окно": "glass", "потолок": "ceiling"}.get(group, "plain")
    if group == "свет" and "подвес" not in solid["name"]:
        kind = "glow"
    mesh.materials.append(material(solid["color"], kind))
    collections[group].objects.link(obj)
    return obj


def load_font():
    """Шрифт с кириллицей; упаковывается в .blend, чтобы файл не зависел от системы."""
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf",
                 "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
        if os.path.exists(path):
            font = bpy.data.fonts.load(path)
            font.pack()
            return font
    return None  # встроенный шрифт Blender


def text_object(name, text, collection, max_w, max_h, max_size, min_size, color):
    """Плоская надпись, вписанная в прямоугольник max_w × max_h."""
    curve = bpy.data.curves.new(name, "FONT")
    curve.body = text
    curve.align_x, curve.align_y = "CENTER", "CENTER"
    curve.size = 1.0
    if font:
        curve.font = font
    obj = bpy.data.objects.new(name, curve)
    collection.objects.link(obj)
    bpy.context.view_layer.update()
    tw, th = max(obj.dimensions.x, 1e-6), max(obj.dimensions.y, 1e-6)
    curve.size = max(min(max_w / tw, max_h / th, max_size), min_size)
    curve.materials.append(material(color))
    return obj


def furniture_label(obj, solid):
    """Надпись на верхней грани предмета; привязана к нему и ездит вместе с ним."""
    text = solid["name"].split(" ", 1)[1].replace(" (верх)", "")
    xs, ys = [p[0] for p in solid["rings"][0]], [p[1] for p in solid["rings"][0]]
    w, d = (max(xs) - min(xs)) * MM, (max(ys) - min(ys)) * MM
    long, short = max(w, d), min(w, d)
    words = text.split()
    if len(words) > 1 and long < 2.2 * short:
        text = "\n".join(words)  # почти квадратный предмет — переносим по словам
    rgb = [int(solid["color"][i : i + 2], 16) / 255 for i in (1, 3, 5)]
    dark = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2] < 0.55
    # Не мельче 9 см: на маленьком предмете надпись может слегка выйти за край, зато читается.
    tobj = text_object("Подпись " + solid["name"], text, labels_col, 0.86 * long, 0.7 * short, 0.22, 0.09,
                       "#ffffff" if dark else "#111111")
    tobj.parent = obj
    tobj.location = (0.0, 0.0, (solid["z1"] - solid["z0"]) * MM + 0.004)
    tobj.rotation_euler = (0.0, 0.0, math.radians(90) if d > w else 0.0)


with open(src, encoding="utf-8") as fh:
    doc = json.load(fh)
solids = doc["solids"]
objects = [prism(s) for s in solids]
for key in LOCKED:
    collections[key].hide_select = True

font = load_font()

# --- подписи на мебели
labels_col = bpy.data.collections.new("Подписи мебели")
scene.collection.children.link(labels_col)
for obj, solid in zip(objects, solids):
    # Низ кухни закрыт верхними шкафами — подпись ставим только на верх.
    if solid["group"] == "мебель" and not solid["name"].endswith("(низ)"):
        furniture_label(obj, solid)
labels_col.hide_select = True  # щелчок по надписи выделяет сам предмет под ней

# --- названия и площади помещений на полу
rooms_col = bpy.data.collections.new("Подписи помещений")
scene.collection.children.link(rooms_col)
for lb in doc.get("room_labels", []):
    side = max(lb["radius"] * MM * 1.7, 0.9)
    tobj = text_object("Помещение " + lb["text"].split("\n")[0], lb["text"], rooms_col, side, side, 0.34, 0.12, "#101010")
    tobj.data.space_line = 1.15
    tobj.location = (lb["x"] * MM, lb["y"] * MM, 0.006)
rooms_col.hide_select = True

# --- источники света под светильниками
for lt in doc.get("lights", []):
    data = bpy.data.lights.new(f"{lt['id']} свет", "POINT")
    data.energy = lt["power"]
    data.color = (1.0, 0.86, 0.68)  # тёплый белый
    data.shadow_soft_size = 0.12
    lamp = bpy.data.objects.new(f"{lt['id']} свет", data)
    drop = 0.62 if lt["kind"] == "chandelier" else 0.16  # чуть ниже плафона
    lamp.location = (lt["x"] * MM, lt["y"] * MM, lt["z"] * MM - drop)
    collections["свет"].objects.link(lamp)

# --- камера и дневной свет: вид сверху-сбоку на всю квартиру
corners = [Vector(c) + o.location for o in objects for c in o.bound_box]
lo = Vector([min(c[i] for c in corners) for i in range(3)])
hi = Vector([max(c[i] for c in corners) for i in range(3)])
center, size = (lo + hi) / 2, max(hi - lo)

cam = bpy.data.objects.new("Камера", bpy.data.cameras.new("Камера"))
scene.collection.objects.link(cam)
cam.location = center + Vector((-0.12 * size, -0.62 * size, 1.15 * size))
cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
cam.data.lens = 32
scene.camera = cam

sun = bpy.data.objects.new("Солнце", bpy.data.lights.new("Солнце", "SUN"))
sun.data.energy = 1.2  # приглушено, чтобы был виден свет люстр
sun.rotation_euler = (math.radians(50), 0, math.radians(-30))
scene.collection.objects.link(sun)

world = bpy.data.worlds.new("Мир")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (1, 1, 1, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.2
scene.world = world

scene.render.engine = "CYCLES"
scene.cycles.samples = 64
scene.cycles.sample_clamp_indirect = 3.0  # гасит яркие точки шума от ламп
scene.cycles.device = "CPU"
scene.cycles.use_denoising = False
scene.render.resolution_x, scene.render.resolution_y = 1000, 720
scene.view_settings.view_transform = "Standard"
scene.render.image_settings.file_format = "JPEG"

# Окно просмотра, с которым файл откроется: вид сверху на всю квартиру и
# стрелки перемещения у выделенного предмета — тянуть за них проще, чем
# ловить мышью точку привязки.
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type != "VIEW_3D":
            continue
        space = area.spaces[0]
        space.show_gizmo = True
        space.show_gizmo_object_translate = True
        space.show_gizmo_object_rotate = True
        space.shading.type = "SOLID"
        space.shading.color_type = "MATERIAL"
        r3d = space.region_3d
        if r3d is not None:
            r3d.view_perspective = "ORTHO"
            r3d.view_rotation = (1.0, 0.0, 0.0, 0.0)  # сверху
            r3d.view_location = center
            r3d.view_distance = size * 1.4

bpy.ops.wm.save_as_mainfile(filepath=dst)
print(f"blend: {len(objects)} объектов, светильников {len(doc.get('lights', []))} → {dst}")
if render_path:
    scene.render.filepath = render_path
    bpy.ops.render.render(write_still=True)
