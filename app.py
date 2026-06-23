import sys
import os
import io
import json
import glob
import zipfile
import tempfile
import subprocess
import datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
import generate_casket_dxf as gcd

CONFIG = json.loads((SRC / "casket_config.json").read_text(encoding="utf-8"))

DESIGNS = [
    ("cross",       "Latin Cross"),
    ("celtic",      "Celtic Cross"),
    ("ihs",         "IHS Monogram"),
    ("sacredheart", "Sacred Heart"),
    ("chirho",      "Chi-Rho"),
]
SIZE_LABELS = {"adult": "Adult", "child": "Child", "infant": "Infant"}
POCKET_DESIGNS = {"cross", "sacredheart"}

GEN_SCRIPT = str(SRC / "generate_casket_dxf.py")
UPLOAD_DIR = Path(tempfile.gettempdir()) / "casket_web_uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__)


def _scan_system_fonts():
    font_dir = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
    names = set()
    if os.path.isdir(font_dir):
        for path in glob.glob(os.path.join(font_dir, "*.ttf")) + glob.glob(os.path.join(font_dir, "*.otf")):
            stem = os.path.splitext(os.path.basename(path))[0]
            family = stem.split("-")[0].split("_")[0].replace(" Bold", "").replace(
                " Italic", "").replace(" Regular", "").strip()
            if family:
                names.add(family)
    fonts = sorted(names) or ["Times New Roman"]
    if "Times New Roman" in fonts:
        fonts.remove("Times New Roman")
    return ["Times New Roman"] + fonts


SYSTEM_FONTS = _scan_system_fonts()


@app.route("/")
def index():
    sizes = [
        {"id": k, "label": SIZE_LABELS.get(k, k.title()), **v}
        for k, v in CONFIG["sizes"].items()
    ]
    designs = [{"id": i, "label": l} for i, l in DESIGNS]
    return render_template(
        "index.html",
        sizes=sizes,
        designs=designs,
        fonts=SYSTEM_FONTS,
        coffin=CONFIG.get("coffin", {}),
        material=CONFIG["material"]["thickness"],
        default_text=CONFIG["engraving"]["inscription"],
    )


@app.route("/api/import-art", methods=["POST"])
def import_art():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "no file"}), 400
    f = request.files["file"]
    name = f.filename or "art"
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".svg", ".dxf"):
        return jsonify({"ok": False, "error": "only .svg or .dxf"}), 400
    dest = UPLOAD_DIR / f"{datetime.datetime.now().strftime('%H%M%S%f')}_{os.path.basename(name)}"
    f.save(str(dest))
    try:
        import art_import
        if ext == ".svg":
            raw_paths, closed, bbox = art_import.parse_svg(str(dest))
        else:
            raw_paths, closed, bbox = art_import.parse_dxf(str(dest))
        norm = art_import.normalize(raw_paths, bbox)
        layer = art_import.auto_layer(norm, closed)
    except Exception as e:
        return jsonify({"ok": False, "error": f"parse failed: {e}"}), 500
    return jsonify({
        "ok": True, "name": os.path.basename(name),
        "paths": norm, "closed": closed, "layer": layer,
    })


@app.route("/api/import-lid-dxf", methods=["POST"])
def import_lid_dxf():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "no file"}), 400
    f = request.files["file"]
    name = f.filename or "design"
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".svg", ".dxf"):
        return jsonify({"ok": False, "error": "only .svg or .dxf"}), 400
    dest = UPLOAD_DIR / f"liddxf_{datetime.datetime.now().strftime('%H%M%S%f')}_{os.path.basename(name)}"
    f.save(str(dest))
    preview = {"paths": [], "closed": []}
    try:
        import art_import
        if ext == ".svg":
            raw, closed, bbox = art_import.parse_svg(str(dest))
        else:
            raw, closed, bbox = art_import.parse_dxf(str(dest))
        preview = {"paths": art_import.normalize(raw, bbox), "closed": closed}
    except Exception:
        pass
    return jsonify({"ok": True, "name": os.path.basename(name),
                    "token": str(dest), **preview})


