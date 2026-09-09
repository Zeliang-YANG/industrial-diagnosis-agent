# DeepSeek 接入说明

项目通过 DeepSeek 的 OpenAI 兼容接口完成真实模型调用。编排器支持 7 个只读工具、确定性高置信路由、工具参数校验、有限重试、证据校验和短期会话上下文。

## 配置

在 `毕设后端/.env` 填写：

```dotenv
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_USE_SYSTEM_PROXY=false
DEEPSEEK_MAX_RETRIES=2
```

Key 只保存在本机；`.env` 已被 Git 忽略。可选运行配置：

```dotenv
AGENT_API_TOKEN=
AGENT_MAX_CONCURRENCY=4
ALLOWED_ORIGINS=http://127.0.0.1:5173,http://localhost:5173
```

设置 `AGENT_API_TOKEN` 后，`/api/agent/chat` 和工具 POST 接口要求 `Authorization: Bearer <token>`。这是本地演示保护，不等同于企业 IdP、RBAC 或租户隔离。

## 检查与运行

```bash
.venv/bin/python 毕设后端/agent_cli.py --check
.venv/bin/python 毕设后端/agent_cli.py '查询 BJ-CNC-001 今天的 OEE，并结合停机事件说明异常和数据限制'
```

启动整套系统后也可调用：

```bash
curl --noproxy '*' http://127.0.0.1:5001/api/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"question":"查询 1 号产线今天的 KPI，并解释各工位贡献","conversation_id":"demo-session"}'
```

响应包含 `answer`、`conversation_id`、request ID、状态、工具 trace、模型/工具耗时、provider request ID、token 用量、RAG 与数据证据核验结果。日志只保存这些运行元数据，不保存问题、回答、工具参数或原始工具结果。

## 编排方式

1. 策略层先拒绝索取密钥、写 SQL 和绕过安全联锁等请求，不消耗模型 token。
2. 高置信设备遥测、产线 KPI、产线时间线和知识问题先由确定性路由抽取参数并执行对应工具。
3. DeepSeek 基于已取得的结构化证据生成回答；其他问题只暴露相关工具 schema，由模型选择工具。
4. 本机校验工具名、参数和结果大小，模型不能调用未暴露工具。
5. 编排层检查知识引用和事件/遥测引用；证据不完整时将状态降为 `needs_review`。

单次诊断最多 6 次模型请求、12 次工具执行，每次输出最多 2048 tokens。网络错误、429 和可恢复 5xx 使用指数退避与随机抖动，重试次数由 `DEEPSEEK_MAX_RETRIES` 控制。聊天接口默认最多 4 个并发模型任务，请求体最大 32 KB。会话只保存最近 8 条消息，30 分钟无活动后过期，适用于单实例本地演示。

## 评测

离线编排测试不会调用 DeepSeek：

```bash
.venv/bin/python -m unittest discover -s 毕设后端/tests -p 'test_*.py'
```

真实模型评测会消耗账号额度：

```bash
.venv/bin/python 毕设后端/evaluate_agent.py --case rag_oee_diagnosis_order
.venv/bin/python 毕设后端/evaluate_agent.py --summary-only
```

当前 8 个案例覆盖 RAG、信息缺失、遥测/产线工具精确参数、分页边界、证据引用、密钥请求和安全联锁越界请求。评测默认绑定隔离的 `yzl_agent_demo`，并检查状态、工具选择、参数、token 预算、关键事实、禁用表述、grounding 和时间线边界。
