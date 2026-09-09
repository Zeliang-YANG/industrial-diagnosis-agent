# app.py
from flask import Flask, jsonify, request
from flask_cors import CORS  # 需要 pip install flask-cors 解决跨域
from db_models import SessionLocal, RawTelemetry, StatusEventLog, DailyKPIReport
from kpi_engine import calculate_daily_kpi, day_window
import datetime
import hmac
import os
import threading
import time
from uuid import uuid4
from sqlalchemy import or_, text
import re
from config import (
    EQUIP_ID_CNC,
    EQUIP_ID_ROBOT,
    EQUIP_ID_PLC,
    LINE_LAYOUT,
    SIM_CONFIG,
    IDEAL_CYCLE_SEC,
    KPI_EQUIP_ID,
)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 32 * 1024
_allowed_origins = [item.strip() for item in os.environ.get(
    'ALLOWED_ORIGINS', 'http://127.0.0.1:5173,http://localhost:5173').split(',') if item.strip()]
CORS(app, resources={r"/api/*": {"origins": _allowed_origins}})
try:
    _max_concurrent_agent_requests = max(1, min(32, int(os.environ.get('AGENT_MAX_CONCURRENCY', '4'))))
except ValueError:
    _max_concurrent_agent_requests = 4
_agent_slots = threading.BoundedSemaphore(_max_concurrent_agent_requests)
_conversation_lock = threading.Lock()
_conversations = {}
_conversation_ttl_sec = 30 * 60


def _api_authorized():
    expected = os.environ.get('AGENT_API_TOKEN', '').strip()
    if not expected:
        return True
    supplied = request.headers.get('Authorization', '')
    return supplied.startswith('Bearer ') and hmac.compare_digest(supplied[7:], expected)


def _conversation_history(conversation_id):
    now = time.monotonic()
    with _conversation_lock:
        expired = [key for key, value in _conversations.items()
                   if now - value['updated_at'] > _conversation_ttl_sec]
        for key in expired:
            _conversations.pop(key, None)
        record = _conversations.get(conversation_id)
        return list(record['messages']) if record else []


def _remember_conversation(conversation_id, question, answer):
    now = time.monotonic()
    with _conversation_lock:
        if len(_conversations) >= 1000 and conversation_id not in _conversations:
            oldest = min(_conversations, key=lambda key: _conversations[key]['updated_at'])
            _conversations.pop(oldest, None)
        record = _conversations.setdefault(conversation_id, {'messages': [], 'updated_at': now})
        record['messages'].extend(({'role': 'user', 'content': question},
                                   {'role': 'assistant', 'content': answer}))
        record['messages'] = record['messages'][-8:]
        record['updated_at'] = now


@app.route('/api/health', methods=['GET'])
def health_check():
    """检查本地演示依赖状态，不暴露凭据或连接串。"""
    from agent_tools import TOOL_SCHEMAS
    from deepseek_agent import configuration, AgentError
    from knowledge_base import load_knowledge_chunks, knowledge_revision, KNOWLEDGE_VERSION
    from audit_log import audit_path

    components = {}
    try:
        with SessionLocal() as session:
            session.execute(text('SELECT 1'))
        components['database'] = {'status': 'ok'}
    except Exception:
        components['database'] = {'status': 'error'}

    try:
        chunks = load_knowledge_chunks()
        components['knowledge_base'] = {
            'status': 'ok', 'version': KNOWLEDGE_VERSION,
            'content_revision': knowledge_revision(),
            'document_count': len({item['document_id'] for item in chunks}),
            'chunk_count': len(chunks),
        }
    except Exception:
        components['knowledge_base'] = {'status': 'error'}

    try:
        key, _, model = configuration()
        configured = bool(key and not key.startswith('your_'))
        components['model_provider'] = {
            'status': 'ok' if configured else 'not_configured',
            'provider': 'deepseek', 'model': model,
        }
    except AgentError:
        components['model_provider'] = {'status': 'error', 'provider': 'deepseek'}

    components['agent_tools'] = {'status': 'ok', 'count': len(TOOL_SCHEMAS)}
    audit_parent = audit_path().parent
    try:
        audit_parent.mkdir(parents=True, exist_ok=True)
        audit_ready = os.access(audit_parent, os.W_OK)
    except OSError:
        audit_ready = False
    components['audit_log'] = {'status': 'ok' if audit_ready else 'error'}
    ready = all(item['status'] == 'ok' for item in components.values())
    return jsonify({'status': 'healthy' if ready else 'degraded',
                    'components': components}), 200 if ready else 503


