# -*- coding: utf-8 -*-
"""05_clean_to_db.py —— 清洗原始采集数据并入库 MySQL

用法:
    python src/05_clean_to_db.py --dry-run # 只分析，不写数据库（推荐先跑）
    python src/05_clean_to_db.py # 正式清洗并入库
    python src/05_clean_to_db.py --limit 5000 # 只用前5000行测试

清洗规则（共 11 条，每条都对应一个已发现的真实数据问题）:
  R1 剔除广告推广位 bvid 为空 —— B站搜索接口混入的课程广告，占 2.3%
  R2 按 bvid 去重 同一视频被多个关键词搜到，跨关键词重复率 25.5%
  R3 剔除播放量 < 100 极小样本的互动率无统计意义
  R4 剔除 点赞 > 播放量 口径矛盾（广告残留或计数异常）
  R5 duration 解析为秒 原始格式 "1973:5" 是 分:秒，必须转数值才能建模
  R6 标记多P合集 B站对合集返回的是所有分P累计时长（最长817小时），
                           占 13.6%，不标记会把回归彻底带偏
  R7 派生 pub_hour/weekday 时段分析的核心字段
  R8 计算互动率 (点赞+投币+收藏)/播放
  R9 互动率 1%/99% 截尾 防止极端值主导回归
  R10 拆分标签到关联表 一对多关系，练 JOIN 的关键
  R11 入库 4 张表 含外键关联与数据质量校验
"""
import argparse, collections, csv, json, os, sys
from datetime import datetime

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw", "search_raw.jsonl")
CLEANDIR = os.path.join(ROOT, "data", "clean")
UPS_NEEDED = os.path.join(CLEANDIR, "ups_needed.json")
os.makedirs(CLEANDIR, exist_ok=True)

COLLECTION_SEC = 4 * 3600 # R6 阈值：超过 4 小时视为多P合集
MIN_PLAY = 100 # R3 阈值
SEP = "=" * 68


def hr(t):
    print(f"\n{SEP}\n {t}\n{SEP}")


