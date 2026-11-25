"""通用小工具。"""

from __future__ import annotations

import datetime
import re
from typing import Iterable, List


def human_size(num_bytes: int) -> str:
    """把字节数格式化为人类可读字符串。"""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def format_part_ranges(parts: Iterable[int]) -> str:
    """把缺失片号压缩成 ``1-3, 5, 7-9`` 形式。"""
    ordered = sorted(set(parts))
    if not ordered:
        return ""
    ranges: List[str] = []
    start = prev = ordered[0]
    for value in ordered[1:]:
        if value == prev + 1:
            prev = value
            continue
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = value
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(ranges)


def timestamp() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, fallback: str = "received_file") -> str:
    """去掉路径成分和本地文件系统非法字符，防止目录穿越。"""
    base = name.replace("\\", "/").split("/")[-1].strip()
    base = _INVALID_FILENAME_RE.sub("_", base)
    base = base.strip(" .")
    return base or fallback
