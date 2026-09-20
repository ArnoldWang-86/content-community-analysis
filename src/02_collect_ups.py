# -*- coding: utf-8 -*-
"""02_collect_ups.py —— 补充 UP 主粉丝数

数据来源优先顺序:
  1. data/clean/ups_needed.json ← 由 05_clean_to_db.py --dry-run 生成（精准，推荐）
  2. data/raw/search_raw.jsonl ← 兜底（全量，会多采广告账号和低播放视频的UP主）

用法:
    python src/02_collect_ups.py # 采集
    python src/02_collect_ups.py --sleep 0.4 # 加快（默认0.8）
    python src/02_collect_ups.py --limit 200 # 测试
    python src/02_collect_ups.py --stats # 只看进度
"""
import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bili import Bili

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
CLEAN = os.path.join(ROOT, "data", "clean")
OUT = os.path.join(RAW, "ups.jsonl")
NEEDED = os.path.join(CLEAN, "ups_needed.json")
SRC = os.path.join(RAW, "search_raw.jsonl")


def load_targets():
    """返回 [(mid, uname), ...]"""
    if os.path.exists(NEEDED):
        d = json.load(open(NEEDED, encoding="utf-8"))
        print(f"数据来源: {NEEDED}")
        print(f" (由 dry-run 生成，已剔除广告与低播放视频)")
        return [(x["mid"], x.get("uname")) for x in d["mids"]]

    print(f"数据来源: {SRC} [兜底：全量，未去广告]")
    names, mids = {}, []
    for line in open(SRC, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        m = r.get("mid")
        if m and str(m) not in names:
            names[str(m)] = r.get("author")
            mids.append(m)
    return [(m, names[str(m)]) for m in mids]


def load_done():
    d = {}
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                r = json.loads(line)
                if r.get("mid"):
                    d[str(r["mid"])] = r
            except Exception:
                pass
    return d


def main(limit, sleep):
    targets = load_targets()
    done = load_done()
    todo = [(m, n) for m, n in targets if str(m) not in done]

    print(f"\nUP主总数 {len(targets):,} | 已完成 {len(done):,} | 待采集 {len(todo):,}")
    est = len(todo) * (sleep + 0.4) / 60
    print(f"按 sleep={sleep}s 估算，约需 {est:.0f} 分钟 ({est/60:.1f} 小时)\n")
    if limit:
        todo = todo[:limit]
    if not todo:
        print("全部已完成，无需采集"); return

    c = Bili(sleep=sleep)
    t0, ok, fail = time.time(), 0, 0
    with open(OUT, "a", encoding="utf-8") as f:
        for i, (mid, uname) in enumerate(todo, 1):
            fo = c.follower(mid)
            if fo is None:
                fail += 1
            else:
                ok += 1
            f.write(json.dumps({"mid": mid, "uname": uname, "follower": fo},
                               ensure_ascii=False) + "\n")
            f.flush()
            if i % 100 == 0 or i == len(todo):
                el = time.time() - t0
                eta = el / i * (len(todo) - i)
                print(f" {i:>6,}/{len(todo):,} 成功{ok} 失败{fail} "
                      f"用时{el/60:5.1f}min 剩余约{eta/60:5.1f}min")
            c.sleep_a_bit()
    print(f"\n完成 -> {OUT}")


def stats():
    d = load_done()
    print(f"已采集 UP 主: {len(d):,}")
    vals = [v["follower"] for v in d.values() if isinstance(v.get("follower"), int)]
    if vals:
        vals.sort()
        print(f" 有效粉丝数: {len(vals):,}")
        for p in (0.25, 0.5, 0.75, 0.9, 0.99):
            print(f" P{p*100:5.1f}: {vals[int(len(vals)*p)]:>12,}")
        print(f" max : {vals[-1]:>12,}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.8)
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.stats:
        stats(); sys.exit(0)
    main(a.limit, a.sleep)
