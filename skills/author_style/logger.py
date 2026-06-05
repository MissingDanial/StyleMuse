"""
通用作家风格仿写 Skill - 日志模块
"""

import logging
import sys
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


def get_logger(name: str) -> logging.Logger:
    """
    获取一个命名 logger，同时输出到控制台和文件。

    Args:
        name: logger 名称（通常用 __name__）

    Returns:
        Logger 实例
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # 控制台 handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)-7s %(name)s - %(message)s",
        datefmt="%H:%M:%S",
    ))

    # 文件 handler
    file_handler = logging.FileHandler(
        LOG_DIR / "stylemuse.log", encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    logger.addHandler(console)
    logger.addHandler(file_handler)

    return logger
