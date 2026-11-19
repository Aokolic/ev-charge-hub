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
      AND p.current_status IN ('IDLE', 'OCCUPIED')
    GROUP BY p.station_id
) AS pa
    ON pa.station_id = s.station_id
LEFT JOIN (
    SELECT
        p.station_id,
        COUNT(g.gun_id)                                                                         AS total_gun_count,
        SUM(CASE WHEN g.current_status = 'IDLE' THEN 1 ELSE 0 END)                              AS idle_gun_count,
        SUM(CASE WHEN g.current_status = 'IDLE' AND g.gun_type = 'DC_FAST' THEN 1 ELSE 0 END)   AS idle_fast_gun_count,
        SUM(CASE WHEN g.current_status = 'IDLE' AND g.gun_type = 'AC_SLOW' THEN 1 ELSE 0 END)   AS idle_slow_gun_count
    FROM charging_pile AS p
    INNER JOIN charging_gun AS g
        ON g.pile_id = p.pile_id
    WHERE p.acceptance_status = 'PASSED'
      AND p.current_status IN ('IDLE', 'OCCUPIED')
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
  AND p.acceptance_status = 'PASSED'
  AND p.current_status IN ('IDLE', 'OCCUPIED');


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
WHERE p.acceptance_status = 'PASSED'
  AND (
      p.current_status IN ('FAULT', 'OFFLINE')
      OR g.current_status IN ('FAULT', 'OFFLINE')
  );


-- ----------------------------------------------------------------------------
-- 视图 1.4：v_device_status_history（面向 ops / admin）
-- 隔离边界：
--   - 仅限运维与管理员审计设备状态变更轨迹及处理离线待办（todo_status = 'PENDING'）；
--   - 采用 UNION ALL 分别关联桩/枪外键分支，避免 COALESCE 连接导致索引失效。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_device_status_history AS
SELECT
    l.log_id,
    s.station_id,
    s.station_code,
    s.station_name,
    l.device_type,
    p.pile_id,
    p.pile_code,
    l.gun_id,
    NULL                        AS gun_code,
    NULL                        AS gun_seq,
    l.prev_status,
    l.new_status,
    l.change_source,
    l.todo_status,
    l.remark,
    l.changed_at
FROM device_status_log AS l
INNER JOIN charging_pile AS p
    ON p.pile_id = l.pile_id
INNER JOIN station AS s
    ON s.station_id = p.station_id
WHERE l.device_type = 'PILE'
  AND l.pile_id IS NOT NULL

UNION ALL

SELECT
    l.log_id,
    s.station_id,
    s.station_code,
    s.station_name,
    l.device_type,
    p.pile_id,
    p.pile_code,
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
INNER JOIN charging_gun AS g
    ON g.gun_id = l.gun_id
INNER JOIN charging_pile AS p
    ON p.pile_id = g.pile_id
INNER JOIN station AS s
    ON s.station_id = p.station_id
WHERE l.device_type = 'GUN'
  AND l.gun_id IS NOT NULL;


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
-- 【业务线 2】订单计费隔离视图（负责人：成员 2 / 孙墨涵，共 3 个）
-- ============================================================================

-- ============================================================
-- 业务线 2：订单计费（孙墨涵 / 02-develop）
-- ============================================================

-- 车主视角：自己的订单，去掉内部主键和冗余字段
CREATE OR REPLACE VIEW v_order_owner AS
SELECT o.order_id,
       o.order_no,
       o.user_id,
       o.station_id,
       o.gun_id,
       o.start_time,
       o.end_time,
       o.kwh,
       o.total_amount,
       o.paid_amount,
       o.total_amount - o.paid_amount AS unpaid_amount,
       o.status,
       CASE o.status
            WHEN 'CHARGING' THEN '充电中'
            WHEN 'FINISHED' THEN '待支付'
            WHEN 'PAID'     THEN '已支付'
            WHEN 'CANCELLED' THEN '已取消'
            WHEN 'REFUNDED' THEN '已退款'
       END AS status_text
FROM charge_order o;

-- 站内视角：运维看本站当天每把枪的情况
CREATE OR REPLACE VIEW v_order_station_daily AS
SELECT DATE(o.start_time) AS stat_date,
       o.station_id,
       o.gun_id,
       COUNT(*) AS order_cnt,
       SUM(CASE WHEN o.status = 'PAID' THEN 1 ELSE 0 END) AS paid_cnt,
       SUM(o.kwh)           AS total_kwh,
       SUM(o.energy_fee)    AS energy_fee,
       SUM(o.service_fee)   AS service_fee,
       SUM(o.total_amount)  AS total_amount
FROM charge_order o
GROUP BY DATE(o.start_time), o.station_id, o.gun_id;

-- 分时结构视角：峰平谷各卖了多少电、平均单价多少，用来验证分时规则真的生效了
CREATE OR REPLACE VIEW v_order_period_stat AS
SELECT d.period,
       DATE(d.begin_time) AS stat_date,
       COUNT(DISTINCT d.order_id) AS order_cnt,
       SUM(d.kwh)         AS total_kwh,
       SUM(d.energy_fee)  AS energy_fee,
       SUM(d.service_fee) AS service_fee,
       ROUND(SUM(d.energy_fee + d.service_fee) / NULLIF(SUM(d.kwh), 0), 4) AS avg_price
FROM order_fee_detail d
GROUP BY d.period, DATE(d.begin_time);

-- ============================================================================
-- 【业务线 3】设备监控与工单隔离视图（负责人：成员 3 / 代润森，共 3 个视图）
-- 说明：三个视图均只供 ops / admin 使用；ops 的行级归属由 DAO 层叠加
--       maintainer_id 条件实现，driver / fleet / settlement 禁查本业务线视图。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 视图 3.1：v_monitor_realtime_status（面向 ops / admin）
-- 隔离边界：仅暴露已验收桩的最新一条遥测，屏蔽遥测主键、未来分区细节等内部字段；
--           使用 ROW_NUMBER 取每桩最新采集，避免 GROUP BY + 回表造成全分区扫描。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_monitor_realtime_status AS
SELECT
    t.pile_id,
    s.station_id,
    s.station_code,
    s.station_name,
    p.pile_code,
    p.current_status                              AS pile_status,
    t.collected_at,
    t.output_voltage,
    t.output_current,
    t.power,
    t.module_temp,
    t.offline_duration_sec,
    t.fault_code,
    CASE
        WHEN p.last_heartbeat_at IS NULL THEN NULL
        ELSE TIMESTAMPDIFF(SECOND, p.last_heartbeat_at, NOW())
    END                                          AS heartbeat_lag_sec
FROM (
    SELECT
        pt.pile_id,
        pt.collected_at,
        pt.output_voltage,
        pt.output_current,
        pt.power,
        pt.module_temp,
        pt.offline_duration_sec,
        pt.fault_code,
        ROW_NUMBER() OVER (PARTITION BY pt.pile_id ORDER BY pt.collected_at DESC) AS rn
    FROM pile_telemetry AS pt
) AS t
INNER JOIN charging_pile AS p
    ON p.pile_id = t.pile_id
INNER JOIN station AS s
    ON s.station_id = p.station_id
WHERE t.rn = 1
  AND p.acceptance_status = 'PASSED';


-- ----------------------------------------------------------------------------
-- 视图 3.2：v_monitor_alarm_overview（面向 ops / admin）
-- 隔离边界：聚合最新派单维修人 latest_maintainer_id，供 ops 按本人隔离行级数据；
--           overdue_flag 标记超时未响应告警，供 DAO 与看板直接过滤。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_monitor_alarm_overview AS
SELECT
    a.alarm_id,
    a.pile_id,
    a.gun_id,
    a.alarm_code,
    a.alarm_level,
    a.original_level,
    a.response_limit_min,
    a.occurred_at,
    a.recovered_at,
    a.status,
    a.escalated_count,
    a.escalated_at,
    (
        SELECT r.maintainer_id
          FROM repair_order AS r
         WHERE r.alarm_id = a.alarm_id
         ORDER BY r.dispatched_at DESC, r.work_order_id DESC
         LIMIT 1
    )                                              AS latest_maintainer_id,
    CASE
        WHEN a.status IN ('ACTIVE', 'PROCESSING')
         AND TIMESTAMPDIFF(MINUTE, a.occurred_at, NOW()) > a.response_limit_min
         AND NOT EXISTS (
             SELECT 1
               FROM repair_order AS r
              WHERE r.alarm_id = a.alarm_id
                AND r.status IN ('ACCEPTED', 'ARRIVED', 'REPAIRED', 'VERIFIED', 'CLOSED')
         )
        THEN 1
        ELSE 0
    END                                            AS overdue_flag
FROM alarm_record AS a;


-- ----------------------------------------------------------------------------
-- 视图 3.3：v_monitor_workorder_closure（面向 ops / admin）
-- 隔离边界：以工单为单位输出全流程时间节点与响应时长，供 ops 按 maintainer_id
--           过滤本人数据，供 admin 做闭环率与 SLA 达标率统计。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_monitor_workorder_closure AS
SELECT
    r.work_order_id,
    r.alarm_id,
    r.maintainer_id,
    r.status,
    r.dispatched_at,
    r.accepted_at,
    r.arrived_at,
    r.repaired_at,
    r.verified_at,
    r.closed_at,
    r.replaced_parts,
    r.result,
    TIMESTAMPDIFF(MINUTE, a.occurred_at, COALESCE(r.accepted_at, NOW())) AS response_minutes,
    CASE
        WHEN r.status = 'DISPATCHED'
         AND TIMESTAMPDIFF(MINUTE, a.occurred_at, NOW()) > a.response_limit_min
        THEN 1
        ELSE 0
    END                                                                  AS overdue_flag
FROM repair_order AS r
INNER JOIN alarm_record AS a
    ON a.alarm_id = r.alarm_id;

-- ============================================================
-- 业务线 5：结算对账（孙墨涵 / 05-develop）
-- ============================================================

-- 账单总览：带已开票金额和未处理差异笔数，结算专员一眼看完
CREATE OR REPLACE VIEW v_settle_bill_detail AS
SELECT b.bill_id,
       b.operator_id,
       b.station_id,
       b.bill_month,
       b.order_count,
       b.total_kwh,
       b.total_amount,
       b.platform_fee,
       b.payable_amount,
       b.status,
       COALESCE(inv.invoiced_amount, 0) AS invoiced_amount,
       b.payable_amount - COALESCE(inv.invoiced_amount, 0) AS uninvoiced_amount,
       COALESCE(df.diff_cnt, 0) AS open_diff_cnt
FROM settlement_bill b
LEFT JOIN (SELECT bill_id, SUM(amount) AS invoiced_amount
           FROM invoice_record
           GROUP BY bill_id) inv ON inv.bill_id = b.bill_id
LEFT JOIN (SELECT bill_id, COUNT(*) AS diff_cnt
           FROM reconcile_diff
           WHERE resolved = 0
           GROUP BY bill_id) df ON df.bill_id = b.bill_id;

-- 月度订单池：按站按月汇总，生成结算单前先看这个数
CREATE OR REPLACE VIEW v_settle_order_month AS
SELECT DATE_FORMAT(o.start_time, '%Y-%m') AS bill_month,
       o.station_id,
       COUNT(*) AS order_cnt,
       SUM(o.kwh)          AS total_kwh,
       SUM(o.total_amount) AS total_amount,
       SUM(o.paid_amount)  AS paid_amount,
       SUM(o.total_amount - o.paid_amount) AS unpaid_amount
FROM charge_order o
WHERE o.status IN ('PAID', 'FINISHED')
GROUP BY DATE_FORMAT(o.start_time, '%Y-%m'), o.station_id;

-- 未处理差异：对账员的工作台
CREATE OR REPLACE VIEW v_settle_diff_open AS
SELECT d.diff_id,
       d.bill_id,
       b.bill_month,
       b.operator_id,
       d.order_id,
       d.diff_type,
       d.expect_amount,
       d.actual_amount,
       d.diff_amount,
       d.remark,
       d.created_at
FROM reconcile_diff d
JOIN settlement_bill b ON b.bill_id = d.bill_id
WHERE d.resolved = 0;

-- ============================================================================
-- 【业务线 4】用户、车辆与优惠券隔离视图（负责人：成员 4 / 李海波，共 5 个视图）
-- 公共口径：
--   1. 全部视图只输出 phone_masked / plate_masked 脱敏列，
--      密码哈希、手机号密文（phone_cipher）、盲索引（phone_hash）一律不出视图；
--   2. 行级归属由 DAO 层叠加参数条件实现：
--      driver -> user_id = 本人；fleet -> fleet_id = 本企业；admin / settlement -> 全量；
--   3. 券“可用”统一口径：status = 'ISSUED' 且 expire_at >= CURDATE()。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 视图 4.1：v_user_masked_profile（面向 driver / fleet 本人，admin / settlement 全量）
-- 隔离边界：隐藏登录口令哈希与手机号密文/盲索引，仅保留脱敏手机号与业务统计列。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_user_masked_profile AS
SELECT
    u.user_id,
    u.username,
    u.role,
    u.fleet_id,
    f.fleet_name,
    u.phone_masked,
    u.realname_status,
    u.member_level,
    u.balance,
    u.register_channel,
    u.status,
    u.last_login_at,
    u.created_at,
    COALESCE(v.vehicle_cnt, 0)            AS vehicle_cnt,
    COALESCE(c.usable_coupon_cnt, 0)      AS usable_coupon_cnt
FROM user_account AS u
LEFT JOIN fleet_account AS f
    ON f.fleet_id = u.fleet_id
LEFT JOIN (
    SELECT vv.user_id, COUNT(*) AS vehicle_cnt
    FROM vehicle AS vv
    WHERE vv.bind_status = 'BOUND'
    GROUP BY vv.user_id
) AS v
    ON v.user_id = u.user_id
LEFT JOIN (
    SELECT cc.user_id, COUNT(*) AS usable_coupon_cnt
    FROM coupon_record AS cc
    WHERE cc.status = 'ISSUED'
      AND cc.expire_at >= CURDATE()
    GROUP BY cc.user_id
) AS c
    ON c.user_id = u.user_id;


-- ----------------------------------------------------------------------------
-- 视图 4.2：v_vehicle_binding（面向 driver / fleet 本人，admin 全量核对）
-- 隔离边界：车牌只出脱敏值；输出当前车主与所属车队，便于识别“一车一主”是否被破坏。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_vehicle_binding AS
SELECT
    v.vehicle_id,
    v.user_id,
    u.username,
    v.fleet_id,
    f.fleet_name,
    v.plate_masked,
    v.plate_color,
    v.vehicle_model,
    v.battery_capacity_kwh,
    v.max_charge_power,
    v.supports_fast_charge,
    v.bind_status,
    v.bound_at,
    v.created_at
FROM vehicle AS v
LEFT JOIN user_account AS u
    ON u.user_id = v.user_id
LEFT JOIN fleet_account AS f
    ON f.fleet_id = v.fleet_id;


-- ----------------------------------------------------------------------------
-- 视图 4.3：v_coupon_user_wallet（面向 driver / fleet 本人的券包）
-- 隔离边界：只输出本人券的券码、面额快照、门槛、到期日与可用标记，隐藏发券操作人。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_coupon_user_wallet AS
SELECT
    c.coupon_id,
    c.coupon_no,
    c.user_id,
    cp.campaign_id,
    cp.campaign_name,
    cp.coupon_kind,
    cp.applicable_station_id,
    c.face_value,
    c.threshold_amount,
    c.issue_channel,
    c.issued_at,
    c.expire_at,
    c.status,
    cp.redeem_rule,
    CASE
        WHEN c.status = 'ISSUED' AND c.expire_at >= CURDATE() THEN 1
        ELSE 0
    END                                        AS usable_flag,
    GREATEST(DATEDIFF(c.expire_at, CURDATE()), 0) AS days_to_expire
FROM coupon_record AS c
INNER JOIN coupon_campaign AS cp
    ON cp.campaign_id = c.campaign_id;


-- ----------------------------------------------------------------------------
-- 视图 4.4：v_coupon_campaign_stat（面向 admin / settlement 的投放与核销看板）
-- 隔离边界：展示计划量、已发量、已核销量与核销率，供运营跟踪投放效果（driver / fleet 禁查）。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_coupon_campaign_stat AS
SELECT
    cp.campaign_id,
    cp.campaign_code,
    cp.campaign_name,
    cp.coupon_kind,
    cp.face_value,
    cp.threshold_amount,
    cp.applicable_station_id,
    s.station_name,
    cp.valid_from,
    cp.valid_to,
    cp.issue_total,
    cp.issued_count,
    cp.per_user_limit,
    cp.status,
    cp.redeem_rule,
    COALESCE(rd.redeemed_cnt, 0)                                    AS redeemed_cnt,
    CAST(COALESCE(rd.redeemed_amount, 0.00) AS DECIMAL(12, 2))      AS redeemed_amount,
    ROUND(COALESCE(rd.redeemed_cnt, 0) / NULLIF(cp.issued_count, 0), 4) AS redeem_rate
FROM coupon_campaign AS cp
LEFT JOIN station AS s
    ON s.station_id = cp.applicable_station_id
LEFT JOIN (
    SELECT c.campaign_id,
           COUNT(*)                    AS redeemed_cnt,
           SUM(d.face_value)           AS redeemed_amount
    FROM coupon_redeem_record AS d
    INNER JOIN coupon_record AS c
        ON c.coupon_id = d.coupon_id
    GROUP BY c.campaign_id
) AS rd
    ON rd.campaign_id = cp.campaign_id;


-- ----------------------------------------------------------------------------
-- 视图 4.5：v_fleet_credit_usage（面向 fleet 本企业，admin / settlement 全量）
-- 隔离边界：以车队为主体汇总绑定车辆数、在用账号数与本月充电金额，
--           便于按 fleet_id 隔离查看授信额度占用（driver / ops 禁查）。
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_fleet_credit_usage AS
SELECT
    f.fleet_id,
    f.fleet_code,
    f.fleet_name,
    f.contact_name,
    f.contact_phone_masked,
    f.status,
    f.credit_limit,
    f.credit_used,
    (f.credit_limit - f.credit_used)                                AS credit_remaining,
    COALESCE(v.vehicle_cnt, 0)                                      AS bound_vehicle_cnt,
    COALESCE(u.user_cnt, 0)                                         AS member_cnt,
    COALESCE(o.order_cnt, 0)                                        AS month_order_cnt,
    CAST(COALESCE(o.order_amount, 0.00) AS DECIMAL(12, 2))          AS month_order_amount
FROM fleet_account AS f
LEFT JOIN (
    SELECT fleet_id, COUNT(*) AS vehicle_cnt
    FROM vehicle
    WHERE bind_status = 'BOUND'
    GROUP BY fleet_id
) AS v
    ON v.fleet_id = f.fleet_id
LEFT JOIN (
    SELECT fleet_id, COUNT(*) AS user_cnt
    FROM user_account
    WHERE role = 'fleet'
    GROUP BY fleet_id
) AS u
    ON u.fleet_id = f.fleet_id
LEFT JOIN (
    SELECT ua.fleet_id,
           COUNT(*)              AS order_cnt,
           SUM(co.total_amount)  AS order_amount
    FROM charge_order AS co
    INNER JOIN user_account AS ua
        ON ua.user_id = co.user_id
    WHERE ua.fleet_id IS NOT NULL
      AND DATE_FORMAT(co.start_time, '%Y-%m') = DATE_FORMAT(CURDATE(), '%Y-%m')
    GROUP BY ua.fleet_id
) AS o
    ON o.fleet_id = f.fleet_id;
