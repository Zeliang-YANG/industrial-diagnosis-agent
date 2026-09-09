"""原毕设的模拟故障字典；不是厂商认证手册或真实根因证据。"""
ALARM_CODE_CATALOG = {
    # CNC
    101: {
        "device_type": "cnc",
        "alarm_label": "主轴过载",
        "alarm_desc": "主轴负载超限，触发保护停机。",
        "severity": "high",
        "suggested_action": "检查切削参数与刀具磨损，确认主轴散热。",
    },
    107: {
        "device_type": "cnc",
        "alarm_label": "伺服跟随误差",
        "alarm_desc": "进给轴位置偏差超阈值。",
        "severity": "high",
        "suggested_action": "检查伺服驱动器、编码器反馈及负载冲击。",
    },
    121: {
        "device_type": "cnc",
        "alarm_label": "行程超限",
        "alarm_desc": "轴向运动接近或触发软/硬限位。",
        "severity": "medium",
        "suggested_action": "核对程序坐标与工件原点，检查限位开关。",
    },
    135: {
        "device_type": "cnc",
        "alarm_label": "润滑异常",
        "alarm_desc": "润滑压力或流量异常。",
        "severity": "medium",
        "suggested_action": "检查润滑泵、油路与油位，必要时维护更换。",
    },
    201: {
        "device_type": "cnc",
        "alarm_label": "冷却系统异常",
        "alarm_desc": "冷却回路压力/流量不足。",
        "severity": "medium",
        "suggested_action": "检查冷却泵、过滤器与冷却液液位。",
    },
    # Robot
    9001: {
        "device_type": "robot",
        "alarm_label": "碰撞检测",
        "alarm_desc": "机器人检测到外力碰撞或力矩异常。",
        "severity": "high",
        "suggested_action": "复位后检查工装干涉点，优化轨迹与速度。",
    },
    9005: {
        "device_type": "robot",
        "alarm_label": "伺服驱动故障",
        "alarm_desc": "关节伺服驱动异常或过流。",
        "severity": "high",
        "suggested_action": "检查伺服驱动器散热与电缆连接。",
    },
    9012: {
        "device_type": "robot",
        "alarm_label": "轨迹奇异点",
        "alarm_desc": "运动学路径接近奇异位形。",
        "severity": "medium",
        "suggested_action": "调整过渡点姿态，降低奇异区域通过速度。",
    },
    9020: {
        "device_type": "robot",
        "alarm_label": "急停回路触发",
        "alarm_desc": "外部急停链路中断或触发。",
        "severity": "high",
        "suggested_action": "检查安全回路、急停按钮与联锁信号。",
    },
    # PLC / 通信
    3001: {
        "device_type": "plc",
        "alarm_label": "PLC系统故障(L1)",
        "alarm_desc": "产线1 PLC 系统故障位触发。",
        "severity": "high",
        "suggested_action": "检查PLC模块状态与I/O总线诊断信息。",
    },
    3002: {
        "device_type": "plc",
        "alarm_label": "PLC系统故障(L2)",
        "alarm_desc": "产线2 PLC 系统故障位触发。",
        "severity": "high",
        "suggested_action": "检查PLC模块状态与I/O总线诊断信息。",
    },
    3003: {
        "device_type": "plc",
        "alarm_label": "PLC系统故障(L3)",
        "alarm_desc": "产线3 PLC 系统故障位触发。",
        "severity": "high",
        "suggested_action": "检查PLC模块状态与I/O总线诊断信息。",
    },
    7001: {
        "device_type": "plc",
        "alarm_label": "通信中断(L1)",
        "alarm_desc": "产线1 采集链路超时。",
        "severity": "medium",
        "suggested_action": "检查交换机端口、网线链路与OPC连接状态。",
    },
    7002: {
        "device_type": "plc",
        "alarm_label": "通信中断(L2)",
        "alarm_desc": "产线2 采集链路超时。",
        "severity": "medium",
        "suggested_action": "检查交换机端口、网线链路与OPC连接状态。",
    },
    7003: {
        "device_type": "plc",
        "alarm_label": "通信中断(L3)",
        "alarm_desc": "产线3 采集链路超时。",
        "severity": "medium",
        "suggested_action": "检查交换机端口、网线链路与OPC连接状态。",
    },
}


def _resolve_alarm_meta(alarm_code):
    code = int(alarm_code or 0)
    base = {
        "alarm_code": code,
        "alarm_label": "无报警",
        "alarm_desc": "",
        "device_type": "unknown",
        "severity": "none",
        "suggested_action": "",
    }
    if code <= 0:
        return base
    if code in ALARM_CODE_CATALOG:
        return {**base, **ALARM_CODE_CATALOG[code]}
    if 100 <= code < 300:
        return {
            **base,
            "device_type": "cnc",
            "alarm_label": f"CNC故障({code})",
            "alarm_desc": "CNC 通用故障码，需结合控制器日志解析。",
            "severity": "medium",
            "suggested_action": "查看数控系统报警历史与维保记录。",
        }
    if 9000 <= code < 9100:
        return {
            **base,
            "device_type": "robot",
            "alarm_label": f"机器人故障({code})",
            "alarm_desc": "机器人通用报警码，需结合控制柜日志。",
            "severity": "medium",
            "suggested_action": "复位后读取机器人控制器详细报警信息。",
        }
    return {
        **base,
        "alarm_label": f"未知报警({code})",
        "alarm_desc": "未收录的报警码。",
        "severity": "medium",
        "suggested_action": "补充报警码字典并核对来源设备。",
    }
