import base64
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from clipfile.receiver_core import (
    IntegrityError,
    ReceiverSession,
    TEMP_DIR_NAME,
)
from clipfile.sender_core import SenderSession
from clipfile.protocol import build_part


class TransferTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.src_dir = self.tmp / "src"
        self.out_dir = self.tmp / "out"
        self.src_dir.mkdir()
        self.out_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_file(self, name: str, data: bytes) -> Path:
        path = self.src_dir / name
        path.write_bytes(data)
        return path

    def _send_all(self, session: SenderSession, receiver: ReceiverSession):
        results = []
        for index in range(1, session.total + 1):
            results.append(receiver.accept(session.part_text(index)))
        return results

    # AC-01 小文件
    def test_small_text_file(self):
        content = ("clipfile 小文件测试\n" * 600).encode("utf-8")
        self.assertGreater(len(content), 10 * 1024)
        source = self._make_file("note.txt", content)
        sender = SenderSession(source, chunk_size=16 * 1024)
        receiver = ReceiverSession(self.out_dir, max_size=10 * 1024 * 1024)
        results = self._send_all(sender, receiver)
        self.assertTrue(all(r.code == "accepted" for r in results))
        self.assertTrue(results[-1].complete)
        target = receiver.finalize()
        self.assertEqual(target.read_bytes(), content)
        self.assertEqual(target.name, "note.txt")
        self.assertFalse((self.out_dir / TEMP_DIR_NAME).exists())

    # AC-02 大文件完整性
    def test_binary_roundtrip(self):
        content = os.urandom(3 * 1024 * 1024 + 123)
        source = self._make_file("blob.bin", content)
        sender = SenderSession(source, chunk_size=256 * 1024)
        receiver = ReceiverSession(self.out_dir, max_size=10 * 1024 * 1024)
        self._send_all(sender, receiver)
        target = receiver.finalize()
        self.assertEqual(target.stat().st_size, len(content))
        self.assertEqual(
            hashlib.sha256(target.read_bytes()).hexdigest(),
            hashlib.sha256(content).hexdigest(),
        )

    # AC-04 重复片幂等
    def test_duplicate_parts_ignored(self):
        content = os.urandom(200 * 1024)
        source = self._make_file("dup.bin", content)
        sender = SenderSession(source, chunk_size=32 * 1024)
        receiver = ReceiverSession(self.out_dir)
        first = sender.part_text(1)
        self.assertEqual(receiver.accept(first).code, "accepted")
        for _ in range(5):
            result = receiver.accept(first)
            self.assertEqual(result.code, "duplicate")
            self.assertEqual(result.received, 1)
        for index in range(2, sender.total + 1):
            receiver.accept(sender.part_text(index))
        self.assertEqual(receiver.finalize().read_bytes(), content)

    # AC-05 缺失片检测
    def test_missing_part_detection(self):
        content = os.urandom(300 * 1024)
        source = self._make_file("miss.bin", content)
        sender = SenderSession(source, chunk_size=32 * 1024)
        receiver = ReceiverSession(self.out_dir)
        self.assertGreaterEqual(sender.total, 5)
        for index in range(1, sender.total + 1):
            if index == 5:
                continue
            receiver.accept(sender.part_text(index))
        self.assertEqual(receiver.missing_parts(), [5])
        self.assertFalse(receiver.complete)
        with self.assertRaises(Exception):
            receiver.finalize()
        receiver.accept(sender.part_text(5))
        self.assertTrue(receiver.complete)
        self.assertEqual(receiver.finalize().read_bytes(), content)

    # AC-03 中断恢复
    def test_resume_after_restart(self):
        content = os.urandom(500 * 1024)
        source = self._make_file("resume.bin", content)
        sender = SenderSession(source, chunk_size=32 * 1024)
        receiver = ReceiverSession(self.out_dir)
        halfway = sender.total // 2
        for index in range(1, halfway + 1):
            receiver.accept(sender.part_text(index))

        # 模拟关闭工具后重新打开：从磁盘恢复会话。
        resumed = ReceiverSession(self.out_dir)
        self.assertTrue(resumed.has_metadata)
        self.assertEqual(resumed.name, "resume.bin")
        self.assertEqual(resumed.received_count, halfway)
        for index in range(halfway + 1, sender.total + 1):
            result = resumed.accept(sender.part_text(index))
            self.assertIn(result.code, ("accepted", "duplicate"))
        target = resumed.finalize()
        self.assertEqual(target.read_bytes(), content)

    # AC-06 剪贴板干扰
    def test_non_protocol_text_ignored(self):
        content = b"abc" * 10000
        source = self._make_file("noise.bin", content)
        sender = SenderSession(source, chunk_size=32 * 1024)
        receiver = ReceiverSession(self.out_dir)
        receiver.accept(sender.part_text(1))
        for noise in ("随便复制的文字", "===CLIPFILE=== 但不是协议", "{}"):
            result = receiver.accept(noise)
            self.assertEqual(result.code, "ignored")
            self.assertEqual(result.received, 1)

    def test_empty_file(self):
        source = self._make_file("empty.dat", b"")
        sender = SenderSession(source, chunk_size=1024)
        self.assertEqual(sender.total, 1)
        receiver = ReceiverSession(self.out_dir)
        receiver.accept(sender.part_text(1))
        self.assertTrue(receiver.complete)
        target = receiver.finalize()
        self.assertEqual(target.stat().st_size, 0)

    def test_corrupted_part_rejected(self):
        source = self._make_file("bad.bin", b"x" * 1000)
        sender = SenderSession(source, chunk_size=256)
        text = sender.part_text(1)
        header = text.split("\n", 1)[0]
        body = text.split("\n", 1)[1]
        tampered = header + "\n" + base64.b64encode(b"y" * 256).decode()
        self.assertNotEqual(body, tampered.split("\n")[1])
        receiver = ReceiverSession(self.out_dir)
        result = receiver.accept(tampered)
        self.assertEqual(result.code, "error")
        self.assertEqual(receiver.received_count, 0)

    def test_sha_mismatch_keeps_temp_parts(self):
        content = os.urandom(64 * 1024)
        source = self._make_file("hash.bin", content)
        sender = SenderSession(source, chunk_size=16 * 1024)
        receiver = ReceiverSession(self.out_dir)
        self._send_all(sender, receiver)
        receiver.sha256 = "0" * 64  # 模拟声明哈希不一致
        with self.assertRaises(IntegrityError):
            receiver.finalize()
        self.assertTrue((self.out_dir / TEMP_DIR_NAME).exists())
        self.assertFalse((self.out_dir / "hash.bin").exists())

    def test_metadata_conflict_rejected(self):
        first = self._make_file("a.bin", b"a" * 1000)
        second = self._make_file("b.bin", b"b" * 1000)
        sender_a = SenderSession(first, chunk_size=256)
        sender_b = SenderSession(second, chunk_size=256)
        receiver = ReceiverSession(self.out_dir)
        receiver.accept(sender_a.part_text(1))
        result = receiver.accept(sender_b.part_text(1))
        self.assertEqual(result.code, "error")
        self.assertIn("未完成会话", result.message)

    def test_oversize_file_rejected(self):
        source = self._make_file("big.bin", b"x" * 100)
        with self.assertRaises(ValueError):
            SenderSession(source, chunk_size=64, max_size=50)

        receiver = ReceiverSession(self.out_dir, max_size=50)
        text = build_part(1, 2, b"x", name="big.bin", size=100, sha256="a" * 64)
        result = receiver.accept(text)
        self.assertEqual(result.code, "error")
        self.assertIn("超过上限", result.message)

    def test_unicode_name_and_path_traversal(self):
        content = b"unicode"
        source = self._make_file("中文文件.txt", content)
        sender = SenderSession(source, chunk_size=64)
        receiver = ReceiverSession(self.out_dir)
        receiver.accept(sender.part_text(1))
        target = receiver.finalize()
        self.assertEqual(target.name, "中文文件.txt")
        self.assertEqual(target.read_bytes(), content)

        malicious = build_part(
            1,
            1,
            b"evil",
            name="../../evil.txt",
            size=4,
            sha256=hashlib.sha256(b"evil").hexdigest(),
        )
        other = self.out_dir / "other"
        other.mkdir()
        receiver2 = ReceiverSession(other)
        receiver2.accept(malicious)
        saved = receiver2.finalize()
        self.assertEqual(saved, other / "evil.txt")

    def test_duplicate_filename_gets_suffix(self):
        content = b"one"
        source = self._make_file("same.txt", content)
        sender = SenderSession(source, chunk_size=64)
        receiver = ReceiverSession(self.out_dir)
        receiver.accept(sender.part_text(1))
        first = receiver.finalize()
        self.assertEqual(first.name, "same.txt")

        receiver2 = ReceiverSession(self.out_dir)
        receiver2.accept(sender.part_text(1))
        second = receiver2.finalize()
        self.assertEqual(second.name, "same (1).txt")
        self.assertEqual(second.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
