-- 01_schema.sql —— 内容社区分析库表结构
-- 执行: mysql -u root -p content_analysis < sql/01_schema.sql

DROP TABLE IF EXISTS video_tag;
DROP TABLE IF EXISTS video;
DROP TABLE IF EXISTS up_info;
DROP TABLE IF EXISTS category;

-- 分区维表
CREATE TABLE category (
  tid INT PRIMARY KEY,
  tname VARCHAR(64) NOT NULL,
  parent_tid INT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- UP主表
CREATE TABLE up_info (
  mid BIGINT PRIMARY KEY,
  uname VARCHAR(128),
  follower BIGINT NULL,
  UNIQUE KEY uk_mid (mid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 视频主表
CREATE TABLE video (
  -- 注意：bvid 必须用 utf8mb4_bin（区分大小写）
  -- B站 bvid 是 Base58 编码，大小写敏感（如 BV1Rqen6TEmU 与 BV1Rqen6TEmu 是两个不同视频）。
  -- 若用默认的 utf8mb4_unicode_ci（ci = case insensitive），这两条会主键冲突。
  bvid VARCHAR(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin PRIMARY KEY,
  aid BIGINT,
  title VARCHAR(512),
  description TEXT,
  pubdate DATETIME,
  pub_hour TINYINT,
  pub_weekday TINYINT,
  duration_s INT,
  tid INT,
  up_mid BIGINT,
  view_cnt BIGINT,
  like_cnt BIGINT,
  coin_cnt BIGINT NULL,
  fav_cnt BIGINT,
  share_cnt BIGINT NULL,
  danmaku_cnt BIGINT,
  reply_cnt BIGINT,
  cid BIGINT NULL,
  interact_rate DECIMAL(10,6) NULL,
  KEY idx_tid (tid),
  KEY idx_pub (pubdate),
  KEY idx_up (up_mid),
  CONSTRAINT fk_video_cat FOREIGN KEY (tid) REFERENCES category(tid),
  CONSTRAINT fk_video_up FOREIGN KEY (up_mid) REFERENCES up_info(mid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 视频-标签（一对多，JOIN 练习点）
CREATE TABLE video_tag (
  -- 外键列必须与被引用列 collation 完全一致，否则建外键会失败
  bvid VARCHAR(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
  tag VARCHAR(128),
  PRIMARY KEY (bvid, tag(64)),
  KEY idx_tag (tag(64)),
  CONSTRAINT fk_tag_video FOREIGN KEY (bvid) REFERENCES video(bvid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
