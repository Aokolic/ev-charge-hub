-- ============================================================================
-- 03. 存储过程与触发器（每条业务线 >= 1 个存储过程 + >= 1 个触发器）
-- 说明：
--   1. 使用 MySQL 标准 DELIMITER // 语法，可直接通过 mysql CLI / Navicat / DataGrip / PyCharm 执行；
--   2. 业务校验失败统一使用 SIGNAL SQLSTATE '45000'（MySQL errno 1644），
--      FastAPI app/main.py 全局异常处理器会自动将其翻译为 HTTP 400 并透传中文提示；
--   3. 支持会话变量 @skip_auto_status_log = 1，供 DAO 或存储过程在同事务内写入自定义备注日志时避免重复留痕。
-- ============================================================================

USE ev_charge_hub;
SET NAMES utf8mb4;

-- ============================================================================
-- 【业务线 1】场站与充电设施 —— 触发器与存储过程（负责人：成员 1 / 组长）
-- ============================================================================

-- 清理已有对象，保证脚本可重复执行
DROP TRIGGER IF EXISTS trg_gun_before_insert_validate;
DROP TRIGGER IF EXISTS trg_gun_before_update_validate;
DROP TRIGGER IF EXISTS trg_gun_after_insert_status_log;
DROP TRIGGER IF EXISTS trg_gun_after_update_status_log;
DROP TRIGGER IF EXISTS trg_pile_before_update_validate;
DROP TRIGGER IF EXISTS trg_pile_after_insert_status_log;
DROP TRIGGER IF EXISTS trg_pile_after_update_status_log;
DROP PROCEDURE IF EXISTS proc_check_pile_heartbeat_offline;
DROP PROCEDURE IF EXISTS proc_commission_charging_pile;

DELIMITER //

-- ----------------------------------------------------------------------------
-- 触发器 1.1：充电枪新增前校验（单枪功率 <= 所属桩额定功率；枪序号 <= 桩设计枪数）
-- ----------------------------------------------------------------------------
CREATE TRIGGER trg_gun_before_insert_validate
BEFORE INSERT ON charging_gun
FOR EACH ROW
BEGIN
    DECLARE v_rated_power       DECIMAL(8, 2);
    DECLARE v_gun_count         TINYINT UNSIGNED;
    DECLARE v_acceptance_status ENUM('PENDING', 'PASSED', 'REJECTED');
    DECLARE v_pile_status       ENUM('IDLE', 'OCCUPIED', 'FAULT', 'OFFLINE');

    SELECT rated_power, gun_count, acceptance_status, current_status
      INTO v_rated_power, v_gun_count, v_acceptance_status, v_pile_status
      FROM charging_pile
     WHERE pile_id = NEW.pile_id;

    IF v_rated_power IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '所属充电桩不存在，无法新增充电枪';
    END IF;

    IF NEW.max_power > v_rated_power THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '单枪最大输出功率不能超过所属充电桩的额定总功率';
    END IF;

    IF NEW.gun_seq > v_gun_count THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '充电枪序号超出所属充电桩的设计枪数上限';
    END IF;

    IF NEW.current_status IN ('IDLE', 'OCCUPIED') AND v_acceptance_status <> 'PASSED' THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '所属充电桩未通过协议联调与验收，充电枪禁止置为空闲或占用状态';
    END IF;

    IF NEW.current_status IN ('IDLE', 'OCCUPIED') AND v_pile_status IN ('FAULT', 'OFFLINE') THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '所属充电桩当前处于故障或离线状态，充电枪禁止置为空闲或占用状态';
    END IF;

    IF NEW.status_updated_at IS NULL THEN
        SET NEW.status_updated_at = NOW();
    END IF;
END //