def _build_cmd(data):
    shape    = data.get("shape", "casket")
    design   = data.get("design", "cross")
    joinery  = data.get("joinery", "box")
    corner   = data.get("corner_style", "square")
    customer = (data.get("customer") or "").strip() or "web_order"
    size     = data.get("size", "adult")
    custom_dims = bool(data.get("custom_dims"))
    panel_items = data.get("panel_items") or {}

    design_layer = "pocket" if design in POCKET_DESIGNS else "centerline"

    cmd = [sys.executable, GEN_SCRIPT,
           "--design", design, "--joinery", joinery,
           "--corner-style", corner, "--customer", customer,
           "--design-layer", design_layer, "--shape", shape]

    if size in CONFIG["sizes"] and not custom_dims:
        cmd += ["--size", size]

    if shape == "casket" and data.get("split_lid"):
        cmd.append("--split-lid")

    if custom_dims:
        try:
            il = str(float(data.get("int_l")))
            iw = str(float(data.get("int_w")))
            id_ = str(float(data.get("int_d")))
        except (TypeError, ValueError):
            raise ValueError("invalid interior dimensions")
        cmd += ["--int-l", il, "--int-w", iw, "--int-d", id_]

    if shape == "coffin":
        cof = data.get("coffin") or {}
        for flag, key in [("--coffin-head-w", "head_W"), ("--coffin-foot-w", "foot_W"),
                          ("--coffin-sfh", "shoulder_from_head"),
                          ("--coffin-sff", "shoulder_from_foot")]:
            if cof.get(key) is not None:
                cmd += [flag, str(cof[key])]

    lid_items = panel_items.get("lid", [])
    texts = [it["text"].strip() for it in lid_items
             if it.get("type") == "text" and (it.get("text") or "").strip()]
    combined_text = "\n".join(texts) if texts else None
    first_text = next((it for it in lid_items
                       if it.get("type") == "text" and (it.get("text") or "").strip()), None)
    d_item = next((it for it in lid_items if it.get("type") == "design"), None)

    if combined_text:
        cmd += ["--text", combined_text]
    if first_text:
        cmd += ["--text-vpos",   str(round(first_text.get("vpos", 0.1), 4)),
                "--text-hpos",   str(round(first_text.get("hpos", 0.5), 4)),
                "--text-scale",  str(round(first_text.get("scale", 1.0), 3)),
                "--font-family", first_text.get("font", "Times New Roman")]
        rot = first_text.get("rotation", 0.0)
        if rot:
            cmd += ["--text-rotate", str(round(rot, 1))]
    if d_item:
        cmd += ["--design-vpos",  str(round(d_item.get("vpos", 0.5), 4)),
                "--design-hpos",  str(round(d_item.get("hpos", 0.5), 4)),
                "--design-scale", str(round(d_item.get("scale", 1.0), 3))]

    return cmd, panel_items


@app.route("/api/export", methods=["POST"])
def export():
    data = request.get_json(force=True, silent=True) or {}
    try:
        cmd, panel_items = _build_cmd(data)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    items_file = None
    non_lid = {k: v for k, v in panel_items.items() if v and k != "lid"}
    if non_lid:
        tf = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                         dir=str(UPLOAD_DIR), encoding="utf-8")
        json.dump(panel_items, tf, indent=2)
        tf.close()
        items_file = tf.name
        cmd += ["--panel-items-json", items_file]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(SRC))
        if result.returncode != 0:
            return jsonify({"ok": False,
                            "error": f"generator failed: {result.stderr[-600:]}"}), 500
        out_dir = None
        for line in result.stdout.splitlines():
            if line.strip().startswith("DXFs:"):
                out_dir = line.split("DXFs:", 1)[1].strip()
                break
        if not out_dir or not os.path.isdir(out_dir):
            return jsonify({"ok": False, "error": "output folder missing after generate"}), 500

        lid_dxf = (data.get("lid_custom_dxf") or "").strip()
        if lid_dxf and os.path.exists(lid_dxf):
            _apply_custom_lid(lid_dxf, out_dir)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for fp in sorted(glob.glob(os.path.join(out_dir, "*"))):
                z.write(fp, os.path.basename(fp))
        buf.seek(0)
        return send_file(buf, mimetype="application/zip", as_attachment=True,
                         download_name=f"{os.path.basename(out_dir)}.zip")
    finally:
        if items_file and os.path.exists(items_file):
            os.unlink(items_file)


def _apply_custom_lid(src_path, out_dir):
    eng = CONFIG["engraving"]
    ext = os.path.splitext(src_path)[1].lower()
    out_path = os.path.join(out_dir, "lid_custom.dxf")
    relayer = str(SRC / "relayer_dxf.py")
    cmd = [sys.executable, relayer, src_path, "--op", "centerline",
           "--depth", str(eng["depth"]), "--tool", str(eng["tool_diameter"]),
           "--output", out_path]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and os.path.exists(out_path):
            return out_path
    except Exception:
        pass
    import shutil
    shutil.copy2(src_path, out_path)
    return out_path


@app.route("/api/freecad", methods=["POST"])
def freecad():
    data = request.get_json(force=True, silent=True) or {}
    size  = data.get("size", "adult")
    shape = data.get("shape", "casket")
    fc_script = SRC / "casket_freecad.py"
    if not fc_script.exists():
        return jsonify({"ok": False, "error": "casket_freecad.py not found"}), 500
    macro_path = UPLOAD_DIR / f"casket_{size}_{shape}.FCMacro"
    try:
        il = float(data.get("int_l"))
        iw = float(data.get("int_w"))
        id_ = float(data.get("int_d"))
        dims_line = (f'cf.make_casket_3d(size="{size}", shape="{shape}", '
                     f'int_l={il}, int_w={iw}, int_d={id_})\n')
    except (TypeError, ValueError):
        dims_line = f'cf.make_casket_3d(size="{size}", shape="{shape}")\n'
    macro_path.write_text(
        f'import sys, os\nsys.path.insert(0, r"{SRC}")\n'
        f'import casket_freecad as cf\n{dims_line}', encoding="utf-8")

    fc_paths = [r"C:\Program Files\FreeCAD\bin\FreeCAD.exe",
                r"C:\Program Files\FreeCAD 1.0\bin\FreeCAD.exe",
                r"C:\Program Files\FreeCAD 0.21\bin\FreeCAD.exe"]
    for fp in fc_paths:
        if os.path.exists(fp):
            subprocess.Popen([fp, str(macro_path)])
            return jsonify({"ok": True, "launched": True,
                            "msg": f"FreeCAD launching with {macro_path.name}"})
    return send_file(str(macro_path), as_attachment=True,
                     download_name=macro_path.name)


def run(port=5002, debug=False):
    import logging
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    print(f"Casket Configurator: http://0.0.0.0:{port}")
    run(port=port)
