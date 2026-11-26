"""发送端核心：文件哈希、分片读取与协议文本生成。

发送端只读文件，不做任何网络访问；分片按需 ``seek + read``，不会把整
个文件读入内存。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from .protocol import (
    DEFAULT_CHUNK_SIZE,
    MAX_FILE_SIZE,
    build_part,
    part_size,
    total_parts,
)

_HASH_BUFFER = 1024 * 1024


def _state_dir() -> Path:
    root = os.environ.get("CLIPFILE_HOME")
    base = Path(root) if root else Path.home() / ".clipfile"
    return base / "sessions"


def _state_file(path: Path) -> Path:
    key = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    return _state_dir() / f"{key}.json"


class SenderSession:
    """一次发送会话。

    构造时以流式方式计算整文件 SHA-256，因此 1.5 GB 文件也不会占用大量
    内存，仅耗时一次全量读取。
    """

    def __init__(
        self,
        path: os.PathLike,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        max_size: int = MAX_FILE_SIZE,
    ) -> None:
        file_path = Path(path).expanduser()
        if not file_path.is_file():
            raise ValueError(f"文件不存在或不是普通文件：{file_path}")
        if chunk_size <= 0:
            raise ValueError("分片大小必须大于 0")

        size = file_path.stat().st_size
        if size > max_size:
            raise ValueError(
                f"文件大小 {size} 字节超过上限 {max_size} 字节"
            )

        self.path = file_path
        self.chunk_size = int(chunk_size)
        self.max_size = int(max_size)
        self.size = size
        self.mtime_ns = file_path.stat().st_mtime_ns
        self.sha256 = _hash_file(file_path)
        self.total = total_parts(size, self.chunk_size)
        self.current = 1

    @property
    def file_id(self) -> str:
        """文件名 + 大小 + SHA-256 前 8 位的可读标识。"""
        return f"{self.path.name}-{self.size}-{self.sha256[:8]}"

    def part_bytes(self, index: int) -> bytes:
        if not (1 <= index <= self.total):
            raise ValueError("片序号超出范围")
        offset = (index - 1) * self.chunk_size
        length = part_size(self.size, self.chunk_size, index)
        with open(self.path, "rb") as handle:
            handle.seek(offset)
            return handle.read(length)

    def part_text(self, index: Optional[int] = None) -> str:
        """生成第 index 片的协议文本；默认使用当前片。"""
        target = self.current if index is None else index
        data = self.part_bytes(target)
        if target == 1:
            return build_part(
                target,
                self.total,
                data,
                name=self.path.name,
                size=self.size,
                sha256=self.sha256,
            )
        return build_part(target, self.total, data)

    def current_part_size(self) -> int:
        return part_size(self.size, self.chunk_size, self.current)

    def go_next(self) -> bool:
        if self.current < self.total:
            self.current += 1
            return True
        return False

    def go_prev(self) -> bool:
        if self.current > 1:
            self.current -= 1
            return True
        return False


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_HASH_BUFFER)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def load_sender_progress(
    path: os.PathLike,
    *,
    chunk_size: int,
    size: int,
    mtime_ns: int,
) -> Optional[int]:
    """读取历史进度；文件未变化时返回上次发送到的片号。"""
    state_file = _state_file(Path(path))
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        data.get("size") != size
        or data.get("mtime_ns") != mtime_ns
        or data.get("chunk_size") != chunk_size
    ):
        return None
    current = data.get("current")
    if not isinstance(current, int) or current < 1:
        return None
    return current


def save_sender_progress(session: SenderSession) -> None:
    """把当前发送位置写入用户目录（失败不影响传输）。"""
    payload = {
        "path": str(session.path.resolve()),
        "size": session.size,
        "mtime_ns": session.mtime_ns,
        "chunk_size": session.chunk_size,
        "sha256": session.sha256,
        "total": session.total,
        "current": session.current,
    }
    state_file = _state_file(session.path)
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_file.with_name(state_file.name + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, state_file)
    except OSError:
        pass
