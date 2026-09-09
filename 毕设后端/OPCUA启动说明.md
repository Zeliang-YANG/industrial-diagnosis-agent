# Python OPC UA 本地环境

完整平台现可通过 `run_local.py` 一键启动并写入 MySQL，见项目根目录 `README.md`。以下为单独验证 OPC UA 的用法。

已在项目根目录 `.venv` 安装 Python OPC UA 依赖，无需原来的图形化模拟服务器软件。

## 独立演示（目前即可运行）

在终端执行：

```bash
cd '/Users/yzl/Desktop/agent project'
.venv/bin/python 毕设后端/opcua_server.py --demo
```

地址为 `opc.tcp://localhost:53530/OPCUA/SimulationServer`。服务仅监听本机回环地址。
三条产线共 48 个节点，保持 `config.py` 中的 NodeId 和 ns=3 不变。
按 Ctrl+C 停止。节点数据仅存内存，重启后计数归零。

此模式复用 `cnc_simulator.py` 的随机生产、停机、故障逻辑，不连接数据库。
主轴负载、温度目前为初始占位值，不能当成真实传感器曲线。
仿真器中的“通信中断”是状态位变化，不是实际断开 TCP 连接。

## 接回原后端

```bash
cd '/Users/yzl/Desktop/agent project'
.venv/bin/python 毕设后端/opcua_server.py
```

普通模式只提供可读写节点；原 `main.py` 已内置模拟器，应由它驱动生产并采集入库。
不要同时运行 `--demo` 和 `main.py`，否则有两个模拟器竞争写同一批节点。
独立启动原后端时需要配置数据库及安装 Flask、SQLAlchemy 等依赖。推荐使用 `run_local.py` 管理服务与演示数据库。

## 重建环境

在项目根目录运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r 毕设后端/requirements-opcua.txt
```

## 已验证

通过真实本地 OPC UA TCP 客户端，对三条产线全部 48 个节点检查数据类型和读写；逐线验证生产计数增加、CNC 故障状态映射和故障清除。
测试服务使用端口 53531，测试完成后关闭。

下一步先恢复数据库 → 采集入库 → KPI/API 的闭环，再加入只读 Agent 查询工具。
