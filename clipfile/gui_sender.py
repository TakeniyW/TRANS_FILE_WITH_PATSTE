"""发送端图形界面（在云桌面内运行）。"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from .clipboard import TkClipboard
from .protocol import DEFAULT_CHUNK_SIZE, part_size
from .sender_core import (
    SenderSession,
    load_sender_progress,
    save_sender_progress,
)
from .util import human_size, timestamp

CHUNK_OPTIONS = [
    ("128 KB", 128 * 1024),
    ("256 KB", 256 * 1024),
    ("512 KB", 512 * 1024),
    ("1 MB", 1024 * 1024),
]
CHUNK_MAP = dict(CHUNK_OPTIONS)
DEFAULT_LABEL = "512 KB"


class SenderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.clipboard = TkClipboard(root)
        self.session: Optional[SenderSession] = None
        self.auto_job: Optional[str] = None

        root.title("ClipFile 发送端 · 云桌面文件外传")
        root.geometry("820x660")
        root.minsize(720, 580)

        self.file_var = tk.StringVar()
        self.chunk_var = tk.StringVar(value=DEFAULT_LABEL)
        self.auto_var = tk.BooleanVar(value=False)
        self.interval_var = tk.IntVar(value=3)
        self.info_name = tk.StringVar(value="未选择文件")
        self.info_size = tk.StringVar(value="-")
        self.info_hash = tk.StringVar(value="-")
        self.info_parts = tk.StringVar(value="-")
        self.part_var = tk.StringVar(value="第 - / - 片")
        self.status_var = tk.StringVar(value="就绪：请选择要传输的文件")

        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)

        ttk.Label(outer, text="文件").grid(row=0, column=0, sticky="w")
        entry = ttk.Entry(outer, textvariable=self.file_var, state="readonly")
        entry.grid(row=0, column=1, sticky="ew", padx=(8, 8), pady=4)
        ttk.Button(outer, text="选择文件", command=self._choose_file).grid(
            row=0, column=2, sticky="ew"
        )

        ttk.Label(outer, text="分片大小").grid(row=1, column=0, sticky="w")
        chunk_box = ttk.Combobox(
            outer,
            textvariable=self.chunk_var,
            values=[label for label, _ in CHUNK_OPTIONS],
            state="readonly",
            width=12,
        )
        chunk_box.grid(row=1, column=1, sticky="w", padx=(8, 8), pady=4)
        chunk_box.bind("<<ComboboxSelected>>", self._on_chunk_changed)

        info = ttk.LabelFrame(outer, text="文件信息", padding=10)
        info.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 8))
        info.columnconfigure(1, weight=1)

        rows = [
            ("文件名", self.info_name),
            ("原始大小", self.info_size),
            ("SHA-256", self.info_hash),
            ("总片数", self.info_parts),
        ]
        for index, (label, variable) in enumerate(rows):
            ttk.Label(info, text=label).grid(
                row=index, column=0, sticky="nw", padx=(0, 10), pady=2
            )
            ttk.Label(
                info, textvariable=variable, wraplength=620, justify="left"
            ).grid(row=index, column=1, sticky="w", pady=2)

        ttk.Label(outer, textvariable=self.part_var, font=("", 12, "bold")).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(4, 4)
        )
        self.progress = ttk.Progressbar(outer, maximum=1, value=0)
        self.progress.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        buttons = ttk.Frame(outer)
        buttons.grid(row=5, column=0, columnspan=3, sticky="ew")
        for index in range(3):
            buttons.columnconfigure(index, weight=1)
        self.prev_btn = ttk.Button(
            buttons, text="上一片", command=self._go_prev, state="disabled"
        )
        self.prev_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6), ipady=6)
        self.send_btn = ttk.Button(
            buttons,
            text="发送当前片到剪贴板",
            command=self._send_manual,
            state="disabled",
        )
        self.send_btn.grid(row=0, column=1, sticky="ew", padx=6, ipady=6)
        self.next_btn = ttk.Button(
            buttons, text="下一片", command=self._go_next, state="disabled"
        )
        self.next_btn.grid(row=0, column=2, sticky="ew", padx=(6, 0), ipady=6)

        auto_row = ttk.Frame(outer)
        auto_row.grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 6))
        ttk.Checkbutton(
            auto_row,
            text="自动连续发送（每片写入剪贴板后自动跳到下一片）",
            variable=self.auto_var,
            command=self._toggle_auto,
        ).pack(side="left")
        ttk.Label(auto_row, text="间隔").pack(side="left", padx=(14, 4))
        ttk.Spinbox(
            auto_row,
            from_=1,
            to=60,
            width=4,
            textvariable=self.interval_var,
        ).pack(side="left")
        ttk.Label(auto_row, text="秒").pack(side="left", padx=(4, 0))

        log_frame = ttk.LabelFrame(outer, text="日志", padding=6)
        log_frame.grid(row=7, column=0, columnspan=3, sticky="nsew")
        outer.rowconfigure(7, weight=1)
        self.log_text = tk.Text(log_frame, height=8, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        ttk.Label(
            outer, textvariable=self.status_var, foreground="#555555"
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(8, 0))

    # -------------------------------------------------------------- 事件处理
    def _choose_file(self) -> None:
        path = filedialog.askopenfilename(title="选择要传输的文件")
        if path:
            self._load_file(Path(path))

    def _load_file(self, path: Path) -> None:
        self._stop_auto()
        chunk = CHUNK_MAP.get(self.chunk_var.get(), DEFAULT_CHUNK_SIZE)
        self.status_var.set("正在计算文件 SHA-256，请稍候…")
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            session = SenderSession(path, chunk_size=chunk)
        except Exception as exc:  # noqa: BLE001 - 直接展示给用户
            messagebox.showerror("无法读取文件", str(exc))
            self.status_var.set("读取文件失败")
            return
        finally:
            self.root.config(cursor="")
            self.root.update_idletasks()

        self.session = session
        resumed = load_sender_progress(
            session.path,
            chunk_size=session.chunk_size,
            size=session.size,
            mtime_ns=session.mtime_ns,
        )
        if resumed and 1 <= resumed <= session.total:
            session.current = resumed

        self.file_var.set(str(session.path))
        self.info_name.set(session.path.name)
        self.info_size.set(human_size(session.size))
        self.info_hash.set(session.sha256)
        self.info_parts.set(f"{session.total} 片 × {human_size(session.chunk_size)}")
        for button in (self.prev_btn, self.send_btn, self.next_btn):
            button.configure(state="normal")
        self._refresh_position()
        self._log(
            f"已加载 {session.path.name}，共 {session.total} 片"
            + (f"，已恢复进度到第 {session.current} 片" if resumed else "")
        )
        self.status_var.set("文件就绪，点击“发送当前片到剪贴板”开始")

    def _on_chunk_changed(self, _event: object = None) -> None:
        if self.session is not None:
            self._load_file(self.session.path)

    def _go_prev(self) -> None:
        if self.session and self.session.go_prev():
            self._after_nav()

    def _go_next(self) -> None:
        if self.session and self.session.go_next():
            self._after_nav()

    def _after_nav(self) -> None:
        assert self.session is not None
        save_sender_progress(self.session)
        self._refresh_position()

    def _send_manual(self) -> None:
        self._send_current(advance=False)

    def _send_current(self, advance: bool) -> bool:
        session = self.session
        if session is None:
            messagebox.showinfo("提示", "请先选择要发送的文件")
            return False
        index = session.current
        try:
            text = session.part_text()
            self.clipboard.copy(text)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("发送失败", str(exc))
            self.status_var.set("发送失败")
            self._log(f"第 {index} 片发送失败：{exc}")
            return False

        size = part_size(session.size, session.chunk_size, index)
        self._log(
            f"已复制第 {index}/{session.total} 片到剪贴板"
            f"（{human_size(size)}，文本 {human_size(len(text))}）"
        )
        self.status_var.set(f"第 {index} 片已写入剪贴板，请在本地接收端接收")

        if advance:
            if session.current < session.total:
                session.current += 1
                save_sender_progress(session)
                self._refresh_position()
                if self.auto_var.get():
                    try:
                        delay = max(1, int(self.interval_var.get()))
                    except (ValueError, tk.TclError):
                        delay = 3
                    self.auto_job = self.root.after(
                        delay * 1000, self._auto_tick
                    )
            else:
                save_sender_progress(session)
                self._stop_auto()
                self._log("全部分片已发送完毕，请在接收端确认校验结果")
                self.status_var.set("全部分片已发送完毕")
        else:
            save_sender_progress(session)
        return True

    def _refresh_position(self) -> None:
        session = self.session
        if session is None:
            return
        index = session.current
        self.part_var.set(
            f"第 {index} / {session.total} 片 · 本片 {human_size(session.current_part_size())}"
        )
        self.progress.configure(maximum=session.total, value=index)
        self.prev_btn.configure(
            state="normal" if index > 1 else "disabled"
        )
        self.next_btn.configure(
            state="normal" if index < session.total else "disabled"
        )

    def _toggle_auto(self) -> None:
        if self.auto_var.get():
            if self.session is None:
                self.auto_var.set(False)
                messagebox.showinfo("提示", "请先选择要发送的文件")
                return
            self._log("已开启自动连续发送")
            self._auto_tick()
        else:
            self._stop_auto()
            self._log("已停止自动发送")

    def _auto_tick(self) -> None:
        self.auto_job = None
        if not self.auto_var.get() or self.session is None:
            return
        self._send_current(advance=True)

    def _stop_auto(self) -> None:
        if self.auto_job is not None:
            try:
                self.root.after_cancel(self.auto_job)
            except tk.TclError:
                pass
            self.auto_job = None
        if self.auto_var.get():
            self.auto_var.set(False)

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp()}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_close(self) -> None:
        self._stop_auto()
        if self.session is not None:
            save_sender_progress(self.session)
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if root.tk.call("tk", "windowingsystem") == "win32" else "clam")
    except tk.TclError:
        pass
    SenderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