@app.route('/api/agent/chat', methods=['POST'])
def agent_chat():
    from deepseek_agent import run_agent
    from audit_log import record_agent_result
    if not _api_authorized():
        return jsonify({'status': 'error', 'code': 'unauthorized', 'message': '缺少有效访问令牌。'}), 401
    payload = request.get_json(silent=True)
    allowed_fields = {'question', 'conversation_id'}
    if (not isinstance(payload, dict) or 'question' not in payload
            or set(payload) - allowed_fields):
        return jsonify({'status': 'error', 'code': 'invalid_question',
                        'message': '仅接受 question 和可选 conversation_id 字段。'}), 400
    conversation_id = payload.get('conversation_id') or uuid4().hex
    if not isinstance(conversation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}', conversation_id):
        return jsonify({'status': 'error', 'code': 'invalid_conversation_id',
                        'message': 'conversation_id 格式不合法。'}), 400
    if not _agent_slots.acquire(blocking=False):
        return jsonify({'status': 'error', 'code': 'agent_busy', 'message': 'Agent 当前请求已满，请稍后重试。'}), 429
    try:
        result = run_agent(payload['question'], history=_conversation_history(conversation_id))
        result['conversation_id'] = conversation_id
        if isinstance(result.get('answer'), str) and result['answer'].strip():
            _remember_conversation(conversation_id, payload['question'], result['answer'])
        record_agent_result(result)
    finally:
        _agent_slots.release()
    status = 400 if result.get('code') == 'invalid_question' else 503 if result['status'] == 'error' else 200
    return jsonify(result), status


@app.route('/api/tools', methods=['GET'])
def list_agent_tools():
    from agent_tools import TOOL_SCHEMAS
    return jsonify({"tools": TOOL_SCHEMAS})


@app.route('/api/tools/<name>', methods=['POST'])
def call_agent_tool(name):
    from agent_tools import dispatch_tool
    if not _api_authorized():
        return jsonify({'status': 'error', 'code': 'unauthorized', 'message': '缺少有效访问令牌。'}), 401
    result = dispatch_tool(name, request.get_json(silent=True))
    status = 503 if result.get("code") == "database_unavailable" else 400 if result["status"] == "error" else 200
    return jsonify(result), status

STATE_LABELS = {
    "srun": "加工运行",
    "ssby": "待机空转",
    "su_down": "故障停机",
    "sp_down": "计划停机",
    "soff": "关机离线",
    "swork": "作业运行",
    "swait": "空闲等待",
    "steach": "示教调试",
    "sgrip": "抓取放置",
    "salarm": "异常报警",
    "sstop": "停止模式",
    "serror": "系统故障",
    "scomm_err": "通信中断",
}

from fault_catalog import _resolve_alarm_meta

from kpi_service import (DEFAULT_LINE_EQUIP, _safe_percent, _build_station_payload, _build_line_payload)