def parse_duration(d):
    """'1973:5' -> 118385 秒 ; '1:02:03' -> 3723 秒"""
    try:
        parts = [int(x) for x in str(d).split(":")]
    except (ValueError, AttributeError):
        return None
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ==============================================================
# 读取 + 清洗
# ==============================================================
def load_and_clean(limit=None):
    print(f"读取原始数据: {RAW}")
    raw = []
    for line in open(RAW, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            raw.append(json.loads(line))
        except Exception:
            pass
        if limit and len(raw) >= limit:
            break
    print(f" 原始行数: {len(raw):,}")

    stats = collections.OrderedDict()
    stats["原始行数"] = len(raw)

    # ---- R1 剔除广告 ----
    ads = [r for r in raw if not r.get("bvid")]
    rows = [r for r in raw if r.get("bvid")]
    stats["R1 剔除广告(bvid为空)"] = len(ads)

    # ---- R2 按 bvid 去重 ----
    seen, uniq = set(), []
    for r in rows:
        if r["bvid"] not in seen:
            seen.add(r["bvid"])
            uniq.append(r)
    stats["R2 跨关键词去重"] = len(rows) - len(uniq)

    # ---- R3 播放量过滤 ----
    p = [r for r in uniq if (to_int(r.get("play")) or 0) >= MIN_PLAY]
    stats[f"R3 剔除播放<{MIN_PLAY}"] = len(uniq) - len(p)

    # ---- R4 点赞 > 播放 ----
    q = []
    for r in p:
        pl, lk = to_int(r.get("play")) or 0, to_int(r.get("like")) or 0
        if lk > pl:
            continue
        q.append(r)
    stats["R4 剔除点赞>播放"] = len(p) - len(q)

    # ---- R5/R6 duration ----
    nodur = 0
    for r in q:
        sec = parse_duration(r.get("duration"))
        if sec is None:
            nodur += 1
        r["_dur_s"] = sec
        r["_is_collection"] = 1 if (sec is not None and sec > COLLECTION_SEC) else 0
    stats["R5 duration 解析失败"] = nodur
    stats["R6 其中多P合集"] = sum(r["_is_collection"] for r in q)

    # ---- R7 派生时间字段 ----
    for r in q:
        pd = to_int(r.get("pubdate"))
        if pd and pd > 0:
            t = datetime.fromtimestamp(pd)
            r["_dt"] = t
            r["_hour"] = t.hour
            r["_weekday"] = t.weekday() # 0=周一 … 6=周日
        else:
            r["_dt"] = None
            r["_hour"] = None
            r["_weekday"] = None

    # ---- R8 互动率 ----
    for r in q:
        v = to_int(r.get("play")) or 0
        lk = to_int(r.get("like")) or 0
        fv = to_int(r.get("favorites")) or 0
        r["_rate_raw"] = (lk + fv) / v if v > 0 else None

    rates = [r["_rate_raw"] for r in q if r["_rate_raw"] is not None]
    lo, hi = np.percentile(rates, [1, 99])
    stats["R9 截尾下界(P1)"] = round(float(lo), 6)
    stats["R9 截尾上界(P99)"] = round(float(hi), 6)
    n_clipped = 0
    for r in q:
        if r["_rate_raw"] is None:
            r["_rate"] = None
        else:
            v = min(max(r["_rate_raw"], lo), hi)
            if v != r["_rate_raw"]:
                n_clipped += 1
            r["_rate"] = round(float(v), 6)
    stats["R9 被截尾的样本"] = n_clipped

    # ---- R10 标签 ----
    n_tags = 0
    for r in q:
        tags = [t.strip() for t in str(r.get("tag") or "").split(",") if t.strip()]
        r["_tags"] = list(dict.fromkeys(tags)) # 去重保序
        n_tags += len(r["_tags"])
    stats["R10 标签总数"] = n_tags

    stats["最终视频数"] = len(q)

    hr("清洗过程统计")
    for k, v in stats.items():
        if k.startswith(""):
            print(f" {k:<26} {v:>10,}")
        else:
            print(f" {k:<26} {v:>10,}")

    return q, stats


# ==============================================================
# dry-run：只输出统计 + 需要的 UP 清单
# ==============================================================
def dry_run(rows):
    hr("duration 分布（用于判断合集阈值）")
    secs = sorted(r["_dur_s"] for r in rows if r["_dur_s"] is not None)
    n = len(secs)
    for pct in (0.5, 0.75, 0.9, 0.95, 0.99, 0.995):
        v = secs[min(int(n * pct), n - 1)]
        print(f" P{pct*100:5.1f}: {v/60:10.1f} 分钟 ({v/3600:7.2f} 小时)")
    for th in (1, 2, 3, 4, 6, 10):
        c = sum(1 for s in secs if s > th * 3600)
        print(f" > {th:2d} 小时: {c:7,} 条 ({c/n*100:5.2f}%)")

    hr("互动率分布")
    rt = sorted(r["_rate"] for r in rows if r["_rate"] is not None)
    m = len(rt)
    for pct in (0.01, 0.25, 0.5, 0.75, 0.99):
        print(f" P{pct*100:5.1f}: {rt[min(int(m*pct), m-1)]:.6f}")

    hr("分区分布（前 15）")
    cat = collections.Counter((r.get("typeid"), r.get("typename")) for r in rows)
    print(f" 分区总数: {len(cat)}")
    for (tid, tn), c in cat.most_common(15):
        print(f" {str(tid):>5} {str(tn):<16} {c:>7,}")

    hr("需要采集粉丝数的 UP 主")
    ups = {}
    for r in rows:
        m = r.get("mid")
        if m and str(m) not in ups:
            ups[str(m)] = r.get("author")
    print(f" 唯一 UP 主: {len(ups):,}")
    est_min = len(ups) * 1.2 / 60
    print(f" 按每个约 1.2 秒估算，采集耗时约 {est_min:.0f} 分钟 ({est_min/60:.1f} 小时)")

    with open(UPS_NEEDED, "w", encoding="utf-8") as f:
        json.dump({"count": len(ups),
                   "mids": [{"mid": int(k), "uname": v} for k, v in ups.items()]},
                  f, ensure_ascii=False)
    print(f" 已写出清单 -> {UPS_NEEDED}")

    # 预览 CSV
    prev = os.path.join(CLEANDIR, "clean_preview.csv")
    cols = ["bvid", "title", "tname", "author", "view", "like", "favorites",
            "danmaku", "review", "_dur_s", "_is_collection", "_hour",
            "_weekday", "_rate"]
    with open(prev, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows[:2000]:
            w.writerow([r.get("bvid"), str(r.get("title"))[:60], r.get("typename"),
                        r.get("author"), r.get("play"), r.get("like"),
                        r.get("favorites"), r.get("danmaku"), r.get("review"),
                        r.get("_dur_s"), r.get("_is_collection"), r.get("_hour"),
                        r.get("_weekday"), r.get("_rate")])
    print(f" 清洗后预览(前2000条) -> {prev}")


# ==============================================================
# 入库
# ==============================================================
def load_followers():
    """读取已采集的 UP 主粉丝数（可能还没有）"""
    f = os.path.join(ROOT, "data", "raw", "ups.jsonl")
    d = {}
    if os.path.exists(f):
        for line in open(f, encoding="utf-8"):
            try:
                r = json.loads(line)
                d[str(r["mid"])] = r.get("follower")
            except Exception:
                pass
    return d


def load_details():
    """读取已采集的视频详情（投币/分享/cid，可能还没有）"""
    f = os.path.join(ROOT, "data", "raw", "detail.jsonl")
    d = {}
    if os.path.exists(f):
        for line in open(f, encoding="utf-8"):
            try:
                r = json.loads(line)
                d[r["bvid"]] = r
            except Exception:
                pass
    return d


def write_db(rows):
    from db import conn

    followers = load_followers()
    details = load_details()
    print(f"\n 已采集粉丝数的 UP 主: {len(followers):,}")
    print(f" 已采集详情的视频 : {len(details):,}")
    if not followers:
        print(" [提示] 还没采 UP 主粉丝数，up_info.follower 将为 NULL")
    if not details:
        print(" [提示] 还没采视频详情，coin/share/cid 将为 NULL，互动率暂不含投币")

    # ---- 组装 category ----
    cats = {(to_int(r.get("typeid")), str(r.get("typename") or "未知")) for r in rows}
    cats = {(t, n) for t, n in cats if t is not None}

    # ---- 组装 up_info ----
    ups = {}
    for r in rows:
        m = to_int(r.get("mid"))
        if m and m not in ups:
            ups[m] = (m, r.get("author"), followers.get(str(m)))

    # ---- 先算「含投币」的原始互动率，再统一做 1%/99% 截尾 ----
    # 注意：清洗阶段 R9 的截尾是基于 (点赞+收藏)，这里要把投币并进来重算，
    # 否则入库的 interact_rate 会保留极端值（实测截尾前最大 5.79 = 579%）
    raw_rates = []
    for r in rows:
        d = details.get(r["bvid"])
        coin = d.get("coin") if d else None
        v = to_int(r.get("play")) or 0
        base = (to_int(r.get("like")) or 0) + (to_int(r.get("favorites")) or 0) + (coin or 0)
        raw_rates.append(base / v if v > 0 else None)

    valid = [x for x in raw_rates if x is not None]
    lo2, hi2 = np.percentile(valid, [1, 99])
    print(" 互动率(含投币) 截尾区间: P1=%.6f P99=%.6f" % (lo2, hi2))
    print(f" 截尾前 max={max(valid):.4f} → 截尾后 max={min(max(valid), hi2):.4f}")

    # ---- 组装 video / video_tag ----
    vids, tags = [], []
    for r, rr in zip(rows, raw_rates):
        bv = r["bvid"]
        d = details.get(bv)
        coin = d.get("coin") if d else None
        share = d.get("share") if d else None
        cid = d.get("cid") if d else None

        v = to_int(r.get("play")) or 0
        lk = to_int(r.get("like")) or 0
        fv = to_int(r.get("favorites")) or 0
        rate = round(float(min(max(rr, lo2), hi2)), 6) if rr is not None else None

        vids.append((
            bv, to_int(r.get("aid")), str(r.get("title"))[:500],
            str(r.get("description"))[:2000],
            r["_dt"], r["_hour"], r["_weekday"], r["_dur_s"],
            to_int(r.get("typeid")), to_int(r.get("mid")),
            v, lk, coin, fv, share,
            to_int(r.get("danmaku")) or 0, to_int(r.get("review")) or 0,
            cid, rate,
        ))
        for t in r["_tags"]:
            tags.append((bv, t[:120]))

    # ---- 写入 ----
    with conn() as c:
        with c.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS=0")
            for t in ("video_tag", "video", "up_info", "category"):
                cur.execute(f"TRUNCATE TABLE {t}")
            cur.execute("SET FOREIGN_KEY_CHECKS=1")

            cur.executemany(
                "INSERT INTO category(tid,tname) VALUES(%s,%s)", list(cats))
            cur.executemany(
                "INSERT INTO up_info(mid,uname,follower) VALUES(%s,%s,%s)", list(ups.values()))
            cur.executemany(
                "INSERT INTO video(bvid,aid,title,description,pubdate,pub_hour,pub_weekday,"
                "duration_s,tid,up_mid,view_cnt,like_cnt,coin_cnt,fav_cnt,share_cnt,"
                "danmaku_cnt,reply_cnt,cid,interact_rate) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", vids)
            cur.executemany(
                "INSERT IGNORE INTO video_tag(bvid,tag) VALUES(%s,%s)", tags)
            c.commit()

            hr("入库结果")
            for t in ("category", "up_info", "video", "video_tag"):
                cur.execute(f"SELECT COUNT(*) AS n FROM {t}")
                print(f" {t:<12} {cur.fetchone()['n']:>9,} 行")

            hr("数据质量校验")
            checks = [
                ("主键唯一性(应等于总数)",
                 "SELECT COUNT(DISTINCT bvid) FROM video"),
                ("有 NULL 时间戳的(应为0)",
                 "SELECT COUNT(*) FROM video WHERE pubdate IS NULL"),
                ("多P合集标记数",
                 "SELECT COUNT(*) FROM video WHERE duration_s > 14400"),
                ("互动率截尾后最大值",
                 "SELECT ROUND(MAX(interact_rate),4) FROM video"),
                ("孤立视频(无UP主,应为0)",
                 "SELECT COUNT(*) FROM video v LEFT JOIN up_info u ON v.up_mid=u.mid "
                 "WHERE u.mid IS NULL"),
            ]
            for name, q in checks:
                cur.execute(q)
                print(f" {name:<28} {list(cur.fetchone().values())[0]:>10,}")

    print(f"\n 写入完成")


# ==============================================================
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只分析不入库")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    rows, stats = load_and_clean(a.limit)
    if not rows:
        print("没有可用数据"); sys.exit(1)

    if a.dry_run:
        dry_run(rows)
        hr("DRY-RUN 完成，未写入数据库")
    else:
        write_db(rows)
