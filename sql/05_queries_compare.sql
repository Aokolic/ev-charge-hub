-- 05. 复杂 SQL 双实现与 EXPLAIN 对比（每条业务线 >= 3 组）
-- 写法 A：直接查原表、自己写 JOIN / 聚合
-- 写法 B：查 02_views.sql 里的 RBAC 视图
-- 每组都给出 EXPLAIN，对比扫描方式、是否用上索引、预估行数

USE ev_charge_hub;

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
