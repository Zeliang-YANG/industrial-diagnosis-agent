# config.py

# 仿真服务器地址
OPC_URL = "opc.tcp://localhost:53530/OPCUA/SimulationServer"

# 1. 节点 NodeId 配置
NODE_CONFIG = {
    # CNC
    "ControlMode":   "ns=3;i=1014",
    "AlarmCode":     "ns=3;i=1015",
    "MachineStatus": "ns=3;i=1011",
    "FeedOverride":  "ns=3;i=1016",
    "PartCount":    "ns=3;i=1012",
    "BadPartCount": "ns=3;i=1017",
    "SpindleSpeed": "ns=3;i=1009",
    "SpindleLoad":  "ns=3;i=1010",
    "Temperature":  "ns=3;i=1013",
    # Robot
    "ErrorID": "ns=3;i=1022",
    "GripperStatus": "ns=3;i=1021",
    "MotionState": "ns=3;i=1020",
    "RobotMode": "ns=3;i=1019",
    # PLC
    "CPU_Switch": "ns=3;i=1024",
    "Socket_Connect": "ns=3;i=1026",
    "SystemFault": "ns=3;i=1025",
}

# 三条产线的 OPC 节点映射（用于后端采集 1/2/3 线）
LINE_NODE_CONFIG = {
    1: {
        "cnc": {
            "ControlMode": "ns=3;i=1014",
            "AlarmCode": "ns=3;i=1015",
            "MachineStatus": "ns=3;i=1011",
            "FeedOverride": "ns=3;i=1016",
            "PartCount": "ns=3;i=1012",
            "BadPartCount": "ns=3;i=1017",
            "SpindleSpeed": "ns=3;i=1009",
            "SpindleLoad": "ns=3;i=1010",
            "Temperature": "ns=3;i=1013",
        },
        "robot": {
            "ErrorID": "ns=3;i=1022",
            "GripperStatus": "ns=3;i=1021",
            "MotionState": "ns=3;i=1020",
            "RobotMode": "ns=3;i=1019",
        },
        "plc": {
            "CPU_Switch": "ns=3;i=1024",
            "Socket_Connect": "ns=3;i=1026",
            "SystemFault": "ns=3;i=1025",
        },
    },
    2: {
        "cnc": {
            "AlarmCode": "ns=3;i=1028",
            "FeedOverride": "ns=3;i=1029",
            "BadPartCount": "ns=3;i=1030",
            "ControlMode": "ns=3;i=1031",
            "Temperature": "ns=3;i=1032",
            "SpindleSpeed": "ns=3;i=1033",
            "SpindleLoad": "ns=3;i=1034",
            "PartCount": "ns=3;i=1035",
            "MachineStatus": "ns=3;i=1036",
        },
        "robot": {
            "MotionState": "ns=3;i=1048",
            "ErrorID": "ns=3;i=1049",
            "GripperStatus": "ns=3;i=1050",
            "RobotMode": "ns=3;i=1051",
        },
        "plc": {
            "Socket_Connect": "ns=3;i=1058",
            "SystemFault": "ns=3;i=1059",
            "CPU_Switch": "ns=3;i=1060",
        },
    },
    3: {
        "cnc": {
            "AlarmCode": "ns=3;i=1038",
            "FeedOverride": "ns=3;i=1039",
            "BadPartCount": "ns=3;i=1040",
            "ControlMode": "ns=3;i=1041",
            "Temperature": "ns=3;i=1042",
            "SpindleSpeed": "ns=3;i=1043",
            "SpindleLoad": "ns=3;i=1044",
            "PartCount": "ns=3;i=1045",
            "MachineStatus": "ns=3;i=1046",
        },
        "robot": {
            "MotionState": "ns=3;i=1053",
            "ErrorID": "ns=3;i=1054",
            "GripperStatus": "ns=3;i=1055",
            "RobotMode": "ns=3;i=1056",
        },
        "plc": {
            "Socket_Connect": "ns=3;i=1062",
            "SystemFault": "ns=3;i=1063",
            "CPU_Switch": "ns=3;i=1064",
        },
    },
}

# 2. 状态映射描述
STATE_MAP = {
    "srun": "加工运行 (创造价值)",
    "ssby": "待机空转 (性能损失)",
    "sp_down": "计划停机 (换刀/调试)",
    "su_down": "故障停机 (需要维修)",
    "soff": "关机/离线"
}

# Robot/PLC 状态映射
ROBOT_STATE_MAP = {
    "swork": "作业运行",
    "swait": "空闲等待",
    "steach": "示教/调试",
    "sgrip": "抓取/放置",
    "salarm": "异常报警",
    "soff": "离线/中断",
}

PLC_STATE_MAP = {
    "srun": "运行",
    "sstop": "停止",
    "serror": "故障",
    "scomm_err": "通信中断",
}

# 3. 关机/离线判定（与 OPC 仿真约定一致：可按现场点位调整）
CONTROL_MODE_OFF = 0
MACHINE_STATUS_OFFLINE = 2

# 3.1 Robot/PLC 底层信号语义（用于状态解析，若“状态码反了”优先改这里）
ROBOT_MODE_AUTO = 1
ROBOT_MODE_TEACH = 2
ROBOT_MOTION_WORK_VALUE = True      # MotionState 等于该值时判定为 swork
ROBOT_GRIP_ACTIVE_VALUE = True      # GripperStatus 等于该值时判定为 sgrip

