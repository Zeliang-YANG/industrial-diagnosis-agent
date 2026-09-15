"""看板与产线报表共享的展示及加权汇总口径。"""
from config import LINE_LAYOUT
from db_models import StatusEventLog
from kpi_engine import calculate_daily_kpi, day_window, event_query

LINE_DEVICE_WEIGHTS = {
    "cnc": 0.6,
    "robot": 0.3,
    "plc": 0.1,
}

DEVICE_TYPE_LABEL = {
    "cnc": "CNC",
    "robot": "机器人",
    "plc": "PLC",
}

DEFAULT_LINE_EQUIP = {
    idx: {
        "cnc": payload["cnc"],
        "robot": payload["robot"],
        "plc": payload["plc"],
    }
    for idx, payload in LINE_LAYOUT.items()
}

UNHEALTHY_STATES = {"su_down", "salarm", "serror", "scomm_err", "soff"}


def _safe_percent(numerator, denominator):
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator) * 100.0


def _weighted_average(entries):
    weight_sum = sum(item["weight"] for item in entries)
    if weight_sum <= 0:
        return 0.0
    weighted_value = sum(item["value"] * item["weight"] for item in entries)
    return weighted_value / weight_sum


def _latest_state(session, equip_id, target_date, as_of=None):
    start, end = day_window(target_date, as_of)
    latest = (
        event_query(session, equip_id, start, end)
        .order_by(StatusEventLog.start_time.desc(), StatusEventLog.id.desc())
        .first()
    )
    return (latest.state_code if latest else "") or ""


def _build_station_payload(session, target_date, line_index, device_type, equip_id, persist_daily=False, as_of=None):
    result = calculate_daily_kpi(
        target_date=target_date,
        equip_id=equip_id,
        persist=persist_daily,
        as_of=as_of,
        verbose=False,
    )
    stats = (result or {}).get(
        "stats",
        {
            "t_val": 0,
            "t_loss": 0,
            "t_down": 0,
            "t_plan_down": 0,
            "t_non_sch": 0,
        },
    )
    metrics = (result or {}).get(
        "metrics",
        {
            "availability": 0,
            "performance": 0,
            "quality": 0,
            "oee": 0,
            "mtbf": 0,
            "mttr": 0,
            "t_load": 0,
            "t_op": 0,
            "t_total": 0,
        },
    )
    total_qty = (result or {}).get("total_qty", 0)
    bad_qty = (result or {}).get("bad_qty", 0)
    fault_count = (result or {}).get("fault_count", 0)
    t_plan = float(stats.get("t_plan_down", 0))
    t_down = float(stats.get("t_down", 0))
    t_load = float(metrics.get("t_load", 0))
    maintenance_rate = _safe_percent(t_plan + t_down, t_load)

    mtbf = float(metrics.get("mtbf", 0))
    mttr = float(metrics.get("mttr", 0))
    fault_free_rate = _safe_percent(mtbf, mtbf + mttr)
    latest_state = _latest_state(session, equip_id, target_date, as_of)
    healthy = bool(latest_state) and latest_state not in UNHEALTHY_STATES
    station_no = {"cnc": 1, "robot": 2, "plc": 3}[device_type]

    return {
        "node_type": "station",
        "line_index": line_index,
        "station_no": station_no,
        "label": f"工位{station_no}（{DEVICE_TYPE_LABEL[device_type]}）",
        "equip_id": equip_id,
        "device_type": device_type,
        "healthy": healthy,
        "has_data": result is not None,
        "oee_applicable": (result or {}).get("oee_applicable", False),
        "warnings": (result or {}).get("warnings", ["no_data"]),
        "latest_state": latest_state,
        "time_factors_sec": {
            "t_val": int(stats.get("t_val", 0)),
            "t_loss": int(stats.get("t_loss", 0)),
            "t_down": int(stats.get("t_down", 0)),
            "t_plan_down": int(stats.get("t_plan_down", 0)),
            "t_non_sch": int(stats.get("t_non_sch", 0)),
            "t_op": int(metrics.get("t_op", 0)),
            "t_load": int(metrics.get("t_load", 0)),
            "t_total": int(metrics.get("t_total", 0)),
        },
        "metrics": {
            "oee": round(float(metrics.get("oee", 0)), 2),
            "availability": round(float(metrics.get("availability", 0)), 2),
            "performance": round(float(metrics.get("performance", 0)), 2),
            "quality": round(float(metrics.get("quality", 0)), 2),
            "mtbf": round(mtbf, 2),
            # plc 按用户口径展示 mtvf（同 mtbf）
            "mtvf": round(mtbf, 2),
            "mttr": round(mttr, 2),
            "maintenance_rate": round(maintenance_rate, 2),
            "fault_free_rate": round(fault_free_rate, 2),
        },
        "production": {
            "total_qty": int(total_qty),
            "bad_qty": int(bad_qty),
            "good_qty": int(max(0, total_qty - bad_qty)),
            "fault_count": int(fault_count),
        },
    }

def _build_line_payload(line_index, stations):
    by_type = {s["device_type"]: s for s in stations}
    cnc = by_type.get("cnc", {})
    weighted_availability = _weighted_average(
        [
            {
                "value": float(by_type.get(dtype, {}).get("metrics", {}).get("availability", 0)),
                "weight": weight,
            }
            for dtype, weight in LINE_DEVICE_WEIGHTS.items()
        ]
    )
    weighted_maintenance = _weighted_average(
        [
            {
                "value": float(by_type.get(dtype, {}).get("metrics", {}).get("maintenance_rate", 0)),
                "weight": weight,
            }
            for dtype, weight in LINE_DEVICE_WEIGHTS.items()
        ]
    )
    weighted_fault_free = _weighted_average(
        [
            {
                "value": float(by_type.get(dtype, {}).get("metrics", {}).get("fault_free_rate", 0)),
                "weight": weight,
            }
            for dtype, weight in LINE_DEVICE_WEIGHTS.items()
        ]
    )
    cnc_performance = float(cnc.get("metrics", {}).get("performance", 0))
    cnc_quality = float(cnc.get("metrics", {}).get("quality", 0))
    line_oee = (weighted_availability / 100.0) * (cnc_performance / 100.0) * (cnc_quality / 100.0) * 100.0
    healthy_count = sum(1 for s in stations if s.get("healthy"))

    return {
        "node_type": "line",
        "line_index": line_index,
        "label": f"产线{line_index}",
        "metrics": {
            "oee": round(line_oee, 2),
            "good_rate": round(cnc_quality, 2),
            "maintenance_rate": round(weighted_maintenance, 2),
            "fault_free_rate": round(weighted_fault_free, 2),
            "availability_weighted": round(weighted_availability, 2),
        },
        "healthy_count": int(healthy_count),
        "total_devices": 3,
        "stations": sorted(stations, key=lambda x: x.get("station_no", 0)),
    }
