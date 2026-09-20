-- ============================================================================
-- 02_analysis.sql —— 内容社区视频表现分析（12 条）
-- ============================================================================
-- 数据源：content_analysis 库（video / up_info / video_tag / category）
-- 数据规模：19,456 条真实视频（清洗后）
--
-- 组织逻辑（按分析链条，不是随便列）：
-- 第 1 层 建基座 Q1 明细宽表，后面全部基于它
-- 第 2 层 挖洞察 Q2 ~ Q8 各维度 × 互动率，回答"什么内容表现好"
-- 第 3 层 做归因 Q9 ~ Q11 趋势 + 贡献度 + 画像，回答"为什么"
-- 第 4 层 保可信 Q12 数据质量校验，证明结论站得住
--
-- 注意：已知抽样特征：67% 的视频发布于 2026 年（B站搜索排序偏向新内容），
-- 因此不做"年度趋势"类结论，只做「时段/分区/长度/粉丝量级」等
-- 与年份无关的横截面分析。Q9 的月度趋势仅用于演示方法。
-- ============================================================================

USE content_analysis;


-- ============================================================================
-- Q1 【基座】视频粒度分析宽表（4 表 JOIN）
-- ----------------------------------------------------------------------------
-- 业务问题：把分散在 4 张表里的信息拼成一张可分析的宽表——
-- 每条视频带上它的分区名、UP主名与粉丝数、以及各项衍生指标。
-- SQL 要点：4 表 INNER JOIN + 派生指标计算
-- 用途： 后续所有分析的基座；也可直接在 Tableau 里连这张视图
-- ============================================================================
SELECT
    v.bvid,
    v.title,
    c.tname AS 分区,
    u.uname AS up主,
    u.follower AS 粉丝数,
    v.pubdate,
    v.pub_hour AS 发布小时,
    v.pub_weekday AS 发布星期, -- 0=周一
    v.duration_s,
    v.view_cnt AS 播放量,
    v.like_cnt, v.coin_cnt, v.fav_cnt, v.share_cnt,
    v.danmaku_cnt, v.reply_cnt,
    -- 衍生指标：各互动行为的比率
    ROUND(v.like_cnt / v.view_cnt, 6) AS 点赞率,
    ROUND(v.coin_cnt / v.view_cnt, 6) AS 投币率,
    ROUND(v.fav_cnt / v.view_cnt, 6) AS 收藏率,
    ROUND(v.danmaku_cnt / v.view_cnt * 1000, 4) AS 弹幕密度_千播,
    ROUND(v.fav_cnt / NULLIF(v.like_cnt, 0), 4) AS 内容沉淀比, -- 收藏/点赞：越高说明越"有用"
    v.interact_rate AS 互动率,
    IF(v.duration_s > 14400, 1, 0) AS 是否多P合集
FROM video v
JOIN category c ON v.tid = c.tid
JOIN up_info u ON v.up_mid = u.mid
ORDER BY v.view_cnt DESC
LIMIT 100;


-- ============================================================================
-- Q2 【洞察】各分区播放量 Top-10
-- ----------------------------------------------------------------------------
-- 业务问题：每个内容分区的头部内容长什么样？头部和其它内容差多少量级？
-- SQL 要点：ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...)
-- 这是"分组取 TopN"的标准解法，比 GROUP BY + 自连接高效得多
-- ============================================================================
WITH ranked AS (
    SELECT
        c.tname AS 分区,
        v.title,
        u.uname AS up主,
        u.follower AS 粉丝数,
        v.view_cnt AS 播放量,
        ROW_NUMBER() OVER (PARTITION BY v.tid ORDER BY v.view_cnt DESC) AS 分区内排名
    FROM video v
    JOIN category c ON v.tid = c.tid
    JOIN up_info u ON v.up_mid = u.mid
)
SELECT * FROM ranked
WHERE 分区内排名 <= 10
ORDER BY 播放量 DESC
LIMIT 60;


