import logging
import os
from dotenv import load_dotenv
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

load_dotenv()
LOG_DIR = os.getenv("LOG_DIR", ".logs")


def setup_logger(module_name: str, log_dir: str | None = LOG_DIR) -> logging.Logger:
    """配置日志系统"""
    os.makedirs(log_dir, exist_ok=True)
    handler = TimedRotatingFileHandler(
        filename=os.path.join(log_dir, f"{datetime.now().strftime('%Y-%m-%d')}.log"),
        when="midnight",
        backupCount=7,
        encoding="utf-8",
    )
    formatter = logging.Formatter(
        "%(asctime)s - <%(name)s> - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)

    logger = logging.getLogger(module_name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    return logger
