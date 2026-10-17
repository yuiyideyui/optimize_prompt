import time
import urllib.error
from functools import wraps


def retry_on_network_error(max_retries=4, retry_delay=60):
    """
    网络请求重试装饰器

    当遇到网络错误时自动重试，等待指定时间后继续尝试

    Args:
        max_retries: 最大重试次数（默认4次）
        retry_delay: 重试间隔秒数（默认60秒）
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as e:
                    last_exception = e

                    # 如果是最后一次尝试，直接抛出异常
                    if attempt == max_retries - 1:
                        raise RuntimeError(
                            f"网络请求失败，已重试 {max_retries} 次: {str(e)}"
                        ) from e

                    # 打印重试信息
                    print(f"[网络错误] 第 {attempt + 1}/{max_retries} 次尝试失败: {str(e)}")
                    print(f"[重试] 等待 {retry_delay} 秒后进行第 {attempt + 2} 次尝试...")

                    # 等待后重试
                    time.sleep(retry_delay)

            # 理论上不会到达这里，但为了安全性
            if last_exception:
                raise last_exception

        return wrapper
    return decorator