-- ============================================================================
-- Q3 【洞察】各分区互动率排名与四分位
-- ----------------------------------------------------------------------------
-- 业务问题：哪些分区的内容"质量"更高（互动率而非绝对播放量）？
-- 某条视频在它所在分区里处于什么水平？
-- SQL 要点：PERCENT_RANK() 算百分位排名 + NTILE(4) 分四档
-- PERCENT_RANK 用来看"排在前百分之几"，NTILE 用来分箱对比
-- ============================================================================
WITH stat AS (
    SELECT
        c.tname AS 分区,
        v.bvid,
        v.title,
        v.view_cnt AS 播放量,
        v.interact_rate AS 互动率,
        PERCENT_RANK() OVER (PARTITION BY v.tid ORDER BY v.interact_rate DESC) AS 互动率百分位,
        NTILE(4) OVER (PARTITION BY v.tid ORDER BY v.interact_rate DESC) AS 互动率四分档
    FROM video v
    JOIN category c ON v.tid = c.tid
)
SELECT
    分区,
    COUNT(*) AS 视频数,
    ROUND(AVG(互动率), 4) AS 平均互动率,
    ROUND(AVG(播放量), 0) AS 平均播放量,
    -- 头部 10% 的互动率 vs 尾部 25%，看差距有多大
    ROUND(AVG(CASE WHEN 互动率百分位 <= 0.10 THEN 互动率 END), 4) AS 头部10pct互动率,
    ROUND(AVG(CASE WHEN 互动率百分位 >= 0.75 THEN 互动率 END), 4) AS 尾部25pct互动率
FROM stat
GROUP BY 分区
HAVING COUNT(*) >= 100 -- 样本太小的分区不参与对比
ORDER BY 平均互动率 DESC;


-- ============================================================================
-- Q4 【洞察】爆款口径定义：同分区互动率 Top 5%
-- ----------------------------------------------------------------------------
-- 业务问题：怎么定义"爆款"？
-- 不用绝对播放量（各分区量级差异太大），而是"在所属分区里排进前5%"。
-- SQL 要点：NTILE(20) 把每个分区的视频按互动率切成 20 等份，第 1 份就是 Top 5%
-- 这个口径会成为 Q11 建模的目标变量（is_hit）
-- ============================================================================
WITH hit AS (
    SELECT
        v.bvid, v.title, v.tid, v.interact_rate, v.view_cnt,
        NTILE(20) OVER (PARTITION BY v.tid ORDER BY v.interact_rate DESC) AS 档位
    FROM video v
)
SELECT
    CASE WHEN 档位 = 1 THEN '爆款(同分区Top5%)' ELSE '普通' END AS 类别,
    COUNT(*) AS 视频数,
    ROUND(AVG(interact_rate), 4) AS 平均互动率,
    ROUND(AVG(view_cnt), 0) AS 平均播放量,
    ROUND(MIN(interact_rate), 4) AS 互动率下界,
    ROUND(MAX(interact_rate), 4) AS 互动率上界
FROM hit
GROUP BY 类别;


-- ============================================================================
-- Q5 【洞察·重点】 发布时段 × 互动率
-- ----------------------------------------------------------------------------
-- 业务问题：什么时间发布的内容表现更好？"晚间黄金档"是真的吗？
-- SQL 要点：CASE WHEN 把 0-23 小时分桶 + 分组聚合
-- 后续： 这份结果会交给 07_stats_model.py 做 Welch t 检验，
-- 算出"晚间 vs 凌晨"的差异百分比和 Cohen's d 效应量
-- ============================================================================
SELECT
    CASE
        WHEN pub_hour BETWEEN 0 AND 5 THEN '1_凌晨(0-5)'
        WHEN pub_hour BETWEEN 6 AND 8 THEN '2_早间(6-8)'
        WHEN pub_hour BETWEEN 9 AND 11 THEN '3_上午(9-11)'
        WHEN pub_hour BETWEEN 12 AND 13 THEN '4_午间(12-13)'
        WHEN pub_hour BETWEEN 14 AND 17 THEN '5_下午(14-17)'
        WHEN pub_hour BETWEEN 18 AND 22 THEN '6_晚间(18-22)'
        ELSE '7_深夜(23)'
    END AS 发布时段,
    COUNT(*) AS 视频数,
    ROUND(AVG(interact_rate), 4) AS 平均互动率,
    ROUND(AVG(view_cnt), 0) AS 平均播放量,
    ROUND(AVG(like_cnt / view_cnt), 4) AS 平均点赞率,
    ROUND(AVG(fav_cnt / view_cnt), 4) AS 平均收藏率
