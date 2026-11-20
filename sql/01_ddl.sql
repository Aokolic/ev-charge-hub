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
-- 【业务线 4】用户、车辆、企业车队与优惠券（负责人：成员 4 / 李海波）
-- 说明：
--   1. 本段须优先于业务线 2 建表：charge_order.user_id / vehicle_id 逻辑引用本段；
--   2. user_account 的账号字段（username / password_hash / role / fleet_id /
--      phone_cipher / phone_hash / phone_masked / status / failed_login_count /
--      locked_until / last_login_at）须与 app/dao/common.py 的认证契约严格对齐；
--   3. 敏感信息三列分离：*_cipher 存 Fernet 密文、*_hash 存 HMAC-SHA256 盲索引
--      （支撑“按手机号/车牌等值检索”而不落明文）、*_masked 存脱敏展示值（供视图直出）；
--   4. 优惠券采用“发放记录 + 核销记录”两表分离：发放一行一张券，核销一行一次抵扣，
--      核销表对 coupon_id 与 order_id 各建唯一索引，从结构上杜绝重复核销与一单多券；
--   5. 删除顺序按外键依赖倒序排列，保证本脚本可反复重跑。
-- ============================================================================

-- 业务线3的 repair_order 引用 user_account，必须先于 user_account 删除，避免外键阻塞重跑
DROP TABLE IF EXISTS repair_order;
DROP TABLE IF EXISTS alarm_record;
DROP TABLE IF EXISTS pile_telemetry;

DROP TABLE IF EXISTS coupon_redeem_record;
DROP TABLE IF EXISTS coupon_record;
DROP TABLE IF EXISTS coupon_campaign;
DROP TABLE IF EXISTS vehicle;
DROP TABLE IF EXISTS user_account;
DROP TABLE IF EXISTS fleet_account;

