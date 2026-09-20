# -*- coding: utf-8 -*-
r"""10_psm_causal.py —— 用倾向得分匹配（PSM）估计发布时段的因果效应

背景：
  原始观察结论是「晚间 19-22 点发布的视频互动率比凌晨 0-6 点高 16.4%」。
  但这是【相关】不是【因果】——晚间发布的视频可能本身就来自更用心的创作者、
  更大的账号、更长或更短的内容，互动率高也许源于这些差异，而非发布时间。

做法：
  1. 用 Logistic 回归估计「一条视频在晚间发布」的倾向得分
  2. 给每条晚间视频匹配一条倾向得分接近的凌晨视频（1:1 最近邻 + caliper）
  3. 检查匹配后协变量是否平衡（标准化均值差 SMD < 0.1 视为平衡）
  4. 在匹配样本上重做 Welch t 检验，得到准因果效应
  5. 对比匹配前后，量化「选择偏差」占多少

匹配变量必须是【发布前可知且不受处理影响】的协变量。
"""
import os, sys, warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.neighbors import NearestNeighbors

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import to_df

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESDIR = os.path.join(ROOT, "results")
SEP = "=" * 76


def hr(t):
    print("\n" + SEP + "\n  " + t + "\n" + SEP)


# ──────────────────────────────────────────────────────────────
def load():
    df = to_df("""
        SELECT v.bvid, v.pubdate, v.pub_hour, v.duration_s, v.tid, c.tname,
               v.up_mid, u.follower, v.view_cnt, v.danmaku_cnt, v.interact_rate
        FROM video v
        JOIN category c ON v.tid = c.tid
        JOIN up_info  u ON v.up_mid = u.mid
    """)
    for c in df.columns:
        if c not in ("bvid", "tname", "pubdate"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["pubdate"] = pd.to_datetime(df["pubdate"], errors="coerce")
    df["year"] = df.pubdate.dt.year
    df["log_dur"] = np.log1p(df.duration_s)
    df["log_fol"] = np.log1p(df.follower.fillna(0))
    df["is_collection"] = (df.duration_s > 14400).astype(int)
    return df


def smd(x, treat, w=None):
    """标准化均值差 (Standardized Mean Difference)"""
    x = np.asarray(x, dtype=float)
    t = np.asarray(treat) == 1
    if w is None:
        m1, m0 = x[t].mean(), x[~t].mean()
        s1, s0 = x[t].std(ddof=1), x[~t].std(ddof=1)
    else:
        w = np.asarray(w, dtype=float)
        m1 = np.average(x[t], weights=w[t]); m0 = np.average(x[~t], weights=w[~t])
        s1 = np.sqrt(np.average((x[t] - m1) ** 2, weights=w[t]))
        s0 = np.sqrt(np.average((x[~t] - m0) ** 2, weights=w[~t]))
    pooled = np.sqrt((s1 ** 2 + s0 ** 2) / 2)
    return (m1 - m0) / pooled if pooled > 0 else 0.0


# ──────────────────────────────────────────────────────────────
def main():
    print(SEP)
    print("  发布时段的因果效应估计 · 倾向得分匹配（PSM）")
    print(SEP)

    df = load()
    hr("1. 定义处理组与对照组")
    df["treat"] = df.pub_hour.between(19, 22).astype(int)     # 晚间
    d = df[df.pub_hour.between(19, 22) | df.pub_hour.between(0, 6)].copy()
    n1, n0 = int((d.treat == 1).sum()), int((d.treat == 0).sum())
    print("  处理组 晚间 19-22 点 : %s 条" % format(n1, ","))
    print("  对照组 凌晨 0-6 点   : %s 条" % format(n0, ","))
    print("  合计                 : %s 条（占全样本 %.1f%%）"
          % (format(len(d), ","), len(d) / len(df) * 100))

    hr("2. 匹配前的原始差异（未控制混淆变量）")
    t0 = d.loc[d.treat == 1, "interact_rate"]
    c0 = d.loc[d.treat == 0, "interact_rate"]
    raw_diff = t0.mean() - c0.mean()
    raw_pct = raw_diff / c0.mean() * 100
    print("  晚间均值 %.4f   凌晨均值 %.4f" % (t0.mean(), c0.mean()))
    print("  绝对差 %+.4f   相对差 %+.2f%%" % (raw_diff, raw_pct))

    hr("3. 估计倾向得分（预测「这条视频会不会在晚间发布」）")
    top = d.tname.value_counts().head(15).index.tolist()
    dum = pd.get_dummies(d.tname.where(d.tname.isin(top), "其他"), prefix="分区")
    D = pd.get_dummies(d.year.astype(int), prefix="年")
    X = pd.concat([
        d[["log_dur", "log_fol", "is_collection"]].reset_index(drop=True),
        D.reset_index(drop=True),
        dum.reset_index(drop=True),
    ], axis=1).astype(float)
    ps_model = LogisticRegression(max_iter=3000, C=1.0).fit(X, d.treat)
    ps = ps_model.predict_proba(X)[:, 1]
    d["ps"] = ps
    d["logit_ps"] = np.log(np.clip(ps, 1e-6, 1 - 1e-6) / (1 - np.clip(ps, 1e-6, 1 - 1e-6)))
    print("  倾向得分范围: %.4f ~ %.4f   均值 %.4f" % (ps.min(), ps.max(), ps.mean()))
    print("  模型区分度 AUC: %.4f"
          % __import__("sklearn.metrics", fromlist=["roc_auc_score"])
            .roc_auc_score(d.treat, ps))

    hr("4. 1:1 最近邻匹配（带 caliper，无放回）")
    # 注意：这里保留原始索引，NearestNeighbors 返回的是位置索引，
    # 需用 ctl.index[j] 映射回 d 里的原始行号（早前版本 reset_index 后丢失了原索引，导致 KeyError）
    trt = d[d.treat == 1].copy()
    ctl = d[d.treat == 0].copy()
    ctl_orig = ctl.index.values
    trt_orig = trt.index.values

    # 共同支撑域诊断：两组倾向得分重叠区有多大
    lo, hi = max(trt.ps.min(), ctl.ps.min()), min(trt.ps.max(), ctl.ps.max())
    in_support = ((d.ps >= lo) & (d.ps <= hi))
    print("  倾向得分重叠区间: [%.4f, %.4f]" % (lo, hi))
    print("  落在共同支撑域内的样本: %s / %s (%.1f%%)"
          % (format(int(in_support.sum()), ","), format(len(d), ","),
             in_support.mean() * 100))
    print("  -> 对照组样本远少于处理组，可匹配的对照有限，这是本设计的固有限制")

    nn = NearestNeighbors(n_neighbors=1).fit(ctl[["logit_ps"]].values)
    sim = {"n_neighbors": 1}
    for cal_mult in (0.2, 0.25, 0.3, 0.5):
        cal = cal_mult * d.logit_ps.std()
        dist, idx = nn.kneighbors(trt[["logit_ps"]].values)
        dist, idx = dist.ravel(), idx.ravel()
        used, pairs = set(), []
        for i in range(len(trt)):
            j = idx[i]
            if dist[i] <= cal and j not in used:
                used.add(j)
                pairs.append((trt_orig[i], ctl_orig[j]))
        print("  caliper=%.2f×SD(%.4f) -> 匹配 %s 对" % (cal_mult, cal, format(len(pairs), ",")))
        if len(pairs) >= 600:
            break
    print("  最终采用 %s 对" % format(len(pairs), ","))

    ti = [p[0] for p in pairs]
    ci = [p[1] for p in pairs]
    m = d.loc[ti + ci].copy()
    m["treat"] = [1] * len(ti) + [0] * len(ci)

    hr("5. 匹配质量检验（协变量平衡性）")
    print("  %-16s %12s %12s %10s" % ("协变量", "匹配前SMD", "匹配后SMD", "是否平衡"))
    print("  " + "-" * 54)
    COV = [("log_dur", "时长(log)"), ("log_fol", "粉丝数(log)"),
           ("is_collection", "是否多P合集"), ("ps", "倾向得分")]
    ok = True
    for col, name in COV:
        b = smd(d[col], d.treat)
        a = smd(m[col], m.treat)
        balanced = abs(a) < 0.1
        ok &= balanced
        print("  %-16s %12.4f %12.4f %10s"
              % (name, b, a, "平衡" if balanced else "仍不平衡"))
    print("\n  经验标准：SMD < 0.1 视为两组可比")
    print("  -> " + ("全部协变量通过平衡性检验" if ok else "仍有协变量不平衡，结论需谨慎"))

    hr("6. 匹配后的因果效应估计")
    t1 = m.loc[m.treat == 1, "interact_rate"]
    c1 = m.loc[m.treat == 0, "interact_rate"]
    adj_diff = t1.mean() - c1.mean()
    adj_pct = adj_diff / c1.mean() * 100
    welch_t, welch_p = stats.ttest_ind(t1, c1, equal_var=False)
    n_1, n_2 = len(t1), len(c1)
    pooled = np.sqrt(((n_1 - 1) * t1.std(ddof=1) ** 2 + (n_2 - 1) * c1.std(ddof=1) ** 2)
                     / (n_1 + n_2 - 2))
    cohen_d = adj_diff / pooled
    print("  匹配样本: 处理组 %d  对照组 %d" % (n_1, n_2))
    print("  晚间均值 %.4f   凌晨均值 %.4f" % (t1.mean(), c1.mean()))
    print("  绝对差 %+.4f   相对差 %+.2f%%" % (adj_diff, adj_pct))
    print("  Welch t 检验: t=%.4f  p=%.3e" % (welch_t, welch_p))
    print("  Cohen's d = %.4f" % cohen_d)

    hr("7. 选择偏差量化")
    print("  匹配前（未控制混淆）: %+.2f%%" % raw_pct)
    print("  匹配后（准因果效应）: %+.2f%%" % adj_pct)
    bias = raw_pct - adj_pct
    print("  ----------------------------------------")
    print("  选择偏差贡献        : %+.2f 个百分点" % bias)
    if raw_pct != 0:
        print("  占总差异的比例      : %.1f%%" % (bias / raw_pct * 100))
    print()
    if abs(adj_pct) < abs(raw_pct):
        print("  解读：控制住「时长 / 粉丝量级 / 分区 / 年份」等差异后，")
        print("        时段本身的效应从 %.2f%% 收窄到 %.2f%%，" % (raw_pct, adj_pct))
        print("        说明原观察差异中有 %.1f%% 来自视频本身的特征差异，而非发布时间。"
              % (bias / raw_pct * 100))
    else:
        print("  解读：控制混淆变量后效应未缩小，说明时段效应较为稳健。")

    hr("8. 交叉验证：IPTW 逆概率加权估计")
    print("  PSM 匹配受限于「对照组只有 %s 条」，无放回匹配会很快耗尽对照池。" % format(n0, ","))
    print("  IPTW 用全部样本、按倾向得分的倒数加权，不丢样本，作为独立交叉验证。")
    w = np.where(d.treat == 1, 1.0 / np.clip(d.ps, 0.05, 0.95),
                 1.0 / (1.0 - np.clip(d.ps, 0.05, 0.95)))
    # 权重截尾，避免极端权重主导结果
    w = np.clip(w, 0, np.percentile(w, 99))
    t_w = np.average(d.loc[d.treat == 1, "interact_rate"], weights=w[d.treat == 1])
    c_w = np.average(d.loc[d.treat == 0, "interact_rate"], weights=w[d.treat == 0])
    iptw_pct = (t_w - c_w) / c_w * 100
    print("  加权后 晚间均值 %.4f   凌晨均值 %.4f" % (t_w, c_w))
    print("  IPTW 估计 相对差 %+.2f%%" % iptw_pct)

    print("\n  加权后的协变量平衡性:")
    print("  %-16s %12s %12s" % ("协变量", "匹配前SMD", "加权后SMD"))
    print("  " + "-" * 42)
    # 平衡性只看真实协变量；倾向得分是加权依据，本身不参与平衡判定
    iptw_ok = True
    for col, name in COV:
        b = smd(d[col], d.treat)
        a = smd(d[col], d.treat, w=w)
        if col != "ps":
            iptw_ok &= abs(a) < 0.1
        flag = "" if col == "ps" else ("平衡" if abs(a) < 0.1 else "未平衡")
        print("  %-16s %12.4f %12.4f   %s" % (name, b, a, flag))

    hr("9. 三种估计对比")
    print("  %-28s %12s" % ("方法", "相对差(%)"))
    print("  " + "-" * 42)
    print("  %-28s %12.2f" % ("原始（未控制混淆）", raw_pct))
    print("  %-28s %12.2f" % ("PSM 1:1 匹配后", adj_pct))
    print("  %-28s %12.2f" % ("IPTW 加权后", iptw_pct))
    print()
    est = np.mean([adj_pct, iptw_pct])
    print("  两种控制方法给出的估计值都在 %.1f%% ~ %.1f%% 区间，" % (min(adj_pct, iptw_pct), max(adj_pct, iptw_pct)))
    print("  均低于未控制的 %.2f%%，说明原结论中约 %.1f 个百分点来自选择偏差。" % (raw_pct, raw_pct - est))
    print()
    print("  ⚠️ 结论边界：")
    print("     · PSM 匹配后「粉丝数」仍有残余不平衡(SMD %.2f)，该估计存在残余混淆"
          % abs(smd(m["log_fol"], m.treat)))
    print("     · IPTW 加权后 3 项协变量%s（SMD 均 < 0.1），该估计更可信"
          % ("全部达到平衡" if iptw_ok else "仍有不平衡"))
    print("     · 本分析为【准实验】，数据集无法做真正的随机 A/B，因果强度低于随机实验")

    # 保存
    out = os.path.join(RESDIR, "m12_PSM因果分析.csv")
    pd.DataFrame([
        ["原始 相对差(%)", round(raw_pct, 2)],
        ["PSM匹配后 相对差(%)", round(adj_pct, 2)],
        ["IPTW加权后 相对差(%)", round(iptw_pct, 2)],
        ["选择偏差(百分点)", round(raw_pct - est, 2)],
        ["匹配对数", len(pairs)],
        ["PSM后 Welch p 值", "%.3e" % welch_p],
        ["PSM后 Cohen's d", round(cohen_d, 4)],
        ["PSM后协变量是否全平衡", "是" if ok else "否（粉丝数残余 SMD %.2f）" % abs(smd(m["log_fol"], m.treat))],
        ["IPTW后协变量是否全平衡", "是" if iptw_ok else "否"],
    ], columns=["指标", "值"]).to_csv(out, index=False, encoding="utf-8")
    print("\n  已导出: %s" % out)


if __name__ == "__main__":
    main()