FROM video
GROUP BY 发布时段
ORDER BY 发布时段;


-- ============================================================================
-- Q6 【洞察】UP主粉丝量级 × 互动率
-- ----------------------------------------------------------------------------
-- 业务问题：粉丝越多，互动率越高吗？还是存在"边际递减"？
-- （小账号粉丝少但黏性高，大账号播放高但互动率低——这是常见现象）
-- SQL 要点：CASE 分箱 + JOIN up_info + 分组聚合
-- 注意： 约 2 条 UP 主粉丝数为 NULL（采集失败率 0.02%），已用 IFNULL 兜底
-- ============================================================================
SELECT
    CASE
        WHEN IFNULL(u.follower, 0) < 1000 THEN '1_<1千'
        WHEN u.follower < 10000 THEN '2_1千-1万'
        WHEN u.follower < 100000 THEN '3_1万-10万'
        WHEN u.follower < 1000000 THEN '4_10万-100万'
        ELSE '5_>100万'
    END AS 粉丝量级,
    COUNT(*) AS 视频数,
    COUNT(DISTINCT v.up_mid) AS UP主数,
    ROUND(AVG(v.interact_rate), 4) AS 平均互动率,
    ROUND(AVG(v.view_cnt), 0) AS 平均播放量,
    ROUND(AVG(u.follower), 0) AS 平均粉丝数
FROM video v
JOIN up_info u ON v.up_mid = u.mid
GROUP BY 粉丝量级
ORDER BY 粉丝量级;


-- ============================================================================
-- Q7 【洞察】内容标签 × 互动率（一对多表 JOIN）
-- ----------------------------------------------------------------------------
-- 业务问题：哪些话题标签下的内容更容易获得高互动？
-- SQL 要点：video_tag 是"视频-标签"的一对多表，必须先 JOIN 展开再聚合，
-- 否则一条多标签视频会被重复计入。这是最典型的多对多分析场景。
-- ============================================================================
SELECT
    t.tag AS 标签,
    COUNT(*) AS 视频数,
    COUNT(DISTINCT t.bvid) AS 去重视频数,
    ROUND(AVG(v.interact_rate), 4) AS 平均互动率,
    ROUND(AVG(v.view_cnt), 0) AS 平均播放量
FROM video_tag t
JOIN video v ON t.bvid = v.bvid
GROUP BY t.tag
HAVING COUNT(DISTINCT t.bvid) >= 30 -- 只保留出现 >=30 次的标签，避免长尾噪声
ORDER BY 平均互动率 DESC
LIMIT 30;


-- ============================================================================
-- Q8 【洞察】视频时长 × 互动率（含"多P合集"治理）
-- ----------------------------------------------------------------------------
-- 业务问题：长视频和短视频，哪种互动更好？
-- 注意：数据陷阱：B站对"多P合集"返回的是**所有分P的累计时长**（最长 817 小时），
-- 占样本 21.7%。若不单独处理，这 4223 条会把回归结果彻底带偏。
-- 做法： 先用 is_collection 分开，再各自分箱，避免量纲混淆
-- ============================================================================
SELECT
    IF(duration_s > 14400, 'B_多P合集(>4小时)', 'A_单集视频') AS 视频类型,
    CASE
        WHEN duration_s <= 60 THEN '1_≤1分钟'
        WHEN duration_s <= 300 THEN '2_1-5分钟'
        WHEN duration_s <= 900 THEN '3_5-15分钟'
        WHEN duration_s <= 3600 THEN '4_15-60分钟'
        WHEN duration_s <= 14400 THEN '5_1-4小时'
        ELSE '6_>4小时(合集)'
    END AS 时长区间,
    COUNT(*) AS 视频数,
    ROUND(AVG(interact_rate), 4) AS 平均互动率,
    ROUND(AVG(view_cnt), 0) AS 平均播放量,
    ROUND(AVG(fav_cnt / view_cnt), 4) AS 平均收藏率
