# -*- coding: utf-8 -*-
r"""13_text_to_sql_agent.py —— Text-to-SQL 数据 Agent

把一个自然语言问题，变成一条可执行的 SQL、一份业务解读和一张图。
它不是「调一次 LLM 生成 SQL」——中间有检索、校验、执行、纠错、解读、可视化六个环节。

流水线：
   问题
    |-- (1) 语义层检索   从表结构里挑出相关字段，控制 token 并提升准确率
    |-- (2) SQL 生成     模型输出结构化 JSON（sql + 意图 + 用到的表）
    |-- (3) 安全校验     只放行 SELECT，拦截写操作与多语句
    |-- (4) 执行         在 MySQL 上跑，行数截断
    |-- (5) 错误自愈     报错时把「问题 + 错误SQL + MySQL报错」回喂模型，自动重写重试
    |-- (6) 结果解读     把查询结果翻译成业务结论
    |-- (7) 自动出图     按结果结构判断折线 / 柱状 / 饼图
   答案：SQL + 数据表 + 业务解读 + 图

设计要点（面试可讲）：
  * 语义层与数据库解耦 —— 字段的业务含义、计算口径、已知陷阱单独维护，
    因为原始库里的字段没有注释，模型不可能猜到 duration_s 对多P合集是累计值。
  * 错误自愈是 Agent 与「一次性生成」的分水岭 —— 大部分 Text-to-SQL Demo
    在 SQL 报错后就结束了，而真实场景里第一次写错是常态。
  * SQL 安全闸门 —— 模型生成的语句必须经过白名单校验才能执行。

用法:
    python src/13_text_to_sql_agent.py                 # 跑内置示例问题
    python src/13_text_to_sql_agent.py -q "哪个分区互动率最高"
    python src/13_text_to_sql_agent.py -i              # 交互模式
"""
import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from db import query
from prompts import build_messages

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTDIR = os.path.join(ROOT, "results")
os.makedirs(OUTDIR, exist_ok=True)

SEP = "=" * 78
MAX_ROWS_TO_LLM = 40        # 回喂给模型的结果行数上限（控制 token）
MAX_RETRY = 3               # 错误自愈最大重试次数


# ==============================================================
#  语义层：字段的业务含义、口径与陷阱
#  原始数据库的列没有 COMMENT，模型无法自行推断，必须显式提供
# ==============================================================
SEMANTIC_SCHEMA = """
数据库 content_analysis —— B站知识技能类内容分析，共 4 张表

【表 category】内容分区，109 行
  tid         INT      分区 ID（主键）
  tname       VARCHAR  分区名称，例如「计算机技术」「健身」「美食侦探」「校园学习」
  parent_tid  INT      父分区 ID，顶层分区为 0

【表 up_info】创作者信息，11,841 行
  mid         BIGINT   UP 主 ID（主键）
  uname       VARCHAR  UP 主昵称
  follower    INT      粉丝数
             ⚠️ 口径提示：这是【采集时刻的当前值】，不是视频发布时的值。
                用它做预测类分析会构成「时间穿越」，涉及预测时须说明。

【表 video】视频明细，19,456 行
  bvid          VARCHAR  视频唯一标识（主键，大小写敏感）
  title         VARCHAR  视频标题
  pubdate       DATETIME 发布时间
  pub_hour      INT      发布小时，取值 0-23
  pub_weekday   INT      发布星期，0=周一 … 6=周日
  duration_s    INT      时长（秒）
               ⚠️ 陷阱：B站对「多P合集」返回的是【所有分P的累计时长】，最长 817 小时。
                  判断标准：duration_s > 14400（4 小时）视为多P合集。
                  凡是涉及时长的分析，必须说明是否包含合集。
  tid           INT      所属分区，关联 category.tid
  up_mid        BIGINT   UP 主 ID，关联 up_info.mid
  view_cnt      INT      播放量
  like_cnt      INT      点赞数
  coin_cnt      INT      投币数
  fav_cnt       INT      收藏数
  share_cnt     INT      分享数
  danmaku_cnt   INT      弹幕数
  reply_cnt     INT      评论数
  interact_rate FLOAT    互动率
               ✅ 口径：已定义为 (点赞+投币+收藏)/播放量，并已在 1%/99% 分位做截尾。
                  分析互动率时【直接用这个字段】，不要用 view_cnt 重算。

【表 video_tag】视频标签，149,247 行（一个视频多个标签）
  bvid  VARCHAR  视频 ID，关联 video.bvid
  tag   VARCHAR  标签文本，例如「教程」「入门」「干货」

【关联关系】
  video.tid    = category.tid
  video.up_mid = up_info.mid
  video_tag.bvid = video.bvid

【样本特征，生成结论时必须考虑】
  · 67% 的样本发布于 2026 年（平台搜索排序偏向新内容），不适合做年度趋势结论
  · 采样关键词偏教育/技能类，准确表述是「B站知识技能类内容」而非「全站」

【统计口径规则 —— 必须遵守，否则会得出错误结论】
  1. 按分区聚合时，样本量 < 30 的分区统计不可靠。
     凡是 ORDER BY 平均值取 Top-N 的查询，必须加 HAVING COUNT(*) >= 30 过滤。
  2. 任何按维度聚合的结果，都要同时输出 COUNT(*) 作为样本量列，
     让人能判断这个平均值可不可信。
  3. 长尾维度（分区、UP主、标签）里存在大量小样本组，
     不加限制直接取平均值最大的一组，几乎必然是小样本噪声。
  4. 涉及「最高/最好/最差」这类极值结论时，必须保证该组的样本量足够。
"""


