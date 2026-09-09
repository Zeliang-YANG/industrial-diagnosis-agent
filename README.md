# 工业设备诊断 Agent 改造

当前已恢复：Python OPC UA → 三条产线采集 → MySQL → KPI/事件 API，并增加 7 个只读诊断工具、独立工业诊断知识库 RAG 和 DeepSeek 调用循环。真实模型联调已通过。

## 本地启动

先在一个终端启动后端（同时启动 OPC UA 节点服务、模拟生产、采集和 Flask API）：

```bash
cd '/Users/yzl/Desktop/agent project'
.venv/bin/python 毕设后端/run_local.py
```

再在另一个终端启动前端：

```bash
cd '/Users/yzl/Desktop/agent project/毕设前端'
npm run dev -- --host 127.0.0.1
```

打开终端显示的前端地址。前端请求本机 5001 端口。
不要同时运行旧 `main.py` 或独立 `opcua_server.py --demo`，一键入口已包含这些职责。
两个终端分别按 Ctrl+C 停止；后端会关闭当前状态事件并保存数据。
可用 `--seconds 30` 运行有限时长，或 `--port 5002` 更换 API 端口（前端地址需要相应修改）。

## 数据库

- 复用 `毕设后端/.env` 中的 MySQL 凭据，支持已有环境变量配置。
- `run_local.py` 固定使用同一 MySQL 服务的 `yzl_agent_demo` 库，首次启动自动创建。
- 原 `yzl` 库的毕设数据保留，不由此入口写入。
- 演示库数据持续保留，启动时从最近遥测恢复累计产量和次品计数。
- 这套数据是模拟数据；当前没有连接真实工业设备。
- 此入口适用于单实例本地演示；强制杀进程可能留下未关闭事件，应优先用 Ctrl+C 正常停止。

## 接口

- `http://127.0.0.1:5001/api/health`：MySQL、知识库、模型配置与 Agent Tools 健康状态。
- `http://127.0.0.1:5001/api/workshop/kpi`：三条产线、九台设备 KPI。
- `http://127.0.0.1:5001/api/events`：状态事件和报警统计。
- `http://127.0.0.1:5001/api/kpi/daily`：默认 CNC 当日 KPI。
- `http://127.0.0.1:5001/api/realtime`：原接口的第一条产线实时状态。

## 重建依赖

```bash
cd '/Users/yzl/Desktop/agent project'
python3 -m venv .venv
.venv/bin/python -m pip install -r 毕设后端/requirements.txt
```

## Agent 工具

已统一设备查询与看板计算入口，产线汇总采用当前看板的加权口径。
7 个工具覆盖设备 KPI、设备状态、故障事件、遥测摘要、产线时间线、产线 KPI 和知识检索，均可通过命令行和 `/api/tools` 调用。
用法、计算口径和限制见 `毕设后端/AGENT工具说明.md`。

DeepSeek 配置、模型调用和命令行诊断见 `毕设后端/DeepSeek接入说明.md`。

前端顶部已加入“工业设备诊断 Agent”面板：选择左侧设备工位后可自动生成带完整设备 ID 和日期的问题，页面会展示自然语言结论、工具执行记录和 token 用量。
当前前后端均为本机演示用途；页面通过 `conversation_id` 保留最近 8 条对话，服务端会在 30 分钟后清理会话。该内存状态适合单实例演示。
前端可用 `VITE_API_BASE_URL` 配置后端根地址；未配置时使用 `http://127.0.0.1:5001`。
历史数据可用于构建评估案例，但须区分历史数据、当前模拟数据和文档中的推测。

RAG 语料独立维护在 `毕设后端/knowledge/`，包含 OEE、CNC、机器人、PLC/OPC UA、数据质量和诊断输出规范。系统按 Markdown 章节动态切块并返回稳定的 `KB-*` 引用，不依赖论文、向量数据库或 embedding API。知识库不包含设备厂商维修手册。

Agent 评估集位于 `毕设后端/evals/cases.json`，可运行单个案例或完整集合：

```bash
.venv/bin/python 毕设后端/evaluate_agent.py --case rag_oee_diagnosis_order
.venv/bin/python 毕设后端/evaluate_agent.py
.venv/bin/python 毕设后端/evaluate_agent.py --summary-only
```

评估默认把数据工具绑定到隔离的 `yzl_agent_demo`，可用 `--database` 显式覆盖。评估会调用 DeepSeek 并消耗额度，检查工具选择、参数、禁用工具、关键事实、引用、token 预算和边界措辞。离线测试只验证编排与评分器。

RAG 检索基准不调用模型，可直接运行：

```bash
.venv/bin/python 毕设后端/evaluate_retrieval.py
```

当前基准包含 14 个查询，输出 Hit@1、Hit@3、MRR、无关问题拒绝率和知识库内容指纹。GitHub Actions 会在 MySQL 8.4 服务上运行后端测试、检索评测和前端生产构建。

完整的 Agent 工程成熟度、风险和两周改造顺序见 `AGENT_ENGINEERING_REVIEW.md`。

面试准备、Agent 工作原理、25 个常见问题及量化指标见 `AGENT_INTERVIEW_GUIDE.md`。
