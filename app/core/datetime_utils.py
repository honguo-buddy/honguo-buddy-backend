"""北京时间（UTC+8）处理工具

该模块提供统一的时间处理函数，确保所有时间戳都使用北京时间（UTC+8）
而不是 UTC 时间，以保证前后端显示一致。
"""
from datetime import datetime, timezone, timedelta, date as date_type
from typing import Optional


# 北京时区（UTC+8）
BEIJING_TZ = timezone(timedelta(hours=8))


def get_now() -> datetime:
    """获取当前北京时间（带时区信息）。
    
    返回值：
        datetime: 北京时间的 datetime 对象（带时区信息）
    
    示例：
        >>> now = get_now()
        >>> print(now)  # 2025-12-18 14:30:45.123456+08:00
    """
    return datetime.now(BEIJING_TZ)


def get_now_naive() -> datetime:
    """获取当前北京时间（不带时区信息）。
    
    此函数用于与不支持时区的 ORM 或数据库字段兼容。
    返回的 datetime 对象表示的是北京时间，但不包含时区信息。
    
    返回值：
        datetime: 北京时间的 datetime 对象（不带时区信息）
    
    示例：
        >>> now = get_now_naive()
        >>> print(now)  # 2025-12-18 14:30:45.123456
    """
    return get_now().replace(tzinfo=None)


def convert_to_beijing_time(dt: Optional[datetime]) -> Optional[datetime]:
    """将 UTC 时间转换为北京时间。
    
    参数：
        dt: 待转换的 datetime 对象（可能含有时区信息）
    
    返回值：
        datetime: 北京时间的 datetime 对象（不带时区信息），如果输入为 None 则返回 None
    
    示例：
        >>> utc_time = datetime.now(timezone.utc)
        >>> bj_time = convert_to_beijing_time(utc_time)
    """
    if not dt:
        return None
    
    # 如果已经是北京时间，直接返回（去掉时区信息）
    if dt.tzinfo == BEIJING_TZ:
        return dt.replace(tzinfo=None)
    
    # 如果没有时区信息，假设为 UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    
    # 转换到北京时区
    beijing_dt = dt.astimezone(BEIJING_TZ)
    
    # 返回无时区信息的版本
    return beijing_dt.replace(tzinfo=None)


def parse_datetime_to_beijing_naive(dt_str: str) -> datetime:
    """将输入字符串解析为北京时间（无时区信息）。

    支持格式（按顺序尝试）：
    - ISO 8601：  "2026-06-10T18:00:00" 或 "2026-06-10"
    - 空格分隔：  "2026-06-10 18:00:00" / "2026-06-10 18:00"
    - 斜杠分隔：  "2026/06/10 18:00:00" / "2026/06/10 18:00" / "2026/6/9 18:00"
    - 纯日期：    "2026-06-10" 或 "2026-6-9"（时间默认 00:00:00）
    - 纯时间：    "15:37" / "15:37:00" / "4:07"（日期默认今天）
    - 输入带时区：先按原时区解析，再转换为北京时间
    """
    dt_str = dt_str.strip()
    today = get_today()

    # 日期时间格式（空格分隔，带秒/不带秒）
    datetime_formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ]
    for fmt in datetime_formats:
        try:
            parsed_dt = datetime.strptime(dt_str, fmt)
            return parsed_dt
        except ValueError:
            continue

    # ISO 8601 原生解析（含时区处理）
    try:
        parsed_dt = datetime.fromisoformat(dt_str)
        if parsed_dt.tzinfo is None:
            return parsed_dt
        return parsed_dt.astimezone(BEIJING_TZ).replace(tzinfo=None)
    except ValueError:
        pass

    # 纯日期（YYYY-MM-DD 或 YYYY-M-D）
    try:
        parsed_dt = datetime.strptime(dt_str, "%Y-%m-%d")
        return parsed_dt
    except ValueError:
        pass
    # 纯日期 ISO 回退（含 2026-06-10 标准格式）
    try:
        parsed_date = date_type.fromisoformat(dt_str)
        return datetime.combine(parsed_date, datetime.min.time())
    except ValueError:
        pass

    # 纯时间（HH:MM:SS / HH:MM / H:MM / H:M）
    time_formats = ["%H:%M:%S", "%H:%M"]
    for fmt in time_formats:
        try:
            parsed_time = datetime.strptime(dt_str, fmt).time()
            return datetime.combine(today, parsed_time)
        except ValueError:
            continue

    raise ValueError(f"无法解析时间字符串: {dt_str!r}，支持格式: ISO datetime / 日期(YYYY-MM-DD) / 时间(HH:MM 或 HH:MM:SS)")


def utc_to_beijing(utc_dt: Optional[datetime]) -> Optional[datetime]:
    """将 UTC 时间转换为北京时间（无时区信息）。
    
    这是 convert_to_beijing_time 的别名，提供简洁的 API。
    
    参数：
        utc_dt: UTC datetime 对象
    
    返回值：
        datetime: 北京时间的 datetime 对象（不带时区信息）
    """
    return convert_to_beijing_time(utc_dt)


def get_today() -> date_type:
    """获取当前北京时间的日期（无时区信息）。
    
    返回值：
        date: 北京时间的日期对象
    
    示例：
        >>> today = get_today()
        >>> print(today)  # 2025-12-26
    """
    return get_now_naive().date()


def beijing_now_for_model():
    """用于 SQLAlchemy Model 的默认值函数。
    
    返回当前北京时间（不带时区信息），专门用于 Model 的 default 参数。
    
    注意：在 SQLAlchemy Column 的 default 参数中使用时，应传递函数引用
    而不是函数调用结果，即：default=beijing_now_for_model（不带括号）
    
    返回值：
        datetime: 北京时间的 datetime 对象（不带时区信息）
    
    示例：
        >>> from sqlalchemy import Column, DateTime
        >>> create_time = Column(DateTime, default=beijing_now_for_model, comment="创建时间")
    """
    return get_now_naive()
