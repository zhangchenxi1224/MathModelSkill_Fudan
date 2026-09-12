"""标定惰性补站的回拉系数 kappa：权衡"额外移动"与"补站数(检测耗时)"。

补站总代价 = 额外移动/5 + 补站数 × 6 × (待定频道数)
kappa=1 表示新站放在距最深点 R_MIN 处（最省移动，但覆盖增量最薄）；
kappa 小则新站更靠近最深点，覆盖增量大、站数少，但移动多。
"""
import math
import numpy as np

R_AREA, R_MIN = 1800.0, 1000.0


def disk_grid(step):
    a = np.arange(-R_AREA, R_AREA + step, step)
    X, Y = np.meshgrid(a, a)
    m = X * X + Y * Y <= R_AREA * R_AREA + 1e-9
    return X[m].copy(), Y[m].copy()


GX, GY = disk_grid(20.0)


def tour_len(seq):
    return sum(math.dist(seq[i], seq[i + 1]) for i in range(len(seq) - 1))


def two_opt(seq, rounds=6):
    n = len(seq)
    for _ in range(rounds):
        imp = False
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                a, b, c = seq[i - 1], seq[i], seq[j]
                d = seq[j + 1] if j + 1 < n else None
                delta = (math.dist(a, c) - math.dist(a, b) if d is None else
                         math.dist(a, c) + math.dist(b, d)
                         - math.dist(a, b) - math.dist(c, d))
                if delta < -1e-9:
                    seq[i:j + 1] = seq[i:j + 1][::-1]
                    imp = True
        if not imp:
            break
    return seq


def nn_tour(start, pts):
    rem, cur, seq = list(pts), start, [start]
    while rem:
        j = min(range(len(rem)), key=lambda i: math.dist(cur, rem[i]))
        cur = rem.pop(j)
        seq.append(cur)
    return two_opt(seq)


def best_insert(seq, q):
    best, at = math.dist(seq[-1], q), len(seq)
    for i in range(len(seq) - 1):
        inc = (math.dist(seq[i], q) + math.dist(q, seq[i + 1])
               - math.dist(seq[i], seq[i + 1]))
        if inc < best:
            best, at = inc, i + 1
    return best, at


def run(N, kappa, trials, seed, n_undet=6):
    rng = np.random.default_rng(seed)
    mv, st = [], []
    for _ in range(trials):
        t = rng.uniform(0, 2 * math.pi, N)
        rr = R_AREA * np.sqrt(rng.uniform(0, 1, N))
        seq = nn_tour((0.0, 0.0), list(zip(rr * np.cos(t), rr * np.sin(t))))
        d2 = np.full(GX.shape, np.inf)
        for cx, cy in seq:
            np.minimum(d2, (GX - cx) ** 2 + (GY - cy) ** 2, out=d2)
        add, k = 0.0, 0
        while k < 25:
            i = int(np.argmax(d2))
            dmax = math.sqrt(d2[i])
            if dmax <= R_MIN:
                break
            deep = (float(GX[i]), float(GY[i]))
            near = min(seq, key=lambda s: math.dist(s, deep))
            d = math.dist(near, deep)
            t_ = min(R_MIN * kappa, d) / d if d > 1e-9 else 0.0
            c = (deep[0] + (near[0] - deep[0]) * t_,
                 deep[1] + (near[1] - deep[1]) * t_)
            inc, at = best_insert(seq, c)
            seq.insert(at, c)
            np.minimum(d2, (GX - c[0]) ** 2 + (GY - c[1]) ** 2, out=d2)
            add += inc
            k += 1
        mv.append(add)
        st.append(k)
    mv, st = np.array(mv), np.array(st)
    total = mv / 5.0 + st * 6.0 * n_undet
    return mv.mean(), st.mean(), total.mean()


print("=" * 78)
print("回拉系数 kappa 标定（总代价 = 额外移动/5 + 补站数×6s×待定频道数）")
print("假定末段待定频道数 = 6")
print("=" * 78)
for N in (10, 13, 16):
    print(f"\nN={N} 个源:")
    print(f"  {'kappa':>6} {'额外移动(m)':>12} {'补站数':>8} "
          f"{'移动耗时(s)':>12} {'检测耗时(s)':>12} {'总代价(s)':>11}")
    best = (1e18, None)
    for kappa in (0.50, 0.60, 0.70, 0.80, 0.90, 0.97):
        m, s, tot = run(N, kappa, 120, seed=100 + N)
        print(f"  {kappa:>6.2f} {m:>12.0f} {s:>8.2f} "
              f"{m/5:>12.0f} {s*36:>12.0f} {tot:>11.0f}")
        if tot < best[0]:
            best = (tot, kappa)
    print(f"  -> 最优 kappa = {best[1]:.2f}, 总代价 {best[0]:.0f} s")
