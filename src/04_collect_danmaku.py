# -*- coding: utf-8 -*-
"""04_collect_danmaku.py —— 弹幕文本采集（项目2 意图识别的语料来源）

关键修复(2026-09-20):
  api.bilibili.com/x/v1/dm/list.so 高频请求后返回 HTTP 412（限流），
  返回 3400 字节 HTML 错误页而非 XML，导致弹幕被**静默采空**（98.8% 为空）。
  改用 comment.bilibili.com/{cid}.xml，命中率从 1.2% 提升到 100%。

抽样策略:
  不随机取前 N 个视频（那样绝大多数弹幕为 0），
  而是**按弹幕数从高到低排序取前 N 个**，单视频平均可得 1500-2500 条。

用法:
    python src/04_collect_danmaku.py # 默认前 1500 个
    python src/04_collect_danmaku.py --limit 3000
    python src/04_collect_danmaku.py --min-danmaku 50
    python src/04_collect_danmaku.py --stats
"""
import argparse, json, os, sys, time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bili import Bili

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
SRC_SEARCH = os.path.join(RAW, "search_raw.jsonl")
SRC_DETAIL = os.path.join(RAW, "detail.jsonl")
OUT = os.path.join(RAW, "danmaku.jsonl")


def parse_xml(content):
    out = []
    if not content:
        return out
    try:
        root = ET.fromstring(content)
    except Exception:
        return out
    for d in root.findall("d"):
        p = (d.get("p") or "").split(",")
        txt = (d.text or "").strip()
        if not txt:
            continue
        out.append({
            "t": float(p[0]) if p and p[0] else None,
            "mode": int(p[1]) if len(p) > 1 and p[1].isdigit() else None,
            "ts": int(p[4]) if len(p) > 4 and p[4].isdigit() else None,
            "text": txt,
        })
    return out


def build_targets(limit, min_danmaku):
    dm = {}
    for line in open(SRC_SEARCH, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        b = r.get("bvid")
        if b and b not in dm:
            dm[b] = int(r.get("danmaku") or 0)

    items = []
    for line in open(SRC_DETAIL, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        bv, cid = r.get("bvid"), r.get("cid")
        if not bv or not cid:
            continue
        cnt = dm.get(bv, 0)
        if cnt < min_danmaku:
            continue
        items.append((bv, cid, r.get("tid"), r.get("tname"),
                      r.get("pubdate"), cnt))
    items.sort(key=lambda x: -x[5])
    return items[:limit] if limit else items


def load_done():
    d = set()
    if os.path.exists(OUT):
        for line in open(OUT, encoding="utf-8"):
            try:
                d.add(json.loads(line)["bvid"])
            except Exception:
                pass
    return d


def main(limit, min_danmaku, sleep):
    targets = build_targets(limit, min_danmaku)
    done = load_done()
    todo = [t for t in targets if t[0] not in done]
    print(f"候选视频 {len(targets):,}（按弹幕量降序，min_danmaku={min_danmaku}）")
    print(f"已完成 {len(done):,} | 本次待采 {len(todo):,}")
    print(f"预计耗时约 {len(todo)*(sleep+0.5)/60:.0f} 分钟\n")
    if not todo:
        print("全部已完成"); return

    c = Bili(sleep=sleep)
    t0, n_dm, n_vid, empty = time.time(), 0, 0, 0
    with open(OUT, "a", encoding="utf-8") as f:
        for i, (bv, cid, tid, tname, pub, cnt) in enumerate(todo, 1):
            try:
                dms = parse_xml(c.danmaku_xml(cid))
            except Exception as e:
                print(f" [skip {bv}] {type(e).__name__}: {str(e)[:50]}")
                dms = []
            n_dm += len(dms)
            n_vid += 1
            if not dms:
                empty += 1
            f.write(json.dumps({"bvid": bv, "cid": cid, "tid": tid,
                                "tname": tname, "pubdate": pub,
                                "danmaku_cnt_total": cnt,
                                "n": len(dms), "danmaku": dms},
                               ensure_ascii=False) + "\n")
            f.flush()
            if i % 50 == 0 or i == len(todo):
                el = time.time() - t0
                eta = el / i * (len(todo) - i)
                print(f" {i:>5,}/{len(todo):,} 弹幕{n_dm:>9,}条 空{empty} "
                      f"平均{n_dm/max(n_vid,1):>6.0f}/视频 "
                      f"用时{el/60:4.1f}min 剩余{eta/60:4.1f}min")
            c.sleep_a_bit()
    print(f"\n完成 -> {OUT}")
    print(f" 视频 {n_vid:,} 弹幕 {n_dm:,} 平均 {n_dm/max(n_vid,1):.0f} 条/视频")


def stats():
    if not os.path.exists(OUT):
        print("尚无数据"); return
    rows = []
    for line in open(OUT, encoding="utf-8"):
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    ns = [r.get("n", 0) for r in rows]
    tot = sum(ns)
    print(f" 视频数 : {len(rows):,}")
    print(f" 弹幕总数 : {tot:,}")
    print(f" 平均 : {tot/max(len(ns),1):.0f} 条/视频")
    print(f" 为 0 的视频 : {sum(1 for n in ns if n == 0):,}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=1500)
    ap.add_argument("--min-danmaku", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.5)
    ap.add_argument("--stats", action="store_true")
    a = ap.parse_args()
    if a.stats:
        stats(); sys.exit(0)
    main(a.limit, a.min_danmaku, a.sleep)
