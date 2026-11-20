-- ============================================================================
-- 05. 复杂 SQL 双实现与 EXPLAIN ANALYZE 对比（每条业务线 >= 3 组）
-- 说明：
--   1. 每组均涉及 >= 3 张表关联与聚合统计，并给出“实现 A（低效反模式）”与“实现 B（索引优化）”；
--   2. 两组实现严格保证查询结果集完全一致，可直接在 MySQL 8.0/9.x 上执行 EXPLAIN ANALYZE 对比耗时与扫描行数。
-- ============================================================================

USE ev_charge_hub;
SET NAMES utf8mb4;

-- ============================================================================
-- 【业务线 1】场站与充电设施复杂查询优化对比（负责人：成员 1 / 组长，共 3 组）
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 第 1 组（4 表关联：operator + station + charging_pile + charging_gun）
-- 业务场景：查询正常合作运营商(ACTIVE)旗下营业中场站(OPEN)已验收(PASSED)桩的
--           装机额定总功率、直流快充总枪数与空闲快充枪数
-- ----------------------------------------------------------------------------

-- [实现 A - 低效]：使用函数破坏枚举/索引条件 + SELECT 标量相关子查询导致反复回表扫描
SELECT
    o.operator_name,
    s.station_id,
    s.station_code,
    s.station_name,
    COALESCE((
        SELECT SUM(p.rated_power)
          FROM charging_pile AS p
         WHERE p.station_id = s.station_id
           AND p.acceptance_status = 'PASSED'
    ), 0.00) AS total_rated_power,
    COALESCE((
        SELECT COUNT(*)
          FROM charging_gun AS g
          INNER JOIN charging_pile AS p ON p.pile_id = g.pile_id
         WHERE p.station_id = s.station_id
           AND p.acceptance_status = 'PASSED'
           AND g.gun_type = 'DC_FAST'
    ), 0) AS dc_fast_total,
    COALESCE((
        SELECT COUNT(*)
          FROM charging_gun AS g
          INNER JOIN charging_pile AS p ON p.pile_id = g.pile_id
         WHERE p.station_id = s.station_id
           AND p.acceptance_status = 'PASSED'
           AND g.gun_type = 'DC_FAST'
           AND g.current_status = 'IDLE'
    ), 0) AS dc_fast_idle
FROM station AS s
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
WHERE UPPER(o.status) = 'ACTIVE'
  AND LOWER(s.station_status) = 'open'
ORDER BY s.station_id ASC;

-- [实现 B - 优化]：去除列上函数以命中 idx_operator_status 与 idx_station_operator_status，
--                 并利用 idx_pile_station_status 对桩、枪分别单次派生聚合后再连接，消除相关子查询
SELECT
    o.operator_name,
    s.station_id,
    s.station_code,
    s.station_name,
    CAST(COALESCE(pa.total_rated_power, 0.00) AS DECIMAL(12, 2)) AS total_rated_power,
    COALESCE(ga.dc_fast_total, 0)                                AS dc_fast_total,
    COALESCE(ga.dc_fast_idle, 0)                                 AS dc_fast_idle
FROM station AS s
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
LEFT JOIN (
    SELECT
        p.station_id,
        SUM(p.rated_power) AS total_rated_power
    FROM charging_pile AS p
    WHERE p.acceptance_status = 'PASSED'
    GROUP BY p.station_id
) AS pa
    ON pa.station_id = s.station_id
LEFT JOIN (
    SELECT
        p.station_id,
        COUNT(*)                                                    AS dc_fast_total,
        SUM(CASE WHEN g.current_status = 'IDLE' THEN 1 ELSE 0 END)  AS dc_fast_idle
    FROM charging_pile AS p
    INNER JOIN charging_gun AS g
        ON g.pile_id = p.pile_id
    WHERE p.acceptance_status = 'PASSED'
      AND g.gun_type = 'DC_FAST'
    GROUP BY p.station_id
) AS ga
    ON ga.station_id = s.station_id
WHERE o.status = 'ACTIVE'
  AND s.station_status = 'OPEN'
ORDER BY s.station_id ASC;

-- EXPLAIN ANALYZE 对比命令（第 1 组：实现 A vs 实现 B）：
EXPLAIN ANALYZE
SELECT
    o.operator_name,
    s.station_id,
    s.station_code,
    s.station_name,
    COALESCE((
        SELECT SUM(p.rated_power)
          FROM charging_pile AS p
         WHERE p.station_id = s.station_id
           AND p.acceptance_status = 'PASSED'
    ), 0.00) AS total_rated_power,
    COALESCE((
        SELECT COUNT(*)
          FROM charging_gun AS g
          INNER JOIN charging_pile AS p ON p.pile_id = g.pile_id
         WHERE p.station_id = s.station_id
           AND p.acceptance_status = 'PASSED'
           AND g.gun_type = 'DC_FAST'
    ), 0) AS dc_fast_total,
    COALESCE((
        SELECT COUNT(*)
          FROM charging_gun AS g
          INNER JOIN charging_pile AS p ON p.pile_id = g.pile_id
         WHERE p.station_id = s.station_id
           AND p.acceptance_status = 'PASSED'
           AND g.gun_type = 'DC_FAST'
           AND g.current_status = 'IDLE'
    ), 0) AS dc_fast_idle
FROM station AS s
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
WHERE UPPER(o.status) = 'ACTIVE'
  AND LOWER(s.station_status) = 'open'
ORDER BY s.station_id ASC;

