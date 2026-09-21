# -*- coding: utf-8 -*-
r"""prompts.py —— 可复用 Prompt 模板库

为什么需要这个文件：
    项目里的 Prompt 原本分散硬编码在各个脚本中（08_intent_llm.py、13_text_to_sql_agent.py）。
    这带来四个问题：换项目要复制粘贴、改动容易漏、无法单独测试、无法版本管理。
    这个模块把 Prompt 当作「一等公民」来管理。

设计原则：
    1. 关注点分离 —— 角色 / 上下文 / 任务 / 约束 / 输出格式 各自独立成字段，
       而不是揉成一大段字符串。这样改一处不会牵连其他部分。
    2. 模板化 —— 可变部分用 {占位符}，同一套逻辑能套用到不同数据库和业务。
    3. 约束清单化 —— 用列表而不是自然段，便于逐条增删与回归验证。
    4. 设计说明随行 —— 每个 Prompt 记录「为什么这么写」，
       这是 Prompt 从「试出来的」变成「可维护的」的关键。
    5. 版本号 —— Prompt 迭代需要可追溯，否则线上效果变化无从归因。

用法:
    from prompts import build_messages, PROMPTS

    msgs = build_messages("sql_generate", schema=SCHEMA, question="哪个分区互动率最高")
    # -> [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}]

命令行查看:
    python src/prompts.py --list
    python src/prompts.py --show sql_generate
"""
import argparse
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class Prompt:
    """一个可复用的 Prompt 模板"""
    name: str
    version: str
    role: str                       # 角色设定：模型该以什么身份回答
    task: str                       # 任务描述：要它做什么
    constraints: List[str] = field(default_factory=list)   # 硬性约束：不能违反什么
    output_format: str = ""         # 输出格式：结构化要求
    context: str = ""               # 上下文模板：如 {schema}，运行时填充
    user_template: str = ""         # 用户消息模板
    design_notes: str = ""          # 设计说明：为什么这么写（面试可讲）

    def system(self, **ctx) -> str:
        """拼装 system 消息：上下文 -> 任务 -> 约束 -> 输出格式"""
        parts = [self.role]
        if self.context:
            parts.append(self.context.format(**ctx))
        parts.append(self.task)
        if self.constraints:
            parts.append("【硬性约束】")
            parts.extend("%d. %s" % (i, c) for i, c in enumerate(self.constraints, 1))
        if self.output_format:
            parts.append("【输出格式】只返回 JSON：\n" + self.output_format)
        return "\n\n".join(parts)

    def user(self, **ctx) -> str:
        return self.user_template.format(**ctx) if self.user_template else ""

    def build(self, **ctx) -> List[Dict[str, str]]:
        return [{"role": "system", "content": self.system(**ctx)},
                {"role": "user", "content": self.user(**ctx)}]


# ==============================================================
#  Prompt 定义
# ==============================================================
PROMPTS: Dict[str, Prompt] = {}


def register(p: Prompt):
    PROMPTS[p.name] = p
    return p


# --------------------------------------------------------------
register(Prompt(
    name="sql_generate",
    version="1.2",
    role="你是一位资深数据分析师，负责把业务问题翻译成 MySQL 查询。",
    context="{schema}",
    task="根据上面的数据库结构和业务问题，写出一条能回答该问题的 MySQL 查询。",
    constraints=[
        "只写 SELECT，禁止 INSERT / UPDATE / DELETE / DROP 等任何写操作",
        "表名和字段名必须来自上面给出的真实结构，不得臆造或改写（例如不要写复数表名）",
        "数字结果用 ROUND(x, 4) 保留合理小数位；比率可以乘 100 转成百分比",
        "排序后默认加 LIMIT 20 以内，避免返回过多行",
        "严格遵守上面的统计口径规则，特别是聚合时的最小样本量要求",
    ],
    output_format='{"sql": "SELECT ...", "intent": "一句话说明这条SQL在算什么", '
                  '"tables": ["用到的表"], "notes": "口径或限制提示，没有则留空"}',
    design_notes=(
        "【为什么把约束写成清单而不是自然段】\n"
        "  清单便于逐条增删，且每一条都对应一类真实的失败模式。例如「不得臆造表名」"
        "对应的是模型把 category 写成 categories 的表名幻觉。约束清单实际上是一份"
        "「已知错误清单」的沉淀。\n\n"
        "【为什么 schema 放在 system 而不是 user】\n"
        "  schema 是稳定不变的上下文，问题才是变化的。放在 system 里可以让同一份"
        "  schema 的多次提问复用前缀，也符合「系统设定 vs 用户输入」的语义分层。\n\n"
        "【为什么要求输出 JSON 而不是纯 SQL】\n"
        "  纯 SQL 无法携带意图和口径提示。加上 intent 字段后，一方面便于日志排查，"
        "  另一方面模型在写 intent 时会先想清楚「这条 SQL 到底在算什么」，"
        "  实测能减少语义层面的错误。"
    ),
    user_template="问题：{question}",
))


