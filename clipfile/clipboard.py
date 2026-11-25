"""剪贴板适配层：GUI 使用 tkinter 的原生纯文本剪贴板。"""

from __future__ import annotations

import tkinter as tk
from typing import Optional


class TkClipboard:
    """基于 tkinter 的剪贴板实现（Windows 上即 CF_UNICODETEXT）。"""

    def __init__(self, root: tk.Misc) -> None:
        self.root = root

    def copy(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        # 让剪贴板内容尽快生效；不调用 update() 以免重入事件循环。
        self.root.update_idletasks()

    def paste(self) -> str:
        try:
            value = self.root.clipboard_get()
        except tk.TclError:
            # 剪贴板为空或内容不是文本。
            return ""
        return value if isinstance(value, str) else str(value)


class MemoryClipboard:
    """内存剪贴板，用于测试与无头环境。"""

    def __init__(self) -> None:
        self.text: str = ""

    def copy(self, text: str) -> None:
        self.text = text

    def paste(self) -> Optional[str]:
        return self.text
