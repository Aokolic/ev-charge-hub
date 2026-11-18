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
-- 前置：线1 的 station / charging_gun、线4 的 user_account / vehicle 已有数据
--       本脚本用 station_id 1~4、gun_id 1001~1020、user_id 9001~9020、vehicle_id 5001~5020

-- 1) 电价规则：4 个站 × 5 个时段 = 20 条
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
    DECLARE cur CURSOR FOR SELECT order_id FROM charge_order WHERE end_time IS NOT NULL ORDER BY order_id;
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
-- 【业务线 3 ~ 业务线 4】测试数据占位区（各负责成员追加，保证每表 >= 20 条）
-- ============================================================================
