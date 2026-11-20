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
    DECLARE v_rule_id   BIGINT;
    DECLARE v_seg_kwh   DECIMAL(10,3);
    DECLARE v_ratio     DECIMAL(12,6) DEFAULT 1;
    DECLARE v_free_sec  INT DEFAULT 0;
    DECLARE v_min_charge DECIMAL(8,2) DEFAULT 0;
    DECLARE v_total     DECIMAL(12,2);
    DECLARE v_done      INT DEFAULT 0;
    DECLARE cur CURSOR FOR
        SELECT rule_id, period, start_time, end_time, energy_price, service_price
        FROM pricing_rule
        WHERE station_id = v_station
          AND is_active = 1
          AND effective_from <= DATE(v_start)
          AND (effective_to IS NULL OR effective_to >= DATE(v_start));
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done = 1;

    -- 先锁订单，避免两个人同时结束同一单、把明细算两遍
    SELECT station_id, start_time, end_time, kwh, discount_amount, status
      INTO v_station, v_start, v_end, v_kwh, v_discount, v_status
      FROM charge_order
     WHERE order_id = p_order_id
       FOR UPDATE;

    IF v_start IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单不存在';
    END IF;
    IF v_end IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单还没结束，不能计费';
    END IF;
    IF v_status NOT IN ('CHARGING', 'FINISHED') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单已支付或已取消，不能重算';
    END IF;

    SET v_sec = TIMESTAMPDIFF(SECOND, v_start, v_end);
    IF v_sec <= 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单时长不合法';
    END IF;

    -- 免费时长和最低消费取“订单开始那一刻生效”的规则，没有就按 0
    SET v_free_sec = COALESCE((
        SELECT free_minutes * 60 FROM pricing_rule
         WHERE station_id = v_station AND is_active = 1
           AND effective_from <= DATE(v_start)
           AND (effective_to IS NULL OR effective_to >= DATE(v_start))
           AND ((start_time < end_time
                 AND TIME(v_start) >= start_time AND TIME(v_start) < end_time)
             OR (start_time > end_time
                 AND (TIME(v_start) >= start_time OR TIME(v_start) < end_time)))
         LIMIT 1), 0);
    SET v_min_charge = COALESCE((
        SELECT min_charge FROM pricing_rule
         WHERE station_id = v_station AND is_active = 1
           AND effective_from <= DATE(v_start)
           AND (effective_to IS NULL OR effective_to >= DATE(v_start))
           AND ((start_time < end_time
                 AND TIME(v_start) >= start_time AND TIME(v_start) < end_time)
             OR (start_time > end_time
                 AND (TIME(v_start) >= start_time OR TIME(v_start) < end_time)))
         LIMIT 1), 0);

    -- 免掉的那段电量不参与计费，按比例摊到各时段
    IF v_free_sec > 0 AND v_sec > 0 THEN
        SET v_ratio = GREATEST(v_sec - v_free_sec, 0) / v_sec;
    END IF;

    -- 重算前先清旧明细，保证过程可以重复跑
    DELETE FROM order_fee_detail WHERE order_id = p_order_id;

    SET v_last_day = DATE(v_end);
    OPEN cur;
    read_loop: LOOP
        FETCH cur INTO v_rule_id, v_period, v_rstart, v_rend, v_ep, v_sp;
        IF v_done = 1 THEN
            LEAVE read_loop;
        END IF;

        -- 规则可能跨零点（22:00~08:00），把前一天也算进来
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
                SET v_seg_kwh = ROUND(v_kwh * v_ratio * v_ov / v_sec, 3);
                INSERT INTO order_fee_detail
                    (order_id, period, rule_id, begin_time, end_time, kwh,
                     energy_price, service_price, energy_fee, service_fee)
                VALUES
                    (p_order_id, v_period, v_rule_id,
                     GREATEST(v_start, v_rs), LEAST(v_end, v_re), v_seg_kwh,
                     v_ep, v_sp,
                     ROUND(v_seg_kwh * v_ep, 2),
                     ROUND(v_seg_kwh * v_sp, 2));
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

    -- 应付不低于最低消费，也不低于 0
    SET v_total = GREATEST(v_ef + v_sf - v_discount, v_min_charge, 0);

    UPDATE charge_order
       SET energy_fee   = v_ef,
           service_fee  = v_sf,
           total_amount = v_total,
           status       = CASE WHEN status = 'CHARGING' THEN 'FINISHED' ELSE status END
     WHERE order_id = p_order_id;
END$$
DELIMITER ;

