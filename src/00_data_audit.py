# -*- coding: utf-8 -*-
"""00_data_audit.py —— 原始数据质量审计（采集后第一件事）
用法: python src/00_data_audit.py
"""
import collections, json, os, re, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
F = os.path.join(ROOT, "data", "raw", "search_raw.jsonl")

FIELDS = ["bvid", "title", "pubdate", "duration", "play", "like",
          "favorites", "danmaku", "review", "author", "mid", "tag", "typename"]
SEP = "=" * 66


def hr(t):
    print(f"\n{SEP}\n {t}\n{SEP}")


rows = []
for line in open(F, encoding="utf-8"):
    line = line.strip()
    if line:
        try:
            rows.append(json.loads(line))
        except Exception:
            pass

hr("1. 基础规模")
bvids = [r.get("bvid") for r in rows]
uniq = set(bvids)
print(f" 原始行数 : {len(rows):,}")
print(f" 唯一 bvid : {len(uniq):,}")
print(f" 跨关键词重复率 : {(1-len(uniq)/len(rows))*100:.1f}%")

hr("2. 字段缺失率")
print(f" {'字段':<14}{'缺失':>8}{'缺失率':>10}")
for f in FIELDS:
    miss = sum(1 for r in rows if r.get(f) in (None, "", 0) and f not in ("play", "like", "favorites", "danmaku", "review"))
    empt = sum(1 for r in rows if r.get(f) is None or r.get(f) == "")
    print(f" {f:<14}{empt:>8,}{empt/len(rows)*100:>9.1f}%")

hr("3. 数值字段分布（唯一视频，去重后）")
seen, uniq_rows = set(), []
for r in rows:
    b = r.get("bvid")
    if b and b not in seen:
        seen.add(b); uniq_rows.append(r)

def stat(name, vals):
    vals = sorted(v for v in vals if isinstance(v, (int, float)))
    if not vals:
        print(f" {name:<12} 无数据"); return
    n = len(vals)
    q = lambda p: vals[min(int(n * p), n - 1)]
    print(f" {name:<12} n={n:>6,} min={vals[0]:>10,} P25={q(.25):>10,} "
          f"中位={q(.5):>10,} P75={q(.75):>10,} P99={q(.99):>12,} max={vals[-1]:>12,}")

for f in ("play", "like", "favorites", "danmaku", "review"):
    stat(f, [r.get(f) for r in uniq_rows])

hr("4. 异常值检查")
zero_play = sum(1 for r in uniq_rows if not r.get("play"))
neg = sum(1 for r in uniq_rows for f in ("play", "like", "favorites", "danmaku", "review")
          if isinstance(r.get(f), (int, float)) and r[f] < 0)
like_gt_play = sum(1 for r in uniq_rows
                   if isinstance(r.get("like"), int) and isinstance(r.get("play"), int)
                   and r["play"] > 0 and r["like"] > r["play"])
print(f" 播放量为 0 或缺失 : {zero_play:,} ({zero_play/len(uniq_rows)*100:.2f}%)")
print(f" 负值字段 : {neg:,}")
print(f" 点赞数 > 播放量 : {like_gt_play:,} ({like_gt_play/len(uniq_rows)*100:.2f}%) <- 数据口径可疑")
small = sum(1 for r in uniq_rows if isinstance(r.get("play"), int) and r["play"] < 100)
print(f" 播放量 < 100 : {small:,} ({small/len(uniq_rows)*100:.2f}%) <- 建议剔除")

hr("5. 发布时间分布")
years = collections.Counter()
months = collections.Counter()
for r in uniq_rows:
    pd = r.get("pubdate")
    if isinstance(pd, int) and pd > 0:
        t = time.localtime(pd)
        years[t.tm_year] += 1
        if t.tm_year == 2026:
            months[t.tm_mon] += 1
tot = sum(years.values())
print(f" {'年份':<8}{'数量':>10}{'占比':>10}")
for y in sorted(years, reverse=True):
    print(f" {y:<8}{years[y]:>10,}{years[y]/tot*100:>9.1f}%")
print(f"\n 2026 年月度分布:")
for m in sorted(months):
    print(f" {m:>2}月: {months[m]:>7,}")

hr("6. 文本字段质量")
title_html = sum(1 for r in uniq_rows if re.search(r"</?em", str(r.get("title", ""))))
empty_title = sum(1 for r in uniq_rows if not r.get("title"))
tag_n = [len(str(r.get("tag", "")).split(",")) if r.get("tag") else 0 for r in uniq_rows]
has_tag = sum(1 for n in tag_n if n > 0)
print(f" 标题残留 HTML 标签: {title_html:,}")
print(f" 标题为空 : {empty_title:,}")
print(f" 有标签的视频 : {has_tag:,} ({has_tag/len(uniq_rows)*100:.1f}%)")
print(f" 平均标签数 : {sum(tag_n)/len(tag_n):.1f}")

hr("7. 分区与UP主覆盖")
tn = collections.Counter(r.get("typename") for r in uniq_rows)
print(f" 分区数: {len(tn)}")
for k, v in tn.most_common(10):
    print(f" {str(k):<16} {v:>7,}")
mids = {r.get("mid") for r in uniq_rows if r.get("mid")}
print(f"\n 唯一 UP 主数: {len(mids):,}")

hr("8. duration 字段格式（需要清洗）")
samples = [str(r.get("duration")) for r in uniq_rows[:5]]
print(" 原始样例:", samples)
bad = sum(1 for r in uniq_rows if not re.fullmatch(r"[\d:]+", str(r.get("duration") or "")))
print(f" 非 分:秒 格式的数量: {bad:,}")
