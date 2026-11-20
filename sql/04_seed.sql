-- ============================================================================
-- 04. 初始化测试数据（每张表 >= 20 条真实业务数据）
-- 说明：
--   1. 按外键依赖顺序写入：operator -> station -> charging_pile -> charging_gun -> device_status_log；
--   2. 全表采用 INSERT ... AS nv ON DUPLICATE KEY UPDATE，可反复重跑复位设备状态与心跳时间戳；
--   3. 固定保留 pile_id=15（超时10分钟）与 pile_id=17（超时8分钟）两台 IDLE 桩用于心跳巡检与第3组对比查询，
--      其余正常在线桩心跳阈值设为 1800 秒，避免演示/测试期间全网误超时；
--   4. 插入期间临时置 @skip_auto_status_log = 1，尾部重置为 NULL，防止覆盖历史日志并严守触发器校验。
-- ============================================================================

USE ev_charge_hub;
SET NAMES utf8mb4;

-- ============================================================================
-- 【业务线 1】场站与充电设施测试数据（负责人：成员 1 / 组长）
-- 数据规模：operator(20) / station(20) / charging_pile(24) / charging_gun(40) / device_status_log(30)
-- ============================================================================

SET @skip_auto_status_log = 1;

-- 1. 运营商档案表（20 条：18 家 ACTIVE + 2 家 SUSPENDED）
INSERT INTO operator (
    operator_id, operator_code, operator_name, credit_code, contact_name, service_phone, status
) VALUES
(1,  'OP-BJ-001', '京能慧充新能源有限公司',     '91110108MA01C00001', '张伟',   '400-801-0001', 'ACTIVE'),
(2,  'OP-BJ-002', '特来电北京运营分公司',       '91110105MA01C00002', '王芳',   '400-801-0002', 'ACTIVE'),
(3,  'OP-BJ-003', '星星充电海淀运营中心',       '91110108MA01C00003', '李敏',   '400-801-0003', 'ACTIVE'),
(4,  'OP-BJ-004', '国网电动北京海淀服务部',     '91110108MA01C00004', '赵强',   '400-801-0004', 'ACTIVE'),
(5,  'OP-BJ-005', '小桔能源朝阳运营有限公司',   '91110105MA01C00005', '刘洋',   '400-801-0005', 'ACTIVE'),
(6,  'OP-BJ-006', '蔚来能源北京补能科技有限公司', '91110105MA01C00006', '陈静',   '400-801-0006', 'ACTIVE'),
(7,  'OP-BJ-007', '理想超充华北运营有限公司',   '91110113MA01C00007', '杨帆',   '400-801-0007', 'ACTIVE'),
(8,  'OP-BJ-008', '比亚迪充能北京科技有限公司', '91110115MA01C00008', '周杰',   '400-801-0008', 'ACTIVE'),
(9,  'OP-BJ-009', '小鹏超充京津冀运营中心',     '91110105MA01C00009', '吴磊',   '400-801-0009', 'ACTIVE'),
(10, 'OP-BJ-010', '华为数字能源北京超充示范部', '91110108MA01C00010', '徐晨',   '400-801-0010', 'ACTIVE'),
(11, 'OP-BJ-011', '南方电网北京合作充换电中心', '91110106MA01C00011', '孙超',   '400-801-0011', 'ACTIVE'),
(12, 'OP-BJ-012', '壳牌智充北京机场运营公司',   '91110113MA01C00012', '马丽',   '400-801-0012', 'ACTIVE'),
(13, 'OP-BJ-013', '中石化易捷充能北京分公司',   '91110102MA01C00013', '朱峰',   '400-801-0013', 'ACTIVE'),
(14, 'OP-BJ-014', '中石油昆仑网电华北中心',     '91110101MA01C00014', '胡涛',   '400-801-0014', 'ACTIVE'),
(15, 'OP-BJ-015', '云快充北京伙伴运营有限公司', '91110112MA01C00015', '郭雪',   '400-801-0015', 'ACTIVE'),
(16, 'OP-BJ-016', '首钢园区智慧能源有限公司',   '91110107MA01C00016', '高飞',   '400-801-0016', 'ACTIVE'),
(17, 'OP-BJ-017', '亦庄绿能充换电科技有限公司', '91110115MA01C00017', '林森',   '400-801-0017', 'ACTIVE'),
(18, 'OP-BJ-018', '通州副中心光储充运营公司',   '91110112MA01C00018', '何婷',   '400-801-0018', 'ACTIVE'),
(19, 'OP-BJ-019', '昌平新能联营测试服务公司',   '91110114MA01C00019', '宋博',   '400-801-0019', 'SUSPENDED'),
(20, 'OP-BJ-020', '房山旧改联营电力服务公司',   '91110111MA01C00020', '唐宇',   '400-801-0020', 'SUSPENDED')
AS nv
ON DUPLICATE KEY UPDATE
    operator_code = nv.operator_code,
    operator_name = nv.operator_name,
    credit_code   = nv.credit_code,
    contact_name  = nv.contact_name,
    service_phone = nv.service_phone,
    status        = nv.status;

-- 2. 充电场站表（20 条：16 座 OPEN、2 座 MAINTENANCE、2 座 CLOSED）
INSERT INTO station (
    station_id, operator_id, station_code, station_name, address,
    longitude, latitude, total_spaces, available_spaces, business_hours, parking_fee_rate, station_status
) VALUES
(1,  1,  'ST-BJ-001', '北林清华东路超充站',       '北京市海淀区清华东路35号东门地面停车场',     116.345200, 40.002100, 24, 15, '00:00-24:00', 6.00,  'OPEN'),
(2,  2,  'ST-BJ-002', '五道口购物中心充电站',     '北京市海淀区成府路28号地下B2层A区',          116.338900, 39.992800, 30, 12, '06:00-23:30', 8.00,  'OPEN'),
(3,  3,  'ST-BJ-003', '学院路高校科创园充电站',   '北京市海淀区学院路30号院科研楼西侧',         116.351400, 39.990500, 20, 14, '00:00-24:00', 4.00,  'OPEN'),
(4,  4,  'ST-BJ-004', '中关村软件园一期快充站',   '北京市海淀区东北旺西路8号软件园广场',         116.292100, 40.044500, 40, 22, '00:00-24:00', 5.00,  'OPEN'),
(5,  5,  'ST-BJ-005', '望京SOHO地下超充站',       '北京市朝阳区望京街10号B3层C区',              116.480600, 39.996400, 36, 18, '00:00-24:00', 8.00,  'OPEN'),
(6,  6,  'ST-BJ-006', '奥林匹克森林公园南门站',   '北京市朝阳区科荟路33号南门生态停车场',       116.391200, 40.015600, 28, 20, '06:00-22:30', 5.00,  'OPEN'),
(7,  7,  'ST-BJ-007', '首都机场T3航站楼超充站',   '北京市顺义区机场高速T3停车楼3层F区',        116.614500, 40.056200, 50, 31, '00:00-24:00', 10.00, 'OPEN'),
(8,  8,  'ST-BJ-008', '亦庄经开区科创十六街站',   '北京市大兴区亦庄科创十六街19号院',           116.531800, 39.794200, 32, 25, '00:00-24:00', 0.00,  'OPEN'),
(9,  9,  'ST-BJ-009', '三里屯太古里南区充电站',   '北京市朝阳区三里屯路19号地下车库',           116.454300, 39.935100, 18, 6,  '07:00-23:00', 12.00, 'OPEN'),
(10, 10, 'ST-BJ-010', '西二旗地铁站全液冷超充站', '北京市海淀区上地十街西二旗北出口地面场',     116.306100, 40.052800, 22, 11, '00:00-24:00', 6.00,  'OPEN'),
(11, 11, 'ST-BJ-011', '丰台科技园总部基地充电站', '北京市丰台区南四环西路188号一区东侧',        116.298700, 39.826400, 26, 17, '00:00-24:00', 4.00,  'OPEN'),
(12, 12, 'ST-BJ-012', '大兴国际机场北航站楼站',   '北京市大兴区榆垡镇机场北停车楼一层',         116.410500, 39.512300, 48, 34, '00:00-24:00', 8.00,  'OPEN'),
(13, 13, 'ST-BJ-013', '西城区月坛易捷综合能源站', '北京市西城区月坛北街25号加油加电站内',       116.348900, 39.918200, 16, 9,  '00:00-24:00', 0.00,  'OPEN'),
(14, 14, 'ST-BJ-014', '东城区东直门交通枢纽站',   '北京市东城区东直门外大街48号地下停车场',     116.435200, 39.941200, 20, 7,  '06:00-24:00', 10.00, 'OPEN'),
(15, 15, 'ST-BJ-015', '通州万达广场充电站',       '北京市通州区新华西街58号B2层东区',           116.642300, 39.908700, 30, 19, '07:00-23:00', 5.00,  'OPEN'),
(16, 16, 'ST-BJ-016', '首钢园六工汇光储充站',     '北京市石景山区石景山路68号首钢园北区',       116.156400, 39.912500, 35, 24, '00:00-24:00', 4.00,  'OPEN'),
(17, 17, 'ST-BJ-017', '亦庄南海子公园南门站',     '北京市大兴区瀛海镇公园南路2号停车场',        116.472100, 39.761400, 20, 20, '06:00-21:00', 2.00,  'MAINTENANCE'),
(18, 18, 'ST-BJ-018', '副中心绿心公园慢充示范站', '北京市通州区张采路绿心森林公园西门',         116.718500, 39.874500, 18, 18, '06:00-22:00', 0.00,  'MAINTENANCE'),
(19, 19, 'ST-BJ-019', '昌平回龙观临时站（暂停）', '北京市昌平区育知东路30号临时停车场',         116.334200, 40.072100, 12, 12, '08:00-20:00', 3.00,  'CLOSED'),
(20, 20, 'ST-BJ-020', '房山良乡大学城老旧改造站', '北京市房山区拱辰街道月华北大街5号',          116.142500, 39.736500, 14, 14, '08:00-20:00', 2.00,  'CLOSED')
AS nv
ON DUPLICATE KEY UPDATE
    operator_id      = nv.operator_id,
    station_code     = nv.station_code,
    station_name     = nv.station_name,
    address          = nv.address,
    longitude        = nv.longitude,
    latitude         = nv.latitude,
    total_spaces     = nv.total_spaces,
    available_spaces = nv.available_spaces,
    business_hours   = nv.business_hours,
    parking_fee_rate = nv.parking_fee_rate,
    station_status   = nv.station_status;

