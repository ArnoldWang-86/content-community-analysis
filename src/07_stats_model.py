# -*- coding: utf-8 -*-
r"""07_stats_model.py —— 统计检验与建模

四部分:
  Part 1 时段效应检验 → 输出差异百分比与 Cohen's d
  Part 2 TF-IDF 标题关键词 → 高互动内容在说什么
  Part 3 KMeans 内容分群 → 输出 k 值与轮廓系数
  Part 4 爆款预测 + SHAP → 输出特征归因

设计要点:
  特征工程严禁数据泄漏 —— 只用「发布前可知」的特征预测爆款。
    播放/点赞/投币/收藏 是「结果」，拿它们预测爆款等于作弊（AUC 会虚高到 0.99）。
  p 值大样本下必然显著，所以重点看「效应量 Cohen's d」。
    1.9 万样本里 p<0.001 毫无信息量，要对业务有意义必须看效应量。
  爆款率仅 5%，类别极不平衡 → 主指标用 PR-AUC 而非 AUC，并开 class_weight。

用法:
    python src/07_stats_model.py
    python src/07_stats_model.py --no-shap # 跳过 SHAP（快一些）
"""
import argparse, os, sys, time, warnings

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
RESDIR = os.path.join(ROOT, "results")
IMGDIR = os.path.join(ROOT, "images")
os.makedirs(RESDIR, exist_ok=True)
os.makedirs(IMGDIR, exist_ok=True)

SEP = "=" * 78
SUMMARY = {}


def hr(t):
    print("\n" + SEP + "\n " + t + "\n" + SEP)


def savefig_safe(fig, path, dpi=140, **kw):
    """保存图片；目标被占用时（如 Windows 照片应用开着）自动重试并降级命名，
    避免整个分析流程因为一个图片被锁而中断。"""
    import matplotlib.pyplot as plt
    for _ in range(3):
        try:
            fig.savefig(path, dpi=dpi, **kw)
            plt.close(fig)
            return path
        except (OSError, PermissionError):
            time.sleep(1.2)
    alt = path.replace(".png", "_new.png")
    try:
        fig.savefig(alt, dpi=dpi, **kw)
        plt.close(fig)
        print(" [warn] %s 被占用，改存为 %s"
              % (os.path.basename(path), os.path.basename(alt)))
        return alt
    except Exception as e:
        plt.close(fig)
        print(" [error] 图片保存失败: %s" % str(e)[:80])
        return None


def setup_cn_font():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


