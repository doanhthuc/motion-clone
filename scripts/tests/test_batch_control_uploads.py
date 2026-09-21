import io
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import uploads

BIG = 10 ** 13          # "plenty of disk" for tests


class UploadsBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "uploads"
        self.dest = self.root.parent / "tg-staging" / "app"
        p = mock.patch.object(uploads, "CHUNK_SIZE", 4)       # tiny chunks: 10 bytes = 3 chunks
        p.start(); self.addCleanup(p.stop)

    def open(self, name="clip.mp4", size=10):
        return uploads.open_upload(self.root, name, size, free_bytes=BIG)

    def put(self, uid, n, data):
        uploads.write_chunk(self.root, uid, n, io.BytesIO(data), len(data))


class TestOpen(UploadsBase):
    def test_returns_id_and_chunk_count(self):
        u = self.open()
        self.assertEqual((u["chunk_size"], u["chunks_total"]), (4, 3))
        self.assertTrue((self.root / u["upload_id"] / "meta.json").is_file())

    def test_rejects_bad_input(self):
        for name, size, code in (("", 10, "bad_request"), ("a.mp4", 0, "bad_request"),
                                 ("a.mp4", uploads.MAX_UPLOAD_BYTES + 1, "too_large")):
            with self.assertRaises(uploads.UploadError) as cm:
                uploads.open_upload(self.root, name, size, free_bytes=BIG)
            self.assertEqual(cm.exception.code, code)

    def test_refuses_without_disk_headroom(self):
        with self.assertRaises(uploads.UploadError) as cm:
            uploads.open_upload(self.root, "a.mp4", 10, free_bytes=uploads.DISK_HEADROOM + 19)
        self.assertEqual(cm.exception.code, "no_space")


class TestChunks(UploadsBase):
    def test_wrong_length_or_index_is_bad_request(self):
        uid = self.open()["upload_id"]
        for n, data in ((0, b"abc"), (2, b"ab12"), (3, b"xx"), (-1, b"abcd")):
            with self.assertRaises(uploads.UploadError, msg=(n, data)) as cm:
                self.put(uid, n, data)
            self.assertEqual(cm.exception.code, "bad_request")

    def test_short_stream_leaves_no_part(self):
        uid = self.open()["upload_id"]
        with self.assertRaises(uploads.UploadError):
            uploads.write_chunk(self.root, uid, 0, io.BytesIO(b"ab"), 4)
        self.assertEqual(uploads.upload_status(self.root, uid)["received"], [])
        self.assertEqual(list((self.root / uid).glob("*.tmp")), [])

    def test_resume_and_overwrite(self):
        uid = self.open()["upload_id"]
        self.put(uid, 2, b"90")
        self.put(uid, 0, b"XXXX")
        self.put(uid, 0, b"0123")              # re-sent chunk overwrites
        self.assertEqual(uploads.upload_status(self.root, uid)["received"], [0, 2])

    def test_unknown_or_hostile_id(self):
        for uid in ("nope", "../x", ""):
            with self.assertRaises(uploads.UploadError) as cm:
                uploads.upload_status(self.root, uid)
            self.assertEqual(cm.exception.code, "not_found")


class TestAssemble(UploadsBase):
    def test_incomplete_is_refused(self):
        uid = self.open()["upload_id"]
        self.put(uid, 0, b"0123")
        with self.assertRaises(uploads.UploadError) as cm:
            uploads.assemble(self.root, uid, self.dest)
        self.assertEqual(cm.exception.code, "incomplete")

    def test_byte_identical_and_cleaned_up(self):
        uid = self.open("Áo dài.mp4")["upload_id"]
        for n, data in ((1, b"4567"), (0, b"0123"), (2, b"89")):
            self.put(uid, n, data)
        staged = uploads.assemble(self.root, uid, self.dest)
        self.assertEqual(staged, self.dest / "Ao_dai.mp4")
        self.assertEqual(staged.read_bytes(), b"0123456789")
        self.assertFalse((self.root / uid).exists())


class TestPrune(UploadsBase):
    def test_removes_only_stale_uploads(self):
        old, new = self.open()["upload_id"], self.open()["upload_id"]
        now = time.time()
        meta = self.root / old / "meta.json"
        m = json.loads(meta.read_text()); m["created_at"] = now - 25 * 3600
        meta.write_text(json.dumps(m))
        self.assertEqual(uploads.prune_uploads(self.root, 24 * 3600, now), [old])
        self.assertTrue((self.root / new).exists())


if __name__ == "__main__":
    unittest.main()
