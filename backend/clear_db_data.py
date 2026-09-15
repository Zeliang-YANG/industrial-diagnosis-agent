#!/usr/bin/env python3
"""清空业务表数据，并将 OPC 仿真侧产量等写回初值（与清库配套）。"""
import argparse
import asyncio

from asyncua import Client, ua
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from config import NODE_CONFIG, LINE_NODE_CONFIG, OPC_URL
from db_models import engine, RawTelemetry, StatusEventLog, DailyKPIReport

TABLES = (
    RawTelemetry.__tablename__,
    StatusEventLog.__tablename__,
    DailyKPIReport.__tablename__,
)


def _truncate_mysql() -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for name in TABLES:
                conn.execute(text(f"TRUNCATE TABLE `{name}`"))
            conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    except OperationalError as e:
        orig = getattr(e, "orig", None)
        if orig is not None and getattr(orig, "args", None) and orig.args[0] == 1045:
            print(
                "连接 MySQL 失败：当前未提供密码（using password: NO）。\n"
                "可选做法：\n"
                "  1) 在项目根目录创建 .env，写入一行：MYSQL_PASSWORD=你的密码\n"
                "  2) 或本终端执行：export MYSQL_PASSWORD='你的密码'\n"
                "  3) 或：export DATABASE_URL='mysql+pymysql://root:密码@127.0.0.1:3306/industrial_agent_demo'"
            )
            raise SystemExit(1) from e
        raise


async def reset_opcua_process_state() -> None:
    """清库后同步重置 CNC/Robot/PLC 关键节点初值。"""
    print(f"正在连接 OPC：{OPC_URL}")
    client = Client(url=OPC_URL)
    try:
        await client.connect()
        nodes = {name: client.get_node(nid) for name, nid in NODE_CONFIG.items()}

        async def w(name: str, value, variant_type=ua.VariantType.Int32) -> None:
            try:
                n = nodes[name]
                await n.write_value(ua.DataValue(ua.Variant(value, variant_type)))
            except Exception as ex:
                print(f"写入节点 {name} 失败: {ex}")

        async def w_nodeid(node_id: str, value, variant_type=ua.VariantType.Int32) -> None:
            try:
                n = client.get_node(node_id)
                await n.write_value(ua.DataValue(ua.Variant(value, variant_type)))
            except Exception as ex:
                print(f"写入节点 {node_id} 失败: {ex}")

        print("正在将 OPC 关键节点重置为初值…")
        # CNC
        await w("PartCount", 0)
        await w("BadPartCount", 0)
        await w("AlarmCode", 0)
        await w("MachineStatus", 0)
        await w("ControlMode", 1)
        await w("SpindleSpeed", 0.0, ua.VariantType.Double)
        # Robot
        await w("ErrorID", 0)
        await w("GripperStatus", False, ua.VariantType.Boolean)
        await w("MotionState", False, ua.VariantType.Boolean)
        await w("RobotMode", 1)
        # PLC
        await w("CPU_Switch", 1)
        await w("Socket_Connect", 0)
        await w("SystemFault", False, ua.VariantType.Boolean)

        # 产线2/3（按真实 NodeId 全量复位）
        for line_index in (2, 3):
            cfg = LINE_NODE_CONFIG.get(line_index, {})
            cnc = cfg.get("cnc", {})
            robot = cfg.get("robot", {})
            plc = cfg.get("plc", {})
            if cnc:
                await w_nodeid(cnc["PartCount"], 0)
                await w_nodeid(cnc["BadPartCount"], 0)
                await w_nodeid(cnc["AlarmCode"], 0)
                await w_nodeid(cnc["MachineStatus"], 0)
                await w_nodeid(cnc["ControlMode"], 1)
                await w_nodeid(cnc["SpindleSpeed"], 0.0, ua.VariantType.Double)
            if robot:
                await w_nodeid(robot["ErrorID"], 0)
                await w_nodeid(robot["GripperStatus"], False, ua.VariantType.Boolean)
                await w_nodeid(robot["MotionState"], False, ua.VariantType.Boolean)
                await w_nodeid(robot["RobotMode"], 1)
            if plc:
                await w_nodeid(plc["CPU_Switch"], 1)
                await w_nodeid(plc["Socket_Connect"], 0)
                await w_nodeid(plc["SystemFault"], False, ua.VariantType.Boolean)
        print("OPC 初值已写入。")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def clear_all(skip_confirm: bool, reset_opc: bool) -> None:
    if not skip_confirm:
        print("即将执行：")
        print("  1) 清空以下 MySQL 表（保留表结构）：")
        for name in TABLES:
            print(f"      - {name}")
        if reset_opc:
            print("  2) 连接 OPC，将 PartCount / 报警 / 机床状态等写回初值")
        else:
            print("  2) 跳过 OPC（已指定 --no-opc）")
        if input("确认请输入「清空」后回车: ").strip() != "清空":
            print("已取消。")
            return

    _truncate_mysql()
    print("数据库业务数据已清空。")

    if reset_opc:
        try:
            await reset_opcua_process_state()
        except Exception as e:
            print(f"OPC 重置失败（数据库已清空）: {e}")
            raise SystemExit(1) from e

    print("可重新运行 main.py。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="清空 raw_telemetry / status_event_log / daily_kpi_report，并可选重置 OPC 初值"
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="不询问，直接执行",
    )
    parser.add_argument(
        "--no-opc",
        action="store_true",
        help="只清数据库，不连接 OPC 写初值",
    )
    args = parser.parse_args()
    asyncio.run(clear_all(skip_confirm=args.yes, reset_opc=not args.no_opc))
