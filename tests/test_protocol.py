import base64
import unittest

from clipfile.protocol import (
    MAGIC,
    ProtocolError,
    build_part,
    crc32_hex,
    parse_part,
    part_size,
    total_parts,
)
from clipfile.util import format_part_ranges, safe_filename


SHA = "a" * 64


class ProtocolTest(unittest.TestCase):
    def test_first_part_roundtrip(self):
        data = b"hello clipfile" * 100
        text = build_part(
            1, 7, data, name="报告 final.zip", size=12345, sha256=SHA
        )
        self.assertTrue(text.startswith(MAGIC + "|v1|"))
        message = parse_part(text)
        self.assertIsNotNone(message)
        self.assertEqual(message.part, 1)
        self.assertEqual(message.total, 7)
        self.assertEqual(message.data, data)
        self.assertEqual(message.name, "报告 final.zip")
        self.assertEqual(message.size, 12345)
        self.assertEqual(message.sha256, SHA)
        self.assertEqual(message.crc, crc32_hex(data))

    def test_later_part_has_no_metadata(self):
        text = build_part(3, 5, b"payload")
        message = parse_part(text)
        self.assertFalse(message.has_metadata)
        self.assertIsNone(message.name)

    def test_non_protocol_is_ignored(self):
        self.assertIsNone(parse_part("普通剪贴板文字"))
        self.assertIsNone(parse_part(""))
        self.assertIsNone(parse_part(None))

    def test_corrupted_base64_rejected(self):
        text = build_part(2, 5, b"abcdef")
        header, body = text.split("\n")
        broken = header + "\n" + body[:4] + "!!!" + body[4:]
        with self.assertRaises(ProtocolError):
            parse_part(broken)

    def test_crc_mismatch_rejected(self):
        data = b"abcdef"
        text = build_part(2, 5, data)
        header = text.split("\n", 1)[0]
        tampered = header + "\n" + base64.b64encode(data + b"x").decode()
        with self.assertRaises(ProtocolError):
            parse_part(tampered)

    def test_bad_part_field_rejected(self):
        text = build_part(1, 1, b"x", name="a.txt", size=1, sha256=SHA)
        broken = text.replace("part=1/1", "part=0/1")
        with self.assertRaises(ProtocolError):
            parse_part(broken)

    def test_unsupported_version_rejected(self):
        text = build_part(1, 1, b"x", name="a.txt", size=1, sha256=SHA)
        with self.assertRaises(ProtocolError):
            parse_part(text.replace("|v1|", "|v9|"))

    def test_part_size_and_total(self):
        self.assertEqual(total_parts(0, 100), 1)
        self.assertEqual(total_parts(100, 100), 1)
        self.assertEqual(total_parts(101, 100), 2)
        self.assertEqual(part_size(101, 100, 1), 100)
        self.assertEqual(part_size(101, 100, 2), 1)


class UtilTest(unittest.TestCase):
    def test_format_part_ranges(self):
        self.assertEqual(format_part_ranges([1, 2, 3, 5, 7, 8]), "1-3, 5, 7-8")
        self.assertEqual(format_part_ranges([]), "")

    def test_safe_filename_blocks_traversal(self):
        self.assertEqual(safe_filename("../../etc/passwd"), "passwd")
        self.assertEqual(safe_filename("a/b\\c.txt"), "c.txt")
        self.assertEqual(safe_filename(""), "received_file")


if __name__ == "__main__":
    unittest.main()