EXPLAIN ANALYZE
SELECT
    o.operator_name,
    s.station_id,
    s.station_code,
    s.station_name,
    CAST(COALESCE(pa.total_rated_power, 0.00) AS DECIMAL(12, 2)) AS total_rated_power,
    COALESCE(ga.dc_fast_total, 0)                                AS dc_fast_total,
    COALESCE(ga.dc_fast_idle, 0)                                 AS dc_fast_idle
FROM station AS s
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
LEFT JOIN (
    SELECT
        p.station_id,
        SUM(p.rated_power) AS total_rated_power
    FROM charging_pile AS p
    WHERE p.acceptance_status = 'PASSED'
    GROUP BY p.station_id
) AS pa
    ON pa.station_id = s.station_id
LEFT JOIN (
    SELECT
        p.station_id,
        COUNT(*)                                                    AS dc_fast_total,
        SUM(CASE WHEN g.current_status = 'IDLE' THEN 1 ELSE 0 END)  AS dc_fast_idle
    FROM charging_pile AS p
    INNER JOIN charging_gun AS g
        ON g.pile_id = p.pile_id
    WHERE p.acceptance_status = 'PASSED'
      AND g.gun_type = 'DC_FAST'
    GROUP BY p.station_id
) AS ga
    ON ga.station_id = s.station_id
WHERE o.status = 'ACTIVE'
  AND s.station_status = 'OPEN'
ORDER BY s.station_id ASC;


-- ----------------------------------------------------------------------------
-- 第 2 组（4~5 表关联：operator + station + charging_pile + charging_gun + device_status_log）
-- 业务场景：统计近 30 天（含当天起向前 30 日零点至今）各营业场站发生 FAULT / OFFLINE
--           异常状态变更的总次数，以及当前仍处于 PENDING 未闭环的运维待办数
-- ----------------------------------------------------------------------------

-- [实现 A - 低效]：DATE() 包裹 changed_at 使时间索引失效 + 单次多表 OR 关联迫使优化器走全扫描
SELECT
    s.station_id,
    s.station_code,
    s.station_name,
    o.operator_name,
    COUNT(l.log_id)                                                  AS abnormal_event_count,
    SUM(CASE WHEN l.todo_status = 'PENDING' THEN 1 ELSE 0 END)       AS pending_todo_count
FROM device_status_log AS l
LEFT JOIN charging_pile AS p_direct
    ON l.device_type = 'PILE' AND l.pile_id = p_direct.pile_id
LEFT JOIN charging_gun AS g
    ON l.device_type = 'GUN' AND l.gun_id = g.gun_id
LEFT JOIN charging_pile AS p_gun
    ON g.pile_id = p_gun.pile_id
INNER JOIN station AS s
    ON s.station_id = COALESCE(p_direct.station_id, p_gun.station_id)
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
WHERE DATE(l.changed_at) >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
  AND l.new_status IN ('FAULT', 'OFFLINE')
  AND s.station_status = 'OPEN'
GROUP BY s.station_id, s.station_code, s.station_name, o.operator_name
ORDER BY abnormal_event_count DESC, s.station_id ASC;

-- [实现 B - 优化]：将时间筛选转为 SARGable 常量下界 + 用 UNION ALL 拆开桩/枪外键分支，
--                 两条分支均命中时间前导联合索引 idx_status_log_type_time_status (device_type, changed_at, new_status, todo_status)
SELECT
    s.station_id,
    s.station_code,
    s.station_name,
    o.operator_name,
    CAST(SUM(agg.event_cnt) AS SIGNED) AS abnormal_event_count,
    SUM(agg.pending_cnt)               AS pending_todo_count
FROM (
    SELECT
        p.station_id,
        COUNT(*)                                                  AS event_cnt,
        SUM(CASE WHEN l.todo_status = 'PENDING' THEN 1 ELSE 0 END) AS pending_cnt
    FROM device_status_log AS l
    INNER JOIN charging_pile AS p
        ON p.pile_id = l.pile_id
    WHERE l.device_type = 'PILE'
      AND l.changed_at >= CAST(DATE_SUB(CURDATE(), INTERVAL 30 DAY) AS DATETIME)
      AND l.new_status IN ('FAULT', 'OFFLINE')
      AND l.pile_id IS NOT NULL
    GROUP BY p.station_id

    UNION ALL

    SELECT
        p.station_id,
        COUNT(*)                                                  AS event_cnt,
        SUM(CASE WHEN l.todo_status = 'PENDING' THEN 1 ELSE 0 END) AS pending_cnt
    FROM device_status_log AS l
    INNER JOIN charging_gun AS g
        ON g.gun_id = l.gun_id
    INNER JOIN charging_pile AS p
        ON p.pile_id = g.pile_id
    WHERE l.device_type = 'GUN'
      AND l.changed_at >= CAST(DATE_SUB(CURDATE(), INTERVAL 30 DAY) AS DATETIME)
      AND l.new_status IN ('FAULT', 'OFFLINE')
      AND l.gun_id IS NOT NULL
    GROUP BY p.station_id
) AS agg
INNER JOIN station AS s
    ON s.station_id = agg.station_id
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
WHERE s.station_status = 'OPEN'
GROUP BY s.station_id, s.station_code, s.station_name, o.operator_name
ORDER BY abnormal_event_count DESC, s.station_id ASC;

-- EXPLAIN ANALYZE 对比命令（第 2 组：实现 A vs 实现 B）：
EXPLAIN ANALYZE
SELECT
    s.station_id,
    s.station_code,
    s.station_name,
    o.operator_name,
    COUNT(l.log_id)                                                  AS abnormal_event_count,
    SUM(CASE WHEN l.todo_status = 'PENDING' THEN 1 ELSE 0 END)       AS pending_todo_count
