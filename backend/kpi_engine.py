from sqlalchemy import or_
from datetime import datetime, date, time, timedelta

from config import (
    IDEAL_CYCLE_SEC,
    SIM_CONFIG,
    KPI_EQUIP_ID,
    OEE_APPLICABLE_EQUIP_IDS,
    STATE_TO_TIME_BUCKET,
    FAULT_STATES,
    TIME_BUCKET_VAL,
    TIME_BUCKET_LOSS,
    TIME_BUCKET_DOWN,
    TIME_BUCKET_PLAN,
    TIME_BUCKET_NON_SCH,
    LINE_LAYOUT,
)
from db_models import SessionLocal, RawTelemetry, StatusEventLog, DailyKPIReport


def day_window(target_date, as_of=None):
    """本机业务时区的左闭右开日窗口；当天截止到查询快照。"""
    start = datetime.combine(target_date, time.min)
    return start, min(start + timedelta(days=1), as_of or datetime.now())


def event_query(session, equip_id, start, end):
    return session.query(StatusEventLog).filter(
        StatusEventLog.equip_id == equip_id,
        StatusEventLog.start_time < end,
        or_(StatusEventLog.end_time.is_(None), StatusEventLog.end_time > start),
    )


def _empty_stats():
    return {
        TIME_BUCKET_VAL: 0,
        TIME_BUCKET_LOSS: 0,
        TIME_BUCKET_DOWN: 0,
        TIME_BUCKET_PLAN: 0,
        TIME_BUCKET_NON_SCH: 0,
    }


def _accumulate_event(stats, fault_count, equip_id, ev, duration_sec):
    equip_map = STATE_TO_TIME_BUCKET.get(equip_id, {})
    bucket = equip_map.get(ev.state_code)
    if bucket in stats:
        stats[bucket] += duration_sec
    if ev.state_code in FAULT_STATES.get(equip_id, set()):
        fault_count += 1
    return fault_count


def _collect_equip_times(session, target_date, equip_id, as_of=None):
    start, end = day_window(target_date, as_of)
    events = event_query(session, equip_id, start, end).order_by(StatusEventLog.id).all()
    stats = _empty_stats()
    fault_count = 0
    for ev in events:
        duration = max(0, (min(ev.end_time or end, end) - max(ev.start_time, start)).total_seconds())
        fault_count = _accumulate_event(stats, fault_count, equip_id, ev, duration)
    return stats, fault_count, events


def _collect_qty(session, target_date, equip_id, as_of=None):
    start, end = day_window(target_date, as_of)
    rows = session.query(RawTelemetry).filter(
        RawTelemetry.equip_id == equip_id,
        RawTelemetry.timestamp >= start, RawTelemetry.timestamp < end,
    ).order_by(RawTelemetry.timestamp, RawTelemetry.id).all()
    warnings = []
    def delta(field):
        total = 0
        previous = None
        for row in rows:
            value = getattr(row, field)
            if value is None or value < 0:
                warnings.append("invalid_counter_sample")
                previous = None
                continue
            if previous is not None:
                if value < previous:
                    warnings.append("counter_reset_detected")
                    # 只累计可观察的正增量，不猜测归零前漏采的产量。
                else:
                    total += value - previous
            previous = value
        return total
    total_qty, bad_qty = delta("part_count"), delta("bad_count")
    if len(rows) < 2:
        warnings.append("insufficient_counter_samples")
    if bad_qty > total_qty:
        warnings.append("bad_count_exceeds_total")
    return total_qty, bad_qty, rows, sorted(set(warnings))


def _kpi_from_components(stats, total_qty, bad_qty, fault_count):
    t_val = stats[TIME_BUCKET_VAL]
    t_loss = stats[TIME_BUCKET_LOSS]
    t_down = stats[TIME_BUCKET_DOWN]
    t_plan = stats[TIME_BUCKET_PLAN]
    t_off = stats[TIME_BUCKET_NON_SCH]

    # 项目采用以下 KPI 统计口径：
    # Tload = Ttotal - Tnon_sch
    # Top   = Tload - Tplan_down - Tdown
    # Tval  = Top - Tloss
    t_total = t_val + t_loss + t_down + t_plan + t_off
    t_load = t_total - t_off
    t_op = t_load - t_plan - t_down
    # 避免极端脏数据导致负值
    t_op = max(0, t_op)

    availability = (t_op / t_load * 100) if t_load > 0 else 0
    ideal_cycle_sec = float(SIM_CONFIG.get("ideal_cycle_sec", IDEAL_CYCLE_SEC))
    theoretical_time = total_qty * ideal_cycle_sec
    performance = (theoretical_time / t_op * 100) if t_op > 0 else 0
    quality = ((total_qty - bad_qty) / total_qty * 100) if total_qty > 0 else 100
    oee = (availability * performance * quality) / 10000
    mtbf = (t_op / fault_count / 60) if fault_count > 0 else 0
    mttr = (t_down / fault_count / 60) if fault_count > 0 else 0

    return {
        "ideal_cycle_sec": ideal_cycle_sec,
        "t_total": t_total,
        "t_load": t_load,
        "t_op": t_op,
        "availability": availability,
        "performance": performance,
        "quality": quality,
        "oee": oee,
        "mtbf": mtbf,
        "mttr": mttr,
    }


