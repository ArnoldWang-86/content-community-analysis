# pytest 测试套件 · AI 数据 Agent

> 为 Text-to-SQL 数据 Agent 编写的测试套件。
> **用途**：把车载测试课程里学的 pytest 用在真实项目上，产出一个可展示的成果。

---

## 快速开始

```bash
cd 项目-内容社区分析
pip install -r tests/requirements-dev.txt

pytest                          # 跑全部测试（读 pytest.ini 里的配置）
pytest -m unit                  # 只跑单元测试（不依赖数据库）
pytest -m db                    # 只跑需要 MySQL 的测试
pytest tests/test_sql_guard.py  # 只跑某个文件
pytest -k "写操作"               # 按用例名筛选

# 生成 HTML 报告（对应课程：pytest-html）
pytest --html=tests/report.html --self-contained-html
```

**当前结果**：`61 passed, 2 xfailed`

---

## 文件结构

```
tests/
├── conftest.py                 fixture 集中管理（数据库连接 / 被测对象 / Excel 用例）
├── pytest.ini                  配置：默认参数、marker 注册、testpaths
├── requirements-dev.txt        测试依赖
├── data/
│   └── dangerous_sql.xlsx      24 条危险 SQL 用例数据（数据与代码分离）
├── _make_testdata.py           重新生成 Excel 用例数据（可选）
├── test_sql_guard.py           SQL 安全闸门测试        ← 核心
├── test_fault_injection.py     故障注入器与容错测试
├── test_schema_rules.py        语义层规则 + Prompt 库结构
└── test_db_execution.py        数据库执行测试（需要 MySQL）
```

---

## ⭐ 学习路线图：课程学到哪，就往哪加东西

这张表把**你的视频课程**和**这套测试**一一对应，边学边练：

| 课程章节 | 学完后可以做的事 | 改哪个文件 |
|---|---|---|
| pytest 初体验 | 读懂现有测试怎么组织 | 通读 `test_sql_guard.py` |
| **用例的组织方式** | 理解为什么按"被测对象"分文件 | 对比四个 test 文件的分工 |
| **执行命令的选项** | 练习 `-k` / `-m` / `-v` / `-x` 筛选与调试 | `pytest.ini` |
| **从 python 代码中执行测试** | 用 `pytest.main()` 写一个"一键跑测试"的入口 | 新建 `tests/run_tests.py` |
| **测试固件使用场景** | 理解为什么数据库连接要做成 fixture | `conftest.py` 的 `db_conn` |
| **固件的定义和三种使用方式** | 把固件从"函数参数"改写成 autouse / usefixtures 对比效果 | `conftest.py` |
| **在测试函数中使用固件返回值** | 观察 `sql_guard` / `dangerous_cases` 怎么被注入 | `test_sql_guard.py` |
| **用 yield 实现收尾** | 看 `db_conn` 里 yield 前后的"创建 / 清理" | `conftest.py` |
| **conftest.py 定义固件** | 试着把某个 fixture 挪进 test 文件，观察能否跨文件使用 | `conftest.py` |
| **固件的作用域 scope** | 把 `db_conn` 从 session 改成 function，对比耗时 | `conftest.py` |
| **参数化的使用场景和基本实现** | 给 `test_sql_guard.py` 补新的危险 SQL 参数组 | `test_sql_guard.py` |
| **每组参数化有多个值** | 观察 `(sql, keyword)` 元组参数的用法 | `test_sql_guard.py` |
| **用例数据分离 - python** | 把用例从 parametrize 挪到 yaml 文件读入 | 新建 `data/cases.yaml` |
| **用例数据分离 - excel** | 读 `dangerous_cases` fixture，给 Excel 补用例 | `data/dangerous_sql.xlsx` |
| **pytest-html 简介与安装** | 生成第一份报告 | 命令行 `--html` |
| **pytest-html 详细用法** | 给报告加标题、环境信息、失败截图 | `pytest.ini` |

---

## 三个测试发现（写测试的真实价值）

这套测试在编写过程中真的发现了问题，都可以拿来在面试里讲：

### ① 安全校验有两道防线，但第一道会掩盖第二道

`validate_sql` 先判断"是否以 SELECT / WITH 开头"，再扫描写操作关键字。
结果是 `DROP TABLE video` 这类语句在第一道就被拦下，报的是"只允许 SELECT 查询"，
**第二道防线（关键字扫描）因此在测试里被掩盖了** —— 万一它被误删，测试仍然全绿。

**处理**：专门构造一条"能过第一道、必须被第二道拦下"的语句来覆盖它。

> 面试可讲：**"测试覆盖率不等于测试有效性 —— 如果断言写得不好，代码删了测试也不会红。"**

### ② 约束写在哪，就该测在哪

最初我把"只允许 SELECT"这条约束放到语义层（`SEMANTIC_SCHEMA`）的测试里，
测试失败 —— 因为它其实写在 Prompt 的约束清单里。

**处理**：把断言移到 `PROMPTS["sql_generate"].constraints`。

> 面试可讲：**"测试要断言『约束存在于系统最终可见的位置』，而不是断言『我记得它应该在哪』。"**

### ③ 发现一个真实缺陷：字符串字面量被误判

```sql
SELECT 'drop' AS word                    -- 应放行，实际被拦
SELECT title FROM video WHERE title LIKE '%delete%'   -- 应放行，实际被拦
```

当前用正则 `\bdrop\b` 匹配关键字，**无法区分"代码里的关键字"和"字符串字面量里的关键字"**。

**处理**：没有删掉这个用例，而是用 `@pytest.mark.xfail` 标记为已知缺陷，
并在 docstring 里写明改进方向（先剥离字符串字面量和注释，或改用 sqlparse 做语法树级校验）。

> 面试可讲：**"我用 xfail 而不是删除来记录已知缺陷 —— 缺陷不会随迭代被遗忘，修好后 xpass 会提示这个用例可以转正了。"**

---

## 设计说明

### 为什么安全校验是测试优先级最高的目标

模型生成什么 SQL 不由我们控制，但**执行什么由 `validate_sql` 这道闸门决定**。
它一旦失效，模型的幻觉可能直接变成对数据库的破坏性操作。

所以它被覆盖得最厚：24 条 Excel 用例 + 参数化覆盖 9 类写操作 + 大小写变体 + 多语句堆叠 + 边界输入。

### 为什么要测"故障注入器本身"

错误自愈是 Agent 的核心能力，但**简单问题上模型一次就写对，自愈逻辑永远不会被触发**，
它的正确性也就无从验证 —— 所以需要注入器主动制造失败。

但这也带来一个风险：**如果注入器没把 SQL 真的改坏，"自愈成功"的结论就是假的**。
所以 `test_fault_injection.py` 里先用元测试证明注入器确实改变了 SQL，
再验证注入后的语句仍能通过安全校验（否则测的就变成安全闸门了）。

> 面试可讲：**"测试容错逻辑之前，先测试『制造故障的工具』本身有没有效 —— 否则测出来的是假阳性。"**

### 为什么数据库用例要 skip 而不是 fail

`conftest.py` 里的 `db_available` 会探测 MySQL 是否可达；
不可达时依赖数据库的用例被 `skip` 而非 `fail`。

这样在没有数据库的机器上（如 CI 默认 runner）依然能跑通其余 50 多个测试，
而不会因为环境缺失导致整条流水线红灯。