-- ----------------------------------------------------------------------------
-- 触发器 1.2：充电枪更新前校验（同步约束功率/枪序号/父桩状态，保留调用方显式时间戳）
-- ----------------------------------------------------------------------------
CREATE TRIGGER trg_gun_before_update_validate
BEFORE UPDATE ON charging_gun
FOR EACH ROW
BEGIN
    DECLARE v_rated_power       DECIMAL(8, 2);
    DECLARE v_gun_count         TINYINT UNSIGNED;
    DECLARE v_acceptance_status ENUM('PENDING', 'PASSED', 'REJECTED');
    DECLARE v_pile_status       ENUM('IDLE', 'OCCUPIED', 'FAULT', 'OFFLINE');

    SELECT rated_power, gun_count, acceptance_status, current_status
      INTO v_rated_power, v_gun_count, v_acceptance_status, v_pile_status
      FROM charging_pile
     WHERE pile_id = NEW.pile_id;

    IF v_rated_power IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '目标所属充电桩不存在';
    END IF;

    IF NEW.max_power > v_rated_power THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '单枪最大输出功率不能超过所属充电桩的额定总功率';
    END IF;

    IF NEW.gun_seq > v_gun_count THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '充电枪序号超出所属充电桩的设计枪数上限';
    END IF;

    IF NEW.current_status IN ('IDLE', 'OCCUPIED') AND v_acceptance_status <> 'PASSED' THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '所属充电桩未通过协议联调与验收，充电枪禁止置为空闲或占用状态';
    END IF;

    IF NEW.current_status IN ('IDLE', 'OCCUPIED') AND v_pile_status IN ('FAULT', 'OFFLINE') THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '所属充电桩当前处于故障或离线状态，充电枪禁止置为空闲或占用状态';
    END IF;

    IF NOT (OLD.current_status <=> NEW.current_status)
       AND (NEW.status_updated_at <=> OLD.status_updated_at OR NEW.status_updated_at IS NULL) THEN
        SET NEW.status_updated_at = NOW();
    END IF;
END //