def _upsert_daily_kpi(session, target_date, equip_id, metrics, total_qty, bad_qty):
    rec = session.query(DailyKPIReport).filter_by(date=target_date, equip_id=equip_id).first()
    if not rec:
        rec = DailyKPIReport(date=target_date, equip_id=equip_id)
        session.add(rec)
    rec.total_time = metrics["t_total"]
    rec.run_time = metrics["t_op"]
    rec.down_time = metrics["t_down"]
    rec.val_time = metrics["t_val"]
    rec.total_count = total_qty
    rec.good_count = total_qty - bad_qty
    rec.availability = round(metrics["availability"], 2)
    rec.performance = round(metrics["performance"], 2)
    rec.quality = round(metrics["quality"], 2)
    rec.oee = round(metrics["oee"], 2)
    rec.mtbf = round(metrics["mtbf"], 2)
    rec.mttr = round(metrics["mttr"], 2)


def _materialize_metrics(stats, total_qty, bad_qty, fault_count):
    out = _kpi_from_components(stats, total_qty, bad_qty, fault_count)
    out["t_val"] = stats[TIME_BUCKET_VAL]
    out["t_loss"] = stats[TIME_BUCKET_LOSS]
    out["t_down"] = stats[TIME_BUCKET_DOWN]
    out["t_plan"] = stats[TIME_BUCKET_PLAN]
    out["t_off"] = stats[TIME_BUCKET_NON_SCH]
    return out


def _print_breakdown(title, metrics, total_qty, bad_qty, fault_count, oee_applicable=True):
    print(f"\n[{title}]")
    print(
        "时间要素(秒): "
        f"Tval={metrics['t_val']} | Tloss={metrics['t_loss']} | "
        f"Tdown={metrics['t_down']} | Tplan={metrics['t_plan']} | Tnon_sch={metrics['t_off']}"
    )
    print(
        "派生时间(秒): "
        f"Top={metrics['t_op']} | Tload={metrics['t_load']} | Ttotal={metrics['t_total']}"
    )
    print(
        f"产量: total={total_qty} | bad={bad_qty} | faults={fault_count}"
    )
    if oee_applicable:
        print(
            "KPI: "
            f"OEE={metrics['oee']:.2f}% | A={metrics['availability']:.2f}% | "
            f"P={metrics['performance']:.2f}% | Q={metrics['quality']:.2f}% | "
            f"MTBF={metrics['mtbf']:.2f}min | MTTR={metrics['mttr']:.2f}min"
        )
    else:
        print(
            "KPI(不含产量口径): "
            f"A={metrics['availability']:.2f}% | "
            f"MTBF={metrics['mtbf']:.2f}min | MTTR={metrics['mttr']:.2f}min | "
            "OEE/P/Q=N/A"
        )


