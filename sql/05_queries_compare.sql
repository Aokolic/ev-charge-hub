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

-- EXPLAIN ANALYZE 对比命令（第 1 组）：
-- EXPLAIN ANALYZE SELECT ... （分别作用于实现 A 与实现 B）


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


-- 业务线 2 & 5 写法说明：写法 A 直接查原表（自己写 JOIN / 聚合）；写法 B 查 02_views.sql 里的 RBAC 视图
-- ============================================================
-- 业务线 2：订单计费
-- ============================================================

-- 第 1 组：查某个车主的订单列表（带未付金额和状态中文）
-- A：查原表，状态中文在应用层翻译
EXPLAIN
SELECT o.order_id, o.order_no, o.start_time, o.kwh,
       o.total_amount, o.paid_amount,
       o.total_amount - o.paid_amount AS unpaid_amount,
       o.status
FROM charge_order o
WHERE o.user_id = 9006
ORDER BY o.start_time DESC;

-- B：直接查视图，状态中文已经在视图里算好
EXPLAIN
SELECT order_id, order_no, start_time, kwh,
       total_amount, paid_amount, unpaid_amount, status, status_text
FROM v_order_owner
WHERE user_id = 9006
ORDER BY start_time DESC;

-- 结论：两边都走 idx_order_user（user_id, start_time），
--       视图多一层 derived，开销几乎一样；好处是前端少写翻译逻辑。

-- 第 2 组：站点当天运营日报（单量、电量、金额）
-- A：自己 GROUP BY
EXPLAIN
SELECT DATE(start_time) AS stat_date, station_id, gun_id,
       COUNT(*) AS order_cnt,
       SUM(kwh) AS total_kwh,
       SUM(total_amount) AS total_amount
FROM charge_order
WHERE start_time >= '2025-03-01' AND start_time < '2025-03-03'
GROUP BY DATE(start_time), station_id, gun_id;

-- B：查视图（视图里已经按天+站+枪聚合好了）
EXPLAIN
SELECT * FROM v_order_station_daily
WHERE stat_date >= '2025-03-01' AND stat_date < '2025-03-03';

-- 结论：A 能在 WHERE 上用 idx_order_time 先过滤再聚合；
--       B 把整个表的聚合都算了一遍再筛，数据量大时反而慢。
--       这就是“视图不是万能”的例子，日报类查询适合手写。

-- 第 3 组：峰平谷各卖了多少电、平均单价
-- A：明细表聚合，单价按“金额/电量”现算
EXPLAIN
SELECT period,
       SUM(kwh) AS total_kwh,
       ROUND(SUM(energy_fee + service_fee) / NULLIF(SUM(kwh), 0), 4) AS avg_price
FROM order_fee_detail
GROUP BY period;

-- B：查视图
EXPLAIN
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
EXPLAIN
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
EXPLAIN
SELECT bill_id, bill_month, payable_amount,
       invoiced_amount, uninvoiced_amount, open_diff_cnt
FROM v_settle_bill_detail
ORDER BY bill_month, bill_id;

-- 结论：两种写法执行计划几乎一样（都是两个 derived 表再 join），
--       差别在维护：口径改了只改视图一处，不用满项目找 SQL。

-- 第 5 组：按站按月汇总待结算订单（生成账单前的核对数）
-- A：原表聚合
EXPLAIN
SELECT DATE_FORMAT(start_time, '%Y-%m') AS bill_month, station_id,
       COUNT(*) AS order_cnt, SUM(total_amount) AS total_amount
FROM charge_order
WHERE status = 'PAID'
GROUP BY DATE_FORMAT(start_time, '%Y-%m'), station_id;

-- B：查视图
EXPLAIN
SELECT bill_month, station_id, order_cnt, total_amount
FROM v_settle_order_month;

-- 结论：A 有 WHERE status='PAID'，能走 idx_order_station 先按站过滤；B 视图里已经过滤过，
--       外层再筛月份用不上索引，数据量大了要把月份条件下推到视图。
--       这也是我把 sp_settle_generate 写成存储过程、而不是直接查视图的原因。

-- 第 6 组：对账员工作台（未处理差异 + 账单信息）
-- A：两表 join
EXPLAIN
SELECT d.diff_id, d.bill_id, b.bill_month, d.order_id,
       d.diff_type, d.diff_amount, d.remark
FROM reconcile_diff d
JOIN settlement_bill b ON b.bill_id = d.bill_id
WHERE d.resolved = 0
ORDER BY d.created_at DESC;

-- B：查视图
EXPLAIN
SELECT diff_id, bill_id, bill_month, order_id, diff_type, diff_amount, remark
FROM v_settle_diff_open
ORDER BY created_at DESC;

-- 结论：A 的 WHERE resolved=0 能走 idx_diff_bill（bill_id, resolved）的前缀之外，
--       B 的过滤写在视图内部，外层 ORDER BY 仍需排序，20 条数据看不出来，
--       实际按 resolved 过滤仍要扫一遍，20 条看不出差别，真实数据量下要补 (resolved, created_at) 索引。


-- ============================================================================
-- 【业务线 3 ~ 业务线 4】复杂 SQL 双实现与 EXPLAIN ANALYZE 对比占位区
-- ============================================================================
