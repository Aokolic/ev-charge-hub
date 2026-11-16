-- ============================================================================
-- 01. 全局建表脚本（物理结构 DDL）
-- 说明：
--   1. 按外键依赖顺序组织：业务线1（场站设施）/ 业务线4（用户车队） -> 业务线2（订单计费） -> 业务线3（监控工单）/ 业务线5（结算对账）
--   2. 编码与引擎：统一 InnoDB + utf8mb4 + utf8mb4_unicode_ci
--   3. 铁律：所有金额、电价、电量、功率、经纬度均采用 DECIMAL，严禁使用 FLOAT/DOUBLE
-- ============================================================================

CREATE DATABASE IF NOT EXISTS ev_charge_hub
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE ev_charge_hub;
SET NAMES utf8mb4;

-- ============================================================================
-- 【业务线 1】场站与充电设施（负责人：成员 1 / 组长）
-- 包含表：operator -> station -> charging_pile -> charging_gun -> device_status_log
-- ============================================================================

-- 1.1 运营商档案表
CREATE TABLE IF NOT EXISTS operator (
    operator_id     BIGINT UNSIGNED                NOT NULL AUTO_INCREMENT COMMENT '运营商主键ID',
    operator_code   VARCHAR(32)                    NOT NULL                COMMENT '运营商业务编码（如 OP-BJ-001）',
    operator_name   VARCHAR(100)                   NOT NULL                COMMENT '运营商全称',
    credit_code     CHAR(18)                       NOT NULL                COMMENT '统一社会信用代码（18位）',
    contact_name    VARCHAR(50)                    NOT NULL                COMMENT '业务对接联系人',
    service_phone   VARCHAR(20)                    NOT NULL                COMMENT '企业客服热线',
    status          ENUM('ACTIVE', 'SUSPENDED')    NOT NULL DEFAULT 'ACTIVE' COMMENT '合作状态：ACTIVE-正常合作, SUSPENDED-暂停',
    created_at      DATETIME                       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    updated_at      DATETIME                       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (operator_id),
    UNIQUE KEY uk_operator_code (operator_code),
    UNIQUE KEY uk_operator_credit_code (credit_code),
    KEY idx_operator_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='业务线1-运营商档案表';

-- 1.2 充电场站表
CREATE TABLE IF NOT EXISTS station (
    station_id        BIGINT UNSIGNED                          NOT NULL AUTO_INCREMENT COMMENT '场站主键ID',
    operator_id       BIGINT UNSIGNED                          NOT NULL                COMMENT '所属运营商ID',
    station_code      VARCHAR(32)                              NOT NULL                COMMENT '场站唯一编号（如 ST-BJ-001）',
    station_name      VARCHAR(120)                             NOT NULL                COMMENT '场站名称',
    address           VARCHAR(255)                             NOT NULL                COMMENT '场站详细地址',
    longitude         DECIMAL(9, 6)                            NOT NULL                COMMENT '经度（-180.000000 ~ 180.000000）',
    latitude          DECIMAL(8, 6)                            NOT NULL                COMMENT '纬度（-90.000000 ~ 90.000000）',
    total_spaces      INT UNSIGNED                             NOT NULL                COMMENT '车位总数',
    available_spaces  INT UNSIGNED                             NOT NULL                COMMENT '当前空闲车位数',
    business_hours    VARCHAR(64)                              NOT NULL DEFAULT '00:00-24:00' COMMENT '营业时间描述（如 00:00-24:00）',
    parking_fee_rate  DECIMAL(8, 2)                            NOT NULL DEFAULT 0.00   COMMENT '停车费标准（元/小时）',
    station_status    ENUM('OPEN', 'MAINTENANCE', 'CLOSED')    NOT NULL DEFAULT 'OPEN' COMMENT '场站状态：OPEN-营业中, MAINTENANCE-维护中, CLOSED-已关闭',
    created_at        DATETIME                                 NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    updated_at        DATETIME                                 NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (station_id),
    UNIQUE KEY uk_station_code (station_code),
    KEY idx_station_operator_status (operator_id, station_status),
    CONSTRAINT fk_station_operator
        FOREIGN KEY (operator_id) REFERENCES operator (operator_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT chk_station_lng CHECK (longitude BETWEEN -180.000000 AND 180.000000),
    CONSTRAINT chk_station_lat CHECK (latitude BETWEEN -90.000000 AND 90.000000),
    CONSTRAINT chk_station_total_spaces CHECK (total_spaces > 0),
    CONSTRAINT chk_station_avail_spaces CHECK (available_spaces <= total_spaces),
    CONSTRAINT chk_station_parking_fee CHECK (parking_fee_rate >= 0.00)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='业务线1-充电场站表';

-- 1.3 充电桩表
CREATE TABLE IF NOT EXISTS charging_pile (
    pile_id                BIGINT UNSIGNED                                    NOT NULL AUTO_INCREMENT COMMENT '充电桩主键ID',
    station_id             BIGINT UNSIGNED                                    NOT NULL                COMMENT '所属场站ID',
    pile_code              VARCHAR(32)                                        NOT NULL                COMMENT '充电桩唯一编号（如 PL-001-01）',
    device_model           VARCHAR(64)                                        NOT NULL                COMMENT '设备型号（现场铭牌核对项）',
    rated_power            DECIMAL(8, 2)                                      NOT NULL                COMMENT '额定总功率（kW）',
    gun_count              TINYINT UNSIGNED                                   NOT NULL                COMMENT '设计配备充电枪数量（1~8）',
    protocol_type          VARCHAR(32)                                        NOT NULL DEFAULT 'OCPP1.6' COMMENT '通信协议（如 OCPP1.6 / 云快充1.5）',
    commissioned_date      DATE                                               NULL                    COMMENT '投运日期（验收投运前可为空）',
    warranty_end_date      DATE                                               NOT NULL                COMMENT '质保截止日期',
    acceptance_status      ENUM('PENDING', 'PASSED', 'REJECTED')              NOT NULL DEFAULT 'PENDING' COMMENT '协议联调与验收状态：PENDING-待验收, PASSED-已通过, REJECTED-未通过',
    current_status         ENUM('IDLE', 'OCCUPIED', 'FAULT', 'OFFLINE')       NOT NULL DEFAULT 'OFFLINE' COMMENT '当前状态：IDLE-空闲, OCCUPIED-占用, FAULT-故障, OFFLINE-离线',
    offline_threshold_sec  INT UNSIGNED                                       NOT NULL DEFAULT 180    COMMENT '心跳离线判定阈值（秒，最低30秒）',
    last_heartbeat_at      DATETIME                                           NULL                    COMMENT '最近一次心跳上报时间',
    status_updated_at      DATETIME                                           NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最近一次状态变更时间',
    created_at             DATETIME                                           NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    updated_at             DATETIME                                           NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (pile_id),
    UNIQUE KEY uk_pile_code (pile_code),
    KEY idx_pile_station_status (station_id, acceptance_status, current_status, rated_power),
    KEY idx_pile_heartbeat_status (current_status, last_heartbeat_at),
    CONSTRAINT fk_pile_station
        FOREIGN KEY (station_id) REFERENCES station (station_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT chk_pile_rated_power CHECK (rated_power > 0.00),
    CONSTRAINT chk_pile_gun_count CHECK (gun_count BETWEEN 1 AND 8),
    CONSTRAINT chk_pile_offline_sec CHECK (offline_threshold_sec >= 30),
    CONSTRAINT chk_pile_warranty_date CHECK (commissioned_date IS NULL OR warranty_end_date >= commissioned_date),
    CONSTRAINT chk_pile_acceptance_online CHECK (acceptance_status = 'PASSED' OR current_status NOT IN ('IDLE', 'OCCUPIED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='业务线1-充电桩表';

-- 1.4 充电枪表（独立实体，订单与计费最小粒度；单枪功率<=所属桩额定功率由触发器校验）
CREATE TABLE IF NOT EXISTS charging_gun (
    gun_id              BIGINT UNSIGNED                              NOT NULL AUTO_INCREMENT COMMENT '充电枪主键ID',
    pile_id             BIGINT UNSIGNED                              NOT NULL                COMMENT '所属充电桩ID',
    gun_code            VARCHAR(32)                                  NOT NULL                COMMENT '充电枪唯一编号（如 GN-001-01-A）',
    gun_seq             TINYINT UNSIGNED                             NOT NULL                COMMENT '桩内枪序号（从1开始）',
    gun_type            ENUM('DC_FAST', 'AC_SLOW')                   NOT NULL                COMMENT '枪型：DC_FAST-直流快充, AC_SLOW-交流慢充',
    connector_standard  VARCHAR(32)                                  NOT NULL                COMMENT '接口标准（如 GB/T 20234.3 / GB/T 20234.2）',
    max_power           DECIMAL(8, 2)                                NOT NULL                COMMENT '单枪最大输出功率（kW，须<=所属桩额定功率）',
    current_status      ENUM('IDLE', 'OCCUPIED', 'FAULT', 'OFFLINE') NOT NULL DEFAULT 'OFFLINE' COMMENT '当前状态：IDLE-空闲, OCCUPIED-占用, FAULT-故障, OFFLINE-离线',
    status_updated_at   DATETIME                                     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '最近一次状态变更时间',
    created_at          DATETIME                                     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    updated_at          DATETIME                                     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (gun_id),
    UNIQUE KEY uk_gun_code (gun_code),
    UNIQUE KEY uk_gun_pile_seq (pile_id, gun_seq),
    KEY idx_gun_status_type (current_status, gun_type),
    CONSTRAINT fk_gun_pile
        FOREIGN KEY (pile_id) REFERENCES charging_pile (pile_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT chk_gun_seq CHECK (gun_seq >= 1),
    CONSTRAINT chk_gun_max_power CHECK (max_power > 0.00)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='业务线1-充电枪表';

-- 1.5 设备状态变更日志表（pile_id 与 gun_id 互斥二选一，消除传递依赖并保留状态变更轨迹）
CREATE TABLE IF NOT EXISTS device_status_log (
    log_id         BIGINT UNSIGNED                                                    NOT NULL AUTO_INCREMENT COMMENT '状态日志主键ID',
    device_type    ENUM('PILE', 'GUN')                                                NOT NULL                COMMENT '变更对象类型：PILE-充电桩, GUN-充电枪',
    pile_id        BIGINT UNSIGNED                                                    NULL                    COMMENT '关联充电桩ID（仅 device_type=PILE 时非空）',
    gun_id         BIGINT UNSIGNED                                                    NULL                    COMMENT '关联充电枪ID（仅 device_type=GUN 时非空）',
    prev_status    ENUM('IDLE', 'OCCUPIED', 'FAULT', 'OFFLINE')                       NULL                    COMMENT '变更前状态（首次建档初始化允许为空）',
    new_status     ENUM('IDLE', 'OCCUPIED', 'FAULT', 'OFFLINE')                       NOT NULL                COMMENT '变更后状态',
    change_source  ENUM('INIT', 'ACCEPTANCE', 'ORDER', 'HEARTBEAT', 'MANUAL', 'ALARM') NOT NULL DEFAULT 'MANUAL' COMMENT '变更触发来源',
    todo_status    ENUM('NONE', 'PENDING', 'DONE')                                    NOT NULL DEFAULT 'NONE' COMMENT '运维待办状态：NONE-无需处理, PENDING-待处理, DONE-已办结',
    remark         VARCHAR(255)                                                       NULL                    COMMENT '变更说明或现场铭牌核对备注',
    changed_at     DATETIME                                                           NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '状态变更发生时间',
    PRIMARY KEY (log_id),
    KEY idx_status_log_type_time_status (device_type, changed_at, new_status, todo_status),
    KEY idx_status_log_pile_time (pile_id, changed_at),
    KEY idx_status_log_gun_time (gun_id, changed_at),
    KEY idx_status_log_todo_time (todo_status, changed_at),
    CONSTRAINT fk_status_log_pile
        FOREIGN KEY (pile_id) REFERENCES charging_pile (pile_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_status_log_gun
        FOREIGN KEY (gun_id) REFERENCES charging_gun (gun_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT chk_status_log_target CHECK (
        (device_type = 'PILE' AND pile_id IS NOT NULL AND gun_id IS NULL)
        OR
        (device_type = 'GUN' AND pile_id IS NULL AND gun_id IS NOT NULL)
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='业务线1-设备状态变更日志表';


-- ============================================================================
-- 【业务线 4】用户账号、车辆、车队与优惠券（负责人：成员 4 / 李海波，待补充）
-- 说明：须优先于业务线2建表；user_account 字段须与 app/dao/common.py 契约对齐
-- ============================================================================


-- ============================================================================
-- 【业务线 2】分时计费规则、充电订单与支付退款（负责人：成员 2 / 孙墨涵，待补充）
-- 说明：依赖 station(station_id)、charging_gun(gun_id)、user_account(user_id)
-- ============================================================================


-- ============================================================================
-- 【业务线 3】设备运行遥测（分区表）、告警与维修工单（负责人：成员 3 / 代润森，待补充）
-- 说明：依赖 charging_pile(pile_id)、charging_gun(gun_id)、user_account(user_id)
-- ============================================================================


-- ============================================================================
-- 【业务线 5】运营商月结账单、对账差异与开票红冲（负责人：成员 2 / 孙墨涵，待补充）
-- 说明：依赖 operator(operator_id)、station(station_id)、charge_order(order_id)
-- ============================================================================