def _compute_formula_metrics(session, target_date, equip_id):
    # 与 kpi_engine 统一口径，避免屏幕接口与报表接口出现 Ttotal、A/P/Q 不一致。
    result = calculate_daily_kpi(
        target_date=target_date,
        equip_id=equip_id,
        persist=False,
        verbose=False,
    )
    if not result:
        return {
            "a": 0.0,
            "p": 0.0,
            "q": 0.0,
            "oee": 0.0,
            "mtbf": 0.0,
            "mttr": 0.0,
            "t_run_sec": 0,
            "t_down_sec": 0,
            "t_loss_sec": 0,
            "t_plan_sec": 0,
            "t_non_sch_sec": 0,
            "t_total_sec": 0,
            "total_qty": 0,
            "bad_qty": 0,
            "fault_count": 0,
            "ideal_cycle_sec": float(SIM_CONFIG.get("ideal_cycle_sec", IDEAL_CYCLE_SEC)),
        }

    stats = result.get("stats", {})
    metrics = result.get("metrics", {})
    total_qty = int(result.get("total_qty", 0))
    bad_qty = int(result.get("bad_qty", 0))
    fault_count = int(result.get("fault_count", 0))

    return {
        "a": round(float(metrics.get("availability", 0)), 2),
        "p": round(float(metrics.get("performance", 0)), 2),
        "q": round(float(metrics.get("quality", 0)), 2),
        "oee": round(float(metrics.get("oee", 0)), 2),
        "mtbf": round(float(metrics.get("mtbf", 0)), 2),
        "mttr": round(float(metrics.get("mttr", 0)), 2),
        "t_run_sec": int(metrics.get("t_op", 0)),
        "t_down_sec": int(stats.get("t_down", 0)),
        "t_loss_sec": int(stats.get("t_loss", 0)),
        "t_plan_sec": int(stats.get("t_plan_down", 0)),
        "t_non_sch_sec": int(stats.get("t_non_sch", 0)),
        "t_total_sec": int(metrics.get("t_total", 0)),
        "total_qty": total_qty,
        "bad_qty": bad_qty,
        "fault_count": fault_count,
        "ideal_cycle_sec": float(SIM_CONFIG.get("ideal_cycle_sec", IDEAL_CYCLE_SEC)),
    }


def _classify_device_type(equip_id):
    token = (equip_id or "").lower()
    if "cnc" in token or "baoji" in token:
        return "cnc"
    if "kuka" in token or "robot" in token or "r2000" in token:
        return "robot"
    if "plc" in token or "s7" in token or "siemens" in token:
        return "plc"
    return None


def _extract_line_index(equip_id):
    nums = re.findall(r"(\d+)", equip_id or "")
    if not nums:
        return 1
    tail = nums[-1]
    raw = int(tail)
    if raw in (1, 2, 3):
        return raw
    if tail in {"01", "02", "03", "001", "002", "003"}:
        return int(tail[-1])
    # 兜底：将末尾数字映射到 1~3
    return max(1, min(3, raw))


def _resolve_line_equipment(session):
    resolved = {idx: dict(DEFAULT_LINE_EQUIP[idx]) for idx in (1, 2, 3)}
    raw_ids = [row[0] for row in session.query(RawTelemetry.equip_id).distinct().all()]
    event_ids = [row[0] for row in session.query(StatusEventLog.equip_id).distinct().all()]
    for equip_id in set(raw_ids + event_ids):
        device_type = _classify_device_type(equip_id)
        if not device_type:
            continue
        line_index = _extract_line_index(equip_id)
        line_index = max(1, min(3, line_index))
        resolved[line_index][device_type] = equip_id
    return resolved


def _build_workshop_payload(lines):
    line_count = len(lines) or 1
    workshop_oee = sum(float(l["metrics"]["oee"]) for l in lines) / line_count
    workshop_good_rate = sum(float(l["metrics"]["good_rate"]) for l in lines) / line_count
    workshop_maintenance = sum(float(l["metrics"]["maintenance_rate"]) for l in lines) / line_count
    healthy_count = sum(int(l.get("healthy_count", 0)) for l in lines)
    total_devices = 9
    health_rate = _safe_percent(healthy_count, total_devices)

    return {
        "node_type": "workshop",
        "label": "仿真车间",
        "metrics": {
            "oee": round(workshop_oee, 2),
            "good_rate": round(workshop_good_rate, 2),
            "maintenance_rate": round(workshop_maintenance, 2),
            "equipment_health_rate": round(health_rate, 2),
        },
        "healthy_count": int(healthy_count),
        "total_devices": int(total_devices),
        "lines": lines,
    }

