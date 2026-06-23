"""
Catholic Casket DXF Generator — Thermwood Control Nesting
Reads casket_config.json, outputs one DXF per part into dxf/{size}/

Usage:
  python generate_casket_dxf.py                   all sizes, cross engraving
  python generate_casket_dxf.py --size adult       one size
  python generate_casket_dxf.py --design celtic    alternate engraving design
  python generate_casket_dxf.py --text "In Memoriam John Smith 1945-2026"

Thermwood Control Nesting layer convention (manual pp.97-106):
  outline z0p75          through cut — part perimeter, depth = mat thickness
  dado z0p375            groove cut — 4-line closed rectangle
  dado2 / dado3          second / third dado on same part (rabbet ends)
  pocket d0p25 z0p125    pocket engraving — V-bit fills shape
  centerline d0p25 z0p125 centerline trace — V-bit follows line
  'p' = decimal point:   z0p375=0.375"   d0p25=0.25"
  ONE outline layer per file. ONE part per DXF file.
"""

import json, os, math, sys, argparse
import ezdxf
from ezdxf.math import Matrix44

BASE        = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "casket_config.json")
DXF_ROOT    = os.path.normpath(os.path.join(BASE, "..", "dxf"))

with open(CONFIG_PATH) as f:
    CFG = json.load(f)

MAT     = CFG["material"]["thickness"]
SHEET_W = CFG["sheet"]["width"]
SHEET_L = CFG["sheet"]["length"]
KERF    = CFG["sheet"]["kerf"]
J       = CFG["joinery"]
ENG     = CFG["engraving"]
FEAT    = CFG.get("features", {})

# ── Layer name helpers ──────────────────────────────────────────────────────

def zv(v):
    s = f"{v:.4f}".rstrip("0")
    if s.endswith("."): s += "0"
    return s.replace(".", "p")

L_OUTLINE = f"outline z{zv(MAT)}"
L_DADO    = f"dado z{zv(J['dado_depth'])}"
L_DADO2   = f"dado2 z{zv(J['dado_depth'])}"
L_DADO3   = f"dado3 z{zv(J['dado_depth'])}"
L_POCKET  = f"pocket d{zv(ENG['tool_diameter'])} z{zv(ENG['depth'])}"
L_CENTER  = f"centerline d{zv(ENG['tool_diameter'])} z{zv(ENG['depth'])}"
L_DRILL   = f"drill z{zv(MAT)}"
L_DADO4   = f"dado4 z{zv(J['dado_depth'])}"
L_NOTES   = "Notes"
L_DRILL_HALF = f"drill z{zv(MAT/2)}"
L_FINGER     = f"pocket d{zv(J.get('joinery_bit_diameter', 0.5))} z{zv(MAT)}"
RO_R             = FEAT.get("roundover_radius", 0.0)
L_ROUND          = f"roundover r{zv(RO_R)} z{zv(RO_R)}" if RO_R > 0 else None
L_ROUNDOVER_TAB  = f"roundover_tab z{zv(MAT)}"

# ── DXF geometry helpers ────────────────────────────────────────────────────

def add_rect(msp, x, y, w, h, layer):
    pts = [(x,y),(x+w,y),(x+w,y+h),(x,y+h)]
    for i in range(4):
        msp.add_line(pts[i], pts[(i+1)%4], dxfattribs={"layer": layer})

def add_polygon(msp, pts, layer):
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts)
    for i in range(n):
        msp.add_line(pts[i], pts[(i+1)%n], dxfattribs={"layer": layer})

def add_arc_approx(msp, cx, cy, r, a0, a1, segs, layer):
    angs = [a0 + (a1-a0)*i/segs for i in range(segs+1)]
    for i in range(len(angs)-1):
        p1 = (cx+r*math.cos(angs[i]),   cy+r*math.sin(angs[i]))
        p2 = (cx+r*math.cos(angs[i+1]), cy+r*math.sin(angs[i+1]))
        msp.add_line(p1, p2, dxfattribs={"layer": layer})

def add_finger_slots(msp, panel_L, panel_H, side_tabs_first, corner_style="square"):
    n, fw = box_finger_params(panel_H, FINGER_W)
    slot_depth = MAT + CLEARANCE

    def is_tab(i):
        return (i % 2 == 0) if side_tabs_first else (i % 2 == 1)

    dovetail_side = (corner_style == "dovetail") and side_tabs_first
    undercut = slot_depth * math.tan(math.radians(14)) if dovetail_side else 0.0

    for i in range(n):
        if not is_tab(i):
            y_bot = i * fw
            y_top = (i + 1) * fw
            if dovetail_side:
                # Trapezoid: wider at face (x=0), narrower at inner end (x=slot_depth).
                # Straight bit cuts this shape; the wider opening leaves tabs that
                # are narrower at their exposed face, matching the dovetail-bit undercut
                # in the end panel slots.  Inner corners radius = JOINERY_BIT_R from bit.
                add_polygon(msp, [
                    (0,          y_bot - undercut),
                    (slot_depth, y_bot),
                    (slot_depth, y_top),
                    (0,          y_top + undercut),
                ], L_FINGER)
                add_polygon(msp, [
                    (panel_L,              y_bot - undercut),
                    (panel_L - slot_depth, y_bot),
                    (panel_L - slot_depth, y_top),
                    (panel_L,              y_top + undercut),
                ], L_FINGER)
                r = JOINERY_BIT_R
                msp.add_text(
                    f"Dovetail side slots: inner corners r={r}\" — straight bit",
                    dxfattribs={"height": 0.25, "layer": L_NOTES}
                ).set_placement((panel_L / 2, -1.2),
                                align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
            else:
                # End panels: rectangular path; dovetail bit creates undercut at runtime
                add_rect(msp, 0,                    y_bot, slot_depth, y_top - y_bot, L_FINGER)
                add_rect(msp, panel_L - slot_depth, y_bot, slot_depth, y_top - y_bot, L_FINGER)

# ── Box joint helpers ───────────────────────────────────────────────────────

MIN_FINGER = J.get("min_finger", 0.625)
MAX_FINGER = J.get("max_finger", 1.375)

def box_finger_params(panel_H, target_fw):
    n = max(1, int(panel_H / target_fw))
    if n % 2 == 0:
        n -= 1
    for _ in range(20):
        fw = panel_H / max(1, n)
        if fw < MIN_FINGER and n > 1:
            n -= 2
        elif fw > MAX_FINGER:
            candidate = n + 2
            if panel_H / candidate >= MIN_FINGER:
                n = candidate
            else:
                break
        else:
            break
    n = max(1, n)
    return n, panel_H / n


def _insert_dogbones(pts, r):
    if r <= 0:
        return pts, []
    result = []
    arcs = []
    n = len(pts)
    for i in range(n):
        p_cur  = pts[i]
        p_prev = pts[(i - 1) % n]
        p_next = pts[(i + 1) % n]
        dx_in  = p_cur[0] - p_prev[0]; dy_in  = p_cur[1] - p_prev[1]
        dx_out = p_next[0] - p_cur[0];  dy_out = p_next[1] - p_cur[1]
        li = math.sqrt(dx_in*dx_in + dy_in*dy_in)
        lo = math.sqrt(dx_out*dx_out + dy_out*dy_out)
        if li < 1e-9 or lo < 1e-9:
            result.append(p_cur); continue
        uxi = dx_in/li;  uyi = dy_in/li
        uxo = dx_out/lo; uyo = dy_out/lo
        cross = uxi*uyo - uyi*uxo
        if cross > -0.7:
            result.append(p_cur); continue
        ex = p_cur[0] - r*uxi; ey = p_cur[1] - r*uyi
        fx = p_cur[0] + r*uxo; fy = p_cur[1] + r*uyo
        a0 = math.atan2(ey - p_cur[1], ex - p_cur[0])
        a1 = math.atan2(fy - p_cur[1], fx - p_cur[0])
        while a1 > a0: a1 -= 2*math.pi
        seg_idx = len(result)
        result.append((ex, ey))
        result.append((fx, fy))
        arcs.append((seg_idx, p_cur[0], p_cur[1], r,
                     math.degrees(a0) % 360, math.degrees(a1) % 360))
    return result, arcs


def add_polygon_with_dogbones(msp, pts, arcs, layer):
    if len(pts) < 2:
        return
    arc_map = {a[0]: a for a in arcs}
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        if i in arc_map:
            _, cx, cy, r, a_start, a_end = arc_map[i]
            msp.add_arc((cx, cy), r, a_start, a_end, dxfattribs={"layer": layer})
        else:
            s, e = pts[i], pts[j]
            if abs(s[0]-e[0]) > 1e-9 or abs(s[1]-e[1]) > 1e-9:
                msp.add_line(s, e, dxfattribs={"layer": layer})


def box_joint_outline(total_L, panel_H, mat, finger_w, side_tabs_first=True, clearance=0.0, dogbone_r=0.0):
    """
    Perimeter pts for a panel with box joint combs on both short ends.
    side_tabs_first=True  → side panel  (tab at y=0, slot at y=fw, ...)
    side_tabs_first=False → end panel   (slot at y=0, tab at y=fw, ...)
    Tabs reach the panel edge; slots recess by mat+clearance from each end.
    """
    n, fw = box_finger_params(panel_H, finger_w)

    def is_tab(i):
        return (i % 2 == 0) if side_tabs_first else (i % 2 == 1)

    def lx(i):
        return 0.0 if is_tab(i) else mat + clearance

    def rx(i):
        return total_L if is_tab(i) else total_L - mat - clearance

    pts = []
    pts.append((lx(0), 0.0))
    pts.append((rx(0), 0.0))

    for i in range(n):
        y_top = (i + 1) * fw
        pts.append((rx(i), y_top))
        if i < n - 1 and abs(rx(i) - rx(i + 1)) > 1e-9:
            pts.append((rx(i + 1), y_top))

    pts.append((lx(n - 1), panel_H))

    for i in range(n - 1, -1, -1):
        y_bot = i * fw
        pts.append((lx(i), y_bot))
        if i > 0 and abs(lx(i) - lx(i - 1)) > 1e-9:
            pts.append((lx(i - 1), y_bot))

    clean = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - clean[-1][0]) > 1e-9 or abs(p[1] - clean[-1][1]) > 1e-9:
            clean.append(p)
    if dogbone_r > 0:
        clean, arcs = _insert_dogbones(clean, dogbone_r)
    else:
        arcs = []
    return clean, arcs


