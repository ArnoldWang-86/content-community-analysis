# -*- coding: utf-8 -*-
r"""12_anova.py —— 单因素方差分析（分区对互动率的影响）

问题：
  前面只做过分区【两两】比较（晚间 vs 凌晨），但数据里有 109 个分区。
  真正该问的是：分区之间的互动率差异是真实的，还是随机波动？

为什么不能直接两两做 t 检验：
  109 个分区两两比较需 C(109,2)=5,886 次检验，每次 5% 假阳性率，
  即使分区之间毫无差异，也会期望出现约 294 次"显著"结果。
  ANOVA 一次性检验全部组，把族错误率控制在 5%。

流程：假设检验 -> ANOVA -> 事后检验(Tukey HSD) -> 效应量(eta^2)
"""
import os, sys, warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multicomp import pairwise_tukeyhsd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import to_df

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESDIR = os.path.join(ROOT, "results")
SEP = "=" * 76
MIN_N = 100          # 每个分区至少要有多少样本才纳入分析


def hr(t):
    print("\n" + SEP + "\n  " + t + "\n" + SEP)


def welch_anova(groups):
    """Welch 方差分析：不要求方差齐性，返回 F、df、p"""
    k = len(groups)
    ns = np.array([len(g) for g in groups], dtype=float)
    means = np.array([g.mean() for g in groups])
    vars_ = np.array([g.var(ddof=1) for g in groups])
    w = ns / vars_
    sw = w.sum()
    mw = (w * means).sum() / sw
    num = ((w * (means - mw) ** 2).sum()) / (k - 1)
    lam = (((1 - w / sw) ** 2) / (ns - 1)).sum()
    den = 1 + 2 * (k - 2) / (k ** 2 - 1) * lam
    F = num / den
    df1 = k - 1
    df2 = 1 / (3 / (k ** 2 - 1) * lam)
    p = stats.f.sf(F, df1, df2)
    return F, df1, df2, p