DROP TRIGGER IF EXISTS trg_payment_guard;
DROP TRIGGER IF EXISTS trg_payment_after_insert;
DELIMITER $$
CREATE TRIGGER trg_payment_guard
BEFORE INSERT ON payment_record
FOR EACH ROW
BEGIN
    DECLARE v_user_id      BIGINT UNSIGNED;
    DECLARE v_total        DECIMAL(10,2);
    DECLARE v_paid         DECIMAL(10,2);
    DECLARE v_status       VARCHAR(20);
    DECLARE v_role         VARCHAR(20);
    DECLARE v_fleet_id     BIGINT UNSIGNED;
    DECLARE v_balance      DECIMAL(12,2);
    DECLARE v_fleet_st     VARCHAR(20);
    DECLARE v_credit_limit DECIMAL(12,2);
    DECLARE v_credit_used  DECIMAL(12,2);

    IF NEW.amount = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '支付金额不能为 0';
    END IF;

    SELECT user_id, total_amount, paid_amount, status
      INTO v_user_id, v_total, v_paid, v_status
      FROM charge_order
     WHERE order_id = NEW.order_id
       FOR UPDATE;

    IF v_status IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单不存在';
    END IF;
    IF v_status = 'CHARGING' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单还在充电，不能支付';
    END IF;
    IF v_status = 'CANCELLED' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单已取消，不能支付或退款';
    END IF;

    IF NEW.amount > 0 AND v_paid + NEW.amount > v_total THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '支付金额超过订单应付金额';
    END IF;
    IF NEW.amount < 0 AND v_paid + NEW.amount < 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '退款金额超过已支付金额';
    END IF;

    -- BEFORE INSERT 仅做校验，不做任何 UPDATE，避免 INSERT IGNORE 命中重复 txn_no 时误改订单或余额
    IF NEW.status <> 'FAILED'
       AND NEW.method = 'BALANCE'
       AND NEW.amount > 0
       AND COALESCE(@skip_balance_sync, 0) = 0 THEN
        SELECT role, fleet_id, balance
          INTO v_role, v_fleet_id, v_balance
          FROM user_account
         WHERE user_id = v_user_id
           FOR UPDATE;

        IF v_role = 'fleet' AND v_fleet_id IS NOT NULL THEN
            SELECT status, credit_limit, credit_used
              INTO v_fleet_st, v_credit_limit, v_credit_used
              FROM fleet_account
             WHERE fleet_id = v_fleet_id
               FOR UPDATE;

            IF v_fleet_st IS NULL OR v_fleet_st <> 'ACTIVE' OR v_credit_used + NEW.amount > v_credit_limit THEN
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '车队可用授信额度不足或车队状态异常';
            END IF;
        ELSE
            IF v_balance IS NULL OR v_balance < NEW.amount THEN
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '账户余额不足，请充值或更换支付方式';
            END IF;
        END IF;
    END IF;
END$$

