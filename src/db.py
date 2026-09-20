# -*- coding: utf-8 -*-
"""db.py —— 项目数据库连接（MySQL 8.4 @ D盘）

连接信息：
    host=127.0.0.1 port=3306 user=root 密码为空 database=content_analysis
"""
import pymysql

DB = dict(host="127.0.0.1", port=3306, user="root", password="",
          database="content_analysis", charset="utf8mb4",
          cursorclass=pymysql.cursors.DictCursor)


def conn():
    return pymysql.connect(**DB)


def query(sql, args=None):
    """执行查询，返回 dict 列表"""
    with conn() as c:
        with c.cursor() as cur:
            cur.execute(sql, args or ())
            return cur.fetchall()


def execute(sql, args=None, many=None):
    """执行写操作；many 为可迭代参数时用 executemany"""
    with conn() as c:
        with c.cursor() as cur:
            if many is not None:
                cur.executemany(sql, many)
            else:
                cur.execute(sql, args or ())
            c.commit()
            return cur.rowcount


def to_df(sql, args=None):
    """查询直接返回 pandas DataFrame

    注意：必须用「普通 Cursor」而不是 DictCursor。
    pd.read_sql 与 pymysql 的 DictCursor 一起使用时会丢失类型信息，
    导致 BIGINT/DECIMAL 列被当成字符串（pandas 3.x 下会直接报
    "unsupported operand type(s) for /: 'str' and 'str'"）。
    """
    import pandas as pd
    import pymysql as _pm
    cfg = dict(DB)
    cfg["cursorclass"] = _pm.cursors.Cursor # 关键：换回普通游标
    c = _pm.connect(**cfg)
    try:
        return pd.read_sql(sql, c, params=args)
    finally:
        c.close()


if __name__ == "__main__":
    r = query("SELECT VERSION() AS v, DATABASE() AS db, @@port AS port")
    print("连接成功:", r)
    print("表:", [list(x.values())[0] for x in query("SHOW TABLES")])
