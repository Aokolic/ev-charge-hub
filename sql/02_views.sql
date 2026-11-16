-- ============================================================================
-- 02. RBAC 角色隔离与脱敏视图（每条业务线 >= 3 个视图）
-- 铁律：
--   1. 原始物理表禁止直接暴露给前端，列表/看板查询统一通过本文件中的视图完成；
--   2. 严格按 5 类角色（driver / fleet / ops / settlement / admin）裁剪列与行；
--   3. 视图可重复执行（CREATE OR REPLACE VIEW），依赖 sql/01_ddl.sql 先建表。
-- ============================================================================

USE ev_charge_hub;
SET NAMES utf8mb4;

-- ============================================================================
-- 【业务线 1】场站与充电设施隔离视图（负责人：成员 1 / 组长，共 5 个视图）
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 视图 1.1：v_driver_station_overview（面向 driver / fleet）
-- 隔离边界：
--   - 行过滤：仅显示营业中场站(OPEN)、正常合作运营商(ACTIVE)及已验收桩(PASSED)下属枪数；
--   - 列裁剪：隐藏信用代码、运营商联系人/客服电话、通信协议、质保截止、心跳阈值等内部信息。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_driver_station_overview AS
SELECT
    s.station_id,
    s.station_code,
    s.station_name,
    o.operator_name,
    s.address,
    s.longitude,
    s.latitude,
    s.business_hours,
    s.parking_fee_rate,
    s.total_spaces,
    s.available_spaces,
    COALESCE(pa.active_pile_count, 0)         AS active_pile_count,
    COALESCE(ga.total_gun_count, 0)           AS total_gun_count,
    COALESCE(ga.idle_gun_count, 0)            AS idle_gun_count,
    COALESCE(ga.idle_fast_gun_count, 0)       AS idle_fast_gun_count,
    COALESCE(ga.idle_slow_gun_count, 0)       AS idle_slow_gun_count
FROM station AS s
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
LEFT JOIN (
    SELECT
        p.station_id,
        COUNT(*) AS active_pile_count
    FROM charging_pile AS p
    WHERE p.acceptance_status = 'PASSED'
    GROUP BY p.station_id
) AS pa
    ON pa.station_id = s.station_id
LEFT JOIN (
    SELECT
        p.station_id,
        COUNT(g.gun_id)                                                            AS total_gun_count,
        SUM(CASE WHEN g.current_status = 'IDLE' THEN 1 ELSE 0 END)                 AS idle_gun_count,
        SUM(CASE WHEN g.current_status = 'IDLE' AND g.gun_type = 'DC_FAST' THEN 1 ELSE 0 END) AS idle_fast_gun_count,
        SUM(CASE WHEN g.current_status = 'IDLE' AND g.gun_type = 'AC_SLOW' THEN 1 ELSE 0 END) AS idle_slow_gun_count
    FROM charging_pile AS p
    INNER JOIN charging_gun AS g
        ON g.pile_id = p.pile_id
    WHERE p.acceptance_status = 'PASSED'
    GROUP BY p.station_id
) AS ga
    ON ga.station_id = s.station_id
WHERE s.station_status = 'OPEN'
  AND o.status = 'ACTIVE';


-- ----------------------------------------------------------------------------
-- 视图 1.2：v_driver_gun_availability（面向 driver / fleet）
-- 隔离边界：
--   - 车主扫码/场站详情查枪专用，仅放行营业场站内已验收桩名下的充电枪；
--   - 仅输出枪型、接口标准、最大功率与可用状态，屏蔽设备型号、协议、心跳及质保字段。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_driver_gun_availability AS
SELECT
    g.gun_id,
    g.gun_code,
    g.gun_seq,
    g.gun_type,
    g.connector_standard,
    g.max_power,
    g.current_status AS gun_status,
    p.pile_id,
    p.pile_code,
    p.rated_power,
    p.current_status AS pile_status,
    s.station_id,
    s.station_code,
    s.station_name,
    s.business_hours
FROM charging_gun AS g
INNER JOIN charging_pile AS p
    ON p.pile_id = g.pile_id
INNER JOIN station AS s
    ON s.station_id = p.station_id
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
WHERE s.station_status = 'OPEN'
  AND o.status = 'ACTIVE'
  AND p.acceptance_status = 'PASSED';


-- ----------------------------------------------------------------------------
-- 视图 1.3：v_fault_gun_detail（面向 ops / admin）
-- 隔离边界：
--   - 仅限场站运维与管理员查看，用于故障枪与离线桩定位（driver / fleet / settlement 禁查）；
--   - 使用 LEFT JOIN charging_gun，确保已离线或故障但尚未配枪的桩同样纳入排障清单。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_fault_gun_detail AS
SELECT
    o.operator_id,
    o.operator_name,
    o.service_phone,
    s.station_id,
    s.station_code,
    s.station_name,
    s.address,
    p.pile_id,
    p.pile_code,
    p.device_model,
    p.protocol_type,
    p.current_status AS pile_status,
    p.offline_threshold_sec,
    p.last_heartbeat_at,
    CASE
        WHEN p.last_heartbeat_at IS NULL THEN NULL
        ELSE TIMESTAMPDIFF(SECOND, p.last_heartbeat_at, NOW())
    END              AS heartbeat_lag_sec,
    p.status_updated_at AS pile_status_updated_at,
    g.gun_id,
    g.gun_code,
    g.gun_seq,
    g.gun_type,
    g.connector_standard,
    g.max_power,
    g.current_status AS gun_status,
    g.status_updated_at AS gun_status_updated_at