# ==============================================================
def load_data():
    from db import to_df
    sql = """
        SELECT v.bvid, v.title, v.pubdate, v.pub_hour, v.pub_weekday, v.duration_s,
               v.tid, c.tname, v.up_mid, u.follower,
               v.view_cnt, v.like_cnt, v.coin_cnt, v.fav_cnt,
               v.share_cnt, v.danmaku_cnt, v.reply_cnt, v.interact_rate
        FROM video v
        JOIN category c ON v.tid = c.tid
        JOIN up_info u ON v.up_mid = u.mid
    """
    df = to_df(sql)
    # 类型兜底：确保数值列是真数值（防驱动返回字符串）
    for c in ["pub_hour", "pub_weekday", "duration_s", "tid", "up_mid", "follower",
              "view_cnt", "like_cnt", "coin_cnt", "fav_cnt", "share_cnt",
              "danmaku_cnt", "reply_cnt", "interact_rate"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["pubdate"] = pd.to_datetime(df["pubdate"], errors="coerce") # 注意别被上面转成 NaN
    df = df.dropna(subset=["interact_rate", "view_cnt"]).reset_index(drop=True)
    # 派生：各种比率（便于分群与描述）
    df["like_rate"] = df.like_cnt / df.view_cnt
    df["coin_rate"] = df.coin_cnt / df.view_cnt
    df["fav_rate"] = df.fav_cnt / df.view_cnt
    df["danmaku_k"] = df.danmaku_cnt / df.view_cnt * 1000
    df["is_collection"] = (df.duration_s > 14400).astype(int)
    df["log_duration"] = np.log1p(df.duration_s)
    df["log_follower"] = np.log1p(df.follower.fillna(0))
    df["title_len"] = df.title.str.len()
    # 同分区互动率 Top 5% 视为爆款
    df["rank_pct"] = df.groupby("tid").interact_rate.rank(pct=True, ascending=False)
    df["is_hit"] = (df.rank_pct <= 0.05).astype(int)
    return df


# ==============================================================
# Part 1 时段效应检验
# ==============================================================
def part1_time_effect(df):
    hr("Part 1 · 发布时段对互动率的影响（严格统计检验）")

    late = df.loc[df.pub_hour.between(19, 22), "interact_rate"].values # 晚间 19-22
    dawn = df.loc[df.pub_hour.between(0, 6), "interact_rate"].values # 凌晨 0-6

    m1, m2 = late.mean(), dawn.mean()
    diff_pct = (m1 - m2) / m2 * 100
    n1, n2 = len(late), len(dawn)

    print(f" 晚间(19-22点) 样本 {n1:>6,} 平均互动率 {m1:.4f}")
    print(f" 凌晨(0-6点) 样本 {n2:>6,} 平均互动率 {m2:.4f}")
    print(f" 绝对差 {(m1-m2):+.4f} 相对差 {diff_pct:+.2f}%")

    # ---- 前提检验 ----
    print("\n [前提检验]")
    s1 = np.random.RandomState(0).choice(late, min(5000, n1), replace=False)
    s2 = np.random.RandomState(1).choice(dawn, min(5000, n2), replace=False)
    w1, p1 = stats.shapiro(s1)
    w2, p2 = stats.shapiro(s2)
    print(f" Shapiro-Wilk 正态性 晚间 p={p1:.3e} 凌晨 p={p2:.3e}"
          f" -> {'均非正态' if p1<0.05 and p2<0.05 else '至少一组近似正态'}")
    lev_w, lev_p = stats.levene(late, dawn)
    print(f" Levene 方差齐性 p={lev_p:.3e}"
          f" -> {'方差不齐，应用 Welch' if lev_p<0.05 else '方差齐'}")

    # ---- Welch t 检验 ----
    t, p = stats.ttest_ind(late, dawn, equal_var=False)
    # ---- 非参数交叉验证 ----
    u, pu = stats.mannwhitneyu(late, dawn, alternative="two-sided")
    print("\n [显著性检验]")
    print(f" Welch t 检验 t={t:.4f} p={p:.3e}")
    print(f" Mann-Whitney U U={u:.0f} p={pu:.3e} (非参数，不依赖正态假设)")
    print(f" 平均秩(晚间/凌晨) {stats.rankdata(np.r_[late,dawn])[:n1].mean():.1f} / "
          f"{stats.rankdata(np.r_[late,dawn])[n1:].mean():.1f}")

    # ---- 效应量 ----
    n_1, n_2 = len(late), len(dawn)
    pooled = np.sqrt(((n_1-1)*late.std(ddof=1)**2 + (n_2-1)*dawn.std(ddof=1)**2) / (n_1+n_2-2))
    d = (m1 - m2) / pooled
    # Cliff's delta 作为非参数效应量
    gt = sum((late[:, None] > dawn[None, :]).sum(1)) if n1*n2 < 4e8 else None
    print("\n [效应量] <- 比 p 值重要：大样本下 p 必然是 0")
    print(f" Cohen's d = {d:.4f} "
          f"({'微小' if abs(d)<0.2 else '小' if abs(d)<0.5 else '中等' if abs(d)<0.8 else '大'}效应)")

    # ---- Bootstrap 置信区间 ----
    rs = np.random.RandomState(42)
    boots = [rs.choice(late, n1).mean() - rs.choice(dawn, n2).mean() for _ in range(2000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    print(f" 差值 95% 置信区间 (Bootstrap 2000次): [{lo:+.4f}, {hi:+.4f}]")
    print(f" -> {'区间不含0，差异稳健' if lo>0 or hi<0 else '区间含0，差异不稳健'}")

    # ---- 备份口径：18-22 vs 0-5 ----
    l2 = df.loc[df.pub_hour.between(18, 22), "interact_rate"]
    d2 = df.loc[df.pub_hour.between(0, 5), "interact_rate"]
    t2, p2b = stats.ttest_ind(l2, d2, equal_var=False)
    print(f"\n [备用口径 18-22 vs 0-5] 差 {(l2.mean()-d2.mean())/d2.mean()*100:+.2f}% "
          f"p={p2b:.3e}")

    SUMMARY.update({
        "晚间样本": n1, "凌晨样本": n2,
        "晚间均值": m1, "凌晨均值": m2,
        "差异百分比": diff_pct, "welch_p": p, "mannwhitney_p": pu,
        "cohens_d": d, "CI_low": lo, "CI_high": hi,
    })

    # ---- 画图 ----
    plt = setup_cn_font()
    g = df.groupby("pub_hour").interact_rate.agg(["mean", "count"])
    fig, ax1 = plt.subplots(figsize=(11, 4.5))
    ax1.bar(g.index, g["mean"], color="#4C72B0", alpha=.85)
    ax1.axhline(df.interact_rate.mean(), color="crimson", ls="--", lw=1.2,
                label=f"全样本均值 {df.interact_rate.mean():.4f}")
    ax1.axvspan(18.5, 22.5, color="orange", alpha=.18, label="晚间 19-22点")
    ax1.axvspan(-0.5, 6.5, color="gray", alpha=.15, label="凌晨 0-6点")
    ax1.set_xlabel("发布小时"); ax1.set_ylabel("平均互动率")
    ax1.set_title("各发布小时的平均互动率（19,456 条视频）")
    ax1.set_xticks(range(0, 24)); ax1.legend(fontsize=9)
    plt.tight_layout()
    fp = os.path.join(IMGDIR, "fig1_发布时段效应.png")
    savefig_safe(fig, fp)
    print(f"\n 图已保存: {fp}")


# ==============================================================
# Part 2 TF-IDF 标题关键词
# ==============================================================
def part2_tfidf(df):
    hr("Part 2 · 高互动 vs 低互动的标题关键词（jieba + TF-IDF）")
    import jieba
    from sklearn.feature_extraction.text import TfidfVectorizer

    stop = set("的 了 是 在 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 "
               "没有 看 好 自己 这 那 他 她 它 与 及 或 等 为 对 从 被 把 让 给 之 "
               "版 全 集 第 期 篇 个 中 下 大 小 新".split())

    def cut(t):
        return " ".join(w for w in jieba.cut(str(t)) if len(w) > 1 and w not in stop)

    print(" 正在分词 ...")
    df = df.copy()
    df["tokens"] = df.title.map(cut)

    q_hi, q_lo = df.interact_rate.quantile(0.75), df.interact_rate.quantile(0.25)
    hi = df[df.interact_rate >= q_hi]
    lo = df[df.interact_rate <= q_lo]
    print(f" 高互动组 {len(hi):,} 条 (互动率>={q_hi:.4f})")
    print(f" 低互动组 {len(lo):,} 条 (互动率<={q_lo:.4f})")

    vec = TfidfVectorizer(max_features=3000, min_df=20, token_pattern=r"(?u)\S+")
    vec.fit(df.tokens)
    m_hi = np.asarray(vec.transform(hi.tokens).mean(axis=0)).ravel()
    m_lo = np.asarray(vec.transform(lo.tokens).mean(axis=0)).ravel()
    words = np.array(vec.get_feature_names_out())
    lift = m_hi / (m_lo + 1e-9)

    print("\n [高互动组特有的关键词 Top 20] (按 TF-IDF 均值排序)")
    for w, v in sorted(zip(words, m_hi), key=lambda x: -x[1])[:20]:
        print(f" {w:<14} 高互动TF-IDF={v:.5f} 低互动={m_lo[list(words).index(w)]:.5f}")

    # 直接算「高/低」比值会被稀有词污染（分母≈0 时倍数爆到几百万，毫无意义）。
    # 改用带平滑的对数几率比（Monroe et al. 方法），并做方差归一化得到 z 值。
    c_hi = np.asarray((vec.transform(hi.tokens) > 0).sum(axis=0)).ravel()
    c_lo = np.asarray((vec.transform(lo.tokens) > 0).sum(axis=0)).ravel()
    n_hi, n_lo = len(hi), len(lo)
    a = 0.01
    lo_odds = (np.log((c_hi + a) / (n_hi - c_hi + a))
               - np.log((c_lo + a) / (n_lo - c_lo + a)))
    z = lo_odds / np.sqrt(1.0 / (c_hi + a) + 1.0 / (c_lo + a))
    lift = z

    print("\n [区分度最高的词 Top 15] (对数几率比 z 值，已平滑去稀有词噪声)")
    print(f" {'词':<14}{'z值':>8}{'高互动出现':>10}{'低互动出现':>10}")
    for i in np.argsort(-z)[:15]:
        print(f" {words[i]:<14}{z[i]:>8.2f}{c_hi[i]:>10,}{c_lo[i]:>10,}")
    print("\n [低互动组特有词 Top 8]")
    for i in np.argsort(z)[:8]:
        print(f" {words[i]:<14}{z[i]:>8.2f}{c_hi[i]:>10,}{c_lo[i]:>10,}")

    top_words = [words[i] for i in np.argsort(-m_hi)[:20]]
    SUMMARY["高互动关键词Top10"] = "、".join(top_words[:10])

    pd.DataFrame({"词": words, "高互动TFIDF": m_hi, "低互动TFIDF": m_lo, "倍数": lift}) \
      .sort_values("倍数", ascending=False) \
      .to_csv(os.path.join(RESDIR, "m02_标题关键词区分度.csv"),
              index=False, encoding="utf-8")
    print(f"\n 已导出: results\\m02_标题关键词区分度.csv")


# ==============================================================
# Part 3 KMeans 内容分群
# ==============================================================
def part3_kmeans(df):
    hr("Part 3 · 内容分群（KMeans + 肘部法 + 轮廓系数）")
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    # 特征设计：用「互动结构占比」而不是绝对比率。
    # 原因：绝对比率右偏严重（投币率分布在 0~5.79 之间），直接聚类会被极值主导，
    # 结果退化成"异常值 vs 正常值"（实测 k=2 轮廓系数 0.85 但只有两类）。
    # 改用占比后，刻画的是「这类内容靠什么获得互动」——
    # 弹幕驱动 / 收藏驱动 / 投币驱动，业务含义清晰，轮廓系数 0.58。
    R4 = ["like_rate", "coin_rate", "fav_rate", "danmaku_k"]
    tot = df[R4].sum(axis=1).replace(0, np.nan)
    X = df[R4].div(tot, axis=0).fillna(0)
    X["log_total"] = np.log1p(df.interact_rate * 1000) # 总量维度
    Xs = StandardScaler().fit_transform(X.values)

    inertias, sils = [], []
    ks = range(2, 11)
    print(f" {'k':>3} {'惯性':>14} {'轮廓系数':>10}")
    best_k, best_s = None, -1
    for k in ks:
        km = KMeans(n_clusters=k, n_init=10, random_state=42).fit(Xs)
        inertias.append(km.inertia_)
        sil = __import__("sklearn.metrics", fromlist=["silhouette_score"]) \
                .silhouette_score(Xs, km.labels_, sample_size=5000, random_state=42)
        sils.append(sil)
        mark = ""
        if sil > best_s:
            best_s, best_k = sil, k
            mark = " ← 当前最优"
        print(f" {k:>3} {km.inertia_:>14,.0f} {sil:>10.4f}{mark}")

    print(f"\n 最优 k = {best_k}，轮廓系数 = {best_s:.4f}")
    km = KMeans(n_clusters=best_k, n_init=10, random_state=42).fit(Xs)
    df = df.copy()
    df["cluster"] = km.labels_

    print(f"\n [各群画像]")
    df["_like_share"] = X.iloc[:, 0].values
    df["_coin_share"] = X.iloc[:, 1].values
    df["_fav_share"] = X.iloc[:, 2].values
    df["_danm_share"] = X.iloc[:, 3].values
    prof = df.groupby("cluster").agg(
        视频数=("bvid", "size"),
        平均互动率=("interact_rate", "mean"),
        平均播放=("view_cnt", "mean"),
        平均时长分=("duration_s", lambda s: s.mean()/60),
        平均粉丝=("follower", "mean"),
        点赞率=("like_rate", "mean"),
        投币率=("coin_rate", "mean"),
        收藏率=("fav_rate", "mean"),
        弹幕密度=("danmaku_k", "mean"),
        点赞占比=("_like_share", "mean"),
        投币占比=("_coin_share", "mean"),
        收藏占比=("_fav_share", "mean"),
        弹幕占比=("_danm_share", "mean"),
    ).round(4)
    prof["主分区"] = df.groupby("cluster").tname.agg(lambda s: s.mode().iat[0])
    prof["占比%"] = (prof.视频数 / len(df) * 100).round(1)
    print(prof.to_string())
    prof.to_csv(os.path.join(RESDIR, "m03_内容分群画像.csv"), encoding="utf-8")

    SUMMARY["KMeans_k"] = best_k
    SUMMARY["轮廓系数"] = best_s

    plt = setup_cn_font()
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
    a1.plot(list(ks), inertias, "o-", color="#4C72B0")
    a1.set_xlabel("k"); a1.set_ylabel("惯性 (Inertia)"); a1.set_title("肘部法")
    a2.plot(list(ks), sils, "o-", color="#DD8452")
    a2.axvline(best_k, color="crimson", ls="--", label=f"最优 k={best_k}")
    a2.set_xlabel("k"); a2.set_ylabel("轮廓系数"); a2.set_title("轮廓系数"); a2.legend()
    plt.tight_layout()
    fp = os.path.join(IMGDIR, "fig2_KMeans选k.png")
    savefig_safe(fig, fp)
    print(f"\n 图已保存: {fp}")
    return df


# ==============================================================
# Part 4 爆款预测模型（含特征有效性审查 + 消融实验）
# ==============================================================
def build_features(df, tier=2):
    """按可信度分层构造特征

    Tier 1 完全安全 : 发布前可知，且发布后不再变化
    Tier 2 轻微隐患 : 多P合集的 duration 是【所有分P累计时长】，会随更新增长
    Tier 3 明显泄漏 : 粉丝数是【采集时】的值而非【发布时】的值 —— 时间穿越
    """
    from db import to_df
    tc = to_df("SELECT bvid, COUNT(*) AS n FROM video_tag GROUP BY bvid")
    tag_cnt = df.bvid.map(dict(zip(tc.bvid, tc.n))).fillna(0)
    top_cats = df.tname.value_counts().head(15).index.tolist()
    cat_dum = pd.get_dummies(df.tname.where(df.tname.isin(top_cats), "其他"),
                             prefix="分区")
    parts = [df[["pub_hour", "pub_weekday", "title_len"]].reset_index(drop=True),
             pd.Series(tag_cnt.values, name="标签数")]
    if tier >= 2:
        parts.insert(0, df[["log_duration", "is_collection"]].reset_index(drop=True))
    if tier >= 3:
        parts.insert(0, df[["log_follower"]].reset_index(drop=True))
    parts.append(cat_dum.reset_index(drop=True))
    return pd.concat(parts, axis=1).astype(float)


def cv_eval(X, y, model):
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    proba = cross_val_predict(model, X, y,
                              cv=StratifiedKFold(5, shuffle=True, random_state=42),
                              method="predict_proba", n_jobs=-1)[:, 1]
    return roc_auc_score(y, proba), average_precision_score(y, proba), proba


def part4_model(df, do_shap=True):
    hr("Part 4 · 爆款预测模型")
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    y = df.is_hit.values
    base = y.mean()
    print(" 样本 %s 正例(爆款) %s 正例率 %.2f%%"
          % (format(len(y), ","), format(int(y.sum()), ","), base * 100))
    print(" 目标口径: 同分区互动率 Top 5%（已做分区归一化，分区信息被剔除）")

    RF = dict(n_estimators=300, min_samples_leaf=5,
              class_weight="balanced_subsample", n_jobs=-1, random_state=42)

    # ── 4.1 特征有效性审查 ─────────────────────────────────
    hr("4.1 特征有效性审查（这一步决定模型可不可信）")
    print(" 判断标准：预测时拿得到 + 发布后不随时间变化")
    print()
    print(" Tier 1 · 完全安全")
    print(" pub_hour / pub_weekday 发布时刻，发布前决定")
    print(" title_len / 标签数 发布时确定")
    print(" 分区 发布时选择")
    print()
    print(" Tier 2 · 轻微隐患")
    print(" log_duration / is_collection")
    print(" [!] B站对多P合集返回的是【所有分P累计时长】，占样本 %.1f%%" % (df.is_collection.mean() * 100))
    print(" 创作者后续更新会让该值在发布后继续增长，隐含了未来的信息")
    print()
    print(" Tier 3 · 明显泄漏")
    print(" log_follower")
    print(" [!] 粉丝数是【2026-09 采集时的当前值】，不是【视频发布时的值】")
    print(" 对老视频而言，“现在粉丝多”部分源于“过去爆过”——属于时间穿越")
    yr = pd.to_datetime(df.pubdate, errors="coerce").dt.year
    chk = df.assign(年=yr).groupby("年").follower.mean()
    print(" 实证: " + " ".join("%d年%.0f万" % (int(k), v / 10000)
                                       for k, v in chk.items() if k >= 2025))

    # ── 4.2 消融实验 ───────────────────────────────────────
    hr("4.2 消融实验：逐层加入可疑特征，看 AUC 变化")
    tiers = [("Tier1 仅完全安全特征", 1),
             ("Tier1+2 加时长/合集", 2),
             ("Tier1+2+3 全量(含泄漏)", 3)]
    abl = []
    print(" %-26s %5s %10s %10s" % ("特征集", "特征数", "AUC", "PR-AUC"))
    for name, t in tiers:
        X = build_features(df, t)
        auc, ap, _ = cv_eval(X, y, RandomForestClassifier(**RF))
        abl.append((name, X.shape[1], auc, ap))
        print(" %-26s %5d %10.4f %10.4f" % (name, X.shape[1], auc, ap))
    d_fol = abl[2][2] - abl[1][2]
    d_dur = abl[1][2] - abl[0][2]
    print()
    print(" [结论]")
    print(" 粉丝数(log_follower) 贡献 AUC %.4f" % d_fol)
    print(" 时长(log_duration) 贡献 AUC %.4f" % d_dur)
    print(" 全量模型 %.4f 中有 %.4f 来自两个可疑特征" % (abl[2][2], abl[2][2] - abl[0][2]))
    print(" -> 去掉后仅剩 %.4f，说明【仅凭发布前特征很难预测爆款】" % abl[0][2])
    with open(os.path.join(RESDIR, "m06_特征消融实验.csv"), "w",
              encoding="utf-8", newline="") as f:
        import csv as _csv
        w = _csv.writer(f)
        w.writerow(["特征集", "特征数", "AUC", "PR-AUC"])
        for row in abl:
            w.writerow(row)

    # ── 4.3 主模型（Tier1+2，已去除时间穿越特征）──────────
    hr("4.3 主模型（仅用发布前特征，已剔除时间穿越变量）")
    X = build_features(df, 2)
    print(" 特征数 %d 已剔除: log_follower（时间穿越）" % X.shape[1])
    models = {
        "逻辑回归": make_pipeline(StandardScaler(),
                                  LogisticRegression(max_iter=3000,
                                                     class_weight="balanced")),
        "随机森林": RandomForestClassifier(**RF),
    }
    res = {}
    print("\n %-10s %9s %10s" % ("模型", "AUC", "PR-AUC"))
    for name, m in models.items():
        auc, ap, proba = cv_eval(X, y, m)
        res[name] = (auc, ap, proba)
        print(" %-10s %9.4f %10.4f" % (name, auc, ap))
    print(" %-10s %9.4f %10.4f <- 随机基线" % ("基线", 0.5, base))
    for name in res:
        print(" %-10s PR-AUC 是基线的 %.2f 倍" % (name, res[name][1] / base))

    best = max(res, key=lambda k: res[k][1])
    SUMMARY["模型AUC"] = res[best][0]
    SUMMARY["模型PRAUC"] = res[best][1]
    SUMMARY["最优模型"] = best
    SUMMARY["消融_仅安全特征AUC"] = abl[0][2]

    print("\n [为什么逻辑回归几乎等于随机？]")
    print(" 目标已按分区归一化，剩余特征与爆款呈【非线性】关系：")
    print(" · 时长、发布小时的影响不是单调的")
    print(" · 逻辑回归只能拟合单调关系，随机森林可以捕捉非线性")
    print(" 两者差距本身就是「关系非线性」的证据。")

    # ── 4.4 特征重要性与 SHAP ─────────────────────────────
    rf = models["随机森林"]
    rf.fit(X, y)
    imp = pd.DataFrame({"特征": X.columns, "重要性": rf.feature_importances_}) \
            .sort_values("重要性", ascending=False)
    print("\n [随机森林 特征重要性 Top 12]")
    for _, r in imp.head(12).iterrows():
        print(" %-16s %.4f %s" % (r.特征, r.重要性, "█" * int(r.重要性 * 400)))
    imp.to_csv(os.path.join(RESDIR, "m04_特征重要性.csv"), index=False, encoding="utf-8")

    if do_shap:
        print("\n 正在计算 SHAP 值 ...")
        import shap
        expl = shap.TreeExplainer(rf)
        idx = np.random.RandomState(0).choice(len(X), min(2000, len(X)), replace=False)
        sv = np.asarray(expl.shap_values(X.iloc[idx]))
        if sv.ndim == 3:
            sv = sv[:, :, 1]
        sv = sv.reshape(len(idx), -1)
        mean_abs = np.abs(sv).mean(axis=0).ravel()
        sdf = pd.DataFrame({"特征": X.columns, "平均|SHAP|": mean_abs}) \
                .sort_values("平均|SHAP|", ascending=False)
        print("\n [SHAP 特征归因 Top 12]")
        for _, r in sdf.head(12).iterrows():
            print(" %-16s %.5f %s" % (r.特征, r["平均|SHAP|"],
                                          "█" * int(r["平均|SHAP|"] * 1500)))
        sdf.to_csv(os.path.join(RESDIR, "m05_SHAP特征归因.csv"),
                   index=False, encoding="utf-8")
        SUMMARY["SHAP_Top3"] = "、".join(sdf.特征.head(3))

        plt = setup_cn_font()
        fig = plt.figure(figsize=(9, 6))
        shap.summary_plot(sv, X.iloc[idx], show=False, max_display=12)
        plt.tight_layout()
        fp = os.path.join(IMGDIR, "fig4_SHAP特征归因.png")
        savefig_safe(fig, fp, bbox_inches="tight")
        print("\n SHAP 图已保存: %s" % fp)

    plt = setup_cn_font()
    fig, ax = plt.subplots(figsize=(8, 5))
    d = imp.head(12).iloc[::-1]
    ax.barh(d.特征, d.重要性, color="#4C72B0")
    ax.set_xlabel("重要性")
    ax.set_title("爆款预测 · 随机森林特征重要性 Top 12（已剔除时间穿越特征）")
    plt.tight_layout()
    fp = os.path.join(IMGDIR, "fig3_特征重要性.png")
    savefig_safe(fig, fp)
    print(" 重要性图已保存: %s" % fp)

    # 消融对比图
    fig, ax = plt.subplots(figsize=(8, 4))
    names = [a[0] for a in abl]
    aucs = [a[2] for a in abl]
    cols = ["#55A868", "#4C72B0", "#C44E52"]
    ax.bar(range(len(names)), aucs, color=cols)
    ax.axhline(0.5, color="gray", ls="--", lw=1.2, label="随机基线 0.5")
    ax.set_xticks(range(len(names)))
    ax.set_xticklabels([n.replace(" ", "\n") for n in names], fontsize=9)
    ax.set_ylabel("AUC (5折交叉验证)")
    ax.set_title("特征消融实验：可疑特征贡献了多少 AUC")
    for i, v in enumerate(aucs):
        ax.text(i, v + 0.005, "%.4f" % v, ha="center", fontsize=10)
    ax.legend()
    plt.tight_layout()
    fp = os.path.join(IMGDIR, "fig5_特征消融实验.png")
    savefig_safe(fig, fp)
    print(" 消融图已保存: %s" % fp)

# ==============================================================
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-shap", action="store_true")
    a = ap.parse_args()

    print(SEP)
    print(" 内容社区视频表现分析 · 统计检验与建模")
    print(SEP)

    df = load_data()
    print(f"\n 数据加载完成: {len(df):,} 条视频，{df.tid.nunique()} 个分区")
    print(f" 爆款(同分区Top5%) 共 {df.is_hit.sum():,} 条 ({df.is_hit.mean()*100:.2f}%)")

    part1_time_effect(df)
    part2_tfidf(df)
    df = part3_kmeans(df)
    part4_model(df, do_shap=not a.no_shap)

    hr("关键指标汇总")
    for k, v in SUMMARY.items():
        if isinstance(v, float):
            print(f" {k:<20} {v:,.4f}")
        else:
            print(f" {k:<20} {v}")
    hr("全部完成")
