# cnc_logic.py
import asyncio
from datetime import datetime
from asyncua import Client, ua
from config import (
    NODE_CONFIG,
    LINE_NODE_CONFIG,
    LINE_LAYOUT,
    OPC_URL,
    STATE_MAP,
    ROBOT_STATE_MAP,
    PLC_STATE_MAP,
)
from db_models import SessionLocal, RawTelemetry, StatusEventLog
from cnc_model import CNCModel
from cnc_simulator import CNCSimulator


class DigitalTwinCNC:
    def __init__(self):
        self.url = OPC_URL
        self.client = None
        self.nodes = {}
        self.db_session = SessionLocal()

        self.current_states = {}
        self.current_events = {}
        self.equip_to_line = {}
        for idx, line in LINE_LAYOUT.items():
            for equip_id in line.values():
                self.equip_to_line[equip_id] = idx

        self.model = None
        self.simulator = None
        self.simulators = {}
        self._shutdown_done = False

    @staticmethod
    def _line_key(line_index, signal_name):
        return f"L{line_index}_{signal_name}"

    async def connect(self):
        print(f"正在连接仿真服务器: {self.url}")
        self.client = Client(url=self.url)
        try:
            await self.client.connect()
            # 保留一号线原始键名（供内置仿真器写入）
            for name, node_id in NODE_CONFIG.items():
                self.nodes[name] = self.client.get_node(node_id)
            # 三条产线全量节点（供多线采集）
            for line_index, role_map in LINE_NODE_CONFIG.items():
                for signal_map in role_map.values():
                    for signal_name, node_id in signal_map.items():
                        key = self._line_key(line_index, signal_name)
                        self.nodes[key] = self.client.get_node(node_id)

            self.model = CNCModel(self.nodes)
            self.simulators = {
                idx: CNCSimulator(self._build_line_writer(idx))
                for idx in LINE_LAYOUT.keys()
            }
            self.simulator = self.simulators.get(1)
            print("系统模块初始化完成。")
        except Exception as e:
            print(f"连接失败: {e}")
            raise

    async def write_node(self, name, value, variant_type=ua.VariantType.Int32):
        try:
            node = self.nodes[name]
            await node.write_value(ua.DataValue(ua.Variant(value, variant_type)))
        except Exception as e:
            print(f"写入节点 {name} 失败: {e}")

    def _build_line_writer(self, line_index):
        async def _writer(name, value, variant_type=ua.VariantType.Int32):
            key = name if line_index == 1 else self._line_key(line_index, name)
            node = self.nodes.get(key)
            if node is None and line_index == 1:
                node = self.nodes.get(self._line_key(1, name))
            if node is None:
                return
            try:
                await node.write_value(ua.DataValue(ua.Variant(value, variant_type)))
            except Exception as e:
                print(f"写入节点 {key} 失败: {e}")

        return _writer

    def _line_data(self, raw_data, line_index):
        # 统一产线数据结构，后续状态解析与落库都用该口径。
        signals = (
            "ControlMode",
            "AlarmCode",
            "MachineStatus",
            "FeedOverride",
            "PartCount",
            "BadPartCount",
            "SpindleSpeed",
            "SpindleLoad",
            "Temperature",
            "ErrorID",
            "GripperStatus",
            "MotionState",
            "RobotMode",
            "CPU_Switch",
            "Socket_Connect",
            "SystemFault",
        )
        out = {}
        for sig in signals:
            out[sig] = raw_data.get(self._line_key(line_index, sig))
            if out[sig] is None and line_index == 1:
                out[sig] = raw_data.get(sig)
        return out

    def _state_map_for_equip(self, equip_id):
        if equip_id in {line["cnc"] for line in LINE_LAYOUT.values()}:
            return STATE_MAP
        if equip_id in {line["robot"] for line in LINE_LAYOUT.values()}:
            return ROBOT_STATE_MAP
        if equip_id in {line["plc"] for line in LINE_LAYOUT.values()}:
            return PLC_STATE_MAP
        return {}

    def _alarm_code_for_transition(self, equip_id, state, line_data):
        line_index = self.equip_to_line.get(equip_id, 1)
        line = LINE_LAYOUT.get(line_index, {})
        if equip_id == line.get("cnc") and state == "su_down":
            return line_data.get("AlarmCode") or 0
        if equip_id == line.get("robot") and state == "salarm":
            return line_data.get("ErrorID") or 0
        if equip_id == line.get("plc") and state == "serror":
            # PLC 无独立错误码节点，使用可区分产线的系统故障编码。
            return 3000 + int(line_index)
        if equip_id == line.get("plc") and state == "scomm_err":
            # 通信中断单独编码，便于帕累托与报表区分。
            return 7000 + int(line_index)
        return 0

    def _resolve_all_states(self, line_data_map):
        states = {}
        for line_index, line_data in line_data_map.items():
            layout = LINE_LAYOUT[line_index]
            states[layout["cnc"]] = self.model.resolve_cnc_state(line_data)
            states[layout["robot"]] = self.model.resolve_robot_state(line_data)
            states[layout["plc"]] = self.model.resolve_plc_state(line_data)
        return states

    def save_to_db(self, line_data_map, states, now):
        try:
            # 每条产线 CNC 都落一条遥测（用于各线产量/质量/OEE）
            for line_index, line_data in line_data_map.items():
                cnc_id = LINE_LAYOUT[line_index]["cnc"]
                tele = RawTelemetry(
                    timestamp=now,
                    equip_id=cnc_id,
                    spindle_speed=line_data.get("SpindleSpeed"),
                    spindle_load=line_data.get("SpindleLoad"),
                    temperature=line_data.get("Temperature"),
                    part_count=line_data.get("PartCount"),
                    bad_count=line_data.get("BadPartCount"),
                    machine_state=states.get(cnc_id, "ssby"),
                )
                self.db_session.add(tele)

            for equip_id, state in states.items():
                prev_state = self.current_states.get(equip_id)
                if state == prev_state:
                    continue

                line_index = self.equip_to_line.get(equip_id, 1)
                line_data = line_data_map.get(line_index, {})
                prev_event = self.current_events.get(equip_id)
                if prev_event:
                    prev_event.end_time = now
                    prev_event.duration_sec = int(
                        (now - prev_event.start_time).total_seconds()
                    )
                    self.db_session.add(prev_event)

                new_event = StatusEventLog(
                    equip_id=equip_id,
                    state_code=state,
                    start_time=now,
                    alarm_code=self._alarm_code_for_transition(equip_id, state, line_data),
                )
                self.db_session.add(new_event)
                self.current_events[equip_id] = new_event
                self.current_states[equip_id] = state

                state_map = self._state_map_for_equip(equip_id)
                print(
                    f"[状态跃迁][{equip_id}] -> {state_map.get(state, state)}"
                )

            self.db_session.commit()
        except Exception as e:
            print(f"数据库写入错误: {e}")
            self.db_session.rollback()
            # 状态缓存已在事务中改变，写入失败时停止采集，不能继续产生不完整事件。
            raise

    def finalize_current_events(self, now):
        for event in self.current_events.values():
            if event is None or event.end_time is not None:
                continue
            event.end_time = now
            event.duration_sec = int((now - event.start_time).total_seconds())

    async def shutdown(self):
        if self._shutdown_done:
            return
        self._shutdown_done = True
        now = datetime.now()
        try:
            self.finalize_current_events(now)
            self.db_session.commit()
        except Exception as e:
            print(f"退出落库错误: {e}")
            self.db_session.rollback()
        if self.client:
            try:
                await self.client.disconnect()
            except Exception as e:
                print(f"OPC 断开异常: {e}")
            self.client = None
        if self.simulators:
            fragments = []
            for idx in sorted(self.simulators.keys()):
                stats = self.simulators[idx].get_loss_reason_stats()
                fragments.append(
                    f"L{idx}:wait_material={stats.get('wait_material', 0)}s,"
                    f"wait_robot={stats.get('wait_robot', 0)}s,"
                    f"micro_stop={stats.get('micro_stop', 0)}s"
                )
            print("[损失原因汇总] " + " | ".join(fragments))
        self.db_session.close()

    async def start_monitoring(self):
        print("数字孪生监控系统启动成功...")
        print("-" * 50)
        while True:
            raw_data = await self.model.get_all_data()
            line_data_map = {
                idx: self._line_data(raw_data, idx) for idx in LINE_LAYOUT.keys()
            }
            states = self._resolve_all_states(line_data_map)
            self.save_to_db(line_data_map, states, datetime.now())

            active_losses = []
            for idx in sorted(self.simulators.keys()):
                reason = self.simulators[idx].get_current_loss_reason()
                if reason:
                    active_losses.append(f"L{idx}:{reason}")
            loss_text = f" | LossReason:{','.join(active_losses)}" if active_losses else ""
            line_fragments = []
            for idx in sorted(LINE_LAYOUT.keys()):
                line = LINE_LAYOUT[idx]
                line_data = line_data_map[idx]
                part_disp = line_data.get("PartCount")
                part_disp = part_disp if part_disp is not None else "-"
                cnc_state = states.get(line["cnc"], "ssby")
                robot_state = states.get(line["robot"], "swait")
                plc_state = states.get(line["plc"], "srun")
                line_fragments.append(
                    f"L{idx} 产量:{str(part_disp):<3} "
                    f"CNC:{STATE_MAP.get(cnc_state, cnc_state)} "
                    f"Robot:{ROBOT_STATE_MAP.get(robot_state, robot_state)} "
                    f"PLC:{PLC_STATE_MAP.get(plc_state, plc_state)}"
                )
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                + " || ".join(line_fragments)
                + f"{loss_text}"
            )

            for idx in sorted(self.simulators.keys()):
                await self.simulators[idx].simulate_production(line_data_map[idx])
            await asyncio.sleep(1)
