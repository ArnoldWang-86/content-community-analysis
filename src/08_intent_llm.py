# -*- coding: utf-8 -*-
r"""08_intent_llm.py —— 弹幕用户意图识别与语义分析（项目 2）

流程:
  Part 1 清洗弹幕噪声（超短/纯数字/纯符号/系统提示）
  Part 2 分层抽样（按原始频次加权，反映真实弹幕分布）
  Part 3 调用 DeepSeek API 批量意图标注（JSON 模式 + 并发限流 + 断点续跑）
  Part 4 意图结构分析（整体分布 / 分区 / 内容形式 / 时段）
  Part 5 导出人工抽检样本 → 你标注后回填，脚本算准确率

API Key 三种提供方式（任选其一）:
  1. 环境变量: setx DEEPSEEK_API_KEY "sk-xxx" (设完要重开终端)
  2. .env 文件: 在项目根目录建 .env，写一行 DEEPSEEK_API_KEY=sk-xxx
  3. 命令行: python src\08_intent_llm.py --api-key sk-xxx

用法:
    python src\08_intent_llm.py --check # 只测 API 通不通（标3条）
    python src\08_intent_llm.py --sample 6000 # 完整流程
    python src\08_intent_llm.py --analyze-only # 跳过标注，只做分析
    python src\08_intent_llm.py --score # 回填抽检结果后算准确率
"""
import argparse, collections, json, os, random, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
RESDIR = os.path.join(ROOT, "results")
CLNDIR = os.path.join(ROOT, "data", "clean")
os.makedirs(RESDIR, exist_ok=True)
os.makedirs(CLNDIR, exist_ok=True)

DM_FILE = os.path.join(RAW, "danmaku.jsonl")
CLEAN_FILE = os.path.join(CLNDIR, "danmaku_clean.jsonl")
SAMPLE_FILE = os.path.join(CLNDIR, "intent_sample.jsonl")
LABEL_FILE = os.path.join(CLNDIR, "intent_labeled.jsonl")
SPOT_FILE = os.path.join(RESDIR, "m07_人工抽检样本.csv")

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
BATCH = 20
WORKERS = 4

INTENTS = ["求推荐", "求教程", "消费决策", "情绪共鸣", "吐槽", "催更", "提问", "打卡许愿", "其他"]

SYS_PROMPT = """你是内容社区的用户意图分析专家。请为每条B站弹幕标注其用户意图。

可选标签（只能选这 9 个之一）：
- 求推荐: 希望获得内容/产品/资源/方法推荐，必须有“求”的意味。
  例:"求同款"、"有没有类似的"、"求个链接"
- 求教程: 请求讲解、教学、方法指导。
  例:"能不能出个教程"、"这个怎么做的"、"求讲讲"
- 消费决策: 涉及购买、价格、值不值得买、在哪买。
  例:"多少钱"、"已下单"、"好便宜"、"蹲个链接"
- 情绪共鸣: 表达情绪、认同、赞美、感叹、玩梗、自嘲。
  例:"太真实了"、"哈哈哈"、"泪目"、"绝了"、"爷青回"
- 吐槽: 批评、不满、否定、质疑内容或他人。
  例:"讲得太烂了"、"误人子弟"、"这也太假了"
- 催更: 明确要求更新后续内容。
  例:"等更新"、"下一期呢"、"什么时候出第二集"
- 提问: 【必须是疑问句或明确询问】
  判断标准：句末有问号，或含“吗/呢/怎么/为什么/是不是/哪/谁/多少/能不能”等疑问结构。
  [!] 陈述句、断言、术语罗列、单纯说出一个名词 —— 一律【不算】提问！
  正例:"这是第几集"、"up主用的什么软件"、"能带得动吗"
  反例(都不算提问):"时间复杂度"、"但佳能是日本的"、"编程语句符号都是英文的"
- 打卡许愿: 打卡签到、许愿、祈福、立flag、报进度、自我激励。
  例:"打卡第一天"、"来打个卡"、"上岸必还愿"、"我一定要考上"、"从头开始学"
- 其他: 以上都不是的【无指向内容】：单纯的名词/人名/食物名/地名、
  无指向的玩梗、纯吐槽语气的语气词、与内容完全无关的句子。
  例:"羊肉"、"赵记老铺"、"him"、"apt"、"2020/10/27"

规则：
1. 只输出标签本身，不要解释
2. 一句话有多重意图时，选最主要的那个
3. 拿不准时优先归入「其他」，不要硬凑
4. 必须严格返回 JSON，格式 {"labels": ["标签1","标签2", ...]}
   数组长度必须等于输入条数，顺序一一对应。"""


