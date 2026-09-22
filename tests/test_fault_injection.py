# -*- coding: utf-8 -*-
"""test_fault_injection.py —— 故障注入器与错误自愈测试

被测对象：fault_wrong_column / fault_wrong_table / fault_syntax_error

为什么测它：
    错误自愈是 Agent 与"一次性生成"的分水岭。但简单问题上模型一次就写对，
    自愈逻辑永远不会被触发 —— 于是它的正确性也无法被验证。
    所以需要故障注入器主动制造失败，这正是"测试容错逻辑本身"的做法。

    这个测试确保：注入器真的能把 SQL 改坏（否则自愈测试就是假阳性）。
"""
import pytest


@pytest.mark.unit
class TestFaultInjectors:
    """先验证"注入器本身有效" —— 这是元测试（测试的测试）

    如果注入器没把 SQL 改坏，那么"自愈成功"的结论就是假的。
    所以要先证明：改坏之后的 SQL 确实与原来不同。
    """

    SAMPLE = ("SELECT c.tname, COUNT(*) AS n FROM video v "
              "JOIN category c ON v.tid = c.tid "
              "GROUP BY c.tid HAVING COUNT(*) >= 30")

    @pytest.mark.parametrize("name", ["wrong_column", "wrong_table", "syntax_error"])
    def test_注入器确实改变了SQL(self, fault_injectors, name):
        new_sql, desc = fault_injectors[name](self.SAMPLE)
        assert new_sql != self.SAMPLE, "注入器没有改动 SQL，自愈测试会失去意义"
        assert isinstance(desc, str) and desc, "应返回故障描述"

    def test_字段名幻觉注入(self, fault_injectors):
        """把正确字段改成不存在的字段，模拟模型幻觉"""
        new_sql, desc = fault_injectors["wrong_column"](
            "SELECT interact_rate FROM video")
        assert "interact_rate" not in new_sql
        assert "interaction_rate" in new_sql or "nonexistent" in new_sql
        assert "字段" in desc or "column" in desc.lower()

    def test_表名幻觉注入(self, fault_injectors):
        new_sql, desc = fault_injectors["wrong_table"](
            "SELECT 1 FROM video v JOIN category c ON v.tid = c.tid")
        assert "categorys" in new_sql or "videos" in new_sql
        assert "表名" in desc

    def test_语法错误注入(self, fault_injectors):
        new_sql, desc = fault_injectors["syntax_error"](self.SAMPLE)
        assert "GROP BY" in new_sql or "ORDER" in new_sql
        assert new_sql.lower() != self.SAMPLE.lower()

    def test_注入后仍可被安全校验通过(self, sql_guard, fault_injectors):
        """故障注入只改业务逻辑，不应把 SQL 变成危险语句

        这一点很重要：如果注入器把 SELECT 改成了 DROP，
        那么自愈测试测的就变成"安全闸门"而不是"错误自愈"了。
        """
        for name in ("wrong_column", "wrong_table", "syntax_error"):
            new_sql, _ = fault_injectors[name](self.SAMPLE)
            try:
                sql_guard(new_sql)
            except ValueError as e:
                pytest.fail("注入后的 SQL 不应触发安全拦截：%s | %s" % (name, e))