CREATE TRIGGER trg_payment_after_insert
AFTER INSERT ON payment_record
FOR EACH ROW
BEGIN
    DECLARE v_user_id  BIGINT UNSIGNED;
    DECLARE v_total    DECIMAL(10,2);
    DECLARE v_paid     DECIMAL(10,2);
    DECLARE v_status   VARCHAR(20);
    DECLARE v_new_paid DECIMAL(10,2);
    DECLARE v_role     VARCHAR(20);
    DECLARE v_fleet_id BIGINT UNSIGNED;

    IF NEW.status <> 'FAILED' THEN
        SELECT user_id, total_amount, paid_amount, status
          INTO v_user_id, v_total, v_paid, v_status
          FROM charge_order
         WHERE order_id = NEW.order_id
           FOR UPDATE;

        SET v_new_paid = v_paid + NEW.amount;

        UPDATE charge_order
           SET paid_amount = v_new_paid,
               status = CASE
                            WHEN v_new_paid >= total_amount THEN 'PAID'
                            WHEN v_new_paid <= 0 AND NEW.amount < 0 THEN 'REFUNDED'
                            WHEN NEW.amount < 0 AND v_status = 'PAID' THEN 'PAID'
                            ELSE 'FINISHED'
                        END
         WHERE order_id = NEW.order_id;

        IF NEW.method = 'BALANCE' AND COALESCE(@skip_balance_sync, 0) = 0 THEN
            SELECT role, fleet_id
              INTO v_role, v_fleet_id
              FROM user_account
             WHERE user_id = v_user_id
               FOR UPDATE;

            IF v_role = 'fleet' AND v_fleet_id IS NOT NULL THEN
                UPDATE fleet_account
                   SET credit_used = GREATEST(0.00, credit_used + NEW.amount)
                 WHERE fleet_id = v_fleet_id;
            ELSE
                UPDATE user_account
                   SET balance = balance - NEW.amount
                 WHERE user_id = v_user_id;
            END IF;
        END IF;
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
    IN p_month       CHAR(7) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
    IN p_platform_rate DECIMAL(5,4)
)
BEGIN
    DECLARE v_rate DECIMAL(5,4);

    SET v_rate = COALESCE(p_platform_rate, 0.0500);

    -- 只汇总本运营商的场站，一个站一张单。本月没有已付订单的站也建一张 0 元单，
    -- 否则这些站的欠费差异没有账单可挂。按实收净额（paid_amount）结算。
    INSERT INTO settlement_bill
        (operator_id, station_id, bill_month, order_count, total_kwh,
         total_amount, platform_fee, payable_amount, status)
    SELECT p_operator_id,
           s.station_id,
           p_month,
           COALESCE(agg.cnt, 0),
           COALESCE(agg.kwh, 0),
           COALESCE(agg.amt, 0),
           ROUND(COALESCE(agg.amt, 0) * v_rate, 2),
           COALESCE(agg.amt, 0) - ROUND(COALESCE(agg.amt, 0) * v_rate, 2),
           'DRAFT'
      FROM station s
      LEFT JOIN (
            SELECT o.station_id,
                   COUNT(*)           AS cnt,
                   SUM(o.kwh)         AS kwh,
                   SUM(o.paid_amount) AS amt
              FROM charge_order o
             WHERE DATE_FORMAT(o.start_time, '%Y-%m') = p_month
               AND o.status = 'PAID'
             GROUP BY o.station_id
      ) agg ON agg.station_id = s.station_id
     WHERE s.operator_id = p_operator_id
       AND (agg.station_id IS NOT NULL
            OR EXISTS (SELECT 1 FROM charge_order o2
                        WHERE o2.station_id = s.station_id
                          AND DATE_FORMAT(o2.start_time, '%Y-%m') = p_month))
    ON DUPLICATE KEY UPDATE
        order_count    = IF(status = 'DRAFT', VALUES(order_count), order_count),
        total_kwh      = IF(status = 'DRAFT', VALUES(total_kwh), total_kwh),
        total_amount   = IF(status = 'DRAFT', VALUES(total_amount), total_amount),
        platform_fee   = IF(status = 'DRAFT', VALUES(platform_fee), platform_fee),
        payable_amount = IF(status = 'DRAFT', VALUES(payable_amount), payable_amount);

    -- 当月没结清的钱记成待处理差异：actual - expect 为负，表示还差多少（排除因退款导致的差额）
    INSERT INTO reconcile_diff
        (bill_id, order_id, diff_type, expect_amount, actual_amount, diff_amount, remark)
    SELECT b.bill_id,
           o.order_id,
           'UNPAID',
           o.total_amount,
           o.paid_amount,
           o.paid_amount - o.total_amount,
           '订单未结清'
      FROM charge_order o
      JOIN station s
        ON s.station_id = o.station_id
       AND s.operator_id = p_operator_id
      JOIN settlement_bill b
        ON b.operator_id = p_operator_id
       AND b.station_key = o.station_id
       AND b.bill_month = p_month
     WHERE DATE_FORMAT(o.start_time, '%Y-%m') = p_month
       AND o.status = 'FINISHED'
       AND o.total_amount > o.paid_amount
       AND NOT EXISTS (SELECT 1 FROM payment_record pr
                        WHERE pr.order_id = o.order_id
                          AND pr.amount < 0
                          AND pr.status = 'REFUNDED')
       AND NOT EXISTS (SELECT 1 FROM reconcile_diff d
                        WHERE d.bill_id = b.bill_id
                          AND d.order_id = o.order_id
                          AND d.diff_type = 'UNPAID');
END$$
DELIMITER ;