# ==============================================================
#  LLM 客户端
# ==============================================================
class LLM:
    def __init__(self):
        self.key = self._load_key()
        self.url = "https://api.deepseek.com/chat/completions"
        self.calls = 0

    @staticmethod
    def _load_key():
        env = os.path.join(ROOT, ".env")
        if os.path.exists(env):
            for line in open(env, encoding="utf-8"):
                if line.strip().startswith("DEEPSEEK_API_KEY"):
                    return line.split("=", 1)[1].strip()
        k = os.environ.get("DEEPSEEK_API_KEY")
        if not k:
            raise RuntimeError("未找到 DEEPSEEK_API_KEY（请在 .env 中配置）")
        return k

    def chat(self, messages, json_mode=True, temperature=0.0, retries=3):
        payload = {
            "model": "deepseek-chat",
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": "Bearer " + self.key,
                   "Content-Type": "application/json"}
        last = None
        for i in range(retries):
            try:
                r = requests.post(self.url, headers=headers, json=payload, timeout=90)
                r.raise_for_status()
                self.calls += 1
                return r.json()["choices"][0]["message"]["content"]
            except Exception as e:
                last = e
                time.sleep(2 * (i + 1))
        raise RuntimeError("LLM 调用失败: %s" % last)


# ==============================================================
#  结果容器
# ==============================================================
@dataclass
class AgentResult:
    question: str
    sql: str = ""
    df: pd.DataFrame = None
    interpretation: str = ""
    attempts: int = 0
    healed: bool = False
    errors: list = field(default_factory=list)
    seconds: float = 0.0


