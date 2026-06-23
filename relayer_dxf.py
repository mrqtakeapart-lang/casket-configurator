"""
Thermwood Re-Layer Tool
Reassigns all geometry in an external DXF (from Etsy, Inkscape, etc.)
to the correct Thermwood Control Nesting layer for engraving.

Usage:
  py relayer_dxf.py input.dxf                          # re-layer as pocket engraving
  py relayer_dxf.py input.dxf --op centerline          # centerline trace instead
  py relayer_dxf.py input.dxf --depth 0.2 --tool 0.5  # custom depth and tool

Output: input_thermwood.dxf in the same folder.

Supported operations:
  pocket      pocket d#p# z#p#     fills the shape (best for cross, IHS, motifs)
  centerline  centerline d#p# z#p# traces the outline (best for fine line art)
  dado        dado z#p#            dado groove
"""

import ezdxf, os, sys, argparse, math

def zv(v):
    s = f"{v:.4f}".rstrip("0")
    if s.endswith("."): s += "0"
    return s.replace(".", "p")

def _cubic_bezier_pts(x0,y0,x1,y1,x2,y2,x3,y3,n=8):
    return [((1-t)**3*x0+3*(1-t)**2*t*x1+3*(1-t)*t**2*x2+t**3*x3,
             (1-t)**3*y0+3*(1-t)**2*t*y1+3*(1-t)*t**2*y2+t**3*y3)
            for t in [i/n for i in range(n+1)]]

def _quad_bezier_pts(x0,y0,x1,y1,x2,y2,n=6):
    return [((1-t)**2*x0+2*(1-t)*t*x1+t**2*x2,
             (1-t)**2*y0+2*(1-t)*t*y1+t**2*y2)
            for t in [i/n for i in range(n+1)]]

def _parse_svg_path_d(d):
    import re
    tokens = re.findall(r'[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?', d)
    segs = []; x = y = sx = sy = 0.0; cmd = "M"; i = 0
    def eat(n):
        nonlocal i
        vs = [float(tokens[i+j]) for j in range(n)]; i += n; return vs
    while i < len(tokens):
        if tokens[i].isalpha(): cmd = tokens[i]; i += 1; continue
        if cmd in "Mm":
            x2,y2 = eat(2)
            if cmd=="m": x2+=x; y2+=y
            x,y,sx,sy = x2,y2,x2,y2; cmd = "l" if cmd=="m" else "L"
        elif cmd in "Ll":
            x2,y2 = eat(2)
            if cmd=="l": x2+=x; y2+=y
            segs.append(((x,y),(x2,y2))); x,y = x2,y2
        elif cmd in "Hh":
            x2 = eat(1)[0]
            if cmd=="h": x2+=x
            segs.append(((x,y),(x2,y))); x=x2
        elif cmd in "Vv":
            y2 = eat(1)[0]
            if cmd=="v": y2+=y
            segs.append(((x,y),(x,y2))); y=y2
        elif cmd in "Cc":
            cx1,cy1,cx2,cy2,ex,ey = eat(6)
            if cmd=="c": cx1+=x;cy1+=y;cx2+=x;cy2+=y;ex+=x;ey+=y
            pts = _cubic_bezier_pts(x,y,cx1,cy1,cx2,cy2,ex,ey)
            for j in range(len(pts)-1): segs.append((pts[j],pts[j+1]))
            x,y = ex,ey
        elif cmd in "Qq":
            cx1,cy1,ex,ey = eat(4)
            if cmd=="q": cx1+=x;cy1+=y;ex+=x;ey+=y
            pts = _quad_bezier_pts(x,y,cx1,cy1,ex,ey)
            for j in range(len(pts)-1): segs.append((pts[j],pts[j+1]))
            x,y = ex,ey
        elif cmd in "Aa":
            eat(7); pass  # arc: skip complex arc math, endpoint only
        elif cmd in "Zz":
            segs.append(((x,y),(sx,sy))); x,y = sx,sy
        else:
            i += 1
    return segs

def _parse_svg(svg_path):
    import xml.etree.ElementTree as ET
    tree = ET.parse(svg_path); root = tree.getroot()
    def tag(e): return e.tag.split("}")[-1]
    segs = []
    vb = root.get("viewBox","")
    flip_y = 1.0
    if vb:
        try: _,_,_,h = [float(v) for v in vb.split()]; flip_y = h
        except Exception: pass
    for elem in root.iter():
        t = tag(elem)
        if t == "path":
            segs.extend(_parse_svg_path_d(elem.get("d","")))
        elif t == "rect":
            x=float(elem.get("x",0)); y=float(elem.get("y",0))
            w=float(elem.get("width",0)); h=float(elem.get("height",0))
            segs += [((x,y),(x+w,y)),((x+w,y),(x+w,y+h)),
                     ((x+w,y+h),(x,y+h)),((x,y+h),(x,y))]
        elif t == "line":
            segs.append(((float(elem.get("x1",0)),float(elem.get("y1",0))),
                         (float(elem.get("x2",0)),float(elem.get("y2",0)))))
        elif t == "circle":
            cx=float(elem.get("cx",0)); cy=float(elem.get("cy",0)); r=float(elem.get("r",0))
            n = max(36, int(2*math.pi*r/2))
            angs = [2*math.pi*i/n for i in range(n+1)]
            for i in range(n):
                segs.append(((cx+r*math.cos(angs[i]), cy+r*math.sin(angs[i])),
                             (cx+r*math.cos(angs[i+1]), cy+r*math.sin(angs[i+1]))))
        elif t == "polyline" or t == "polygon":
            raw = elem.get("points","").split()
            pts = []
            for i in range(0, len(raw)-1, 2):
                pts.append((float(raw[i]), float(raw[i+1])))
            rng = range(len(pts)-1) if t=="polyline" else range(len(pts))
            for i in rng:
                segs.append((pts[i], pts[(i+1)%len(pts)]))
    return segs, flip_y

