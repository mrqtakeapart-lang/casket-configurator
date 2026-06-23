import math
import xml.etree.ElementTree as ET


def parse_svg(path):
    tree = ET.parse(path)
    root = tree.getroot()
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    def tag(name):
        return ns + name

    def parse_transform(t):
        if not t:
            return (1, 0, 0, 1, 0, 0)
        t = t.strip()
        if t.startswith("translate("):
            vals = [float(v) for v in t[10:-1].replace(",", " ").split()]
            tx, ty = vals[0], vals[1] if len(vals) > 1 else 0
            return (1, 0, 0, 1, tx, ty)
        if t.startswith("scale("):
            vals = [float(v) for v in t[6:-1].replace(",", " ").split()]
            sx = vals[0]; sy = vals[1] if len(vals) > 1 else sx
            return (sx, 0, 0, sy, 0, 0)
        if t.startswith("matrix("):
            vals = [float(v) for v in t[7:-1].replace(",", " ").split()]
            if len(vals) == 6:
                return tuple(vals)
        return (1, 0, 0, 1, 0, 0)

    def apply_transform(x, y, m):
        a, b, c, d, e, f = m
        return a * x + c * y + e, b * x + d * y + f

    def parse_d(d_attr):
        paths_out, closed_out = [], []
        if not d_attr:
            return paths_out, closed_out
        tokens = d_attr.replace(",", " ").split()
        i = 0
        cur_path = []
        cur_x, cur_y = 0.0, 0.0
        cmd = None
        while i < len(tokens):
            tok = tokens[i]
            if tok.upper() in "MLHVZCSQTA":
                cmd = tok
                i += 1
                continue
            try:
                v = float(tok)
            except ValueError:
                cmd = tok
                i += 1
                continue
            if cmd in ("M", "L"):
                x = v; y = float(tokens[i + 1]); i += 2
                cur_x, cur_y = x, y
                cur_path.append((cur_x, cur_y))
            elif cmd in ("m", "l"):
                x = cur_x + v; y = cur_y + float(tokens[i + 1]); i += 2
                cur_x, cur_y = x, y
                cur_path.append((cur_x, cur_y))
            elif cmd == "H":
                cur_x = v; i += 1
                cur_path.append((cur_x, cur_y))
            elif cmd == "h":
                cur_x += v; i += 1
                cur_path.append((cur_x, cur_y))
            elif cmd == "V":
                cur_y = v; i += 1
                cur_path.append((cur_x, cur_y))
            elif cmd == "v":
                cur_y += v; i += 1
                cur_path.append((cur_x, cur_y))
            elif cmd in ("Z", "z"):
                if cur_path:
                    paths_out.append(cur_path); closed_out.append(True)
                    cur_path = []
                i += 0
            else:
                i += 1
            if cmd not in ("Z", "z") and not cur_path:
                i += 1
        if cur_path:
            paths_out.append(cur_path); closed_out.append(False)
        return paths_out, closed_out

    all_paths, all_closed = [], []
    xs, ys = [], []

    for el in root.iter():
        m = parse_transform(el.get("transform", ""))
        local_tag = el.tag.replace(ns, "") if ns else el.tag
        if local_tag == "line":
            try:
                pts = [
                    apply_transform(float(el.get("x1", 0)), float(el.get("y1", 0)), m),
                    apply_transform(float(el.get("x2", 0)), float(el.get("y2", 0)), m),
                ]
                all_paths.append(pts); all_closed.append(False)
                for px, py in pts: xs.append(px); ys.append(py)
            except (ValueError, TypeError):
                pass
        elif local_tag in ("polyline", "polygon"):
            raw = el.get("points", "").replace(",", " ").split()
            try:
                raw_pts = [(float(raw[j]), float(raw[j + 1])) for j in range(0, len(raw) - 1, 2)]
                pts = [apply_transform(px, py, m) for px, py in raw_pts]
                if pts:
                    is_closed = local_tag == "polygon"
                    all_paths.append(pts); all_closed.append(is_closed)
                    for px, py in pts: xs.append(px); ys.append(py)
            except (ValueError, IndexError):
                pass
        elif local_tag == "rect":
            try:
                rx = float(el.get("x", 0)); ry = float(el.get("y", 0))
                rw = float(el.get("width", 0)); rh = float(el.get("height", 0))
                corners = [(rx, ry), (rx + rw, ry), (rx + rw, ry + rh), (rx, ry + rh)]
                pts = [apply_transform(px, py, m) for px, py in corners]
                all_paths.append(pts); all_closed.append(True)
                for px, py in pts: xs.append(px); ys.append(py)
            except (ValueError, TypeError):
                pass
        elif local_tag == "path":
            d_attr = el.get("d", "")
            p_paths, p_closed = parse_d(d_attr)
            for pp, pc in zip(p_paths, p_closed):
                tpts = [apply_transform(px, py, m) for px, py in pp]
                if tpts:
                    all_paths.append(tpts); all_closed.append(pc)
                    for px, py in tpts: xs.append(px); ys.append(py)

    if not xs:
        return [], [], (0, 0, 1, 1)
    bbox = (min(xs), min(ys), max(xs), max(ys))
    return all_paths, all_closed, bbox


