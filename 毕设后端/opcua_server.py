"""本地 OPC UA 节点服务器；默认由原 main.py 驱动模拟生产。"""
import argparse
import asyncio
import logging

from asyncua import Server, ua
from config import LINE_NODE_CONFIG, OPC_URL
from cnc_simulator import CNCSimulator


def initial_value(name):
    if name in {"MotionState", "GripperStatus", "SystemFault"}:
        return False, ua.VariantType.Boolean
    if name in {"SpindleSpeed", "SpindleLoad", "Temperature"}:
        return (25.0 if name == "Temperature" else 0.0), ua.VariantType.Double
    return {"ControlMode": 1, "RobotMode": 1, "CPU_Switch": 1,
            "FeedOverride": 100}.get(name, 0), ua.VariantType.Int32


async def build_server(endpoint=OPC_URL):
    server = Server()
    await server.init()
    server.set_endpoint(endpoint)
    # 匿名读写仅用于本机模拟，监听回环地址。
    server.socket_address = ("127.0.0.1", server.endpoint.port)
    server.set_server_name("Industrial KPI Python Simulator")
    server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
    await server.register_namespace("urn:industrial-kpi:reserved")
    index = await server.register_namespace("urn:industrial-kpi:devices")
    if index != 3:
        raise RuntimeError("原项目要求设备命名空间 ns=3")
    lines = {}
    for line_id, roles in LINE_NODE_CONFIG.items():
        folder = await server.nodes.objects.add_object(
            ua.NodeId(f"Line{line_id}", index), f"Line{line_id}")
        lines[line_id] = {}
        for role, signals in roles.items():
            group = await folder.add_object(
                ua.NodeId(f"Line{line_id}.{role}", index), role)
            for name, node_id in signals.items():
                value, kind = initial_value(name)
                node = await group.add_variable(node_id, name, value, varianttype=kind)
                await node.set_writable()
                lines[line_id][name] = node
    return server, lines


def make_simulator(nodes):
    async def write(name, value, variant_type=ua.VariantType.Int32):
        await nodes[name].write_value(ua.DataValue(ua.Variant(value, variant_type)))
    return CNCSimulator(write)


async def run(demo=False):
    server, lines = await build_server()
    simulators = {key: make_simulator(nodes) for key, nodes in lines.items()}
    async with server:
        print(f"OPC UA 已启动：{OPC_URL}，3 条产线 / 48 个节点", flush=True)
        print("模式：" + ("独立模拟（不要同时运行 main.py）" if demo else
                          "节点服务（可另行启动 main.py 驱动生产与采集）"), flush=True)
        while True:
            if demo:
                for key, nodes in lines.items():
                    data = {name: await node.read_value() for name, node in nodes.items()}
                    await simulators[key].simulate_production(data)
            await asyncio.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="无需数据库，独立模拟三条产线")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        asyncio.run(run(args.demo))
    except KeyboardInterrupt:
        print("OPC UA 服务已停止。")