-- 3. 充电桩表（24 条：时间线与日志表一致，固定保留 pile_id=15/17 为在线超时样本）
INSERT INTO charging_pile (
    pile_id, station_id, pile_code, device_model, rated_power, gun_count, protocol_type,
    commissioned_date, warranty_end_date, acceptance_status, current_status,
    offline_threshold_sec, last_heartbeat_at, status_updated_at
) VALUES
(1,  1,  'PL-001-01', 'HN-DC180-D2',  180.00, 2, 'OCPP1.6',     DATE(DATE_SUB(NOW(), INTERVAL 27 DAY)), '2030-12-31', 'PASSED',  'IDLE',     1800, DATE_SUB(NOW(), INTERVAL 20 SECOND), DATE_SUB(NOW(), INTERVAL 27 DAY)),
(2,  1,  'PL-001-02', 'HN-DC120-D2',  120.00, 2, 'OCPP1.6',     DATE(DATE_SUB(NOW(), INTERVAL 27 DAY)), '2030-12-31', 'PASSED',  'OCCUPIED', 1800, DATE_SUB(NOW(), INTERVAL 15 SECOND), DATE_SUB(NOW(), INTERVAL 25 MINUTE)),
(3,  2,  'PL-002-01', 'TLD-DC120-V3', 120.00, 2, '云快充1.5',   DATE(DATE_SUB(NOW(), INTERVAL 22 DAY)), '2030-12-31', 'PASSED',  'IDLE',     1800, DATE_SUB(NOW(), INTERVAL 30 SECOND), DATE_SUB(NOW(), INTERVAL 22 DAY)),
(4,  2,  'PL-002-02', 'TLD-AC14-D2',  14.00,  2, '云快充1.5',   DATE(DATE_SUB(NOW(), INTERVAL 22 DAY)), '2030-12-31', 'PASSED',  'IDLE',     1800, DATE_SUB(NOW(), INTERVAL 40 SECOND), DATE_SUB(NOW(), INTERVAL 22 DAY)),
(5,  3,  'PL-003-01', 'STAR-DC160-A', 160.00, 2, 'OCPP2.0.1',   DATE(DATE_SUB(NOW(), INTERVAL 20 DAY)), '2030-12-31', 'PASSED',  'IDLE',     1800, DATE_SUB(NOW(), INTERVAL 25 SECOND), DATE_SUB(NOW(), INTERVAL 20 DAY)),
(6,  3,  'PL-003-02', 'STAR-DC120-B', 120.00, 2, 'OCPP2.0.1',   DATE(DATE_SUB(NOW(), INTERVAL 20 DAY)), '2030-12-31', 'PASSED',  'FAULT',    1800, DATE_SUB(NOW(), INTERVAL 5 DAY),     DATE_SUB(NOW(), INTERVAL 5 DAY)),
(7,  4,  'PL-004-01', 'SGCC-DC240-X', 240.00, 2, '国网南瑞2.0', DATE(DATE_SUB(NOW(), INTERVAL 18 DAY)), '2030-12-31', 'PASSED',  'OCCUPIED', 1800, DATE_SUB(NOW(), INTERVAL 10 SECOND), DATE_SUB(NOW(), INTERVAL 40 MINUTE)),
(8,  4,  'PL-004-02', 'SGCC-DC120-S', 120.00, 2, '国网南瑞2.0', DATE(DATE_SUB(NOW(), INTERVAL 18 DAY)), '2030-12-31', 'PASSED',  'IDLE',     1800, DATE_SUB(NOW(), INTERVAL 18 SECOND), DATE_SUB(NOW(), INTERVAL 18 DAY)),
(9,  5,  'PL-005-01', 'XJ-DC180-PRO', 180.00, 2, 'OCPP1.6',     DATE(DATE_SUB(NOW(), INTERVAL 16 DAY)), '2030-12-31', 'PASSED',  'IDLE',     1800, DATE_SUB(NOW(), INTERVAL 22 SECOND), DATE_SUB(NOW(), INTERVAL 16 DAY)),
(10, 6,  'PL-006-01', 'NIO-HPC360',   360.00, 2, 'OCPP2.0.1',   DATE(DATE_SUB(NOW(), INTERVAL 14 DAY)), '2031-12-31', 'PASSED',  'IDLE',     1800, DATE_SUB(NOW(), INTERVAL 12 SECOND), DATE_SUB(NOW(), INTERVAL 12 DAY)),
(11, 7,  'PL-007-01', 'LI-5C480-U',   480.00, 2, 'OCPP2.0.1',   DATE(DATE_SUB(NOW(), INTERVAL 14 DAY)), '2031-12-31', 'PASSED',  'OCCUPIED', 1800, DATE_SUB(NOW(), INTERVAL 8 SECOND),  DATE_SUB(NOW(), INTERVAL 18 MINUTE)),
(12, 8,  'PL-008-01', 'BYD-DC120-G2', 120.00, 2, '云快充1.5',   DATE(DATE_SUB(NOW(), INTERVAL 13 DAY)), '2030-12-31', 'PASSED',  'IDLE',     1800, DATE_SUB(NOW(), INTERVAL 35 SECOND), DATE_SUB(NOW(), INTERVAL 13 DAY)),
(13, 9,  'PL-009-01', 'XP-S4-360KW',  360.00, 2, 'OCPP2.0.1',   DATE(DATE_SUB(NOW(), INTERVAL 11 DAY)), '2031-12-31', 'PASSED',  'OFFLINE',  180,  DATE_SUB(NOW(), INTERVAL 45 MINUTE), DATE_SUB(NOW(), INTERVAL 42 MINUTE)),
(14, 10, 'PL-010-01', 'HW-LFC600',    600.00, 2, 'OCPP2.0.1',   DATE(DATE_SUB(NOW(), INTERVAL 10 DAY)), '2031-12-31', 'PASSED',  'IDLE',     1800, DATE_SUB(NOW(), INTERVAL 14 SECOND), DATE_SUB(NOW(), INTERVAL 10 DAY)),
(15, 11, 'PL-011-01', 'CSG-DC120-M',  120.00, 2, '国网南瑞2.0', DATE(DATE_SUB(NOW(), INTERVAL 9 DAY)),  '2030-12-31', 'PASSED',  'IDLE',     180,  DATE_SUB(NOW(), INTERVAL 10 MINUTE), DATE_SUB(NOW(), INTERVAL 9 DAY)),
(16, 12, 'PL-012-01', 'SHL-DC240-D',  240.00, 2, 'OCPP1.6',     DATE(DATE_SUB(NOW(), INTERVAL 9 DAY)),  '2030-12-31', 'PASSED',  'OCCUPIED', 1800, DATE_SUB(NOW(), INTERVAL 19 SECOND), DATE_SUB(NOW(), INTERVAL 35 MINUTE)),
(17, 13, 'PL-013-01', 'YJC-DC120-S',  120.00, 1, '云快充1.5',   DATE(DATE_SUB(NOW(), INTERVAL 7 DAY)),  '2030-12-31', 'PASSED',  'IDLE',     120,  DATE_SUB(NOW(), INTERVAL 8 MINUTE),  DATE_SUB(NOW(), INTERVAL 7 DAY)),
(18, 14, 'PL-014-01', 'KL-DC160-S',   160.00, 1, 'OCPP1.6',     DATE(DATE_SUB(NOW(), INTERVAL 6 DAY)),  '2030-12-31', 'PASSED',  'FAULT',    180,  DATE_SUB(NOW(), INTERVAL 3 DAY),     DATE_SUB(NOW(), INTERVAL 3 DAY)),
(19, 15, 'PL-015-01', 'YKC-AC7-S1',   7.00,   1, '云快充1.5',   DATE(DATE_SUB(NOW(), INTERVAL 6 DAY)),  '2029-12-31', 'PASSED',  'IDLE',     1800, DATE_SUB(NOW(), INTERVAL 50 SECOND), DATE_SUB(NOW(), INTERVAL 6 DAY)),
(20, 16, 'PL-016-01', 'SG-DC180-B',   180.00, 2, 'OCPP2.0.1',   DATE(DATE_SUB(NOW(), INTERVAL 5 DAY)),  '2031-12-31', 'PASSED',  'IDLE',     1800, DATE_SUB(NOW(), INTERVAL 26 SECOND), DATE_SUB(NOW(), INTERVAL 5 DAY)),
(21, 17, 'PL-017-01', 'YZ-DC120-T1',  120.00, 2, 'OCPP1.6',     NULL,                                   '2031-12-31', 'PENDING', 'OFFLINE',  180,  NULL,                                DATE_SUB(NOW(), INTERVAL 4 DAY)),
(22, 18, 'PL-018-01', 'TZ-AC14-T2',   14.00,  2, '云快充1.5',   NULL,                                   '2031-12-31', 'PENDING', 'OFFLINE',  300,  NULL,                                DATE_SUB(NOW(), INTERVAL 4 DAY)),
(23, 19, 'PL-019-01', 'CP-DC60-OLD',  60.00,  1, 'OCPP1.6',     '2023-06-01',                           '2028-06-01', 'PASSED',  'OFFLINE',  180,  DATE_SUB(NOW(), INTERVAL 20 DAY),    DATE_SUB(NOW(), INTERVAL 20 DAY)),
(24, 20, 'PL-020-01', 'FS-AC7-OLD',   7.00,   1, '云快充1.5',   NULL,                                   '2029-12-31', 'REJECTED','OFFLINE',  300,  NULL,                                DATE_SUB(NOW(), INTERVAL 8 DAY))
AS nv
ON DUPLICATE KEY UPDATE
    station_id            = nv.station_id,
    pile_code             = nv.pile_code,
    device_model          = nv.device_model,
    rated_power           = nv.rated_power,
    gun_count             = nv.gun_count,
    protocol_type         = nv.protocol_type,
    commissioned_date     = nv.commissioned_date,
    warranty_end_date     = nv.warranty_end_date,
    acceptance_status     = nv.acceptance_status,
    current_status        = nv.current_status,
    offline_threshold_sec = nv.offline_threshold_sec,
    last_heartbeat_at     = nv.last_heartbeat_at,
    status_updated_at     = nv.status_updated_at;

