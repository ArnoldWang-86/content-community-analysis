# -*- coding: utf-8 -*-
"""实时查看采集进度（只读，不影响采集进程）"""
import collections, json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
F = os.path.join(RAW, "search_raw.jsonl")
STATE = os.path.join(RAW, "_search_state.json")

if not os.path.exists(F):
    print(" 还没有数据，采集可能刚开始")
    sys.exit(0)

rows, bad = [], 0
for line in open(F, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    try:
        rows.append(json.loads(line))
    except Exception:
        bad += 1

bv = {r.get("bvid") for r in rows if r.get("bvid")}
kws = collections.Counter(r.get("_keyword") for r in rows)

print(f" 原始行数 : {len(rows):,}")
print(f" 唯一视频数 : {len(bv):,} <-- 有效视频数")
print(f" 覆盖关键词 : {len(kws)}")
print()

# 每个关键词的唯一数
per = {}
for kw in kws:
    per[kw] = len({r["bvid"] for r in rows if r.get("_keyword") == kw})

done = []
if os.path.exists(STATE):
    try:
        done = json.load(open(STATE, encoding="utf-8")).get("done", [])
    except Exception:
        pass

print(f" 已完成关键词: {len(done)}/28")
for i, kw in enumerate(done, 1):
    print(f" {i:2d}. {kw:10s} {per.get(kw, 0):5d} 条")
cur = [k for k in kws if k not in done]
if cur:
    print(f" 进行中 : {cur[-1]} ({per.get(cur[-1],0)} 条)")

# 年份分布
years = collections.Counter()
for r in rows:
    pd = r.get("pubdate")
    if isinstance(pd, int) and pd > 0:
        years[time.strftime("%Y", time.localtime(pd))] += 1
if years:
    print()
    print(" 发布年份分布:")
    for y in sorted(years):
        print(f" {y}: {years[y]:6,}")
