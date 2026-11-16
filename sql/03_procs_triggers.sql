-- 03. 存储过程与触发器（每条业务线 >= 1 个存储过程 + >= 1 个触发器）

-- ============================================================
-- 业务线 2：订单计费（孙墨涵 / 02-develop）
-- ============================================================

USE ev_charge_hub;

DROP PROCEDURE IF EXISTS sp_order_calc;
DELIMITER $$
CREATE PROCEDURE sp_order_calc(IN p_order_id BIGINT)
BEGIN
    DECLARE v_station   BIGINT;
    DECLARE v_start     DATETIME;
    DECLARE v_end       DATETIME;
    DECLARE v_kwh       DECIMAL(10,3);
    DECLARE v_discount  DECIMAL(10,2);
    DECLARE v_status    VARCHAR(20);
    DECLARE v_sec       BIGINT;
    DECLARE v_day       DATE;
    DECLARE v_last_day  DATE;
    DECLARE v_rs        DATETIME;
    DECLARE v_re        DATETIME;
    DECLARE v_ov        BIGINT;
    DECLARE v_cnt       INT DEFAULT 0;
    DECLARE v_ef        DECIMAL(12,2) DEFAULT 0;
    DECLARE v_sf        DECIMAL(12,2) DEFAULT 0;
    DECLARE v_period    VARCHAR(10);
    DECLARE v_ep        DECIMAL(8,4);
    DECLARE v_sp        DECIMAL(8,4);
    DECLARE v_rstart    TIME;
    DECLARE v_rend      TIME;
    DECLARE v_done      INT DEFAULT 0;
    DECLARE cur CURSOR FOR
        SELECT period, start_time, end_time, energy_price, service_price
        FROM pricing_rule
        WHERE station_id = v_station
          AND is_active = 1
          AND effective_from <= DATE(v_start)
          AND (effective_to IS NULL OR effective_to >= DATE(v_start));
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done = 1;

    SELECT station_id, start_time, end_time, kwh, discount_amount, status
      INTO v_station, v_start, v_end, v_kwh, v_discount, v_status
      FROM charge_order
     WHERE order_id = p_order_id;

    IF v_start IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单不存在';
    END IF;
    IF v_end IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单还没结束，不能计费';
    END IF;

    SET v_sec = TIMESTAMPDIFF(SECOND, v_start, v_end);
    IF v_sec <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单时长不合法';
    END IF;

    -- 重算前先清掉旧明细，保证过程可以重复跑
    DELETE FROM order_fee_detail WHERE order_id = p_order_id;

    SET v_last_day = DATE(v_end);
    OPEN cur;
    read_loop: LOOP
        FETCH cur INTO v_period, v_rstart, v_rend, v_ep, v_sp;
        IF v_done = 1 THEN
            LEAVE read_loop;
        END IF;

        -- 规则可能跨零点（22:00~06:00），把前一天也算进来
        SET v_day = DATE(v_start) - INTERVAL 1 DAY;
        WHILE v_day <= v_last_day DO
            SET v_rs = TIMESTAMP(v_day, v_rstart);
            IF v_rend > v_rstart THEN
                SET v_re = TIMESTAMP(v_day, v_rend);
            ELSE
                SET v_re = TIMESTAMP(v_day + INTERVAL 1 DAY, v_rend);
            END IF;

            SET v_ov = TIMESTAMPDIFF(SECOND,
                                     GREATEST(v_start, v_rs),
                                     LEAST(v_end, v_re));

            IF v_ov > 0 THEN
                INSERT INTO order_fee_detail
                    (order_id, period, begin_time, end_time, kwh,
                     energy_price, service_price, energy_fee, service_fee)
                VALUES
                    (p_order_id, v_period,
                     GREATEST(v_start, v_rs), LEAST(v_end, v_re),
                     ROUND(v_kwh * v_ov / v_sec, 3),
                     v_ep, v_sp,
                     ROUND(ROUND(v_kwh * v_ov / v_sec, 3) * v_ep, 2),
                     ROUND(ROUND(v_kwh * v_ov / v_sec, 3) * v_sp, 2));
                SET v_cnt = v_cnt + 1;
            END IF;

            SET v_day = v_day + INTERVAL 1 DAY;
        END WHILE;
    END LOOP;
    CLOSE cur;

    IF v_cnt = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '该站点没有匹配的分时电价规则';
    END IF;

    SELECT COALESCE(SUM(energy_fee), 0), COALESCE(SUM(service_fee), 0)
      INTO v_ef, v_sf
      FROM order_fee_detail
     WHERE order_id = p_order_id;

    UPDATE charge_order
       SET energy_fee   = v_ef,
           service_fee  = v_sf,
           total_amount = GREATEST(v_ef + v_sf - v_discount, 0),
           status       = CASE WHEN status = 'CHARGING' THEN 'FINISHED' ELSE status END
     WHERE order_id = p_order_id;
END$$
DELIMITER ;

