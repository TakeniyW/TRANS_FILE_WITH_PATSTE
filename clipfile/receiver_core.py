"""接收端核心：分片落盘、缺片检测、断点续传与合并校验。

接收端把每个分片解码后单独写入 ``.clipfile_temp/parts``，因此内存占用
只与单片大小有关；全部收齐后再流式合并并校验整文件 SHA-256。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .protocol import (
    MAX_FILE_SIZE,
    ProtocolError,
    crc32_hex,
    parse_part,
)
from .util import human_size, safe_filename

TEMP_DIR_NAME = ".clipfile_temp"
PARTS_DIR_NAME = "parts"
SESSION_FILE_NAME = "session.json"
SESSION_VERSION = 1
_MERGE_BUFFER = 1024 * 1024
_HEX8_RE = re.compile(r"^[0-9a-f]{8}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class ReceiverError(RuntimeError):
    """接收端一般性错误。"""


class IncompleteError(ReceiverError):
    def __init__(self, missing: List[int]):
        super().__init__(
            "缺少分片：" + ", ".join(str(x) for x in missing[:20])
        )
        self.missing = missing


class IntegrityError(ReceiverError):
    """校验失败。"""


@dataclass
class AcceptResult:
    """一次接收操作的结果。"""

    code: str  # accepted / duplicate / ignored / error
    message: str
    part: Optional[int] = None
    received: int = 0
    total: Optional[int] = None
    bytes_received: int = 0
    complete: bool = False


@dataclass
class _PartInfo:
    crc: str
    size: int

    def as_dict(self) -> Dict[str, object]:
        return {"crc": self.crc, "size": self.size}


class ReceiverSession:
    """绑定到某个保存目录的接收会话（支持从磁盘恢复）。"""

    def __init__(
        self,
        save_dir: os.PathLike,
        max_size: int = MAX_FILE_SIZE,
    ) -> None:
        self.save_dir = Path(save_dir).expanduser()
        self.max_size = int(max_size)
        self.temp_dir = self.save_dir / TEMP_DIR_NAME
        self.parts_dir = self.temp_dir / PARTS_DIR_NAME
        self.session_file = self.temp_dir / SESSION_FILE_NAME

        self.name: Optional[str] = None
        self.size: Optional[int] = None
        self.sha256: Optional[str] = None
        self.total: Optional[int] = None
        self.parts: Dict[str, _PartInfo] = {}

        self._load()

    # ------------------------------------------------------------------ 状态
    @property
    def has_metadata(self) -> bool:
        return self.name is not None and self.total is not None

    @property
    def received_count(self) -> int:
        return len(self.parts)

    @property
    def bytes_received(self) -> int:
        return sum(info.size for info in self.parts.values())

    @property
    def complete(self) -> bool:
        return (
            self.has_metadata
            and self.total is not None
            and len(self.parts) == self.total
        )

    def missing_parts(self) -> List[int]:
        if not self.total:
            return []
        return [i for i in range(1, self.total + 1) if str(i) not in self.parts]

    # ---------------------------------------------------------------- 接收片
    def accept(self, text: object) -> AcceptResult:
        """接收一段剪贴板文本。非协议文本返回 ``ignored``。"""
        try:
            message = parse_part(text)
        except ProtocolError as exc:
            return self._result("error", f"协议解析失败：{exc}")

        if message is None:
            return self._result("ignored", "剪贴板内容不是本工具协议文本，已忽略")

        if not self.has_metadata:
            if not message.has_metadata:
                return self._result(
                    "error", "尚未收到首片（含文件信息的第 1 片），请先在发送端发送第 1 片"
                )
            if message.size is None or message.size > self.max_size:
                return self._result(
                    "error",
                    f"文件大小超过上限 {human_size(self.max_size)}",
                )
            try:
                self._init_session(
                    message.name or "",
                    message.size,
                    message.sha256 or "",
                    message.total,
                )
            except OSError as exc:
                return self._result("error", f"创建临时目录失败：{exc}")
        else:
            if message.total != self.total:
                return self._result(
                    "error",
                    f"总片数不一致（会话 {self.total}，收到 {message.total}）",
                )
            if message.has_metadata:
                if (
                    message.name != self.name
                    or message.size != self.size
                    or message.sha256 != self.sha256
                ):
                    return self._result(
                        "error",
                        "该目录存在另一文件的未完成会话，请更换保存目录或清理 "
                        f"{TEMP_DIR_NAME} 后重试",
                    )

        key = str(message.part)
        part_path = self._part_path(message.part)
        existing = self.parts.get(key)

        if (
            existing is not None
            and part_path.exists()
            and part_path.stat().st_size == existing.size
            and existing.crc == message.crc
            and existing.size == len(message.data)
        ):
            return self._result(
                "duplicate",
                f"第 {message.part} 片已存在，已忽略重复片",
                part=message.part,
            )

        try:
            self.parts_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = part_path.with_name(part_path.name + ".tmp")
            with open(tmp_path, "wb") as handle:
                handle.write(message.data)
            os.replace(tmp_path, part_path)
        except OSError as exc:
            return self._result("error", f"写入分片失败：{exc}", part=message.part)

        self.parts[key] = _PartInfo(crc=message.crc, size=len(message.data))
        self._save()

        if existing is not None:
            message_text = f"第 {message.part} 片校验不一致，已用新数据替换"
        else:
            message_text = f"已接收第 {message.part}/{self.total} 片"

        return self._result(
            "accepted", message_text, part=message.part
        )

    def _result(
        self,
        code: str,
        message: str,
        part: Optional[int] = None,
    ) -> AcceptResult:
        received = self.received_count
        total = self.total
        complete = self.complete
        if code == "accepted" and complete:
            message += "，全部分片已收齐"
        if code == "duplicate" and complete:
            message += "，全部分片已收齐"
        return AcceptResult(
            code=code,
            message=message,
            part=part,
            received=received,
            total=total,
            bytes_received=self.bytes_received,
            complete=complete,
        )

    def _init_session(
        self, name: str, size: int, sha256: str, total: int
    ) -> None:
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.parts_dir.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.size = size
        self.sha256 = sha256
        self.total = total
        self.parts = {}
        self._save()

    def _part_path(self, index: int) -> Path:
        return self.parts_dir / f"{index:06d}.bin"

    # ---------------------------------------------------------------- 合并保存
    def finalize(self) -> Path:
        """按序合并所有分片、校验并保存到目标目录，返回最终路径。"""
        if not self.has_metadata:
            raise ReceiverError("尚未收到任何分片")
        missing = self.missing_parts()
        if missing:
            raise IncompleteError(missing)

        assembled = self.temp_dir / "assembled.tmp"
        digest = hashlib.sha256()
        written = 0
        try:
            with open(assembled, "wb") as out:
                for index in range(1, int(self.total) + 1):
                    info = self.parts[str(index)]
                    part_path = self._part_path(index)
                    if not part_path.is_file():
                        raise IntegrityError(
                            f"第 {index} 片临时文件丢失，请重发该片"
                        )
                    with open(part_path, "rb") as handle:
                        data = handle.read()
                    if len(data) != info.size or crc32_hex(data) != info.crc:
                        raise IntegrityError(
                            f"第 {index} 片临时数据损坏，请重发该片"
                        )
                    out.write(data)
                    digest.update(data)
                    written += len(data)

            if self.size is not None and written != self.size:
                raise IntegrityError(
                    f"合并后大小 {written} 与原文件大小 {self.size} 不一致"
                )
            if digest.hexdigest() != self.sha256:
                raise IntegrityError("整文件 SHA-256 校验失败")

            self.save_dir.mkdir(parents=True, exist_ok=True)
            target = _unique_path(
                self.save_dir / safe_filename(self.name or "received_file")
            )
            os.replace(assembled, target)
        finally:
            # 合并失败时保留分片以便续传，但清理未完成的合并文件。
            if assembled.exists():
                try:
                    assembled.unlink()
                except OSError:
                    pass

        shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.name = None
        self.size = None
        self.sha256 = None
        self.total = None
        self.parts = {}
        return target

    # ---------------------------------------------------------------- 持久化
    def _save(self) -> None:
        if not self.has_metadata:
            return
        payload = {
            "version": SESSION_VERSION,
            "name": self.name,
            "size": self.size,
            "sha256": self.sha256,
            "total": self.total,
            "parts": {k: v.as_dict() for k, v in self.parts.items()},
        }
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.session_file.with_name(self.session_file.name + ".tmp")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.session_file)
        except OSError:
            pass

    def _load(self) -> None:
        if not self.session_file.exists():
            return
        try:
            data = json.loads(self.session_file.read_text(encoding="utf-8"))
            if data.get("version") != SESSION_VERSION:
                raise ValueError("会话版本不匹配")
            name = data["name"]
            size = int(data["size"])
            sha256 = str(data["sha256"]).lower()
            total = int(data["total"])
            if (
                not isinstance(name, str)
                or not name
                or size < 0
                or size > self.max_size
                or total < 1
                or not _HEX64_RE.match(sha256)
            ):
                raise ValueError("会话字段非法")
        except (OSError, ValueError, KeyError, TypeError):
            bad = self.session_file.with_name("session.json.bad")
            try:
                os.replace(self.session_file, bad)
            except OSError:
                pass
            return

        loaded: Dict[str, _PartInfo] = {}
        raw_parts = data.get("parts") or {}
        if isinstance(raw_parts, dict):
            for key, value in raw_parts.items():
                try:
                    index = int(key)
                    crc = str(value["crc"]).lower()
                    part_bytes = int(value["size"])
                except (ValueError, KeyError, TypeError):
                    continue
                if not (1 <= index <= total) or not _HEX8_RE.match(crc):
                    continue
                path = self._part_path(index)
                try:
                    if not path.is_file() or path.stat().st_size != part_bytes:
                        continue
                except OSError:
                    continue
                loaded[str(index)] = _PartInfo(crc=crc, size=part_bytes)

        # 断点续传：即使分片都不存在，也保留会话元信息与总片数。
        self.name = name
        self.size = size
        self.sha256 = sha256
        self.total = total
        self.parts = loaded


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(1, 10000):
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    raise ReceiverError("目标目录中同名文件过多，无法保存")


__all__ = [
    "AcceptResult",
    "IncompleteError",
    "IntegrityError",
    "ReceiverError",
    "ReceiverSession",
    "TEMP_DIR_NAME",
]
