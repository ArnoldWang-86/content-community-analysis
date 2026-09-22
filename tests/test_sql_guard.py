# -*- coding: utf-8 -*-
"""test_sql_guard.py —— SQL 安全闸门测试

被测对象：DataAgent.validate_sql(sql)

为什么它是最该测的：
    模型生成什么 SQL 不由我们控制，但"执行什么"由这道闸门决定。
    它是整个 Agent 的安全边界，一旦失效，模型幻觉可能直接变成
    对生产库的破坏性操作。

覆盖的课程内容：
    · 参数化            —— @pytest.mark.parametrize 单组数据
    · 参数化多值        —— 用元组传多组数据
    · Excel 用例分离     —— 从 conftest 的 dangerous_cases fixture 读取
    · 固件使用          —— sql_guard / dangerous_cases 均由 fixture 注入
    · 标记（marker）     —— @pytest.mark.unit 在 pytest.ini 中注册
"""
import pytest


@pytest.mark.unit
class TestSqlGuardBasic:
    """基础行为：什么该放行、什么该拦"""

    def test_正常查询应放行(self, sql_guard):
        sql = "SELECT * FROM video LIMIT 10"
        assert sql_guard(sql) == sql

    def test_结尾分号应被容忍(self, sql_guard):
        """合法查询末尾带分号是常见写法，不应因此被拦"""
        assert sql_guard("SELECT 1;") == "SELECT 1"

    def test_首尾空白应被容忍(self, sql_guard):
        assert sql_guard("   SELECT 1   ") == "SELECT 1"

    def test_CTE查询应放行(self, sql_guard):
        sql = "WITH t AS (SELECT 1 AS a) SELECT * FROM t"
        assert sql_guard(sql) == sql


@pytest.mark.unit
class TestSqlGuardBlocking:
    """拦截能力：参数化驱动，一组参数一个用例

    对应课程：【参数化的使用场景和基本实现】
    把多组数据写进 parametrize，pytest 会自动展开成多个独立用例，
    某一组失败不会影响其他组，且失败时能直接看到是哪组数据。
    """

    @pytest.mark.parametrize(
        "sql, keyword",
        [
            ("DROP TABLE video",                        "DROP"),
            ("DELETE FROM video WHERE view_cnt < 100",  "DELETE"),
            ("UPDATE video SET interact_rate = 0",      "UPDATE"),
            ("INSERT INTO video (bvid) VALUES ('x')",   "INSERT"),
            ("TRUNCATE TABLE video",                    "TRUNCATE"),
            ("ALTER TABLE video ADD COLUMN c INT",      "ALTER"),
            ("CREATE TABLE tmp (a INT)",                "CREATE"),
            ("REPLACE INTO video VALUES (1)",           "REPLACE"),
            ("GRANT ALL ON *.* TO 'u'@'%'",             "GRANT"),
        ],
        ids=["drop", "delete", "update", "insert", "truncate",
             "alter", "create", "replace", "grant"],
    )
    def test_写操作应被拦截(self, sql_guard, sql, keyword):
        """每一种写操作都必须被拦下 —— 参数化覆盖全部关键字

        注意断言写法的取舍：
          validate_sql 会先判断"是否以 SELECT / WITH 开头"，因此非法语句
          往往先被这条规则拦下（报"只允许 SELECT 查询"），而不是报出具体的
          写操作关键字。所以这里只断言"被拦截"这个结果，不苛求错误信息措辞——
          测试应该锁定【行为】，而不是锁定【实现细节或文案】。
        """
        with pytest.raises(ValueError) as exc:
            sql_guard(sql)
        msg = str(exc.value)
        assert ("拦截" in msg) or (keyword in msg), "未给出拦截提示：%s" % msg

    def test_关键字检测是第二道防线(self, sql_guard):
        """"以 SELECT 开头"不等于安全 —— 关键字扫描是独立的第二道防线

        为什么需要单独测这条：
          validate_sql 有两道检查。第一道看"是否以 SELECT / WITH 开头"，
          非法语句通常在这里就被拦下了，于是第二道（关键字扫描）很容易
          在测试中被漏掉 —— 万一它被误删，测试仍然是全绿的。
          所以需要构造一条"能通过第一道、但必须被第二道拦下"的语句。

        真实场景对应：模型被 prompt injection 诱导，在合法查询后拼接写操作。
        """
        with pytest.raises(ValueError) as exc:
            sql_guard("SELECT 1 FROM video WHERE 1=1 DELETE FROM video")
        assert "DELETE" in str(exc.value), "应由关键字检测给出具体提示"

    @pytest.mark.parametrize(
        "sql",
        ["dRoP tAbLe video", "DeLeTe FROM video", "uPdAtE video SET a=1"],
        ids=["混合大小写a", "混合大小写b", "混合大小写c"],
    )
    def test_大小写混写仍应被拦截(self, sql_guard, sql):
        """攻击者常靠大小写规避关键字匹配"""
        with pytest.raises(ValueError):
            sql_guard(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1; DROP TABLE video",
            "select 1; drop table video",
            "SELECT * FROM video; DELETE FROM video",
        ],
        ids=["多语句1", "多语句2", "多语句3"],
    )
    def test_多语句应被拦截(self, sql_guard, sql):
        """通过堆叠语句执行写操作是最常见的手法"""
        with pytest.raises(ValueError):
            sql_guard(sql)

    @pytest.mark.parametrize(
        "sql", ["SHOW TABLES", "EXPLAIN SELECT 1", "DESC video"],
        ids=["show", "explain", "desc"],
    )
    def test_非SELECT语句应被拦截(self, sql_guard, sql):
        with pytest.raises(ValueError):
            sql_guard(sql)


