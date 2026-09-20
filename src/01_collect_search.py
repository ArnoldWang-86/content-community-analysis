# -*- coding: utf-8 -*-
"""
01_collect_search.py —— B站搜索接口批量采集（项目主数据源）

用法:
    python src/01_collect_search.py # 全量（28关键词 x 50页，约30分钟）
    python src/01_collect_search.py --pages 5 # 每关键词5页（快速验证）
    python src/01_collect_search.py --keywords 数据分析,机器学习
    python src/01_collect_search.py --stats # 统计已采集
    python src/01_collect_search.py --reset # 清空重采

关键：必须带 wbi 签名，否则 page 参数被忽略（永远返回第1页）
"""
import argparse, json, os, re, sys, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bili import Bili

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(RAW, "search_raw.jsonl")
STATE = os.path.join(RAW, "_search_state.json")
os.makedirs(RAW, exist_ok=True)

KEYWORDS = [
    "数据分析", "Python教程", "机器学习", "SQL教程", "统计学", "Excel技巧",
    "编程入门", "人工智能", "大模型", "数据可视化",
    "美食探店", "旅行vlog", "穿搭", "美妆", "健身", "宠物", "摄影",
    "游戏解说", "数码测评", "读书分享", "学习方法",
    "职场干货", "面试技巧", "副业", "自媒体运营", "理财",
    "考研数学", "英语学习",
]
FIELDS = ["bvid", "aid", "title", "description", "pubdate", "duration",
          "play", "like", "favorites", "danmaku", "review",
          "author", "mid", "tag", "typename", "typeid", "arcurl"]
TAG_RE = re.compile(r"</?em[^>]*>")


def clean(s):
    return TAG_RE.sub("", s).replace("&quot;", '"').strip() if isinstance(s, str) else s


def load_state():
    if os.path.exists(STATE):
        return json.load(open(STATE, encoding="utf-8"))
    return {"done": [], "seen": {}}


def save_state(st):
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


def collect(kws, max_page, sleep, fresh=False):
    st = {"done": [], "seen": {}} if fresh else load_state()
    done = set(st["done"])
    seen = set(st.get("seen", {}).get("all", []))
    c = Bili(sleep=sleep)
    new_total = 0

    with open(OUT, "a", encoding="utf-8") as fout:
        for i, kw in enumerate(kws, 1):
            if kw in done:
                print(f"[{i}/{len(kws)}] {kw} 已完成，跳过"); continue
            t0, pages_ok = time.time(), 0
            for page in range(1, max_page + 1):
                res = c.search(kw, page)
                if not res:
                    print(f" p{page} 空返回，停止该关键词"); break
                pages_ok += 1
                for it in res:
                    rec = {k: (clean(it.get(k)) if k in ("title", "description") else it.get(k))
                           for k in FIELDS}
                    rec.update({"_keyword": kw, "_page": page,
                                "_ts": datetime.now().isoformat(timespec="seconds")})
                    fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    seen.add(it.get("bvid"))
                fout.flush()
                c.sleep_a_bit()
            new_total += 0
            done.add(kw)
            st["done"] = sorted(done)
            st["seen"] = {"all": sorted(seen)}
            save_state(st)
            print(f"[{i}/{len(kws)}] {kw:8s} {pages_ok:2d}页 "
                  f"唯一累计 {len(seen):6d} ({time.time()-t0:5.1f}s)")
    print(f"\n完成：唯一 bvid 累计 {len(seen)} 条 -> {OUT}")


def show_stats():
    if not os.path.exists(OUT):
        print("尚无数据"); return
    bv, kws, years = set(), {}, {}
    n = 0
    for line in open(OUT, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        n += 1
        if r.get("bvid"):
            bv.add(r["bvid"])
        kws[r.get("_keyword")] = kws.get(r.get("_keyword"), 0) + 1
        pd = r.get("pubdate")
        if isinstance(pd, int) and pd > 0:
            y = datetime.fromtimestamp(pd).year
            years[y] = years.get(y, 0) + 1
    print(f"原始行数 : {n}")
    print(f"唯一 bvid 数 : {len(bv)} <- 有效样本数")
    print(f"覆盖关键词 : {len(kws)}")
    print(f"年份分布 : {dict(sorted(years.items()))}")
    print(f"已完成关键词 : {len(load_state()['done'])}/{len(KEYWORDS)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=50)
    ap.add_argument("--keywords", type=str, default="")
    ap.add_argument("--sleep", type=float, default=1.2)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    if a.stats:
        show_stats(); sys.exit(0)
    kws = [k.strip() for k in a.keywords.split(",") if k.strip()] or KEYWORDS
    if a.reset:
        for p in (OUT, STATE):
            if os.path.exists(p):
                os.remove(p)
        print("已重置")
    collect(kws, a.pages, a.sleep)
    show_stats()
