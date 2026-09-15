"""设备诊断的只读工具层：参数校验、查询、证据、统一错误结构。"""
import inspect
import re
from collections import Counter
from datetime import date as Date, datetime, time as Time

from sqlalchemy.exc import SQLAlchemyError
from config import ALL_EQUIP_IDS, FAULT_STATES, IDEAL_CYCLE_SEC, SIM_CONFIG, LINE_LAYOUT
from db_models import SessionLocal, RawTelemetry, StatusEventLog
from kpi_engine import calculate_daily_kpi, calculate_line_kpi, day_window, event_query
from fault_catalog import _resolve_alarm_meta
from knowledge_base import search_knowledge


STATE_DEFINITIONS = {
    "srun": "加工或设备运行", "ssby": "待机空转（性能损失）",
    "su_down": "故障停机", "sp_down": "计划停机", "soff": "关机或离线",
    "swork": "机器人作业运行", "swait": "机器人空闲等待",
    "steach": "机器人示教调试", "sgrip": "机器人抓取放置",
    "salarm": "机器人异常报警", "sstop": "PLC 停止模式",
    "serror": "PLC 系统故障", "scomm_err": "PLC 通信中断",
    "unknown": "未知状态",
}


def validate(equip_id, date):
    if not isinstance(equip_id, str) or equip_id not in ALL_EQUIP_IDS:
        raise ValueError("未知设备，请使用工具 schema 中列出的完整设备 ID")
    if not isinstance(date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("date 必须为 YYYY-MM-DD")
    value = Date.fromisoformat(date)
    if value > Date.today():
        raise ValueError("不能查询未来日期")
    return value


def source(session):
    return {"database": session.get_bind().url.database,
            "timezone": "Asia/Shanghai", "mode": "read_only",
            "data_kind": "simulation" if session.get_bind().url.database == "industrial_agent_demo"
                         else "historical_or_test_data_origin_unverified"}


def query_kpi(equip_id, date, compare_date=None):
    target = validate(equip_id, date)
    baseline_date = validate(equip_id, compare_date) if compare_date is not None else None
    as_of = datetime.now()
    current = calculate_daily_kpi(target, equip_id, persist=False, verbose=False, as_of=as_of)
    with SessionLocal() as session:
        provenance = source(session)
    output = {"status": "ok" if current else "no_data", "equip_id": equip_id,
              "date": date, "as_of": as_of.isoformat(), "source": provenance,
              "current": current,
              "definition": {
                  "version": "equipment_daily_v2",
                  "availability": "(t_val+t_loss)/(t_val+t_loss+t_down+t_plan_down)*100",
                  "performance": "observed_count*ideal_cycle_sec/(t_val+t_loss)*100",
                  "quality": "(observed_count-bad_count)/observed_count*100",
                  "oee": "availability*performance*quality/10000",
                  "ideal_cycle_sec": (current or {}).get("metrics", {}).get("ideal_cycle_sec", float(SIM_CONFIG.get("ideal_cycle_sec", IDEAL_CYCLE_SEC))),
                  "parameter_basis": "当前配置重算；历史节拍参数尚未版本化保存",
                  "percent_unit": "0–100; differences are percentage points",
                  "reliability_unit": "minutes",
                  "quantity_basis": "sum_nonnegative_deltas_between_in_day_samples",
                  "warning": "零产量时 Q=100、无故障时 MTBF/MTTR=0 是兼容占位值；须结合 warnings，不能据此作诊断。",
              }}
    if baseline_date is not None:
        baseline = calculate_daily_kpi(baseline_date, equip_id, persist=False, verbose=False, as_of=as_of)
        output["comparison"] = {
            "date": compare_date, "status": "ok" if current and baseline else "insufficient_data",
            "baseline": baseline,
            "delta_percentage_points": {
                key: round(current["metrics"][key] - baseline["metrics"][key], 4)
                for key in ("availability", "performance", "quality", "oee")
            } if current and baseline else None,
            "warning": "按各日已记录区间比较；当天不完整或观测覆盖不同，不能直接归因为整日趋势。",
        }
    return output


def _events(equip_id, date, limit, after_id, faults_only):
    target = validate(equip_id, date)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit 必须是 1–100 的整数")
    if type(after_id) is not int or after_id < 0:
        raise ValueError("after_id 必须是非负整数")
    as_of = datetime.now()
    start, end = day_window(target, as_of)
    with SessionLocal() as session:
        query = event_query(session, equip_id, start, end)
        if faults_only:
            query = query.filter(StatusEventLog.state_code.in_(FAULT_STATES.get(equip_id, set())))
        rows = query.filter(StatusEventLog.id > after_id).order_by(StatusEventLog.id).limit(limit + 1).all()
        truncated = len(rows) > limit
        events = []
        for row in rows[:limit]:
            clipped_start, clipped_end = max(row.start_time, start), min(row.end_time or end, end)
            item = {"id": row.id, "equip_id": row.equip_id, "state_code": row.state_code,
                    "start_time": row.start_time.isoformat(),
                    "end_time": row.end_time.isoformat() if row.end_time else None,
                    "window_start": clipped_start.isoformat(), "window_end": clipped_end.isoformat(),
                    "duration_in_window_sec": max(0, (clipped_end-clipped_start).total_seconds()),
                    "is_open": row.end_time is None, "alarm_code": row.alarm_code,
                    "evidence_ref": f"status_event_log:{row.id}"}
            if faults_only:
                item["catalog"] = _resolve_alarm_meta(row.alarm_code)
                item["catalog_source"] = "fault_catalog.py — 原项目模拟规则，非厂商手册"
                item["root_cause_confirmed"] = False
            events.append(item)
        return {"status": "ok" if events else "no_matching_events", "equip_id": equip_id,
                "date": date, "as_of": as_of.isoformat(), "source": source(session),
                "events": events, "truncated": truncated,
                "next_after_id": events[-1]["id"] if truncated else None,
                "note": "无匹配记录不代表设备正常；事件按 ID 分页，时长仅为所选日期内的交集。"}


def query_device_state(equip_id, date, limit=50, after_id=0):
    return _events(equip_id, date, limit, after_id, False)


def query_fault_events(equip_id, date, limit=50, after_id=0):
    return _events(equip_id, date, limit, after_id, True)


def _time_window(target_date, start_time=None, end_time=None):
    day_start, day_end = day_window(target_date, datetime.now())

    def parse_clock(value, label, fallback):
        if value is None:
            return fallback
        if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", value):
            raise ValueError(f"{label} 必须为 HH:MM 或 HH:MM:SS")
        try:
            parsed = Time.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{label} 不是有效时间") from None
        return datetime.combine(target_date, parsed)

    start = parse_clock(start_time, "start_time", day_start)
    end = min(parse_clock(end_time, "end_time", day_end), day_end)
    if start < day_start or start >= end:
        raise ValueError("时间窗口必须位于查询日期内且 start_time 早于 end_time")
    return start, end


def _numeric_summary(rows, field):
    values = [float(getattr(row, field)) for row in rows if getattr(row, field) is not None]
    if not values:
        return {"sample_count": 0, "min": None, "max": None, "avg": None, "latest": None}
    return {"sample_count": len(values), "min": round(min(values), 3),
            "max": round(max(values), 3), "avg": round(sum(values) / len(values), 3),
            "latest": round(values[-1], 3)}


def _counter_delta(rows, field):
    total, resets, previous = 0, 0, None
    for row in rows:
        value = getattr(row, field)
        if value is None or value < 0:
            previous = None
            continue
        if previous is not None:
            if value < previous:
                resets += 1
            else:
                total += value - previous
        previous = value
    return int(total), resets


def query_telemetry_summary(equip_id, date, start_time=None, end_time=None):
    """按受控时间窗口汇总原始遥测，不返回整段高频明细。"""
    target = validate(equip_id, date)
    start, end = _time_window(target, start_time, end_time)
    with SessionLocal() as session:
        rows = (
            session.query(RawTelemetry)
            .filter(RawTelemetry.equip_id == equip_id,
                    RawTelemetry.timestamp >= start, RawTelemetry.timestamp < end)
            .order_by(RawTelemetry.timestamp, RawTelemetry.id)
            .limit(100001)
            .all()
        )
        if len(rows) > 100000:
            return {"status": "error", "code": "sample_limit_exceeded", "message": "采样点超过 100000，请缩小时间窗口。"}
        if not rows:
            return {"status": "no_data", "equip_id": equip_id, "date": date,
                    "window": {"start": start.isoformat(), "end": end.isoformat()},
                    "source": source(session), "note": "无遥测记录不代表设备正常。"}

        gaps = [max(0.0, (current.timestamp - previous.timestamp).total_seconds())
                for previous, current in zip(rows, rows[1:])]
        large_gaps = [gap for gap in gaps if gap > 5]
        observed_coverage = sum(gap for gap in gaps if gap <= 5)
        part_delta, part_resets = _counter_delta(rows, "part_count")
        bad_delta, bad_resets = _counter_delta(rows, "bad_count")
        states = Counter(row.machine_state or "unknown" for row in rows)
        return {
            "status": "ok", "equip_id": equip_id, "date": date,
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "source": source(session),
            "sample_count": len(rows),
            "observed_start": rows[0].timestamp.isoformat(),
            "observed_end": rows[-1].timestamp.isoformat(),
            "observed_coverage_sec": round(observed_coverage, 3),
            "telemetry_gap_count": len(large_gaps),
            "max_telemetry_gap_sec": round(max(large_gaps), 3) if large_gaps else 0,
            "signals": {
                "spindle_speed": _numeric_summary(rows, "spindle_speed"),
                "spindle_load": _numeric_summary(rows, "spindle_load"),
                "temperature": _numeric_summary(rows, "temperature"),
            },
            "counters": {"part_count_delta": part_delta, "bad_count_delta": bad_delta,
                         "part_count_resets": part_resets, "bad_count_resets": bad_resets},
            "state_sample_counts": dict(sorted(states.items())),
            "state_definitions": {code: STATE_DEFINITIONS.get(code, "未定义状态")
                                  for code in sorted(states)},
            "evidence": {"first_ref": f"raw_telemetry:{rows[0].id}",
                         "last_ref": f"raw_telemetry:{rows[-1].id}"},
            "warnings": (["telemetry_gaps_detected"] if large_gaps else [])
                        + (["counter_reset_detected"] if part_resets or bad_resets else []),
            "note": "统计值仅描述已采集样本；采集空档内的状态和产量不可推断。",
        }


def _validate_line(line_id):
    if type(line_id) is not int or line_id not in LINE_LAYOUT:
        raise ValueError("line_id 必须是 1、2 或 3")
    return line_id


def query_line_timeline(line_id, date, limit=100, after_id=0):
    """查询同一产线三类设备的统一状态时间线。"""
    line_id = _validate_line(line_id)
    target = Date.fromisoformat(date) if isinstance(date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else None
    if target is None or target > Date.today():
        raise ValueError("date 必须为不晚于今天的 YYYY-MM-DD")
    if type(limit) is not int or not 1 <= limit <= 200 or type(after_id) is not int or after_id < 0:
        raise ValueError("分页参数不合法")
    start, end = day_window(target, datetime.now())
    layout = LINE_LAYOUT[line_id]
    role_by_equip = {equip_id: role for role, equip_id in layout.items()}
    with SessionLocal() as session:
        rows = (session.query(StatusEventLog)
                .filter(StatusEventLog.equip_id.in_(list(layout.values())),
                        StatusEventLog.start_time < end,
                        (StatusEventLog.end_time.is_(None) | (StatusEventLog.end_time > start)),
                        StatusEventLog.id > after_id)
                .order_by(StatusEventLog.id).limit(limit + 1).all())
        truncated = len(rows) > limit
        selected = rows[:limit]
        events = []
        for row in selected:
            clipped_start, clipped_end = max(row.start_time, start), min(row.end_time or end, end)
            events.append({"id": row.id, "evidence_ref": f"status_event_log:{row.id}",
                           "equip_id": row.equip_id, "device_role": role_by_equip[row.equip_id],
                           "state_code": row.state_code,
                           "start_time": clipped_start.isoformat(), "end_time": clipped_end.isoformat(),
                           "duration_in_window_sec": max(0, (clipped_end - clipped_start).total_seconds()),
                           "alarm_code": row.alarm_code, "is_open": row.end_time is None})
        events.sort(key=lambda item: (item["start_time"], item["id"]))
        page_window = {
            "start": min((item["start_time"] for item in events), default=None),
            "end": max((item["end_time"] for item in events), default=None),
        }
        return {"status": "ok" if events else "no_matching_events", "line_id": line_id,
                "date": date, "equipment": layout, "source": source(session), "events": events,
                "page_window": page_window,
                "state_definitions": {code: STATE_DEFINITIONS.get(code, "未定义状态")
                                      for code in sorted({item["state_code"] for item in events})},
                "truncated": truncated,
                "next_after_id": max((row.id for row in selected), default=None) if truncated else None,
                "note": "时间先后只提供因果线索，不能单独证明控制逻辑或物理根因。"}


def query_line_kpi(line_id, date, compare_date=None):
    line_id = _validate_line(line_id)
    target = validate(LINE_LAYOUT[line_id]["cnc"], date)
    baseline_date = validate(LINE_LAYOUT[line_id]["cnc"], compare_date) if compare_date else None
    current = calculate_line_kpi(target, persist=False, verbose=False, line_index=line_id)

    def compact(result):
        if not result:
            return None
        return {
            "metrics": result.get("metrics", {}),
            "data_quality": result.get("data_quality"),
            "warnings": result.get("warnings", []),
            "production": {"total_qty": result.get("total_qty", 0),
                           "bad_qty": result.get("bad_qty", 0),
                           "fault_count": result.get("fault_count", 0)},
            "cnc_evidence": result.get("evidence", {}),
            "dashboard": result.get("dashboard", {}),
        }

    with SessionLocal() as session:
        provenance = source(session)
    output = {"status": "ok" if current else "no_data", "line_id": line_id,
              "date": date, "source": provenance, "current": compact(current),
              "definition": {
                  "formula": "weighted_device_availability * cnc_performance * cnc_quality",
                  "device_weights": {"cnc": 0.6, "robot": 0.3, "plc": 0.1},
                  "percent_unit": "0-100",
                  "difference_unit": "percentage_points",
                  "reliability_unit": "minutes",
                  "station_limitations_path": "current.dashboard.stations[].warnings",
              }}
    if baseline_date:
        baseline = calculate_line_kpi(baseline_date, persist=False, verbose=False, line_index=line_id)
        output["comparison"] = {"date": compare_date, "baseline": compact(baseline),
                                "status": "ok" if current and baseline else "insufficient_data",
                                "oee_delta_percentage_points": round(current["metrics"]["oee"] - baseline["metrics"]["oee"], 4)
                                if current and baseline else None}
    return output


TOOLS = {fn.__name__: fn for fn in (
    query_kpi, query_device_state, query_fault_events, query_telemetry_summary,
    query_line_timeline, query_line_kpi, search_knowledge,
)}
TOOL_SCHEMAS = []
for name in TOOLS:
    if name == "search_knowledge":
        properties = {
            "query": {"type": "string", "description": "需要从工业设备诊断知识库检索的问题或关键词"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 5},
        }
        required = ["query"]
    elif name in {"query_line_timeline", "query_line_kpi"}:
        properties = {
            "line_id": {"type": "integer", "enum": sorted(LINE_LAYOUT)},
            "date": {"type": "string", "description": "查询日期 YYYY-MM-DD，Asia/Shanghai"},
        }
        required = ["line_id", "date"]
    else:
        properties = {
            "equip_id": {"type": "string", "enum": ALL_EQUIP_IDS},
            "date": {"type": "string", "description": "查询日期 YYYY-MM-DD，Asia/Shanghai"},
        }
        required = ["equip_id", "date"]
    if name in {"query_kpi", "query_line_kpi"}:
        properties["compare_date"] = {"type": "string", "description": "可选的基准日期 YYYY-MM-DD"}
    elif name in {"query_device_state", "query_fault_events", "query_line_timeline"}:
        properties.update(limit={"type": "integer", "minimum": 1,
                                 "maximum": 200 if name == "query_line_timeline" else 100},
                          after_id={"type": "integer", "minimum": 0})
    elif name == "query_telemetry_summary":
        properties.update(
            start_time={"type": "string", "description": "可选，HH:MM 或 HH:MM:SS"},
            end_time={"type": "string", "description": "可选，HH:MM 或 HH:MM:SS"},
        )
    TOOL_SCHEMAS.append({"name": name, "description": {
        "query_kpi": "查询设备日 KPI 和可选日期对比；先检查数据缺失、适用范围及 warnings。",
        "query_device_state": "查询日期内状态事件，含跨日事件、证据 ID 和分页信息。",
        "query_fault_events": "查询故障状态记录及模拟故障字典；不能据此确认物理根因。",
        "query_telemetry_summary": "查询受控时间窗口内的转速、负载、温度、产量计数和采集质量摘要。",
        "query_line_timeline": "对齐一条产线的 CNC、机器人和 PLC 状态事件，用于分析异常传播顺序。",
        "query_line_kpi": "查询产线级 KPI、三个工位贡献、工位 warnings 和可选日期对比；仅说明已有数据限制时无需再调用设备工具。",
        "search_knowledge": "检索独立的工业设备诊断知识库，返回 OEE、CNC、机器人、PLC/OPC UA 和数据质量相关知识及稳定引用 ID。",
    }[name], "parameters": {"type": "object", "properties": properties,
                            "required": required, "additionalProperties": False}})


def dispatch_tool(name, arguments):
    if name not in TOOLS:
        return {"status": "error", "code": "unknown_tool", "message": "不支持的工具"}
    try:
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须是 JSON object")
        inspect.signature(TOOLS[name]).bind(**arguments)
        return TOOLS[name](**arguments)
    except (ValueError, TypeError):
        return {"status": "error", "code": "invalid_arguments",
                "message": "参数不合法，请核对设备 ID、日期、检索词、分页范围和工具 schema"}
    except SQLAlchemyError:
        return {"status": "error", "code": "database_unavailable",
                "message": "数据库查询失败；不能将连接错误解释为无数据"}