FROM device_status_log AS l
LEFT JOIN charging_pile AS p_direct
    ON l.device_type = 'PILE' AND l.pile_id = p_direct.pile_id
LEFT JOIN charging_gun AS g
    ON l.device_type = 'GUN' AND l.gun_id = g.gun_id
LEFT JOIN charging_pile AS p_gun
    ON g.pile_id = p_gun.pile_id
INNER JOIN station AS s
    ON s.station_id = COALESCE(p_direct.station_id, p_gun.station_id)
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
WHERE DATE(l.changed_at) >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
  AND l.new_status IN ('FAULT', 'OFFLINE')
  AND s.station_status = 'OPEN'
GROUP BY s.station_id, s.station_code, s.station_name, o.operator_name
ORDER BY abnormal_event_count DESC, s.station_id ASC;

EXPLAIN ANALYZE
SELECT
    s.station_id,
    s.station_code,
    s.station_name,
    o.operator_name,
    CAST(SUM(agg.event_cnt) AS SIGNED) AS abnormal_event_count,
    SUM(agg.pending_cnt)               AS pending_todo_count
FROM (
    SELECT
        p.station_id,
        COUNT(*)                                                  AS event_cnt,
        SUM(CASE WHEN l.todo_status = 'PENDING' THEN 1 ELSE 0 END) AS pending_cnt
    FROM device_status_log AS l
    INNER JOIN charging_pile AS p
        ON p.pile_id = l.pile_id
    WHERE l.device_type = 'PILE'
      AND l.changed_at >= CAST(DATE_SUB(CURDATE(), INTERVAL 30 DAY) AS DATETIME)
      AND l.new_status IN ('FAULT', 'OFFLINE')
      AND l.pile_id IS NOT NULL
    GROUP BY p.station_id

    UNION ALL

    SELECT
        p.station_id,
        COUNT(*)                                                  AS event_cnt,
        SUM(CASE WHEN l.todo_status = 'PENDING' THEN 1 ELSE 0 END) AS pending_cnt
    FROM device_status_log AS l
    INNER JOIN charging_gun AS g
        ON g.gun_id = l.gun_id
    INNER JOIN charging_pile AS p
        ON p.pile_id = g.pile_id
    WHERE l.device_type = 'GUN'
      AND l.changed_at >= CAST(DATE_SUB(CURDATE(), INTERVAL 30 DAY) AS DATETIME)
      AND l.new_status IN ('FAULT', 'OFFLINE')
      AND l.gun_id IS NOT NULL
    GROUP BY p.station_id
) AS agg
INNER JOIN station AS s
    ON s.station_id = agg.station_id
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
WHERE s.station_status = 'OPEN'
GROUP BY s.station_id, s.station_code, s.station_name, o.operator_name
ORDER BY abnormal_event_count DESC, s.station_id ASC;


-- ----------------------------------------------------------------------------
-- 第 3 组（4 表关联：operator + station + charging_pile + charging_gun）
-- 业务场景：检索营业中(OPEN)且已验收(PASSED)但已离线或心跳超时的充电桩及受影响枪明细
-- ----------------------------------------------------------------------------

-- [实现 A - 低效]：使用 TIMESTAMPDIFF() 包裹 last_heartbeat_at，无法利用联合索引 idx_pile_heartbeat_status
SELECT
    o.operator_name,
    s.station_code,
    s.station_name,
    p.pile_id,
    p.pile_code,
    p.current_status AS pile_status,
    p.last_heartbeat_at,
    p.offline_threshold_sec,
    g.gun_id,
    g.gun_code,
    g.current_status AS gun_status
FROM charging_pile AS p
INNER JOIN station AS s
    ON s.station_id = p.station_id
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
LEFT JOIN charging_gun AS g
    ON g.pile_id = p.pile_id
WHERE o.status = 'ACTIVE'
  AND s.station_status = 'OPEN'
  AND p.acceptance_status = 'PASSED'
  AND (
      p.current_status = 'OFFLINE'
      OR (
          p.current_status IN ('IDLE', 'OCCUPIED')
          AND (
              p.last_heartbeat_at IS NULL
              OR TIMESTAMPDIFF(SECOND, p.last_heartbeat_at, NOW()) > p.offline_threshold_sec
          )
      )
  )
ORDER BY p.pile_id ASC, g.gun_seq ASC;

-- [实现 B - 优化]：结合 DDL 约束 offline_threshold_sec >= 30，先用常量区间
--                 last_heartbeat_at < DATE_SUB(NOW(), INTERVAL 30 SECOND) 下推走 idx_pile_heartbeat_status
--                 锁定候选桩，再精确复核每台桩的独立阈值并关联枪明细
SELECT
    o.operator_name,
    s.station_code,
    s.station_name,
    cand.pile_id,
    cand.pile_code,
    cand.current_status AS pile_status,
    cand.last_heartbeat_at,
    cand.offline_threshold_sec,
    g.gun_id,
    g.gun_code,
    g.current_status AS gun_status
