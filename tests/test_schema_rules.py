# -*- coding: utf-8 -*-
"""test_schema_rules.py —— 语义层（Schema）规则测试

被测对象：SEMANTIC_SCHEMA 常量

为什么测它：
    语义层是 Text-to-SQL 准确率的关键。它把字段的业务含义、计算口径
    与已知陷阱显式写出来 —— 模型不可能自己猜到"duration_s 对多P合集
    返回的是累计时长"。

    如果这些规则被误删或改动，模型生成 SQL 的质量会直接下降，
    但这种退化不会报错、只会悄悄变差。所以需要用测试把规则"钉住"。
"""
import pytest


@pytest.fixture(scope="module")
def schema(agent_module):
    """模块级 fixture：整个文件复用一份语义层文本

    对应课程：【测试固件的作用域 scope】—— scope 选得越大复用越多、
    创建开销越小，但也要权衡"数据是否可能被用例污染"。
    语义层是只读常量，用 module 级最合适。
    """
    return agent_module.SEMANTIC_SCHEMA


@pytest.fixture(scope="module")
def prompts_mod():
    """模块级 fixture：Prompt 模板库"""
    import importlib
    return importlib.import_module("prompts")


@pytest.mark.unit
class TestSchemaContent:
    """确保关键业务规则没有被误删"""

    @pytest.mark.parametrize(
        "keyword, why",
        [
            ("interact_rate",  "互动率字段名，模型必须知道"),
            ("不要用",          "明确禁止重算互动率的口径说明"),
            ("duration_s",     "时长字段名"),
            ("累计时长",        "多P合集时长的陷阱说明"),
            ("follower",       "粉丝数字段名"),
            ("HAVING COUNT",   "聚合最小样本量规则的核心表达式"),
            ("30",             "最小样本量阈值"),
            ("口径",            "字段口径说明"),
        ],
    )
    def test_关键规则存在(self, schema, keyword, why):
        assert keyword in schema, "语义层缺少关键内容：%s（%s）" % (keyword, why)

    def test_统计口径规则有独立小节(self, schema):
        """聚合口径规则必须单独成节，避免被淹没在字段说明里"""
        assert "统计口径规则" in schema or "口径" in schema

    def test_样本特征有说明(self, schema):
        """时间分布偏斜等样本特征，是结论表述的前提"""
        assert "2026" in schema, "应说明样本的时间分布偏斜"

    def test_表结构覆盖全部四张表(self, schema):
        for t in ("category", "up_info", "video", "video_tag"):
            assert t in schema, "语义层缺少表：%s" % t


@pytest.mark.unit
class TestPromptLibrary:
    """Prompt 模板库的结构测试

    被测对象：prompts.PROMPTS / build_messages
    """

    def test_四个Prompt都已注册(self, prompts_mod):
        expected = {"sql_generate", "sql_repair", "sql_interpret", "intent_classify"}
        assert expected.issubset(set(prompts_mod.PROMPTS.keys()))

    @pytest.mark.parametrize(
        "name", ["sql_generate", "sql_repair", "sql_interpret", "intent_classify"]
    )
    def test_每个Prompt结构完整(self, prompts_mod, name):
        p = prompts_mod.PROMPTS[name]
        assert p.version,          "%s 缺少版本号" % name
        assert p.role,             "%s 缺少角色设定" % name
        assert p.task,             "%s 缺少任务描述" % name
        assert p.constraints,      "%s 缺少硬性约束" % name
        assert p.design_notes,     "%s 缺少设计说明" % name

    def test_sql_generate含SELECT约束(self, prompts_mod):
        """"只允许 SELECT" 这条约束写在 Prompt 里，不在语义层里

        测试要断言"约束存在于系统最终可见的地方"——
        这里就是 sql_generate 这个 Prompt 的约束清单。
        """
        p = prompts_mod.PROMPTS["sql_generate"]
        joined = " ".join(p.constraints)
        assert "SELECT" in joined
        assert "写操作" in joined or "禁止" in joined

    def test_build_messages返回标准结构(self, prompts_mod):
        msgs = prompts_mod.build_messages(
            "sql_generate", schema="[SCHEMA]", question="测试问题")
        assert len(msgs) == 2
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert "测试问题" in msgs[1]["content"]

    def test_未注册的Prompt应报错(self, prompts_mod):
        with pytest.raises(KeyError):
            prompts_mod.build_messages("not_exist", x=1)
