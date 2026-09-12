"""几何核心：示向度楔形交会定位、凸多边形裁剪、直径（旋转卡壳）、最小外接圆（Welzl）。

坐标约定：x 东、y 北，方位角自 x 轴正向逆时针为正，单位度，范围 [0,360)。
已通过 300 组随机交叉验证（直径/MEC 对暴力解）与 20000 组楔形包含性验证。
"""
import math
import random

EPS = 1e-12
DEG = math.pi / 180.0
R_AREA = 1800.0          # 目标区域半径
SVD_ERR = 1.0            # 示向度误差界（度）


def norm_deg(a):
    return a % 360.0


def unit(deg):
    r = deg * DEG
    return (math.cos(r), math.sin(r))


def bearing(frm, to):
    """从 frm 指向 to 的真实方位角（度）。"""
    return norm_deg(math.degrees(math.atan2(to[1] - frm[1], to[0] - frm[0])))


def ang_diff(a, b):
    """a-b 归一化到 (-180,180]。"""
    d = (a - b + 180.0) % 360.0 - 180.0
    return d + 360.0 if d <= -180.0 else d


def dist(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def wedge_halfplanes(s, m, e=SVD_ERR):
    """检测点 s 处示向度 m（误差界 e 度）对应的楔形，返回两个半平面 (a,b,c)。

    真方位 theta in [m-e, m+e]，因 2e<180 故楔形为凸锥。
    约束 cross(u_lo, v)>=0 且 cross(v, u_hi)>=0，其中 v = p - s。
    """
    lo, hi = unit(m - e), unit(m + e)
    h1 = (lo[1], -lo[0], lo[1] * s[0] - lo[0] * s[1])
    h2 = (-hi[1], hi[0], -hi[1] * s[0] + hi[0] * s[1])
    return [h1, h2]


def clip_halfplane(poly, a, b, c):
    """凸多边形与半平面 a*x+b*y<=c 求交（Sutherland-Hodgman）。"""
    if not poly:
        return []
    out = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        dp = a * p[0] + b * p[1] - c
        dq = a * q[0] + b * q[1] - c
        if dp <= EPS:
            out.append(p)
        if (dp < -EPS and dq > EPS) or (dp > EPS and dq < -EPS):
            t = dp / (dp - dq)
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1])))
    return out


def box(half=4000.0):
    return [(-half, -half), (half, -half), (half, half), (-half, half)]


def disk_poly(R=R_AREA, n=256, center=(0.0, 0.0)):
    """圆域的内接正 n 边形（保守内近似；n=256 时半径误差 < 0.14 m）。"""
    return [(center[0] + R * math.cos(2 * math.pi * i / n),
             center[1] + R * math.sin(2 * math.pi * i / n)) for i in range(n)]


def intersect_wedges(obs, e=SVD_ERR, init=None):
    """obs = [(station, svd_deg), ...] -> 交会定位区域（凸多边形顶点表）。"""
    poly = list(init) if init is not None else box()
    for s, m in obs:
        for (a, b, c) in wedge_halfplanes(s, m, e):
            poly = clip_halfplane(poly, a, b, c)
            if not poly:
                return []
    return poly


def hull(pts):
    """Andrew monotone chain 凸包，逆时针，无共线冗余点。"""
    p = sorted(set((round(x, 9), round(y, 9)) for x, y in pts))
    if len(p) <= 2:
        return p

    def half(seq):
        h = []
        for q in seq:
            while len(h) >= 2 and _cross(h[-2], h[-1], q) <= 0:
                h.pop()
            h.append(q)
        return h

    lo = half(p)
    up = half(list(reversed(p)))
    return lo[:-1] + up[:-1]


def diameter(pts):
    """点集直径（最远点对）。凸包 + 旋转卡壳。返回 (d, (p,q))。"""
    h = hull(pts)
    n = len(h)
    if n == 0:
        return 0.0, None
    if n == 1:
        return 0.0, (h[0], h[0])
    if n == 2:
        return dist(h[0], h[1]), (h[0], h[1])
    best, pair = -1.0, None
    j = 1
    for i in range(n):
        ni = (i + 1) % n
        while True:
            nj = (j + 1) % n
            if abs(_cross(h[i], h[ni], h[nj])) > abs(_cross(h[i], h[ni], h[j])) + EPS:
                j = nj
            else:
                break
        for cand in (h[j], h[(j + 1) % n]):
            for base in (h[i], h[ni]):
                d = dist(base, cand)
                if d > best:
                    best, pair = d, (base, cand)
    return best, pair


def _circ2(a, b):
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0), dist(a, b) / 2.0


def _circ3(a, b, c):
    ax, ay = a
    bx, by = b[0] - ax, b[1] - ay
    cx, cy = c[0] - ax, c[1] - ay
    d = 2.0 * (bx * cy - by * cx)
    if abs(d) < 1e-14:
        return None
    b2, c2 = bx * bx + by * by, cx * cx + cy * cy
    ux = (b2 * cy - c2 * by) / d
    uy = (c2 * bx - b2 * cx) / d
    return (ax + ux, ay + uy), math.hypot(ux, uy)


def mec(pts):
    """最小外接圆（Welzl 增量算法，期望 O(n)）。返回 (center, radius)。"""
    p = list({(round(x, 9), round(y, 9)) for x, y in pts})
    if not p:
        return (0.0, 0.0), 0.0
    if len(p) == 1:
        return p[0], 0.0
    random.Random(12345).shuffle(p)
    c, r = _circ2(p[0], p[1])
    for i in range(2, len(p)):
        if dist(p[i], c) <= r + 1e-9:
            continue
        c, r = _circ2(p[i], p[0])
        for j in range(1, i):
            if dist(p[j], c) <= r + 1e-9:
                continue
            c, r = _circ2(p[i], p[j])
            for k in range(j):
                if dist(p[k], c) <= r + 1e-9:
                    continue
                res = _circ3(p[i], p[j], p[k])
                if res:
                    c, r = res
    return c, r


def centroid(poly):
    """多边形形心；退化时返回顶点均值。"""
    n = len(poly)
    if n == 0:
        return None
    if n < 3:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    a = cx = cy = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cr = x1 * y2 - x2 * y1
        a += cr
        cx += (x1 + x2) * cr
        cy += (y1 + y2) * cr
    if abs(a) < 1e-12:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    return (cx / (3.0 * a), cy / (3.0 * a))


def area(poly):
    n = len(poly)
    if n < 3:
        return 0.0
    a = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0
