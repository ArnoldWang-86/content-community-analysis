# -*- coding: utf-8 -*-
"""test_db_execution.py —— 数据库执行测试（需要 MySQL）

被测对象：DataAgent.execute_sql(sql)

说明：
    这一组用例依赖真实 MySQL，通过 conftest 里的 db_available fixture
    探测环境；不可达时整组 skip 而不是 fail —— 这样在没有数据库的
    机器上（如 CI 的默认 runner）依然能跑通其余测试。
"""
import pytest


@pytest.mark.db
class TestDbExecution:

    def test_数据库可达(self, db_available):
        """环境自检：数据库不可达时后续用例会 skip，先明确报告一下"""
        if not db_available:
            pytest.skip("MySQL 不可达")

    def test_简单查询(self, db_available, agent_module):
        if not db_available:
            pytest.skip("MySQL 不可达")
        df = agent_module.DataAgent.execute_sql("SELECT COUNT(*) AS n FROM video")
        assert len(df) == 1
        assert int(df.iloc[0]["n"]) > 0

    def test_四张表均可查询(self, db_available, agent_module):
        if not db_available:
            pytest.skip("MySQL 不可达")
        for t in ("video", "category", "up_info", "video_tag"):
            df = agent_module.DataAgent.execute_sql("SELECT COUNT(*) AS n FROM " + t)
            assert int(df.iloc[0]["n"]) > 0, "表 %s 无数据" % t

    def test_非法SQL应抛异常(self, db_available, agent_module):
        """执行层不负责安全校验，非法 SQL 应由数据库报错"""
        if not db_available:
            pytest.skip("MySQL 不可达")
        with pytest.raises(Exception):
            agent_module.DataAgent.execute_sql("SELECT * FROM 不存在的表")

    def test_互动率口径一致(self, db_available, agent_module):
        """回归测试：interact_rate 应等于 (点赞+投币+收藏)/播放量（截尾前）

        这是一条"口径回归"用例 —— 防止后续改动悄悄改了指标定义。
        """
        if not db_available:
            pytest.skip("MySQL 不可达")
        df = agent_module.DataAgent.execute_sql("""
            SELECT interact_rate,
                   (like_cnt + coin_cnt + fav_cnt) / view_cnt AS calc
            FROM video WHERE view_cnt > 0 LIMIT 200
        """)
        diff = (df["interact_rate"].astype(float) - df["calc"].astype(float)).abs()
        # 允许截尾造成的偏差，但不允许整体口径错误
        assert diff.median() < 0.01, "互动率口径可能已被改动"