def _tool_requirements(joinery, corner_style):
    lines = [
        "TOOL REQUIREMENTS",
        "─" * 45,
        "Profile / outline cuts:",
        f"  Bit: 1/2\" upcut spiral endmill",
        f"  Layer: {L_OUTLINE}",
        "",
        "Dado grooves (bottom panel slot):",
        f"  Bit: 1/2\" upcut spiral",
        f"  Layer: {L_DADO}",
        "",
        "Engraving / design:",
        f"  Pocket fills : layer {L_POCKET}",
        f"  Centerline   : layer {L_CENTER}",
        f"  Bit: 1/4\" V-bit or straight, depth {ENG['depth']}\"",
    ]
    if FEAT.get("rope_handles"):
        lines += ["", "Rope handle holes:",
                  f"  Bit: {FEAT.get('handle_hole_dia', 0.75)}\" brad-point or Forstner",
                  f"  Layer: {L_DRILL}"]
    if joinery == "box":
        lines += [
            "",
            "Finger joint slots (pocket through-cuts at each panel end):",
            f"  Bit: 1/2\" upcut spiral",
            f"  Layer: {L_FINGER}",
            f"  Slot width: {FINGER_W}\"  Slot depth: {MAT + CLEARANCE:.4f}\"",
            f"  Inner radius: {JOINERY_BIT_R}\" from bit — no dogbones needed",
            f"  Dado stops at joint zone ({MAT + CLEARANCE:.3f}\" from each end)",
        ]
        if corner_style == "dovetail":
            undercut = round(2 * MAT * math.tan(math.radians(14)), 3)
            lines += [
                "  *** DOVETAIL CUT — REPLACE STRAIGHT BIT BEFORE RUNNING FINGER SLOTS ***",
                "  Bit: 14 deg undercut/dovetail endmill, 1/2\" overall dia",
                f"  At {MAT}\" depth, base is ~{undercut}\" wider than face",
                f"  Clearance used: {CLEARANCE}\"",
            ]
    if RO_R > 0 and L_ROUND:
        lines += ["", "Edge roundover (lid / sides):",
                  f"  Radius: {RO_R}\"  Layer: {L_ROUND}",
                  "  Set up in Thermwood machine program"]
    return "\n".join(lines)


# ── Engraving designs ───────────────────────────────────────────────────────

def cross_pts(cx, cy, h):
    ah=h*.097; bh=h*.278; bvh=h*.097; bcy=cy+h*.167
    top=cy+h/2; bot=cy-h/2; bt=bcy+bvh; bb=bcy-bvh
    return [(cx-ah,top),(cx+ah,top),(cx+ah,bt),(cx+bh,bt),
            (cx+bh,bb),(cx+ah,bb),(cx+ah,bot),(cx-ah,bot),
            (cx-ah,bb),(cx-bh,bb),(cx-bh,bt),(cx-ah,bt)]

def add_cross(msp, cx, cy, h, layer):
    add_polygon(msp, cross_pts(cx, cy, h), layer)

def add_celtic_cross(msp, cx, cy, h, layer):
    add_polygon(msp, cross_pts(cx, cy, h), layer)
    add_arc_approx(msp, cx, cy+h*.167, h*.15, 0, 2*math.pi, 48, layer)

def add_ihs(msp, cx, cy, size, layer):
    s = size
    add_arc_approx(msp, cx, cy, s/2, 0, 2*math.pi, 72, layer)
    add_rect(msp, cx-s*.39, cy-s*.30, s*.08, s*.60, layer)
    add_rect(msp, cx-s*.19, cy-s*.30, s*.08, s*.60, layer)
    add_rect(msp, cx-s*.19, cy-s*.04, s*.38, s*.08, layer)
    add_rect(msp, cx+s*.11, cy-s*.30, s*.08, s*.60, layer)
    add_arc_approx(msp, cx+s*.32, cy+s*.09, s*.14, math.pi, 2*math.pi, 24, layer)
    add_arc_approx(msp, cx+s*.32, cy-s*.09, s*.14,        0,   math.pi, 24, layer)

def add_sacredheart(msp, cx, cy, h, layer):
    r = h * 0.28
    add_arc_approx(msp, cx-r*.5, cy+r*.1, r*.55,  0, math.pi, 32, layer)
    add_arc_approx(msp, cx+r*.5, cy+r*.1, r*.55,  0, math.pi, 32, layer)
    for p1, p2 in [((cx-r,cy+r*.1),(cx,cy-r*1.1)),((cx,cy-r*1.1),(cx+r,cy+r*.1))]:
        msp.add_line(p1, p2, dxfattribs={"layer": layer})
    add_cross(msp, cx, cy+r*1.3, h*.28, layer)
    for deg in range(0, 360, 30):
        a = math.radians(deg)
        msp.add_line((cx+r*1.15*math.cos(a), cy+r*1.15*math.sin(a)),
                     (cx+r*1.45*math.cos(a), cy+r*1.45*math.sin(a)),
                     dxfattribs={"layer": layer})

def add_chi_rho(msp, cx, cy, h, layer):
    """Chi-Rho (☧) — circle with P and X superimposed."""
    r = h * 0.45
    add_arc_approx(msp, cx, cy, r, 0, 2*math.pi, 72, layer)
    # Rho vertical staff
    msp.add_line((cx, cy-r*.7), (cx, cy+r*.7), dxfattribs={"layer": layer})
    # Rho loop (right bump at top of staff)
    add_arc_approx(msp, cx+r*.18, cy+r*.35, r*.28, -math.pi/2, math.pi/2, 24, layer)
    msp.add_line((cx, cy+r*.63), (cx, cy+r*.07), dxfattribs={"layer": layer})
    # Chi diagonals
    msp.add_line((cx-r*.5, cy-r*.5), (cx+r*.5, cy+r*.5), dxfattribs={"layer": layer})
    msp.add_line((cx+r*.5, cy-r*.5), (cx-r*.5, cy+r*.5), dxfattribs={"layer": layer})

_FONT_FAMILY = "Times New Roman"

_POCKET_DESIGNS     = {"cross", "sacredheart"}
_CENTERLINE_DESIGNS = {"celtic", "ihs", "chirho"}

def _item_layer(item):
    if item.get("type") == "text":
        return L_CENTER
    if item.get("type") == "custom_art":
        return L_POCKET if all(item.get("closed", [])) else L_CENTER
    d = item.get("design", "cross")
    return L_POCKET if d in _POCKET_DESIGNS else L_CENTER


def add_panel_design_items(msp, panel_W, panel_H, items):
    """Write a list of app-format item dicts onto a panel of given dimensions."""
    if not items:
        return
    for item in items:
        itype = item.get("type")
        if itype not in ("text", "design", "custom_art"):
            continue
        cx = panel_W * item.get("hpos", 0.5)
        cy = panel_H * item.get("vpos", 0.5)
        scale = item.get("scale", 1.0)
        rot   = item.get("rotation", 0.0)
        layer = _item_layer(item)
        if itype == "design":
            d = item.get("design", "cross")
            eng_h = min(panel_H * 0.68, 14.0, panel_W * 0.85) * scale
            if d == "cross":         add_cross(msp, cx, cy, eng_h, layer)
            elif d == "celtic":      add_celtic_cross(msp, cx, cy, eng_h, layer)
            elif d == "ihs":         add_ihs(msp, cx, cy, min(eng_h, 8.0), layer)
            elif d == "sacredheart": add_sacredheart(msp, cx, cy, eng_h, layer)
            elif d == "chirho":      add_chi_rho(msp, cx, cy, eng_h, layer)
        elif itype == "text":
            text = item.get("text", "").strip()
            if not text:
                continue
            txt_h = min(panel_H * 0.055, 1.1) * scale
            add_text_as_geometry(msp, text, cx, cy, txt_h, layer, rotation=rot)
        elif itype == "custom_art":
            paths  = item.get("paths", [])
            closed = item.get("closed", [])
            if not paths:
                continue
            all_closed = all(closed) if closed else False
            art_layer  = L_POCKET if all_closed else L_CENTER
            art_size   = min(panel_W, panel_H) * 0.65 * scale
            rot_rad    = math.radians(rot)
            cos_r, sin_r = math.cos(rot_rad), math.sin(rot_rad)
            for pi_idx, path in enumerate(paths):
                if len(path) < 2:
                    continue
                transformed = []
                for nx, ny in path:
                    px = (nx - 0.5) * art_size
                    py = (ny - 0.5) * art_size
                    transformed.append((px * cos_r - py * sin_r + cx,
                                        px * sin_r + py * cos_r + cy))
                for k in range(len(transformed) - 1):
                    msp.add_line(transformed[k], transformed[k + 1],
                                 dxfattribs={"layer": art_layer})
                is_closed = closed[pi_idx] if pi_idx < len(closed) else False
                if is_closed and len(transformed) >= 3:
                    msp.add_line(transformed[-1], transformed[0],
                                 dxfattribs={"layer": art_layer})