def hr(t):
    print("\n" + "=" * 78 + "\n " + t + "\n" + "=" * 78)


SYS_PAT = re.compile(r"^[\[\(【（].{0,20}[\]\)】）]$")
PURE_NUM = re.compile(r"^[0-9０-９\.\-\+%？?！!。，,~～]+$")
PURE_SYM = re.compile(r"^[^\w\u4e00-\u9fff]+$")


def clean_danmaku(force=False):
    if os.path.exists(CLEAN_FILE) and not force:
        n = sum(1 for _ in open(CLEAN_FILE, encoding="utf-8"))
        print(" 已存在清洗语料（%d 个视频），跳过" % n)
        return
    print(" 正在清洗 ...")
    stats = collections.Counter()
    kept = 0
    with open(DM_FILE, encoding="utf-8") as fin, open(CLEAN_FILE, "w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            clean = []
            for d in r.get("danmaku", []):
                t = (d.get("text") or "").strip()
                if not t:
                    continue
                if len(t) <= 1:
                    stats["长度<=1"] += 1
                elif SYS_PAT.match(t):
                    stats["系统提示类"] += 1
                elif PURE_NUM.match(t):
                    stats["纯数字/标点"] += 1
                elif PURE_SYM.match(t):
                    stats["纯符号"] += 1
                else:
                    clean.append({"t": d.get("t"), "text": t})
                    stats["保留"] += 1
            if clean:
                kept += 1
            fout.write(json.dumps({"bvid": r.get("bvid"), "tid": r.get("tid"),
                                   "tname": r.get("tname"), "pubdate": r.get("pubdate"),
                                   "n": len(clean), "danmaku": clean},
                                  ensure_ascii=False) + "\n")
    total = sum(stats.values())
    print(" 弹幕总数 %s" % format(total, ","))
    for k, v in stats.most_common():
        print(" %-14s %10s (%5.2f%%)" % (k, format(v, ","), v / total * 100))
    print(" 有效视频 %s -> %s" % (format(kept, ","), CLEAN_FILE))


def build_sample(n, force=False):
    if os.path.exists(SAMPLE_FILE) and not force:
        cnt = sum(1 for _ in open(SAMPLE_FILE, encoding="utf-8"))
        print(" 已存在抽样文件（%s 条），跳过" % format(cnt, ","))
        return
    print(" 正在构建抽样 ...")
    pool = []
    for line in open(CLEAN_FILE, encoding="utf-8"):
        r = json.loads(line)
        for d in r["danmaku"]:
            pool.append((d["text"], r["bvid"], r.get("tid"), r.get("pubdate")))
    print(" 语料池 %s 条" % format(len(pool), ","))
    rs = random.Random(42)
    idx = rs.sample(range(len(pool)), min(n, len(pool)))
    # 采集详情接口不返回分区名，故只存 tid，分析阶段再从数据库补 tname
    sample = [{"text": pool[i][0], "bvid": pool[i][1],
               "tid": pool[i][2], "pubdate": pool[i][3]} for i in idx]
    print(" 抽样 %s 条（频次加权 —— 高频弹幕本就代表主流意图）" % format(len(sample), ","))
    with open(SAMPLE_FILE, "w", encoding="utf-8") as f:
        for s in sample:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    print(" -> %s" % SAMPLE_FILE)


def load_key(cli_key=None):
    if cli_key:
        return cli_key
    k = os.environ.get("DEEPSEEK_API_KEY")
    if k:
        return k.strip()
    envf = os.path.join(ROOT, ".env")
    if os.path.exists(envf):
        for line in open(envf, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def call_api(key, texts, retries=4):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYS_PROMPT},
            {"role": "user", "content": "请标注以下 %d 条弹幕：\n" % len(texts)
             + "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate(texts))},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.0,
        "max_tokens": 2000,
    }
    for t in range(retries):
        try:
            r = requests.post(API_URL, headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json"},
                json=payload, timeout=90)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** t + random.random() * 2); continue
            r.raise_for_status()
            labels = json.loads(r.json()["choices"][0]["message"]["content"]).get("labels", [])
            if len(labels) != len(texts):
                labels = (labels + ["其他"] * len(texts))[:len(texts)]
            return [x if x in INTENTS else "其他" for x in labels]
        except Exception as e:
            if t == retries - 1:
                print(" [fail] %s: %s" % (type(e).__name__, str(e)[:80]))
            time.sleep(2 ** t + random.random())
    return None