PLC_CPU_RUN_VALUE = 1               # CPU_Switch 等于该值时判定可运行
PLC_SOCKET_COMM_ERR_VALUE = 1       # Socket_Connect 等于该值时判定通信中断

# 4. 设备标识
EQUIP_ID_CNC = "BJ-CNC-001"
EQUIP_ID_ROBOT = "KUKA-R2000-001"
EQUIP_ID_PLC = "S7-PLC-001"

# 三条产线设备清单
LINE_LAYOUT = {
    1: {
        "cnc": EQUIP_ID_CNC,
        "robot": EQUIP_ID_ROBOT,
        "plc": EQUIP_ID_PLC,
    },
    2: {
        "cnc": "Baoji_CNC_01_02",
        "robot": "KUKA_Robot_R2000_02",
        "plc": "Siemens_PLC_S7_02",
    },
    3: {
        "cnc": "Baoji_CNC_01_03",
        "robot": "KUKA_Robot_R2000_03",
        "plc": "Siemens_PLC_S7_03",
    },
}

CNC_EQUIP_IDS = [LINE_LAYOUT[idx]["cnc"] for idx in (1, 2, 3)]
ROBOT_EQUIP_IDS = [LINE_LAYOUT[idx]["robot"] for idx in (1, 2, 3)]
PLC_EQUIP_IDS = [LINE_LAYOUT[idx]["plc"] for idx in (1, 2, 3)]
ALL_EQUIP_IDS = CNC_EQUIP_IDS + ROBOT_EQUIP_IDS + PLC_EQUIP_IDS

# 5. KPI：当前先以 CNC 为统计对象
KPI_EQUIP_ID = EQUIP_ID_CNC
LINE_KPI_EQUIP_ID = "LINE-001"
# 产线汇总统一使用 kpi_service.py 的 0.6/0.3/0.1 加权设备可用率。
OEE_APPLICABLE_EQUIP_IDS = set(CNC_EQUIP_IDS + [LINE_KPI_EQUIP_ID])

# 6. 单件理想节拍（秒），与仿真5s 加工 + 2s 上下料一致
IDEAL_CYCLE_SEC = 7

# 6.1 阶段A：车间仿真运行参数（秒/概率）
SIM_CONFIG = {
    # 基础节拍
    "loading_sec": 2,
    "processing_sec": 5,
    "unloading_sec": 1,
    # 性能损失细分场景（Tloss）
    "wait_material_prob_per_tick": 0.02,
    "wait_material_sec": 6,
    "micro_stop_prob_per_tick": 0.01,
    "micro_stop_sec": 3,
    # 计划停机（示教/调试）
    "planned_downtime_every_cycles": 12,
    "planned_downtime_sec": 20,
    # 非计划事件
    "repair_sec": 6,
    "cnc_fault_prob_per_tick": 0.01,
    "robot_fault_prob_per_tick": 0.003,
    "plc_fault_prob_per_tick": 0.002,
    "comm_loss_prob_per_tick": 0.0015,
    "comm_loss_sec": 8,
    # 质量损失
    "bad_part_prob_per_cycle": 0.03,
    # 加工段主轴设定
    "processing_spindle_speed": 1200,
}

# 7. 时间要素桶（用于将设备状态映射到 KPI 时间要素）
TIME_BUCKET_VAL = "t_val"
TIME_BUCKET_LOSS = "t_loss"
TIME_BUCKET_DOWN = "t_down"
TIME_BUCKET_PLAN = "t_plan_down"
TIME_BUCKET_NON_SCH = "t_non_sch"

# 每类设备状态 -> 时间要素 的映射关系（对齐第二章时间要素模型）
_CNC_TIME_MAP = {
    "srun": TIME_BUCKET_VAL,
    "ssby": TIME_BUCKET_LOSS,
    "su_down": TIME_BUCKET_DOWN,
    "sp_down": TIME_BUCKET_PLAN,
    "soff": TIME_BUCKET_NON_SCH,
}
_ROBOT_TIME_MAP = {
    "swork": TIME_BUCKET_VAL,
    "sgrip": TIME_BUCKET_VAL,
    "swait": TIME_BUCKET_LOSS,
    "salarm": TIME_BUCKET_DOWN,
    "steach": TIME_BUCKET_PLAN,
    "soff": TIME_BUCKET_NON_SCH,
}
_PLC_TIME_MAP = {
    "srun": TIME_BUCKET_VAL,
    "sstop": TIME_BUCKET_PLAN,
    "serror": TIME_BUCKET_DOWN,
    "scomm_err": TIME_BUCKET_NON_SCH,
}

STATE_TO_TIME_BUCKET = {}
for eid in CNC_EQUIP_IDS:
    STATE_TO_TIME_BUCKET[eid] = dict(_CNC_TIME_MAP)
for eid in ROBOT_EQUIP_IDS:
    STATE_TO_TIME_BUCKET[eid] = dict(_ROBOT_TIME_MAP)
for eid in PLC_EQUIP_IDS:
    STATE_TO_TIME_BUCKET[eid] = dict(_PLC_TIME_MAP)

# 用于 MTBF/MTTR 的“故障状态”集合
FAULT_STATES = {}
for eid in CNC_EQUIP_IDS:
    FAULT_STATES[eid] = {"su_down"}
for eid in ROBOT_EQUIP_IDS:
    FAULT_STATES[eid] = {"salarm"}
for eid in PLC_EQUIP_IDS:
    FAULT_STATES[eid] = {"serror"}
