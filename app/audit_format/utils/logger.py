import logging
import sys
from contextvars import ContextVar
from config import AGENT_NAME, LOG_LEVEL

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_ctx.get()
        return True


def set_request_id(request_id: str):
    rid = (request_id or "").strip() or "-"
    return _request_id_ctx.set(rid)


def reset_request_id(token):
    _request_id_ctx.reset(token)

def setup_logger(name: str = AGENT_NAME) -> logging.Logger:
    """
    配置标准化的日志记录器
    符合系统开发规范：标准化日志模块
    """
    logger = logging.getLogger(name)

    # 设置日志级别
    try:
        level = getattr(logging, LOG_LEVEL)
    except AttributeError:
        level = logging.INFO
    logger.setLevel(level)

    # 避免重复添加 Handler
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(request_id)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        handler.addFilter(_RequestIdFilter())
        logger.addHandler(handler)

    return logger