FROM video
GROUP BY 视频类型, 时长区间
ORDER BY 视频类型, 时长区间;


-- ============================================================================
-- Q9 【归因】分区月度汇总 + 环比（CTE + LAG）
-- ----------------------------------------------------------------------------
-- 业务问题：各分区的月度表现如何变化？本月比上月涨了还是跌了？
-- SQL 要点：CTE 先算月度汇总，再用 LAG() OVER (PARTITION BY 分区 ORDER BY 月)
-- 取上一期的值算环比 —— 这是异动监控看板的核心 SQL 模式
-- 注意：注意：样本 67% 集中在 2026 年，此处仅演示方法，趋势结论需谨慎
-- ============================================================================
WITH monthly AS (
    SELECT
        c.tname AS 分区,
        DATE_FORMAT(v.pubdate, '%Y-%m') AS 月份,
        COUNT(*) AS 视频数,
        ROUND(AVG(v.interact_rate), 4) AS 平均互动率,
        SUM(v.view_cnt) AS 总播放量
    FROM video v
    JOIN category c ON v.tid = c.tid
    WHERE v.pubdate >= '2026-01-01'
    GROUP BY 分区, 月份
    HAVING COUNT(*) >= 20
)
SELECT
    分区,
    月份,
    视频数,
    平均互动率,
    LAG(平均互动率) OVER (PARTITION BY 分区 ORDER BY 月份) AS 上月互动率,
    ROUND(平均互动率
          - LAG(平均互动率) OVER (PARTITION BY 分区 ORDER BY 月份), 4) AS 环比变化,
    ROUND(100.0 * (平均互动率
          - LAG(平均互动率) OVER (PARTITION BY 分区 ORDER BY 月份))
          / NULLIF(LAG(平均互动率) OVER (PARTITION BY 分区 ORDER BY 月份), 0), 2) AS 环比变化率_百分比
FROM monthly
ORDER BY 分区, 月份;


-- ============================================================================
-- Q10 【归因】分区对整体互动率的贡献度拆解
-- ----------------------------------------------------------------------------
-- 业务问题：整体互动率发生变动时，是哪个分区贡献的？
-- SQL 要点：经典"量价拆解"——把总体均值拆成 Σ(分区占比 × 分区均值)
-- 再用占比变化 × 均值变化，量化每个分区对整体变动的贡献
-- 用途： 异动归因的标准方法论，面试高频考点
-- ============================================================================
WITH seg AS (
    SELECT
        c.tname AS 分区,
        COUNT(*) AS 视频数,
        AVG(v.interact_rate) AS 分区互动率
    FROM video v
    JOIN category c ON v.tid = c.tid
    GROUP BY 分区
),
tot AS (SELECT SUM(视频数) AS 总数 FROM seg)
SELECT
    分区,
    视频数,
    ROUND(100.0 * 视频数 / (SELECT 总数 FROM tot), 2) AS 样本占比_百分比,
    ROUND(分区互动率, 4) AS 分区平均互动率,
    -- 贡献度 = 该分区对整体均值的拉动量
    ROUND(100.0 * 视频数 / (SELECT 总数 FROM tot) * 分区互动率, 4) AS 对整体互动率的贡献,
    -- 整体互动率 = Σ(分区视频数 × 分区互动率) / 总视频数
    ROUND((SELECT SUM(s.视频数 * s.分区互动率) / (SELECT 总数 FROM tot)
           FROM seg s), 4) AS 整体互动率_加权
FROM seg
ORDER BY 对整体互动率的贡献 DESC
LIMIT 20;


