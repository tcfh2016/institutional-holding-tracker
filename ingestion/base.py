"""
数据采集基类：提供通用重试、日志、延迟功能
"""
import time
import logging
from functools import wraps
from typing import Callable, Any

from config.settings import REQUEST_RETRIES, REQUEST_TIMEOUT, REQUEST_DELAY

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def retry_on_error(max_retries: int = REQUEST_RETRIES, delay: float = REQUEST_DELAY):
    """装饰器：失败时自动重试"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries:
                        logging.error(
                            f"[Retry] {func.__name__} failed after {max_retries} attempts: {e}",
                            exc_info=True,
                        )
                        raise
                    logging.warning(
                        f"[Retry] {func.__name__} attempt {attempt}/{max_retries} failed: {e}; "
                        f"retrying in {delay * attempt:.1f}s..."
                    )
                    time.sleep(delay * attempt)
            return None
        return wrapper
    return decorator


def safe_request(func: Callable, *args, **kwargs) -> Any:
    """安全调用 akshare 接口，带延迟"""
    start = time.perf_counter()
    logging.info(f"[Request] {func.__name__} started")
    try:
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        shape = getattr(result, "shape", None)
        logging.info(f"[Request] {func.__name__} completed in {elapsed:.2f}s, shape={shape}")
        time.sleep(REQUEST_DELAY)
        return result
    except Exception as e:
        elapsed = time.perf_counter() - start
        logging.error(
            f"[Request] {func.__name__} failed after {elapsed:.2f}s: {e}",
            exc_info=True,
        )
        raise