# --------------------------------------------------------------
register(Prompt(
    name="sql_repair",
    version="1.1",
    role="你是 MySQL 专家，擅长根据报错信息定位并修正 SQL 问题。",
    context="{schema}",
    task="下面这条 SQL 执行失败了。请分析失败原因，并给出修正后的 SQL。",
    constraints=[
        "只修改导致报错的部分，不要顺手改动其他逻辑",
        "修正后的语句仍必须是可执行的单条 SELECT",
        "如果报错源于字段或表名与真实结构不符，以真实结构为准",
    ],
    output_format='{"sql": "修正后的SELECT语句", "reason": "错在哪里、依据什么修正"}',
    design_notes=(
        "【这是 Agent 与「一次性生成」的分水岭】\n"
        "  只有生成没有修复的系统，遇到 SQL 报错就结束了。而真实场景里第一次写错是常态，"
        "  尤其是字段名和表名——模型不知道你的库实际怎么命名。\n\n"
        "【为什么要求输出 reason】\n"
        "  reason 不是给用户看的装饰，它是可观测性的一部分：当自愈频繁触发时，"
        "  看 reason 的分布就能知道该往 schema 里补什么。如果 80% 的自愈都是表名幻觉，"
        "  说明 schema 的表名描述不够醒目，应该改 schema 而不是加更多重试。\n\n"
        "【为什么强调「只改报错部分」】\n"
        "  实测发现如果不加这条约束，模型倾向于重写整条 SQL，有时会把原本正确的"
        "  业务口径（比如 HAVING COUNT(*) >= 30）改掉。修复行为本身需要被约束。"
    ),
    user_template=("原始业务问题：{question}\n\n"
                   "执行失败的 SQL：\n{sql}\n\n"
                   "MySQL 报错信息：\n{error}\n\n"
                   "请分析原因并给出修正后的 SQL。"),
))


# --------------------------------------------------------------
register(Prompt(
    name="sql_interpret",
    version="1.2",
    role="你是资深商业分析师，负责把查询结果翻译成业务结论。",
    task="基于下面的 SQL 查询结果，用中文回答业务问题。",
    constraints=[
        "直接给结论，不要复述 SQL 语句",
        "必须引用具体数字，并突出最大 / 最小 / 差异",
        "2 到 4 句话，简洁专业，不要分点罗列",
        "如果样本量偏小或口径存在限制，用一句话带过",
        "不要编造结果里没有的信息",
    ],
    output_format="",
    design_notes=(
        "【为什么限制在 2-4 句】\n"
        "  模型默认倾向写一大段。但业务方要的是结论不是报告，过长反而没人看。\n\n"
        "【为什么强制引用具体数字】\n"
        "  不加这条约束时，模型会写「互动率表现良好」这类无法验证的话。"
        "  强制引用数字后，输出自然变得可核查。\n\n"
        "【为什么要求提示样本量限制】\n"
        "  这是从一次真实错误里加的规则：模型曾算出「音乐教学分区互动率最高 25.42%」，"
        "  而该分区只有 3 条视频。SQL 本身合法，错误出在结论层面。"
        "  加上「样本量偏小要提示」之后，输出会自动带上「但样本仅 91 条，代表性有限」。\n\n"
        "【为什么禁止编造】\n"
        "  模型在解读时会「合理外推」，比如从相关性推断因果。明确禁止能减少这类越界。"
    ),
    user_template=("业务问题：{question}\n\n"
                   "SQL：\n{sql}\n\n"
                   "查询结果（共 {n_rows} 行，展示前 {n_show} 行）：\n{preview}"),
))