FROM (
    SELECT
        p.pile_id,
        p.station_id,
        p.pile_code,
        p.current_status,
        p.last_heartbeat_at,
        p.offline_threshold_sec
    FROM charging_pile AS p
    WHERE p.current_status = 'OFFLINE'
      AND p.acceptance_status = 'PASSED'

    UNION ALL

    SELECT
        p.pile_id,
        p.station_id,
        p.pile_code,
        p.current_status,
        p.last_heartbeat_at,
        p.offline_threshold_sec
    FROM charging_pile AS p
    WHERE p.current_status IN ('IDLE', 'OCCUPIED')
      AND p.acceptance_status = 'PASSED'
      AND (
          p.last_heartbeat_at IS NULL
          OR (
              p.last_heartbeat_at < DATE_SUB(NOW(), INTERVAL 30 SECOND)
              AND TIMESTAMPDIFF(SECOND, p.last_heartbeat_at, NOW()) > p.offline_threshold_sec
          )
      )
) AS cand
INNER JOIN station AS s
    ON s.station_id = cand.station_id
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
LEFT JOIN charging_gun AS g
    ON g.pile_id = cand.pile_id
WHERE o.status = 'ACTIVE'
  AND s.station_status = 'OPEN'
ORDER BY cand.pile_id ASC, g.gun_seq ASC;

-- EXPLAIN ANALYZE 对比命令（第 3 组：实现 A vs 实现 B）：
EXPLAIN ANALYZE
SELECT
    o.operator_name,
    s.station_code,
    s.station_name,
    p.pile_id,
    p.pile_code,
    p.current_status AS pile_status,
    p.last_heartbeat_at,
    p.offline_threshold_sec,
    g.gun_id,
    g.gun_code,
    g.current_status AS gun_status
FROM charging_pile AS p
INNER JOIN station AS s
    ON s.station_id = p.station_id
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
LEFT JOIN charging_gun AS g
    ON g.pile_id = p.pile_id
WHERE o.status = 'ACTIVE'
  AND s.station_status = 'OPEN'
  AND p.acceptance_status = 'PASSED'
  AND (
      p.current_status = 'OFFLINE'
      OR (
          p.current_status IN ('IDLE', 'OCCUPIED')
          AND (
              p.last_heartbeat_at IS NULL
              OR TIMESTAMPDIFF(SECOND, p.last_heartbeat_at, NOW()) > p.offline_threshold_sec
          )
      )
  )
ORDER BY p.pile_id ASC, g.gun_seq ASC;

EXPLAIN ANALYZE
SELECT
    o.operator_name,
    s.station_code,
    s.station_name,
    cand.pile_id,
    cand.pile_code,
    cand.current_status AS pile_status,
    cand.last_heartbeat_at,
    cand.offline_threshold_sec,
    g.gun_id,
    g.gun_code,
    g.current_status AS gun_status
FROM (
    SELECT
        p.pile_id,
        p.station_id,
        p.pile_code,
        p.current_status,
        p.last_heartbeat_at,
        p.offline_threshold_sec
    FROM charging_pile AS p
    WHERE p.current_status = 'OFFLINE'
      AND p.acceptance_status = 'PASSED'

    UNION ALL

    SELECT
        p.pile_id,
        p.station_id,
        p.pile_code,
        p.current_status,
        p.last_heartbeat_at,
        p.offline_threshold_sec
    FROM charging_pile AS p
    WHERE p.current_status IN ('IDLE', 'OCCUPIED')
      AND p.acceptance_status = 'PASSED'
      AND (
          p.last_heartbeat_at IS NULL
          OR (
              p.last_heartbeat_at < DATE_SUB(NOW(), INTERVAL 30 SECOND)
              AND TIMESTAMPDIFF(SECOND, p.last_heartbeat_at, NOW()) > p.offline_threshold_sec
          )
      )
) AS cand
INNER JOIN station AS s
    ON s.station_id = cand.station_id
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
LEFT JOIN charging_gun AS g
    ON g.pile_id = cand.pile_id
WHERE o.status = 'ACTIVE'
  AND s.station_status = 'OPEN'
ORDER BY cand.pile_id ASC, g.gun_seq ASC;


-- 业务线 2 & 5 写法说明：写法 A 直接查原表（自己写 JOIN / 聚合）；写法 B 查 02_views.sql 里的 RBAC 视图
-- ============================================================
-- 业务线 2：订单计费
-- ============================================================

-- 第 1 组：查某个车主的订单列表（带未付金额和状态中文）
-- A：查原表，状态中文在应用层翻译
EXPLAIN ANALYZE
SELECT o.order_id, o.order_no, o.start_time, o.kwh,
       o.total_amount, o.paid_amount,
       o.total_amount - o.paid_amount AS unpaid_amount,
       o.status, s.station_name, u.username
FROM charge_order o
JOIN station s      ON s.station_id = o.station_id
JOIN user_account u ON u.user_id = o.user_id
WHERE o.user_id = 9006
ORDER BY o.start_time DESC;

-- B：直接查视图，状态中文已经在视图里算好
EXPLAIN ANALYZE
SELECT v.order_id, v.order_no, v.start_time, v.kwh,
       v.total_amount, v.paid_amount, v.unpaid_amount, v.status, v.status_text,
       s.station_name
FROM v_order_owner v
JOIN station s ON s.station_id = v.station_id
WHERE v.user_id = 9006
ORDER BY v.start_time DESC;

-- 结论：两边都走 idx_order_user（user_id, start_time），
--       视图多一层 derived，开销几乎一样；好处是前端少写翻译逻辑。

-- 第 2 组：站点当天运营日报（单量、电量、金额）
-- A：自己 GROUP BY
EXPLAIN ANALYZE
SELECT DATE(start_time) AS stat_date, station_id, gun_id,
       COUNT(*) AS order_cnt,
       SUM(kwh) AS total_kwh,
       SUM(total_amount) AS total_amount
