-- 04. 初始化测试数据（每张表 >= 20 条真实业务数据）
-- 前置：线1 的 station / charging_gun、线4 的 user_account / vehicle 已有数据
--       本脚本用 station_id 1~4、gun_id 1001~1020、user_id 9001~9020、vehicle_id 5001~5020

USE ev_charge_hub;

-- 1) 电价规则：4 个站 × 5 个时段 = 20 条
INSERT INTO pricing_rule
(station_id, period, start_time, end_time, energy_price, service_price,
 free_minutes, min_charge, effective_from, effective_to, is_active)
SELECT s.station_id, p.period, p.st, p.et, p.ep, p.sp, 0, 0.00, '2025-01-01', NULL, 1
FROM (SELECT 1 AS station_id UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4) s
CROSS JOIN (
    SELECT 'VALLEY' AS period, '22:00:00' AS st, '08:00:00' AS et, 0.3000 AS ep, 0.2000 AS sp
    UNION ALL SELECT 'FLAT', '08:00:00', '10:00:00', 0.6000, 0.3000
    UNION ALL SELECT 'PEAK', '10:00:00', '15:00:00', 0.9000, 0.4000
    UNION ALL SELECT 'FLAT', '15:00:00', '18:00:00', 0.6000, 0.3000
    UNION ALL SELECT 'PEAK', '18:00:00', '22:00:00', 0.9000, 0.4000
) p
WHERE 1 = 1
ON DUPLICATE KEY UPDATE energy_price = VALUES(energy_price);

-- 2) 订单：20 条，前 5 条充电中（不同枪，避开 uk_gun_active），其余已支付
INSERT IGNORE INTO charge_order
(order_no, user_id, vehicle_id, gun_id, station_id, start_time, end_time,
 start_soc, end_soc, kwh, discount_amount, status)
WITH RECURSIVE seq AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM seq WHERE n < 20)
SELECT CONCAT('SEED2025', LPAD(n, 6, '0')),
       9000 + n, 5000 + n, 1000 + n, 1 + MOD(n, 4),
       TIMESTAMP('2025-03-01 08:00:00') + INTERVAL n * 7 HOUR,
       CASE WHEN n <= 5 THEN NULL
            ELSE TIMESTAMP('2025-03-01 08:00:00') + INTERVAL n * 7 HOUR + INTERVAL 150 MINUTE END,
       20, 90, 20 + n,
       CASE WHEN MOD(n, 3) = 0 THEN 2.00 ELSE 0.00 END,
       CASE WHEN n <= 5 THEN 'CHARGING' ELSE 'PAID' END
FROM seq;

-- 3) 用存储过程给已结束的订单算分时明细（顺便验证过程能批量跑）
DROP PROCEDURE IF EXISTS seed_calc;
DELIMITER $$
CREATE PROCEDURE seed_calc()
BEGIN
    DECLARE i INT DEFAULT 6;
    WHILE i <= 20 DO
        CALL sp_order_calc(i);
        SET i = i + 1;
    END WHILE;
END$$
DELIMITER ;
CALL seed_calc();
DROP PROCEDURE seed_calc;

-- 4) 支付流水：每个已支付订单一条，金额等于应付
--    注意：触发器会回写 charge_order，同一语句里不能再读 charge_order，所以先快照到临时表
DROP TEMPORARY TABLE IF EXISTS tmp_pay;
CREATE TEMPORARY TABLE tmp_pay AS
SELECT order_id, total_amount, end_time FROM charge_order
WHERE status = 'PAID' AND total_amount > 0;

INSERT INTO payment_record (order_id, amount, method, txn_no, status, paid_at)
SELECT order_id, total_amount,
       CASE MOD(order_id, 3) WHEN 0 THEN 'WECHAT' WHEN 1 THEN 'ALIPAY' ELSE 'BALANCE' END,
       CONCAT('SEEDTXN', order_id), 'SUCCESS', end_time
FROM tmp_pay;
DROP TEMPORARY TABLE tmp_pay;

-- 4.1) 再补 5 条退款流水，保证支付表也够 20 条，同时演示负数流水
DROP TEMPORARY TABLE IF EXISTS tmp_refund;
CREATE TEMPORARY TABLE tmp_refund AS
SELECT order_id, 1.00 AS amt FROM charge_order
WHERE status = 'PAID' ORDER BY order_id LIMIT 5;

INSERT INTO payment_record (order_id, amount, method, txn_no, status, paid_at)
SELECT order_id, -amt, 'WECHAT', CONCAT('SEEDREF', order_id), 'REFUNDED', NOW()
FROM tmp_refund;
DROP TEMPORARY TABLE tmp_refund;
-- 5) 结算单：5 个月 × 4 个站 = 20 条
INSERT INTO settlement_bill
(operator_id, station_id, bill_month, order_count, total_kwh,
 total_amount, platform_fee, payable_amount, status)
SELECT 1 + MOD(s.station_id, 2), s.station_id, m.bill_month,
       10, 500.000, 350.00, 17.50, 332.50, 'DRAFT'
FROM (SELECT 1 AS station_id UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4) s
CROSS JOIN (SELECT '2025-01' AS bill_month UNION ALL SELECT '2025-02' UNION ALL SELECT '2025-03'
            UNION ALL SELECT '2025-04' UNION ALL SELECT '2025-05') m
WHERE 1 = 1
ON DUPLICATE KEY UPDATE order_count = VALUES(order_count);

-- 6) 对账差异：每张账单 1 条 = 20 条
INSERT INTO reconcile_diff
(bill_id, order_id, diff_type, expect_amount, actual_amount, diff_amount, resolved, remark, created_at)
SELECT b.bill_id, 1 + b.bill_id, 'AMOUNT_MISMATCH', 100.00, 98.50, 1.50, 0,
       '第三方流水比平台少 1.50', NOW()
FROM settlement_bill b
WHERE NOT EXISTS (SELECT 1 FROM reconcile_diff d WHERE d.bill_id = b.bill_id);

-- 7) 发票：每张账单开 30% 的金额 = 20 条（同样先快照，避开触发的表冲突）
DROP TEMPORARY TABLE IF EXISTS tmp_inv;
CREATE TEMPORARY TABLE tmp_inv AS
SELECT b.bill_id, ROUND(b.payable_amount * 0.3, 2) AS amt
FROM settlement_bill b
WHERE NOT EXISTS (SELECT 1 FROM invoice_record i WHERE i.bill_id = b.bill_id);

INSERT INTO invoice_record
(bill_id, amount, invoice_no, invoice_type, status, issued_at)
SELECT bill_id, amt, CONCAT('INV2025', LPAD(bill_id, 5, '0')), 'NORMAL', 'ISSUED', NOW()
FROM tmp_inv;
DROP TEMPORARY TABLE tmp_inv;

-- 自检：各表条数
SELECT 'pricing_rule' AS tbl, COUNT(*) AS cnt FROM pricing_rule
UNION ALL SELECT 'charge_order', COUNT(*) FROM charge_order
UNION ALL SELECT 'order_fee_detail', COUNT(*) FROM order_fee_detail
UNION ALL SELECT 'payment_record', COUNT(*) FROM payment_record
UNION ALL SELECT 'settlement_bill', COUNT(*) FROM settlement_bill
UNION ALL SELECT 'reconcile_diff', COUNT(*) FROM reconcile_diff
UNION ALL SELECT 'invoice_record', COUNT(*) FROM invoice_record;
