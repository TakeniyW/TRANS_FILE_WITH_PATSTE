"""接收端图形界面（在本地桌面运行）。"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

from .clipboard import TkClipboard
from .receiver_core import (
    IncompleteError,
    IntegrityError,
    ReceiverError,
    ReceiverSession,
)
from .util import format_part_ranges, human_size, timestamp


def _default_save_dir() -> Path:
    downloads = Path.home() / "Downloads"
    return downloads if downloads.exists() else Path.home()


class ReceiverApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.clipboard = TkClipboard(root)
        self.session = ReceiverSession(_default_save_dir())
        self.listening = False
        self.poll_job: Optional[str] = None
        self.last_text = ""
        self.merging = False

        root.title("ClipFile 接收端 · 本地还原文件")
        root.geometry("820x680")
        root.minsize(720, 600)

        self.dir_var = tk.StringVar(value=str(self.session.save_dir))
        self.auto_merge_var = tk.BooleanVar(value=True)
        self.info_name = tk.StringVar(value="等待接收首片…")
        self.info_size = tk.StringVar(value="-")
        self.info_total = tk.StringVar(value="-")
        self.info_received = tk.StringVar(value="0 / -")
        self.progress_var = tk.StringVar(value="0.0%")
        self.status_var = tk.StringVar(value="就绪：选择保存目录后点击“开始监听”")

        self._build_ui()
        self._refresh()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.session.has_metadata:
            self._log(
                "检测到未完成会话："
                f"{self.session.name}，已接收 {self.session.received_count}/"
                f"{self.session.total} 片"
            )
            self.root.after(200, self._maybe_auto_finalize)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)

        ttk.Label(outer, text="保存目录").grid(row=0, column=0, sticky="w")
        ttk.Entry(
            outer, textvariable=self.dir_var, state="readonly"
        ).grid(row=0, column=1, sticky="ew", padx=(8, 8), pady=4)
        ttk.Button(outer, text="选择目录", command=self._choose_dir).grid(
            row=0, column=2, sticky="ew"
        )

        info = ttk.LabelFrame(outer, text="接收信息", padding=10)
        info.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 8))
        info.columnconfigure(1, weight=1)
        rows = [
            ("文件名", self.info_name),
            ("原始大小", self.info_size),
            ("总片数", self.info_total),
            ("已接收", self.info_received),
        ]
        for index, (label, variable) in enumerate(rows):
            ttk.Label(info, text=label).grid(
                row=index, column=0, sticky="nw", padx=(0, 10), pady=2
            )
            ttk.Label(
                info, textvariable=variable, wraplength=620, justify="left"
            ).grid(row=index, column=1, sticky="w", pady=2)

        progress_row = ttk.Frame(outer)
        progress_row.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(4, 8))
        progress_row.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(
            progress_row, maximum=100, value=0
        )
        self.progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            progress_row, textvariable=self.progress_var, width=8, anchor="e"
        ).grid(row=0, column=1, padx=(8, 0))

        self.recv_var = tk.StringVar(value="已接收 0 / - 片 · 0 B")
        ttk.Label(outer, textvariable=self.recv_var, font=("", 11, "bold")).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )

        buttons = ttk.Frame(outer)
        buttons.grid(row=4, column=0, columnspan=3, sticky="ew")
        for index in range(3):
            buttons.columnconfigure(index, weight=1)
        self.listen_btn = ttk.Button(
            buttons, text="开始监听", command=self._toggle_listen
        )
        self.listen_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6), ipady=6)
        self.recv_btn = ttk.Button(
            buttons, text="接收当前片", command=self._receive_manual
        )
        self.recv_btn.grid(row=0, column=1, sticky="ew", padx=6, ipady=6)
        self.check_btn = ttk.Button(
            buttons, text="合并保存", command=self._manual_finalize
        )
        self.check_btn.grid(row=0, column=2, sticky="ew", padx=(6, 0), ipady=6)

        ttk.Checkbutton(
            outer,
            text="全部分片收齐后自动合并、校验并保存",
            variable=self.auto_merge_var,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 6))

        log_frame = ttk.LabelFrame(outer, text="日志", padding=6)
        log_frame.grid(row=6, column=0, columnspan=3, sticky="nsew")
        outer.rowconfigure(6, weight=1)
        self.log_text = tk.Text(log_frame, height=9, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.log_text.yview
        )
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        ttk.Label(
            outer, textvariable=self.status_var, foreground="#555555"
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 0))

    # -------------------------------------------------------------- 事件处理
    def _choose_dir(self) -> None:
        if self.merging:
            messagebox.showinfo("提示", "正在合并文件，请稍候")
            return
        path = filedialog.askdirectory(
            title="选择接收文件的保存目录",
            initialdir=str(self.session.save_dir),
        )
        if path:
            self._set_dir(Path(path))

    def _set_dir(self, path: Path) -> None:
        self._stop_listen()
        self.session = ReceiverSession(path)
        self.dir_var.set(str(path))
        self.last_text = ""
        self._refresh()
        if self.session.has_metadata:
            self._log(
                f"切换到 {path}，检测到未完成会话：{self.session.name}，"
                f"已接收 {self.session.received_count}/{self.session.total} 片"
            )
            self._maybe_auto_finalize()
        else:
            self._log(f"已设置保存目录：{path}")

    def _toggle_listen(self) -> None:
        if self.listening:
            self._stop_listen()
            self._log("已停止剪贴板监听")
            self.status_var.set("已停止监听")
        else:
            self.listening = True
            self.last_text = ""
            self.listen_btn.configure(text="停止监听")
            self._log("已开始监听剪贴板（每秒检查一次）")
            self.status_var.set("正在监听剪贴板…")
            self._poll_once()
            self.poll_job = self.root.after(1000, self._poll_tick)

    def _poll_tick(self) -> None:
        self.poll_job = None
        if not self.listening:
            return
        self._poll_once()
        if self.listening:
            self.poll_job = self.root.after(1000, self._poll_tick)

    def _poll_once(self) -> None:
        text = self.clipboard.paste()
        if not text or text == self.last_text:
            return
        self.last_text = text
        self._accept_text(text)

    def _receive_manual(self) -> None:
        text = self.clipboard.paste()
        if not text:
            messagebox.showinfo("提示", "剪贴板为空或内容不是文本")
            return
        self.last_text = text
        self._accept_text(text)

    def _accept_text(self, text: str) -> None:
        if self.merging:
            return
        result = self.session.accept(text)
        if result.code == "ignored":
            self.status_var.set("剪贴板内容不是本工具协议文本，已忽略")
            return
        if result.code == "error":
            self._log(f"接收失败：{result.message}")
            self.status_var.set(result.message)
            return

        self._log(result.message)
        self._refresh()
        missing = self.session.missing_parts()
        suffix = f"，缺少 {len(missing)} 片" if missing else ""
        self.status_var.set(
            f"已接收 {result.received}/{result.total} 片"
            f"（{human_size(result.bytes_received)}）{suffix}"
        )
        if result.complete:
            self._maybe_auto_finalize()

    def _manual_finalize(self) -> None:
        if not self.session.has_metadata:
            messagebox.showinfo("提示", "尚未收到任何分片")
            return
        self._start_finalize()

    def _maybe_auto_finalize(self) -> None:
        if (
            self.session.complete
            and self.auto_merge_var.get()
            and not self.merging
        ):
            self._start_finalize()

    def _start_finalize(self) -> None:
        if self.merging:
            return
        if not self.session.complete:
            missing = self.session.missing_parts()
            messagebox.showwarning(
                "尚未收齐",
                "缺少分片：" + (format_part_ranges(missing) or "-"),
            )
            return
        self.merging = True
        self._stop_listen()
        self._set_buttons("disabled")
        self.status_var.set("正在合并分片并校验 SHA-256，请稍候…")
        self._log("全部分片已收齐，开始合并与校验")
        threading.Thread(target=self._finalize_worker, daemon=True).start()

    def _finalize_worker(self) -> None:
        try:
            target = self.session.finalize()
        except IncompleteError as exc:
            self.root.after(0, self._finalize_failed, exc, True)
        except (IntegrityError, ReceiverError, OSError) as exc:
            self.root.after(0, self._finalize_failed, exc, False)
        except Exception as exc:  # noqa: BLE001
            self.root.after(0, self._finalize_failed, exc, False)
        else:
            self.root.after(0, self._finalize_done, target)

    def _finalize_done(self, target: Path) -> None:
        self.merging = False
        self._set_buttons("normal")
        self._refresh()
        # 避免重新监听时把剪贴板里残留的旧分片再次解析。
        self.last_text = self.clipboard.paste()
        self._log(f"传输完成，SHA-256 校验通过，已保存到：{target}")
        self.status_var.set(f"传输完成：{target.name}")
        messagebox.showinfo(
            "传输完成",
            f"SHA-256 校验通过，文件已保存：\n{target}",
        )

    def _finalize_failed(self, exc: Exception, incomplete: bool) -> None:
        self.merging = False
        self._set_buttons("normal")
        self._refresh()
        self._log(f"合并失败：{exc}")
        self.status_var.set("合并失败，已保留临时分片")
        if incomplete:
            missing = self.session.missing_parts()
            messagebox.showerror(
                "缺少分片",
                "无法合并，缺少分片：" + format_part_ranges(missing),
            )
        else:
            messagebox.showerror(
                "校验失败",
                f"{exc}\n\n临时分片已保留，可在发送端补发有问题的分片后重试。",
            )

    def _set_buttons(self, state: str) -> None:
        for button in (self.listen_btn, self.recv_btn, self.check_btn):
            button.configure(state=state)

    def _refresh(self) -> None:
        session = self.session
        total = session.total
        received = session.received_count

        if session.has_metadata:
            self.info_name.set(session.name or "-")
            self.info_size.set(human_size(session.size or 0))
            self.info_total.set(f"{total} 片")
        else:
            self.info_name.set("等待接收首片…")
            self.info_size.set("-")
            self.info_total.set("-")
        self.info_received.set(f"{received} / {total if total else '-'}")

        percent = (received / total * 100.0) if total else 0.0
        self.progress.configure(maximum=100, value=percent)
        self.progress_var.set(f"{percent:.1f}%")
        self.recv_var.set(
            f"已接收 {received} / {total if total else '-'} 片 · "
            f"{human_size(session.bytes_received)}"
        )

    def _stop_listen(self) -> None:
        self.listening = False
        if self.poll_job is not None:
            try:
                self.root.after_cancel(self.poll_job)
            except tk.TclError:
                pass
            self.poll_job = None
        self.listen_btn.configure(text="开始监听")

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{timestamp()}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_close(self) -> None:
        self._stop_listen()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use(
            "vista"
            if root.tk.call("tk", "windowingsystem") == "win32"
            else "clam"
        )
    except tk.TclError:
        pass
    ReceiverApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