FROM charge_order
WHERE start_time >= '2025-03-01' AND start_time < '2025-03-03'
GROUP BY DATE(start_time), station_id, gun_id;

-- B：查视图（视图里已经按天+站+枪聚合好了）
EXPLAIN ANALYZE
SELECT * FROM v_order_station_daily
WHERE stat_date >= '2025-03-01' AND stat_date < '2025-03-03';

-- 结论：A 能在 WHERE 上用 idx_order_time 先过滤再聚合；
--       B 把整个表的聚合都算了一遍再筛，数据量大时反而慢。
--       这就是“视图不是万能”的例子，日报类查询适合手写。

-- 第 3 组：峰平谷各卖了多少电、平均单价
-- A：明细表聚合，单价按“金额/电量”现算
EXPLAIN ANALYZE
SELECT period,
       SUM(kwh) AS total_kwh,
       ROUND(SUM(energy_fee + service_fee) / NULLIF(SUM(kwh), 0), 4) AS avg_price
FROM order_fee_detail
GROUP BY period;

-- B：查视图
EXPLAIN ANALYZE
SELECT period, SUM(total_kwh) AS total_kwh,
       ROUND(SUM(energy_fee + service_fee) / NULLIF(SUM(total_kwh), 0), 4) AS avg_price
FROM v_order_period_stat
GROUP BY period;

-- 结论：两边都是全表聚合，5 行结果没差别。视图的意义是“口径统一”，
--       避免 A 里每个人自己算平均单价写法不一样。

-- ============================================================
-- 业务线 5：结算对账
-- ============================================================

-- 第 4 组：账单总览（应付、已开票、还差多少、有几条未处理差异）
-- A：三个表自己 LEFT JOIN 子查询
EXPLAIN ANALYZE
SELECT b.bill_id, b.bill_month, b.payable_amount,
       COALESCE(i.amt, 0) AS invoiced_amount,
       b.payable_amount - COALESCE(i.amt, 0) AS uninvoiced_amount,
       COALESCE(d.cnt, 0) AS open_diff_cnt
FROM settlement_bill b
LEFT JOIN (SELECT bill_id, SUM(amount) AS amt FROM invoice_record GROUP BY bill_id) i
       ON i.bill_id = b.bill_id
LEFT JOIN (SELECT bill_id, COUNT(*) AS cnt FROM reconcile_diff
           WHERE resolved = 0 GROUP BY bill_id) d
       ON d.bill_id = b.bill_id
ORDER BY b.bill_month, b.station_id;

-- B：查视图
EXPLAIN ANALYZE
SELECT bill_id, bill_month, payable_amount,
       invoiced_amount, uninvoiced_amount, open_diff_cnt
FROM v_settle_bill_detail
ORDER BY bill_month, bill_id;

-- 结论：两种写法执行计划几乎一样（都是两个 derived 表再 join），
--       差别在维护：口径改了只改视图一处，不用满项目找 SQL。

-- 第 5 组：按站按月汇总待结算订单（生成账单前的核对数）
-- A：原表聚合
EXPLAIN ANALYZE
SELECT DATE_FORMAT(o.start_time, '%Y-%m') AS bill_month,
       o.station_id, s.station_name, op.operator_name,
       COUNT(*) AS order_cnt, SUM(o.total_amount) AS total_amount
FROM charge_order o
JOIN station s  ON s.station_id = o.station_id
JOIN operator op ON op.operator_id = s.operator_id
WHERE o.status = 'PAID'
GROUP BY DATE_FORMAT(o.start_time, '%Y-%m'), o.station_id, s.station_name, op.operator_name;

-- B：查视图
EXPLAIN ANALYZE
SELECT v.bill_month, v.station_id, s.station_name,
       COUNT(*) AS order_cnt, SUM(v.total_amount) AS total_amount
FROM v_settle_order_month v
JOIN station s ON s.station_id = v.station_id
GROUP BY v.bill_month, v.station_id, s.station_name;

-- 结论：A 有 WHERE status='PAID'，能走 idx_order_station 先按站过滤；B 视图里已经过滤过，
--       外层再筛月份用不上索引，数据量大了要把月份条件下推到视图。
--       这也是我把 sp_settle_generate 写成存储过程、而不是直接查视图的原因。

-- 第 6 组：对账员工作台（未处理差异 + 账单信息）
-- A：两表 join
EXPLAIN ANALYZE
SELECT d.diff_id, d.bill_id, b.bill_month, d.order_id,
       d.diff_type, d.diff_amount, d.remark
FROM reconcile_diff d
JOIN settlement_bill b ON b.bill_id = d.bill_id
WHERE d.resolved = 0
ORDER BY d.created_at DESC;

-- B：查视图
EXPLAIN ANALYZE
SELECT diff_id, bill_id, bill_month, order_id, diff_type, diff_amount, remark
FROM v_settle_diff_open
ORDER BY created_at DESC;

-- 结论：A 的 WHERE resolved=0 能走 idx_diff_bill（bill_id, resolved）的前缀之外，
--       B 的过滤写在视图内部，外层 ORDER BY 仍需排序，20 条数据看不出来，
--       实际按 resolved 过滤仍要扫一遍，20 条看不出差别，真实数据量下要补 (resolved, created_at) 索引。


-- ============================================================================
-- 【业务线 3】设备监控与工单复杂查询优化对比（负责人：成员 3 / 代润森，共 3 组）
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 第 1 组（4 表关联：operator + station + charging_pile + pile_telemetry）
-- 业务场景：查询营业场站内每根已验收桩的最新一分钟遥测与故障码
-- ----------------------------------------------------------------------------