@pytest.mark.unit
class TestSqlGuardBoundary:
    """边界值：空、空白、只有关键字

    对应课程：【边界值分析】—— 空值和全空白是最容易被忽略的两类输入
    """

    @pytest.mark.parametrize(
        "sql", ["", "   ", "\n", "\t", ";", "drop"],
        ids=["空字符串", "空格", "换行", "制表符", "仅分号", "仅关键字"],
    )
    def test_无效输入应被拦截(self, sql_guard, sql):
        with pytest.raises(ValueError):
            sql_guard(sql)


@pytest.mark.unit
class TestSqlGuardFromExcel:
    """数据驱动：用例写在 Excel 里，测试逻辑只负责断言

    对应课程：【用例数据和测试函数分离 excel】

    好处：新增用例只需编辑 tests/data/dangerous_sql.xlsx，
         不用改一行测试代码；测试同学也能独立补充用例。
    """

    def test_Excel用例逐条驱动(self, sql_guard, dangerous_cases):
        assert dangerous_cases, "用例数据为空"

        failures = []
        for case in dangerous_cases:
            cid = case["用例ID"]
            sql = case["SQL"] or ""
            should_block = int(case["预期拦截"])
            # 已知误报单独处理（见下一个用例）
            if case["类别"] == "已知误报":
                continue
            try:
                sql_guard(sql)
                blocked = False
                err = ""
            except ValueError as e:
                blocked = True
                err = str(e)
            if blocked != bool(should_block):
                failures.append(
                    "%s | 预期%s 实际%s | SQL=%r | %s"
                    % (cid, "拦截" if should_block else "放行",
                       "拦截" if blocked else "放行", sql[:50], err)
                )

        assert not failures, "以下用例不符合预期：\n" + "\n".join(failures)

    @pytest.mark.xfail(reason="已知缺陷：关键字出现在字符串字面量中会被误拦", strict=False)
    @pytest.mark.parametrize(
        "sql",
        ["SELECT 'drop' AS word",
         "SELECT title FROM video WHERE title LIKE '%delete%'"],
        ids=["字符串字面量", "LIKE模式"],
    )
    def test_已知误报_字符串中的关键字(self, sql_guard, sql):
        """已知缺陷：当前用正则匹配关键字，无法区分"代码"和"字符串字面量"

        用 xfail 标记而不是删掉这个用例，原因是：
          · 缺陷被记录下来，不会随代码迭代被遗忘
          · 将来修好后 xpass 会提示"这个用例可以转正了"
          · strict=False 表示允许它通过（修好后不算失败）

        改进方向：先剥离字符串字面量和注释，再做关键字匹配；
                 或改用 SQL 解析器（如 sqlparse）做语法树级校验。
        """
        sql_guard(sql)      # 期望不抛异常 —— 当前实现会抛，故 xfail