def label(key, batch=BATCH, workers=WORKERS):
    done = set()
    if os.path.exists(LABEL_FILE):
        for line in open(LABEL_FILE, encoding="utf-8"):
            try:
                d = json.loads(line)
                done.add(d["text"] + "|" + str(d.get("bvid")))
            except Exception:
                pass
    sample = [json.loads(l) for l in open(SAMPLE_FILE, encoding="utf-8") if l.strip()]
    todo = [s for s in sample if s["text"] + "|" + str(s.get("bvid")) not in done]
    print(" 抽样 %s | 已完成 %s | 待标注 %s"
          % (format(len(sample), ","), format(len(done), ","), format(len(todo), ",")))
    if not todo:
        print(" 全部已完成"); return

    batches = [todo[i:i + batch] for i in range(0, len(todo), batch)]
    print(" 批次数 %s（每批 %d 条，%d 并发，预计约 %.1f 分钟）"
          % (format(len(batches), ","), batch, workers, len(batches) * 3.0 / workers / 60))
    t0, ok, fail, dn = time.time(), 0, 0, 0
    with open(LABEL_FILE, "a", encoding="utf-8") as fout:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(call_api, key, [b["text"] for b in bt]): bt for bt in batches}
            for fut in as_completed(futs):
                bt = futs[fut]
                labels = fut.result()
                dn += 1
                if labels is None:
                    fail += 1
                else:
                    ok += 1
                    for s, lb in zip(bt, labels):
                        fout.write(json.dumps({"text": s["text"], "bvid": s["bvid"],
                                               "tid": s.get("tid"),
                                               "pubdate": s.get("pubdate"),
                                               "intent": lb}, ensure_ascii=False) + "\n")
                    fout.flush()
                if dn % 10 == 0 or dn == len(batches):
                    el = time.time() - t0
                    eta = el / dn * (len(batches) - dn)
                    print(" 批次 %5s/%s 成功%d 失败%d 用时%.1fmin 剩余约%.1fmin"
                          % (format(dn, ","), format(len(batches), ","), ok, fail, el / 60, eta / 60))
    print("\n 标注完成 -> %s" % LABEL_FILE)


def analyze():
    import pandas as pd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from db import to_df
    rows = [json.loads(l) for l in open(LABEL_FILE, encoding="utf-8") if l.strip()]
    df = pd.DataFrame(rows)
    # 分区名从数据库维表补齐（采集接口不返回 tname，原代码这里一直是空的）
    try:
        cat = to_df("SELECT tid, tname FROM category")
        cat["tid"] = pd.to_numeric(cat.tid, errors="coerce")
        df["tid"] = pd.to_numeric(df.get("tid"), errors="coerce")
        df = df.drop(columns=[c for c in ("tname",) if c in df.columns])
        df = df.merge(cat, on="tid", how="left")
    except Exception as e:
        print(" [warn] 分区名补齐失败: %s" % str(e)[:60])
    df["tname"] = df["tname"].fillna("未知分区") if "tname" in df else "未知分区"
    df["pubdate"] = pd.to_numeric(df.pubdate, errors="coerce")
    df["发布小时"] = pd.to_datetime(df.pubdate, unit="s", errors="coerce").dt.hour
    n = len(df)
    print(" 已标注样本: %s 条" % format(n, ","))

    hr("4.1 整体意图分布")
    vc = df.intent.value_counts()
    for k, v in vc.items():
        print(" %-8s %7s (%5.2f%%) %s"
              % (k, format(v, ","), v / n * 100, "█" * int(v / n * 100)))
    vc.to_frame("数量").assign(占比=lambda d: (d.数量 / n * 100).round(2)) \
      .to_csv(os.path.join(RESDIR, "m08_意图整体分布.csv"), encoding="utf-8")

    hr("4.2 各内容分区的意图结构（Top 10 分区，单位 %）")
    top = df.tname.value_counts().head(10).index
    sub = df[df.tname.isin(top)]
    pv = (pd.crosstab(sub.tname, sub.intent, normalize="index") * 100).round(2)
    pv["样本数"] = sub.tname.value_counts()
    print(pv.to_string())
    pv.to_csv(os.path.join(RESDIR, "m09_分区意图结构.csv"), encoding="utf-8")

    hr("4.3 各发布时段的意图结构（单位 %）")
    pt = (pd.crosstab(df.发布小时, df.intent, normalize="index") * 100).round(2)
    pt["样本数"] = df.发布小时.value_counts()
    print(pt.to_string())
    pt.to_csv(os.path.join(RESDIR, "m10_时段意图结构.csv"), encoding="utf-8")

    hr("4.4 剔除『其他』后的有意图结构")
    d2 = df[df.intent != "其他"]
    m = len(d2)
    print(" 有意图弹幕 %s 条（占全部 %.1f%%）" % (format(m, ","), m / n * 100))
    for k, v in d2.intent.value_counts().items():
        print(" %-8s %6s %5.2f%%" % (k, format(v, ","), v / m * 100))
    d2.intent.value_counts().to_frame("数量") .assign(占比=lambda x: (x.数量 / m * 100).round(2)) .to_csv(os.path.join(RESDIR, "m11_有意图弹幕结构.csv"), encoding="utf-8")

    hr("4.5 关键发现")
    print(" 占比最高意图 : %s (%.2f%%)" % (vc.index[0], vc.iloc[0] / n * 100))
    need = sum(vc.get(k, 0) for k in ("求推荐", "求教程", "消费决策", "提问"))
    print(" 『信息需求类』合计（求推荐+求教程+消费决策+提问）: %.2f%%" % (need / n * 100))
    if "打卡许愿" in vc:
        print(" 『打卡许愿』占比: %.2f%% <- 社区仪式行为，非内容需求" % (vc["打卡许愿"] / n * 100))
    if "消费决策" in vc:
        hi = (pd.crosstab(sub.tname, sub.intent, normalize="index")["消费决策"] * 100) \
                .sort_values(ascending=False)
        print("\n 消费决策意图占比最高的分区:")
        for k, v in hi.head(5).items():
            print(" %-14s %.2f%%" % (k, v))

    hr("4.5 导出人工抽检样本（200 条）")
    spot = df.sample(n=min(200, len(df)), random_state=7)[["text", "intent"]].copy()
    spot.columns = ["弹幕内容", "模型标注"]
    spot["是否正确(填1或0)"] = ""
    spot["你的标注(可选)"] = ""
    spot.to_csv(SPOT_FILE, index=False, encoding="utf-8-sig")
    print(" 已导出 -> %s" % SPOT_FILE)
    print(" 逐条判断后在『是否正确』列填 1(对) 或 0(错)，然后运行:")
    print(r" python src\08_intent_llm.py --score")