-- [实现 A - 低效]：SELECT 标量相关子查询每桩回表取最大时间，且在时间列上使用
--                  DATE() 函数，导致 uk_pile_telemetry_time 与分区裁剪失效
EXPLAIN ANALYZE
SELECT
    s.station_code,
    s.station_name,
    p.pile_id,
    p.pile_code,
    t.collected_at,
    t.power,
    t.module_temp,
    t.fault_code
FROM charging_pile AS p
INNER JOIN station AS s
    ON s.station_id = p.station_id
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
LEFT JOIN pile_telemetry AS t
    ON t.pile_id = p.pile_id
   AND t.collected_at = (
       SELECT MAX(t2.collected_at)
         FROM pile_telemetry AS t2
        WHERE t2.pile_id = p.pile_id
          AND DATE(t2.collected_at) = DATE(NOW())
   )
WHERE s.station_status = 'OPEN'
  AND o.status = 'ACTIVE'
  AND p.acceptance_status = 'PASSED'
ORDER BY p.pile_id ASC;

-- [实现 B - 优化]：使用 ROW_NUMBER 窗口函数每桩取最新一行，避免相关子查询回表；
--                 时间过滤使用常量区间，使索引与 RANGE 分区裁剪同时生效
EXPLAIN ANALYZE
SELECT
    s.station_code,
    s.station_name,
    p.pile_id,
    p.pile_code,
    t.collected_at,
    t.power,
    t.module_temp,
    t.fault_code
FROM charging_pile AS p
INNER JOIN station AS s
    ON s.station_id = p.station_id
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
LEFT JOIN (
    SELECT
        pt.pile_id,
        pt.collected_at,
        pt.power,
        pt.module_temp,
        pt.fault_code,
        ROW_NUMBER() OVER (PARTITION BY pt.pile_id ORDER BY pt.collected_at DESC) AS rn
    FROM pile_telemetry AS pt
    WHERE pt.collected_at >= CAST(CURDATE() AS DATETIME)
) AS t
    ON t.pile_id = p.pile_id AND t.rn = 1
WHERE s.station_status = 'OPEN'
  AND o.status = 'ACTIVE'
  AND p.acceptance_status = 'PASSED'
ORDER BY p.pile_id ASC;


-- ----------------------------------------------------------------------------
-- 第 2 组（5 表关联：operator + station + charging_pile + alarm_record + repair_order）
-- 业务场景：统计近 30 分钟各营业场站超时未响应告警数量与最高告警等级
-- ----------------------------------------------------------------------------

-- [实现 A - 低效]：NOT EXISTS 子查询反复扫描 repair_order；WHERE 用
--                  TIMESTAMPDIFF 包裹 occurred_at，无法命中 idx_alarm_status_time
EXPLAIN ANALYZE
SELECT
    s.station_id,
    s.station_code,
    s.station_name,
    o.operator_name,
    COUNT(*)                                  AS overdue_alarm_count,
    MIN(a.alarm_level)                        AS most_urgent_alarm_level
FROM alarm_record AS a
INNER JOIN charging_pile AS p
    ON p.pile_id = a.pile_id
INNER JOIN station AS s
    ON s.station_id = p.station_id
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
WHERE a.status IN ('ACTIVE', 'PROCESSING')
  AND TIMESTAMPDIFF(MINUTE, a.occurred_at, NOW()) > a.response_limit_min
  AND NOT EXISTS (
      SELECT 1
        FROM repair_order AS r
       WHERE r.alarm_id = a.alarm_id
         AND r.status IN ('ACCEPTED', 'ARRIVED', 'REPAIRED', 'VERIFIED')
  )
GROUP BY s.station_id, s.station_code, s.station_name, o.operator_name
ORDER BY overdue_alarm_count DESC;

-- [实现 B - 优化]：由于 DDL 约束 response_limit_min >= 15，先用常量时间下界锁定候选
--                 行命中 idx_alarm_status_time(status, occurred_at)，再逐行精确复核
EXPLAIN ANALYZE
SELECT
    s.station_id,
    s.station_code,
    s.station_name,
    o.operator_name,
    COUNT(*)                                  AS overdue_alarm_count,
    MIN(a.alarm_level)                        AS most_urgent_alarm_level
FROM alarm_record AS a
INNER JOIN charging_pile AS p
    ON p.pile_id = a.pile_id
INNER JOIN station AS s
    ON s.station_id = p.station_id
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
WHERE a.status IN ('ACTIVE', 'PROCESSING')
  AND a.occurred_at < DATE_SUB(NOW(), INTERVAL 15 MINUTE)
  AND TIMESTAMPDIFF(MINUTE, a.occurred_at, NOW()) > a.response_limit_min
  AND NOT EXISTS (
      SELECT 1
        FROM repair_order AS r
       WHERE r.alarm_id = a.alarm_id
         AND r.status IN ('ACCEPTED', 'ARRIVED', 'REPAIRED', 'VERIFIED')
  )
GROUP BY s.station_id, s.station_code, s.station_name, o.operator_name
ORDER BY overdue_alarm_count DESC;


-- ----------------------------------------------------------------------------
-- 第 3 组（4 表关联：station + charging_pile + alarm_record + repair_order）
-- 业务场景：统计近 7 天各场站维修工单量、平均响应时长与按时闭环率
-- ----------------------------------------------------------------------------