# 1. 实时数据接口：给看板大屏使用
@app.route('/api/realtime')
def get_realtime():
    session = SessionLocal()
    try:
        equip_order = [EQUIP_ID_CNC, EQUIP_ID_ROBOT, EQUIP_ID_PLC]

        # 先取最近20条遥测，再按 equip_id 分组（每组取最新）
        latest_telemetry = (
            session.query(RawTelemetry)
            .order_by(RawTelemetry.id.desc())
            .limit(20)
            .all()
        )

        by_equip = {}
        for row in latest_telemetry:
            if row.equip_id not in by_equip:
                by_equip[row.equip_id] = {
                    "equip_id": row.equip_id,
                    "state": row.thesis_state,
                    "speed": row.spindle_speed,
                    "count": row.part_count,
                }

        # 对遥测缺失设备，使用状态事件表补最近状态
        missing = [eid for eid in equip_order if eid not in by_equip]
        if missing:
            latest_events = (
                session.query(StatusEventLog)
                .filter(StatusEventLog.equip_id.in_(missing))
                .order_by(StatusEventLog.id.desc())
                .all()
            )
            for ev in latest_events:
                if ev.equip_id in by_equip:
                    continue
                by_equip[ev.equip_id] = {
                    "equip_id": ev.equip_id,
                    "state": ev.state_code,
                    "speed": None,
                    "count": 0,
                }

        # 确保 CNC/Robot/PLC 三台都返回
        for eid in equip_order:
            by_equip.setdefault(
                eid,
                {
                    "equip_id": eid,
                    "state": "unknown",
                    "speed": None,
                    "count": 0,
                },
            )

        data = [by_equip[eid] for eid in equip_order]
        return jsonify(data)
    finally:
        session.close()

# 2. KPI 报表接口：给报表页使用
@app.route('/api/kpi/daily')
def get_daily_kpi():
    from agent_tools import validate
    equip_id = request.args.get("equip_id", KPI_EQUIP_ID)
    try:
        target = validate(equip_id, request.args.get("date", str(datetime.date.today())))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    res = calculate_daily_kpi(target_date=target, equip_id=equip_id, persist=False, verbose=False)
    return jsonify(res['metrics'] if res else {})


@app.route('/api/update_sim', methods=['POST'])
def update_sim():
    """
    推演中心参数下发接口：
    前端提交 idealCycle / faultRate / setupTime / qualityRate
    动态更新 SIM_CONFIG 里相关参数，作用于后续仿真循环。
    """
    payload = request.get_json(silent=True) or {}

    try:
        if "idealCycle" in payload:
            # 前端单位：秒/件 -> OEE 理想节拍（与仿真 processing_sec 分离）
            ideal_cycle = max(1.0, float(payload["idealCycle"]))
            SIM_CONFIG["ideal_cycle_sec"] = ideal_cycle
            # 保留既有行为：同步影响仿真加工时长，便于联动推演
            SIM_CONFIG["processing_sec"] = ideal_cycle

        if "faultRate" in payload:
            # 前端单位：百分比 -> 每tick概率，做保守缩放
            SIM_CONFIG["cnc_fault_prob_per_tick"] = max(0.0, min(1.0, float(payload["faultRate"]) / 100.0))

        if "setupTime" in payload:
            # 前端单位：分钟/班次 -> 后端计划停机窗口（秒），这里按单次窗口模拟
            SIM_CONFIG["planned_downtime_sec"] = max(0.0, float(payload["setupTime"]) * 60.0)

        if "qualityRate" in payload:
            # 前端给的是合格率 -> 后端存的是次品概率
            q = max(0.0, min(100.0, float(payload["qualityRate"])))
            SIM_CONFIG["bad_part_prob_per_cycle"] = (100.0 - q) / 100.0

        return jsonify({
            "ok": True,
            "message": "仿真参数已更新",
            "sim_config": {
                "ideal_cycle_sec": SIM_CONFIG.get("ideal_cycle_sec", IDEAL_CYCLE_SEC),
                "processing_sec": SIM_CONFIG.get("processing_sec"),
                "cnc_fault_prob_per_tick": SIM_CONFIG.get("cnc_fault_prob_per_tick"),
                "planned_downtime_sec": SIM_CONFIG.get("planned_downtime_sec"),
                "bad_part_prob_per_cycle": SIM_CONFIG.get("bad_part_prob_per_cycle"),
            },
        })
    except (TypeError, ValueError):
        return jsonify({"ok": False, "message": "参数格式错误"}), 400


