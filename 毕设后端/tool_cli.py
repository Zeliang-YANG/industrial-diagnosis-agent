"""直接验证诊断工具，不调用 LLM，不写入数据库。"""
import argparse
from datetime import date
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tool', choices=['query_kpi', 'query_device_state', 'query_fault_events',
                                         'query_telemetry_summary', 'query_line_timeline',
                                         'query_line_kpi', 'search_knowledge'])
    parser.add_argument('--equip-id', default='BJ-CNC-001')
    parser.add_argument('--date', default=str(date.today()))
    parser.add_argument('--compare-date')
    parser.add_argument('--limit', type=int, default=50)
    parser.add_argument('--after-id', type=int, default=0)
    parser.add_argument('--line-id', type=int, choices=[1, 2, 3], default=1)
    parser.add_argument('--start-time', help='遥测窗口开始时间，HH:MM 或 HH:MM:SS')
    parser.add_argument('--end-time', help='遥测窗口结束时间，HH:MM 或 HH:MM:SS')
    parser.add_argument('--query', default='CNC 的 OEE 下降时应该按什么顺序排查？')
    parser.add_argument('--top-k', type=int, default=3)
    parser.add_argument('--database', choices=['yzl_agent_demo', 'yzl'], default='yzl_agent_demo')
    args = parser.parse_args()
    import db_models
    from sqlalchemy import create_engine
    engine = create_engine(db_models.engine.url.set(database=args.database), pool_pre_ping=True)
    db_models.SessionLocal.configure(bind=engine)
    from agent_tools import dispatch_tool
    if args.tool == 'search_knowledge':
        parameters = {'query': args.query, 'top_k': args.top_k}
    elif args.tool.startswith('query_line_'):
        parameters = {'line_id': args.line_id, 'date': args.date}
    else:
        parameters = {'equip_id': args.equip_id, 'date': args.date}
    if args.tool in ('query_kpi', 'query_line_kpi'):
        if args.compare_date:
            parameters['compare_date'] = args.compare_date
    elif args.tool in ('query_device_state', 'query_fault_events'):
        if args.compare_date:
            parser.error('--compare-date 仅用于 query_kpi')
        parameters.update(limit=args.limit, after_id=args.after_id)
    elif args.tool == 'query_line_timeline':
        parameters.update(limit=args.limit, after_id=args.after_id)
    elif args.tool == 'query_telemetry_summary':
        if args.start_time:
            parameters['start_time'] = args.start_time
        if args.end_time:
            parameters['end_time'] = args.end_time
    try:
        result = dispatch_tool(args.tool, parameters)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 1 if result['status'] == 'error' else 0
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
