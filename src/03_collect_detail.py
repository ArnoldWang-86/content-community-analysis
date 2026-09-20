# -*- coding: utf-8 -*-
"""03_collect_detail.py —— 补充视频详情：投币 / 分享 / cid（cid 用于取弹幕）
2 万条约需 2.5 小时，建议挂后台跑。
用法: python src/03_collect_detail.py # 全量（断点续跑）
      python src/03_collect_detail.py --limit 300 # 测试
      python src/03_collect_detail.py --stats
"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bili import Bili

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
SRC, OUT = os.path.join(RAW, "search_raw.jsonl"), os.path.join(RAW, "detail.jsonl")


def load_done():
    d = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                r = json.loads(line)
                if r.get("bvid"):
                    d.add(r["bvid"])
            except Exception:
                pass
    return d


def main(limit):
    bvids, seen = [], set()
    for line in open(SRC, encoding="utf-8"):
        try:
            b = json.loads(line).get("bvid")
        except Exception:
            continue
        if b and b not in seen:
            seen.add(b); bvids.append(b)
    done = load_done()
    todo = [b for b in bvids if b not in done]
    print(f"视频总数 {len(bvids)} | 已完成 {len(done)} | 待采集 {len(todo)}")
    if limit:
        todo = todo[:limit]

    c = Bili(sleep=0.45)
    t0, fail = time.time(), 0
    with open(OUT, "a", encoding="utf-8") as f:
        for i, bv in enumerate(todo, 1):
            d = c.video(bv)
            if not d:
                fail += 1
                continue
            st = d.get("stat") or {}
            f.write(json.dumps({
                "bvid": bv, "aid": d.get("aid"), "cid": d.get("cid"),
                "coin": st.get("coin"), "share": st.get("share"),
                "like": st.get("like"), "fav": st.get("favorite"),
                "view": st.get("view"), "danmaku": st.get("danmaku"),
                "reply": st.get("reply"), "duration": d.get("duration"),
                "pubdate": d.get("pubdate"), "tname": d.get("tname"),
                "tid": d.get("tid"), "up_mid": (d.get("owner") or {}).get("mid"),
            }, ensure_ascii=False) + "\n")
            f.flush()
            if i % 200 == 0 or i == len(todo):
                el = time.time() - t0
                eta = el / i * (len(todo) - i)
                print(f" {i}/{len(todo)} 失败{fail} 用时{el/60:.1f}min 剩余约{eta/60:.1f}min")
            c.sleep_a_bit()
    print(f"完成 -> {OUT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.stats:
        d = load_done(); print("已完成详情:", len(d)); sys.exit(0)
    main(a.limit)