def parse_dxf(path):
    import ezdxf
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    all_paths, all_closed = [], []
    raw_lines = []
    xs, ys = [], []

    for e in msp:
        t = e.dxftype()
        if t == "LINE":
            p1 = (e.dxf.start.x, e.dxf.start.y)
            p2 = (e.dxf.end.x,   e.dxf.end.y)
            raw_lines.append((p1, p2))
            xs += [p1[0], p2[0]]; ys += [p1[1], p2[1]]
        elif t == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points()]
            if pts:
                is_closed = bool(e.closed)
                all_paths.append(pts); all_closed.append(is_closed)
                for px, py in pts: xs.append(px); ys.append(py)
        elif t == "ARC":
            cx, cy = e.dxf.center.x, e.dxf.center.y
            r = e.dxf.radius
            a0 = math.radians(e.dxf.start_angle)
            a1 = math.radians(e.dxf.end_angle)
            if a1 <= a0:
                a1 += 2 * math.pi
            segs = 16
            pts = [(cx + r * math.cos(a0 + (a1 - a0) * k / segs),
                    cy + r * math.sin(a0 + (a1 - a0) * k / segs))
                   for k in range(segs + 1)]
            all_paths.append(pts); all_closed.append(False)
            for px, py in pts: xs.append(px); ys.append(py)

    if raw_lines:
        merged = _merge_lines(raw_lines)
        for seg, closed in merged:
            all_paths.append(seg); all_closed.append(closed)
            for px, py in seg: xs.append(px); ys.append(py)

    if not xs:
        return [], [], (0, 0, 1, 1)
    bbox = (min(xs), min(ys), max(xs), max(ys))
    return all_paths, all_closed, bbox


def _merge_lines(lines, tol=1e-6):
    segs = list(lines)
    out = []
    used = [False] * len(segs)
    for i in range(len(segs)):
        if used[i]:
            continue
        chain = list(segs[i])
        used[i] = True
        grew = True
        while grew:
            grew = False
            for j in range(len(segs)):
                if used[j]:
                    continue
                p1, p2 = segs[j]
                if _close(chain[-1], p1, tol):
                    chain.append(p2); used[j] = True; grew = True
                elif _close(chain[-1], p2, tol):
                    chain.append(p1); used[j] = True; grew = True
                elif _close(chain[0], p2, tol):
                    chain.insert(0, p1); used[j] = True; grew = True
                elif _close(chain[0], p1, tol):
                    chain.insert(0, p2); used[j] = True; grew = True
        is_closed = _close(chain[0], chain[-1], tol)
        out.append((chain, is_closed))
    return out


def _close(a, b, tol):
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def normalize(paths, bbox):
    x0, y0, x1, y1 = bbox
    span_x = x1 - x0 if x1 > x0 else 1.0
    span_y = y1 - y0 if y1 > y0 else 1.0
    span = max(span_x, span_y)
    ox = (span - span_x) / 2
    oy = (span - span_y) / 2
    result = []
    for path in paths:
        norm = [((px - x0 + ox) / span, (py - y0 + oy) / span) for px, py in path]
        result.append(norm)
    return result


def auto_layer(paths, closed_flags):
    if closed_flags and all(closed_flags):
        return "pocket"
    return "centerline"
