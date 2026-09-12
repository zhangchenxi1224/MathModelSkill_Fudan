"""(1) p-center 数值验证 6 站不可能 / 7 站可行；(2) 修正后的惰性补站代价。

p-center: min_{S,|S|=n} max_{p in D(1800)} min_{s in S} ||p-s||
若最优值 > 1000 则 n 站无法完备覆盖。
"""
import math
import numpy as np
from scipy.optimize import minimize

R_AREA, R_MIN = 1800.0, 1000.0
rng = np.random.default_rng(7)


def disk_grid(step):
    a = np.arange(-R_AREA, R_AREA + step, step)
    X, Y = np.meshgrid(a, a)
    m = X * X + Y * Y <= R_AREA * R_AREA + 1e-9
    return np.stack([X[m], Y[m]], 1)


P_COARSE = disk_grid(25.0)
P_FINE = disk_grid(4.0)


def maxmin(S, P):
    d = np.sqrt(((P[:, None, :] - S[None, :, :]) ** 2).sum(-1))
    return d.min(1).max()


def softmax_obj(flat, P, beta):
    """max/min 的光滑代理，便于梯度优化。"""
    S = flat.reshape(-1, 2)
    d = np.sqrt(((P[:, None, :] - S[None, :, :]) ** 2).sum(-1) + 1e-9)
    dmin = -np.log(np.exp(-beta * d).sum(1) + 1e-300) / beta      # soft-min
    return np.log(np.exp(beta * dmin / R_AREA).sum() + 1e-300) / beta * R_AREA


print("=" * 70)
print("p-center 数值优化：n 个站点覆盖 D(1800) 的最优最大覆盖半径")
print("=" * 70)
for n in (6, 7):
    best = (1e18, None)
    for trial in range(40):
        if trial == 0:
            rho = 1150.0
            S0 = np.array([[0.0, 0.0]] + [[rho*math.cos(2*math.pi*i/(n-1)),
                                           rho*math.sin(2*math.pi*i/(n-1))]
                                          for i in range(n - 1)])
        elif trial == 1:
            rho = 1000.0
            S0 = np.array([[rho*math.cos(2*math.pi*i/n),
                            rho*math.sin(2*math.pi*i/n)] for i in range(n)])
        else:
            t = rng.uniform(0, 2*math.pi, n)
            r = R_AREA*np.sqrt(rng.uniform(0, 1, n))
            S0 = np.stack([r*np.cos(t), r*np.sin(t)], 1)
        x = S0.ravel().copy()
        for beta in (0.02, 0.06, 0.2, 0.8):
            res = minimize(softmax_obj, x, args=(P_COARSE, beta),
                           method="L-BFGS-B",
                           options=dict(maxiter=400, ftol=1e-12))
            x = res.x
        S = x.reshape(-1, 2)
        v = maxmin(S, P_COARSE)
        if v < best[0]:
            best = (v, S.copy())
    v_fine = maxmin(best[1], P_FINE)
    verdict = "可完备覆盖" if v_fine <= R_MIN else "无法完备覆盖"
    print(f"  n={n}: 最优覆盖半径 ≈ {v_fine:>7.2f} m  (需 <= {R_MIN:.0f} m) -> {verdict}")
    print(f"        最优站点: " +
          ", ".join(f"({p[0]:>7.1f},{p[1]:>7.1f})" for p in best[1]))
    print(f"        r/R = {v_fine/R_AREA:.4f}")
print()
print("  文献常数对照: 覆盖单位圆的 6 等圆最优半径 r6=0.5559R, 7 等圆 r7=0.5R")
print(f"  换算: 6 站需 {0.5559*R_AREA:.1f} m > 1000 m (不可行);  "
      f"7 站需 {0.5*R_AREA:.0f} m <= 1000 m (可行)")
