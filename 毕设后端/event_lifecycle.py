"""修复本地模拟器异常退出后遗留的开放状态事件。"""
from datetime import datetime

from db_models import RawTelemetry, StatusEventLog
from config import LINE_LAYOUT


def reconcile_stale_open_events(session, now=None):
    """关闭旧开放事件，返回可审计的变更摘要。

    仅应在确认没有另一采集实例运行后调用。优先以同设备的下一条事件开始时间
    作为结束时间；没有下一事件时，以对应产线最后遥测时间作为采集停止边界。
    """
    now = now or datetime.now()
    equip_to_cnc = {
        equip_id: line["cnc"]
        for line in LINE_LAYOUT.values()
        for equip_id in line.values()
    }
    changes = []
    open_events = (
        session.query(StatusEventLog)
        .filter(StatusEventLog.end_time.is_(None))
        .order_by(StatusEventLog.start_time, StatusEventLog.id)
        .all()
    )
    for item in open_events:
        next_event = (
            session.query(StatusEventLog)
            .filter(
                StatusEventLog.equip_id == item.equip_id,
                StatusEventLog.start_time > item.start_time,
            )
            .order_by(StatusEventLog.start_time, StatusEventLog.id)
            .first()
        )
        if next_event:
            cnc_id = equip_to_cnc.get(item.equip_id)
            latest_before_next = (
                session.query(RawTelemetry)
                .filter(
                    RawTelemetry.equip_id == cnc_id,
                    RawTelemetry.timestamp >= item.start_time,
                    RawTelemetry.timestamp < next_event.start_time,
                )
                .order_by(RawTelemetry.timestamp.desc(), RawTelemetry.id.desc())
                .first()
            )
            gap_sec = (
                (next_event.start_time - latest_before_next.timestamp).total_seconds()
                if latest_before_next else 0
            )
            if latest_before_next and gap_sec > 5:
                end_time = latest_before_next.timestamp
                basis = f"last_observation_before_gap:{latest_before_next.id}"
            else:
                end_time = next_event.start_time
                basis = f"next_event:{next_event.id}"
        else:
            cnc_id = equip_to_cnc.get(item.equip_id)
            latest = (
                session.query(RawTelemetry)
                .filter(
                    RawTelemetry.equip_id == cnc_id,
                    RawTelemetry.timestamp >= item.start_time,
                    RawTelemetry.timestamp <= now,
                )
                .order_by(RawTelemetry.timestamp.desc(), RawTelemetry.id.desc())
                .first()
            )
            end_time = latest.timestamp if latest else item.start_time
            basis = f"latest_telemetry:{latest.id}" if latest else "no_later_observation"
        end_time = max(item.start_time, min(end_time, now))
        item.end_time = end_time
        item.duration_sec = max(0, int((end_time - item.start_time).total_seconds()))
        changes.append({
            "event_id": item.id,
            "equip_id": item.equip_id,
            "start_time": item.start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "duration_sec": item.duration_sec,
            "basis": basis,
        })
    if changes:
        session.commit()
    return changes


def correct_known_interrupted_events(session, event_ids, now=None):
    """纠正曾被截到重启时刻的指定事件；调用方必须提供已核对的事件 ID。"""
    now = now or datetime.now()
    equip_to_cnc = {
        equip_id: line["cnc"]
        for line in LINE_LAYOUT.values()
        for equip_id in line.values()
    }
    changes = []
    for event_id in event_ids:
        item = session.get(StatusEventLog, event_id)
        if item is None:
            continue
        next_event = (
            session.query(StatusEventLog)
            .filter(
                StatusEventLog.equip_id == item.equip_id,
                StatusEventLog.start_time > item.start_time,
            )
            .order_by(StatusEventLog.start_time, StatusEventLog.id)
            .first()
        )
        if next_event is None:
            continue
        latest = (
            session.query(RawTelemetry)
            .filter(
                RawTelemetry.equip_id == equip_to_cnc.get(item.equip_id),
                RawTelemetry.timestamp >= item.start_time,
                RawTelemetry.timestamp < next_event.start_time,
            )
            .order_by(RawTelemetry.timestamp.desc(), RawTelemetry.id.desc())
            .first()
        )
        if latest is None or (next_event.start_time - latest.timestamp).total_seconds() <= 5:
            continue
        old_end = item.end_time
        item.end_time = max(item.start_time, min(latest.timestamp, now))
        item.duration_sec = max(0, int((item.end_time - item.start_time).total_seconds()))
        changes.append({
            "event_id": item.id,
            "old_end_time": old_end.isoformat() if old_end else None,
            "end_time": item.end_time.isoformat(),
            "duration_sec": item.duration_sec,
            "basis": f"last_observation_before_gap:{latest.id}",
        })
    if changes:
        session.commit()
    return changes