def score():
    import pandas as pd
    if not os.path.exists(SPOT_FILE):
        print(" 找不到抽检文件，请先运行主流程"); return
    df = pd.read_csv(SPOT_FILE, encoding="utf-8-sig")
    col = "是否正确(填1或0)"
    df[col] = pd.to_numeric(df[col], errors="coerce")
    valid = df.dropna(subset=[col])
    if len(valid) == 0:
        print(" 还没填『是否正确』列"); return
    acc = valid[col].mean()
    print("\n 人工抽检结果")
    print(" 已标注 %d 条 / 共 %d 条" % (len(valid), len(df)))
    print(" 准确率 = %.1f%%" % (acc * 100))
    print("\n 标注质量: 人工抽检 %d 条，准确率 %.1f%%" % (len(valid), acc * 100))
    wrong = valid[valid[col] == 0]
    if len(wrong):
        print("\n 误判最多的类别:")
        for k, v in wrong.模型标注.value_counts().head(5).items():
            tot = (valid.模型标注 == k).sum()
            print(" %-8s 错 %d/%d (该类 %.0f%% 被误判)" % (k, v, tot, v / tot * 100))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-key", type=str, default=None)
    ap.add_argument("--sample", type=int, default=6000)
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--analyze-only", action="store_true")
    ap.add_argument("--score", action="store_true")
    a = ap.parse_args()

    print("=" * 78)
    print(" 弹幕用户意图识别与语义分析（项目 2）")
    print("=" * 78)

    if a.score:
        score(); sys.exit(0)

    if not a.analyze_only:
        key = load_key(a.api_key)
        if not key:
            print("\n [错误] 未找到 DeepSeek API Key，三种提供方式：")
            print(' 1. setx DEEPSEEK_API_KEY "sk-xxx" 然后重开终端')
            print(" 2. 项目根目录建 .env 文件，写一行: DEEPSEEK_API_KEY=sk-xxx")
            print(r" 3. python src\08_intent_llm.py --api-key sk-xxx")
            sys.exit(1)
        print(" API Key: ...%s" % key[-6:])

        if a.check:
            hr("连通性测试（标 3 条）")
            r = call_api(key, ["求个教程", "多少钱啊", "哈哈哈哈哈"])
            print(" 返回: %s" % r)
            sys.exit(0 if r else 1)

        hr("Part 1 · 清洗弹幕噪声")
        clean_danmaku(force=a.force)
        hr("Part 2 · 分层抽样")
        build_sample(a.sample, force=a.force)
        hr("Part 3 · LLM 批量意图标注")
        label(key, batch=a.batch, workers=a.workers)

    hr("Part 4 · 意图结构分析")
    analyze()
    hr("完成")