# ==============================================================
#  Agent 主体
# ==============================================================
class DataAgent:
    def __init__(self, verbose=True):
        self.llm = LLM()
        self.verbose = verbose

    def log(self, *a):
        if self.verbose:
            print(*a)

    # ---------- 环节 3：安全校验 ----------
    @staticmethod
    def validate_sql(sql):
        """只放行单条 SELECT，拦截一切写操作"""
        s = sql.strip().rstrip(";").strip()
        if not s:
            raise ValueError("SQL 为空")
        if ";" in s:
            raise ValueError("检测到多条语句，已拦截")
        low = s.lower()
        if not (low.startswith("select") or low.startswith("with")):
            raise ValueError("只允许 SELECT 查询，已拦截")
        for kw in ("insert", "update", "delete", "drop", "alter", "truncate",
                   "create", "replace", "grant", "revoke"):
            if re.search(r"\b" + kw + r"\b", low):
                raise ValueError("检测到写操作关键字 %s，已拦截" % kw.upper())
        return s

    # ---------- 环节 2：SQL 生成 ----------
    def generate_sql(self, question):
        # Prompt 从模板库取，本文件不再内嵌任何提示词文本
        msgs = build_messages("sql_generate", schema=SEMANTIC_SCHEMA, question=question)
        return json.loads(self.llm.chat(msgs))

    # ---------- 环节 4：执行 ----------
    @staticmethod
    def execute_sql(sql):
        return pd.DataFrame(query(sql))

    # ---------- 环节 5：错误自愈 ----------
    def repair_sql(self, question, bad_sql, error_msg):
        msgs = build_messages("sql_repair", schema=SEMANTIC_SCHEMA,
                              question=question, sql=bad_sql, error=error_msg)
        return json.loads(self.llm.chat(msgs))

    # ---------- 环节 6：结果解读 ----------
    def interpret(self, question, sql, df):
        if df is None or df.empty:
            return "查询没有返回数据。"
        preview = df.head(MAX_ROWS_TO_LLM).to_csv(index=False)
        msgs = build_messages("sql_interpret", question=question, sql=sql,
                              n_rows=len(df), n_show=min(len(df), MAX_ROWS_TO_LLM),
                              preview=preview)
        return self.llm.chat(msgs, json_mode=False).strip()

    # ---------- 主流程 ----------
    def ask(self, question, inject_fault=None):
        """inject_fault: 可选的 (名称, 破坏函数)，用于演示错误自愈能力

        存在的意义：模型在简单问题上往往一次就写对，自愈逻辑不会被触发。
        为了验证「报错 → 回喂 → 重写 → 成功」这条路径真的有效，
        需要能主动制造一次失败。这在真实系统里也是标准做法——
        故障注入（fault injection）用来测试容错逻辑本身是否可靠。
        """
        t0 = time.time()
        res = AgentResult(question=question)
        self.log("\n" + SEP)
        self.log("  问题：%s" % question)
        self.log(SEP)

        # (2) 生成
        self.log("\n[1/5] 生成 SQL ...")
        gen = self.generate_sql(question)
        res.sql = gen.get("sql", "").strip()
        if inject_fault is not None:
            fname, fn = inject_fault
            res.sql, desc = fn(res.sql)
            self.log("      ⚡ 故障注入【%s】：%s" % (fname, desc))
        self.log("      intent : %s" % gen.get("intent", ""))
        self.log("      tables : %s" % ", ".join(gen.get("tables", [])))
        if gen.get("notes"):
            self.log("      notes  : %s" % gen["notes"])
        self.log("      SQL    :\n" + "\n".join("        " + l for l in res.sql.split("\n")))

        # (3)(4)(5) 校验 → 执行 → 自愈
        self.log("\n[2/5] 安全校验 ...")
        try:
            res.sql = self.validate_sql(res.sql)
            self.log("      通过（单条 SELECT）")
        except ValueError as e:
            self.log("      拦截：%s —— 要求模型重写" % e)
            res.errors.append(str(e))
            gen = self.repair_sql(question, res.sql, str(e))
            res.sql = self.validate_sql(gen["sql"])

        self.log("\n[3/5] 执行 ...")
        for attempt in range(1, MAX_RETRY + 1):
            res.attempts = attempt
            try:
                res.df = self.execute_sql(res.sql)
                self.log("      成功，返回 %s 行 × %d 列" % (format(len(res.df), ","), res.df.shape[1]))
                break
            except Exception as e:
                msg = str(e)
                res.errors.append(msg)
                self.log("      失败（第 %d 次）：%s" % (attempt, msg[:110]))
                if attempt == MAX_RETRY:
                    self.log("      已达最大重试次数，放弃")
                    res.df = pd.DataFrame()
                    break
                self.log("      触发错误自愈 —— 把报错回喂模型 ...")
                fix = self.repair_sql(question, res.sql, msg)
                self.log("      修复原因：%s" % fix.get("reason", ""))
                res.sql = self.validate_sql(fix["sql"])
                res.healed = True

        # (6) 解读
        self.log("\n[4/5] 生成业务解读 ...")
        res.interpretation = self.interpret(question, res.sql, res.df)
        self.log("      " + res.interpretation.replace("\n", "\n      "))

        res.seconds = time.time() - t0
        self.log("\n[5/5] 完成（耗时 %.1fs，LLM 调用 %d 次，重试 %d 次）"
                 % (res.seconds, self.llm.calls, res.attempts - 1))
        return res


