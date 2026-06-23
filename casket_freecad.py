"""
Catholic Casket 3D Model — FreeCAD Macro
Run from FreeCAD: Tools > Macros > Open > select this file > Run

Creates a proper 3D assembly of the casket or coffin with correct panel
geometry, material thickness, dado positions, and assembled configuration.

Can also be called from casket_app.py via a generated .FCMacro launcher.
"""

import json, os, math

BASE        = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "casket_config.json")

with open(CONFIG_PATH) as f:
    CFG = json.load(f)

MAT = CFG["material"]["thickness"]


def make_casket_3d(size="adult", shape="casket", int_l=None, int_w=None, int_d=None):
    import FreeCAD as App
    import Part

    if size in CFG["sizes"] and int_l is None:
        sz = CFG["sizes"][size]
        iL, iW, iD = sz["int_L"], sz["int_W"], sz["int_D"]
    else:
        iL = int_l or 78.0
        iW = int_w or 22.0
        iD = int_d or 12.0

    doc_name = f"Casket_{size.title()}_{shape.title()}"
    try:
        doc = App.getDocument(doc_name)
        if doc:
            App.closeDocument(doc_name)
    except Exception:
        pass
    doc = App.newDocument(doc_name)

    if shape == "coffin":
        _build_coffin(doc, iL, iW, iD)
    else:
        _build_casket(doc, iL, iW, iD)

    doc.recompute()

    try:
        import FreeCADGui as Gui
        Gui.ActiveDocument = Gui.getDocument(doc_name)
        Gui.SendMsgToActiveView("ViewFit")
        Gui.SendMsgToActiveView("ViewIsometric")
    except Exception:
        pass

    out_path = os.path.join(BASE, f"casket_{size}_{shape}.FCStd")
    doc.saveAs(out_path)
    print(f"3D model saved: {out_path}")
    return out_path


# ── Rectangular casket ────────────────────────────────────────────────────────

def _build_casket(doc, iL, iW, iD):
    import Part
    m = MAT
    eL = iL + 2*m
    eW = iW + 2*m
    eH = iD + m

    dado_y = CFG["joinery"]["dado_from_bottom"]

    wood   = (0.72, 0.50, 0.28)
    lid_c  = (0.78, 0.56, 0.32)
    bottom = (0.65, 0.44, 0.24)

    panels = [
        ("Side_L",    Part.makeBox(eL, m,  eH),  (0,    0,    0),    wood),
        ("Side_R",    Part.makeBox(eL, m,  eH),  (0,    eW-m, 0),    wood),
        ("End_Head",  Part.makeBox(m,  iW, eH),  (0,    m,    0),    wood),
        ("End_Foot",  Part.makeBox(m,  iW, eH),  (eL-m, m,    0),    wood),
        ("Bottom",    Part.makeBox(iL, iW, m),   (m,    m,    0),    bottom),
        ("Lid",       Part.makeBox(eL, eW, m),   (0,    0,    eH),   lid_c),
    ]

    # Dado groove cutouts on side panels (bottom seat)
    dado_groove = Part.makeBox(eL, m, m)
    dado_groove.translate(_vec(0, 0, dado_y))

    for name, solid, pos, color in panels:
        solid.translate(_vec(*pos))
        obj = doc.addObject("Part::Feature", name)
        obj.Shape = solid
        _color(obj, color)

    # Cross engraving outline on lid (visual representation)
    lid_cx = eL / 2
    lid_cy = eW / 2
    lid_z  = eH + m + 0.02
    _add_cross_wire(doc, lid_cx, lid_cy, lid_z, min(eW * 0.55, 14.0))

    # Dimension annotations
    _add_dim_label(doc, f"Interior: {iL:.1f}\" × {iW:.1f}\" × {iD:.1f}\"",
                   eL/2, eW/2, -1.5)


def _build_coffin(doc, iL, iW, iD):
    import Part
    m   = MAT
    cfg = CFG.get("coffin", {})
    hw  = cfg.get("head_W",             14.0)
    fw  = cfg.get("foot_W",             12.0)
    sfh = cfg.get("shoulder_from_head", 16.0)
    sff = cfg.get("shoulder_from_foot", 16.0)
    ho  = (iw - hw) / 2 if (iw := iW) else 4.0
    fo  = (iW - fw) / 2

    pH = iD + m  # panel height

    # Interior 8-point outline
    pts_int = [
        (0,        ho),
        (sfh,      0),
        (iL-sff,   0),
        (iL,       fo),
        (iL,       iW-fo),
        (iL-sff,   iW),
        (sfh,      iW),
        (0,        iW-ho),
    ]

    wood  = (0.72, 0.50, 0.28)
    lid_c = (0.78, 0.56, 0.32)

    panels = [
        ("Head",           hw,                                 m,  pH, (0,         (iW-hw)/2,  0), wood),
        ("Foot",           fw,                                 m,  pH, (iL-fw,     (iW-fw)/2,  0), wood),
        ("Shoulder_Head_L",math.sqrt(sfh**2 + ho**2),         m,  pH, (0,         0,          0), wood),
        ("Shoulder_Head_R",math.sqrt(sfh**2 + ho**2),         m,  pH, (0,         iW-m,       0), wood),
        ("Shoulder_Foot_L",math.sqrt(sff**2 + fo**2),         m,  pH, (iL-sff,    0,          0), wood),
        ("Shoulder_Foot_R",math.sqrt(sff**2 + fo**2),         m,  pH, (iL-sff,    iW-m,       0), wood),
        ("Side_L",         iL - sfh - sff,                    m,  pH, (sfh,       0,          0), wood),
        ("Side_R",         iL - sfh - sff,                    m,  pH, (sfh,       iW-m,       0), wood),
    ]

    for name, pw, pt, ph, pos, color in panels:
        import Part
        solid = Part.makeBox(pw, pt, ph)
        solid.translate(_vec(*pos))
        obj = doc.addObject("Part::Feature", name)
        obj.Shape = solid
        _color(obj, color)

    # Coffin lid — extruded 8-sided polygon
    _make_coffin_lid_solid(doc, pts_int, m, iD + m, lid_c)

    _add_dim_label(doc, f"Interior: {iL:.1f}\" × {iW:.1f}\" × {iD:.1f}\" (coffin shape)",
                   iL/2, iW/2, -1.5)


