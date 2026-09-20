# -*- coding: utf-8 -*-
"""06_export_results.py —— 执行 02_analysis.sql 并把每条结果导出为 CSV

为什么用脚本而不是 Navicat 手工导出：
  1. 可复现 —— 改了 SQL 重跑一条命令，全部结果重新生成
  2. 命名统一 —— 不会出现"无标题.csv"
  3. 可校验 —— 每条语句的预期特征词会被验证，SQL 被改乱会立刻报错

用法:
    python src/06_export_results.py # 导出全部 12 条
    python src/06_export_results.py --bom # 同时输出带 BOM 的版本（Excel 双击可开）
    python src/06_export_results.py --only q05 # 只导某一条
"""
import argparse, csv, os, re, sys

import pymysql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQLFILE = os.path.join(ROOT, "sql", "02_analysis.sql")
OUTDIR = os.path.join(ROOT, "results")
os.makedirs(OUTDIR, exist_ok=True)

DB = dict(host="127.0.0.1", port=3306, user="root", password="",
          database="content_analysis", charset="utf8mb4",
          cursorclass=pymysql.cursors.DictCursor)

# (输出文件名, 用于校验该语句没被改乱的特征词)
MAPPING = [
    ("q01_视频明细宽表.csv", "v.bvid"),
    ("q02_分区播放量Top10.csv", "ROW_NUMBER"),
    ("q03_分区互动率分位.csv", "PERCENT_RANK"),
    ("q04_爆款口径.csv", "NTILE(20)"),
    ("q05_发布时段效应.csv", "pub_hour BETWEEN 0"),
    ("q06_粉丝量级效应.csv", "follower, 0) < 1000"),
    ("q07_标签互动率.csv", "video_tag"),
    ("q08_视频时长效应.csv", "duration_s > 14400"),
    ("q09_分区月度环比.csv", "LAG(平均互动率)"),
    ("q10_分区贡献度.csv", "对整体互动率的贡献"),
    ("q11_爆款特征画像.csv", "晚间发布占比"),
    ("q12_数据质量校验.csv", "检查项"),
]


# ── 合规：导出前剔除「内容原文」与「创作者标识」 ─────────────────────
# 视频标题是创作者的作品，UP主用户名属可识别的个人信息。
# 批量复制到公开仓库既无分析价值，也无必要；
# 数据点本身（播放/点赞/时长等纯事实）不受著作权保护，予以保留。
# bvid 作为可追溯的公开标识保留，便于他人核验数据真实性。
DROP_COLS = {
    "q01_视频明细宽表.csv": ["title", "up主"],
    "q02_分区播放量Top10.csv": ["title", "up主"],
}


def parse_statements(path):
    """把 SQL 文件切成单条语句（丢弃注释与 USE）"""
    text = open(path, encoding="utf-8").read()
    stmts, buf = [], []
    for line in text.split("\n"):
        if line.strip().startswith("--"):
            continue # 丢弃注释行
        buf.append(line)
        if ";" in line:
            sql = "\n".join(buf)
            sql = sql[:sql.rindex(";")].strip() # 去掉分号及之后的残留
            if sql and not sql.upper().startswith("USE"):
                stmts.append(sql)
            buf = []
    return stmts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bom", action="store_true", help="额外输出 UTF-8-BOM 版本")
    ap.add_argument("--only", type=str, default="")
    a = ap.parse_args()

    stmts = parse_statements(SQLFILE)
    print(f"SQL 文件: {SQLFILE}")
    print(f"解析出 {len(stmts)} 条语句，预期 {len(MAPPING)} 条")
    if len(stmts) != len(MAPPING):
        print(f" [警告] 数量不一致，请检查 SQL 文件")

    conn = pymysql.connect(**DB)
    ok = fail = 0
    total_rows = 0

    for i, (fn, key) in enumerate(MAPPING):
        if i >= len(stmts):
            break
        if a.only and not fn.startswith(a.only):
            continue
        sql = stmts[i]

        # 校验
        if key not in sql:
            print(f" [跳过] {fn} 特征词 '{key}' 未找到，SQL 可能已被改动")
            fail += 1
            continue

        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
        except Exception as e:
            print(f" [失败] {fn} {type(e).__name__}: {str(e)[:90]}")
            fail += 1
            continue

        if not rows:
            print(f" [空] {fn} 查询无结果")
            continue

        drop = DROP_COLS.get(fn, [])
        cols = [c for c in rows[0].keys() if c not in drop]
        path = os.path.join(OUTDIR, fn)
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            w.writerow(cols)
            for r in rows:
                w.writerow(["" if r[c] is None else r[c] for c in cols])
        if drop:
            print(" └ 合规剔除字段: " + ", ".join(drop))

        if a.bom:
            raw = open(path, "rb").read()
            with open(path.replace(".csv", "_bom.csv"), "wb") as f:
                f.write(b"\xef\xbb\xbf" + raw)

        total_rows += len(rows)
        ok += 1
        print(f" [OK] {fn:<28} {len(rows):>5,} 行 × {len(cols):>2} 列 "
              f"{os.path.getsize(path)/1024:>7.1f} KB")

    print(f"\n导出完成：{ok} 个文件成功，{fail} 个失败，共 {total_rows:,} 行")
    print(f"输出目录: {OUTDIR}")


if __name__ == "__main__":
    main()