@app.route('/api/sim/defaults')
def get_sim_defaults():
    """
    读取当前后端仿真参数，供推演中心初始化/恢复默认使用。
    """
    session = SessionLocal()
    try:
        # 统一按仿真车间实时数据口径：
        # totalTime = 本轮仿真首末遥测时间差（分钟）
        # actualCount = 本轮仿真累计增量产量（件）
        latest_cnc = (
            session.query(RawTelemetry)
            .filter(RawTelemetry.equip_id == EQUIP_ID_CNC)
            .order_by(RawTelemetry.id.desc())
            .first()
        )

        first_cnc = (
            session.query(RawTelemetry)
            .filter(RawTelemetry.equip_id == EQUIP_ID_CNC)
            .order_by(RawTelemetry.id.asc())
            .first()
        )

        if latest_cnc and first_cnc and latest_cnc.timestamp and first_cnc.timestamp:
            total_time = max(0.0, (latest_cnc.timestamp - first_cnc.timestamp).total_seconds() / 60.0)
            actual_count = max(0.0, float((latest_cnc.part_count or 0) - (first_cnc.part_count or 0)))
        else:
            total_time = 0.0
            actual_count = 0.0

        return jsonify({
            # 理想节拍优先读独立字段，回退到 config.py 的 IDEAL_CYCLE_SEC
            "idealCycle": float(SIM_CONFIG.get("ideal_cycle_sec", IDEAL_CYCLE_SEC)),
            "faultRate": float(SIM_CONFIG.get("cnc_fault_prob_per_tick", 0)) * 100.0,
            "setupTime": float(SIM_CONFIG.get("planned_downtime_sec", 0)) / 60.0,
            "qualityRate": (1.0 - float(SIM_CONFIG.get("bad_part_prob_per_cycle", 0))) * 100.0,
            "totalTime": total_time,
            "actualCount": actual_count,
        })
    finally:
        session.close()


@app.route('/api/optimization/baseline')
def get_optimization_baseline():
    """
    推演中心基线数据（与 kpi_engine 完全同口径）。
    返回 OEE 计算最小要素，前端可直接按原始公式复算并做参数推演。
    """
    target_date = datetime.date.today()
    equip_id = request.args.get("equip_id", KPI_EQUIP_ID)
    res = calculate_daily_kpi(
        target_date=target_date,
        equip_id=equip_id,
        persist=False,
        verbose=False,
    )
    if not res:
        return jsonify({"ok": False, "message": "暂无可用KPI基线数据"}), 404

    stats = res["stats"]
    metrics = res["metrics"]
    total_qty = res["total_qty"]
    bad_qty = res["bad_qty"]
    ideal_cycle = float(SIM_CONFIG.get("ideal_cycle_sec", IDEAL_CYCLE_SEC))

    return jsonify({
        "ok": True,
        "date": str(target_date),
        "equip_id": equip_id,
        "idealCycleSec": ideal_cycle,
        "tValSec": float(stats.get("t_val", 0)),
        "tLossSec": float(stats.get("t_loss", 0)),
        "tDownSec": float(stats.get("t_down", 0)),
        "tPlanSec": float(stats.get("t_plan_down", 0)),
        "tOffSec": float(stats.get("t_non_sch", 0)),
        "totalQty": float(total_qty),
        "badQty": float(bad_qty),
        "kpiEngine": {
            "availability": float(metrics.get("availability", 0)),
            "performance": float(metrics.get("performance", 0)),
            "quality": float(metrics.get("quality", 0)),
            "oee": float(metrics.get("oee", 0)),
        },
    })


