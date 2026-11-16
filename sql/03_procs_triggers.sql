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

        SET @skip_auto_status_log = NULL,
            @status_change_source = NULL,
            @status_todo_status   = NULL,
            @status_change_remark = NULL;
    ELSE
        SET p_affected_guns = 0;
    END IF;

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


-- ============================================================================
-- 【业务线 2 ~ 业务线 5】存储过程与触发器占位区（各负责成员按需追加）
-- ============================================================================