def _render_text_line(msp, text, cx, cy, height, layer, rotation=0.0):
    try:
        from ezdxf.addons import text2path
        from ezdxf.fonts.fonts import FontFace
        font  = FontFace(family=_FONT_FAMILY)
        paths = text2path.make_paths_from_str(
            text, font=font, size=height,
            align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER,
        )
        m = Matrix44.translate(cx, cy, 0)
        if rotation:
            m = m @ Matrix44.z_rotate(math.radians(rotation))
        count = 0
        for p in paths:
            p = p.transform(m)
            pts = list(p.flattening(0.02))
            for i in range(len(pts)-1):
                s, e = pts[i], pts[i+1]
                if abs(s.x-e.x) < 1e-6 and abs(s.y-e.y) < 1e-6:
                    continue
                msp.add_line((s.x, s.y), (e.x, e.y), dxfattribs={"layer": layer})
                count += 1
        return count > 0
    except Exception:
        return False

def add_text_as_geometry(msp, text, cx, cy, height, layer, rotation=0.0):
    lines = [l for l in text.split("\n") if l.strip()]
    if not lines:
        return False
    spacing = height * 1.5
    total_h = spacing * (len(lines) - 1)
    any_ok  = False
    for i, line in enumerate(lines):
        line_y = cy + total_h / 2 - i * spacing
        ok = _render_text_line(msp, line, cx, line_y, height, layer, rotation=rotation)
        if ok:
            any_ok = True
        else:
            msp.add_text(
                line, dxfattribs={"height": height, "layer": L_NOTES}
            ).set_placement((cx, line_y),
                            align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    return any_ok

# ── Dimension helpers ───────────────────────────────────────────────────────

def add_dim_horiz(msp, x1, x2, y, label=None, layer=L_NOTES):
    """Horizontal dimension line with tick marks."""
    msp.add_line((x1, y-0.2), (x1, y+0.2), dxfattribs={"layer": layer})
    msp.add_line((x2, y-0.2), (x2, y+0.2), dxfattribs={"layer": layer})
    msp.add_line((x1, y), (x2, y), dxfattribs={"layer": layer})
    mid = (x1+x2)/2
    txt = label or f"{abs(x2-x1):.3f}\""
    msp.add_text(txt, dxfattribs={"height": 0.35, "layer": layer})\
       .set_placement((mid, y+0.3), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

def add_dim_vert(msp, x, y1, y2, label=None, layer=L_NOTES):
    msp.add_line((x-0.2, y1), (x+0.2, y1), dxfattribs={"layer": layer})
    msp.add_line((x-0.2, y2), (x+0.2, y2), dxfattribs={"layer": layer})
    msp.add_line((x, y1), (x, y2), dxfattribs={"layer": layer})
    mid = (y1+y2)/2
    txt = label or f"{abs(y2-y1):.3f}\""
    msp.add_text(txt, dxfattribs={"height": 0.35, "layer": layer})\
       .set_placement((x+0.4, mid), align=ezdxf.enums.TextEntityAlignment.MIDDLE_LEFT)

# ── Panel generators ────────────────────────────────────────────────────────

FINGER_W        = J.get("finger_width", MAT)
CLEARANCE       = J.get("clearance", 0.008)
JOINERY_BIT_R   = J.get("joinery_bit_diameter", 0.5) / 2
def dims(sz):
    il,iw,id_ = sz["int_L"], sz["int_W"], sz["int_D"]
    m = MAT
    return dict(
        int_L=il, int_W=iw, int_D=id_,
        side_L    = il + 2*m,
        side_H    = id_ + m,
        end_W     = iw,
        end_W_box = iw + 2*m,
        end_H     = id_ + m,
        bot_L     = il,
        bot_W     = iw,
        lid_L     = il + 2*m,
        lid_W     = iw + 2*m,
        cleat_L   = il,
        cleat_S   = iw - 2*m,
        cleat_H   = m,
        dado_y    = J["dado_from_bottom"],
    )

def new_doc(): return ezdxf.new("R2010")

def make_side_panel(d, joinery="rabbet", with_dims=True, corner_style="dogbone", items=None):
    doc = new_doc(); msp = doc.modelspace()
    if joinery == "box":
        dogbone_r = JOINERY_BIT_R if corner_style == "dogbone" else 0.0
        pts, arcs = box_joint_outline(d["side_L"], d["side_H"], MAT, FINGER_W,
                                      side_tabs_first=True, clearance=CLEARANCE, dogbone_r=dogbone_r)
        add_polygon_with_dogbones(msp, pts, arcs, L_OUTLINE)
        if corner_style == "dovetail":
            msp.add_text("DOVETAIL CUT — 14° undercut bit 1/2\" dia — see order_summary.txt",
                         dxfattribs={"height": 0.3, "layer": L_NOTES})\
               .set_placement((d["side_L"]/2, -0.5),
                              align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
        add_rect(msp, MAT, d["dado_y"], d["side_L"] - 2*MAT, MAT, L_DADO)
    else:
        add_rect(msp, 0, 0, d["side_L"], d["side_H"], L_OUTLINE)
        add_rect(msp, MAT, d["dado_y"], d["side_L"] - 2*MAT, MAT, L_DADO)
        add_rect(msp, 0, 0, MAT, d["side_H"], L_DADO2)
        add_rect(msp, d["side_L"]-MAT, 0, MAT, d["side_H"], L_DADO3)
    # Rope handle holes (drilled from inside face through panel thickness)
    if FEAT.get("rope_handles", False):
        h_r      = FEAT.get("handle_hole_dia", 0.75) / 2
        setback  = FEAT.get("handle_setback", 12.0)
        cy_h     = d["side_H"] / 2
        for hx in [setback, d["side_L"] - setback]:
            msp.add_circle(center=(hx, cy_h), radius=h_r,
                           dxfattribs={"layer": L_DRILL})

    # Stretcher dado — cross-dado at midpoint for long caskets
    if FEAT.get("stretcher", False) and d["side_L"] >= FEAT.get("stretcher_min_length", 65.0):
        stretch_h = J["dado_from_bottom"]
        sx = d["side_L"] / 2 - MAT / 2
        add_rect(msp, sx, 0, MAT, stretch_h, L_DADO4)

    # Grain direction arrow (Notes layer — always runs lengthwise)
    mid_y = d["side_H"] / 2
    gx    = d["side_L"] * 0.05
    msp.add_line((gx, mid_y), (gx + d["side_L"]*0.08, mid_y),
                 dxfattribs={"layer": L_NOTES})
    msp.add_text("← GRAIN", dxfattribs={"height": 0.3, "layer": L_NOTES})\
       .set_placement((gx, mid_y + 0.4),
                      align=ezdxf.enums.TextEntityAlignment.LEFT)

    if L_ROUND and FEAT.get("roundover_sides", False):
        msp.add_line((0, 0), (d["side_L"], 0), dxfattribs={"layer": L_ROUND})
        msp.add_line((0, d["side_H"]), (d["side_L"], d["side_H"]), dxfattribs={"layer": L_ROUND})

    if FEAT.get("lid_pins", False):
        psb  = FEAT.get("pin_setback", 1.5)
        pd   = FEAT.get("pin_dia", 0.375)
        msp.add_text(
            f"Lid pins: hand-drill dia {pd}\" blind holes into TOP EDGE at "
            f"{psb}\" and {d['side_L']-psb:.3f}\" from head end",
            dxfattribs={"height": 0.3, "layer": L_NOTES}
        ).set_placement((d["side_L"]/2, -1.8),
                        align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

    add_panel_design_items(msp, d["side_L"], d["side_H"], items)

    if with_dims:
        add_dim_horiz(msp, 0, d["side_L"], -1.0)
        add_dim_vert(msp,  -1.0, 0, d["side_H"])
        jlabel = f"BOX JOINT/{corner_style.upper()}" if joinery == "box" else "RABBET"
        msp.add_text(f"SIDE PANEL x2 — INSIDE FACE UP [{jlabel}]",
                     dxfattribs={"height": 0.4, "layer": L_NOTES})\
           .set_placement((d["side_L"]/2, d["side_H"]+0.8),
                          align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    return doc

def make_end_panel(d, joinery="rabbet", with_dims=True, corner_style="dogbone", items=None):
    doc = new_doc(); msp = doc.modelspace()
    if joinery == "box":
        ew = d["end_W_box"]
        dogbone_r = JOINERY_BIT_R if corner_style == "dogbone" else 0.0
        pts, arcs = box_joint_outline(ew, d["end_H"], MAT, FINGER_W,
                                      side_tabs_first=False, clearance=CLEARANCE, dogbone_r=dogbone_r)
        add_polygon_with_dogbones(msp, pts, arcs, L_OUTLINE)
        if corner_style == "dovetail":
            msp.add_text("DOVETAIL CUT — 14° undercut bit 1/2\" dia — see order_summary.txt",
                         dxfattribs={"height": 0.3, "layer": L_NOTES})\
               .set_placement((ew/2, -0.5),
                              align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
        add_rect(msp, MAT, d["dado_y"], d["end_W"], MAT, L_DADO)
    else:
        ew = d["end_W"]
        add_rect(msp, 0, 0, ew, d["end_H"], L_OUTLINE)
        add_rect(msp, 0, d["dado_y"], ew, MAT, L_DADO)

    mid_y_ep = d["end_H"] / 2
    gx_ep    = ew * 0.05
    msp.add_line((gx_ep, mid_y_ep), (gx_ep + ew*0.12, mid_y_ep),
                 dxfattribs={"layer": L_NOTES})
    msp.add_text("← GRAIN", dxfattribs={"height": 0.3, "layer": L_NOTES})\
       .set_placement((gx_ep, mid_y_ep + 0.4),
                      align=ezdxf.enums.TextEntityAlignment.LEFT)

    add_panel_design_items(msp, ew, d["end_H"], items)

    if with_dims:
        add_dim_horiz(msp, 0, ew, -1.0)
        add_dim_vert(msp,  -1.0, 0, d["end_H"])
        jlabel = f"BOX JOINT/{corner_style.upper()}" if joinery == "box" else "RABBET"
        msp.add_text(f"END PANEL x2 — INSIDE FACE UP [{jlabel}]",
                     dxfattribs={"height": 0.4, "layer": L_NOTES})\
           .set_placement((ew/2, d["end_H"]+0.8),
                          align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    return doc

def make_bottom(d, joinery="rabbet", with_dims=True):
    doc = new_doc(); msp = doc.modelspace()
    if joinery == "box":
        m  = MAT
        bL, bW = d["bot_L"], d["bot_W"]
        add_polygon(msp, [
            (m,    0),   (bL-m, 0),
            (bL,   m),   (bL,   bW-m),
            (bL-m, bW),  (m,    bW),
            (0,    bW-m),(0,    m),
        ], L_OUTLINE)
    else:
        add_rect(msp, 0, 0, d["bot_L"], d["bot_W"], L_OUTLINE)

    mid_y_bt = d["bot_W"] / 2
    gx_bt    = d["bot_L"] * 0.05
    msp.add_line((gx_bt, mid_y_bt), (gx_bt + d["bot_L"]*0.08, mid_y_bt),
                 dxfattribs={"layer": L_NOTES})
    msp.add_text("← GRAIN", dxfattribs={"height": 0.3, "layer": L_NOTES})\
       .set_placement((gx_bt, mid_y_bt + 0.4),
                      align=ezdxf.enums.TextEntityAlignment.LEFT)

    if with_dims:
        add_dim_horiz(msp, 0, d["bot_L"], -1.0)
        add_dim_vert(msp,  -1.0, 0, d["bot_W"])
        msp.add_text("BOTTOM PANEL x1",
                     dxfattribs={"height": 0.4, "layer": L_NOTES})\
           .set_placement((d["bot_L"]/2, d["bot_W"]+0.8),
                          align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    return doc

def make_lid(d, design="cross", custom_text=None, with_dims=True,
             design_layer=None, text_vpos=None, text_hpos=None, text_scale=1.0,
             text_rotate=0.0, lid_L_override=None, piece_label=None,
             design_vpos=None, design_hpos=None, design_scale=1.0):
    doc = new_doc(); msp = doc.modelspace()
    lid_L = lid_L_override if lid_L_override is not None else d["lid_L"]
    add_rect(msp, 0, 0, lid_L, d["lid_W"], L_OUTLINE)

    # Design position — use provided vpos/hpos if given, else center
    if design_hpos is not None:
        cx = lid_L * design_hpos
    else:
        cx = lid_L / 2
    if design_vpos is not None:
        cy = d["lid_W"] * design_vpos
    else:
        cy = d["lid_W"] / 2

    eng_h     = min(d["lid_W"] * 0.68, 16.0, lid_L * 0.85) * (design_scale or 1.0)
    eng_layer = L_CENTER if design_layer == "centerline" else L_POCKET

    if design == "cross":
        add_cross(msp, cx, cy, eng_h, eng_layer)
    elif design == "celtic":
        add_celtic_cross(msp, cx, cy, eng_h, eng_layer)
    elif design == "ihs":
        add_ihs(msp, cx, cy, min(eng_h, 9.0), eng_layer)
    elif design == "sacredheart":
        add_sacredheart(msp, cx, cy, eng_h, eng_layer)
    elif design == "chirho":
        add_chi_rho(msp, cx, cy, eng_h, eng_layer)
    # design == "none" → no engraving on this piece

    insc = custom_text or (ENG.get("inscription", "") if design != "none" else "")
    text_ok = False
    if insc:
        txt_h = min(d["lid_W"] * 0.055, 1.1) * text_scale
        txt_x = lid_L * text_hpos if text_hpos is not None else cx
        txt_y = d["lid_W"] * text_vpos if text_vpos is not None else txt_h * 2.2
        text_ok = add_text_as_geometry(msp, insc, txt_x, txt_y, txt_h, L_CENTER, rotation=text_rotate)

    # Grain arrow
    msp.add_text("← GRAIN", dxfattribs={"height": 0.3, "layer": L_NOTES})\
       .set_placement((lid_L * 0.05, d["lid_W"] * 0.08),
                      align=ezdxf.enums.TextEntityAlignment.LEFT)

    if L_ROUND and FEAT.get("roundover_lid_all", False):
        msp.add_line((0, 0), (lid_L, 0), dxfattribs={"layer": L_ROUND})
        msp.add_line((lid_L, 0), (lid_L, d["lid_W"]), dxfattribs={"layer": L_ROUND})
        msp.add_line((lid_L, d["lid_W"]), (0, d["lid_W"]), dxfattribs={"layer": L_ROUND})
        msp.add_line((0, d["lid_W"]), (0, 0), dxfattribs={"layer": L_ROUND})

    if FEAT.get("lid_pins", False):
        pr   = FEAT.get("pin_dia", 0.375) / 2
        psb  = FEAT.get("pin_setback", 1.5)
        for px, py in [(psb, psb), (lid_L-psb, psb),
                       (lid_L-psb, d["lid_W"]-psb), (psb, d["lid_W"]-psb)]:
            msp.add_circle(center=(px, py), radius=pr,
                           dxfattribs={"layer": L_DRILL_HALF})
        msp.add_text(
            f"Lid pins: 4x dia {FEAT['pin_dia']}\" blind holes — cut FACE DOWN",
            dxfattribs={"height": 0.3, "layer": L_NOTES}
        ).set_placement((lid_L/2, -1.8), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

    if with_dims:
        add_dim_horiz(msp, 0, lid_L, -1.0)
        add_dim_vert(msp,  -1.0, 0, d["lid_W"])
        lbl  = piece_label or "LID x1"
        status = "geometry" if text_ok else ("ref only" if insc else "no text")
        msp.add_text(
            f"{lbl} — {design.upper()} | text: {status}",
            dxfattribs={"height": 0.4, "layer": L_NOTES}
        ).set_placement((lid_L/2, d["lid_W"]+0.8),
                        align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    return doc

def make_cleat(length, d, label="CLEAT"):
    doc = new_doc(); msp = doc.modelspace()
    add_rect(msp, 0, 0, length, d["cleat_H"], L_OUTLINE)
    msp.add_text(label, dxfattribs={"height": 0.3, "layer": L_NOTES})\
       .set_placement((length/2, d["cleat_H"]+0.4),
                      align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    return doc

def make_stretcher(d):
    """Cross-brace that slots into dado4 grooves at the midpoint of both side panels.
    Sits below the bottom panel level to avoid blocking the interior."""
    doc = new_doc(); msp = doc.modelspace()
    sh = J["dado_from_bottom"]
    sl = d["int_W"]
    add_rect(msp, 0, 0, sl, sh, L_OUTLINE)
    msp.add_text("STRETCHER x1 — dado4 slot at side panel midpoints",
                 dxfattribs={"height": 0.3, "layer": L_NOTES})\
       .set_placement((sl/2, sh+0.4),
                      align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    return doc

# ── Coffin (hexagonal / 6-sided tapered shape) ──────────────────────────────

def _offset_convex_polygon(pts, dist):
    n = len(pts)
    signed_area = sum(
        pts[i][0]*pts[(i+1)%n][1] - pts[(i+1)%n][0]*pts[i][1]
        for i in range(n)
    ) / 2
    sign = 1 if signed_area > 0 else -1
    edges = []
    for i in range(n):
        p1, p2 = pts[i], pts[(i+1)%n]
        dx, dy = p2[0]-p1[0], p2[1]-p1[1]
        L = math.sqrt(dx*dx + dy*dy)
        if L < 1e-9: continue
        nx, ny = sign*dy/L*dist, -sign*dx/L*dist
        edges.append(((p1[0]+nx, p1[1]+ny), (p2[0]+nx, p2[1]+ny)))
    result = []
    m = len(edges)
    for i in range(m):
        e1, e2 = edges[i], edges[(i+1)%m]
        p1, p2, p3, p4 = e1[0], e1[1], e2[0], e2[1]
        dx1, dy1 = p2[0]-p1[0], p2[1]-p1[1]
        dx2, dy2 = p4[0]-p3[0], p4[1]-p3[1]
        denom = dx1*dy2 - dy1*dx2
        if abs(denom) < 1e-9:
            result.append(((p2[0]+p3[0])/2, (p2[1]+p3[1])/2))
        else:
            t = ((p3[0]-p1[0])*dy2 - (p3[1]-p1[1])*dx2) / denom
            result.append((p1[0]+t*dx1, p1[1]+t*dy1))
    return result


def coffin_dims(sz, overrides=None):
    il, iw, id_ = sz["int_L"], sz["int_W"], sz["int_D"]
    # Always start from proportionally-scaled values (preserves adult shape across sizes)
    # Adult reference: iw=22, il=78, head_W=14 (63.6%), foot_W=12 (54.5%), sfh=sff=16 (20.5%)
    _base_iw = 22.0; _base_il = 78.0
    hw  = 14.0 * (iw / _base_iw)
    fw  = 12.0 * (iw / _base_iw)
    sfh = 16.0 * (il / _base_il)
    sff = 16.0 * (il / _base_il)
    # Explicit overrides (from app form or CLI) take priority over auto-scaling
    ovr = overrides or {}
    hw  = ovr.get("head_W",             hw)
    fw  = ovr.get("foot_W",             fw)
    sfh = ovr.get("shoulder_from_head", sfh)
    sff = ovr.get("shoulder_from_foot", sff)
    # Clamp: head/foot must be narrower than shoulder
    hw  = min(hw, iw - 0.5)
    fw  = min(fw, iw - 0.5)
    ho  = (iw - hw) / 2
    fo  = (iw - fw) / 2
    pts_int = [
        (0,    ho),
        (sfh,  0),
        (il,   fo),
        (il,   iw-fo),
        (sfh,  iw),
        (0,    iw-ho),
    ]
    pts_ext = _offset_convex_polygon(pts_int, MAT)
    return dict(
        int_L=il, int_W=iw, int_D=id_,
        head_W=hw, foot_W=fw,
        shoulder_from_head=sfh, shoulder_from_foot=sff,
        head_offset=ho, foot_offset=fo,
        panel_H=id_ + MAT,
        pts_int=pts_int, pts_ext=pts_ext,
        sh_head_L=math.sqrt(sfh**2 + ho**2),
        sh_foot_L=math.sqrt(sff**2 + fo**2),
        side_L=il - sfh - sff,
        head_angle=math.degrees(math.atan2(ho, sfh)),
        foot_angle=math.degrees(math.atan2(fo, sff)),
        dado_y=J["dado_from_bottom"],
    )


def _coffin_head_note(msp, w, h, label):
    msp.add_text("← GRAIN", dxfattribs={"height": 0.3, "layer": L_NOTES})\
       .set_placement((w*0.10, h/2+0.4), align=ezdxf.enums.TextEntityAlignment.LEFT)
    msp.add_text(label, dxfattribs={"height": 0.4, "layer": L_NOTES})\
       .set_placement((w/2, h+0.8), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)


def _coffin_box_joint(msp, total_L, panel_H, joinery, corner_style, side_tabs_first):
    if joinery != "box":
        add_rect(msp, 0, 0, total_L, panel_H, L_OUTLINE)
        return
    dogbone_r = JOINERY_BIT_R if corner_style == "dogbone" else 0.0
    pts, arcs = box_joint_outline(total_L, panel_H, MAT, FINGER_W,
                                  side_tabs_first=side_tabs_first, clearance=CLEARANCE,
                                  dogbone_r=dogbone_r)
    add_polygon_with_dogbones(msp, pts, arcs, L_OUTLINE)
    if corner_style == "dovetail":
        msp.add_text("DOVETAIL CUT — 14° undercut bit 1/2\" dia — see order_summary.txt",
                     dxfattribs={"height": 0.3, "layer": L_NOTES})\
           .set_placement((total_L/2, -0.5),
                          align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)


def make_coffin_head(dc, joinery="rabbet", with_dims=True, corner_style="dogbone", items=None):
    doc = new_doc(); msp = doc.modelspace()
    w, h = dc["head_W"], dc["panel_H"]
    _coffin_box_joint(msp, w, h, joinery, corner_style, side_tabs_first=True)
    _sd = (MAT + CLEARANCE) if joinery == "box" else MAT
    add_rect(msp, _sd, dc["dado_y"], w - 2*_sd, MAT, L_DADO)
    add_panel_design_items(msp, w, h, items)
    if with_dims:
        add_dim_horiz(msp, 0, w, -1.0); add_dim_vert(msp, -1.0, 0, h)
        jlabel = f"BOX JOINT/{corner_style.upper()}" if joinery == "box" else "RABBET"
        _coffin_head_note(msp, w, h, f"COFFIN HEAD x1 — inside face up [{jlabel}]")
    return doc


def make_coffin_foot(dc, joinery="rabbet", with_dims=True, corner_style="dogbone", items=None):
    doc = new_doc(); msp = doc.modelspace()
    w, h = dc["foot_W"], dc["panel_H"]
    _coffin_box_joint(msp, w, h, joinery, corner_style, side_tabs_first=True)
    _sd = (MAT + CLEARANCE) if joinery == "box" else MAT
    add_rect(msp, _sd, dc["dado_y"], w - 2*_sd, MAT, L_DADO)
    add_panel_design_items(msp, w, h, items)
    if with_dims:
        add_dim_horiz(msp, 0, w, -1.0); add_dim_vert(msp, -1.0, 0, h)
        jlabel = f"BOX JOINT/{corner_style.upper()}" if joinery == "box" else "RABBET"
        _coffin_head_note(msp, w, h, f"COFFIN FOOT x1 — inside face up [{jlabel}]")
    return doc


def make_coffin_shoulder(dc, at="head", joinery="rabbet", with_dims=True, corner_style="dogbone", items=None):
    doc = new_doc(); msp = doc.modelspace()
    pl = dc["sh_head_L"] if at == "head" else dc["sh_foot_L"]
    ang = dc["head_angle"] if at == "head" else dc["foot_angle"]
    h = dc["panel_H"]
    _coffin_box_joint(msp, pl, h, joinery, corner_style, side_tabs_first=False)
    _sd = (MAT + CLEARANCE) if joinery == "box" else MAT
    add_rect(msp, _sd, dc["dado_y"], pl - 2*_sd, MAT, L_DADO)
    add_panel_design_items(msp, pl, h, items)
    msp.add_text("← GRAIN", dxfattribs={"height": 0.3, "layer": L_NOTES})\
       .set_placement((pl*0.05, h/2+0.4), align=ezdxf.enums.TextEntityAlignment.LEFT)
    if joinery == "box":
        msp.add_text(
            f"BEVEL NOTE: trim tab end-faces to {ang:.1f}deg — both ends proud after straight cut",
            dxfattribs={"height": 0.3, "layer": L_NOTES}
        ).set_placement((pl/2, h+1.3), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    if with_dims:
        add_dim_horiz(msp, 0, pl, -1.0); add_dim_vert(msp, -1.0, 0, h)
        jlabel = f"BOX JOINT/{corner_style.upper()}" if joinery == "box" else "RABBET"
        msp.add_text(
            f"COFFIN SHOULDER ({at.upper()}) x2 — bevel {ang:.1f}deg both ends [{jlabel}]",
            dxfattribs={"height": 0.4, "layer": L_NOTES}
        ).set_placement((pl/2, h+0.8), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    return doc


def make_coffin_side(dc, joinery="rabbet", with_dims=True, corner_style="dogbone", items=None):
    doc = new_doc(); msp = doc.modelspace()
    sl, h = dc["side_L"], dc["panel_H"]
    _coffin_box_joint(msp, sl, h, joinery, corner_style, side_tabs_first=True)
    _sd = (MAT + CLEARANCE) if joinery == "box" else MAT
    add_rect(msp, _sd, dc["dado_y"], sl - 2*_sd, MAT, L_DADO)
    if FEAT.get("rope_handles", False) and sl >= 2*FEAT.get("handle_setback", 12.0):
        h_r = FEAT.get("handle_hole_dia", 0.75)/2
        sb  = FEAT.get("handle_setback", 12.0)
        for hx in [sb, sl-sb]:
            msp.add_circle(center=(hx, h/2), radius=h_r, dxfattribs={"layer": L_DRILL})
    add_panel_design_items(msp, sl, h, items)
    msp.add_text("← GRAIN", dxfattribs={"height": 0.3, "layer": L_NOTES})\
       .set_placement((sl*0.05, h/2+0.4), align=ezdxf.enums.TextEntityAlignment.LEFT)
    if with_dims:
        add_dim_horiz(msp, 0, sl, -1.0); add_dim_vert(msp, -1.0, 0, h)
        jlabel = f"BOX JOINT/{corner_style.upper()}" if joinery == "box" else "RABBET"
        msp.add_text(f"COFFIN SIDE x2 — inside face up [{jlabel}]",
                     dxfattribs={"height": 0.4, "layer": L_NOTES})\
           .set_placement((sl/2, h+0.8), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    return doc


def _coffin_bottom_box_pts(pts, notch):
    n = len(pts)
    result = []
    for i in range(n):
        curr = pts[i]
        prev = pts[(i - 1) % n]
        nxt  = pts[(i + 1) % n]
        dx_in  = curr[0] - prev[0];  dy_in  = curr[1] - prev[1]
        L_in   = math.sqrt(dx_in**2 + dy_in**2)
        dx_out = nxt[0]  - curr[0];  dy_out = nxt[1]  - curr[1]
        L_out  = math.sqrt(dx_out**2 + dy_out**2)
        p_in  = (curr[0] - dx_in /L_in *notch, curr[1] - dy_in /L_in *notch) if L_in  > notch else prev
        p_out = (curr[0] + dx_out/L_out*notch, curr[1] + dy_out/L_out*notch) if L_out > notch else nxt
        result.append(p_in)
        result.append(p_out)
    return result


def make_coffin_bottom(dc, joinery="rabbet", with_dims=True):
    doc = new_doc(); msp = doc.modelspace()
    if joinery == "box":
        notched_pts = _coffin_bottom_box_pts(dc["pts_int"], MAT + CLEARANCE)
        add_polygon(msp, notched_pts, L_OUTLINE)
    else:
        add_polygon(msp, dc["pts_int"], L_OUTLINE)
    mid_y = dc["int_W"]/2
    gx = dc["int_L"]*0.05
    msp.add_line((gx, mid_y), (gx+dc["int_L"]*0.08, mid_y), dxfattribs={"layer": L_NOTES})
    msp.add_text("← GRAIN", dxfattribs={"height": 0.3, "layer": L_NOTES})\
       .set_placement((gx, mid_y+0.4), align=ezdxf.enums.TextEntityAlignment.LEFT)
    if with_dims:
        add_dim_horiz(msp, 0, dc["int_L"], -1.0)
        add_dim_vert(msp, -1.0, 0, dc["int_W"])
        msp.add_text(
            f"COFFIN BOTTOM x1  {dc['int_L']:.1f}\" x {dc['int_W']:.1f}\" (6-sided) — sits on cleats",
            dxfattribs={"height": 0.4, "layer": L_NOTES}
        ).set_placement((dc["int_L"]/2, dc["int_W"]+0.8),
                        align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    return doc


def make_coffin_lid(dc, design="cross", custom_text=None, with_dims=True,
                    design_layer=None, text_vpos=None, text_hpos=None, text_scale=1.0,
                    text_rotate=0.0,
                    design_vpos=None, design_hpos=None, design_scale=1.0):
    doc = new_doc(); msp = doc.modelspace()
    pts = dc["pts_ext"]
    add_polygon(msp, pts, L_OUTLINE)
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    lid_L, lid_W = x1-x0, y1-y0
    cx = x0 + lid_L * (design_hpos if design_hpos is not None else 0.5)
    cy = y0 + lid_W * (design_vpos if design_vpos is not None else 0.5)
    eng_h = min(lid_W*0.60, 16.0, lid_L*0.75) * (design_scale or 1.0)
    eng_layer = L_CENTER if design_layer == "centerline" else L_POCKET
    if design == "cross":       add_cross(msp, cx, cy, eng_h, eng_layer)
    elif design == "celtic":    add_celtic_cross(msp, cx, cy, eng_h, eng_layer)
    elif design == "ihs":       add_ihs(msp, cx, cy, min(eng_h, 9.0), eng_layer)
    elif design == "sacredheart": add_sacredheart(msp, cx, cy, eng_h, eng_layer)
    elif design == "chirho":    add_chi_rho(msp, cx, cy, eng_h, eng_layer)
    insc = custom_text or ENG.get("inscription", "")
    text_ok = False
    if insc:
        txt_h = min(lid_W*0.055, 1.1) * text_scale
        txt_x = x0 + lid_L * (text_hpos if text_hpos is not None else 0.5)
        txt_y = y0 + lid_W * (text_vpos if text_vpos is not None else 0.10)
        text_ok = add_text_as_geometry(msp, insc, txt_x, txt_y, txt_h, L_CENTER, rotation=text_rotate)
    msp.add_text("← GRAIN", dxfattribs={"height": 0.3, "layer": L_NOTES})\
       .set_placement((x0+lid_L*0.05, y0+lid_W*0.08), align=ezdxf.enums.TextEntityAlignment.LEFT)
    if L_ROUND and FEAT.get("roundover_lid_all", False):
        for i in range(len(pts)):
            msp.add_line(pts[i], pts[(i+1)%len(pts)], dxfattribs={"layer": L_ROUND})
    if with_dims:
        add_dim_horiz(msp, x0, x1, y0-1.0)
        add_dim_vert(msp, x0-1.0, y0, y1)
        status = "geometry" if text_ok else ("ref only" if insc else "no text")
        msp.add_text(f"COFFIN LID x1 — {design.upper()} | text: {status}",
                     dxfattribs={"height": 0.4, "layer": L_NOTES})\
           .set_placement((cx, y1+0.8), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    return doc


def generate_coffin_size(size_name, size_cfg, design="cross", custom_text=None,
                          customer=None, design_layer=None,
                          text_vpos=None, text_hpos=None, text_scale=1.0,
                          text_rotate=0.0,
                          joinery="rabbet", corner_style="dogbone",
                          design_vpos=None, design_hpos=None, design_scale=1.0,
                          coffin_overrides=None, panel_items=None):
    if coffin_overrides:
        size_cfg = dict(size_cfg)
    dc = coffin_dims(size_cfg, overrides=coffin_overrides)
    pi = panel_items or {}
    if joinery == "box":
        print(f"  Note: shoulder tabs cut square — bevel {dc['head_angle']:.1f}deg (head) / {dc['foot_angle']:.1f}deg (foot) before assembly")
    import datetime
    date_str = datetime.date.today().strftime("%Y%m%d")
    if customer:
        safe = "".join(c for c in customer if c.isalnum() or c in " _-").strip().replace(" ", "_")
        folder = f"{safe}_{size_name}_coffin_{design}_{date_str}"
        out = os.path.join(DXF_ROOT, "orders", folder)
    else:
        out = os.path.join(DXF_ROOT, f"{size_name}_coffin")
    os.makedirs(out, exist_ok=True)
    kw = dict(design_layer=design_layer, text_vpos=text_vpos,
              text_hpos=text_hpos, text_scale=text_scale, text_rotate=text_rotate,
              design_vpos=design_vpos, design_hpos=design_hpos, design_scale=design_scale)
    parts = {
        "coffin_head":          make_coffin_head(dc, joinery, corner_style=corner_style, items=pi.get("coffin_head")),
        "coffin_foot":          make_coffin_foot(dc, joinery, corner_style=corner_style, items=pi.get("coffin_foot")),
        "coffin_shoulder_head": make_coffin_shoulder(dc, at="head", joinery=joinery, corner_style=corner_style, items=pi.get("coffin_sh_head")),
        "coffin_shoulder_foot": make_coffin_shoulder(dc, at="foot", joinery=joinery, corner_style=corner_style, items=pi.get("coffin_sh_foot")),
        "coffin_side":          make_coffin_side(dc, joinery, corner_style=corner_style, items=pi.get("coffin_side")),
        "coffin_bottom":        make_coffin_bottom(dc, joinery=joinery),
    }
    # Only generate the selected design lid
    parts[f"coffin_lid_{design}"] = make_coffin_lid(dc, design, custom_text, **kw)
    for name, doc in parts.items():
        doc.saveas(os.path.join(out, f"{name}.dxf"))
    print(f"\n{'='*60}")
    print(f"  COFFIN {size_name.upper()}  ({dc['int_L']}\" x {dc['int_W']}\" x {dc['int_D']}\"  interior)")
    print(f"  Head {dc['head_W']}\"  Shoulder {dc['int_W']}\"  Foot {dc['foot_W']}\"")
    print(f"  Shoulder break: {dc['shoulder_from_head']}\" from head  {dc['shoulder_from_foot']}\" from foot")
    print(f"  Bevel: head={dc['head_angle']:.1f}deg  foot={dc['foot_angle']:.1f}deg")
    print(f"  Panels:")
    print(f"    Head              x1   {dc['head_W']:.2f}\" x {dc['panel_H']:.2f}\"")
    print(f"    Foot              x1   {dc['foot_W']:.2f}\" x {dc['panel_H']:.2f}\"")
    print(f"    Shoulder (head)   x2   {dc['sh_head_L']:.2f}\" x {dc['panel_H']:.2f}\"")
    print(f"    Shoulder (foot)   x2   {dc['sh_foot_L']:.2f}\" x {dc['panel_H']:.2f}\"")
    print(f"    Side              x2   {dc['side_L']:.2f}\" x {dc['panel_H']:.2f}\"")
    print(f"    Bottom            x1   6-sided {dc['int_L']:.1f}\" x {dc['int_W']:.1f}\"")
    print(f"    Lid               x1   6-sided exterior footprint")
    sheet_cost  = CFG["sheet"].get("cost", 45.0)
    consumables = FEAT.get("consumables_cost", 15.0)
    n_sheets = 3 if dc["int_L"] > 60 else 2
    mat_cost = n_sheets * sheet_cost + consumables
    print(f"  Material COGS (est): ~{n_sheets} sheets x ${sheet_cost:.2f} + ${consumables:.2f} = ${mat_cost:.2f}")
    print(f"  DXFs: {out}")
    import datetime as _dt
    coffin_summary = [
        "ORDER SUMMARY — COFFIN",
        f"Generated:   {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Customer:    {customer or '—'}",
        f"Size:        {size_name.title()}  ({dc['int_L']}\" x {dc['int_W']}\" x {dc['int_D']}\" interior)",
        f"Shape:       Coffin (6-sided tapered)",
        f"Head: {dc['head_W']}\"  Shoulder: {dc['int_W']}\"  Foot: {dc['foot_W']}\"",
        f"Shoulder break: {dc['shoulder_from_head']}\" from head  {dc['shoulder_from_foot']}\" from foot",
        f"Bevel: head={dc['head_angle']:.1f}deg  foot={dc['foot_angle']:.1f}deg",
        f"Design:      {design.upper()}",
        f"Joinery:     {joinery.title()}",
        f"",
        f"Material COGS (est): ~{n_sheets} sheets x ${sheet_cost:.2f} + ${consumables:.2f} = ${mat_cost:.2f}",
        f"",
        f"Output folder: {out}",
        f"Files: {', '.join(os.path.basename(p) for p in sorted(__import__('glob').glob(os.path.join(out,'*.dxf'))))}",
        f"",
        _tool_requirements(joinery, corner_style),
    ]
    with open(os.path.join(out, "order_summary.txt"), "w", encoding="utf-8") as _sf:
        _sf.write("\n".join(l for l in coffin_summary if l is not None))
    print(f"  Summary: {os.path.join(out, 'order_summary.txt')}")
    return out


# ── Yield / shelf-packing nesting ───────────────────────────────────────────

def yield_report(d, joinery="rabbet"):
    """
    Shelf-packing algorithm — sort tallest first, fit multiple parts per row.
    Returns (n_sheets, yield_pct, sheets).
    sheets = list of lists of (name, x, y, w, h).
    """
    ew = d["end_W_box"] if joinery == "box" else d["end_W"]
    all_parts = sorted([
        ("side_panel",   d["side_L"], d["side_H"]),
        ("side_panel",   d["side_L"], d["side_H"]),
        ("end_panel",    ew,          d["end_H"]),
        ("end_panel",    ew,          d["end_H"]),
        ("bottom_panel", d["bot_L"],  d["bot_W"]),
        ("lid_panel",    d["lid_L"],  d["lid_W"]),
        ("cleat_long",   d["cleat_L"],d["cleat_H"]),
        ("cleat_long",   d["cleat_L"],d["cleat_H"]),
        ("cleat_short",  d["cleat_S"],d["cleat_H"]),
        ("cleat_short",  d["cleat_S"],d["cleat_H"]),
    ], key=lambda p: -p[2])

    sheets_placed = []
    cur_sheet     = []
    shelves       = []   # each: [y, shelf_h, x_cursor]

    def sheet_top():
        return (max(y+h for y,h,_ in shelves) + KERF) if shelves else 0.0

    for name, pw, ph in all_parts:
        placed = False
        for s in shelves:
            sy, sh, sx = s
            if ph <= sh and sx + pw <= SHEET_L:
                cur_sheet.append((name, sx, sy, pw, ph))
                s[2] += pw + KERF
                placed = True
                break
        if not placed:
            ny = sheet_top()
            if ny + ph <= SHEET_W:
                shelves.append([ny, ph, pw + KERF])
                cur_sheet.append((name, 0, ny, pw, ph))
                placed = True
        if not placed:
            sheets_placed.append(cur_sheet)
            cur_sheet = [(name, 0, 0, pw, ph)]
            shelves   = [[0, ph, pw + KERF]]

    sheets_placed.append(cur_sheet)

    used  = sum(pw*ph for s in sheets_placed for _,_,_,pw,ph in s)
    total = len(sheets_placed) * SHEET_W * SHEET_L
    return len(sheets_placed), 100.0*used/total if total else 0, sheets_placed

# ── Generate all files for one size ─────────────────────────────────────────

def generate_size(size_name, size_cfg, design="cross", custom_text=None,
                  joinery="rabbet", customer=None,
                  design_layer=None, text_vpos=None, text_hpos=None, text_scale=1.0,
                  text_rotate=0.0, split_lid=None,
                  design_vpos=None, design_hpos=None, design_scale=1.0,
                  corner_style="dogbone", panel_items=None):
    d   = dims(size_cfg)
    pi  = panel_items or {}
    import datetime
    date_str = datetime.date.today().strftime("%Y%m%d")
    if customer:
        safe = "".join(c for c in customer if c.isalnum() or c in " _-").strip().replace(" ", "_")
        folder = f"{safe}_{size_name}_{design}_{joinery}_{date_str}"
        out = os.path.join(DXF_ROOT, "orders", folder)
    else:
        out = os.path.join(DXF_ROOT, size_name)
    os.makedirs(out, exist_ok=True)

    do_split     = split_lid if split_lid is not None else FEAT.get("split_lid", False)
    split_frac   = FEAT.get("split_lid_fraction", 0.40)
    do_stretcher = (FEAT.get("stretcher", False) and
                    d["side_L"] >= FEAT.get("stretcher_min_length", 65.0))

    parts = {
        "side_panel":   make_side_panel(d, joinery, corner_style=corner_style, items=pi.get("side")),
        "bottom_panel": make_bottom(d, joinery=joinery),
        "end_panel":    make_end_panel(d, joinery, corner_style=corner_style, items=pi.get("end")),
        "cleat_long":   make_cleat(d["cleat_L"], d, "CLEAT LONG x2"),
        "cleat_short":  make_cleat(d["cleat_S"], d, "CLEAT SHORT x2"),
    }

    if do_stretcher:
        parts["stretcher"] = make_stretcher(d)

    # Only generate the selected design lid (not all 5)
    kw = dict(design_layer=design_layer,
              text_vpos=text_vpos, text_hpos=text_hpos, text_scale=text_scale,
              text_rotate=text_rotate,
              design_vpos=design_vpos, design_hpos=design_hpos, design_scale=design_scale)
    if do_split:
        head_L = d["lid_L"] * split_frac       - KERF / 2
        foot_L = d["lid_L"] * (1 - split_frac) - KERF / 2
        parts[f"lid_{design}_head"] = make_lid(d, "none", custom_text,
                                               lid_L_override=head_L,
                                               piece_label="LID HEAD x1", **kw)
        parts[f"lid_{design}_foot"] = make_lid(d, design, None,
                                               lid_L_override=foot_L,
                                               piece_label="LID FOOT x1", **kw)
    else:
        parts[f"lid_{design}"] = make_lid(d, design, custom_text, **kw)

    text_ok = False
    for name, doc in parts.items():
        doc.saveas(os.path.join(out, f"{name}.dxf"))
        if "lid" in name and design in name:
            note_ent = [e for e in doc.modelspace()
                        if e.dxftype()=="TEXT" and "geometry" in e.dxf.text.lower()]
            text_ok = bool(note_ent) and "geometry" in (note_ent[0].dxf.text if note_ent else "")

    n, pct, sheets = yield_report(d, joinery)

    ew_key = "end_W_box" if joinery == "box" else "end_W"
    print(f"\n{'='*60}")
    print(f"  {size_name.upper()}  ({d['int_L']}\" x {d['int_W']}\" x {d['int_D']}\" interior)  [{joinery.upper()} JOINT]")
    print(f"{'='*60}")
    print(f"  Panels:")
    for lbl, key_l, key_h, qty in [
        ("Side",   "side_L","side_H",2), ("End",  ew_key,"end_H",2),
        ("Bottom","bot_L","bot_W",1),    ("Lid",  "lid_L","lid_W",1),
        ("Cleat long","cleat_L","cleat_H",2), ("Cleat short","cleat_S","cleat_H",2),
    ]:
        print(f"    {lbl:<12} x{qty}  {d[key_l]:.3f}\" x {d[key_h]:.3f}\"")
    print(f"  Sheets (96\"x48\"): {n}   Yield: {pct:.1f}%")
    for i, sheet in enumerate(sheets):
        area = sum(pw*ph for _,_,_,pw,ph in sheet)
        print(f"  Sheet {i+1} ({area/SHEET_W/SHEET_L*100:.0f}% full):")
        for nm,x,y,pw,ph in sorted(sheet, key=lambda s: s[2]):
            print(f"    {nm:<22}  {pw:.2f}\" x {ph:.2f}\"  at ({x:.1f}\", {y:.1f}\")")
    split_note = f"  Split lid: HEAD {d['lid_L']*split_frac-KERF/2:.2f}\" + FOOT {d['lid_L']*(1-split_frac)-KERF/2:.2f}\"  (kerf {KERF}\" accounted)" if do_split else ""
    if split_note: print(split_note)
    if do_stretcher: print(f"  Stretcher: {d['int_W']:.2f}\" x {J['dado_from_bottom']:.2f}\"  dado4 at side midpoint")
    handles_note = f"  Rope handles: 2 holes dia {FEAT.get('handle_hole_dia',0.75)}\" at {FEAT.get('handle_setback',12.0)}\" setback" if FEAT.get("rope_handles") else ""
    if handles_note: print(handles_note)
    print(f"  Design: {design}   Inscription: {custom_text or ENG.get('inscription','—')}")
    sheet_cost   = CFG["sheet"].get("cost", 45.0)
    consumables  = FEAT.get("consumables_cost", 15.0)
    mat_cost     = n * sheet_cost + consumables
    print(f"  Material COGS: {n} sheet{'s' if n>1 else ''} x ${sheet_cost:.2f} + ${consumables:.2f} consumables = ${mat_cost:.2f}")
    print(f"  DXFs: {out}")

    import datetime as _dt
    _retail = {"adult": 1895, "child": 1395, "infant": 975}.get(size_name, 0)
    _retail_fp = {"adult": 875, "child": 650, "infant": 450}.get(size_name, 0)
    summary_lines = [
        f"ORDER SUMMARY",
        f"Generated:   {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Customer:    {customer or '—'}",
        f"Size:        {size_name.title()}  ({d['int_L']}\" x {d['int_W']}\" x {d['int_D']}\" interior)",
        f"Design:      {design.upper()}  [{design_layer or 'pocket'}]",
        f"Joinery:     {joinery.title()}",
        f"Inscription: {custom_text or ENG.get('inscription', '—')}",
        f"Split lid:   {'Yes' if do_split else 'No'}",
        f"Stretcher:   {'Yes' if do_stretcher else 'No'}",
        f"Rope hdls:   {'Yes' if FEAT.get('rope_handles') else 'No'}",
        f"Lid pins:    {'Yes' if FEAT.get('lid_pins') else 'No'}",
        f"",
        f"Material COGS:  ${mat_cost:.2f}  ({n} sheet{'s' if n>1 else ''} @ ${sheet_cost:.2f} + ${consumables:.2f} consumables)",
        f"Assembled price (suggested): ${_retail:,}" if _retail else "",
        f"Flat-pack price (suggested): ${_retail_fp:,}" if _retail_fp else "",
        f"",
        f"Output folder: {out}",
        f"Files: {', '.join(os.path.basename(p) for p in sorted(__import__('glob').glob(os.path.join(out,'*.dxf'))))}",
        f"",
        _tool_requirements(joinery, corner_style),
    ]
    with open(os.path.join(out, "order_summary.txt"), "w", encoding="utf-8") as _sf:
        _sf.write("\n".join(l for l in summary_lines if l is not None))
    print(f"  Summary: {os.path.join(out, 'order_summary.txt')}")

    return n, pct

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--size",    choices=list(CFG["sizes"].keys()))
    p.add_argument("--design",  default=ENG.get("default_design","cross"),
                   choices=["cross","celtic","ihs","sacredheart","chirho"])
    p.add_argument("--text",    default=None,
                   help="Custom inscription (overrides config).")
    p.add_argument("--joinery",      default="rabbet", choices=["rabbet","box"])
    p.add_argument("--corner-style", default="square",
                   choices=["square", "dovetail"],
                   help="Box joint variant: square=standard finger pockets, dovetail=14deg undercut bit")
    p.add_argument("--customer",     default=None)
    p.add_argument("--int-l",        type=float, default=None)
    p.add_argument("--int-w",        type=float, default=None)
    p.add_argument("--int-d",        type=float, default=None)
    p.add_argument("--design-layer", default=None, choices=["pocket","centerline"])
    p.add_argument("--text-vpos",    type=float, default=None)
    p.add_argument("--text-hpos",    type=float, default=None)
    p.add_argument("--text-scale",   type=float, default=1.0)
    p.add_argument("--text-rotate",  type=float, default=0.0,
                   help="Text rotation in degrees")
    p.add_argument("--design-vpos",  type=float, default=None)
    p.add_argument("--design-hpos",  type=float, default=None)
    p.add_argument("--design-scale", type=float, default=1.0)
    p.add_argument("--split-lid",    action="store_true", default=None,
                   help="Generate two-piece split lid (accounts for kerf)")
    p.add_argument("--font-family",  default=None,
                   help="Font family name for inscription text (default: Times New Roman)")
    p.add_argument("--shape",        default="casket", choices=["casket", "coffin"],
                   help="casket = rectangular box, coffin = tapered 6-sided")
    p.add_argument("--coffin-head-w",  type=float, default=None)
    p.add_argument("--coffin-foot-w",  type=float, default=None)
    p.add_argument("--coffin-sfh",     type=float, default=None)
    p.add_argument("--coffin-sff",     type=float, default=None)
    p.add_argument("--panel-items-json", default=None,
                   help="Path to JSON file with per-panel item lists for multi-panel designs")
    args = p.parse_args()

    panel_items = {}
    if args.panel_items_json and os.path.exists(args.panel_items_json):
        try:
            with open(args.panel_items_json) as _f:
                panel_items = json.load(_f)
        except Exception as _e:
            print(f"  Warning: could not load panel items JSON: {_e}")

    if args.int_l or args.int_w or args.int_d:
        base_size = CFG["sizes"].get(args.size or "adult")
        custom_cfg = {
            "int_L": args.int_l or base_size["int_L"],
            "int_W": args.int_w or base_size["int_W"],
            "int_D": args.int_d or base_size["int_D"],
        }
        size_label = args.size or "custom"
        if args.int_l: size_label = "custom"
        sizes = {size_label: custom_cfg}
    elif args.size:
        sizes = {args.size: CFG["sizes"][args.size]}
    else:
        sizes = CFG["sizes"]

    global _FONT_FAMILY
    if args.font_family:
        _FONT_FAMILY = args.font_family

    print(f"\nCatholic Casket DXF Generator")
    print(f"Material: {MAT}\" BB ply  Sheet: {SHEET_L}\"x{SHEET_W}\"  Kerf: {KERF}\"")
    print(f"Joinery: {args.joinery}  Layers: {L_OUTLINE} | {L_DADO} | {L_POCKET}")

    coffin_ovr = {}
    if args.coffin_head_w is not None: coffin_ovr["head_W"]             = args.coffin_head_w
    if args.coffin_foot_w is not None: coffin_ovr["foot_W"]             = args.coffin_foot_w
    if args.coffin_sfh    is not None: coffin_ovr["shoulder_from_head"] = args.coffin_sfh
    if args.coffin_sff    is not None: coffin_ovr["shoulder_from_foot"] = args.coffin_sff

    for size_name, size_cfg in sizes.items():
        if args.shape == "coffin":
            generate_coffin_size(size_name, size_cfg, args.design, args.text,
                                  args.customer, args.design_layer,
                                  args.text_vpos, args.text_hpos, args.text_scale,
                                  args.text_rotate,
                                  joinery=args.joinery,
                                  corner_style=args.corner_style,
                                  design_vpos=args.design_vpos,
                                  design_hpos=args.design_hpos,
                                  design_scale=args.design_scale,
                                  coffin_overrides=coffin_ovr if coffin_ovr else None,
                                  panel_items=panel_items)
        else:
            generate_size(size_name, size_cfg, args.design, args.text,
                          args.joinery, args.customer,
                          args.design_layer, args.text_vpos, args.text_hpos, args.text_scale,
                          args.text_rotate,
                          split_lid=args.split_lid if args.split_lid else None,
                          design_vpos=args.design_vpos,
                          design_hpos=args.design_hpos,
                          design_scale=args.design_scale,
                          corner_style=args.corner_style,
                          panel_items=panel_items)

    print(f"\nLoad in Control Nesting: F11 > F2 > Load > DXF Files")
    print(f"Settings: Format = Imperial, material thickness = {MAT}\"")

if __name__ == "__main__":
    main()
