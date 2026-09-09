"""DeepSeek 诊断入口；--check 只检查配置，不发送模型请求。"""
import argparse
import json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('question', nargs='?')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--database', choices=['yzl_agent_demo', 'yzl'], default='yzl_agent_demo')
    args = parser.parse_args()
    from deepseek_agent import configuration, run_agent, AgentError
    if args.check:
        try:
            key, base, model = configuration()
            print(json.dumps({'api_key_configured': bool(key and not key.startswith('your_')),
                              'base_url': base, 'model': model, 'network_request_sent': False}, ensure_ascii=False, indent=2))
            return 0
        except AgentError as exc:
            print(str(exc))
            return 1
    if not args.question:
        parser.error('请输入诊断问题，或使用 --check 检查配置')
    import db_models
    from sqlalchemy import create_engine
    engine = create_engine(db_models.engine.url.set(database=args.database), pool_pre_ping=True)
    db_models.SessionLocal.configure(bind=engine)
    try:
        result = run_agent(args.question)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if result['status'] in ('ok', 'needs_clarification', 'insufficient_evidence') else 1
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