DROP TRIGGER IF EXISTS trg_payment_guard;
DELIMITER $$
CREATE TRIGGER trg_payment_guard
BEFORE INSERT ON payment_record
FOR EACH ROW
BEGIN
    DECLARE v_total  DECIMAL(10,2);
    DECLARE v_paid   DECIMAL(10,2);
    DECLARE v_status VARCHAR(20);

    IF NEW.amount = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '支付金额不能为 0';
    END IF;

    SELECT total_amount, paid_amount, status
      INTO v_total, v_paid, v_status
      FROM charge_order
     WHERE order_id = NEW.order_id
       FOR UPDATE;

    IF v_status = 'CHARGING' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单还在充电，不能支付';
    END IF;

    IF NEW.amount > 0 AND v_paid + NEW.amount > v_total THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '支付金额超过订单应付金额';
    END IF;

    IF NEW.amount < 0 AND v_paid + NEW.amount < 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '退款金额超过已支付金额';
    END IF;

    IF NEW.status = 'SUCCESS' THEN
        UPDATE charge_order
           SET paid_amount = paid_amount + NEW.amount,
               status = CASE
                            WHEN paid_amount + NEW.amount >= total_amount THEN 'PAID'
                            WHEN paid_amount + NEW.amount <= 0 THEN 'REFUNDED'
                            ELSE status
                        END
         WHERE order_id = NEW.order_id;
    END IF;
END$$
DELIMITER ;

-- ============================================================
-- 业务线 5：结算对账（孙墨涵 / 05-develop）
-- ============================================================

DROP PROCEDURE IF EXISTS sp_settle_generate;
DELIMITER $$
CREATE PROCEDURE sp_settle_generate(
    IN p_operator_id BIGINT,
    IN p_month       CHAR(7),
    IN p_platform_rate DECIMAL(5,4)
)
BEGIN
    DECLARE v_rate DECIMAL(5,4);

    SET v_rate = COALESCE(p_platform_rate, 0.0500);

    -- 按站点汇总当月的已支付订单，一个站一张结算单
    INSERT INTO settlement_bill
        (operator_id, station_id, bill_month, order_count, total_kwh,
         total_amount, platform_fee, payable_amount, status)
    SELECT p_operator_id,
           o.station_id,
           p_month,
           COUNT(*),
           SUM(o.kwh),
           SUM(o.total_amount),
           ROUND(SUM(o.total_amount) * v_rate, 2),
           SUM(o.total_amount) - ROUND(SUM(o.total_amount) * v_rate, 2),
           'DRAFT'
      FROM charge_order o
     WHERE DATE_FORMAT(o.start_time, '%Y-%m') = p_month
       AND o.status = 'PAID'
     GROUP BY o.station_id
    ON DUPLICATE KEY UPDATE
        order_count    = VALUES(order_count),
        total_kwh      = VALUES(total_kwh),
        total_amount   = VALUES(total_amount),
        platform_fee   = VALUES(platform_fee),
        payable_amount = VALUES(payable_amount);

    -- 当月有订单但没结清的钱，记成一条待处理差异，和对账单对不上时能查到原因
    INSERT INTO reconcile_diff
        (bill_id, order_id, diff_type, expect_amount, actual_amount, diff_amount, remark)
    SELECT b.bill_id,
           o.order_id,
           'UNPAID',
           o.total_amount,
           o.paid_amount,
           o.total_amount - o.paid_amount,
           '订单未结清'
      FROM charge_order o
      JOIN settlement_bill b
        ON b.station_id = o.station_id
       AND b.bill_month = p_month
       AND b.operator_id = p_operator_id
     WHERE DATE_FORMAT(o.start_time, '%Y-%m') = p_month
       AND o.total_amount > o.paid_amount
       AND NOT EXISTS (SELECT 1 FROM reconcile_diff d
                        WHERE d.bill_id = b.bill_id AND d.order_id = o.order_id
                          AND d.diff_type = 'UNPAID');
END$$
DELIMITER ;

DROP TRIGGER IF EXISTS trg_invoice_guard;
DELIMITER $$
CREATE TRIGGER trg_invoice_guard
BEFORE INSERT ON invoice_record
FOR EACH ROW
BEGIN
    DECLARE v_payable  DECIMAL(10,2);
    DECLARE v_invoiced DECIMAL(10,2);
    DECLARE v_orig_amt DECIMAL(10,2);
    DECLARE v_orig_st  VARCHAR(20);

    IF NEW.amount = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '开票金额不能为 0';
    END IF;

    SELECT payable_amount INTO v_payable
      FROM settlement_bill
     WHERE bill_id = NEW.bill_id
       FOR UPDATE;

    -- 红冲票是负数，和原票相加正好抵消，所以这里不按状态过滤
    SELECT COALESCE(SUM(amount), 0) INTO v_invoiced
      FROM invoice_record
     WHERE bill_id = NEW.bill_id;

    IF NEW.red_flush_of IS NULL THEN
        IF NEW.amount < 0 THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '负数发票必须指定被红冲的原票';
        END IF;
        IF v_invoiced + NEW.amount > v_payable THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '开票金额超过账单应付金额';
        END IF;
    ELSE
        SELECT amount, status INTO v_orig_amt, v_orig_st
          FROM invoice_record
         WHERE invoice_id = NEW.red_flush_of;

        IF v_orig_amt IS NULL THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '被红冲的发票不存在';
        END IF;
        IF v_orig_st <> 'ISSUED' THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '只有已开具的发票才能红冲';
        END IF;
        IF NEW.amount > 0 OR -NEW.amount > v_orig_amt THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '红冲金额必须为负且不超过原票金额';
        END IF;
        -- 注意：MySQL 不允许在 invoice_record 的触发器里再改 invoice_record，
        -- 所以原票状态由调用方（DAO）在插入红冲票之后补一条 UPDATE 置为 RED_FLUSHED。
    END IF;
END$$
DELIMITER ;
