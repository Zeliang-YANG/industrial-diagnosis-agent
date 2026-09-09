from sqlalchemy import create_engine, Column, Integer, Float, String, DateTime, Date, BigInteger, Index
from sqlalchemy.orm import declarative_base, sessionmaker
import datetime
import os

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(_BASE_DIR, ".env")


def _load_dotenv():
    """从项目目录下的 .env 加载键值（不覆盖已有环境变量）。无需安装 python-dotenv。"""
    if not os.path.isfile(_ENV_PATH):
        return
    with open(_ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = val


_load_dotenv()


def _database_url():
    """
    优先使用环境变量 DATABASE_URL（完整连接串）。
    否则用 MYSQL_USER / MYSQL_PASSWORD / MYSQL_HOST / MYSQL_PORT / MYSQL_DATABASE 拼接。
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    user = os.environ.get("MYSQL_USER", "root")
    password = os.environ.get("MYSQL_PASSWORD", "")
    host = os.environ.get("MYSQL_HOST", "127.0.0.1")
    port = os.environ.get("MYSQL_PORT", "3306")
    database = os.environ.get("MYSQL_DATABASE", "yzl")
    if password:
        return f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}"
    return f"mysql+pymysql://{user}@{host}:{port}/{database}"


DB_URL = _database_url()

engine = create_engine(DB_URL, echo=False)
Base = declarative_base()

# ==========================================
# 表 1: 实时遥测数据表 (用于记录每一秒的波形)
# ==========================================
class RawTelemetry(Base):
    __tablename__ = 'raw_telemetry'

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.datetime.now)
    equip_id = Column(String(20), default="BJ-CNC-001")

    spindle_speed = Column(Float)   # 主轴转速
    spindle_load = Column(Float)    # 主轴负载
    temperature = Column(Float)     # 电机温度
    part_count = Column(Integer)    # 产量
    bad_count = Column(Integer)     # 次品量
    thesis_state = Column(String(20)) # 论文定义的当前状态(srun, ssby等)

# ==========================================
# 表 2: 设备状态事件表 (用于计算 OEE 和 MTBF)
# ==========================================
class StatusEventLog(Base):
    __tablename__ = 'status_event_log'

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    equip_id = Column(String(20), default="BJ-CNC-001")
    state_code = Column(String(10)) # srun, ssby, su_down, sp_down
    start_time = Column(DateTime)   # 状态开始时间
    end_time = Column(DateTime, nullable=True) # 状态结束时间
    duration_sec = Column(Integer, default=0)  # 持续时长(秒)
    alarm_code = Column(Integer, default=0)    # 故障码

# ==========================================
# 表 3: [新增] 每日/班次 KPI 聚合表
# 用于第5章看板展示，存储计算好的高价值指标
# ==========================================
class DailyKPIReport(Base):
    __tablename__ = 'daily_kpi_report'

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, default=datetime.date.today) # 统计日期
    equip_id = Column(String(20), default="BJ-CNC-001")

    # 时间统计 (秒)
    total_time = Column(Integer)     # 总日历时间 (T_total)
    run_time = Column(Integer)       # 实际运行时间 (T_op = srun + ssby)
    down_time = Column(Integer)      # 故障停机时间 (T_down = su_down)
    val_time = Column(Integer)       # 价值加工时间 (T_val = srun)

    # 产量统计
    total_count = Column(Integer)    # 总产量
    good_count = Column(Integer)     # 合格品产量

    # 核心 KPI (百分比)
    oee = Column(Float)              # 全局设备效率
    availability = Column(Float)     # 时间开动率
    performance = Column(Float)      # 性能开动率
    quality = Column(Float)          # 良品率

    # 可靠性指标
    mtbf = Column(Float)             # 平均故障间隔 (Mean Time Between Failures)
    mttr = Column(Float)             # 平均修复时间 (Mean Time To Repair)


# Agent 查询的主要访问路径；日报按设备和日期保持唯一，避免并发 upsert 产生重复行。
Index("ix_raw_telemetry_equip_timestamp", RawTelemetry.equip_id, RawTelemetry.timestamp)
Index("ix_status_event_equip_start_end", StatusEventLog.equip_id,
      StatusEventLog.start_time, StatusEventLog.end_time)
Index("ux_daily_kpi_date_equip", DailyKPIReport.date, DailyKPIReport.equip_id, unique=True)


def ensure_query_indexes():
    """为已经存在的演示表补建索引；若日报存在重复数据则唯一索引会拒绝启动。"""
    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(bind=engine, checkfirst=True)

# ==========================================
# 初始化建表指令
# ==========================================
def init_db():
    print("⏳ 正在连接数据库并创建缺失的数据表...")
    # Base.metadata.create_all 会自动识别新定义的类并创建缺失的表
    Base.metadata.create_all(engine)
    print("✅ 数据表 'daily_kpi_report' 已就绪！")

# 创建用于后续插入数据的 Session 工厂
SessionLocal = sessionmaker(bind=engine)

if __name__ == "__main__":
    init_db()