def calculate_daily_kpi(target_date=None, equip_id=KPI_EQUIP_ID, persist=True, verbose=True, as_of=None):
    if target_date is None:
        target_date = date.today()

    session = SessionLocal()
    if verbose:
        print(f"统计日期: {target_date}")
    try:
        as_of = as_of or datetime.now()
        stats, fault_count, events = _collect_equip_times(session, target_date, equip_id, as_of)
        total_qty, bad_qty, rows, warnings = _collect_qty(session, target_date, equip_id, as_of)

        if not events and not rows:
            if verbose:
                print(f"{equip_id}: 无状态事件和遥测数据。")
            return None

        metrics = _materialize_metrics(stats, total_qty, bad_qty, fault_count)
        if not events:
            warnings.append("missing_state_events")
        if not fault_count:
            warnings.append("no_faults_mtbf_mttr_undefined")
        if metrics["performance"] > 100:
            warnings.append("performance_exceeds_100")
        if total_qty == 0:
            warnings.append("no_observed_production_quality_undefined")
        if any(ev.end_time is None for ev in events):
            warnings.append("open_events_capped_at_query_time")
        telemetry_gaps = []
        observed_coverage_sec = 0.0
        for previous, current in zip(rows, rows[1:]):
            gap = max(0.0, (current.timestamp - previous.timestamp).total_seconds())
            if gap > 5:
                telemetry_gaps.append(gap)
            else:
                observed_coverage_sec += gap
        if telemetry_gaps:
            warnings.append("telemetry_gaps_detected")
        start, end = day_window(target_date, as_of)
        covered_end = start
        for ev in sorted(events, key=lambda item: (item.start_time, item.id)):
            clipped_start = max(start, ev.start_time)
            clipped_end = min(end, ev.end_time or end)
            if clipped_start < covered_end:
                warnings.append("overlapping_state_events")
            covered_end = max(covered_end, clipped_end)
        data_quality = "invalid" if any(w in warnings for w in (
            "overlapping_state_events", "invalid_counter_sample", "bad_count_exceeds_total"
        )) else "warning" if warnings else "ok"

        if persist:
            _upsert_daily_kpi(session, target_date, equip_id, metrics, total_qty, bad_qty)
            session.commit()

        if verbose:
            _print_breakdown(
                f"单设备KPI - {equip_id}",
                metrics,
                total_qty,
                bad_qty,
                fault_count,
                oee_applicable=equip_id in OEE_APPLICABLE_EQUIP_IDS,
            )

        return {
            "equip_id": equip_id,
            "stats": stats,
            "fault_count": fault_count,
            "total_qty": total_qty,
            "bad_qty": bad_qty,
            "metrics": metrics,
            "as_of": as_of.isoformat(),
            "warnings": sorted(set(warnings)),
            "data_quality": data_quality,
            "oee_applicable": equip_id in OEE_APPLICABLE_EQUIP_IDS,
            "evidence": {
                "event_count": len(events),
                "event_ids_preview": [ev.id for ev in events[:20]],
                "event_ids_truncated": len(events) > 20,
                "telemetry_count": len(rows),
                "first_telemetry_id": rows[0].id if rows else None,
                "last_telemetry_id": rows[-1].id if rows else None,
                "observed_start": rows[0].timestamp.isoformat() if rows else None,
                "observed_end": rows[-1].timestamp.isoformat() if rows else None,
                "observed_segment_count": len(telemetry_gaps) + 1 if rows else 0,
                "observed_coverage_sec": round(observed_coverage_sec, 3),
                "telemetry_gap_count": len(telemetry_gaps),
                "max_telemetry_gap_sec": round(max(telemetry_gaps), 3) if telemetry_gaps else 0,
                "coverage_note": "首末时间是边界，不代表连续观测；coverage 仅累计相邻采样间隔不超过 5 秒的区间。",
            },
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def calculate_line_kpi(target_date=None, persist=True, verbose=True, line_index=1):
    """与看板一致：加权设备可用率 × CNC 性能 × CNC 良品率。"""
    from kpi_service import _build_station_payload, _build_line_payload
    target_date = target_date or date.today()
    if line_index not in LINE_LAYOUT:
        raise ValueError("未知产线")
    layout = LINE_LAYOUT[line_index]
    as_of = datetime.now()
    with SessionLocal() as session:
        stations = [_build_station_payload(session, target_date, line_index, role, eid,
                                          persist_daily=False, as_of=as_of)
                    for role, eid in layout.items()]
        payload = _build_line_payload(line_index, stations)
        anchor = calculate_daily_kpi(target_date, layout["cnc"], persist=False,
                                     verbose=False, as_of=as_of)
        if anchor is None:
            return None
        metrics = dict(anchor["metrics"])
        metrics.update(oee=payload["metrics"]["oee"],
                       availability=payload["metrics"]["availability_weighted"])
        equip_id = f"LINE-{line_index:03d}"
        if persist:
            _upsert_daily_kpi(session, target_date, equip_id, metrics,
                              anchor["total_qty"], anchor["bad_qty"])
            session.commit()
        result = {**anchor, "equip_id": equip_id, "metrics": metrics,
                  "aggregation": "weighted_device_availability_cnc_pq",
                  "time_and_reliability_basis": "cnc_anchor", "dashboard": payload}
        if verbose:
            print(f"产线 {line_index} 加权汇总：{payload['metrics']}")
        return result


if __name__ == "__main__":
    # 默认将三条产线全部设备的当日 KPI 落库，避免只写入 1 号线。
    seen = set()
    for line in LINE_LAYOUT.values():
        for equip_id in (line["cnc"], line["robot"], line["plc"]):
            if equip_id in seen:
                continue
            seen.add(equip_id)
            calculate_daily_kpi(equip_id=equip_id, persist=True, verbose=True)
    calculate_line_kpi()