-- ----------------------------------------------------------------------------
-- 触发器 1.3：充电桩更新前校验（未验收禁止投运 + 降配前校验已有枪参数 + 状态时间刷新）
-- ----------------------------------------------------------------------------
CREATE TRIGGER trg_pile_before_update_validate
BEFORE UPDATE ON charging_pile
FOR EACH ROW
BEGIN
    IF NEW.current_status IN ('IDLE', 'OCCUPIED') AND NEW.acceptance_status <> 'PASSED' THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '未通过协议联调与验收的充电桩禁止投运为空闲或占用状态';
    END IF;

    IF NEW.rated_power < OLD.rated_power AND EXISTS (
        SELECT 1
          FROM charging_gun
         WHERE pile_id = NEW.pile_id
           AND max_power > NEW.rated_power
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '存在最大功率高于新额定功率的充电枪，禁止下调桩额定功率';
    END IF;

    IF NEW.gun_count < OLD.gun_count AND EXISTS (
        SELECT 1
          FROM charging_gun
         WHERE pile_id = NEW.pile_id
           AND gun_seq > NEW.gun_count
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '存在序号超出新枪数上限的充电枪，禁止缩减桩设计枪数';
    END IF;

    IF NOT (OLD.current_status <=> NEW.current_status)
       AND (NEW.status_updated_at <=> OLD.status_updated_at OR NEW.status_updated_at IS NULL) THEN
        SET NEW.status_updated_at = NOW();
    END IF;
END //

-- ----------------------------------------------------------------------------
-- 触发器 1.4 ~ 1.7：充电桩与充电枪建档/状态变更自动留痕（写入 device_status_log）
-- ----------------------------------------------------------------------------
CREATE TRIGGER trg_pile_after_insert_status_log
AFTER INSERT ON charging_pile
FOR EACH ROW
BEGIN
    IF COALESCE(@skip_auto_status_log, 0) = 0 THEN
        INSERT INTO device_status_log (
            device_type, pile_id, gun_id, prev_status, new_status,
            change_source, todo_status, remark, changed_at
        ) VALUES (
            'PILE', NEW.pile_id, NULL, NULL, NEW.current_status,
            'INIT', 'NONE', '充电桩建档初始化状态', NEW.status_updated_at
        );
    END IF;
END //

CREATE TRIGGER trg_pile_after_update_status_log
AFTER UPDATE ON charging_pile
FOR EACH ROW
BEGIN
    IF NOT (OLD.current_status <=> NEW.current_status)
       AND COALESCE(@skip_auto_status_log, 0) = 0 THEN
        INSERT INTO device_status_log (
            device_type, pile_id, gun_id, prev_status, new_status,
            change_source, todo_status, remark, changed_at
        ) VALUES (
            'PILE',
            NEW.pile_id,
            NULL,
            OLD.current_status,
            NEW.current_status,
            COALESCE(@status_change_source, 'MANUAL'),
            COALESCE(@status_todo_status, IF(NEW.current_status IN ('FAULT', 'OFFLINE'), 'PENDING', 'NONE')),
            COALESCE(@status_change_remark, '充电桩状态变更自动留痕'),
            NEW.status_updated_at
        );
        SET @status_change_source = NULL,
            @status_todo_status   = NULL,
            @status_change_remark = NULL;
    END IF;
END //

CREATE TRIGGER trg_gun_after_insert_status_log
AFTER INSERT ON charging_gun
FOR EACH ROW
BEGIN
    IF COALESCE(@skip_auto_status_log, 0) = 0 THEN
        INSERT INTO device_status_log (
            device_type, pile_id, gun_id, prev_status, new_status,
            change_source, todo_status, remark, changed_at
        ) VALUES (
            'GUN', NULL, NEW.gun_id, NULL, NEW.current_status,
            'INIT', 'NONE', '充电枪建档初始化状态', NEW.status_updated_at
        );
    END IF;
END //

CREATE TRIGGER trg_gun_after_update_status_log
AFTER UPDATE ON charging_gun
FOR EACH ROW
BEGIN
    IF NOT (OLD.current_status <=> NEW.current_status)
       AND COALESCE(@skip_auto_status_log, 0) = 0 THEN
        INSERT INTO device_status_log (
            device_type, pile_id, gun_id, prev_status, new_status,
            change_source, todo_status, remark, changed_at
        ) VALUES (
            'GUN',
            NULL,
            NEW.gun_id,
            OLD.current_status,
            NEW.current_status,
            COALESCE(@status_change_source, 'MANUAL'),
            COALESCE(@status_todo_status, IF(NEW.current_status IN ('FAULT', 'OFFLINE'), 'PENDING', 'NONE')),
            COALESCE(@status_change_remark, '充电枪状态变更自动留痕'),
            NEW.status_updated_at
        );
        SET @status_change_source = NULL,
            @status_todo_status   = NULL,
            @status_change_remark = NULL;
    END IF;
END //

-- ----------------------------------------------------------------------------
-- 存储过程 1.1：心跳离线超时巡检（自动置为 OFFLINE 并生成运维待办）
-- 调用方式：CALL proc_check_pile_heartbeat_offline(NOW(), @piles, @guns);
-- ----------------------------------------------------------------------------
CREATE PROCEDURE proc_check_pile_heartbeat_offline (
    IN  p_check_time     DATETIME,
    OUT p_affected_piles INT,
    OUT p_affected_guns  INT
)
BEGIN
    DECLARE v_now DATETIME;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        SET @skip_auto_status_log = NULL,
            @status_change_source = NULL,
            @status_todo_status   = NULL,
            @status_change_remark = NULL;
        DROP TEMPORARY TABLE IF EXISTS tmp_offline_piles;
        RESIGNAL;
    END;

    SET v_now = COALESCE(p_check_time, NOW());

    -- 使用临时表加 FOR UPDATE 锁定本次判定的离线超阈值桩，避免并发心跳导致状态竞态与重复待办
    DROP TEMPORARY TABLE IF EXISTS tmp_offline_piles;
    CREATE TEMPORARY TABLE tmp_offline_piles (
        pile_id               BIGINT UNSIGNED PRIMARY KEY,
        prev_status           ENUM('IDLE', 'OCCUPIED', 'FAULT', 'OFFLINE') NOT NULL,
        offline_threshold_sec INT UNSIGNED NOT NULL
    );

    INSERT INTO tmp_offline_piles (pile_id, prev_status, offline_threshold_sec)
    SELECT p.pile_id, p.current_status, p.offline_threshold_sec
      FROM charging_pile AS p
     WHERE p.current_status IN ('IDLE', 'OCCUPIED')
       AND (
           p.last_heartbeat_at IS NULL
           OR TIMESTAMPDIFF(SECOND, p.last_heartbeat_at, v_now) > p.offline_threshold_sec
       )
       FOR UPDATE;

    SELECT COUNT(*) INTO p_affected_piles FROM tmp_offline_piles;

    IF p_affected_piles > 0 THEN
        -- 关闭触发器自动插日志，改为显式批量写入带业务语义与 PENDING 待办的日志
        SET @skip_auto_status_log = 1;

        INSERT INTO device_status_log (
            device_type, pile_id, gun_id, prev_status, new_status,
            change_source, todo_status, remark, changed_at
        )
        SELECT
            'PILE',
            t.pile_id,
            NULL,
            t.prev_status,
            'OFFLINE',
            'HEARTBEAT',
            'PENDING',
            CONCAT('心跳超时超过 ', t.offline_threshold_sec, ' 秒，系统自动置为离线并生成运维待办'),
            v_now
        FROM tmp_offline_piles AS t;

        UPDATE charging_pile AS p
        INNER JOIN tmp_offline_piles AS t
            ON t.pile_id = p.pile_id
           SET p.current_status = 'OFFLINE',
               p.status_updated_at = v_now;

        -- 同步将离线桩下尚处于 IDLE / OCCUPIED 的充电枪置为 OFFLINE（保留已有 FAULT 状态）
        INSERT INTO device_status_log (
            device_type, pile_id, gun_id, prev_status, new_status,
            change_source, todo_status, remark, changed_at
        )
        SELECT
            'GUN',
            NULL,
            g.gun_id,
            g.current_status,
            'OFFLINE',
            'HEARTBEAT',
            'PENDING',
            '所属充电桩心跳超时离线，联动置为离线并生成运维待办',
            v_now
        FROM charging_gun AS g
        INNER JOIN tmp_offline_piles AS t
            ON t.pile_id = g.pile_id
        WHERE g.current_status IN ('IDLE', 'OCCUPIED');

        SET p_affected_guns = ROW_COUNT();

        IF p_affected_guns > 0 THEN
            UPDATE charging_gun AS g
            INNER JOIN tmp_offline_piles AS t
                ON t.pile_id = g.pile_id
               SET g.current_status = 'OFFLINE',
                   g.status_updated_at = v_now
             WHERE g.current_status IN ('IDLE', 'OCCUPIED');
        END IF;
    ELSE
        SET p_affected_guns = 0;
    END IF;

    -- 无论是否命中超时桩，统一清理会话上下文变量，防止残留状态污染后续手工 DML
    SET @skip_auto_status_log = NULL,
        @status_change_source = NULL,
        @status_todo_status   = NULL,
        @status_change_remark = NULL;

    DROP TEMPORARY TABLE IF EXISTS tmp_offline_piles;
END //

-- ----------------------------------------------------------------------------
-- 存储过程 1.2：充电桩协议联调验收与投运（原子更新桩验收态、可用态、下属枪与日志）
-- 调用方式：CALL proc_commission_charging_pile(1, CURDATE(), '现场铭牌及OCPP1.6联调通过');
-- ----------------------------------------------------------------------------
CREATE PROCEDURE proc_commission_charging_pile (
    IN p_pile_id           BIGINT UNSIGNED,
    IN p_commissioned_date DATE,
    IN p_remark            VARCHAR(255)
)
BEGIN
    DECLARE v_now               DATETIME;
    DECLARE v_comm_date         DATE;
    DECLARE v_warranty_end      DATE;
    DECLARE v_acceptance_status ENUM('PENDING', 'PASSED', 'REJECTED');
    DECLARE v_prev_status       ENUM('IDLE', 'OCCUPIED', 'FAULT', 'OFFLINE');
    DECLARE v_configured_guns   INT DEFAULT 0;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        SET @skip_auto_status_log = NULL,
            @status_change_source = NULL,
            @status_todo_status   = NULL,
            @status_change_remark = NULL;
        RESIGNAL;
    END;

    SET v_now = NOW();
    SET v_comm_date = COALESCE(p_commissioned_date, CURDATE());

    SELECT warranty_end_date, acceptance_status, current_status
      INTO v_warranty_end, v_acceptance_status, v_prev_status
      FROM charging_pile
     WHERE pile_id = p_pile_id
       FOR UPDATE;

    IF v_warranty_end IS NULL THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '指定充电桩不存在';
    END IF;

    IF v_acceptance_status = 'PASSED' THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '该充电桩已完成验收投运，无需重复验收';
    END IF;

    IF v_prev_status IN ('FAULT', 'OCCUPIED') THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '充电桩处于故障或占用状态，禁止直接执行验收投运';
    END IF;

    IF v_warranty_end < v_comm_date THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '质保截止日期不能早于投运日期';
    END IF;

    SELECT COUNT(*)
      INTO v_configured_guns
      FROM charging_gun
     WHERE pile_id = p_pile_id;

    IF v_configured_guns = 0 THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '请至少为该充电桩配置 1 把充电枪后再执行投运验收';
    END IF;

    SET @skip_auto_status_log = 1;

    UPDATE charging_pile
       SET acceptance_status = 'PASSED',
           commissioned_date = v_comm_date,
           current_status    = 'IDLE',
           last_heartbeat_at = v_now,
           status_updated_at = v_now
     WHERE pile_id = p_pile_id;

    INSERT INTO device_status_log (
        device_type, pile_id, gun_id, prev_status, new_status,
        change_source, todo_status, remark, changed_at
    ) VALUES (
        'PILE',
        p_pile_id,
        NULL,
        v_prev_status,
        'IDLE',
        'ACCEPTANCE',
        'NONE',
        COALESCE(NULLIF(TRIM(p_remark), ''), '协议联调与现场验收通过，允许正式投运'),
        v_now
    );

    -- 将该桩下处于初始 OFFLINE 状态的充电枪同步激活为 IDLE
    INSERT INTO device_status_log (
        device_type, pile_id, gun_id, prev_status, new_status,
        change_source, todo_status, remark, changed_at
    )
    SELECT
        'GUN',
        NULL,
        g.gun_id,
        g.current_status,
        'IDLE',
        'ACCEPTANCE',
        'NONE',
        '所属充电桩验收投运，充电枪同步上线空闲',
        v_now
    FROM charging_gun AS g
    WHERE g.pile_id = p_pile_id
      AND g.current_status = 'OFFLINE';

    UPDATE charging_gun
       SET current_status    = 'IDLE',
           status_updated_at = v_now
     WHERE pile_id = p_pile_id
       AND current_status = 'OFFLINE';

    SET @skip_auto_status_log = NULL,
        @status_change_source = NULL,
        @status_todo_status   = NULL,
        @status_change_remark = NULL;
END //

DELIMITER ;


-- ============================================================
-- 业务线 2：订单计费（孙墨涵 / 02-develop）
-- ============================================================

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


-- ============================================================================
-- 【业务线 3 ~ 业务线 4】存储过程与触发器占位区（各负责成员按需追加）
-- ============================================================================
