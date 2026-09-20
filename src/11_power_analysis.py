# -*- coding: utf-8 -*-
r"""11_power_analysis.py —— A/B 实验功效分析（样本量测算）

背景：
  本项目的数据是历史观察数据，发布时间由创作者自己决定，无法做随机分流，
  所以因果部分用了 PSM / IPTW 准实验方法（见 10_psm_causal.py）。

  但 A/B 实验里有一项工作是【不需要实验数据】就能做的：功效分析。
  它回答一个在实验开始前就必须回答的问题——
  「要检出多大的效果，我需要多少样本？」

  这份分析用项目自身的基线数据（互动率均值与标准差）测算：
    · 不同相对提升幅度（MDE）下，每组所需样本量
    · 达到 80% / 90% 功效分别需要多少样本
    · 反过来：以本项目现有的样本规模，最小能检出多大的效应

方法：
  互动率是连续型比率变量，采用独立双样本 t 检验的功效公式
  （statsmodels TTestIndPower），双尾检验、两组等量分配。
"""
import os, sys, warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from statsmodels.stats.power import TTestIndPower

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import to_df

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMGDIR = os.path.join(ROOT, "images")
RESDIR = os.path.join(ROOT, "results")
os.makedirs(IMGDIR, exist_ok=True)

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
SEP = "=" * 76


def hr(t):
    print("\n" + SEP + "\n  " + t + "\n" + SEP)


def savefig_safe(fig, path, dpi=140, **kw):
    for i in range(3):
        try:
            fig.savefig(path, dpi=dpi, **kw)
            return path
        except OSError:
            path = path.replace(".png", "_new.png")
    return None