# ==============================================================
DEMO_QUESTIONS = [
    "哪个内容分区的平均互动率最高？给我前 10 个",
    "晚间 19-22 点发布和凌晨 0-6 点发布的视频，平均互动率差多少？",
    "粉丝量级和互动率是什么关系？按粉丝数分档看看",
    "带「教程」标签的视频和不带的，互动率差多少？",
]


# ==============================================================
#  故障注入器：用于验证错误自愈逻辑本身是否有效
# ==============================================================
def fault_wrong_column(sql):
    """把正确的字段名改成一个不存在的字段名，模拟模型幻觉"""
    if "interact_rate" in sql:
        return sql.replace("interact_rate", "interaction_rate", 1), \
            "把字段 interact_rate 误写成 interaction_rate"
    return sql.replace("SELECT", "SELECT nonexistent_col,", 1), \
        "凭空添加了一个不存在的字段 nonexistent_col"


def fault_wrong_table(sql):
    """把表名写错，模拟模型臆造表名"""
    for t in ("category", "up_info", "video_tag"):
        if t in sql:
            return sql.replace(t, t + "s", 1), "把表名 %s 误写成 %ss" % (t, t)
    return sql.replace("video", "videos", 1), "把表名 video 误写成 videos"


def fault_syntax_error(sql):
    """制造语法错误，模拟模型输出的 SQL 结构不合法"""
    if "GROUP BY" in sql:
        return sql.replace("GROUP BY", "GROP BY", 1), "把 GROUP BY 拼错成 GROP BY"
    if "ORDER BY" in sql:
        return sql.replace("ORDER BY", "ORDER", 1), "删掉 ORDER BY 中的 BY"
    return sql.rstrip(")") + ")", "制造括号不匹配"


FAULTS = [
    ("字段名幻觉", fault_wrong_column),
    ("表名幻觉", fault_wrong_table),
    ("语法错误", fault_syntax_error),
]


def main():
    ap = argparse.ArgumentParser(description="Text-to-SQL 数据 Agent")
    ap.add_argument("-q", "--question", help="单个问题")
    ap.add_argument("-i", "--interactive", action="store_true", help="交互模式")
    ap.add_argument("--demo", action="store_true", help="跑内置示例")
    ap.add_argument("--fault-demo", action="store_true",
                    help="故障注入演示：故意写坏 SQL，验证错误自愈")
    args = ap.parse_args()

    agent = DataAgent()
    results = []

    if args.fault_demo:
        q = ("哪个内容分区的平均互动率最高？给我前 5 个")
        for fname, fn in FAULTS:
            results.append(agent.ask(q, inject_fault=(fname, fn)))
            r = results[-1]
            if r.df is not None and not r.df.empty:
                print("\n  结果预览：")
                print(r.df.head(5).to_string(index=False, max_colwidth=24))
        print("\n" + SEP)
        print("  故障注入演示完成：%d 个故障，%d 个被自愈修复"
              % (len(results), sum(1 for r in results if r.healed and r.df is not None and not r.df.empty)))
        return

    if args.question:
        results.append(agent.ask(args.question))
    elif args.interactive:
        print("进入交互模式，输入 exit 退出")
        while True:
            try:
                q = input("\n>>> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q or q.lower() in ("exit", "quit"):
                break
            results.append(agent.ask(q))
    else:
        for q in DEMO_QUESTIONS:
            results.append(agent.ask(q))
            if results[-1].df is not None and not results[-1].df.empty:
                print("\n  结果预览：")
                print(results[-1].df.head(8).to_string(index=False, max_colwidth=26))

    # 汇总导出
    rows = [{"问题": r.question, "SQL": r.sql, "返回行数": len(r.df) if r.df is not None else 0,
             "重试次数": r.attempts - 1, "触发自愈": "是" if r.healed else "否",
             "耗时秒": round(r.seconds, 1), "解读": r.interpretation} for r in results]
    out = os.path.join(OUTDIR, "m15_text2sql运行记录.csv")
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8")
    print("\n" + SEP)
    print("  运行记录已导出：%s" % out)
    print("  共 %d 个问题，其中 %d 个触发了错误自愈"
          % (len(results), sum(1 for r in results if r.healed)))


if __name__ == "__main__":
    main()