-- [实现 A - 低效]：DATE() 包裹 dispatched_at 使时间索引失效，且每行做 CASE 函数计算
EXPLAIN ANALYZE
SELECT
    s.station_id,
    s.station_name,
    COUNT(r.work_order_id)                                                          AS work_order_count,
    ROUND(AVG(TIMESTAMPDIFF(MINUTE, a.occurred_at, r.accepted_at)), 1)             AS avg_response_min,
    ROUND(100 * SUM(CASE WHEN r.closed_at IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) AS closure_rate
FROM repair_order AS r
INNER JOIN alarm_record AS a
    ON a.alarm_id = r.alarm_id
INNER JOIN charging_pile AS p
    ON p.pile_id = a.pile_id
INNER JOIN station AS s
    ON s.station_id = p.station_id
WHERE DATE(r.dispatched_at) >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
GROUP BY s.station_id, s.station_name
ORDER BY work_order_count DESC;

-- [实现 B - 优化]：时间筛选改为常量下界直接命中 idx_workorder_dispatch_time，
--                 聚合前先裁剪工单明细
EXPLAIN ANALYZE
SELECT
    s.station_id,
    s.station_name,
    COUNT(r.work_order_id)                                                          AS work_order_count,
    ROUND(AVG(TIMESTAMPDIFF(MINUTE, a.occurred_at, r.accepted_at)), 1)             AS avg_response_min,
    ROUND(100 * SUM(CASE WHEN r.closed_at IS NOT NULL THEN 1 ELSE 0 END) / COUNT(*), 1) AS closure_rate
FROM repair_order AS r
INNER JOIN alarm_record AS a
    ON a.alarm_id = r.alarm_id
INNER JOIN charging_pile AS p
    ON p.pile_id = a.pile_id
INNER JOIN station AS s
    ON s.station_id = p.station_id
WHERE r.dispatched_at >= CAST(DATE_SUB(CURDATE(), INTERVAL 7 DAY) AS DATETIME)
GROUP BY s.station_id, s.station_name
ORDER BY work_order_count DESC;

-- ============================================================================
-- 【业务线 4】用户、车辆与优惠券复杂查询优化对比（负责人：成员 4 / 李海波，共 3 组）
-- 说明：本文件与线 2/5 一样采用“写法 A 直查原表 / 写法 B 走 02_views.sql 的 RBAC 视图”的
--       对照口径，每组两种写法的结果集完全一致，可直接用 EXPLAIN ANALYZE 比较扫描行数与耗时。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 第 1 组（4 表关联：fleet_account + user_account + vehicle + coupon_record）
-- 业务场景：车队管理页——列出正常合作车队下的用车人、绑定车辆数与当前可用券数
-- ----------------------------------------------------------------------------

-- [实现 A - 低效]：每行两次相关子查询 + 在 role 列上套 LOWER() 使 idx_user_role_status 失效
EXPLAIN ANALYZE
SELECT
    f.fleet_code,
    f.fleet_name,
    u.user_id,
    u.username,
    u.phone_masked,
    (SELECT COUNT(*)
       FROM vehicle AS v
      WHERE v.user_id = u.user_id
        AND v.bind_status = 'BOUND')                                   AS vehicle_cnt,
    (SELECT COUNT(*)
       FROM coupon_record AS c
      WHERE c.user_id = u.user_id
        AND c.status = 'ISSUED'
        AND c.expire_at >= CURDATE())                                   AS usable_coupon_cnt
FROM fleet_account AS f
INNER JOIN user_account AS u
    ON u.fleet_id = f.fleet_id
WHERE f.status = 'ACTIVE'
  AND LOWER(u.role) = 'fleet'
ORDER BY f.fleet_code ASC, u.user_id ASC;

-- [实现 B - 优化]：直接复用视图 v_user_masked_profile（视图内已把 vehicle_cnt /
--                 usable_coupon_cnt 一次预聚合好，且只出脱敏手机号），
--                 此处不再重复 join vehicle 做二次聚合
EXPLAIN ANALYZE
SELECT
    f.fleet_code,
    f.fleet_name,
    p.user_id,
    p.username,
    p.phone_masked,
    p.vehicle_cnt                                                       AS vehicle_cnt,
    p.usable_coupon_cnt                                                 AS usable_coupon_cnt
FROM fleet_account AS f
INNER JOIN v_user_masked_profile AS p
    ON p.fleet_id = f.fleet_id
WHERE f.status = 'ACTIVE'
  AND p.role = 'fleet'
ORDER BY f.fleet_code ASC, p.user_id ASC;

-- 结论：A 每个输出行要分别回表统计车辆与券（相关子查询按行数线性放大），
--       且 LOWER(role) 让 idx_user_role_status 无法命中（前导列 role 被函数包裹）；
--       B 的车辆与券统计都在视图内一次性预聚合，fleet_id / role 前缀可命中
--       idx_user_fleet 与 idx_user_role_status，扫描行数由“行数 × 2 次回表”降为“视图内各扫一遍”。


-- ----------------------------------------------------------------------------
-- 第 2 组（4 表关联：coupon_campaign + coupon_record + coupon_redeem_record + station）
-- 业务场景：运营看板——活动投放量、已核销量、核销率与适用场站
-- ----------------------------------------------------------------------------

-- [实现 A - 低效]：三个标量子查询各扫一遍券表，且用 DATE_FORMAT(valid_from) 破坏索引
EXPLAIN ANALYZE
SELECT
    cp.campaign_code,
    cp.campaign_name,
    s.station_name,
    cp.face_value,
    cp.issue_total,
    (SELECT COUNT(*)
       FROM coupon_record AS c
      WHERE c.campaign_id = cp.campaign_id)                            AS issued_cnt,
    (SELECT COUNT(*)
       FROM coupon_redeem_record AS d
       INNER JOIN coupon_record AS c2
           ON c2.coupon_id = d.coupon_id
      WHERE c2.campaign_id = cp.campaign_id)                           AS redeemed_cnt,
    (SELECT ROUND(COUNT(*) / NULLIF(
                (SELECT COUNT(*) FROM coupon_record AS c3
                  WHERE c3.campaign_id = cp.campaign_id), 0), 4)
       FROM coupon_redeem_record AS d2
       INNER JOIN coupon_record AS c4
           ON c4.coupon_id = d2.coupon_id
      WHERE c4.campaign_id = cp.campaign_id)                           AS redeem_rate
FROM coupon_campaign AS cp
LEFT JOIN station AS s
    ON s.station_id = cp.applicable_station_id
WHERE DATE_FORMAT(cp.valid_from, '%Y') = '2026'
ORDER BY cp.campaign_id ASC;

-- [实现 B - 优化]：直接查视图 v_coupon_campaign_stat（内部一次预聚合出核销量与核销率），
--                 有效期改为范围条件以命中 idx_campaign_status_valid
EXPLAIN ANALYZE
SELECT
    campaign_code,
    campaign_name,
    station_name,
    face_value,
    issue_total,
    issued_count        AS issued_cnt,
    redeemed_cnt,
    redeem_rate
FROM v_coupon_campaign_stat
WHERE valid_from >= '2026-01-01'
  AND valid_from <  '2027-01-01'
ORDER BY campaign_id ASC;

-- 结论：A 的 4 个标量子查询把券表扫了 4 遍，代价随活动数线性增长；
--       B 只扫一次 coupon_record + 一次 coupon_redeem_record 派生聚合，
--       核销率在视图内用 NULLIF 兜零除，避免应用层再补一次除法判断。
-- 索引提示：idx_campaign_status_valid 的首列是 status，本组只按 valid_from 过滤时
--       用不到该索引，实际是 idx_primary 顺序扫活动表（活动量小，代价可忽略）；
--       若看板固定只看“投放中”的活动，建议在 WHERE 上补 status = 'ACTIVE' 以命中该复合索引。


-- ----------------------------------------------------------------------------
-- 第 3 组（4 表关联：user_account + charge_order + coupon_redeem_record + coupon_record）
-- 业务场景：2025 年 3 月车主充电对账——单量、订单金额、券抵扣金额（按金额倒序）
-- ----------------------------------------------------------------------------

-- [实现 A - 低效]：在 start_time 上套 DATE_FORMAT 导致 idx_order_user 失效，
--                 并用 OR 连接两个角色值，优化器只能退化为全表扫描后再过滤
EXPLAIN ANALYZE
SELECT
    u.user_id,
    u.username,
    u.member_level,
    COUNT(o.order_id)                                                   AS order_cnt,
    COALESCE(SUM(o.total_amount), 0.00)                                 AS total_amount,
    COALESCE(SUM(d.face_value), 0.00)                                   AS coupon_discount
FROM user_account AS u
LEFT JOIN charge_order AS o
    ON o.user_id = u.user_id
   AND DATE_FORMAT(o.start_time, '%Y-%m') = '2025-03'
LEFT JOIN coupon_redeem_record AS d
    ON d.order_id = o.order_id
   AND d.user_id = u.user_id
WHERE u.role = 'driver' OR u.role = 'fleet'
GROUP BY u.user_id, u.username, u.member_level
ORDER BY total_amount DESC;

-- [实现 B - 优化]：时间改半开区间命中 idx_order_user 的范围扫描，
--                 角色改 IN 命中 idx_user_role_status，券抵扣先按订单聚合再并入
EXPLAIN ANALYZE
SELECT
    u.user_id,
    u.username,
    u.member_level,
    COALESCE(agg.order_cnt, 0)                                           AS order_cnt,
    COALESCE(agg.total_amount, 0.00)                                     AS total_amount,
    COALESCE(agg.coupon_discount, 0.00)                                  AS coupon_discount
FROM user_account AS u
LEFT JOIN (
    SELECT o.user_id,
           COUNT(*)                            AS order_cnt,
           SUM(o.total_amount)                 AS total_amount,
           SUM(COALESCE(d.face_value, 0.00))   AS coupon_discount
    FROM charge_order AS o
    LEFT JOIN (
        SELECT order_id, SUM(face_value) AS face_value
        FROM coupon_redeem_record
        GROUP BY order_id
    ) AS d
        ON d.order_id = o.order_id
    WHERE o.start_time >= '2025-03-01'
      AND o.start_time <  '2025-04-01'
    GROUP BY o.user_id
) AS agg
    ON agg.user_id = u.user_id
WHERE u.role IN ('driver', 'fleet')
ORDER BY total_amount DESC;

-- 结论：A 因为 DATE_FORMAT 与 OR 双重破坏索引，只能全表扫 charge_order 再逐行过滤；
--       B 的时间半开区间未以 user_id 为前缀，无法直接点查 idx_order_user，
--       但仍是索引覆盖扫描（不回表），而 A 连覆盖扫描都用不上；
--       角色 IN 命中 idx_user_role_status，券抵扣先按订单分组，聚合行数从
--       “订单数 × 券数”降为“订单数”，券量越大差距越明显。