# --------------------------------------------------------------
register(Prompt(
    name="intent_classify",
    version="2.0",
    role="你是内容社区的用户意图分析专家，负责为弹幕标注用户意图。",
    task="为下面每一条弹幕标注一个意图类别。",
    constraints=[
        "只能从给定的类别中选择，不得自创类别",
        "每条弹幕只给一个类别",
        "判断依据是文本本身，不要脑补视频内容",
        "拿不准时优先归入「其他」，不要硬凑到某个类别",
    ],
    output_format='{"results": [{"id": 1, "intent": "类别名"}, ...]}，'
                  "数组长度必须等于输入条数，顺序一一对应",
    design_notes=(
        "【v2.0 的关键改动：给「提问」加了硬性判据】\n"
        "  v1.0 只写了「询问事实性信息」，结果模型把陈述句也塞进来——"
        "  「时间复杂度」这种纯术语、「但佳能是日本的」这种断言都被判成提问。\n"
        "  人工抽检发现误判率约 40%。\n"
        "  v2.0 加了可判定的条件：句末有问号，或含「吗/呢/怎么/为什么/是不是/哪/谁/多少」，"
        "  并显式列出反例。修复后人工抽检「提问」类的正确率从约 60% 升到 90%。\n\n"
        "【为什么新增「打卡许愿」类别】\n"
        "  v1.0 只有 8 类，「其他」占比高达 42.4%。抽查发现里面混着大量「打卡」「许愿」"
        "  「立flag」类内容——这是内容社区里量很大的独立行为，反映社区归属感。\n"
        "  新增为第 9 类后，「其他」降到 35%。\n\n"
        "【为什么要求 id 而不是靠顺序】\n"
        "  批量标注时模型偶尔会漏掉或合并条目。带上 id 后能精确对齐，"
        "  缺哪条一目了然，而不是整体错位。"
    ),
    user_template="请标注以下 {n} 条弹幕的意图：\n{items}",
))


# ==============================================================
#  对外接口
# ==============================================================
def build_messages(name: str, **ctx) -> List[Dict[str, str]]:
    """按名称构建 messages，供各家 LLM API 直接使用"""
    if name not in PROMPTS:
        raise KeyError("未注册的 Prompt: %s（可选：%s）" % (name, ", ".join(PROMPTS)))
    return PROMPTS[name].build(**ctx)


def main():
    ap = argparse.ArgumentParser(description="Prompt 模板库")
    ap.add_argument("--list", action="store_true", help="列出全部 Prompt")
    ap.add_argument("--show", help="查看某个 Prompt 的完整内容与设计说明")
    args = ap.parse_args()

    if args.show:
        p = PROMPTS.get(args.show)
        if not p:
            print("未找到：%s" % args.show); return
        print("=" * 78)
        print("  Prompt: %s   (v%s)" % (p.name, p.version))
        print("=" * 78)
        print("\n--- system 模板 ---\n")
        print(p.system(schema="[运行时注入的表结构]", question="[运行时注入]", sql="[..]",
                       error="[..]", n_rows=0, n_show=0, preview="[..]",
                       n=0, items="[..]"))
        print("\n--- user 模板 ---\n")
        print(p.user(question="[..]", sql="[..]", error="[..]", n_rows=0, n_show=0,
                     preview="[..]", n=0, items="[..]"))
        print("\n--- 设计说明 ---\n")
        print(p.design_notes)
        return

    print("=" * 78)
    print("  可复用 Prompt 模板库（共 %d 个）" % len(PROMPTS))
    print("=" * 78)
    for name, p in PROMPTS.items():
        print("\n  %-18s v%-5s  约束 %d 条" % (name, p.version, len(p.constraints)))
        print("      %s" % p.task)
        print("      设计说明 %d 字" % len(p.design_notes))
    print("\n  用 --show <名称> 查看完整内容")


if __name__ == "__main__":
    main()
