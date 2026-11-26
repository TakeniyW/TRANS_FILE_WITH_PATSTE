"""分片协议：构造与解析剪贴板文本。

协议格式（v1）::

    ===CLIPFILE===|v1|name=report.zip|size=104857600|sha256=<64位hex>|part=1/205|crc=9f8e7d6c|
    <Base64 数据>

- 首片携带文件名、原始大小、整文件 SHA-256；后续片仅携带 part / crc。
- 文件名使用 UTF-8 百分号编码（避免中文、空格、特殊字符破坏 ASCII 协议）。
- crc32 针对本片原始二进制数据计算，输出 8 位小写十六进制。
- 只认以 ``===CLIPFILE===`` 开头的文本，其他剪贴板内容一律视为无关。
"""

from __future__ import annotations

import base64
import binascii
import math
import os
import re
import zlib
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, unquote

MAGIC = "===CLIPFILE==="
VERSION = "v1"
HEADER_NEWLINE = "\n"

DEFAULT_CHUNK_SIZE = 512 * 1024  # 512 KB
MIN_CHUNK_SIZE = 16 * 1024
MAX_CHUNK_SIZE = 4 * 1024 * 1024


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# 单文件大小上限，默认 1.5 GB，可用环境变量覆盖。
MAX_FILE_SIZE = _env_int("CLIPFILE_MAX_FILE_SIZE", 1536 * 1024 * 1024)

_HEX8_RE = re.compile(r"^[0-9a-f]{8}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class ProtocolError(ValueError):
    """剪贴板文本符合协议前缀但内容非法。"""


@dataclass(frozen=True)
class PartMessage:
    """解析后的一片数据。"""

    part: int
    total: int
    crc: str
    data: bytes
    name: Optional[str] = None
    size: Optional[int] = None
    sha256: Optional[str] = None

    @property
    def has_metadata(self) -> bool:
        return self.name is not None


def crc32_hex(data: bytes) -> str:
    """计算数据的 CRC32，返回 8 位小写十六进制。"""
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"


def total_parts(file_size: int, chunk_size: int) -> int:
    """按分片大小计算总片数；空文件也算 1 片。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if file_size < 0:
        raise ValueError("file_size 不能为负数")
    return max(1, math.ceil(file_size / chunk_size))


def part_size(file_size: int, chunk_size: int, part: int) -> int:
    """返回第 part 片（从 1 开始）包含的原始字节数。"""
    if part < 1:
        raise ValueError("part 从 1 开始")
    offset = (part - 1) * chunk_size
    return max(0, min(chunk_size, file_size - offset))


def build_part(
    part: int,
    total: int,
    data: bytes,
    *,
    name: Optional[str] = None,
    size: Optional[int] = None,
    sha256: Optional[str] = None,
) -> str:
    """构造一片可直接写入剪贴板的文本。"""
    if total < 1 or not (1 <= part <= total):
        raise ValueError("片序号超出范围")
    if name is not None:
        if size is None or sha256 is None:
            raise ValueError("首片必须同时提供 name / size / sha256")
        if not _HEX64_RE.match(sha256.lower()):
            raise ValueError("sha256 必须是 64 位十六进制")
    elif size is not None or sha256 is not None:
        raise ValueError("提供 size / sha256 时必须同时提供 name")

    fields = [MAGIC, VERSION]
    if name is not None:
        fields.append("name=" + quote(name, safe=""))
        fields.append(f"size={int(size)}")
        fields.append("sha256=" + sha256.lower())
    fields.append(f"part={part}/{total}")
    fields.append("crc=" + crc32_hex(data))

    header = "|".join(fields) + "|"
    body = base64.b64encode(data).decode("ascii")
    return header + HEADER_NEWLINE + body


def parse_part(text: object) -> Optional[PartMessage]:
    """解析剪贴板文本。

    返回 ``None`` 表示这不是协议文本（应当忽略）；格式非法则抛
    :class:`ProtocolError`。
    """
    if not isinstance(text, str):
        return None

    cleaned = text.lstrip("\ufeff\r\n\t ")
    # 只有 MAGIC 后紧跟字段分隔符才视为协议文本；否则当作普通剪贴板内容忽略。
    if not cleaned.startswith(MAGIC + "|"):
        return None

    newline = cleaned.find(HEADER_NEWLINE)
    if newline < 0:
        raise ProtocolError("缺少 Base64 数据部分")

    header = cleaned[:newline].strip()
    body = "".join(cleaned[newline + 1 :].split())
    tokens = header.split("|")
    if len(tokens) < 4 or tokens[0] != MAGIC:
        raise ProtocolError("协议头格式错误")
    if tokens[1] != VERSION:
        raise ProtocolError(f"不支持的协议版本：{tokens[1]}")

    kv = {}
    for token in tokens[2:]:
        if not token:
            continue
        if "=" not in token:
            raise ProtocolError(f"协议字段格式错误：{token}")
        key, value = token.split("=", 1)
        kv[key] = value

    part, total = _parse_part_field(kv.get("part"))
    crc = (kv.get("crc") or "").lower()
    if not _HEX8_RE.match(crc):
        raise ProtocolError("crc 字段非法")

    name = size = sha256 = None
    has_any_meta = any(k in kv for k in ("name", "size", "sha256"))
    if has_any_meta:
        if not all(k in kv for k in ("name", "size", "sha256")):
            raise ProtocolError("首片元信息不完整")
        name = unquote(kv["name"])
        if not name:
            raise ProtocolError("文件名为空")
        try:
            size = int(kv["size"])
        except ValueError as exc:
            raise ProtocolError("size 字段非法") from exc
        if size < 0:
            raise ProtocolError("size 不能为负数")
        sha256 = kv["sha256"].lower()
        if not _HEX64_RE.match(sha256):
            raise ProtocolError("sha256 字段非法")

    try:
        data = base64.b64decode(body.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ProtocolError("Base64 数据损坏") from exc

    if crc32_hex(data) != crc:
        raise ProtocolError("本片 CRC32 校验失败")

    return PartMessage(
        part=part,
        total=total,
        crc=crc,
        data=data,
        name=name,
        size=size,
        sha256=sha256,
    )


def _parse_part_field(value: Optional[str]) -> tuple:
    if not value or "/" not in value:
        raise ProtocolError("part 字段非法")
    left, right = value.split("/", 1)
    try:
        part = int(left)
        total = int(right)
    except ValueError as exc:
        raise ProtocolError("part 字段非法") from exc
    if total < 1 or part < 1 or part > total:
        raise ProtocolError("片序号超出范围")
    return part, total