def main():
    print(SEP)
    print("  A/B 实验功效分析 · 样本量测算")
    print(SEP)

    hr("1. 基线参数（来自项目全样本）")
    df = to_df("SELECT interact_rate FROM video")
    r = pd.to_numeric(df.interact_rate, errors="coerce").dropna()
    mu0 = r.mean()
    sd = r.std(ddof=1)
    print("  样本量        : %s 条" % format(len(r), ","))
    print("  基线互动率 μ0 : %.5f  (%.2f%%)" % (mu0, mu0 * 100))
    print("  标准差   σ    : %.5f" % sd)
    print("  变异系数 σ/μ0 : %.2f  (互动率个体差异很大，这是样本量需求的根源)" % (sd / mu0))

    hr("2. 不同 MDE 下每组所需的样本量")
    print("  设定：双尾检验 α=0.05，两组等量分配（1:1）")
    print()
    print("  %-14s %-12s %10s %12s %12s" % ("相对提升", "绝对提升", "Cohen's d", "每组样本", "两组合计"))
    print("  " + "-" * 66)
    rows = []
    for rel in (0.03, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50):
        mu1 = mu0 * (1 + rel)
        d = (mu1 - mu0) / sd
        n = TTestIndPower().solve_power(effect_size=d, alpha=0.05, power=0.80, ratio=1.0)
        n = int(np.ceil(n))
        rows.append((rel, mu1 - mu0, d, n))
        print("  %-14s %-12s %10.4f %12s %12s"
              % ("+%.0f%%" % (rel * 100), "+%.5f" % (mu1 - mu0), d,
                 format(n, ","), format(n * 2, ",")))

    hr("3. 功效水平的影响（同一个 MDE，要 80% 还是 90% 功效）")
    print("  %-14s %14s %14s %12s" % ("相对提升", "80%功效每组", "90%功效每组", "多出的样本"))
    print("  " + "-" * 60)
    for rel in (0.05, 0.10, 0.20):
        d = (mu0 * rel) / sd
        n80 = int(np.ceil(TTestIndPower().solve_power(effect_size=d, alpha=0.05, power=0.80, ratio=1.0)))
        n90 = int(np.ceil(TTestIndPower().solve_power(effect_size=d, alpha=0.05, power=0.90, ratio=1.0)))
        print("  %-14s %14s %14s %12s"
              % ("+%.0f%%" % (rel * 100), format(n80, ","), format(n90, ","),
                 "+%.0f%%" % ((n90 / n80 - 1) * 100)))

    hr("4. 反过来看：以本项目的数据规模，最小能检出多大效应")
    print("  参照本项目实际可用的两组样本量（晚间 5,142 / 凌晨 915）：")
    print()
    for label, ngrp in (("晚间组规模 5,142", 5142), ("凌晨组规模 915", 915),
                        ("两组取小 915（受限端）", 915)):
        d = TTestIndPower().solve_power(effect_size=None, nobs1=ngrp,
                                        alpha=0.05, power=0.80, ratio=1.0)
        mde = d * sd / mu0
        print("  %-24s -> 可检出的最小相对提升 = %+.1f%%" % (label, mde * 100))

    hr("5. 业务结论")
    d10 = (mu0 * 0.10) / sd
    n10 = int(np.ceil(TTestIndPower().solve_power(effect_size=d10, alpha=0.05, power=0.80, ratio=1.0)))
    print("  · 互动率的个体差异极大（变异系数 %.1f），因此实验所需样本量偏高。" % (sd / mu0))
    print("  · 要检出 +10%% 的相对提升，每组需要约 %s 条样本。" % format(n10, ","))
    print("  · 本项目的凌晨组只有 915 条，若按同样规模做实验，")
    print("    只能稳定检出 +%d%% 以上的效应——更小的改动【验不出来】。"
          % round(TTestIndPower().solve_power(effect_size=None, nobs1=915, alpha=0.05,
                                              power=0.80, ratio=1.0) * sd / mu0 * 100))
    print()
    print("  -> 实践含义：提出「小改动带来小提升」的实验方案前，先算样本量。")
    print("     流量不够时，要么延长实验周期，要么接受只能检出大效应。")

    # ── 出图 ──────────────────────────────────────────────
    hr("6. 生成功效曲线图")
    fig, ax = plt.subplots(figsize=(9, 5.2))
    rels = np.linspace(0.02, 0.60, 60)
    for pw, color, ls in ((0.80, "#4C72B0", "-"), (0.90, "#DD8452", "--")):
        ns = []
        for rel in rels:
            d = (mu0 * rel) / sd
            ns.append(TTestIndPower().solve_power(effect_size=d, alpha=0.05,
                                                  power=pw, ratio=1.0))
        ax.plot(rels * 100, ns, color=color, lw=2.4, ls=ls,
                label="功效 %d%%" % (pw * 100))
    ax.axhline(915, color="#C44E52", lw=1.6, ls=":",
               label="本项目对照组实际规模 (915)")
    for rel, _, _, n in rows:
        if rel in (0.05, 0.10, 0.20):
            ax.plot(rel * 100, n, "o", color="#55A868", ms=7, zorder=5)
    ax.set_yscale("log")
    ax.set_xlabel("要检出的相对提升（MDE，%）", fontsize=11)
    ax.set_ylabel("每组所需样本量（对数刻度）", fontsize=11)
    ax.set_title("A/B 实验功效曲线：能检出的效果越小，所需样本量越大\n"
                 "（基线互动率 %.2f%%，α=0.05，双尾，1:1 分流）" % (mu0 * 100),
                 fontsize=12)
    ax.legend(fontsize=10)
    ax.grid(alpha=0.3, which="both")
    fp = os.path.join(IMGDIR, "fig7_AB实验功效曲线.png")
    savefig_safe(fig, fp, bbox_inches="tight")
    plt.close(fig)
    print("  已保存:", fp)

    # ── 导出 ──────────────────────────────────────────────
    out = os.path.join(RESDIR, "m13_AB功效分析.csv")
    pd.DataFrame(
        [["基线互动率", round(mu0, 5)], ["标准差", round(sd, 5)],
         ["变异系数", round(sd / mu0, 3)]] +
        [["MDE +%.0f%% 每组样本量" % (rel * 100), n] for rel, _, _, n in rows],
        columns=["指标", "值"]).to_csv(out, index=False, encoding="utf-8")
    print("  已导出:", out)


if __name__ == "__main__":
    main()
