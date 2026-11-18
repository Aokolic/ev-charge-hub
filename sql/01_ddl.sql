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
-- 【业务线 2】分时计费规则、充电订单与支付退款（负责人：成员 2 / 孙墨涵）
-- 说明：依赖 station(station_id)、charging_gun(gun_id)、user_account(user_id)
--       四张表：电价规则、充电订单、分时费用明细、支付流水
--       station_id / gun_id / user_id / vehicle_id 先只留 BIGINT UNSIGNED 列，
--       跨线外键统一放在文件最后一段，等线 1、线 4 建完表再加，避免顺序耦合。
-- ============================================================================

-- 电价规则：一个站、一个时段（峰/平/谷）、一段有效期，对应一条单价
DROP TABLE IF EXISTS order_fee_detail;
DROP TABLE IF EXISTS payment_record;
DROP TABLE IF EXISTS charge_order;
DROP TABLE IF EXISTS pricing_rule;

CREATE TABLE pricing_rule (
    rule_id        BIGINT       NOT NULL AUTO_INCREMENT COMMENT '规则ID',
    station_id     BIGINT UNSIGNED       NOT NULL COMMENT '所属场站（线1）',
    period         ENUM('PEAK','FLAT','VALLEY') NOT NULL COMMENT '峰/平/谷',
    start_time     TIME         NOT NULL COMMENT '时段开始，允许跨零点',
    end_time       TIME         NOT NULL COMMENT '时段结束',
    energy_price   DECIMAL(8,4) NOT NULL COMMENT '电费单价 元/度',
    service_price  DECIMAL(8,4) NOT NULL COMMENT '服务费单价 元/度',
    free_minutes   INT          NOT NULL DEFAULT 0 COMMENT '免费分钟数，先充够这个再计费',
    min_charge     DECIMAL(8,2) NOT NULL DEFAULT 0.00 COMMENT '起步价',
    effective_from DATE         NOT NULL COMMENT '生效日期',
    effective_to   DATE         NULL COMMENT '失效日期，空表示长期',
    is_active      TINYINT(1)   NOT NULL DEFAULT 1,
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (rule_id),
    UNIQUE KEY uk_rule_slot (station_id, period, start_time, end_time, effective_from),
    KEY idx_rule_station (station_id, is_active),
    CONSTRAINT ck_rule_price CHECK (energy_price >= 0 AND service_price >= 0 AND min_charge >= 0),
    CONSTRAINT ck_rule_time  CHECK (start_time <> end_time),
    CONSTRAINT ck_rule_date  CHECK (effective_to IS NULL OR effective_to >= effective_from)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分时电价规则（业务线2）';

-- 充电订单：一次充电一行，钱先记 0，结束时由 sp_order_calc 算出来
CREATE TABLE charge_order (
    order_id        BIGINT       NOT NULL AUTO_INCREMENT,
    order_no        VARCHAR(32)  NOT NULL COMMENT '对外单号',
    user_id         BIGINT UNSIGNED       NOT NULL COMMENT '车主（线4）',
    vehicle_id      BIGINT UNSIGNED       NULL COMMENT '车辆（线4），扫码充电可能不带车',
    gun_id          BIGINT UNSIGNED       NOT NULL COMMENT '充电枪（线1）',
    station_id      BIGINT UNSIGNED       NOT NULL COMMENT '场站（线1），冗余一份省得每次join',
    start_time      DATETIME     NOT NULL,
    end_time        DATETIME     NULL,
    start_soc       DECIMAL(5,2) NULL COMMENT '开始电量百分比',
    end_soc         DECIMAL(5,2) NULL,
    kwh             DECIMAL(10,3) NOT NULL DEFAULT 0.000 COMMENT '充了多少度',
    energy_fee      DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    service_fee     DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    discount_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    total_amount    DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '应付',
    paid_amount     DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '已付，退款会冲减',
    status          ENUM('CHARGING','FINISHED','PAID','CANCELLED','REFUNDED') NOT NULL DEFAULT 'CHARGING',
    -- 充电中的单子把 gun_id 暴露出来，配合唯一索引挡住“同一把枪两个进行中订单”
    active_gun_id   BIGINT GENERATED ALWAYS AS (CASE WHEN status = 'CHARGING' THEN gun_id END) STORED,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (order_id),
    UNIQUE KEY uk_order_no (order_no),
    UNIQUE KEY uk_gun_active (active_gun_id),
    KEY idx_order_user (user_id, start_time),
    KEY idx_order_station (station_id, start_time),
    CONSTRAINT ck_order_time CHECK (end_time IS NULL OR end_time > start_time),
    CONSTRAINT ck_order_kwh  CHECK (kwh >= 0),
    CONSTRAINT ck_order_soc  CHECK ((start_soc IS NULL OR start_soc BETWEEN 0 AND 100)
                                AND (end_soc   IS NULL OR end_soc   BETWEEN 0 AND 100)),
    CONSTRAINT ck_order_money CHECK (energy_fee >= 0 AND service_fee >= 0
                                 AND discount_amount >= 0 AND total_amount >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='充电订单（业务线2）';

-- 费用明细：一单跨峰平谷就拆成多行，单价存快照，以后调价不影响老单
CREATE TABLE order_fee_detail (
    detail_id     BIGINT       NOT NULL AUTO_INCREMENT,
    order_id      BIGINT       NOT NULL,
    period        ENUM('PEAK','FLAT','VALLEY') NOT NULL,
    rule_id       BIGINT       NULL COMMENT '命中哪条电价规则，留个痕迹',
    begin_time    DATETIME     NOT NULL,
    end_time      DATETIME     NOT NULL,
    kwh           DECIMAL(10,3) NOT NULL,
    energy_price  DECIMAL(8,4) NOT NULL COMMENT '下单时的电价快照',
    service_price DECIMAL(8,4) NOT NULL,
    energy_fee    DECIMAL(10,2) NOT NULL,
    service_fee   DECIMAL(10,2) NOT NULL,
    PRIMARY KEY (detail_id),
    UNIQUE KEY uk_detail_seg (order_id, begin_time),
    KEY idx_detail_order (order_id),
    CONSTRAINT fk_detail_order FOREIGN KEY (order_id) REFERENCES charge_order (order_id) ON DELETE CASCADE,
    CONSTRAINT ck_detail_time CHECK (end_time > begin_time),
    CONSTRAINT ck_detail_kwh  CHECK (kwh >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='分时费用明细（业务线2）';

-- 支付流水：一单可以付多次，退款记负数，txn_no 唯一防回调重复入账
CREATE TABLE payment_record (
    payment_id BIGINT       NOT NULL AUTO_INCREMENT,
    order_id   BIGINT       NOT NULL,
    amount     DECIMAL(10,2) NOT NULL COMMENT '负数表示退款',
    method     ENUM('WECHAT','ALIPAY','BALANCE','CASH') NOT NULL,
    txn_no     VARCHAR(64)  NOT NULL COMMENT '第三方流水号，幂等用',
    status     ENUM('SUCCESS','FAILED','REFUNDED') NOT NULL DEFAULT 'SUCCESS',
    paid_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    remark     VARCHAR(200) NULL,
    PRIMARY KEY (payment_id),
    UNIQUE KEY uk_payment_txn (txn_no),
    KEY idx_payment_order (order_id),
    CONSTRAINT fk_payment_order FOREIGN KEY (order_id) REFERENCES charge_order (order_id) ON DELETE CASCADE,
    CONSTRAINT ck_payment_amount CHECK (amount <> 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='支付流水（业务线2）';


-- ============================================================================
-- 【业务线 3】设备运行遥测（分区表）、告警与维修工单（负责人：成员 3 / 代润森，待补充）
-- 说明：依赖 charging_pile(pile_id)、charging_gun(gun_id)、user_account(user_id)
-- ============================================================================


-- ============================================================================
-- 【业务线 5】运营商月结账单、对账差异与开票红冲（负责人：成员 2 / 孙墨涵）
-- 说明：依赖 operator(operator_id)、station(station_id)、charge_order(order_id)
--       data flow：settle 只读线2 的订单，写自己的三张表
-- ============================================================================

DROP TABLE IF EXISTS invoice_record;
DROP TABLE IF EXISTS reconcile_diff;
DROP TABLE IF EXISTS settlement_bill;

-- 月度结算单：一个运营商（或一个站）一个月只能有一张
CREATE TABLE settlement_bill (
    bill_id        BIGINT       NOT NULL AUTO_INCREMENT,
    operator_id    BIGINT UNSIGNED       NOT NULL COMMENT '运营商（线1 若有表则挂外键）',
    station_id     BIGINT UNSIGNED       NULL COMMENT '按站结算时填，按运营商结算留空',
    bill_month     CHAR(7)      NOT NULL COMMENT 'YYYY-MM',
    order_count    INT          NOT NULL DEFAULT 0,
    total_kwh      DECIMAL(12,3) NOT NULL DEFAULT 0.000,
    total_amount   DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '订单总额',
    platform_fee   DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '平台抽成',
    payable_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '应打给运营商',
    status         ENUM('DRAFT','CONFIRMED','INVOICED','CLOSED') NOT NULL DEFAULT 'DRAFT',
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (bill_id),
    UNIQUE KEY uk_bill_month (operator_id, station_id, bill_month),
    CONSTRAINT ck_bill_money CHECK (total_amount >= 0 AND platform_fee >= 0 AND payable_amount >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='月度结算单（业务线5）';

-- 对账差异：账单和订单对不上时记一行，指回具体订单
CREATE TABLE reconcile_diff (
    diff_id       BIGINT       NOT NULL AUTO_INCREMENT,
    bill_id       BIGINT       NOT NULL,
    order_id      BIGINT       NULL COMMENT '能定位到单就填，聚合类差异可空',
    diff_type     ENUM('MISSING_ORDER','AMOUNT_MISMATCH','DUPLICATE_PAY','UNPAID') NOT NULL,
    expect_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    actual_amount DECIMAL(12,2) NOT NULL DEFAULT 0.00,
    diff_amount   DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '实际减应得',
    resolved      TINYINT(1)   NOT NULL DEFAULT 0,
    remark        VARCHAR(200) NULL,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (diff_id),
    KEY idx_diff_bill (bill_id, resolved),
    CONSTRAINT fk_diff_bill FOREIGN KEY (bill_id) REFERENCES settlement_bill (bill_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='对账差异（业务线5）';

-- 开票记录：红冲不删原票，另开一张负数票，用 red_flush_of 指回去
CREATE TABLE invoice_record (
    invoice_id   BIGINT       NOT NULL AUTO_INCREMENT,
    bill_id      BIGINT       NOT NULL,
    invoice_no   VARCHAR(32)  NOT NULL,
    amount       DECIMAL(12,2) NOT NULL COMMENT '红冲票为负数',
    invoice_type ENUM('NORMAL','SPECIAL') NOT NULL DEFAULT 'NORMAL',
    status       ENUM('ISSUED','RED_FLUSHED') NOT NULL DEFAULT 'ISSUED',
    red_flush_of BIGINT       NULL COMMENT '红冲的是哪张票',
    issued_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (invoice_id),
    UNIQUE KEY uk_invoice_no (invoice_no),
    KEY idx_invoice_bill (bill_id),
    CONSTRAINT fk_invoice_bill FOREIGN KEY (bill_id) REFERENCES settlement_bill (bill_id),
    CONSTRAINT fk_invoice_red FOREIGN KEY (red_flush_of) REFERENCES invoice_record (invoice_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='开票记录（业务线5）';

-- ============================================================
-- 跨线外键：等线1（station / charging_gun）、线4（user_account / vehicle）建完表再执行
-- 类型必须都是 BIGINT UNSIGNED，名字不一致就改这里
-- ============================================================
-- ALTER TABLE charge_order
--     ADD CONSTRAINT fk_order_station FOREIGN KEY (station_id) REFERENCES station (station_id),
--     ADD CONSTRAINT fk_order_gun     FOREIGN KEY (gun_id)     REFERENCES charging_gun (gun_id),
--     ADD CONSTRAINT fk_order_user    FOREIGN KEY (user_id)    REFERENCES user_account (user_id),
--     ADD CONSTRAINT fk_order_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicle (vehicle_id);