-- 4. 充电枪表（40 条：功率/序号/父桩状态全校验合规）
INSERT INTO charging_gun (
    gun_id, pile_id, gun_code, gun_seq, gun_type, connector_standard, max_power, current_status, status_updated_at
) VALUES
(1,  1,  'GN-001-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 180.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 27 DAY)),
(2,  1,  'GN-001-01-B', 2, 'DC_FAST', 'GB/T 20234.3', 120.00, 'FAULT',    DATE_SUB(NOW(), INTERVAL 2 DAY)),
(3,  2,  'GN-001-02-A', 1, 'DC_FAST', 'GB/T 20234.3', 120.00, 'OCCUPIED', DATE_SUB(NOW(), INTERVAL 25 MINUTE)),
(4,  2,  'GN-001-02-B', 2, 'DC_FAST', 'GB/T 20234.3', 120.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 27 DAY)),
(5,  3,  'GN-002-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 120.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 15 DAY)),
(6,  3,  'GN-002-01-B', 2, 'DC_FAST', 'GB/T 20234.3', 60.00,  'IDLE',     DATE_SUB(NOW(), INTERVAL 22 DAY)),
(7,  4,  'GN-002-02-A', 1, 'AC_SLOW', 'GB/T 20234.2', 7.00,   'IDLE',     DATE_SUB(NOW(), INTERVAL 22 DAY)),
(8,  4,  'GN-002-02-B', 2, 'AC_SLOW', 'GB/T 20234.2', 7.00,   'IDLE',     DATE_SUB(NOW(), INTERVAL 22 DAY)),
(9,  5,  'GN-003-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 160.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 20 DAY)),
(10, 5,  'GN-003-01-B', 2, 'DC_FAST', 'GB/T 20234.3', 80.00,  'IDLE',     DATE_SUB(NOW(), INTERVAL 20 DAY)),
(11, 6,  'GN-003-02-A', 1, 'DC_FAST', 'GB/T 20234.3', 120.00, 'FAULT',    DATE_SUB(NOW(), INTERVAL 5 DAY)),
(12, 6,  'GN-003-02-B', 2, 'DC_FAST', 'GB/T 20234.3', 120.00, 'FAULT',    DATE_SUB(NOW(), INTERVAL 5 DAY)),
(13, 7,  'GN-004-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 240.00, 'OCCUPIED', DATE_SUB(NOW(), INTERVAL 40 MINUTE)),
(14, 7,  'GN-004-01-B', 2, 'DC_FAST', 'GB/T 20234.3', 120.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 18 DAY)),
(15, 8,  'GN-004-02-A', 1, 'DC_FAST', 'GB/T 20234.3', 120.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 18 DAY)),
(16, 8,  'GN-004-02-B', 2, 'DC_FAST', 'GB/T 20234.3', 120.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 18 DAY)),
(17, 9,  'GN-005-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 180.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 16 DAY)),
(18, 9,  'GN-005-01-B', 2, 'DC_FAST', 'GB/T 20234.3', 90.00,  'IDLE',     DATE_SUB(NOW(), INTERVAL 16 DAY)),
(19, 10, 'GN-006-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 360.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 14 DAY)),
(20, 10, 'GN-006-01-B', 2, 'DC_FAST', 'GB/T 20234.3', 180.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 14 DAY)),
(21, 11, 'GN-007-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 480.00, 'OCCUPIED', DATE_SUB(NOW(), INTERVAL 18 MINUTE)),
(22, 11, 'GN-007-01-B', 2, 'DC_FAST', 'GB/T 20234.3', 240.00, 'OCCUPIED', DATE_SUB(NOW(), INTERVAL 10 MINUTE)),
(23, 12, 'GN-008-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 120.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 13 DAY)),
(24, 12, 'GN-008-01-B', 2, 'DC_FAST', 'GB/T 20234.3', 120.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 13 DAY)),
(25, 13, 'GN-009-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 360.00, 'OFFLINE',  DATE_SUB(NOW(), INTERVAL 42 MINUTE)),
(26, 13, 'GN-009-01-B', 2, 'DC_FAST', 'GB/T 20234.3', 180.00, 'OFFLINE',  DATE_SUB(NOW(), INTERVAL 42 MINUTE)),
(27, 14, 'GN-010-01-A', 1, 'DC_FAST', 'ChaoJi/液冷',  600.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 10 DAY)),
(28, 14, 'GN-010-01-B', 2, 'DC_FAST', 'GB/T 20234.3', 250.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 10 DAY)),
(29, 15, 'GN-011-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 120.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 9 DAY)),
(30, 15, 'GN-011-01-B', 2, 'DC_FAST', 'GB/T 20234.3', 120.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 9 DAY)),
(31, 16, 'GN-012-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 240.00, 'OCCUPIED', DATE_SUB(NOW(), INTERVAL 35 MINUTE)),
(32, 16, 'GN-012-01-B', 2, 'DC_FAST', 'GB/T 20234.3', 120.00, 'OCCUPIED', DATE_SUB(NOW(), INTERVAL 35 MINUTE)),
(33, 17, 'GN-013-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 120.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 7 DAY)),
(34, 18, 'GN-014-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 160.00, 'FAULT',    DATE_SUB(NOW(), INTERVAL 3 DAY)),
(35, 19, 'GN-015-01-A', 1, 'AC_SLOW', 'GB/T 20234.2', 7.00,   'IDLE',     DATE_SUB(NOW(), INTERVAL 6 DAY)),
(36, 20, 'GN-016-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 180.00, 'IDLE',     DATE_SUB(NOW(), INTERVAL 5 DAY)),
(37, 20, 'GN-016-01-B', 2, 'DC_FAST', 'GB/T 20234.3', 90.00,  'IDLE',     DATE_SUB(NOW(), INTERVAL 5 DAY)),
(38, 21, 'GN-017-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 120.00, 'OFFLINE',  DATE_SUB(NOW(), INTERVAL 4 DAY)),
(39, 23, 'GN-019-01-A', 1, 'DC_FAST', 'GB/T 20234.3', 60.00,  'OFFLINE',  DATE_SUB(NOW(), INTERVAL 20 DAY)),
(40, 24, 'GN-020-01-A', 1, 'AC_SLOW', 'GB/T 20234.2', 7.00,   'OFFLINE',  DATE_SUB(NOW(), INTERVAL 8 DAY))
AS nv
ON DUPLICATE KEY UPDATE
    pile_id            = nv.pile_id,
    gun_code           = nv.gun_code,
    gun_seq            = nv.gun_seq,
    gun_type           = nv.gun_type,
    connector_standard = nv.connector_standard,
    max_power          = nv.max_power,
    current_status     = nv.current_status,
    status_updated_at  = nv.status_updated_at;

-- 5. 设备状态变更日志表（30 条：时间线与各桩/枪状态变更时刻严密对应）
INSERT INTO device_status_log (
    log_id, device_type, pile_id, gun_id, prev_status, new_status, change_source, todo_status, remark, changed_at
) VALUES
(1,  'PILE', 1,    NULL, NULL,       'OFFLINE',  'INIT',       'NONE',    '北林超充站1号桩建档录入',                     DATE_SUB(NOW(), INTERVAL 28 DAY)),
(2,  'PILE', 1,    NULL, 'OFFLINE',  'IDLE',     'ACCEPTANCE', 'NONE',    'OCPP1.6协议联调及铭牌核对通过正式投运',      DATE_SUB(NOW(), INTERVAL 27 DAY)),
(3,  'GUN',  NULL, 1,    'OFFLINE',  'IDLE',     'ACCEPTANCE', 'NONE',    '1号桩A枪同步验收上线',                      DATE_SUB(NOW(), INTERVAL 27 DAY)),
(4,  'GUN',  NULL, 2,    'OFFLINE',  'IDLE',     'ACCEPTANCE', 'NONE',    '1号桩B枪同步验收上线',                      DATE_SUB(NOW(), INTERVAL 27 DAY)),
(5,  'GUN',  NULL, 2,    'IDLE',     'FAULT',    'ALARM',      'PENDING', '绝缘检测电阻偏低告警，枪头锁定待修',          DATE_SUB(NOW(), INTERVAL 2 DAY)),
(6,  'GUN',  NULL, 3,    'IDLE',     'OCCUPIED', 'ORDER',      'NONE',    '车主扫码启动直流快充订单',                  DATE_SUB(NOW(), INTERVAL 25 MINUTE)),
(7,  'PILE', 2,    NULL, 'IDLE',     'OCCUPIED', 'ORDER',      'NONE',    '2号桩A枪进入带载输出状态',                  DATE_SUB(NOW(), INTERVAL 25 MINUTE)),
(8,  'PILE', 3,    NULL, 'OFFLINE',  'IDLE',     'ACCEPTANCE', 'NONE',    '五道口1号桩验收投运',                       DATE_SUB(NOW(), INTERVAL 22 DAY)),
(9,  'GUN',  NULL, 5,    'IDLE',     'OCCUPIED', 'ORDER',      'NONE',    '早高峰快充订单启充',                        DATE_SUB(NOW(), INTERVAL 15 DAY)),
(10, 'GUN',  NULL, 5,    'OCCUPIED', 'IDLE',     'ORDER',      'NONE',    '充电结束拔枪结算并释放为空闲',              DATE_SUB(NOW(), INTERVAL 15 DAY)),
(11, 'PILE', 6,    NULL, 'IDLE',     'FAULT',    'ALARM',      'PENDING', '学院路2号桩主接触器粘连告警',               DATE_SUB(NOW(), INTERVAL 5 DAY)),
(12, 'GUN',  NULL, 11,   'IDLE',     'FAULT',    'ALARM',      'PENDING', '所属桩接触器故障导致A枪不可用',             DATE_SUB(NOW(), INTERVAL 5 DAY)),
(13, 'GUN',  NULL, 12,   'IDLE',     'FAULT',    'ALARM',      'PENDING', '所属桩接触器故障导致B枪不可用',             DATE_SUB(NOW(), INTERVAL 5 DAY)),
(14, 'PILE', 7,    NULL, 'IDLE',     'OCCUPIED', 'ORDER',      'NONE',    '中关村软件园240kW桩A枪启动快充',            DATE_SUB(NOW(), INTERVAL 40 MINUTE)),
(15, 'GUN',  NULL, 13,   'IDLE',     'OCCUPIED', 'ORDER',      'NONE',    '高功率直流充电进行中',                      DATE_SUB(NOW(), INTERVAL 40 MINUTE)),
(16, 'PILE', 10,   NULL, 'IDLE',     'OFFLINE',  'HEARTBEAT',  'DONE',    '网络波动导致心跳超时，已重连恢复上线',      DATE_SUB(NOW(), INTERVAL 12 DAY)),
(17, 'PILE', 10,   NULL, 'OFFLINE',  'IDLE',     'MANUAL',     'DONE',    '运维人员核查通信网关后恢复空闲',            DATE_SUB(NOW(), INTERVAL 12 DAY)),
(18, 'PILE', 11,   NULL, 'IDLE',     'OCCUPIED', 'ORDER',      'NONE',    'T3航站楼5C超充桩双枪并发充电',              DATE_SUB(NOW(), INTERVAL 18 MINUTE)),
(19, 'GUN',  NULL, 21,   'IDLE',     'OCCUPIED', 'ORDER',      'NONE',    'T3超充A枪充电中',                           DATE_SUB(NOW(), INTERVAL 18 MINUTE)),
(20, 'GUN',  NULL, 22,   'IDLE',     'OCCUPIED', 'ORDER',      'NONE',    'T3超充B枪充电中',                           DATE_SUB(NOW(), INTERVAL 10 MINUTE)),
(21, 'PILE', 13,   NULL, 'IDLE',     'OFFLINE',  'HEARTBEAT',  'PENDING', '三里屯超充桩心跳超时自动置为离线（阈值180秒）', DATE_SUB(NOW(), INTERVAL 42 MINUTE)),
(22, 'GUN',  NULL, 25,   'IDLE',     'OFFLINE',  'HEARTBEAT',  'PENDING', '所属桩离线联动A枪置为离线',                 DATE_SUB(NOW(), INTERVAL 42 MINUTE)),
(23, 'GUN',  NULL, 26,   'IDLE',     'OFFLINE',  'HEARTBEAT',  'PENDING', '所属桩离线联动B枪置为离线',                 DATE_SUB(NOW(), INTERVAL 42 MINUTE)),
(24, 'PILE', 14,   NULL, 'OFFLINE',  'IDLE',     'ACCEPTANCE', 'NONE',    '600kW全液冷超充桩验收合格投运',             DATE_SUB(NOW(), INTERVAL 10 DAY)),
(25, 'PILE', 16,   NULL, 'IDLE',     'OCCUPIED', 'ORDER',      'NONE',    '大兴机场充电桩进入高负荷输出状态',          DATE_SUB(NOW(), INTERVAL 35 MINUTE)),
(26, 'PILE', 18,   NULL, 'IDLE',     'FAULT',    'ALARM',      'PENDING', '东直门桩急停按钮被触发导致故障停机',        DATE_SUB(NOW(), INTERVAL 3 DAY)),
(27, 'GUN',  NULL, 34,   'IDLE',     'FAULT',    'ALARM',      'PENDING', '所属桩急停故障，A枪同步置为故障',           DATE_SUB(NOW(), INTERVAL 3 DAY)),
(28, 'PILE', 21,   NULL, NULL,       'OFFLINE',  'INIT',       'NONE',    '南海子新装桩已建档，待协议联调验收',        DATE_SUB(NOW(), INTERVAL 4 DAY)),
(29, 'PILE', 23,   NULL, 'IDLE',     'OFFLINE',  'MANUAL',     'DONE',    '昌平临时站关停维护，整站设备下线封存',      DATE_SUB(NOW(), INTERVAL 20 DAY)),
(30, 'PILE', 24,   NULL, NULL,       'OFFLINE',  'INIT',       'NONE',    '房山改造桩验收未通过，维持离线整改',        DATE_SUB(NOW(), INTERVAL 8 DAY))
AS nv
ON DUPLICATE KEY UPDATE
    device_type   = nv.device_type,
    pile_id       = nv.pile_id,
    gun_id        = nv.gun_id,
    prev_status   = nv.prev_status,
    new_status    = nv.new_status,
    change_source = nv.change_source,
    todo_status   = nv.todo_status,
    remark        = nv.remark,
    changed_at    = nv.changed_at;

SET @skip_auto_status_log = NULL;


-- ============================================================================
-- 【业务线 2 & 业务线 5】订单计费与结算对账测试数据（负责人：成员 2 / 孙墨涵）
-- ============================================================================
-- 前置：线1 的 station / charging_gun / charging_pile 已有数据
--       枪号和站号都从库里实时取，不写死；user_id 9001~9020、vehicle_id 5001~5020 是虚构值

-- 1) 电价规则：全部场站 × 5 个时段（条数随 station 表行数变化，20 个站即 100 条）
INSERT INTO pricing_rule
(station_id, period, start_time, end_time, energy_price, service_price,
 free_minutes, min_charge, effective_from, effective_to, is_active)
SELECT s.station_id, p.period, p.st, p.et, p.ep, p.sp, 0, 0.00, '2025-01-01', NULL, 1
FROM station s
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
WITH RECURSIVE seq AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM seq WHERE n < 20),
gun AS (SELECT g.gun_id, p.station_id,
               ROW_NUMBER() OVER (ORDER BY g.gun_id) AS rn
        FROM charging_gun g JOIN charging_pile p ON p.pile_id = g.pile_id)
SELECT CONCAT('SEED2025', LPAD(n, 6, '0')),
       9000 + n, 5000 + n, g.gun_id, g.station_id,
       TIMESTAMP('2025-03-01 08:00:00') + INTERVAL n * 7 HOUR,
       CASE WHEN n <= 5 THEN NULL
            ELSE TIMESTAMP('2025-03-01 08:00:00') + INTERVAL n * 7 HOUR + INTERVAL 150 MINUTE END,
       20, 90, 20 + n,
       CASE WHEN MOD(n, 3) = 0 THEN 2.00 ELSE 0.00 END,
       CASE WHEN n <= 5 THEN 'CHARGING' ELSE 'PAID' END
FROM seq
JOIN gun g ON g.rn = n;

-- 3) 用存储过程给已结束的订单算分时明细（顺便验证过程能批量跑）
DROP PROCEDURE IF EXISTS seed_calc;
DELIMITER $$
CREATE PROCEDURE seed_calc()
BEGIN
    DECLARE v_done INT DEFAULT 0;
    DECLARE v_oid BIGINT;
    -- 只算本脚本插入的种子订单，避免重跑时改动库里别人的历史订单
    DECLARE cur CURSOR FOR SELECT order_id FROM charge_order
        WHERE end_time IS NOT NULL AND order_no LIKE 'SEED2025%' ORDER BY order_id;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_done = 1;
    OPEN cur;
    calc_loop: LOOP
        FETCH cur INTO v_oid;
        IF v_done = 1 THEN LEAVE calc_loop; END IF;
        CALL sp_order_calc(v_oid);
    END LOOP;
    CLOSE cur;
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


-- ============================================================================
-- 【业务线 3】设备监控与工单测试数据（负责人：成员 3 / 代润森）
-- 数据规模：pile_telemetry(24) / alarm_record(22) / repair_order(24)
-- 说明：遥测采用固定时间戳便于重复执行；告警与工单保留 1:N 演示样本
--       （alarm_id=21/22 各挂 2 张工单：1 张已闭环 + 1 张处理中）。
-- ============================================================================

-- 1. 分钟级遥测（24 条，按桩每分钟一条，故障/离线桩携带对应 fault_code）
INSERT INTO pile_telemetry (
    telemetry_id, pile_id, collected_at, output_voltage, output_current, power,
    module_temp, offline_duration_sec, fault_code
) VALUES
(1,  1,  TIMESTAMPADD(MINUTE, 1,  '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 28.5, 0,    NULL),
(2,  2,  TIMESTAMPADD(MINUTE, 2,  '2026-11-15 08:00:00'), 420.0, 52.00, 21.840, 39.2, 0, NULL),
(3,  3,  TIMESTAMPADD(MINUTE, 3,  '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 30.1, 0,    NULL),
(4,  4,  TIMESTAMPADD(MINUTE, 4,  '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 26.3, 0,    NULL),
(5,  5,  TIMESTAMPADD(MINUTE, 5,  '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 31.2, 0,    NULL),
(6,  6,  TIMESTAMPADD(MINUTE, 6,  '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 55.8, 0,    'E_CONTACTOR_STUCK'),
(7,  7,  TIMESTAMPADD(MINUTE, 7,  '2026-11-15 08:00:00'), 480.0, 60.00, 28.800, 45.1, 0, NULL),
(8,  8,  TIMESTAMPADD(MINUTE, 8,  '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 27.4, 0,    NULL),
(9,  9,  TIMESTAMPADD(MINUTE, 9,  '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 29.0, 0,    NULL),
(10, 10, TIMESTAMPADD(MINUTE, 10, '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 33.6, 0,    NULL),
(11, 11, TIMESTAMPADD(MINUTE, 11, '2026-11-15 08:00:00'), 500.0, 55.00, 27.500, 47.3, 0, NULL),
(12, 12, TIMESTAMPADD(MINUTE, 12, '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 28.8, 0,    NULL),
(13, 13, TIMESTAMPADD(MINUTE, 13, '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 0.0,  45,   'E_COMM_TIMEOUT'),
(14, 14, TIMESTAMPADD(MINUTE, 14, '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 35.2, 0,    NULL),
(15, 15, TIMESTAMPADD(MINUTE, 15, '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 30.7, 0,    NULL),
(16, 16, TIMESTAMPADD(MINUTE, 16, '2026-11-15 08:00:00'), 450.0, 48.00, 21.600, 42.9, 0, NULL),
(17, 17, TIMESTAMPADD(MINUTE, 17, '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 27.1, 0,    NULL),
(18, 18, TIMESTAMPADD(MINUTE, 18, '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 61.3, 0,    'E_ESTOP_TRIGGERED'),
(19, 19, TIMESTAMPADD(MINUTE, 19, '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 26.9, 0,    NULL),
(20, 20, TIMESTAMPADD(MINUTE, 20, '2026-11-15 08:00:00'), 0.0,   NULL, 0.000, 31.8, 0,    NULL),
(21, 21, TIMESTAMPADD(MINUTE, 21, '2026-11-15 08:00:00'), NULL,  NULL, 0.000, 0.0,  120,  'E_OFFLINE'),
(22, 22, TIMESTAMPADD(MINUTE, 22, '2026-11-15 08:00:00'), NULL,  NULL, 0.000, 0.0,  300,  'E_OFFLINE'),
(23, 23, TIMESTAMPADD(MINUTE, 23, '2026-11-15 08:00:00'), NULL,  NULL, 0.000, 0.0,  1200, 'E_OFFLINE'),
(24, 24, TIMESTAMPADD(MINUTE, 24, '2026-11-15 08:00:00'), NULL,  NULL, 0.000, 0.0,  300,  'E_OFFLINE')
AS nv
ON DUPLICATE KEY UPDATE
    pile_id              = nv.pile_id,
    output_voltage       = nv.output_voltage,
    output_current       = nv.output_current,
    power                = nv.power,
    module_temp          = nv.module_temp,
    offline_duration_sec = nv.offline_duration_sec,
    fault_code           = nv.fault_code;


-- 2. 设备告警（22 条：覆盖 ACTIVE / PROCESSING / RECOVERED / CLOSED 与四级等级）
INSERT INTO alarm_record (
    alarm_id, pile_id, gun_id, alarm_code, alarm_level, original_level,
    response_limit_min, occurred_at, recovered_at, status, escalated_count, escalated_at
) VALUES
(1,  6,  11, 'E_CONTACTOR_STUCK',   'L1', 'L1', 15,  '2026-11-16 09:30:00', NULL,                 'ACTIVE',     0, NULL),
(2,  13, NULL, 'E_COMM_TIMEOUT',     'L2', 'L2', 30,  '2026-11-16 08:00:00', NULL,                 'ACTIVE',     0, NULL),
(3,  18, 34,  'E_ESTOP_TRIGGERED',   'L3', 'L3', 120, '2026-11-15 10:00:00', NULL,                 'ACTIVE',     0, NULL),
(4,  2,  3,   'E_MODULE_OVERHEAT',   'L1', 'L1', 15,  '2026-11-16 10:20:00', NULL,                 'PROCESSING', 0, NULL),
(5,  6,  11,  'E_CONTACTOR_STUCK',   'L2', 'L2', 30,  '2026-11-14 06:00:00', '2026-11-14 10:00:00', 'RECOVERED',  0, NULL),
(6,  6,  11,  'E_INSULATION_LOW',    'L1', 'L1', 15,  '2026-11-13 06:00:00', '2026-11-13 09:00:00', 'CLOSED',     0, NULL),
(7,  13, NULL, 'E_COMM_TIMEOUT',     'L3', 'L3', 120, '2026-11-12 07:30:00', '2026-11-12 12:00:00', 'RECOVERED',  0, NULL),
(8,  18, 34,  'E_ESTOP_TRIGGERED',   'L2', 'L2', 30,  '2026-11-11 07:30:00', '2026-11-11 10:00:00', 'CLOSED',     0, NULL),
(9,  1,  NULL, 'E_POWER_SUPPLY_ABN', 'L4', 'L4', 480, '2026-11-16 11:00:00', NULL,                 'PROCESSING', 0, NULL),
(10, 13, 25,  'E_GUN_LOCK_FAIL',     'L3', 'L2', 120, '2026-11-16 06:00:00', NULL,                 'PROCESSING', 1, '2026-11-16 08:01:00'),
(11, 11, 21,  'E_OVER_CURRENT',      'L2', 'L2', 30,  '2026-11-16 09:45:00', NULL,                 'PROCESSING', 0, NULL),
(12, 11, 21,  'E_OVER_CURRENT',      'L1', 'L1', 15,  '2026-11-15 08:00:00', '2026-11-15 08:20:00', 'RECOVERED',  0, NULL),
(13, 16, 31,  'E_COOLING_FAIL',      'L2', 'L2', 30,  '2026-11-14 07:00:00', '2026-11-14 09:00:00', 'CLOSED',     0, NULL),
(14, 7,  13,  'E_OVER_VOLTAGE',      'L3', 'L3', 120, '2026-11-16 08:30:00', NULL,                 'PROCESSING', 0, NULL),
(15, 10, NULL, 'E_TEMP_SENSOR_FAIL', 'L4', 'L4', 480, '2026-11-15 05:00:00', NULL,                 'PROCESSING', 0, NULL),
(16, 2,  3,   'E_MODULE_OVERHEAT',   'L2', 'L2', 30,  '2026-11-13 06:00:00', '2026-11-13 07:30:00', 'RECOVERED',  0, NULL),
(17, 12, NULL, 'E_FAN_SPEED_LOW',    'L3', 'L3', 120, '2026-11-12 04:00:00', '2026-11-12 09:00:00', 'CLOSED',     0, NULL),
(18, 9,  17,  'E_OVER_CURRENT',      'L1', 'L1', 15,  '2026-11-16 10:50:00', NULL,                 'PROCESSING', 0, NULL),
(19, 8,  NULL, 'E_COMM_RETRY_LIMIT', 'L2', 'L1', 30,  '2026-11-16 07:10:00', NULL,                 'PROCESSING', 1, '2026-11-16 07:41:00'),
(20, 14, NULL, 'E_COOLANT_LEAK',     'L4', 'L4', 480, '2026-11-11 03:00:00', '2026-11-11 12:00:00', 'RECOVERED',  0, NULL),
(21, 15, 29,  'E_OVER_CURRENT',      'L2', 'L2', 30,  '2026-11-16 08:15:00', NULL,                 'PROCESSING', 0, NULL),
(22, 20, 36,  'E_OVER_TEMPERATURE',  'L3', 'L3', 120, '2026-11-15 11:00:00', NULL,                 'PROCESSING', 0, NULL)
AS nv
ON DUPLICATE KEY UPDATE
    pile_id             = nv.pile_id,
    gun_id              = nv.gun_id,
    alarm_code          = nv.alarm_code,
    alarm_level         = nv.alarm_level,
    original_level      = nv.original_level,
    response_limit_min  = nv.response_limit_min,
    occurred_at         = nv.occurred_at,
    recovered_at        = nv.recovered_at,
    status              = nv.status,
    escalated_count     = nv.escalated_count,
    escalated_at        = nv.escalated_at;


-- 3. 维修工单（24 条：alarm_id=21/22 各 2 张工单演示 1:N；时间节点严格递增）
--    种子重跑时临时跳过“同告警仅一张未关闭工单”守卫；生产写入仍由触发器和 DAO 行锁保护。
--    user_account 测试账号在本段之后才写入，因此临时关闭外键检查；脚本末尾恢复。
SET @skip_repair_order_guard = 1;
SET @old_foreign_key_checks = @@FOREIGN_KEY_CHECKS;
SET FOREIGN_KEY_CHECKS = 0;

INSERT INTO repair_order (
    work_order_id, alarm_id, maintainer_id, status, dispatched_at, accepted_at,
    arrived_at, repaired_at, verified_at, closed_at, replaced_parts, result
) VALUES
(1,  5,  2, 'CLOSED',     '2026-11-14 06:05:00', '2026-11-14 06:10:00', '2026-11-14 06:20:00', '2026-11-14 07:30:00', '2026-11-14 07:50:00', '2026-11-14 08:00:00', '主接触器', '首次维修更换后复发'),
(2,  6,  3, 'CLOSED',     '2026-11-13 06:05:00', '2026-11-13 06:10:00', '2026-11-13 06:20:00', '2026-11-13 07:10:00', '2026-11-13 07:20:00', '2026-11-13 07:30:00', NULL, '首次绝缘复测通过后复发'),
(3,  7,  4, 'CLOSED',     '2026-11-12 07:35:00', '2026-11-12 07:45:00', '2026-11-12 08:00:00', '2026-11-12 08:50:00', '2026-11-12 09:00:00', '2026-11-12 09:10:00', '通信模块', '首次模块复位后复发'),
(4,  4,  2, 'ACCEPTED',   '2026-11-16 10:22:00', '2026-11-16 10:30:00', NULL,                 NULL,                 NULL,                 NULL,                 NULL, NULL),
(5,  5,  3, 'CLOSED',     '2026-11-14 08:02:00', '2026-11-14 08:05:00', '2026-11-14 08:20:00', '2026-11-14 09:30:00', '2026-11-14 09:50:00', '2026-11-14 10:00:00', '主接触器', '更换主接触器并测试通过'),
(6,  6,  4, 'CLOSED',     '2026-11-13 08:02:00', '2026-11-13 08:06:00', '2026-11-13 08:15:00', '2026-11-13 08:40:00', '2026-11-13 08:55:00', '2026-11-13 09:00:00', NULL, '绝缘检测复测正常'),
(7,  7,  2, 'CLOSED',     '2026-11-12 09:20:00', '2026-11-12 09:30:00', '2026-11-12 09:50:00', '2026-11-12 11:20:00', '2026-11-12 11:45:00', '2026-11-12 12:00:00', '通信模块', '更换4G通信模块恢复心跳'),
(8,  8,  3, 'CLOSED',     '2026-11-11 07:35:00', '2026-11-11 07:40:00', '2026-11-11 08:00:00', '2026-11-11 09:30:00', '2026-11-11 09:50:00', '2026-11-11 10:00:00', NULL, '复位急停按钮并测试'),
(9,  9,  5, 'DISPATCHED', '2026-11-16 11:02:00', NULL,                 NULL,                 NULL,                 NULL,                 NULL,                 NULL, NULL),
(10, 10, 2, 'ACCEPTED',   '2026-11-16 06:02:00', '2026-11-16 06:10:00', NULL,                 NULL,                 NULL,                 NULL,                 NULL, NULL),
(11, 11, 3, 'DISPATCHED', '2026-11-16 09:47:00', NULL,                 NULL,                 NULL,                 NULL,                 NULL,                 NULL, NULL),
(12, 12, 4, 'CLOSED',     '2026-11-15 08:02:00', '2026-11-15 08:04:00', '2026-11-15 08:08:00', '2026-11-15 08:15:00', '2026-11-15 08:18:00', '2026-11-15 08:20:00', NULL, '重新标定电流采样'),
(13, 13, 5, 'CLOSED',     '2026-11-14 07:02:00', '2026-11-14 07:10:00', '2026-11-14 07:30:00', '2026-11-14 08:40:00', '2026-11-14 08:55:00', '2026-11-14 09:00:00', '散热风扇', '更换散热风扇并验证风量'),
(14, 14, 2, 'DISPATCHED', '2026-11-16 08:32:00', NULL,                 NULL,                 NULL,                 NULL,                 NULL,                 NULL, NULL),
(15, 15, 3, 'ACCEPTED',   '2026-11-15 05:02:00', '2026-11-15 05:20:00', NULL,                 NULL,                 NULL,                 NULL,                 NULL, NULL),
(16, 16, 4, 'CLOSED',     '2026-11-13 06:02:00', '2026-11-13 06:08:00', '2026-11-13 06:20:00', '2026-11-13 07:10:00', '2026-11-13 07:25:00', '2026-11-13 07:30:00', NULL, '清理风道并复测温度'),
(17, 17, 5, 'CLOSED',     '2026-11-12 04:05:00', '2026-11-12 04:15:00', '2026-11-12 04:40:00', '2026-11-12 08:30:00', '2026-11-12 08:50:00', '2026-11-12 09:00:00', '风扇驱动板', '更换风扇驱动板'),
(18, 18, 2, 'DISPATCHED', '2026-11-16 10:52:00', NULL,                 NULL,                 NULL,                 NULL,                 NULL,                 NULL, NULL),
(19, 19, 3, 'ARRIVED',    '2026-11-16 07:12:00', '2026-11-16 07:20:00', '2026-11-16 07:35:00', NULL,                 NULL,                 NULL,                 NULL, NULL),
(20, 20, 4, 'CLOSED',     '2026-11-11 03:10:00', '2026-11-11 03:30:00', '2026-11-11 04:00:00', '2026-11-11 11:00:00', '2026-11-11 11:40:00', '2026-11-11 12:00:00', '液冷管接头', '更换接头并补充冷却液'),
(21, 21, 5, 'CLOSED',     '2026-11-16 08:16:00', '2026-11-16 08:20:00', '2026-11-16 08:35:00', '2026-11-16 09:10:00', '2026-11-16 09:20:00', '2026-11-16 09:30:00', NULL, '首次维修未彻底消除，已闭环'),
(22, 21, 5, 'DISPATCHED', '2026-11-16 10:40:00', NULL,                 NULL,                 NULL,                 NULL,                 NULL,                 NULL, NULL),
(23, 22, 2, 'CLOSED',     '2026-11-15 11:05:00', '2026-11-15 11:15:00', '2026-11-15 11:35:00', '2026-11-15 14:00:00', '2026-11-15 14:20:00', '2026-11-15 14:30:00', NULL, '首次处置后复测通过'),
(24, 22, 2, 'ACCEPTED',   '2026-11-15 15:10:00', '2026-11-15 15:20:00', NULL,                 NULL,                 NULL,                 NULL,                 NULL, NULL)
AS nv
ON DUPLICATE KEY UPDATE
    alarm_id       = nv.alarm_id,
    maintainer_id  = nv.maintainer_id,
    status         = nv.status,
    dispatched_at  = nv.dispatched_at,
    accepted_at    = nv.accepted_at,
    arrived_at     = nv.arrived_at,
    repaired_at    = nv.repaired_at,
    verified_at    = nv.verified_at,
    closed_at      = nv.closed_at,
    replaced_parts = nv.replaced_parts,
    result         = nv.result;

SET @skip_repair_order_guard = NULL;
SET FOREIGN_KEY_CHECKS = @old_foreign_key_checks;

-- ============================================================================
-- 【业务线 4】用户、车辆、企业车队与优惠券测试数据（负责人：成员 4 / 李海波）
-- 数据规模：fleet_account(5) / user_account(32) / vehicle(25) / coupon_campaign(6)
--           / coupon_record(24) / coupon_redeem_record(10)
-- 说明：
--   1. 插入顺序严格按外键依赖：fleet_account -> user_account -> vehicle ->
--      coupon_campaign -> coupon_record -> coupon_redeem_record；
--   2. 演示账号统一口令 Demo@123456（PBKDF2-SHA256，迭代 100000 次），
--      32 个账号轮换使用 4 个真实哈希串，可直接 POST /api/auth/login 登录；
--   3. 敏感字段三列分离：phone_cipher / plate_cipher 为 'SEED_FERNET::<明文>' 占位密文，
--      真实写入路径走 app/dao/common.py 的 encrypt_sensitive（Fernet 对称加密）；
--      phone_hash / plate_hash 为 SHA2(CONCAT('seed:', 明文), 256) 占位盲索引，
--      真实写入路径走 hash_sensitive_lookup（HMAC-SHA256，密钥取自 SECRET_KEY），
--      故演示库按盲索引等值检索与线上口径不同，重置真实数据请统一走 DAO 写入；
--      phone_masked / plate_masked 为与明文严格对应的手写脱敏展示值；
--      8 位新能源号牌按“保留前 2 位与后 2 位”脱敏，与 DDL 注释示例同形；
--   4. 9001~9020 与业务线 2 的充电订单车主严格对齐（charge_order.user_id = 9000 + n、
--      vehicle_id = 5000 + n），5001~5020 为对应车辆；2~5 是业务线 3 维修工单的
--      maintainer_id；1 为平台管理员，6/7 为客服结算专员，9101~9105 绑定车队 1~5；
--   5. 核销记录不写死订单号：用 INSERT ... SELECT 从 charge_order 实时取 status='PAID'
--      的订单，逐条校验“券持有人 = 订单车主 + 场站取订单场站 + 抵扣额 = LEAST(券面额, 订单金额)”；
--      因此本段必须排在业务线 2 之后执行（01 中跨线外键默认注释，未启用物理外键）；
--   6. 发券/核销前临时置 @skip_coupon_guard = 1（理由见 03 脚本业务线 4 注释：
--      可批量造出 PAUSED/ENDED 活动券、过期券与作废券），核销完成后重置为 NULL；
--      跳过发券触发器后 issued_count 不再自动累加，故按实际发券张数回填且不超过计划量；
--   7. 全部语句采用 INSERT ... AS nv ON DUPLICATE KEY UPDATE，保证脚本可反复重跑复位。
-- ============================================================================

-- 1. 企业车队档案表（5 条：4 家 ACTIVE 正常合作 + 1 家 SUSPENDED 暂停，授信均为 20 万级）
INSERT INTO fleet_account (
    fleet_id, fleet_code, fleet_name, credit_code, contact_name,
    contact_phone_cipher, contact_phone_hash, contact_phone_masked,
    credit_limit, credit_used, status
) VALUES
(1, 'FL-BJ-001', '北京公交集团电动出行分公司',   '91110108MA01F0011A', '李建国',
 CONCAT('SEED_FERNET::', '13800000801'), SHA2(CONCAT('seed:', '13800000801'), 256), '138****0801',
 200000.00, 132480.50, 'ACTIVE'),
(2, 'FL-BJ-002', '滴滴出行企业服务（北京）有限公司', '91110105MA01F0022B', '王海涛',
 CONCAT('SEED_FERNET::', '13800000802'), SHA2(CONCAT('seed:', '13800000802'), 256), '138****0802',
 180000.00, 96500.00, 'ACTIVE'),
(3, 'FL-BJ-003', '京东物流华北新能源车队',       '91110113MA01F0033C', '张春燕',
 CONCAT('SEED_FERNET::', '13800000803'), SHA2(CONCAT('seed:', '13800000803'), 256), '138****0803',
 260000.00, 204300.00, 'ACTIVE'),
(4, 'FL-BJ-004', '顺丰速运北京新能源运输队',     '91110112MA01F0044D', '刘志强',
 CONCAT('SEED_FERNET::', '13800000804'), SHA2(CONCAT('seed:', '13800000804'), 256), '138****0804',
 150000.00, 48200.00, 'ACTIVE'),
(5, 'FL-BJ-005', '首汽约车北京运营中心',         '91110106MA01F0055E', '陈晓东',
 CONCAT('SEED_FERNET::', '13800000805'), SHA2(CONCAT('seed:', '13800000805'), 256), '138****0805',
 220000.00, 176800.00, 'SUSPENDED')
AS nv
ON DUPLICATE KEY UPDATE
    fleet_code           = nv.fleet_code,
    fleet_name           = nv.fleet_name,
    credit_code          = nv.credit_code,
    contact_name         = nv.contact_name,
    contact_phone_cipher = nv.contact_phone_cipher,
    contact_phone_hash   = nv.contact_phone_hash,
    contact_phone_masked = nv.contact_phone_masked,
    credit_limit         = nv.credit_limit,
    credit_used          = nv.credit_used,
    status               = nv.status;

-- 2. 平台账号表（32 条：1 admin + 4 ops + 2 settlement + 20 driver + 5 fleet）
--    1=admin（平台运营管理员）；2~5=ops（业务线 3 维修工单 maintainer_id）；
--    6/7=settlement（客服与结算）；9001~9020=driver（业务线 2 订单车主）；
--    9101~9105=fleet（企业用车人，fleet_id 依次为 1~5，满足 chk_user_fleet_role）
INSERT INTO user_account (
    user_id, username, password_hash, role, fleet_id,
    phone_cipher, phone_hash, phone_masked,
    realname_status, realname_verified_at, register_channel, member_level,
    balance, status, failed_login_count, locked_until, last_login_at, created_at
) VALUES
(1, 'admin', 'pbkdf2_sha256$100000$00d3e2451f81d2171fdb590e75b52873$a0d8f72605c427fe93448a0e6dd2156d0557f9e106a74e16ae0eb57a9914eda4', 'admin', NULL,
 CONCAT('SEED_FERNET::', '13800000001'), SHA2(CONCAT('seed:', '13800000001'), 256), '138****0001',
 'VERIFIED', '2025-09-01 10:30:00', 'OFFLINE', 'PLATINUM', 0.00, 'ACTIVE', 0, NULL, '2026-11-19 08:30:00', '2025-09-01 09:00:00'),
(2, 'wang_ops', 'pbkdf2_sha256$100000$0ef0755bbe23ae477dd46ed6a2b3fd48$b77fd9afd84e370ef8a2d6d959127b7ea70d134b4d028be97d4f2867db315a2b', 'ops', NULL,
 CONCAT('SEED_FERNET::', '13800000002'), SHA2(CONCAT('seed:', '13800000002'), 256), '138****0002',
 'VERIFIED', '2025-09-05 09:20:00', 'OFFLINE', 'GOLD', 0.00, 'ACTIVE', 0, NULL, '2026-11-19 07:50:00', '2025-09-05 09:00:00'),
(3, 'liu_ops', 'pbkdf2_sha256$100000$fe887849a41bbc74aea9a610b61bf34b$6180e571270c27c54f8bc750aa1541c937b631657eae5df704d1e9e3c68dd500', 'ops', NULL,
 CONCAT('SEED_FERNET::', '13800000003'), SHA2(CONCAT('seed:', '13800000003'), 256), '138****0003',
 'VERIFIED', '2025-09-06 14:10:00', 'OFFLINE', 'GOLD', 0.00, 'ACTIVE', 0, NULL, '2026-11-18 18:20:00', '2025-09-06 14:00:00'),
(4, 'chen_ops', 'pbkdf2_sha256$100000$85e9b2f27eea77b026c8e551e99f4d10$3cc93153f43f40edb3b9ee01ad460078d5cd2b45df1b7f84da72c0078d678076', 'ops', NULL,
 CONCAT('SEED_FERNET::', '13800000004'), SHA2(CONCAT('seed:', '13800000004'), 256), '138****0004',
 'VERIFIED', '2025-09-12 10:40:00', 'OFFLINE', 'SILVER', 0.00, 'ACTIVE', 0, NULL, '2026-11-19 06:40:00', '2025-09-12 10:30:00'),
(5, 'zhao_ops', 'pbkdf2_sha256$100000$00d3e2451f81d2171fdb590e75b52873$a0d8f72605c427fe93448a0e6dd2156d0557f9e106a74e16ae0eb57a9914eda4', 'ops', NULL,
 CONCAT('SEED_FERNET::', '13800000005'), SHA2(CONCAT('seed:', '13800000005'), 256), '138****0005',
 'VERIFIED', '2025-10-08 09:10:00', 'OFFLINE', 'SILVER', 0.00, 'ACTIVE', 0, NULL, '2026-11-17 20:05:00', '2025-10-08 09:00:00'),
(6, 'zhang_settle', 'pbkdf2_sha256$100000$0ef0755bbe23ae477dd46ed6a2b3fd48$b77fd9afd84e370ef8a2d6d959127b7ea70d134b4d028be97d4f2867db315a2b', 'settlement', NULL,
 CONCAT('SEED_FERNET::', '13800000006'), SHA2(CONCAT('seed:', '13800000006'), 256), '138****0006',
 'VERIFIED', '2025-09-20 11:00:00', 'OFFLINE', 'GOLD', 0.00, 'ACTIVE', 0, NULL, '2026-11-19 09:05:00', '2025-09-20 10:50:00'),
(7, 'sun_settle', 'pbkdf2_sha256$100000$fe887849a41bbc74aea9a610b61bf34b$6180e571270c27c54f8bc750aa1541c937b631657eae5df704d1e9e3c68dd500', 'settlement', NULL,
 CONCAT('SEED_FERNET::', '13800000007'), SHA2(CONCAT('seed:', '13800000007'), 256), '138****0007',
 'VERIFIED', '2025-09-22 15:30:00', 'OFFLINE', 'GOLD', 0.00, 'ACTIVE', 0, NULL, '2026-11-18 17:45:00', '2025-09-22 15:20:00'),
(9001, 'driver_9001', 'pbkdf2_sha256$100000$85e9b2f27eea77b026c8e551e99f4d10$3cc93153f43f40edb3b9ee01ad460078d5cd2b45df1b7f84da72c0078d678076', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009001'), SHA2(CONCAT('seed:', '13800009001'), 256), '138****9001',
 'VERIFIED', '2025-10-12 10:00:00', 'APP', 'SILVER', 128.50, 'ACTIVE', 0, NULL, '2026-11-19 07:30:00', '2025-10-12 09:15:00'),
(9002, 'driver_9002', 'pbkdf2_sha256$100000$00d3e2451f81d2171fdb590e75b52873$a0d8f72605c427fe93448a0e6dd2156d0557f9e106a74e16ae0eb57a9914eda4', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009002'), SHA2(CONCAT('seed:', '13800009002'), 256), '138****9002',
 'VERIFIED', '2025-10-18 21:00:00', 'MINI_PROGRAM', 'NORMAL', 46.00, 'ACTIVE', 0, NULL, '2026-11-18 21:10:00', '2025-10-18 20:30:00'),
(9003, 'driver_9003', 'pbkdf2_sha256$100000$0ef0755bbe23ae477dd46ed6a2b3fd48$b77fd9afd84e370ef8a2d6d959127b7ea70d134b4d028be97d4f2867db315a2b', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009003'), SHA2(CONCAT('seed:', '13800009003'), 256), '138****9003',
 'VERIFIED', '2025-11-02 08:30:00', 'APP', 'NORMAL', 0.00, 'ACTIVE', 0, NULL, '2026-11-16 12:20:00', '2025-11-02 08:05:00'),
(9004, 'driver_9004', 'pbkdf2_sha256$100000$fe887849a41bbc74aea9a610b61bf34b$6180e571270c27c54f8bc750aa1541c937b631657eae5df704d1e9e3c68dd500', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009004'), SHA2(CONCAT('seed:', '13800009004'), 256), '138****9004',
 'PENDING', NULL, 'MINI_PROGRAM', 'NORMAL', 200.00, 'ACTIVE', 0, NULL, '2026-11-15 19:00:00', '2025-11-21 19:40:00'),
(9005, 'driver_9005', 'pbkdf2_sha256$100000$85e9b2f27eea77b026c8e551e99f4d10$3cc93153f43f40edb3b9ee01ad460078d5cd2b45df1b7f84da72c0078d678076', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009005'), SHA2(CONCAT('seed:', '13800009005'), 256), '138****9005',
 'VERIFIED', '2025-12-03 12:40:00', 'WEB', 'NORMAL', 0.00, 'ACTIVE', 0, NULL, '2026-11-14 08:15:00', '2025-12-03 12:10:00'),
(9006, 'driver_9006', 'pbkdf2_sha256$100000$00d3e2451f81d2171fdb590e75b52873$a0d8f72605c427fe93448a0e6dd2156d0557f9e106a74e16ae0eb57a9914eda4', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009006'), SHA2(CONCAT('seed:', '13800009006'), 256), '138****9006',
 'VERIFIED', '2026-01-15 07:50:00', 'APP', 'SILVER', 88.80, 'ACTIVE', 0, NULL, '2026-11-19 06:55:00', '2026-01-15 07:20:00'),
(9007, 'driver_9007', 'pbkdf2_sha256$100000$0ef0755bbe23ae477dd46ed6a2b3fd48$b77fd9afd84e370ef8a2d6d959127b7ea70d134b4d028be97d4f2867db315a2b', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009007'), SHA2(CONCAT('seed:', '13800009007'), 256), '138****9007',
 'VERIFIED', '2026-01-28 21:30:00', 'APP', 'NORMAL', 30.00, 'ACTIVE', 0, NULL, '2026-11-18 19:40:00', '2026-01-28 21:05:00'),
(9008, 'driver_9008', 'pbkdf2_sha256$100000$fe887849a41bbc74aea9a610b61bf34b$6180e571270c27c54f8bc750aa1541c937b631657eae5df704d1e9e3c68dd500', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009008'), SHA2(CONCAT('seed:', '13800009008'), 256), '138****9008',
 'VERIFIED', '2026-02-11 11:10:00', 'MINI_PROGRAM', 'NORMAL', 0.00, 'ACTIVE', 0, NULL, '2026-11-16 17:30:00', '2026-02-11 10:45:00'),
(9009, 'driver_9009', 'pbkdf2_sha256$100000$85e9b2f27eea77b026c8e551e99f4d10$3cc93153f43f40edb3b9ee01ad460078d5cd2b45df1b7f84da72c0078d678076', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009009'), SHA2(CONCAT('seed:', '13800009009'), 256), '138****9009',
 'VERIFIED', '2026-02-26 19:00:00', 'APP', 'NORMAL', 66.60, 'ACTIVE', 1, NULL, '2026-11-13 20:10:00', '2026-02-26 18:30:00'),
(9010, 'driver_9010', 'pbkdf2_sha256$100000$00d3e2451f81d2171fdb590e75b52873$a0d8f72605c427fe93448a0e6dd2156d0557f9e106a74e16ae0eb57a9914eda4', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009010'), SHA2(CONCAT('seed:', '13800009010'), 256), '138****9010',
 'VERIFIED', '2026-03-08 09:30:00', 'WEB', 'GOLD', 520.00, 'ACTIVE', 0, NULL, '2026-11-19 09:20:00', '2026-03-08 09:00:00'),
(9011, 'driver_9011', 'pbkdf2_sha256$100000$0ef0755bbe23ae477dd46ed6a2b3fd48$b77fd9afd84e370ef8a2d6d959127b7ea70d134b4d028be97d4f2867db315a2b', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009011'), SHA2(CONCAT('seed:', '13800009011'), 256), '138****9011',
 'VERIFIED', '2026-03-19 13:50:00', 'APP', 'SILVER', 150.00, 'ACTIVE', 0, NULL, '2026-11-17 18:00:00', '2026-03-19 13:25:00'),
(9012, 'driver_9012', 'pbkdf2_sha256$100000$fe887849a41bbc74aea9a610b61bf34b$6180e571270c27c54f8bc750aa1541c937b631657eae5df704d1e9e3c68dd500', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009012'), SHA2(CONCAT('seed:', '13800009012'), 256), '138****9012',
 'VERIFIED', '2026-04-02 07:20:00', 'MINI_PROGRAM', 'NORMAL', 0.00, 'ACTIVE', 0, NULL, '2026-11-15 07:40:00', '2026-04-02 06:50:00'),
(9013, 'driver_9013', 'pbkdf2_sha256$100000$85e9b2f27eea77b026c8e551e99f4d10$3cc93153f43f40edb3b9ee01ad460078d5cd2b45df1b7f84da72c0078d678076', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009013'), SHA2(CONCAT('seed:', '13800009013'), 256), '138****9013',
 'VERIFIED', '2026-04-17 22:40:00', 'APP', 'NORMAL', 75.50, 'ACTIVE', 0, NULL, '2026-11-16 22:30:00', '2026-04-17 22:10:00'),
(9014, 'driver_9014', 'pbkdf2_sha256$100000$00d3e2451f81d2171fdb590e75b52873$a0d8f72605c427fe93448a0e6dd2156d0557f9e106a74e16ae0eb57a9914eda4', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009014'), SHA2(CONCAT('seed:', '13800009014'), 256), '138****9014',
 'VERIFIED', '2026-05-05 17:00:00', 'APP', 'NORMAL', 12.00, 'ACTIVE', 0, NULL, '2026-11-12 16:50:00', '2026-05-05 16:35:00'),
(9015, 'driver_9015', 'pbkdf2_sha256$100000$0ef0755bbe23ae477dd46ed6a2b3fd48$b77fd9afd84e370ef8a2d6d959127b7ea70d134b4d028be97d4f2867db315a2b', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009015'), SHA2(CONCAT('seed:', '13800009015'), 256), '138****9015',
 'VERIFIED', '2026-05-23 11:40:00', 'MINI_PROGRAM', 'SILVER', 300.00, 'ACTIVE', 0, NULL, '2026-11-18 11:20:00', '2026-05-23 11:15:00'),
(9016, 'driver_9016', 'pbkdf2_sha256$100000$fe887849a41bbc74aea9a610b61bf34b$6180e571270c27c54f8bc750aa1541c937b631657eae5df704d1e9e3c68dd500', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009016'), SHA2(CONCAT('seed:', '13800009016'), 256), '138****9016',
 'VERIFIED', '2026-06-09 09:10:00', 'APP', 'NORMAL', 0.00, 'ACTIVE', 0, NULL, '2026-11-16 09:05:00', '2026-06-09 08:40:00'),
(9017, 'driver_9017', 'pbkdf2_sha256$100000$85e9b2f27eea77b026c8e551e99f4d10$3cc93153f43f40edb3b9ee01ad460078d5cd2b45df1b7f84da72c0078d678076', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009017'), SHA2(CONCAT('seed:', '13800009017'), 256), '138****9017',
 'VERIFIED', '2026-06-27 19:50:00', 'WEB', 'NORMAL', 45.00, 'ACTIVE', 0, NULL, '2026-11-14 19:30:00', '2026-06-27 19:20:00'),
(9018, 'driver_9018', 'pbkdf2_sha256$100000$00d3e2451f81d2171fdb590e75b52873$a0d8f72605c427fe93448a0e6dd2156d0557f9e106a74e16ae0eb57a9914eda4', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009018'), SHA2(CONCAT('seed:', '13800009018'), 256), '138****9018',
 'VERIFIED', '2026-07-14 10:30:00', 'APP', 'SILVER', 180.00, 'ACTIVE', 0, NULL, '2026-11-19 08:10:00', '2026-07-14 10:05:00'),
(9019, 'driver_9019', 'pbkdf2_sha256$100000$0ef0755bbe23ae477dd46ed6a2b3fd48$b77fd9afd84e370ef8a2d6d959127b7ea70d134b4d028be97d4f2867db315a2b', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009019'), SHA2(CONCAT('seed:', '13800009019'), 256), '138****9019',
 'UNVERIFIED', NULL, 'MINI_PROGRAM', 'NORMAL', 0.00, 'ACTIVE', 0, NULL, '2026-11-11 15:20:00', '2026-08-01 15:50:00'),
(9020, 'driver_9020', 'pbkdf2_sha256$100000$fe887849a41bbc74aea9a610b61bf34b$6180e571270c27c54f8bc750aa1541c937b631657eae5df704d1e9e3c68dd500', 'driver', NULL,
 CONCAT('SEED_FERNET::', '13800009020'), SHA2(CONCAT('seed:', '13800009020'), 256), '138****9020',
 'VERIFIED', '2026-10-10 10:00:00', 'APP', 'NORMAL', 58.00, 'ACTIVE', 0, NULL, '2026-11-18 09:45:00', '2026-10-10 09:30:00'),
(9101, 'fleet_9101', 'pbkdf2_sha256$100000$85e9b2f27eea77b026c8e551e99f4d10$3cc93153f43f40edb3b9ee01ad460078d5cd2b45df1b7f84da72c0078d678076', 'fleet', 1,
 CONCAT('SEED_FERNET::', '13800009101'), SHA2(CONCAT('seed:', '13800009101'), 256), '138****9101',
 'VERIFIED', '2025-08-20 10:00:00', 'FLEET_IMPORT', 'GOLD', 0.00, 'ACTIVE', 0, NULL, '2026-11-19 08:45:00', '2025-08-20 09:30:00'),
(9102, 'fleet_9102', 'pbkdf2_sha256$100000$00d3e2451f81d2171fdb590e75b52873$a0d8f72605c427fe93448a0e6dd2156d0557f9e106a74e16ae0eb57a9914eda4', 'fleet', 2,
 CONCAT('SEED_FERNET::', '13800009102'), SHA2(CONCAT('seed:', '13800009102'), 256), '138****9102',
 'VERIFIED', '2025-09-02 14:00:00', 'FLEET_IMPORT', 'SILVER', 0.00, 'ACTIVE', 0, NULL, '2026-11-18 14:30:00', '2025-09-02 13:40:00'),
(9103, 'fleet_9103', 'pbkdf2_sha256$100000$0ef0755bbe23ae477dd46ed6a2b3fd48$b77fd9afd84e370ef8a2d6d959127b7ea70d134b4d028be97d4f2867db315a2b', 'fleet', 3,
 CONCAT('SEED_FERNET::', '13800009103'), SHA2(CONCAT('seed:', '13800009103'), 256), '138****9103',
 'VERIFIED', '2025-10-15 09:30:00', 'FLEET_IMPORT', 'GOLD', 0.00, 'ACTIVE', 0, NULL, '2026-11-19 07:20:00', '2025-10-15 09:00:00'),
(9104, 'fleet_9104', 'pbkdf2_sha256$100000$fe887849a41bbc74aea9a610b61bf34b$6180e571270c27c54f8bc750aa1541c937b631657eae5df704d1e9e3c68dd500', 'fleet', 4,
 CONCAT('SEED_FERNET::', '13800009104'), SHA2(CONCAT('seed:', '13800009104'), 256), '138****9104',
 'VERIFIED', '2026-01-08 16:00:00', 'FLEET_IMPORT', 'SILVER', 0.00, 'ACTIVE', 0, NULL, '2026-11-17 16:10:00', '2026-01-08 15:40:00'),
(9105, 'fleet_9105', 'pbkdf2_sha256$100000$85e9b2f27eea77b026c8e551e99f4d10$3cc93153f43f40edb3b9ee01ad460078d5cd2b45df1b7f84da72c0078d678076', 'fleet', 5,
 CONCAT('SEED_FERNET::', '13800009105'), SHA2(CONCAT('seed:', '13800009105'), 256), '138****9105',
 'VERIFIED', '2026-04-20 11:00:00', 'FLEET_IMPORT', 'SILVER', 0.00, 'ACTIVE', 0, NULL, '2026-11-16 11:30:00', '2026-04-20 10:30:00')
AS nv
ON DUPLICATE KEY UPDATE
    username             = nv.username,
    password_hash        = nv.password_hash,
    role                 = nv.role,
    fleet_id             = nv.fleet_id,
    phone_cipher         = nv.phone_cipher,
    phone_hash           = nv.phone_hash,
    phone_masked         = nv.phone_masked,
    realname_status      = nv.realname_status,
    realname_verified_at = nv.realname_verified_at,
    register_channel     = nv.register_channel,
    member_level         = nv.member_level,
    balance              = nv.balance,
    status               = nv.status,
    failed_login_count   = nv.failed_login_count,
    locked_until         = nv.locked_until,
    last_login_at        = nv.last_login_at,
    created_at           = nv.created_at;

-- 3. 车辆表（25 条：20 辆私家车 + 5 辆企业车队车；3 辆交流慢充车 supports_fast_charge=0）
--    5001~5020 与 driver 9001~9020 一一对应（vehicle_id = 5000 + n <-> user_id = 9000 + n），
--    5101~5105 绑定 9101~9105 并填 fleet_id = 1~5；25 个车牌互不相同，plate_hash 全局唯一。
INSERT INTO vehicle (
    vehicle_id, user_id, fleet_id, plate_cipher, plate_hash, plate_masked, plate_color,
    vehicle_model, battery_capacity_kwh, max_charge_power, supports_fast_charge, bind_status, bound_at
) VALUES
(5001, 9001, NULL, CONCAT('SEED_FERNET::', '京AD10001'), SHA2(CONCAT('seed:', '京AD10001'), 256), '京A***01',
 'GREEN', '比亚迪 汉 EV 2024款 605KM 尊贵型', 85.40, 120.00, 1, 'BOUND', '2025-10-12 10:20:00'),
(5002, 9002, NULL, CONCAT('SEED_FERNET::', '京AD10002'), SHA2(CONCAT('seed:', '京AD10002'), 256), '京A***02',
 'GREEN', '特斯拉 Model 3 长续航全轮驱动版', 78.40, 250.00, 1, 'BOUND', '2025-10-18 21:20:00'),
(5003, 9003, NULL, CONCAT('SEED_FERNET::', '京AD10003'), SHA2(CONCAT('seed:', '京AD10003'), 256), '京A***03',
 'GREEN', '蔚来 ES6 75kWh 签名版', 75.00, 180.00, 1, 'BOUND', '2025-11-02 08:40:00'),
(5004, 9004, NULL, CONCAT('SEED_FERNET::', '京AD10004'), SHA2(CONCAT('seed:', '京AD10004'), 256), '京A***04',
 'GREEN', '小鹏 G6 580 长续航 Max', 66.00, 280.00, 1, 'BOUND', '2025-11-21 20:00:00'),
(5005, 9005, NULL, CONCAT('SEED_FERNET::', '京AD10005'), SHA2(CONCAT('seed:', '京AD10005'), 256), '京A***05',
 'GREEN', '理想 L7 Max 增程版', 42.80, 90.00, 1, 'BOUND', '2025-12-03 12:50:00'),
(5006, 9006, NULL, CONCAT('SEED_FERNET::', '京AD10006'), SHA2(CONCAT('seed:', '京AD10006'), 256), '京A***06',
 'GREEN', '比亚迪 秦 PLUS EV 510KM 豪华型', 57.60, 60.00, 1, 'BOUND', '2026-01-15 08:00:00'),
(5007, 9007, NULL, CONCAT('SEED_FERNET::', '京AD10007'), SHA2(CONCAT('seed:', '京AD10007'), 256), '京A***07',
 'GREEN', '五菱宏光 MINI EV 马卡龙 215KM', 17.30, 7.00, 0, 'BOUND', '2026-01-28 21:40:00'),
(5008, 9008, NULL, CONCAT('SEED_FERNET::', '京AD10008'), SHA2(CONCAT('seed:', '京AD10008'), 256), '京A***08',
 'GREEN', '广汽埃安 AION S Plus 70 智领版', 58.80, 90.00, 1, 'BOUND', '2026-02-11 11:20:00'),
(5009, 9009, NULL, CONCAT('SEED_FERNET::', '京AD10009'), SHA2(CONCAT('seed:', '京AD10009'), 256), '京A***09',
 'GREEN', '极氪 001 WE 后驱版 100kWh', 100.00, 200.00, 1, 'BOUND', '2026-02-26 19:10:00'),
(5010, 9010, NULL, CONCAT('SEED_FERNET::', '京AD10010'), SHA2(CONCAT('seed:', '京AD10010'), 256), '京A***10',
 'GREEN', '大众 ID.4 CROZZ 长续航 PRO', 84.80, 100.00, 1, 'BOUND', '2026-03-08 09:40:00'),
(5011, 9011, NULL, CONCAT('SEED_FERNET::', '京AD10011'), SHA2(CONCAT('seed:', '京AD10011'), 256), '京A***11',
 'GREEN', '宝马 i3 eDrive35L', 70.30, 100.00, 1, 'BOUND', '2026-03-19 14:00:00'),
(5012, 9012, NULL, CONCAT('SEED_FERNET::', '京AD10012'), SHA2(CONCAT('seed:', '京AD10012'), 256), '京A***12',
 'GREEN', '长安 Lumin 糯玉米 210KM', 27.00, 7.00, 0, 'BOUND', '2026-04-02 07:30:00'),
(5013, 9013, NULL, CONCAT('SEED_FERNET::', '京AD10013'), SHA2(CONCAT('seed:', '京AD10013'), 256), '京A***13',
 'GREEN', '特斯拉 Model Y 后轮驱动版', 60.00, 170.00, 1, 'BOUND', '2026-04-17 22:50:00'),
(5014, 9014, NULL, CONCAT('SEED_FERNET::', '京AD10014'), SHA2(CONCAT('seed:', '京AD10014'), 256), '京A***14',
 'GREEN', '问界 M5 智驾版 增程', 40.00, 100.00, 1, 'BOUND', '2026-05-05 17:10:00'),
(5015, 9015, NULL, CONCAT('SEED_FERNET::', '京AD10015'), SHA2(CONCAT('seed:', '京AD10015'), 256), '京A***15',
 'GREEN', '比亚迪 海豹 06 DM-i 120KM 尊贵型', 60.50, 80.00, 1, 'BOUND', '2026-05-23 11:50:00'),
(5016, 9016, NULL, CONCAT('SEED_FERNET::', '京AD10016'), SHA2(CONCAT('seed:', '京AD10016'), 256), '京A***16',
 'GREEN', '零跑 C11 增程 285 智享版', 43.70, 70.00, 1, 'BOUND', '2026-06-09 09:20:00'),
(5017, 9017, NULL, CONCAT('SEED_FERNET::', '京AD10017'), SHA2(CONCAT('seed:', '京AD10017'), 256), '京A***17',
 'GREEN', '奇瑞 小蚂蚁 30.6kWh 磷酸铁锂版', 30.60, 7.00, 0, 'BOUND', '2026-06-27 20:00:00'),
(5018, 9018, NULL, CONCAT('SEED_FERNET::', '京AD10018'), SHA2(CONCAT('seed:', '京AD10018'), 256), '京A***18',
 'GREEN', '深蓝 SL03 515 纯电版', 58.10, 100.00, 1, 'BOUND', '2026-07-14 10:40:00'),
(5019, 9019, NULL, CONCAT('SEED_FERNET::', '京AD10019'), SHA2(CONCAT('seed:', '京AD10019'), 256), '京A***19',
 'GREEN', '哪吒 U-II 500 行业版', 68.20, 90.00, 1, 'BOUND', '2026-08-01 16:00:00'),
(5020, 9020, NULL, CONCAT('SEED_FERNET::', '京AD10020'), SHA2(CONCAT('seed:', '京AD10020'), 256), '京A***20',
 'GREEN', '广汽传祺 E9 PHEV 宗师版', 25.60, 60.00, 1, 'BOUND', '2026-10-10 10:10:00'),
(5101, 9101, 1, CONCAT('SEED_FERNET::', '京AF10021'), SHA2(CONCAT('seed:', '京AF10021'), 256), '京A***21',
 'GREEN', '比亚迪 e9 网约出行版', 63.40, 100.00, 1, 'BOUND', '2025-08-20 10:20:00'),
(5102, 9102, 2, CONCAT('SEED_FERNET::', '京AF10022'), SHA2(CONCAT('seed:', '京AF10022'), 256), '京A***22',
 'GREEN', '广汽埃安 AION Y Plus 610 智享版', 61.50, 80.00, 1, 'BOUND', '2025-09-02 14:20:00'),
(5103, 9103, 3, CONCAT('SEED_FERNET::', '京AF10023'), SHA2(CONCAT('seed:', '京AF10023'), 256), '京A***23',
 'GREEN', '东风风神 E70 500 网约版', 50.30, 60.00, 1, 'BOUND', '2025-10-15 09:40:00'),
(5104, 9104, 4, CONCAT('SEED_FERNET::', '京AF10024'), SHA2(CONCAT('seed:', '京AF10024'), 256), '京A***24',
 'GREEN', '开瑞 优优 EV 厢式运输车', 41.90, 60.00, 1, 'BOUND', '2026-01-08 16:20:00'),
(5105, 9105, 5, CONCAT('SEED_FERNET::', '京AF10025'), SHA2(CONCAT('seed:', '京AF10025'), 256), '京A***25',
 'GREEN', '荣威 Ei5 旅行版 网约车', 52.50, 60.00, 1, 'BOUND', '2026-04-20 11:10:00')
AS nv
ON DUPLICATE KEY UPDATE
    user_id              = nv.user_id,
    fleet_id             = nv.fleet_id,
    plate_cipher         = nv.plate_cipher,
    plate_hash           = nv.plate_hash,
    plate_masked         = nv.plate_masked,
    plate_color          = nv.plate_color,
    vehicle_model        = nv.vehicle_model,
    battery_capacity_kwh = nv.battery_capacity_kwh,
    max_charge_power     = nv.max_charge_power,
    supports_fast_charge = nv.supports_fast_charge,
    bind_status          = nv.bind_status,
    bound_at             = nv.bound_at;

-- 4. 优惠活动表（6 条：ACTIVE 3 / PAUSED 1 / ENDED 1 / DRAFT 1；CASH 3 / DISCOUNT 1 / SERVICE_FEE_FREE 2）
--    1 为「新人券包」全场站通用；2 仅限 station_id=2（五道口）、3 仅限 station_id=5（望京SOHO）；
--    4/5/6 为全场站通用（applicable_station_id = NULL）。
--    当下（2026-11-19）活动 1/2/4 均为 ACTIVE 且落在有效期内，可供发券与核销演示。
INSERT INTO coupon_campaign (
    campaign_id, campaign_code, campaign_name, coupon_kind, face_value, threshold_amount,
    applicable_station_id, valid_from, valid_to, issue_total, issued_count, per_user_limit,
    redeem_rule, status, created_by
) VALUES
(1, 'CP-202506-NEW01', '新人券包-首充20元无门槛立减', 'CASH', 20.00, 0.00,
 NULL, '2025-06-01', '2027-06-30', 5000, 0, 1,
 '新注册车主首笔充电可用，单笔订单限用一张，不可与其他优惠叠加', 'ACTIVE', 1),
(2, 'CP-202511-ST02', '五道口购物中心站站庆10元立减', 'CASH', 10.00, 0.00,
 2, '2025-11-01', '2027-10-31', 1200, 0, 2,
 '仅限五道口购物中心充电站（station_id=2）使用，单笔订单限用一张', 'ACTIVE', 1),
(3, 'CP-202507-SVC05', '望京SOHO服务费5元减免券', 'SERVICE_FEE_FREE', 5.00, 0.00,
 5, '2025-07-01', '2026-09-30', 800, 0, 2,
 '仅限望京SOHO地下超充站（station_id=5）使用，服务费减免不折现、不找零', 'PAUSED', 1),
(4, 'CP-202611-D11', '双十一充电8元折扣券', 'DISCOUNT', 8.00, 0.00,
 NULL, '2026-11-01', '2026-11-30', 2000, 0, 3,
 '折扣权益按 8.00 元折算抵扣，单笔订单限用一张，不可与其他优惠叠加', 'ACTIVE', 1),
(5, 'CP-202501-OLD15', '老用户回馈15元立减', 'CASH', 15.00, 30.00,
 NULL, '2025-01-01', '2026-06-30', 1000, 0, 1,
 '历史活动已结束，仅保留用于过期券与核销率统计演示', 'ENDED', 1),
(6, 'CP-202612-WIN', '冬季暖充服务费6元减免券', 'SERVICE_FEE_FREE', 6.00, 0.00,
 NULL, '2026-12-01', '2027-02-28', 3000, 0, 2,
 '冬季投放草稿，待运营审核后上线，单笔订单限用一张', 'DRAFT', 1)
AS nv
ON DUPLICATE KEY UPDATE
    campaign_code         = nv.campaign_code,
    campaign_name         = nv.campaign_name,
    coupon_kind           = nv.coupon_kind,
    face_value            = nv.face_value,
    threshold_amount      = nv.threshold_amount,
    applicable_station_id = nv.applicable_station_id,
    valid_from            = nv.valid_from,
    valid_to              = nv.valid_to,
    issue_total           = nv.issue_total,
    issued_count          = nv.issued_count,
    per_user_limit        = nv.per_user_limit,
    redeem_rule           = nv.redeem_rule,
    status                = nv.status,
    created_by            = nv.created_by;

-- 5. 优惠券发放记录表（24 条：REDEEMED 10 / ISSUED 6 / EXPIRED 4 / VOID 4）
--    券 1~10 为已核销券（持券人 9006~9015，与核销表一一对应，且订单均为 PAID）；
--    券 11~16 为待使用；17~20 为过期券（活动 3/5 已过期或结束）；21~24 为作废券。
SET @skip_coupon_guard = 1;

INSERT INTO coupon_record (
    coupon_id, coupon_no, campaign_id, user_id, face_value, threshold_amount,
    issue_channel, issued_by, issued_at, expire_at, status
) VALUES
(1,  'CP2026110001', 1, 9006, 20.00, 0.00, 'NEW_USER_PACK', NULL, '2026-01-15 09:16:00', '2027-06-30', 'REDEEMED'),
(2,  'CP2026110002', 1, 9007, 20.00, 0.00, 'NEW_USER_PACK', NULL, '2026-01-28 21:06:00', '2027-06-30', 'REDEEMED'),
(3,  'CP2026110003', 1, 9008, 20.00, 0.00, 'NEW_USER_PACK', NULL, '2026-02-11 10:46:00', '2027-06-30', 'REDEEMED'),
(4,  'CP2026110004', 1, 9009, 20.00, 0.00, 'NEW_USER_PACK', NULL, '2026-02-26 18:31:00', '2027-06-30', 'REDEEMED'),
(5,  'CP2026110005', 1, 9010, 20.00, 0.00, 'NEW_USER_PACK', NULL, '2026-03-08 09:01:00', '2027-06-30', 'REDEEMED'),
(6,  'CP2026110006', 4, 9011, 8.00,  0.00, 'ACTIVITY',      1,    '2026-11-11 00:05:00', '2026-11-30', 'REDEEMED'),
(7,  'CP2026110007', 4, 9012, 8.00,  0.00, 'ACTIVITY',      1,    '2026-11-11 00:06:00', '2026-11-30', 'REDEEMED'),
(8,  'CP2026110008', 4, 9013, 8.00,  0.00, 'ACTIVITY',      1,    '2026-11-11 00:07:00', '2026-11-30', 'REDEEMED'),
(9,  'CP2026110009', 4, 9014, 8.00,  0.00, 'ACTIVITY',      1,    '2026-11-11 09:30:00', '2026-11-30', 'REDEEMED'),
(10, 'CP2026110010', 4, 9015, 8.00,  0.00, 'ACTIVITY',      1,    '2026-11-11 09:31:00', '2026-11-30', 'REDEEMED'),
(11, 'CP2026110011', 1, 9016, 20.00, 0.00, 'NEW_USER_PACK', NULL, '2026-11-12 08:40:00', '2027-06-30', 'ISSUED'),
(12, 'CP2026110012', 1, 9017, 20.00, 0.00, 'NEW_USER_PACK', NULL, '2026-11-12 08:41:00', '2027-06-30', 'ISSUED'),
(13, 'CP2026110013', 4, 9001, 8.00,  0.00, 'ACTIVITY',      1,    '2026-11-11 09:40:00', '2026-11-30', 'ISSUED'),
(14, 'CP2026110014', 4, 9002, 8.00,  0.00, 'ACTIVITY',      1,    '2026-11-11 09:41:00', '2026-11-30', 'ISSUED'),
(15, 'CP2026110015', 2, 9018, 10.00, 0.00, 'ACTIVITY',      1,    '2026-11-08 14:20:00', '2027-10-31', 'ISSUED'),
(16, 'CP2026110016', 2, 9019, 10.00, 0.00, 'ACTIVITY',      1,    '2026-11-08 14:21:00', '2027-10-31', 'ISSUED'),
(17, 'CP2026110017', 5, 9020, 15.00, 30.00, 'OPS_GRANT',    1,    '2026-01-10 10:00:00', '2026-06-30', 'EXPIRED'),
(18, 'CP2026110018', 5, 9001, 15.00, 30.00, 'OPS_GRANT',    1,    '2026-01-10 10:02:00', '2026-06-30', 'EXPIRED'),
(19, 'CP2026110019', 3, 9003, 5.00,  0.00, 'OPS_GRANT',    1,    '2026-03-15 11:00:00', '2026-09-30', 'EXPIRED'),
(20, 'CP2026110020', 3, 9004, 5.00,  0.00, 'OPS_GRANT',    1,    '2026-03-15 11:01:00', '2026-09-30', 'EXPIRED'),
(21, 'CP2026110021', 1, 9020, 20.00, 0.00, 'COMPENSATION', 1,    '2026-11-10 16:30:00', '2027-06-30', 'VOID'),
(22, 'CP2026110022', 2, 9005, 10.00, 0.00, 'OPS_GRANT',    2,    '2026-11-09 15:10:00', '2027-10-31', 'VOID'),
(23, 'CP2026110023', 1, 9002, 20.00, 0.00, 'NEW_USER_PACK', NULL, '2026-11-13 09:00:00', '2027-06-30', 'VOID'),
(24, 'CP2026110024', 4, 9003, 8.00,  0.00, 'ACTIVITY',      1,    '2026-11-11 12:40:00', '2026-11-30', 'VOID')
AS nv
ON DUPLICATE KEY UPDATE
    coupon_no        = nv.coupon_no,
    campaign_id      = nv.campaign_id,
    user_id          = nv.user_id,
    face_value       = nv.face_value,
    threshold_amount = nv.threshold_amount,
    issue_channel    = nv.issue_channel,
    issued_by        = nv.issued_by,
    issued_at        = nv.issued_at,
    expire_at        = nv.expire_at,
    status           = nv.status;

-- 5.1 回填活动已发放张数：跳过发券触发器后 issued_count 不会自动累加，
--     故在券发放完成后按 coupon_record 实际张数回填，并用 LEAST 兜底，
--     确保 issued_count <= issue_total（chk_campaign_quota）。
UPDATE coupon_campaign AS cp
   SET issued_count = LEAST(
           (SELECT COUNT(*) FROM coupon_record AS c WHERE c.campaign_id = cp.campaign_id),
           cp.issue_total
       );

-- 6. 优惠券核销记录表（10 条：券 1~10 各核销一次，一券一销、一单一券）
--    不写死 order_id：从 charge_order 取 status='PAID' 的真实订单，
--    按 user_id 与持券人对齐（每个车主取最早一张已支付订单），
--    station_id 取订单场站、order_amount 取订单 total_amount、
--    抵扣额取 LEAST(券面额, 订单金额)，天然满足 chk_redeem_face（>0 且 <= order_amount）。
INSERT INTO coupon_redeem_record (
    coupon_id, order_id, user_id, station_id, face_value, order_amount, redeemed_at
)
SELECT c.coupon_id,
       o.order_id,
       c.user_id,
       o.station_id,
       LEAST(c.face_value, o.total_amount),
       o.total_amount,
       TIMESTAMP('2026-11-16 10:30:00')
FROM (
    SELECT coupon_id, user_id, face_value,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY coupon_id) AS user_rn
    FROM coupon_record
    WHERE coupon_id BETWEEN 1 AND 10
      AND status = 'REDEEMED'
) AS c
INNER JOIN (
    SELECT order_id, user_id, station_id, total_amount,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY order_id) AS user_rn
    FROM charge_order
    WHERE status = 'PAID'
      AND total_amount > 0.00
) AS o
    ON o.user_id = c.user_id
   AND o.user_rn = 1
WHERE c.user_rn = 1
ON DUPLICATE KEY UPDATE
    face_value   = VALUES(face_value),
    order_amount = VALUES(order_amount),
    redeemed_at  = VALUES(redeemed_at);

SET @skip_coupon_guard = NULL;

-- 自检：本段各表条数（应为 5 / 32 / 25 / 6 / 24 / 10）
SELECT 'fleet_account' AS tbl, COUNT(*) AS cnt FROM fleet_account
UNION ALL SELECT 'user_account', COUNT(*) FROM user_account
UNION ALL SELECT 'vehicle', COUNT(*) FROM vehicle
UNION ALL SELECT 'coupon_campaign', COUNT(*) FROM coupon_campaign
UNION ALL SELECT 'coupon_record', COUNT(*) FROM coupon_record
UNION ALL SELECT 'coupon_redeem_record', COUNT(*) FROM coupon_redeem_record;

-- ============================================================================
-- 业务线 4 结束
-- ============================================================================
