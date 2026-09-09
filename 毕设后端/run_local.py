"""一键启动本机 OPC UA、采集器、MySQL 演示库和 HTTP API。"""
import argparse
import asyncio
import logging
import signal
import threading


async def run(seconds, port):
    import db_models
    from sqlalchemy import create_engine, text
    if db_models.engine.dialect.name != "mysql":
        raise RuntimeError("本地入口需要原项目的 MySQL 连接配置")
    # 复用连接凭据，模拟数据独立保存，不改写原毕设数据库。
    with db_models.engine.begin() as connection:
        connection.execute(text("CREATE DATABASE IF NOT EXISTS yzl_agent_demo CHARACTER SET utf8mb4"))
    url = db_models.engine.url.set(database="yzl_agent_demo")
    db_models.engine.dispose()
    db_models.engine = create_engine(url, pool_pre_ping=True)
    db_models.SessionLocal.configure(bind=db_models.engine)
    from db_models import init_db, ensure_query_indexes, engine, SessionLocal, RawTelemetry
    from event_lifecycle import reconcile_stale_open_events
    from opcua_server import build_server
    from cnc_logic import DigitalTwinCNC
    from app import app
    from werkzeug.serving import make_server

    init_db()
    ensure_query_indexes()
    with SessionLocal() as session:
        repaired = reconcile_stale_open_events(session)
        if repaired:
            print(f"已修复 {len(repaired)} 条异常退出遗留的开放状态事件。", flush=True)
    server, _ = await build_server()
    # 服务端重启后承接最近累计产量，避免当天计数归零导致负产量。
    from config import LINE_LAYOUT, LINE_NODE_CONFIG
    from asyncua import ua
    with SessionLocal() as session:
        for line_id, layout in LINE_LAYOUT.items():
            latest = session.query(RawTelemetry).filter_by(equip_id=layout["cnc"]).order_by(RawTelemetry.id.desc()).first()
            if latest:
                for name, value in (("PartCount", latest.part_count), ("BadPartCount", latest.bad_count)):
                    node = server.get_node(LINE_NODE_CONFIG[line_id]["cnc"][name])
                    await node.write_value(ua.Variant(value or 0, ua.VariantType.Int32))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    async with server:
        twin = DigitalTwinCNC()
        http = None
        worker = None
        monitor = None
        waiter = None
        try:
            await twin.connect()
            http = make_server("127.0.0.1", port, app, threaded=True)
            worker = threading.Thread(target=http.serve_forever, daemon=True)
            worker.start()
            print("模拟数据库：yzl_agent_demo（MySQL，数据持续保留）", flush=True)
            print(f"API：http://127.0.0.1:{port}/api/workshop/kpi", flush=True)
            print("按 Ctrl+C 停止全部后端服务并关闭当前状态事件。", flush=True)
            monitor = asyncio.create_task(twin.start_monitoring())
            waiter = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait(
                [monitor, waiter], timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
            if monitor in done:
                await monitor
        finally:
            for task in (monitor, waiter):
                if task is not None:
                    task.cancel()
            await asyncio.gather(*(t for t in (monitor, waiter) if t is not None),
                                 return_exceptions=True)
            if http is not None:
                await asyncio.to_thread(http.shutdown)
                http.server_close()
                worker.join()
            await twin.shutdown()
            engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, help="运行指定秒数后自动停止")
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()
    if args.seconds is not None and args.seconds <= 0:
        parser.error("--seconds 必须大于 0")
    logging.basicConfig(level=logging.ERROR)
    asyncio.run(run(args.seconds, args.port))