def _make_coffin_lid_solid(doc, pts_int, mat, lid_z, color):
    import Part
    # Offset pts_int outward by MAT
    n = len(pts_int)
    area2 = sum(pts_int[i][0]*pts_int[(i+1)%n][1] - pts_int[(i+1)%n][0]*pts_int[i][1]
                for i in range(n))
    sign = 1 if area2 > 0 else -1
    edges = []
    for i in range(n):
        p1, p2 = pts_int[i], pts_int[(i+1)%n]
        dx, dy = p2[0]-p1[0], p2[1]-p1[1]
        L = math.sqrt(dx*dx+dy*dy)
        if L < 1e-9: continue
        nx, ny = sign*dy/L*mat, -sign*dx/L*mat
        edges.append(((p1[0]+nx, p1[1]+ny), (p2[0]+nx, p2[1]+ny)))
    result = []
    m2 = len(edges)
    for i in range(m2):
        e1, e2 = edges[i], edges[(i+1)%m2]
        dx1,dy1 = e1[1][0]-e1[0][0], e1[1][1]-e1[0][1]
        dx2,dy2 = e2[1][0]-e2[0][0], e2[1][1]-e2[0][1]
        denom = dx1*dy2 - dy1*dx2
        if abs(denom) < 1e-9:
            result.append(((e1[1][0]+e2[0][0])/2, (e1[1][1]+e2[0][1])/2))
        else:
            t = ((e2[0][0]-e1[0][0])*dy2 - (e2[0][1]-e1[0][1])*dx2) / denom
            result.append((e1[0][0]+t*dx1, e1[0][1]+t*dy1))
    try:
        verts = [Part.Vertex(x, y, lid_z) for x, y in result]
        wires = Part.makePolygon([Part.Vector(x, y, lid_z) for x, y in result] +
                                  [Part.Vector(result[0][0], result[0][1], lid_z)])
        face  = Part.Face(wires)
        lid   = face.extrude(Part.Vector(0, 0, mat))
        obj   = doc.addObject("Part::Feature", "Coffin_Lid")
        obj.Shape = lid
        _color(obj, color)
    except Exception as e:
        print(f"Lid solid failed: {e}")


# ── Engraving cross wire on lid ───────────────────────────────────────────────

def _add_cross_wire(doc, cx, cy, z, h):
    import Part
    ah = h*.097; bh = h*.278; bvh = h*.097; bcy = cy + h*.167
    top = cy + h/2; bot = cy - h/2
    pts = [
        (cx-ah, top), (cx+ah, top), (cx+ah, bcy+bvh), (cx+bh, bcy+bvh),
        (cx+bh, bcy-bvh), (cx+ah, bcy-bvh), (cx+ah, bot), (cx-ah, bot),
        (cx-ah, bcy-bvh), (cx-bh, bcy-bvh), (cx-bh, bcy+bvh), (cx-ah, bcy+bvh),
    ]
    try:
        wire = Part.makePolygon([Part.Vector(x, y, z) for x, y in pts] +
                                 [Part.Vector(pts[0][0], pts[0][1], z)])
        obj = doc.addObject("Part::Feature", "Lid_Cross_Engraving")
        obj.Shape = wire
        _color(obj, (0.55, 0.35, 0.15))
    except Exception:
        pass


def _add_dim_label(doc, text, x, y, z):
    try:
        import Draft
        Draft.make_text([text], placement=_vec(x, y, z))
    except Exception:
        pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _vec(x, y, z):
    import FreeCAD as App
    return App.Vector(x, y, z)


def _color(obj, rgb):
    try:
        obj.ViewObject.ShapeColor = rgb
    except Exception:
        pass


if __name__ == "__main__":
    import sys
    size  = sys.argv[1] if len(sys.argv) > 1 else "adult"
    shape = sys.argv[2] if len(sys.argv) > 2 else "casket"
    make_casket_3d(size=size, shape=shape)