-- ============================================================================
-- Q11 【画像】爆款内容的共性特征
-- ----------------------------------------------------------------------------
-- 业务问题：爆款视频在"发布时段 / 时长 / 粉丝量级"上有什么共同点？
-- SQL 要点：用 Q4 的爆款口径（同分区 Top 5%）作为筛选条件，
-- 多维度 CROSS JOIN 式对比 —— 爆款组 vs 普通组的分布差异
-- 用途： 直接产出可落地的内容策略建议；也是 07_stats_model.py 建模前的探索
-- ============================================================================
WITH hit AS (
    SELECT
        v.bvid, v.pub_hour, v.duration_s, v.up_mid, v.view_cnt,
        NTILE(20) OVER (PARTITION BY v.tid ORDER BY v.interact_rate DESC) AS 档位
    FROM video v
)
SELECT
    CASE WHEN h.档位 = 1 THEN '爆款' ELSE '普通' END AS 组别,
    COUNT(*) AS 视频数,
    ROUND(AVG(h.pub_hour), 2) AS 平均发布小时,
    ROUND(100.0 * SUM(h.pub_hour BETWEEN 18 AND 22) / COUNT(*), 1) AS 晚间发布占比_百分比,
    ROUND(AVG(h.duration_s) / 60, 1) AS 平均时长_分钟,
    ROUND(100.0 * SUM(h.duration_s > 14400) / COUNT(*), 1) AS 多P合集占比_百分比,
    ROUND(AVG(u.follower), 0) AS 平均粉丝数,
    ROUND(100.0 * SUM(IFNULL(u.follower,0) < 10000) / COUNT(*), 1) AS 万粉以下占比_百分比
FROM hit h
JOIN up_info u ON h.up_mid = u.mid
GROUP BY 组别;


-- ============================================================================
-- Q12 【校验】数据质量五项检查
-- ----------------------------------------------------------------------------
-- 业务问题：我凭什么相信上面的结论？——先证明数据本身是干净的
-- SQL 要点：主键唯一性 / 外键完整性 / 空值率 / 数值合理性 / 分布合理性
-- 面试价值：能主动做数据质量校验，是数据分析岗的重要加分项
-- ============================================================================
SELECT '1.主键唯一性(应=总数)' AS 检查项,
       CONCAT(COUNT(DISTINCT bvid), ' / ', COUNT(*)) AS 结果,
       IF(COUNT(DISTINCT bvid) = COUNT(*), 'PASS', 'FAIL') AS 判定
FROM video

UNION ALL
SELECT '2.孤儿视频(无对应UP主)',
       CAST((SELECT COUNT(*) FROM video v
             LEFT JOIN up_info u ON v.up_mid = u.mid WHERE u.mid IS NULL) AS CHAR),
       IF((SELECT COUNT(*) FROM video v
           LEFT JOIN up_info u ON v.up_mid = u.mid WHERE u.mid IS NULL) = 0, 'PASS', 'FAIL')

UNION ALL
SELECT '3.孤儿标签(无对应视频)',
       CAST((SELECT COUNT(*) FROM video_tag t
             LEFT JOIN video v ON t.bvid = v.bvid WHERE v.bvid IS NULL) AS CHAR),
       IF((SELECT COUNT(*) FROM video_tag t
           LEFT JOIN video v ON t.bvid = v.bvid WHERE v.bvid IS NULL) = 0, 'PASS', 'FAIL')

UNION ALL
SELECT '4.关键字段空值(时间/播放/互动率)',
       CAST((SELECT COUNT(*) FROM video
             WHERE pubdate IS NULL OR view_cnt IS NULL OR interact_rate IS NULL) AS CHAR),
       IF((SELECT COUNT(*) FROM video
           WHERE pubdate IS NULL OR view_cnt IS NULL OR interact_rate IS NULL) = 0, 'PASS', 'FAIL')

UNION ALL
SELECT '5.互动率越界(>1 或 <0)',
       CAST((SELECT COUNT(*) FROM video WHERE interact_rate > 1 OR interact_rate < 0) AS CHAR),
       IF((SELECT COUNT(*) FROM video WHERE interact_rate > 1 OR interact_rate < 0) = 0, 'PASS', 'FAIL')

UNION ALL
SELECT '6.播放量<100的残留(应为0)',
       CAST((SELECT COUNT(*) FROM video WHERE view_cnt < 100) AS CHAR),
       IF((SELECT COUNT(*) FROM video WHERE view_cnt < 100) = 0, 'PASS', 'FAIL');
