# Agent 工具说明

系统提供 7 个只读工具。所有数据库工具使用固定 SQL/ORM 查询、严格 JSON Schema、设备或产线白名单和结果数量上限；没有任意 SQL、设备写入或控制能力。

| 工具 | 主要输入 | 输出与用途 |
| --- | --- | --- |
| `query_kpi` | `equip_id`、`date`、可选 `compare_date` | 设备 A/P/Q/OEE、时间、产量、同比百分点和数据质量警告 |
| `query_device_state` | `equip_id`、`date`、分页参数 | 当日状态区间、交集时长、稳定事件引用 |
| `query_fault_events` | `equip_id`、`date`、分页参数 | 故障事件、故障码、模拟规则字典与出处 |
| `query_telemetry_summary` | `equip_id`、`date`、可选日内时间窗口 | 样本覆盖、空档、温度/负载/转速统计、计数器变化与原始数据引用 |
| `query_line_timeline` | `line_id`、`date`、分页参数 | CNC、Robot、PLC 合并时间线、分页窗口和事件引用 |
| `query_line_kpi` | `line_id`、`date`、可选 `compare_date` | 产线加权指标、工位贡献、单位和限制 |
| `search_knowledge` | `query`、`top_k` | 独立知识文档章节、来源属性、版本、内容哈希和稳定 `KB-*` 引用 |

`equip_id` 必须使用 `config.py` 中的完整 ID，例如 `BJ-CNC-001`；`line_id` 只能是 1、2、3。系统不把含糊简称静默映射到设备。

## 命令行验证

以下命令无需启动 Flask 或 OPC UA，只要求 MySQL 可用且演示库已有数据：

```bash
.venv/bin/python 毕设后端/tool_cli.py query_kpi --equip-id BJ-CNC-001 --date 2026-09-09
.venv/bin/python 毕设后端/tool_cli.py query_telemetry_summary --equip-id BJ-CNC-001 --date 2026-09-09 --start-time 08:00 --end-time 10:00
.venv/bin/python 毕设后端/tool_cli.py query_line_timeline --line-id 1 --date 2026-09-09 --limit 20
.venv/bin/python 毕设后端/tool_cli.py query_line_kpi --line-id 1 --date 2026-09-09
.venv/bin/python 毕设后端/tool_cli.py search_knowledge --query 'CNC 的 OEE 下降时应该按什么顺序排查？'
```

默认只读 `yzl_agent_demo`。只有明确传 `--database yzl` 时才读取原毕设库。

## 计算与证据口径

- `Tload = Tval + Tloss + Tdown + Tplan`，`Top = Tval + Tloss`。
- `A = Top / Tload`，`P = 产量 × 理想节拍 / Top`，`Q = 合格品 / 产量`，`OEE = A × P × Q`。
- A/P/Q/OEE 单位为百分数；日期比较为百分点；MTBF/MTTR 和事件时长使用分钟。
- 产线展示指标为 `(0.6 × CNC A + 0.3 × Robot A + 0.1 × PLC A) × CNC P × CNC Q`，不是状态区间并集计算的物理产线 OEE。
- 相邻遥测超过 5 秒会报告空档；计数器下降会标记 reset 并跳过该跳变。
- Robot 和 PLC 没有产量口径，不能解释其 OEE/P/Q 占位值。
- 时间线的 `page_window` 只描述本页已返回事件；事件的 `end_time` 不是设备恢复证据。
- `status_event_log:<id>` 与 `raw_telemetry:<id>` 是数据证据引用；模型回答中的此类引用会由编排层校验是否来自实际工具结果。

## RAG

`knowledge/` 与论文分开维护，当前包含 6 份项目自建诊断手册、27 个章节块。检索器采用可解释的本地混合词法排序，支持文件变更自动重载、文档内容哈希和知识库 revision。离线基准包含 14 个查询；当前不含厂商授权维修手册，也没有把通用排查建议当作已确认物理根因。

## 验证

```bash
.venv/bin/python -m unittest discover -s 毕设后端/tests -p 'test_*.py'
.venv/bin/python 毕设后端/evaluate_retrieval.py
```

测试使用随机命名的临时 MySQL 库并在结束后删除，覆盖参数边界、跨午夜、分页、遥测空档、计数器重置、只读 SQL、HTTP 一致性、RAG 与证据核验。
