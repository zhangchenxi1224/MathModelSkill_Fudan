"""终局决策的公平代价模型：抵近移动是"本来就要付"的沉没成本。

机器狗终究要走到源的 20 m 内才能清除，故"朝目标走"的移动不应计入补测开销。
补测的边际成本 = 检测 6 s + 为制造交角而偏离直线的绕行增量。
铺盖的边际成本 = 沿条铺 clear 的失败次数 × 3 s + 条内移动。
"""
import math
import geom as G

EPS_R = 1.0 * G.DEG
V, T_MEAS, T_SW, T_FAIL, T_OK = 5.0, 5.0, 1.0, 3.0, 5.0
R_CL = 20.0


def region(d, alpha_deg):
    h = alpha_deg / 2.0
    s1 = (d * math.cos(math.radians(90 + h)), d * math.sin(math.radians(90 + h)))
    s2 = (d * math.cos(math.radians(90 - h)), d * math.sin(math.radians(90 - h)))
    poly = G.intersect_wedges([(s1, G.bearing(s1, (0, 0))),
                               (s2, G.bearing(s2, (0, 0)))])
    if not poly:
        return None
    D, pair = G.diameter(poly)
    _, rho = G.mec(poly)
    ux = ((pair[1][0] - pair[0][0]) / D, (pair[1][1] - pair[0][1]) / D)
    nx = (-ux[1], ux[0])
    pr = [p[0] * nx[0] + p[1] * nx[1] for p in poly]
    return D, max(pr) - min(pr), rho


def cost_sweep(D, w):
    """沿长轴铺 clear。宽 w>=2R_CL 时需多排，按 ceil(w/(2R_CL)) 排计。"""
    rows = max(1, math.ceil(w / (2 * R_CL)))
    w_eff = w / rows
    step = 2.0 * math.sqrt(max(R_CL ** 2 - (w_eff / 2) ** 2, 1e-6))
    n_per = max(1, math.ceil(D / step))
    n = rows * n_per
    if n > 12:
        return None                       # 过多，判为不可行
    k = (n + 1) / 2.0                     # 期望命中序号（源在区域内近似均匀）
    move = (k - 1) * step / V             # 相邻清除点间移动
    return n, move + (k - 1) * T_FAIL + T_OK


def cost_remeasure(d, rho, detour_frac=0.25):
    """补测的边际成本：检测 6 s + 绕行增量 + 抵近后清除。

    朝目标走 d - d' 的移动是沉没成本（终究要走），只计绕行增量。
    绕行增量按"为制造 beta=90° 需侧移，路径变长 detour_frac 倍"估计。
    抵近到 d' 后新 rho' = d'*eps/sin(45°)，若 <=20 则一次清除成功。
    """
    d_new = 250.0                         # 抵近到 250 m 处再测（推论5）
    if d <= d_new:
        d_new = d * 0.5
    approach = d - d_new                  # 沉没
    detour = approach * detour_frac / V   # 绕行增量（计入）
    rho_new = d_new * EPS_R / math.sin(math.radians(45.0))
    t = detour + T_MEAS + T_SW            # 绕行 + 检测
    t += rho_new / V + T_OK               # 走到新估计中心 + 清除
    if rho_new > R_CL:
        t += T_FAIL + rho_new / V
    return t


print("=" * 86)
print("终局决策（公平模型：朝目标的抵近移动为沉没成本，不计入补测）")
print("=" * 86)
print(f"{'d(m)':>6} {'alpha':>6} {'长D':>7} {'宽w':>7} {'rho':>7} "
      f"{'铺n':>5} {'铺盖':>8} {'补测':>8} {'优选':>7}")
recs = []
for d in (300, 500, 700, 900, 1100, 1300, 1500):
    for a in (40, 60, 90, 120, 140):
        R = region(d, a)
        if not R:
            continue
        D, w, rho = R
        cs = cost_sweep(D, w)
        cr = cost_remeasure(d, rho)
        if cs is None:
            print(f"{d:>6} {a:>6} {D:>7.1f} {w:>7.1f} {rho:>7.1f} "
                  f"{'>12':>5} {'-':>8} {cr:>8.1f} {'补测':>7}")
            recs.append((rho, w, None, cr, "补测"))
            continue
        n, ts = cs
        best = "铺盖" if ts <= cr else "补测"
        print(f"{d:>6} {a:>6} {D:>7.1f} {w:>7.1f} {rho:>7.1f} "
              f"{n:>5} {ts:>8.1f} {cr:>8.1f} {best:>7}")
        recs.append((rho, w, ts, cr, best))

sw = [r for r in recs if r[4] == "铺盖"]
rm = [r for r in recs if r[4] == "补测"]
print()
print("=" * 86)
print("阈值")
print("=" * 86)
if sw:
    print(f"  铺盖占优: rho ∈ [{min(r[0] for r in sw):.1f}, "
          f"{max(r[0] for r in sw):.1f}] m,  条宽 w ∈ "
          f"[{min(r[1] for r in sw):.1f}, {max(r[1] for r in sw):.1f}] m")
if rm:
    print(f"  补测占优: rho ∈ [{min(r[0] for r in rm):.1f}, "
          f"{max(r[0] for r in rm):.1f}] m,  条宽 w ∈ "
          f"[{min(r[1] for r in rm):.1f}, {max(r[1] for r in rm):.1f}] m")
print()
print("推荐判据（以 clear 次数为准，而非 rho）：")
print("  设沿长轴铺盖所需 clear 次数 n = ceil(w/40)*ceil(D/step)，")
print("  若 n <= 3 用铺盖（期望 <=12 s）；否则先抵近补测（推论5：抵近至 250 m")
print("  即得 rho' = 250*eps/sin45 ≈ 6.2 m << 20 m，必定一次清除成功）。")
