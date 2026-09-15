"""脱敏的 Agent 调用审计日志；不记录问题、回答、工具参数或工具原始结果。"""
import json
import logging
import os
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


_LOCK = threading.Lock()
_LOGGERS = {}


def audit_path():
    configured = os.environ.get("AGENT_AUDIT_LOG", "").strip()
    return Path(configured) if configured else Path(__file__).with_name("logs") / "agent_audit.jsonl"


def _logger_for(path):
    resolved = str(path.resolve())
    with _LOCK:
        if resolved in _LOGGERS:
            return _LOGGERS[resolved]
        path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("agent_audit." + str(len(_LOGGERS)))
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        _LOGGERS[resolved] = logger
        return logger


def record_agent_result(result):
    """写入最小审计元数据；日志失败不会改变 Agent 对用户的响应。"""
    try:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "request_id": result.get("request_id"),
            "status": result.get("status"),
            "code": result.get("code"),
            "model": result.get("model"),
            "model_requests": result.get("model_requests", 0),
            "tool_names": [item.get("name") for item in result.get("trace", [])],
            "latency_ms": result.get("latency_ms"),
            "usage": result.get("usage", {}),
            "grounding_status": result.get("grounding", {}).get("status"),
        }
        _logger_for(audit_path()).info(json.dumps(entry, ensure_ascii=False, allow_nan=False))
        return True
    except Exception:
        return False