@app.route('/api/events')
def get_events():
    """
    异常分析页事件接口：
    - 最近事件明细（用于甘特图）
    - 报警 Top5
    - 损失状态下钻（基于状态码）
    """
    session = SessionLocal()
    try:
        # 异常分析甘特图需要较长时段可回溯，放宽事件条数上限
        limit = max(1, min(5000, int(request.args.get("limit", 2000))))
        date_str = request.args.get("date")
        equip_ids_raw = (request.args.get("equip_ids") or "").strip()
        equip_ids = [item.strip() for item in equip_ids_raw.split(",") if item.strip()]
        target_date = datetime.date.fromisoformat(date_str) if date_str else datetime.date.today()
        now = datetime.datetime.now()
        day_start, day_end = day_window(target_date, now)

        query = (
            session.query(StatusEventLog)
            .filter(
                StatusEventLog.start_time < day_end,
                or_(StatusEventLog.end_time.is_(None), StatusEventLog.end_time > day_start),
            )
        )
        if equip_ids:
            query = query.filter(StatusEventLog.equip_id.in_(equip_ids))
        rows = query.order_by(StatusEventLog.start_time.desc()).limit(limit).all()

        events = []
        alarm_stats = {}
        loss_by_state = {}
        for row in reversed(rows):
            start_time = max(row.start_time, day_start)
            end_time = min(row.end_time or day_end, day_end)
            duration_sec = max(0, int((end_time - start_time).total_seconds()))
            alarm_code = int(row.alarm_code or 0)
            alarm_meta = _resolve_alarm_meta(alarm_code)
            state_code = row.state_code or "unknown"

            events.append({
                "id": int(row.id),
                "equip_id": row.equip_id,
                "state_code": state_code,
                "state_label": STATE_LABELS.get(state_code, state_code),
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat() if end_time else None,
                "duration_sec": int(duration_sec),
                "alarm_code": alarm_code,
                "alarm_meta": alarm_meta,
            })

            if alarm_code > 0:
                key = str(alarm_code)
                stat = alarm_stats.setdefault(
                    key,
                    {"alarm_code": alarm_code, "count": 0, "duration_sec": 0, "equip_ids": set()},
                )
                stat["count"] += 1
                stat["duration_sec"] += int(duration_sec)
                stat["equip_ids"].add(row.equip_id)

            # 当前数据库未持久化 simulator loss_reason，先按状态码下钻损失来源
            if state_code in {"ssby", "swait", "sp_down", "steach", "sstop", "soff", "scomm_err"}:
                bucket = loss_by_state.setdefault(
                    state_code,
                    {"state_code": state_code, "state_label": STATE_LABELS.get(state_code, state_code), "count": 0, "duration_sec": 0},
                )
                bucket["count"] += 1
                bucket["duration_sec"] += int(duration_sec)

        alarm_top5 = sorted(
            [
                {
                    "alarm_code": v["alarm_code"],
                    "alarm_meta": _resolve_alarm_meta(v["alarm_code"]),
                    "count": v["count"],
                    "duration_sec": v["duration_sec"],
                    "equip_ids": sorted(list(v["equip_ids"])),
                }
                for v in alarm_stats.values()
            ],
            key=lambda x: (-x["count"], -x["duration_sec"]),
        )[:5]

        loss_breakdown = sorted(
            list(loss_by_state.values()),
            key=lambda x: -x["duration_sec"],
        )

        return jsonify({
            "ok": True,
            "target_date": str(target_date),
            "equip_ids": equip_ids,
            "events": events,
            "alarm_top5": alarm_top5,
            "loss_breakdown": loss_breakdown,
        })
    except ValueError:
        return jsonify({"ok": False, "message": "date 格式错误，应为 YYYY-MM-DD"}), 400
    finally:
        session.close()