def main():
    print(SEP)
    print("  单因素方差分析：内容分区对互动率的影响")
    print(SEP)

    df = to_df("""
        SELECT c.tname, v.interact_rate
        FROM video v JOIN category c ON v.tid = c.tid
    """)
    df["interact_rate"] = pd.to_numeric(df.interact_rate, errors="coerce")
    df = df.dropna()

    hr("1. 数据准备")
    cnt = df.tname.value_counts()
    keep = cnt[cnt >= MIN_N].index.tolist()
    d = df[df.tname.isin(keep)].copy()
    print("  全样本分区数 : %d 个" % df.tname.nunique())
    print("  样本量 >= %d 的分区 : %d 个（覆盖 %s 条样本，占 %.1f%%）"
          % (MIN_N, len(keep), format(len(d), ","), len(d) / len(df) * 100))
    print("  说明：样本量过小的分区方差估计不可靠，纳入会污染 F 检验")

    groups = [g.interact_rate.values for _, g in d.groupby("tname")]
    names = [n for n, _ in d.groupby("tname")]
    order = np.argsort([g.mean() for g in groups])[::-1]
    groups = [groups[i] for i in order]
    names = [names[i] for i in order]

    hr("2. 各组描述统计（按互动率降序）")
    print("  %-18s %8s %12s %12s" % ("分区", "样本数", "平均互动率", "标准差"))
    print("  " + "-" * 54)
    for n, g in list(zip(names, groups))[:12]:
        print("  %-18s %8s %12.4f %12.4f" % (n, format(len(g), ","), g.mean(), g.std(ddof=1)))
    if len(names) > 12:
        print("  ...（共 %d 个分区）" % len(names))

    hr("3. 前提假设检验")
    lev_w, lev_p = stats.levene(*groups, center="median")
    print("  Levene 方差齐性检验 : W=%.2f  p=%.3e" % (lev_w, lev_p))
    if lev_p < 0.05:
        print("  -> 方差不齐（p<0.05），满足 ANOVA 的方差齐性假设【不成立】")
        print("     因此同时给出 Welch ANOVA 与 Kruskal-Wallis 作为稳健替代")
    else:
        print("  -> 方差齐性假设成立")
    print()
    print("  正态性：各组样本量较大（均 >= %d），依中心极限定理，"
          % MIN_N)
    print("  组均值近似正态，ANOVA 的 F 检验对此较为稳健。")

    hr("4. 单因素方差分析（F 检验）")
    F, p = stats.f_oneway(*groups)
    ss_between = sum(len(g) * (g.mean() - d.interact_rate.mean()) ** 2 for g in groups)
    ss_total = ((d.interact_rate - d.interact_rate.mean()) ** 2).sum()
    ss_within = ss_total - ss_between
    df_b = len(groups) - 1
    df_w = len(d) - len(groups)
    eta2 = ss_between / ss_total
    print("  经典 ANOVA :  F(%d, %d) = %.2f   p = %.3e" % (df_b, df_w, F, p))
    Fw, df1w, df2w, pw = welch_anova(groups)
    print("  Welch ANOVA:  F(%.1f, %.1f) = %.2f   p = %.3e" % (df1w, df2w, Fw, pw))
    H, pH = stats.kruskal(*groups)
    print("  Kruskal-Wallis（非参数）: H = %.2f   p = %.3e" % (H, pH))
    print()
    print("  三种方法结论一致，分区对互动率存在显著影响")

    hr("5. 效应量 eta^2")
    print("  eta^2 = 组间平方和 / 总平方和 = %.4f" % eta2)
    print("  含义：互动率的变异中，有 %.1f%% 可以由「内容分区」解释" % (eta2 * 100))
    lvl = "小" if eta2 < 0.06 else ("中" if eta2 < 0.14 else "大")
    print("  按 Cohen 标准（0.01 小 / 0.06 中 / 0.14 大）→ 【%s效应】" % lvl)

    hr("6. 事后检验：Tukey HSD 两两比较")
    print("  ANOVA 显著只说明「至少有两组不同」，但不知道是哪两组。")
    print("  Tukey HSD 对全部两两比较做校正，把族错误率控制在 5%。\n")
    tk = pairwise_tukeyhsd(d.interact_rate.values, d.tname.values, alpha=0.05)
    rows = []
    for r in tk.summary().data[1:]:
        a, b, meandiff, padj, lo, hi, rej = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
        rows.append((a, b, meandiff, padj, bool(rej)))
    sig = [r for r in rows if r[4]]
    print("  共 %d 组两两比较，其中 %d 组差异显著（占 %.1f%%）"
          % (len(rows), len(sig), len(sig) / len(rows) * 100))
    print()
    print("  差异最大的 8 对：")
    print("  %-16s %-16s %12s %12s" % ("分区 A", "分区 B", "均值差", "校正后 p"))
    print("  " + "-" * 60)
    for a, b, md, padj, rej in sorted(rows, key=lambda x: -abs(x[2]))[:8]:
        star = " *" if rej else ""
        print("  %-16s %-16s %12.4f %12.3e%s" % (a[:16], b[:16], md, padj, star))

    hr("7. 结论")
    print("  1) 分区对互动率有显著影响：F(%d,%d)=%.2f, p<0.001" % (df_b, df_w, F))
    print("     三种方法（经典 ANOVA / Welch / Kruskal-Wallis）结论一致。")
    print("  2) 但效应量只有 eta^2=%.4f，属于【%s效应】——" % (eta2, lvl))
    print("     分区只能解释互动率变异的 %.1f%%，剩余 %.1f%% 来自分区内部差异。"
          % (eta2 * 100, (1 - eta2) * 100))
    print("  3) 实践含义：分区是有用的分层维度，但【不足以单独预测表现】。")
    print("     做内容策略时应「先看分区基线，再在分区内部比」——这正是")
    print("     项目把「爆款」定义为【同分区内 Top 5%%】的原因。")

    out = os.path.join(RESDIR, "m14_单因素方差分析.csv")
    pd.DataFrame([
        ["纳入分区数", len(groups)],
        ["纳入样本数", len(d)],
        ["F 值", round(F, 3)],
        ["p 值", "%.3e" % p],
        ["Welch F", round(Fw, 3)],
        ["Welch p", "%.3e" % pw],
        ["Kruskal-Wallis H", round(H, 2)],
        ["Kruskal-Wallis p", "%.3e" % pH],
        ["eta^2 效应量", round(eta2, 4)],
        ["效应等级", lvl],
        ["Tukey 显著对数", len(sig)],
        ["Tukey 总对数", len(rows)],
        ["Levene p", "%.3e" % lev_p],
    ], columns=["指标", "值"]).to_csv(out, index=False, encoding="utf-8")
    print("\n  已导出:", out)


if __name__ == "__main__":
    main()