DROP TRIGGER IF EXISTS trg_invoice_guard;
DROP TRIGGER IF EXISTS trg_invoice_after_insert;
DELIMITER $$
CREATE TRIGGER trg_invoice_guard
BEFORE INSERT ON invoice_record
FOR EACH ROW
BEGIN
    DECLARE v_payable  DECIMAL(12,2);
    DECLARE v_bill_st  VARCHAR(20);
    DECLARE v_invoiced DECIMAL(12,2);
    DECLARE v_orig_amt DECIMAL(12,2);
    DECLARE v_orig_st  VARCHAR(20);
    DECLARE v_orig_bill BIGINT;
    DECLARE v_orig_flush BIGINT;

    IF NEW.amount = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '开票金额不能为 0';
    END IF;

    SELECT payable_amount, status INTO v_payable, v_bill_st
      FROM settlement_bill
     WHERE bill_id = NEW.bill_id
       FOR UPDATE;

    IF v_payable IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '账单不存在';
    END IF;
    IF v_bill_st = 'CLOSED' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '账单已关单，禁止开票或红冲';
    END IF;
    IF v_bill_st NOT IN ('CONFIRMED', 'INVOICED') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '只有已确认或已开票账单才能开票或红冲，请先确认账单';
    END IF;

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
        -- 锁住原票：两笔并发红冲都读到 ISSUED 的话，已开票额会被冲成负数
        -- 这里不能加 FOR UPDATE：触发器里锁自己这张表会报 1442，
        -- 并发保护由 DAO 的 SELECT ... FOR UPDATE 负责。
        SELECT amount, status, bill_id, red_flush_of
          INTO v_orig_amt, v_orig_st, v_orig_bill, v_orig_flush
          FROM invoice_record
         WHERE invoice_id = NEW.red_flush_of;

        IF v_orig_amt IS NULL THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '被红冲的发票不存在';
        END IF;
        IF v_orig_bill <> NEW.bill_id THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '红冲票必须开在原票所属的账单上';
        END IF;
        IF v_orig_flush IS NOT NULL THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '红冲票本身不能再被红冲';
        END IF;
        IF v_orig_st <> 'ISSUED' THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '只有已开具的发票才能红冲';
        END IF;
        IF NEW.amount > 0 OR -NEW.amount > v_orig_amt THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '红冲金额必须为负且不超过原票金额';
        END IF;
        -- 注意：MySQL 不允许在 invoice_record 的触发器里再改 invoice_record，
        -- 所以原票状态由调用方（DAO）插入红冲票后补一条 UPDATE 置为 RED_FLUSHED。
    END IF;
END$$

CREATE TRIGGER trg_invoice_after_insert
AFTER INSERT ON invoice_record
FOR EACH ROW
BEGIN
    DECLARE v_net_invoiced DECIMAL(12,2);

    SELECT COALESCE(SUM(amount), 0) INTO v_net_invoiced
      FROM invoice_record
     WHERE bill_id = NEW.bill_id;

    IF v_net_invoiced > 0 THEN
        UPDATE settlement_bill
           SET status = 'INVOICED'
         WHERE bill_id = NEW.bill_id
           AND status = 'CONFIRMED';
    ELSE
        UPDATE settlement_bill
           SET status = 'CONFIRMED'
         WHERE bill_id = NEW.bill_id
           AND status = 'INVOICED';
    END IF;
END$$
DELIMITER ;

-- ============================================================================
-- 【业务线 3】设备监控与工单 —— 触发器与存储过程（负责人：成员 3 / 代润森）
-- ============================================================================

DROP TRIGGER IF EXISTS trg_repair_order_before_insert_active;
DROP TRIGGER IF EXISTS trg_repair_order_before_update_active;
DROP PROCEDURE IF EXISTS proc_escalate_overdue_alarms;

DELIMITER //