@app.route('/api/kpi/history')
def get_history_kpi():
    """
    KPI 历史接口：读取 DailyKPIReport 最近 N 天数据。
    若缺失记录，按天调用 kpi_engine 现场计算（不落库）补齐返回。
    """
    session = SessionLocal()
    try:
        days = max(1, min(30, int(request.args.get("days", 7))))
        equip_id = request.args.get("equip_id", KPI_EQUIP_ID)

        end_date = datetime.date.today()
        start_date = end_date - datetime.timedelta(days=days - 1)
        dates = [start_date + datetime.timedelta(days=i) for i in range(days)]

        series = []
        for d in dates:
            calc = calculate_daily_kpi(target_date=d, equip_id=equip_id, persist=False, verbose=False)
            m = calc["metrics"] if calc else {}
            series.append({
                "date": str(d),
                "oee": float(m.get("oee", 0)),
                "availability": float(m.get("availability", 0)),
                "performance": float(m.get("performance", 0)),
                "quality": float(m.get("quality", 0)),
                "mtbf": float(m.get("mtbf", 0)),
                "mttr": float(m.get("mttr", 0)),
            })

        return jsonify({
            "ok": True,
            "equip_id": equip_id,
            "days": days,
            "series": series,
        })
    finally:
        session.close()


@app.route('/api/screen/metrics')
def get_screen_metrics():
    """
    单页大屏接口：按公式输出实时 KPI，并给出昨日对比用于雷达图。
    """
    session = SessionLocal()
    try:
        equip_id = request.args.get("equip_id", KPI_EQUIP_ID)
        today = datetime.date.today()
        yesterday = today - datetime.timedelta(days=1)

        current = _compute_formula_metrics(session, today, equip_id)
        previous = _compute_formula_metrics(session, yesterday, equip_id)

        return jsonify({
            "ok": True,
            "timestamp": datetime.datetime.now().isoformat(),
            "equip_id": equip_id,
            "current": current,
            "previous": previous,
            "radar": {
                "labels": ["A", "P", "Q", "OEE"],
                "today": [current["a"], current["p"], current["q"], current["oee"]],
                "yesterday": [previous["a"], previous["p"], previous["q"], previous["oee"]],
            },
        })
    finally:
        session.close()


@app.route('/api/workshop/kpi')
def get_workshop_kpi():
    session = SessionLocal()
    try:
        date_str = request.args.get("date")
        target_date = datetime.date.fromisoformat(date_str) if date_str else datetime.date.today()
        persist_daily = request.args.get("persist", "0") != "0"
        line_equip = _resolve_line_equipment(session)

        lines = []
        for idx in (1, 2, 3):
            equip_map = line_equip.get(idx, {})
            stations = []
            for dtype in ("cnc", "robot", "plc"):
                stations.append(
                    _build_station_payload(
                        session=session,
                        target_date=target_date,
                        line_index=idx,
                        device_type=dtype,
                        equip_id=equip_map.get(dtype, DEFAULT_LINE_EQUIP[idx][dtype]),
                        persist_daily=persist_daily,
                    )
                )
            lines.append(_build_line_payload(idx, stations))

        workshop = _build_workshop_payload(lines)
        return jsonify(
            {
                "ok": True,
                "target_date": str(target_date),
                "workshop": workshop,
            }
        )
    except ValueError:
        return jsonify({"ok": False, "message": "date 格式错误，应为 YYYY-MM-DD"}), 400
    finally:
        session.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)
