# main.py
import asyncio
import sys
from cnc_logic import DigitalTwinCNC

async def start():
    twin = DigitalTwinCNC()
    try:
        await twin.connect()
        await twin.start_monitoring()
    finally:
        await twin.shutdown()

if __name__ == "__main__":
    # Windows 下的异步兼容性处理
    if sys.platform.lower().startswith('win'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(start())
    except KeyboardInterrupt:
        print("\n👋 系统已安全退出。")