-- 4.1 企业车队（企业客户）档案表
CREATE TABLE fleet_account (
    fleet_id              BIGINT UNSIGNED                            NOT NULL AUTO_INCREMENT COMMENT '车队主键ID',
    fleet_code            VARCHAR(32)                                NOT NULL                COMMENT '车队编号（如 FL-BJ-001）',
    fleet_name            VARCHAR(120)                               NOT NULL                COMMENT '企业名称',
    credit_code           CHAR(18)                                   NULL                    COMMENT '统一社会信用代码',
    contact_name          VARCHAR(50)                                NOT NULL                COMMENT '企业业务对接人',
    contact_phone_cipher  VARCHAR(255)                               NOT NULL                COMMENT '对接人手机号密文（Fernet）',
    contact_phone_hash    CHAR(64)                                   NOT NULL                COMMENT '对接人手机号盲索引（HMAC-SHA256）',
    contact_phone_masked  VARCHAR(20)                                NULL                    COMMENT '对接人手机号脱敏展示（如 138****5678）',
    credit_limit          DECIMAL(12, 2)                             NOT NULL DEFAULT 0.00   COMMENT '月度授信额度（元）',
    credit_used           DECIMAL(12, 2)                             NOT NULL DEFAULT 0.00   COMMENT '本月已用授信额度（元）',
    status                ENUM('ACTIVE', 'SUSPENDED', 'CLOSED')      NOT NULL DEFAULT 'ACTIVE' COMMENT '合作状态：ACTIVE-正常, SUSPENDED-暂停, CLOSED-已终止',
    created_at            DATETIME                                   NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    updated_at            DATETIME                                   NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (fleet_id),
    UNIQUE KEY uk_fleet_code (fleet_code),
    KEY idx_fleet_status (status),
    CONSTRAINT chk_fleet_credit CHECK (credit_limit >= 0.00 AND credit_used >= 0.00),
    CONSTRAINT chk_fleet_credit_used CHECK (credit_used <= credit_limit)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='业务线4-企业车队档案表';

-- 4.2 平台账号表（5 类角色统一账号；手机号加密存储 + 盲索引 + 脱敏展示）
CREATE TABLE user_account (
    user_id              BIGINT UNSIGNED                                     NOT NULL AUTO_INCREMENT COMMENT '用户主键ID',
    username             VARCHAR(50)                                         NOT NULL                COMMENT '登录用户名',
    password_hash        VARCHAR(255)                                        NOT NULL                COMMENT 'PBKDF2-SHA256 加盐哈希串',
    role                 ENUM('driver', 'ops', 'settlement', 'fleet', 'admin') NOT NULL DEFAULT 'driver' COMMENT 'RBAC 角色（与 app/auth.py RoleEnum 对齐）',
    fleet_id             BIGINT UNSIGNED                                     NULL                    COMMENT '所属企业车队ID（仅 fleet 角色非空）',
    phone_cipher         VARCHAR(255)                                        NOT NULL                COMMENT '手机号密文（Fernet 对称加密）',
    phone_hash           CHAR(64)                                            NOT NULL                COMMENT '手机号盲索引（HMAC-SHA256，供等值检索与唯一性校验）',
    phone_masked         VARCHAR(20)                                         NULL                    COMMENT '手机号脱敏展示（如 138****5678）',
    realname_status      ENUM('UNVERIFIED', 'PENDING', 'VERIFIED', 'REJECTED') NOT NULL DEFAULT 'UNVERIFIED' COMMENT '实名认证状态',
    realname_verified_at DATETIME                                            NULL                    COMMENT '实名认证通过时间',
    register_channel     ENUM('APP', 'MINI_PROGRAM', 'WEB', 'OFFLINE', 'FLEET_IMPORT') NOT NULL DEFAULT 'APP' COMMENT '注册渠道',
    member_level         ENUM('NORMAL', 'SILVER', 'GOLD', 'PLATINUM')        NOT NULL DEFAULT 'NORMAL' COMMENT '会员等级',
    balance              DECIMAL(12, 2)                                      NOT NULL DEFAULT 0.00   COMMENT '账户余额（元，禁止为负）',
    status               ENUM('ACTIVE', 'FROZEN', 'DISABLED')                NOT NULL DEFAULT 'ACTIVE' COMMENT '账号状态：ACTIVE-正常, FROZEN-冻结, DISABLED-禁用',
    failed_login_count   INT UNSIGNED                                        NOT NULL DEFAULT 0      COMMENT '连续登录失败次数（达阈值锁定）',
    locked_until         DATETIME                                            NULL                    COMMENT '账号锁定截止时间（为空表示未锁定）',
    last_login_at        DATETIME                                            NULL                    COMMENT '最近一次登录成功时间',
    created_at           DATETIME                                            NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '注册时间',
    updated_at           DATETIME                                            NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (user_id),
    UNIQUE KEY uk_user_username (username),
    UNIQUE KEY uk_user_phone_hash (phone_hash),
    KEY idx_user_role_status (role, status),
    KEY idx_user_fleet (fleet_id),
    KEY idx_user_member (member_level, balance),
    CONSTRAINT fk_user_fleet
        FOREIGN KEY (fleet_id) REFERENCES fleet_account (fleet_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT chk_user_balance CHECK (balance >= 0.00),
    CONSTRAINT chk_user_fleet_role CHECK (role <> 'fleet' OR fleet_id IS NOT NULL),
    CONSTRAINT chk_user_failed_count CHECK (failed_login_count >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='业务线4-平台账号表';

-- 4.3 车辆表（一个用户可绑定多辆车；车牌盲索引全局唯一 → 同一车牌同刻只能归属一个用户）
CREATE TABLE vehicle (
    vehicle_id           BIGINT UNSIGNED                             NOT NULL AUTO_INCREMENT COMMENT '车辆主键ID',
    user_id              BIGINT UNSIGNED                             NULL                    COMMENT '当前归属车主ID（未绑定为 NULL）',
    fleet_id             BIGINT UNSIGNED                             NULL                    COMMENT '所属企业车队ID（企业用车非空）',
    plate_cipher         VARCHAR(255)                                NOT NULL                COMMENT '车牌号密文（Fernet）',
    plate_hash           CHAR(64)                                    NOT NULL                COMMENT '车牌号盲索引（HMAC-SHA256，全表检索用）',
    plate_masked         VARCHAR(20)                                 NOT NULL                COMMENT '车牌号脱敏展示（如 京AD****01）',
    plate_color          ENUM('GREEN', 'BLUE', 'YELLOW', 'WHITE', 'BLACK') NOT NULL DEFAULT 'GREEN' COMMENT '号牌颜色（新能源多为 GREEN）',
    vehicle_model        VARCHAR(64)                                 NOT NULL                COMMENT '车型（如 比亚迪 汉 EV）',
    battery_capacity_kwh DECIMAL(8, 2)                               NOT NULL                COMMENT '电池容量（kWh）',
    max_charge_power     DECIMAL(8, 2)                               NOT NULL                COMMENT '车辆最大充电功率（kW）',
    supports_fast_charge TINYINT(1)                                  NOT NULL DEFAULT 1      COMMENT '是否支持直流快充：1-是, 0-否',
    bind_status          ENUM('BOUND', 'UNBOUND', 'DISABLED')        NOT NULL DEFAULT 'BOUND' COMMENT '绑定状态：BOUND-已绑定, UNBOUND-已解绑, DISABLED-停用',
    -- “一车同刻一主”只约束在绑行：解绑后该生成列为 NULL，车牌可被重新绑定（否则车牌会被永久锁死）
    active_plate_hash    CHAR(64) GENERATED ALWAYS AS (
                             CASE WHEN bind_status = 'BOUND' THEN plate_hash END
                         ) STORED                                         COMMENT '在绑状态下的车牌盲索引（解绑后为 NULL，供唯一索引使用）',
    bound_at             DATETIME                                    NULL                    COMMENT '最近一次绑定时间',
    created_at           DATETIME                                    NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    updated_at           DATETIME                                    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (vehicle_id),
    UNIQUE KEY uk_vehicle_active_plate (active_plate_hash),
    KEY idx_vehicle_plate_hash (plate_hash),
    KEY idx_vehicle_user_status (user_id, bind_status),
    KEY idx_vehicle_fleet (fleet_id),
    CONSTRAINT fk_vehicle_user
        FOREIGN KEY (user_id) REFERENCES user_account (user_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_vehicle_fleet
        FOREIGN KEY (fleet_id) REFERENCES fleet_account (fleet_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT chk_vehicle_battery CHECK (battery_capacity_kwh > 0.00),
    CONSTRAINT chk_vehicle_power CHECK (max_charge_power > 0.00 AND max_charge_power <= 500.00),
    CONSTRAINT chk_vehicle_fast_charge CHECK (supports_fast_charge = 0 OR max_charge_power >= 30.00),
    CONSTRAINT chk_vehicle_bound_owner CHECK (bind_status <> 'BOUND' OR user_id IS NOT NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='业务线4-车辆表';

-- 4.4 优惠活动表（运营人员按人群/场站/时段定向投放券）
CREATE TABLE coupon_campaign (
    campaign_id           BIGINT UNSIGNED                          NOT NULL AUTO_INCREMENT COMMENT '活动主键ID',
    campaign_code         VARCHAR(32)                              NOT NULL                COMMENT '活动编号（如 CP-202611-NEW）',
    campaign_name         VARCHAR(120)                             NOT NULL                COMMENT '活动名称',
    coupon_kind           ENUM('CASH', 'DISCOUNT', 'SERVICE_FEE_FREE') NOT NULL DEFAULT 'CASH' COMMENT '券类型：CASH-立减券, DISCOUNT-折扣券, SERVICE_FEE_FREE-服务费减免券',
    face_value            DECIMAL(8, 2)                            NOT NULL                COMMENT '券面额（元，折扣券存折扣权益折算金额）',
    threshold_amount      DECIMAL(8, 2)                            NOT NULL DEFAULT 0.00   COMMENT '使用门槛：订单满该金额方可用（0 表示无门槛）',
    applicable_station_id BIGINT UNSIGNED                          NULL                    COMMENT '适用场站ID（为空表示全场站通用）',
    valid_from            DATE                                     NOT NULL                COMMENT '活动生效日期',
    valid_to              DATE                                     NOT NULL                COMMENT '活动失效日期',
    issue_total           INT UNSIGNED                             NOT NULL                COMMENT '计划发放总量',
    issued_count          INT UNSIGNED                             NOT NULL DEFAULT 0      COMMENT '已发放数量（由触发器累加）',
    per_user_limit        TINYINT UNSIGNED                         NOT NULL DEFAULT 1      COMMENT '每人限领张数（1~20）',
    redeem_rule           VARCHAR(255)                             NOT NULL DEFAULT '单笔订单限用一张，不可与其他优惠叠加' COMMENT '核销规则说明',
    status                ENUM('DRAFT', 'ACTIVE', 'PAUSED', 'ENDED') NOT NULL DEFAULT 'DRAFT' COMMENT '活动状态：DRAFT-草稿, ACTIVE-投放中, PAUSED-暂停, ENDED-已结束',
    created_by            BIGINT UNSIGNED                          NULL                    COMMENT '创建该活动的运营管理员 user_id',
    created_at            DATETIME                                 NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    updated_at            DATETIME                                 NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (campaign_id),
    UNIQUE KEY uk_campaign_code (campaign_code),
    KEY idx_campaign_status_valid (status, valid_from, valid_to),
    KEY idx_campaign_station (applicable_station_id),
    CONSTRAINT fk_campaign_station
        FOREIGN KEY (applicable_station_id) REFERENCES station (station_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_campaign_creator
        FOREIGN KEY (created_by) REFERENCES user_account (user_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT chk_campaign_face CHECK (face_value > 0.00),
    CONSTRAINT chk_campaign_threshold CHECK (threshold_amount >= 0.00),
    CONSTRAINT chk_campaign_valid CHECK (valid_to >= valid_from),
    CONSTRAINT chk_campaign_quota CHECK (issue_total > 0 AND issued_count <= issue_total),
    CONSTRAINT chk_campaign_per_user CHECK (per_user_limit BETWEEN 1 AND 20)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='业务线4-优惠活动表';

-- 4.5 优惠券发放记录表（一张券一行，面额与门槛为发放时快照，活动后续调价不影响已发券）
CREATE TABLE coupon_record (
    coupon_id        BIGINT UNSIGNED                                        NOT NULL AUTO_INCREMENT COMMENT '券主键ID',
    coupon_no        VARCHAR(32)                                            NOT NULL                COMMENT '券码（唯一）',
    campaign_id      BIGINT UNSIGNED                                        NOT NULL                COMMENT '所属优惠活动ID',
    user_id          BIGINT UNSIGNED                                        NOT NULL                COMMENT '持券人ID',
    face_value       DECIMAL(8, 2)                                          NOT NULL                COMMENT '发放时券面额快照（元）',
    threshold_amount DECIMAL(8, 2)                                          NOT NULL DEFAULT 0.00   COMMENT '发放时使用门槛快照（元）',
    issue_channel    ENUM('NEW_USER_PACK', 'OPS_GRANT', 'ACTIVITY', 'COMPENSATION') NOT NULL DEFAULT 'OPS_GRANT' COMMENT '发放渠道',
    issued_by        BIGINT UNSIGNED                                        NULL                    COMMENT '发券运营人员 user_id（系统/活动发放为空）',
    issued_at        DATETIME                                               NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '发放时间',
    expire_at        DATE                                                   NOT NULL                COMMENT '券到期日（核销时校验未过期）',
    status           ENUM('ISSUED', 'REDEEMED', 'EXPIRED', 'VOID')           NOT NULL DEFAULT 'ISSUED' COMMENT '券状态：ISSUED-待使用, REDEEMED-已核销, EXPIRED-已过期, VOID-已作废',
    created_at       DATETIME                                               NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    updated_at       DATETIME                                               NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (coupon_id),
    UNIQUE KEY uk_coupon_no (coupon_no),
    KEY idx_coupon_user_status (user_id, status, expire_at),
    KEY idx_coupon_campaign (campaign_id, status),
    CONSTRAINT fk_coupon_campaign
        FOREIGN KEY (campaign_id) REFERENCES coupon_campaign (campaign_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_coupon_user
        FOREIGN KEY (user_id) REFERENCES user_account (user_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT chk_coupon_face CHECK (face_value > 0.00),
    CONSTRAINT chk_coupon_threshold CHECK (threshold_amount >= 0.00)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='业务线4-优惠券发放记录表';

-- 4.6 优惠券核销记录表（核销与订单 1:1：uk_redeem_order 保证一单一券，uk_redeem_coupon 保证一券一销）
-- 说明：order_id 指向业务线 2 的 charge_order，属跨线逻辑外键（与 repair_order.maintainer_id 同一口径），
--       不建物理外键以避免 01/02 两个分支的建表与删除顺序耦合。
CREATE TABLE coupon_redeem_record (
    redeem_id    BIGINT UNSIGNED   NOT NULL AUTO_INCREMENT COMMENT '核销主键ID',
    coupon_id    BIGINT UNSIGNED   NOT NULL                COMMENT '被核销的券ID（一券一销）',
    order_id     BIGINT            NOT NULL                COMMENT '核销对应的充电订单ID（逻辑引用 charge_order）',
    user_id      BIGINT UNSIGNED   NOT NULL                COMMENT '核销人ID',
    station_id   BIGINT UNSIGNED   NOT NULL                COMMENT '核销场站ID（校验活动适用场站）',
    face_value   DECIMAL(8, 2)     NOT NULL                COMMENT '实际抵扣金额（元）',
    order_amount DECIMAL(10, 2)    NOT NULL                COMMENT '核销时订单金额（用于门槛校验留痕）',
    redeemed_at  DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '核销时间',
    PRIMARY KEY (redeem_id),
    UNIQUE KEY uk_redeem_coupon (coupon_id),
    UNIQUE KEY uk_redeem_order (order_id),
    KEY idx_redeem_user (user_id, redeemed_at),
    KEY idx_redeem_station (station_id, redeemed_at),
    CONSTRAINT fk_redeem_coupon
        FOREIGN KEY (coupon_id) REFERENCES coupon_record (coupon_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_redeem_user
        FOREIGN KEY (user_id) REFERENCES user_account (user_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_redeem_station
        FOREIGN KEY (station_id) REFERENCES station (station_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT chk_redeem_face CHECK (face_value > 0.00 AND face_value <= order_amount)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='业务线4-优惠券核销记录表';


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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='分时电价规则（业务线2）';

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='充电订单（业务线2）';

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
    UNIQUE KEY uk_detail_seg (order_id, rule_id, begin_time),
    KEY idx_detail_order (order_id),
    CONSTRAINT fk_detail_order FOREIGN KEY (order_id) REFERENCES charge_order (order_id) ON DELETE CASCADE,
    CONSTRAINT ck_detail_time CHECK (end_time > begin_time),
    CONSTRAINT ck_detail_kwh  CHECK (kwh >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='分时费用明细（业务线2）';

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='支付流水（业务线2）';


-- ============================================================================
-- 【业务线 3】设备运行遥测（分区表）、告警与维修工单（负责人：成员 3 / 代润森）
-- 说明：依赖 charging_pile(pile_id)、charging_gun(gun_id)、user_account(user_id)。
-- ============================================================================

-- 3.1 分钟级设备运行遥测事实表（按采集时间 RANGE 分区；分区表禁止外键）
CREATE TABLE IF NOT EXISTS pile_telemetry (
    telemetry_id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '遥测记录主键ID',
    pile_id              BIGINT UNSIGNED NOT NULL                COMMENT '关联充电桩ID（逻辑引用 charging_pile.pile_id）',
    collected_at         DATETIME(3)     NOT NULL                COMMENT '采集时间（毫秒精度，分区键）',
    output_voltage       DECIMAL(6, 1)   NULL                    COMMENT '输出电压（V）',
    output_current       DECIMAL(6, 2)   NULL                    COMMENT '输出电流（A）',
    power                DECIMAL(8, 3)   NOT NULL DEFAULT 0.000  COMMENT '当前输出功率（kW）',
    module_temp          DECIMAL(5, 1)   NULL                    COMMENT '模块温度（℃）',
    offline_duration_sec INT UNSIGNED    NOT NULL DEFAULT 0      COMMENT '累计离线时长（秒）',
    fault_code           VARCHAR(32)     NULL                    COMMENT '故障码（无故障为 NULL）',
    PRIMARY KEY (telemetry_id, collected_at),
    UNIQUE KEY uk_pile_telemetry_time (pile_id, collected_at),
    KEY idx_pile_telemetry_fault_time (fault_code, collected_at),
    CONSTRAINT chk_telemetry_voltage CHECK (output_voltage IS NULL OR output_voltage BETWEEN 0 AND 1000),
    CONSTRAINT chk_telemetry_current CHECK (output_current IS NULL OR output_current BETWEEN 0 AND 1000),
    CONSTRAINT chk_telemetry_power CHECK (power >= 0),
    CONSTRAINT chk_telemetry_temp CHECK (module_temp IS NULL OR module_temp BETWEEN -40 AND 150)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='业务线3-分钟级设备运行遥测分区表'
PARTITION BY RANGE COLUMNS (collected_at) (
    PARTITION p202610 VALUES LESS THAN ('2026-11-01'),
    PARTITION p202611 VALUES LESS THAN ('2026-12-01'),
    PARTITION p202612 VALUES LESS THAN ('2027-01-01'),
    PARTITION p202701 VALUES LESS THAN ('2027-02-01'),
    PARTITION p_future  VALUES LESS THAN (MAXVALUE)
);

-- 3.2 设备告警记录表（告警与维修工单为 1:N，同一告警可多次派单）
CREATE TABLE IF NOT EXISTS alarm_record (
    alarm_id            BIGINT UNSIGNED                         NOT NULL AUTO_INCREMENT COMMENT '告警主键ID',
    pile_id             BIGINT UNSIGNED                         NOT NULL                COMMENT '关联充电桩ID',
    gun_id              BIGINT UNSIGNED                         NULL                    COMMENT '关联充电枪ID（仅枪级告警非空）',
    alarm_code          VARCHAR(32)                             NOT NULL                COMMENT '告警码（如 E_OVERVOLT / E_MODULE_OVERHEAT）',
    alarm_level         ENUM('L1', 'L2', 'L3', 'L4')            NOT NULL                COMMENT '当前告警等级：L1-一级(最紧急) / L4-四级',
    original_level      ENUM('L1', 'L2', 'L3', 'L4')            NOT NULL                COMMENT '初始告警等级（升级后不可回退）',
    response_limit_min  SMALLINT UNSIGNED                       NOT NULL                COMMENT '该等级 SLA 响应时限（分钟）',
    occurred_at         DATETIME(3)                             NOT NULL                COMMENT '告警发生时间',
    recovered_at        DATETIME(3)                             NULL                    COMMENT '告警恢复时间（解除前为空）',
    status              ENUM('ACTIVE', 'PROCESSING', 'RECOVERED', 'CLOSED') NOT NULL DEFAULT 'ACTIVE' COMMENT '告警状态',
    escalated_count     TINYINT UNSIGNED                        NOT NULL DEFAULT 0      COMMENT '已自动升级次数',
    escalated_at        DATETIME(3)                             NULL                    COMMENT '最近一次升级时间',
    created_at          DATETIME                                NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    updated_at          DATETIME                                NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (alarm_id),
    KEY idx_alarm_status_time (status, occurred_at),
    KEY idx_alarm_pile_time (pile_id, occurred_at),
    KEY idx_alarm_code (alarm_code),
    CONSTRAINT fk_alarm_pile
        FOREIGN KEY (pile_id) REFERENCES charging_pile (pile_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_alarm_gun
        FOREIGN KEY (gun_id) REFERENCES charging_gun (gun_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT chk_alarm_response_limit CHECK (response_limit_min > 0),
    CONSTRAINT chk_alarm_recovered_time CHECK (recovered_at IS NULL OR recovered_at >= occurred_at),
    CONSTRAINT chk_alarm_escalated_count CHECK (escalated_count >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='业务线3-设备告警记录表';

-- 3.3 维修工单表（同一告警可多次派单；同一时刻仅允许一张处理中工单，由触发器保障）
CREATE TABLE IF NOT EXISTS repair_order (
    work_order_id   BIGINT UNSIGNED   NOT NULL AUTO_INCREMENT COMMENT '维修工单主键ID',
    alarm_id        BIGINT UNSIGNED   NOT NULL                COMMENT '关联告警ID',
    maintainer_id   BIGINT UNSIGNED   NOT NULL                COMMENT '维修人员 user_id（逻辑引用业务线4 user_account.user_id）',
    status          ENUM('DISPATCHED', 'ACCEPTED', 'ARRIVED', 'REPAIRED', 'VERIFIED', 'CLOSED') NOT NULL DEFAULT 'DISPATCHED' COMMENT '工单状态',
    dispatched_at   DATETIME(3)       NOT NULL                COMMENT '派单时间',
    accepted_at     DATETIME(3)       NULL                    COMMENT '接单时间',
    arrived_at      DATETIME(3)       NULL                    COMMENT '到场时间',
    repaired_at     DATETIME(3)       NULL                    COMMENT '修复时间',
    verified_at     DATETIME(3)       NULL                    COMMENT '复核时间',
    closed_at       DATETIME(3)       NULL                    COMMENT '关闭时间',
    replaced_parts  VARCHAR(255)      NULL                    COMMENT '更换备件清单',
    result          VARCHAR(255)      NULL                    COMMENT '处理结果说明',
    created_at      DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    updated_at      DATETIME          NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '记录更新时间',
    PRIMARY KEY (work_order_id),
    KEY idx_workorder_alarm_status (alarm_id, status),
    KEY idx_workorder_maintainer_status (maintainer_id, status),
    KEY idx_workorder_dispatch_time (dispatched_at),
    CONSTRAINT fk_workorder_alarm
        FOREIGN KEY (alarm_id) REFERENCES alarm_record (alarm_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_workorder_maintainer
        FOREIGN KEY (maintainer_id) REFERENCES user_account (user_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT chk_workorder_time_order CHECK (
        (status = 'DISPATCHED' OR accepted_at IS NOT NULL)
        AND (status IN ('DISPATCHED', 'ACCEPTED') OR arrived_at IS NOT NULL)
        AND (status IN ('DISPATCHED', 'ACCEPTED', 'ARRIVED') OR repaired_at IS NOT NULL)
        AND (status IN ('DISPATCHED', 'ACCEPTED', 'ARRIVED', 'REPAIRED') OR verified_at IS NOT NULL)
        AND (status <> 'CLOSED' OR closed_at IS NOT NULL)
        AND (accepted_at IS NULL OR accepted_at >= dispatched_at)
        AND (arrived_at IS NULL OR (accepted_at IS NOT NULL AND arrived_at >= accepted_at))
        AND (repaired_at IS NULL OR (arrived_at IS NOT NULL AND repaired_at >= arrived_at))
        AND (verified_at IS NULL OR (repaired_at IS NOT NULL AND verified_at >= repaired_at))
        AND (closed_at IS NULL OR (verified_at IS NOT NULL AND closed_at >= verified_at))
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='业务线3-维修工单表';


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
    station_key    BIGINT UNSIGNED       AS (COALESCE(station_id, 0)) STORED COMMENT '站号占位，NULL 视为 0 参与唯一键',
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
    UNIQUE KEY uk_bill_month (operator_id, station_key, bill_month),
    CONSTRAINT ck_bill_money CHECK (total_amount >= 0 AND platform_fee >= 0 AND payable_amount >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='月度结算单（业务线5）';

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='对账差异（业务线5）';

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='开票记录（业务线5）';

-- ============================================================
-- 跨线外键：线1（station / charging_gun）、线4（user_account / vehicle）的表都在本文件前面
-- 类型必须都是 BIGINT UNSIGNED，名字不一致就改这里
-- ============================================================
ALTER TABLE charge_order
    ADD CONSTRAINT fk_order_station FOREIGN KEY (station_id) REFERENCES station (station_id),
    ADD CONSTRAINT fk_order_gun     FOREIGN KEY (gun_id)     REFERENCES charging_gun (gun_id),
    ADD CONSTRAINT fk_order_user    FOREIGN KEY (user_id)    REFERENCES user_account (user_id),
    ADD CONSTRAINT fk_order_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicle (vehicle_id);
