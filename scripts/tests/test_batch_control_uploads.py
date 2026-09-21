import errno
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

    def test_write_chunk_after_directory_removed(self):
        uid = self.open()["upload_id"]
        upload_dir = self.root / uid
        # Simulate assemble removing the directory while write_chunk is called
        import shutil as shutil_module
        shutil_module.rmtree(upload_dir)
        # write_chunk should raise not_found, not a raw FileNotFoundError
        with self.assertRaises(uploads.UploadError) as cm:
            self.put(uid, 0, b"0123")
        self.assertEqual(cm.exception.code, "not_found")
        # No stray tmp files should be left
        self.assertEqual(list(self.root.glob("*/*.tmp")), [])

    def test_write_chunk_disk_full_during_stream(self):
        uid = self.open()["upload_id"]
        # Create a stream that raises ENOSPC during read
        class EOSPCStream:
            def __init__(self):
                self.calls = 0
            def read(self, size):
                self.calls += 1
                if self.calls == 2:
                    # Raise ENOSPC on the second read call
                    raise OSError(errno.ENOSPC, "No space left on device")
                # First read returns partial data to force a second read
                return b"01" if self.calls == 1 else b""

        stream = EOSPCStream()
        # Use a larger chunk to force multiple read calls
        with self.assertRaises(uploads.UploadError) as cm:
            uploads.write_chunk(self.root, uid, 1, stream, 4)
        self.assertEqual(cm.exception.code, "no_space")
        # No stray tmp files should be left
        self.assertEqual(list(self.root.glob(f"{uid}/*.tmp")), [])
        # Upload should still exist for retry
        self.assertTrue((self.root / uid / "meta.json").is_file())

    def test_write_chunk_directory_removed_during_stream(self):
        uid = self.open()["upload_id"]
        upload_dir = self.root / uid
        # Create a stream that removes the upload directory during streaming
        class RemoveOnReadStream:
            def __init__(self):
                self.calls = 0
                self.upload_dir = upload_dir
            def read(self, size):
                self.calls += 1
                if self.calls == 2:
                    # Remove the directory on the second read
                    import shutil as shutil_module
                    shutil_module.rmtree(self.upload_dir)
                # Return data to force multiple reads; second read will fail to write
                if self.calls == 1:
                    return b"01"
                raise OSError(2, "No such file or directory")  # Simulate write failure after dir removal

        stream = RemoveOnReadStream()
        with self.assertRaises(uploads.UploadError) as cm:
            uploads.write_chunk(self.root, uid, 1, stream, 4)
        self.assertEqual(cm.exception.code, "not_found")
        # Nothing stray should remain under uploads_root
        self.assertEqual(list(self.root.glob("*/*.tmp")), [])


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

    def test_assemble_disk_full_cleanup_and_retry(self):
        uid = self.open()["upload_id"]
        for n, data in ((1, b"4567"), (0, b"0123"), (2, b"89")):
            self.put(uid, n, data)
        # Patch shutil.copyfileobj to raise OSError(ENOSPC)
        with mock.patch("shutil.copyfileobj") as mock_copy:
            mock_copy.side_effect = OSError(errno.ENOSPC, "No space left on device")
            with self.assertRaises(uploads.UploadError) as cm:
                uploads.assemble(self.root, uid, self.dest)
            self.assertEqual(cm.exception.code, "no_space")
        # Assembled file should be cleaned up
        self.assertFalse((self.root / uid / "assembled").exists())
        # Chunks should still be present for retry
        self.assertEqual(uploads.upload_status(self.root, uid)["received"], [0, 1, 2])


class TestPrune(UploadsBase):
    def test_removes_only_stale_uploads(self):
        old, new = self.open()["upload_id"], self.open()["upload_id"]
        now = time.time()
        meta = self.root / old / "meta.json"
        m = json.loads(meta.read_text()); m["created_at"] = now - 25 * 3600
        meta.write_text(json.dumps(m))
        self.assertEqual(uploads.prune_uploads(self.root, 24 * 3600, now), [old])
        self.assertTrue((self.root / new).exists())

    def test_prune_skips_stray_regular_files(self):
        uid = self.open()["upload_id"]
        # Create a stray regular file in uploads_root (not a directory)
        stray = self.root / "stray.txt"
        stray.write_text("junk")
        now = time.time()
        # Prune should not crash and should not report the stray file
        # Use a long max_age so the upload is not stale
        result = uploads.prune_uploads(self.root, 24 * 3600, now)
        self.assertEqual(result, [])
        # Stray file should still exist (not deleted)
        self.assertTrue(stray.exists())
        # Upload should still exist (not stale)
        self.assertTrue((self.root / uid).exists())


if __name__ == "__main__":
    unittest.main()