FROM charging_pile AS p
INNER JOIN station AS s
    ON s.station_id = p.station_id
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
LEFT JOIN charging_gun AS g
    ON g.pile_id = p.pile_id
WHERE p.current_status IN ('FAULT', 'OFFLINE')
   OR g.current_status IN ('FAULT', 'OFFLINE');


-- ----------------------------------------------------------------------------
-- 视图 1.4：v_device_status_history（面向 ops / admin）
-- 隔离边界：
--   - 仅限运维与管理员审计设备状态变更轨迹及处理离线待办（todo_status = 'PENDING'）；
--   - 按 pile_id / gun_id 双分支 LEFT JOIN 无损还原所属桩、枪及场站信息。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_device_status_history AS
SELECT
    l.log_id,
    s.station_id,
    s.station_code,
    s.station_name,
    l.device_type,
    COALESCE(p_direct.pile_id, p_gun.pile_id)     AS pile_id,
    COALESCE(p_direct.pile_code, p_gun.pile_code) AS pile_code,
    l.gun_id,
    g.gun_code,
    g.gun_seq,
    l.prev_status,
    l.new_status,
    l.change_source,
    l.todo_status,
    l.remark,
    l.changed_at
FROM device_status_log AS l
LEFT JOIN charging_pile AS p_direct
    ON l.device_type = 'PILE'
   AND l.pile_id = p_direct.pile_id
LEFT JOIN charging_gun AS g
    ON l.device_type = 'GUN'
   AND l.gun_id = g.gun_id
LEFT JOIN charging_pile AS p_gun
    ON g.pile_id = p_gun.pile_id
INNER JOIN station AS s
    ON s.station_id = COALESCE(p_direct.station_id, p_gun.station_id);


-- ----------------------------------------------------------------------------
-- 视图 1.5：v_station_realtime_status（面向 admin / settlement）
-- 隔离边界：
--   - 平台运营管理员全局资产看板，兼供结算专员只读核对“运营商-场站”归属；
--   - 桩与枪先分别按 station_id 预聚合后再连接，杜绝 1:N 连接导致额定总功率被重复累加。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_station_realtime_status AS
SELECT
    s.station_id,
    s.station_code,
    s.station_name,
    s.station_status,
    o.operator_id,
    o.operator_code,
    o.operator_name,
    o.status                                   AS operator_status,
    s.address,
    s.business_hours,
    s.parking_fee_rate,
    s.total_spaces,
    s.available_spaces,
    COALESCE(pa.total_piles, 0)                AS total_piles,
    COALESCE(pa.pending_acceptance_piles, 0)   AS pending_acceptance_piles,
    COALESCE(pa.idle_piles, 0)                 AS idle_piles,
    COALESCE(pa.occupied_piles, 0)             AS occupied_piles,
    COALESCE(pa.fault_piles, 0)                AS fault_piles,
    COALESCE(pa.offline_piles, 0)              AS offline_piles,
    CAST(COALESCE(pa.total_rated_power, 0.00) AS DECIMAL(12, 2)) AS total_rated_power,
    COALESCE(ga.total_guns, 0)                 AS total_guns,
    COALESCE(ga.idle_guns, 0)                  AS idle_guns,
    COALESCE(ga.occupied_guns, 0)              AS occupied_guns,
    COALESCE(ga.fault_guns, 0)                 AS fault_guns,
    COALESCE(ga.offline_guns, 0)               AS offline_guns,
    s.updated_at
FROM station AS s
INNER JOIN operator AS o
    ON o.operator_id = s.operator_id
LEFT JOIN (
    SELECT
        p.station_id,
        COUNT(*)                                                                 AS total_piles,
        SUM(CASE WHEN p.acceptance_status = 'PENDING' THEN 1 ELSE 0 END)         AS pending_acceptance_piles,
        SUM(CASE WHEN p.current_status = 'IDLE' THEN 1 ELSE 0 END)               AS idle_piles,
        SUM(CASE WHEN p.current_status = 'OCCUPIED' THEN 1 ELSE 0 END)           AS occupied_piles,
        SUM(CASE WHEN p.current_status = 'FAULT' THEN 1 ELSE 0 END)              AS fault_piles,
        SUM(CASE WHEN p.current_status = 'OFFLINE' THEN 1 ELSE 0 END)            AS offline_piles,
        SUM(p.rated_power)                                                       AS total_rated_power
    FROM charging_pile AS p
    GROUP BY p.station_id
) AS pa
    ON pa.station_id = s.station_id
LEFT JOIN (
    SELECT
        p.station_id,
        COUNT(g.gun_id)                                                          AS total_guns,
        SUM(CASE WHEN g.current_status = 'IDLE' THEN 1 ELSE 0 END)               AS idle_guns,
        SUM(CASE WHEN g.current_status = 'OCCUPIED' THEN 1 ELSE 0 END)           AS occupied_guns,
        SUM(CASE WHEN g.current_status = 'FAULT' THEN 1 ELSE 0 END)              AS fault_guns,
        SUM(CASE WHEN g.current_status = 'OFFLINE' THEN 1 ELSE 0 END)            AS offline_guns
    FROM charging_pile AS p
    INNER JOIN charging_gun AS g
        ON g.pile_id = p.pile_id
    GROUP BY p.station_id
) AS ga
    ON ga.station_id = s.station_id;


-- ============================================================================
-- 【业务线 2 ~ 业务线 5】视图占位区（各负责成员按需追加，每线 >= 3 个视图）
-- ============================================================================
