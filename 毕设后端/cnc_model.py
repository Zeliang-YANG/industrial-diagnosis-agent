# cnc_model.py
from config import (
    CONTROL_MODE_OFF,
    MACHINE_STATUS_OFFLINE,
    EQUIP_ID_CNC,
    EQUIP_ID_ROBOT,
    EQUIP_ID_PLC,
    ROBOT_MODE_AUTO,
    ROBOT_MODE_TEACH,
    ROBOT_MOTION_WORK_VALUE,
    ROBOT_GRIP_ACTIVE_VALUE,
    PLC_CPU_RUN_VALUE,
    PLC_SOCKET_COMM_ERR_VALUE,
)

class CNCModel:
    def __init__(self, nodes):
        self.nodes = nodes

    async def get_all_data(self):
        """批量读取底层 OPC UA 数据"""
        data = {}
        for name, node in self.nodes.items():
            try:
                data[name] = await node.read_value()
            except Exception as e:
                print(f"读取节点 {name} 失败: {e}")
                data[name] = None
        return data

    def resolve_cnc_state(self, data):
        """
        论文第2章核心：状态映射算法
        将底层数据映射为标准状态空间
        """
        alarm = data.get('AlarmCode') or 0
        control = data.get('ControlMode')
        machine = data.get('MachineStatus')
        spindle = data.get('SpindleSpeed') or 0

        # 1. 故障判定
        if alarm > 0:
            return "su_down"

        # 2. 关机/离线（无控制模式或显式离线状态）
        if control == CONTROL_MODE_OFF or machine == MACHINE_STATUS_OFFLINE:
            return "soff"

        # 3. 模式判定 (2为手动/调试)
        if control == 2:
            return "sp_down"

        # 4. 加工运行判定 (自动模式 + 启动信号 + 主轴转速)
        if control == 1 and machine == 1 and spindle > 100:
            return "srun"

        # 5. 待机判定
        return "ssby"

    def resolve_robot_state(self, data):
        """
        机器人状态优先级（高->低）：
        soff -> salarm -> steach -> sgrip -> swork -> swait
        """
        socket_connect = data.get("Socket_Connect")
        error_id = data.get("ErrorID") or 0
        robot_mode = data.get("RobotMode")
        motion_state = data.get("MotionState")
        gripper_status = data.get("GripperStatus")

        # 通信中断时，将机器人归入离线状态（与论文时间要素模型一致）
        if socket_connect == PLC_SOCKET_COMM_ERR_VALUE or robot_mode is None:
            return "soff"
        if error_id != 0:
            return "salarm"
        if robot_mode == ROBOT_MODE_TEACH:
            return "steach"
        if robot_mode == ROBOT_MODE_AUTO and gripper_status == ROBOT_GRIP_ACTIVE_VALUE:
            return "sgrip"
        if robot_mode == ROBOT_MODE_AUTO and motion_state == ROBOT_MOTION_WORK_VALUE:
            return "swork"
        return "swait"

    def resolve_plc_state(self, data):
        """
        PLC 状态优先级（高->低）：
        scomm_err -> serror -> sstop -> srun
        """
        socket_connect = data.get("Socket_Connect")
        system_fault = data.get("SystemFault")
        cpu_switch = data.get("CPU_Switch")

        if socket_connect == PLC_SOCKET_COMM_ERR_VALUE:
            return "scomm_err"
        if bool(system_fault):
            return "serror"
        if cpu_switch != PLC_CPU_RUN_VALUE:
            return "sstop"
        return "srun"

    def resolve_all_states(self, data):
        """统一输出三类设备的状态字典。"""
        return {
            EQUIP_ID_CNC: self.resolve_cnc_state(data),
            EQUIP_ID_ROBOT: self.resolve_robot_state(data),
            EQUIP_ID_PLC: self.resolve_plc_state(data),
        }

    # 兼容旧调用：默认返回 CNC 状态
    def resolve_state(self, data):
        return self.resolve_cnc_state(data)