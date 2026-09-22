# -*- coding: utf-8 -*-
"""conftest.py —— pytest 公共配置与 fixture 集中管理

对应课程：【在 conftest.py 文件中定义固件】【测试固件的作用域 scope】

为什么 fixture 要放 conftest.py：
  pytest 会自动发现同目录及子目录下的 conftest.py，其中定义的 fixture
  无需 import 就能在任何测试文件中直接使用。集中管理能避免重复定义，
  也让"测试依赖了什么"一目了然。
"""
import os
import sys

import pytest

# ── 让 tests/ 能 import 到 src/ 下的模块 ──
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
for p in (SRC, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


# ==============================================================
#  scope="session" —— 整个测试会话只执行一次
#  数据库连接创建开销大，不应该每个用例都建一次
# ==============================================================
@pytest.fixture(scope="session")
def project_root():
    """项目根目录"""
    return ROOT


@pytest.fixture(scope="session")
def data_dir():
    """测试用数据目录（存放 Excel / YAML 用例数据）"""
    return DATA_DIR


@pytest.fixture(scope="session")
def db_available():
    """探测 MySQL 是否可达

    数据库不可用时，依赖它的用例会被 skip 而不是失败 —— 这样在
    没有环境的机器上也能跑通其余测试。
    """
    try:
        from db import query
        query("SELECT 1")
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def db_conn(db_available):
    """数据库连接（session 级，全测试复用）

    对应课程：【测试固件的作用域 scope】
    用 yield 把"创建"和"清理"分开写，无论用例成功还是失败都会执行清理。
    对应课程：【在固件函数中使用 yield 实现收尾工作】
    """
    if not db_available:
        pytest.skip("MySQL 不可达，跳过依赖数据库的用例")
    from db import conn
    c = conn()
    yield c
    c.close()          # ← yield 之后的代码就是"收尾工作"


@pytest.fixture(scope="session")
def agent_module():
    """导入 Agent 模块（不实例化）

    DataAgent.__init__ 会创建 LLM 客户端、需要 API Key，
    而 validate_sql 是 staticmethod，测试它不需要实例。
    """
    import importlib
    return importlib.import_module("13_text_to_sql_agent")


@pytest.fixture(scope="session")
def sql_guard(agent_module):
    """被测目标：SQL 安全校验函数

    这是整个 Agent 的安全闸门 —— 模型生成什么不由我们控制，
    但执行什么由它决定。所以它是测试优先级最高的目标。
    """
    return agent_module.DataAgent.validate_sql


@pytest.fixture(scope="session")
def fault_injectors(agent_module):
    """三个故障注入器，用于验证错误自愈逻辑"""
    return {
        "wrong_column": agent_module.fault_wrong_column,
        "wrong_table": agent_module.fault_wrong_table,
        "syntax_error": agent_module.fault_syntax_error,
    }


@pytest.fixture(scope="session")
def dangerous_cases(data_dir):
    """从 Excel 读入危险 SQL 用例数据

    对应课程：【用例数据和测试函数分离 excel】

    为什么把用例数据放 Excel 而不是写死在代码里：
      测试数据和测试逻辑分离后，新增用例只需改数据文件，不用动代码；
      非技术人员（如测试工程师）也能补充用例。
    """
    import openpyxl
    path = os.path.join(data_dir, "dangerous_sql.xlsx")
    if not os.path.exists(path):
        pytest.skip("用例数据文件不存在: %s" % path)
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    header = rows[0]
    return [dict(zip(header, r)) for r in rows[1:] if r[0]]
