# cnc_simulator.py
import math
import random
import time
from asyncua import ua
from config import SIM_CONFIG


class CNCSimulator:
    """
    阶段A：非阻塞车间状态机
    优先级（高 -> 低）：
    通信中断 -> 故障维修 -> 计划停机 -> 正常生产节拍
    """

    def __init__(self, write_func):
        self.write_node = write_func
        self.phase = "loading"
        self.phase_deadline = 0.0
        self.repair_deadline = 0.0
        self.planned_deadline = 0.0
        self.comm_deadline = 0.0
        self.material_wait_deadline = 0.0
        self.micro_stop_deadline = 0.0
        self.cycle_count = 0
        self.active_fault = None
        self.last_write = {}
        self.current_loss_reason = None
        self.loss_reason_seconds = {
            "wait_material": 0.0,
            "wait_robot": 0.0,
            "micro_stop": 0.0,
        }
        self.last_tick = time.monotonic()
        # 更丰富的仿真故障码库，便于帕累托图形成可分析的 TopN 分布。
        self._fault_code_pool = {
            "cnc": [101, 107, 121, 135, 201],      # 主轴/伺服/超程/润滑
            "robot": [9001, 9005, 9012, 9020],     # 碰撞/伺服/奇异点/急停
            "plc": [3001, 3005, 3010],             # I/O总线/看门狗/模块故障
        }

    async def _set_node(self, name, value, variant_type=ua.VariantType.Int32):
        # 减少重复写入，降低 OPC 压力
        if self.last_write.get(name) == value:
            return
        if name == "SpindleSpeed" and variant_type == ua.VariantType.Int32:
            variant_type = ua.VariantType.Double
        self.last_write[name] = value
        await self.write_node(name, value, variant_type)

    def _set_loss_reason(self, reason):
        self.current_loss_reason = reason

    def _tick_loss_reason(self, now):
        elapsed = max(0.0, now - self.last_tick)
        if self.current_loss_reason in self.loss_reason_seconds:
            self.loss_reason_seconds[self.current_loss_reason] += elapsed
        self.last_tick = now

    def get_current_loss_reason(self):
        return self.current_loss_reason

    def get_loss_reason_stats(self):
        return {k: round(v, 1) for k, v in self.loss_reason_seconds.items()}

    async def _enter_planned_downtime(self, now):
        self.planned_deadline = now + SIM_CONFIG["planned_downtime_sec"]
        self.phase = "loading"
        await self._set_node("ControlMode", 2)   # CNC -> sp_down
        await self._set_node("MachineStatus", 0)
        await self._set_node("SpindleSpeed", 0)
        await self._set_node("RobotMode", 2)     # Robot -> steach
        await self._set_node("CPU_Switch", 0)    # PLC -> sstop
        print("[计划停机] 进入换型/示教窗口。")

    async def _enter_fault(self, now, fault_src):
        self.active_fault = fault_src
        self.repair_deadline = now + SIM_CONFIG["repair_sec"]
        self.phase = "loading"
        await self._set_node("MachineStatus", 0)
        await self._set_node("SpindleSpeed", 0)
        fault_code = random.choice(self._fault_code_pool.get(fault_src, [1]))
        if fault_src == "cnc":
            await self._set_node("AlarmCode", fault_code)
        elif fault_src == "robot":
            await self._set_node("ErrorID", fault_code)
        else:
            await self._set_node("SystemFault", True, ua.VariantType.Boolean)
        print(f"[故障注入] {fault_src} 故障触发(code={fault_code})，进入维修。")

    async def _clear_fault(self):
        await self._set_node("AlarmCode", 0)
        await self._set_node("ErrorID", 0)
        await self._set_node("SystemFault", False, ua.VariantType.Boolean)
        self.active_fault = None
        print("[故障恢复] 维修完成。")

    async def _enter_comm_loss(self, now):
        self.comm_deadline = now + SIM_CONFIG["comm_loss_sec"]
        self.phase = "loading"
        await self._set_node("Socket_Connect", 1)  # PLC -> scomm_err
        await self._set_node("ControlMode", 0)     # CNC -> soff
        await self._set_node("MachineStatus", 2)
        await self._set_node("SpindleSpeed", 0)
        await self._set_node("RobotMode", 2)
        print("[通信中断] 模拟采集链路中断。")

    async def _recover_comm(self):
        await self._set_node("Socket_Connect", 0)
        await self._set_node("ControlMode", 1)
        await self._set_node("MachineStatus", 0)
        await self._set_node("RobotMode", 1)
        await self._set_node("CPU_Switch", 1)
        print("[通信恢复] 采集链路恢复。")

    async def _run_normal_cycle(self, now, data):
        # 基础“上料 -> 加工 -> 下料”状态机（非阻塞）
        if self.phase_deadline <= 0:
            self.phase_deadline = now + SIM_CONFIG["loading_sec"]
            self.phase = "loading"

        if self.phase == "loading":
            # 缺料等待：保持待机但不进入加工
            if self.material_wait_deadline > now:
                self._set_loss_reason("wait_material")
                await self._set_node("MachineStatus", 0)
                await self._set_node("SpindleSpeed", 0.0, ua.VariantType.Double)
                await self._set_node("MotionState", False, ua.VariantType.Boolean)
                await self._set_node("GripperStatus", False, ua.VariantType.Boolean)
                return
            if self.material_wait_deadline and now >= self.material_wait_deadline:
                self.material_wait_deadline = 0.0

            self._set_loss_reason("wait_robot")
            await self._set_node("ControlMode", 1)
            await self._set_node("MachineStatus", 0)
            await self._set_node("SpindleSpeed", 0.0, ua.VariantType.Double)
            await self._set_node("RobotMode", 1)
            await self._set_node("MotionState", True, ua.VariantType.Boolean)
            await self._set_node("GripperStatus", True, ua.VariantType.Boolean)
            await self._set_node("CPU_Switch", 1)

            if random.random() < SIM_CONFIG["wait_material_prob_per_tick"]:
                wait_sec = SIM_CONFIG["wait_material_sec"]
                self.material_wait_deadline = now + wait_sec
                self.phase_deadline += wait_sec
                print("[性能损失] 缺料等待触发。")
                return

            if now >= self.phase_deadline:
                self.phase = "processing"
                self.phase_deadline = now + SIM_CONFIG["processing_sec"]
                await self._set_node("MotionState", False, ua.VariantType.Boolean)
                await self._set_node("GripperStatus", False, ua.VariantType.Boolean)
                await self._set_node("MachineStatus", 1)
                await self._set_node(
                    "SpindleSpeed",
                    SIM_CONFIG["processing_spindle_speed"],
                    ua.VariantType.Double,
                )
                self._set_loss_reason(None)

        elif self.phase == "processing":
            if self.micro_stop_deadline > now:
                self._set_loss_reason("micro_stop")
                await self._set_node("MachineStatus", 0)
                await self._set_node("SpindleSpeed", 0.0, ua.VariantType.Double)
                return
            if self.micro_stop_deadline and now >= self.micro_stop_deadline:
                self.micro_stop_deadline = 0.0
                await self._set_node("MachineStatus", 1)
                await self._set_node(
                    "SpindleSpeed",
                    SIM_CONFIG["processing_spindle_speed"],
                    ua.VariantType.Double,
                )

            if random.random() < SIM_CONFIG["micro_stop_prob_per_tick"]:
                stop_sec = SIM_CONFIG["micro_stop_sec"]
                self.micro_stop_deadline = now + stop_sec
                self.phase_deadline += stop_sec
                self._set_loss_reason("micro_stop")
                print("[性能损失] 微停事件触发。")
                await self._set_node("MachineStatus", 0)
                await self._set_node("SpindleSpeed", 0.0, ua.VariantType.Double)
                return

            self._set_loss_reason(None)
            await self._set_node("ControlMode", 1)
            await self._set_node("MachineStatus", 1)
            dynamic_speed = 1200 + 50 * math.sin(time.time() / 5)
            await self._set_node(
                "SpindleSpeed",
                round(dynamic_speed, 2),
                ua.VariantType.Double,
            )
            await self._set_node("RobotMode", 1)
            if now >= self.phase_deadline:
                self.phase = "unloading"
                self.phase_deadline = now + SIM_CONFIG["unloading_sec"]
                await self._set_node("MachineStatus", 0)
                await self._set_node("SpindleSpeed", 0.0, ua.VariantType.Double)
                # 增量产出（含少量次品概率）
                part_count = data.get("PartCount") or 0
                bad_count = data.get("BadPartCount") or 0
                await self._set_node("PartCount", part_count + 1)
                if random.random() < SIM_CONFIG["bad_part_prob_per_cycle"]:
                    await self._set_node("BadPartCount", bad_count + 1)
                    print("[质量损失] 产生1件次品。")
                self.cycle_count += 1

        elif self.phase == "unloading":
            self._set_loss_reason("wait_robot")
            await self._set_node("RobotMode", 1)
            await self._set_node("MotionState", True, ua.VariantType.Boolean)
            await self._set_node("GripperStatus", True, ua.VariantType.Boolean)
            if now >= self.phase_deadline:
                self.phase = "loading"
                self.phase_deadline = now + SIM_CONFIG["loading_sec"]
                await self._set_node("MotionState", False, ua.VariantType.Boolean)
                await self._set_node("GripperStatus", False, ua.VariantType.Boolean)

    async def simulate_production(self, data):
        now = time.monotonic()
        self._tick_loss_reason(now)

        # 0) 非计划：通信中断
        if self.comm_deadline > now:
            self._set_loss_reason(None)
            return
        if self.comm_deadline and now >= self.comm_deadline:
            self.comm_deadline = 0.0
            await self._recover_comm()
        elif random.random() < SIM_CONFIG["comm_loss_prob_per_tick"]:
            await self._enter_comm_loss(now)
            self._set_loss_reason(None)
            return

        # 1) 非计划：故障停机
        if self.repair_deadline > now:
            self._set_loss_reason(None)
            return
        if self.repair_deadline and now >= self.repair_deadline:
            self.repair_deadline = 0.0
            await self._clear_fault()
        elif self.phase == "processing":
            if random.random() < SIM_CONFIG["cnc_fault_prob_per_tick"]:
                await self._enter_fault(now, "cnc")
                self._set_loss_reason(None)
                return
            if random.random() < SIM_CONFIG["robot_fault_prob_per_tick"]:
                await self._enter_fault(now, "robot")
                self._set_loss_reason(None)
                return
            if random.random() < SIM_CONFIG["plc_fault_prob_per_tick"]:
                await self._enter_fault(now, "plc")
                self._set_loss_reason(None)
                return

        # 2) 计划停机（周期触发）
        if self.planned_deadline > now:
            self._set_loss_reason(None)
            return
        if self.planned_deadline and now >= self.planned_deadline:
            self.planned_deadline = 0.0
            await self._set_node("ControlMode", 1)
            await self._set_node("RobotMode", 1)
            await self._set_node("CPU_Switch", 1)
            print("[计划停机] 结束，恢复生产。")
        elif (
            self.cycle_count > 0
            and self.cycle_count % SIM_CONFIG["planned_downtime_every_cycles"] == 0
            and self.phase == "loading"
        ):
            await self._enter_planned_downtime(now)
            # 防止同一循环重复触发
            self.cycle_count += 1
            self._set_loss_reason(None)
            return

        # 3) 正常生产
        await self._run_normal_cycle(now, data)