-- ----------------------------------------------------------------------------
-- 触发器 3.1：同一告警禁止并存多张处理中维修工单（保留 1:N 但限制同时只开一张）
-- ----------------------------------------------------------------------------
CREATE TRIGGER trg_repair_order_before_insert_active
BEFORE INSERT ON repair_order
FOR EACH ROW
BEGIN
    IF COALESCE(@skip_repair_order_guard, 0) = 0
       AND NEW.status <> 'CLOSED'
       AND EXISTS (
        SELECT 1
          FROM repair_order
         WHERE alarm_id = NEW.alarm_id
           AND status <> 'CLOSED'
    ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '同一告警已存在处理中的维修工单，禁止重复派单';
    END IF;
END //

-- ----------------------------------------------------------------------------
-- 触发器 3.2：更新时防止把已关闭工单重新打开或换到已有活动工单的告警
-- ----------------------------------------------------------------------------
CREATE TRIGGER trg_repair_order_before_update_active
BEFORE UPDATE ON repair_order
FOR EACH ROW
BEGIN
    IF COALESCE(@skip_repair_order_guard, 0) = 0
       AND (NEW.alarm_id <> OLD.alarm_id OR OLD.status = 'CLOSED')
       AND NEW.status <> 'CLOSED'
       AND EXISTS (
           SELECT 1
             FROM repair_order
            WHERE alarm_id = NEW.alarm_id
              AND work_order_id <> OLD.work_order_id
              AND status <> 'CLOSED'
       ) THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = '目标告警已存在处理中的维修工单，禁止重新打开';
    END IF;
END //

-- ----------------------------------------------------------------------------
-- 存储过程 3.1：告警 SLA 超时未响应自动升级
-- 说明：MySQL 触发器无法由时间流逝自动触发，故使用存储过程 + 定时任务（EVENT 或
--       外部调度）执行；L1 最紧急(15min)，超时升级方向为 L4 -> L3 -> L2 -> L1。
-- 调用方式：CALL proc_escalate_overdue_alarms(NOW(), @upgraded);
-- ----------------------------------------------------------------------------
CREATE PROCEDURE proc_escalate_overdue_alarms (
    IN  p_check_time      DATETIME,
    OUT p_escalated_count INT
)
BEGIN
    DECLARE v_now DATETIME;

    SET v_now = COALESCE(p_check_time, NOW());

    -- 先把旧等级的时限映射为新等级的时限，再改 alarm_level；避免 MySQL SET 左到右
    -- 求值导致 response_limit_min 读取到已经变更后的等级而错配一档。
    UPDATE alarm_record
       SET response_limit_min = CASE alarm_level
                WHEN 'L4' THEN 120
                WHEN 'L3' THEN 30
                WHEN 'L2' THEN 15
                ELSE response_limit_min
           END,
           alarm_level = CASE alarm_level
                WHEN 'L4' THEN 'L3'
                WHEN 'L3' THEN 'L2'
                WHEN 'L2' THEN 'L1'
                ELSE alarm_level
           END,
           escalated_count = escalated_count + 1,
           escalated_at    = v_now,
           updated_at      = v_now
     WHERE status IN ('ACTIVE', 'PROCESSING')
       AND alarm_level <> 'L1'
       AND TIMESTAMPDIFF(MINUTE, occurred_at, v_now) > response_limit_min
       AND NOT EXISTS (
           SELECT 1
             FROM repair_order AS r
            WHERE r.alarm_id = alarm_record.alarm_id
              AND r.status IN ('ACCEPTED', 'ARRIVED', 'REPAIRED', 'VERIFIED')
       );

    SET p_escalated_count = ROW_COUNT();
END //

DELIMITER ;

-- ============================================================================
-- 【业务线 4】用户与会员 —— 触发器与存储过程（负责人：成员 4 / 李海波）
-- 设计说明：
--   1. 发券与核销的业务规则全部下沉到数据库：活动有效期/投放状态、券量上限、
--      每人限领、场站适用性、订单归属与使用门槛，均由触发器在写入瞬间校验，
--      Python 侧只负责组装参数，避免“应用层校验”与“直接改库”两条口径不一致；
--   2. 券状态（ISSUED -> REDEEMED）由核销触发器自动回写，保证券表与核销表永不脱节；
--   3. 核销一次性完成“写核销记录 + 回写券状态 + 抵扣订单金额”，整段处于同一事务；
--   4. 初始化脚本可置 @skip_coupon_guard = 1 跳过校验批量造数（与线 1 的
--      @skip_auto_status_log 同一约定），业务运行期该变量为空，校验始终生效。
-- ============================================================================

DROP TRIGGER IF EXISTS trg_coupon_issue_guard;
DROP TRIGGER IF EXISTS trg_coupon_issued_count;
DROP TRIGGER IF EXISTS trg_coupon_redeem_guard;
DROP TRIGGER IF EXISTS trg_coupon_redeem_after;
DROP PROCEDURE IF EXISTS sp_issue_coupon_pack;
DROP PROCEDURE IF EXISTS sp_coupon_redeem;

DELIMITER $$

-- ----------------------------------------------------------------------------
-- 触发器 4.1：发券守卫（活动投放中、有效期内、券量未超发、每人限领、面额快照一致）
-- ----------------------------------------------------------------------------
CREATE TRIGGER trg_coupon_issue_guard
BEFORE INSERT ON coupon_record
FOR EACH ROW
BEGIN
    DECLARE v_status VARCHAR(20);
    DECLARE v_from   DATE;
    DECLARE v_to     DATE;
    DECLARE v_total  INT UNSIGNED;
    DECLARE v_issued INT DEFAULT 0;
    DECLARE v_limit  TINYINT UNSIGNED;
    DECLARE v_face   DECIMAL(8,2);
    DECLARE v_thresh DECIMAL(8,2);
    DECLARE v_got    INT DEFAULT 0;

    IF COALESCE(@skip_coupon_guard, 0) = 0 THEN
        -- 锁住活动行：并发发券时串行化，防止 issued_count 超发
        SELECT status, valid_from, valid_to, issue_total, issued_count, per_user_limit,
               face_value, threshold_amount
          INTO v_status, v_from, v_to, v_total, v_issued, v_limit, v_face, v_thresh
          FROM coupon_campaign
         WHERE campaign_id = NEW.campaign_id
           FOR UPDATE;

        IF v_status IS NULL THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '优惠活动不存在，无法发券';
        END IF;
        IF v_status <> 'ACTIVE' THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '优惠活动未在投放中，不能发券';
        END IF;
        IF CURDATE() < v_from OR CURDATE() > v_to THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '当前不在活动有效期内，不能发券';
        END IF;
        IF v_issued >= v_total THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '活动券量已发完，不能继续发放';
        END IF;
        IF NEW.expire_at > v_to THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '券到期日不得晚于活动失效日';
        END IF;
        IF NEW.face_value <> v_face THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '券面额与活动配置不一致';
        END IF;
        IF NEW.threshold_amount <> v_thresh THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '券使用门槛与活动配置不一致';
        END IF;
        IF NEW.expire_at < CURDATE() THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '券到期日不得早于今天，否则发放即失效';
        END IF;

        SELECT COUNT(*) INTO v_got
          FROM coupon_record
         WHERE user_id = NEW.user_id
           AND campaign_id = NEW.campaign_id
           AND status <> 'VOID';
        IF v_got >= v_limit THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '该用户已达到本活动限领张数';
        END IF;
    END IF;
END$$

-- ----------------------------------------------------------------------------
-- 触发器 4.2：发放量自动累加（与 4.1 同事务，供活动看板统计核销率）
-- ----------------------------------------------------------------------------
CREATE TRIGGER trg_coupon_issued_count
AFTER INSERT ON coupon_record
FOR EACH ROW
BEGIN
    IF COALESCE(@skip_coupon_guard, 0) = 0 THEN
        UPDATE coupon_campaign
           SET issued_count = issued_count + 1
         WHERE campaign_id = NEW.campaign_id;
    END IF;
END$$

-- ----------------------------------------------------------------------------
-- 触发器 4.3：核销守卫（券归属、未核销、未过期、活动投放中、场站适用、门槛达标、
--             订单归属本人且已结束、抵扣不超过券面额与订单金额）
-- ----------------------------------------------------------------------------
CREATE TRIGGER trg_coupon_redeem_guard
BEFORE INSERT ON coupon_redeem_record
FOR EACH ROW
BEGIN
    DECLARE v_owner    BIGINT UNSIGNED;
    DECLARE v_status   VARCHAR(20);
    DECLARE v_expire   DATE;
    DECLARE v_face     DECIMAL(8,2);
    DECLARE v_thresh   DECIMAL(8,2);
    DECLARE v_campaign BIGINT UNSIGNED;
    DECLARE v_c_status VARCHAR(20);
    DECLARE v_c_from   DATE;
    DECLARE v_c_to     DATE;
    DECLARE v_c_station BIGINT UNSIGNED;
    DECLARE v_o_owner  BIGINT UNSIGNED;
    DECLARE v_o_status VARCHAR(20);
    DECLARE v_o_paid   DECIMAL(12,2);
    DECLARE v_o_station BIGINT UNSIGNED;
    DECLARE v_o_total  DECIMAL(10,2);

    IF COALESCE(@skip_coupon_guard, 0) = 0 THEN
        SELECT user_id, status, expire_at, face_value, threshold_amount, campaign_id
          INTO v_owner, v_status, v_expire, v_face, v_thresh, v_campaign
          FROM coupon_record
         WHERE coupon_id = NEW.coupon_id
           FOR UPDATE;

        IF v_owner IS NULL THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '优惠券不存在';
        END IF;
        IF v_owner <> NEW.user_id THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '该券不属于当前用户，禁止核销';
        END IF;
        IF v_status <> 'ISSUED' THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '该券已核销或已作废，不能重复核销';
        END IF;
        IF v_expire < CURDATE() THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '优惠券已过期';
        END IF;

        SELECT status, valid_from, valid_to, applicable_station_id
          INTO v_c_status, v_c_from, v_c_to, v_c_station
          FROM coupon_campaign
         WHERE campaign_id = v_campaign;
        IF v_c_status <> 'ACTIVE' THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '所属优惠活动已停止投放，不能核销';
        END IF;
        IF CURDATE() < v_c_from OR CURDATE() > v_c_to THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '当前不在活动有效期内，不能核销';
        END IF;
        IF v_c_station IS NOT NULL AND v_c_station <> NEW.station_id THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '该券仅限指定场站使用';
        END IF;

        -- 锁住订单行，避免与支付/退款并发把金额改乱
        SELECT user_id, status, paid_amount, station_id, total_amount
          INTO v_o_owner, v_o_status, v_o_paid, v_o_station, v_o_total
          FROM charge_order
         WHERE order_id = NEW.order_id
           FOR UPDATE;
        IF v_o_owner IS NULL THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '核销关联的充电订单不存在';
        END IF;
        IF v_o_owner <> NEW.user_id THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单不属于当前用户，禁止核销';
        END IF;
        -- 只允许“已结束且未支付”的订单用券：已支付/已取消/已退款订单一律拒绝，
        -- 否则会把已付订单的应付金额改成负数，形成“平台倒欠用户”的账并污染月结
        IF v_o_status <> 'FINISHED' THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '仅已结束且未支付的订单可用券抵扣，当前订单状态不可核销';
        END IF;
        IF v_o_paid <> 0.00 THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单已支付或已部分支付，不能再用券抵扣';
        END IF;
        -- 不信任调用方传入的场站与金额，必须与订单实际值一致，防止伪造抵扣
        IF NEW.station_id <> v_o_station THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '核销场站与订单所属场站不一致';
        END IF;
        IF NEW.order_amount <> v_o_total THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '核销时订单金额与订单实际金额不一致';
        END IF;

        IF NEW.order_amount < v_thresh THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单金额未达到优惠券使用门槛';
        END IF;
        IF NEW.face_value > v_face THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '抵扣金额超过券面额';
        END IF;
        IF NEW.face_value > NEW.order_amount THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '抵扣金额不能超过订单金额';
        END IF;
    END IF;
END$$

-- ----------------------------------------------------------------------------
-- 触发器 4.4：核销成功后自动把券置为 REDEEMED（券表状态永远跟随核销表）
-- 说明：
--   1. 初始化脚本若用 INSERT ... SELECT ... FROM coupon_record 批量造核销数据，
--      MySQL 会因“外层语句正在读 coupon_record”而拒绝触发器再写该表（ERROR 1442），
--      故批量造数期间统一置 @skip_coupon_guard = 1 跳过本触发器；
--   2. 正常业务路径（单行 INSERT / 存储过程）不受影响；即便券已是 REDEEMED，
--      这里也先比对再更新，避免无谓写入与自增锁。
-- ----------------------------------------------------------------------------
CREATE TRIGGER trg_coupon_redeem_after
AFTER INSERT ON coupon_redeem_record
FOR EACH ROW
BEGIN
    DECLARE v_status VARCHAR(20);

    IF COALESCE(@skip_coupon_guard, 0) = 0 THEN
        SELECT status INTO v_status
          FROM coupon_record
         WHERE coupon_id = NEW.coupon_id;

        IF v_status IS NOT NULL AND v_status <> 'REDEEMED' THEN
            UPDATE coupon_record
               SET status = 'REDEEMED'
             WHERE coupon_id = NEW.coupon_id;
        END IF;
    END IF;
END$$

-- ----------------------------------------------------------------------------
-- 存储过程 4.1：批量发券（新人券包 / 运营定向投放），逐张过 4.1 触发器校验
-- 说明：
--   1. p_issued_by 记录发券管理员，便于审计“哪张券是谁发的”；
--   2. p_batch 是本次发券的批次前缀，券码 = 批次(8) + UUID 片段(24)，
--      DAO 用它在本事务内按前缀回查本次生成的券码，避免 “commit 后按 LIMIT 回查”
--      在并发发券时取到别人的券（竞态）。
-- 调用方式：CALL sp_issue_coupon_pack(9001, 1, 2, 'NEW_USER_PACK', 1, 'B7C1A2D3', @issued);
-- ----------------------------------------------------------------------------
CREATE PROCEDURE sp_issue_coupon_pack(
    IN  p_user_id     BIGINT UNSIGNED,
    IN  p_campaign_id BIGINT UNSIGNED,
    IN  p_count       TINYINT UNSIGNED,
    -- 显式声明表列的字符集与排序规则：过程参数默认继承“建库时的库默认排序规则”，
    -- 若测试库用 utf8mb4_0900_ai_ci 而表是 utf8mb4_unicode_ci，字符串比对会报 1267
    IN  p_channel     VARCHAR(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
    IN  p_issued_by   BIGINT UNSIGNED,
    IN  p_batch       CHAR(8)     CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
    OUT p_issued      INT
)
BEGIN
    DECLARE v_i        TINYINT UNSIGNED DEFAULT 0;
    DECLARE v_valid_to DATE;
    DECLARE v_face     DECIMAL(8,2);
    DECLARE v_thresh   DECIMAL(8,2);
    DECLARE v_channel  VARCHAR(20);

    IF p_count IS NULL OR p_count < 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '发券张数必须大于 0';
    END IF;
    IF p_batch IS NULL OR CHAR_LENGTH(p_batch) <> 8 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '发券批次号必须为 8 位字符';
    END IF;

    SELECT valid_to, face_value, threshold_amount
      INTO v_valid_to, v_face, v_thresh
      FROM coupon_campaign
     WHERE campaign_id = p_campaign_id;
    IF v_face IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '优惠活动不存在';
    END IF;

    SET v_channel = COALESCE(p_channel, 'OPS_GRANT');
    SET p_issued  = 0;

    WHILE v_i < p_count DO
        -- 券码：批次前缀(8) + 24 位无连字符 UUID，共 32 位，满足 VARCHAR(32) 与唯一索引
        INSERT INTO coupon_record
            (coupon_no, campaign_id, user_id, face_value, threshold_amount,
             issue_channel, issued_by, expire_at, status)
        VALUES
            (CONCAT(p_batch, SUBSTRING(REPLACE(UUID(), '-', ''), 1, 24)),
             p_campaign_id, p_user_id, v_face, v_thresh,
             v_channel, p_issued_by, v_valid_to, 'ISSUED');
        SET v_i      = v_i + 1;
        SET p_issued = p_issued + 1;
    END WHILE;
END$$

-- ----------------------------------------------------------------------------
-- 存储过程 4.2：优惠券核销（写核销记录 -> 触发器回写券状态 -> 抵扣订单金额）
-- 说明：
--   1. 全程对 charge_order 加 FOR UPDATE 行锁，抵扣基于锁内读到的真实金额，
--      并在同一事务内把“折前/折后应付”通过 OUT 参数交回调用方，避免 commit 后再回查；
--   2. 只允许“已结束且未支付”的订单（触发器会二次校验），抵扣后若应付变为 0
--      则直接置为 PAID，避免留下“金额为 0 却待支付”的僵尸订单；
--   3. p_user_id 用于校验“当前登录人就是订单车主”，防止拿着别人的券码核销他人订单。
-- 调用方式：CALL sp_coupon_redeem('CPxxxx', 10001, 9001, @discount, @before, @after);
-- ----------------------------------------------------------------------------
CREATE PROCEDURE sp_coupon_redeem(
    -- 券码参数显式指定表列的排序规则，避免与 coupon_record.coupon_no 比对时报 1267
    IN  p_coupon_no VARCHAR(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
    IN  p_order_id  BIGINT,
    IN  p_user_id   BIGINT UNSIGNED,
    OUT p_discount     DECIMAL(10,2),
    OUT p_total_before DECIMAL(10,2),
    OUT p_total_after  DECIMAL(10,2)
)
BEGIN
    DECLARE v_coupon_id  BIGINT UNSIGNED;
    DECLARE v_face       DECIMAL(8,2);
    DECLARE v_order_user BIGINT UNSIGNED;
    DECLARE v_station    BIGINT UNSIGNED;
    DECLARE v_amount     DECIMAL(10,2);
    DECLARE v_paid       DECIMAL(10,2);
    DECLARE v_status     VARCHAR(20);
    DECLARE v_discount   DECIMAL(10,2);

    SELECT coupon_id, face_value
      INTO v_coupon_id, v_face
      FROM coupon_record
     WHERE coupon_no = p_coupon_no
       FOR UPDATE;
    IF v_coupon_id IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '券码不存在';
    END IF;

    SELECT user_id, station_id, COALESCE(total_amount, 0.00),
           COALESCE(paid_amount, 0.00), status
      INTO v_order_user, v_station, v_amount, v_paid, v_status
      FROM charge_order
     WHERE order_id = p_order_id
       FOR UPDATE;
    IF v_order_user IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '充电订单不存在';
    END IF;
    IF p_user_id IS NOT NULL AND v_order_user <> p_user_id THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '只能核销本人订单上的优惠券';
    END IF;
    IF v_status <> 'FINISHED' OR v_paid <> 0.00 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '仅已结束且未支付的订单可用券抵扣';
    END IF;
    IF v_amount <= 0.00 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '订单金额为 0，无法使用优惠券';
    END IF;

    SET v_discount = LEAST(v_face, v_amount);

    INSERT INTO coupon_redeem_record
        (coupon_id, order_id, user_id, station_id, face_value, order_amount)
    VALUES
        (v_coupon_id, p_order_id, v_order_user, v_station, v_discount, v_amount);

    SET p_total_before = v_amount;
    SET p_total_after  = GREATEST(v_amount - v_discount, 0.00);

    UPDATE charge_order
       SET discount_amount = discount_amount + v_discount,
           total_amount    = p_total_after,
           status          = CASE WHEN p_total_after = 0.00 THEN 'PAID' ELSE status END
     WHERE order_id = p_order_id;

    SET p_discount = v_discount;
END$$

DELIMITER ;

-- ============================================================================
-- 业务线 4 结束
-- ============================================================================


-- 账单与发票不允许删除，只能红冲或作废，防止绕过对账留痕
DROP TRIGGER IF EXISTS trg_bill_no_delete;
DELIMITER $$
CREATE TRIGGER trg_bill_no_delete
BEFORE DELETE ON settlement_bill
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '结算账单不允许物理删除';
END$$
DELIMITER ;

DROP TRIGGER IF EXISTS trg_invoice_no_delete;
DELIMITER $$
CREATE TRIGGER trg_invoice_no_delete
BEFORE DELETE ON invoice_record
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '发票记录不允许物理删除，请用红冲';
END$$
DELIMITER ;