def relayer_svg(src_path, op="pocket", depth=0.125, tool_d=0.25, dest_path=None):
    segs, flip_y = _parse_svg(src_path)
    if not segs:
        print(f"No geometry found in SVG: {src_path}"); return
    if op == "pocket":
        layer = f"pocket d{zv(tool_d)} z{zv(depth)}"
    elif op == "centerline":
        layer = f"centerline d{zv(tool_d)} z{zv(depth)}"
    else:
        layer = f"dado z{zv(depth)}"
    # Normalize: translate so min coords are at origin, flip Y axis
    all_x = [p[0] for seg in segs for p in seg]
    all_y = [p[1] for seg in segs for p in seg]
    min_x, min_y = min(all_x), min(all_y)
    max_y = max(all_y)
    dst = ezdxf.new("R2010"); msp = dst.modelspace()
    for (x1,y1),(x2,y2) in segs:
        # Normalize and flip Y (SVG Y-down → DXF Y-up)
        nx1, ny1 = x1-min_x, max_y-y1
        nx2, ny2 = x2-min_x, max_y-y2
        if abs(nx1-nx2) < 1e-6 and abs(ny1-ny2) < 1e-6: continue
        msp.add_line((nx1,ny1),(nx2,ny2), dxfattribs={"layer": layer})
    if dest_path is None:
        base = os.path.splitext(src_path)[0]
        dest_path = base + "_thermwood.dxf"
    dst.saveas(dest_path)
    print(f"SVG converted: {os.path.basename(src_path)}")
    print(f"  Segments: {len(segs)}  Layer: {layer}")
    print(f"  Output: {dest_path}")

def relayer(src_path, op="pocket", depth=0.125, tool_d=0.25, dest_path=None):
    if not os.path.exists(src_path):
        print(f"File not found: {src_path}"); return

    src = ezdxf.readfile(src_path)
    dst = ezdxf.new("R2010")
    msp_src = src.modelspace()
    msp_dst = dst.modelspace()

    if op == "pocket":
        layer = f"pocket d{zv(tool_d)} z{zv(depth)}"
    elif op == "centerline":
        layer = f"centerline d{zv(tool_d)} z{zv(depth)}"
    elif op == "dado":
        layer = f"dado z{zv(depth)}"
    else:
        layer = op

    copied = 0
    skipped = 0

    for e in msp_src:
        t = e.dxftype()
        if t in ("LINE", "ARC", "CIRCLE", "LWPOLYLINE", "SPLINE", "ELLIPSE"):
            try:
                new_e = e.copy()
                new_e.dxf.layer = layer
                msp_dst.add_entity(new_e)
                copied += 1
            except Exception:
                skipped += 1
        elif t == "POLYLINE":
            # Convert POLYLINE vertices to LINE entities
            try:
                verts = list(e.vertices)
                for i in range(len(verts)-1):
                    p1 = verts[i].dxf.location[:2]
                    p2 = verts[i+1].dxf.location[:2]
                    msp_dst.add_line(p1, p2, dxfattribs={"layer": layer})
                    copied += 1
            except Exception:
                skipped += 1
        elif t == "INSERT":
            # Explode block references to lines
            try:
                for child in e.virtual_entities():
                    if child.dxftype() in ("LINE","ARC","CIRCLE","LWPOLYLINE"):
                        new_e = child.copy()
                        new_e.dxf.layer = layer
                        msp_dst.add_entity(new_e)
                        copied += 1
            except Exception:
                skipped += 1
        else:
            skipped += 1

    if dest_path is None:
        base, ext = os.path.splitext(src_path)
        dest_path = base + "_thermwood.dxf"

    dst.saveas(dest_path)
    print(f"Re-layered: {os.path.basename(src_path)}")
    print(f"  Target layer: {layer}")
    print(f"  Entities copied: {copied}  skipped: {skipped}")
    print(f"  Output: {dest_path}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="Source DXF or SVG file path")
    parser.add_argument("--op", default="pocket",
                        help="Operation type: pocket, centerline, dado (default: pocket)")
    parser.add_argument("--depth",  type=float, default=0.125, help="Cut depth in inches")
    parser.add_argument("--tool",   type=float, default=0.25,  help="Tool diameter in inches")
    parser.add_argument("--output", default=None, help="Output path (default: input_thermwood.dxf)")
    args = parser.parse_args()
    ext = os.path.splitext(args.input)[1].lower()
    if ext == ".svg":
        relayer_svg(args.input, args.op, args.depth, args.tool, args.output)
    else:
        relayer(args.input, args.op, args.depth, args.tool, args.output)

if __name__ == "__main__":
    main()
