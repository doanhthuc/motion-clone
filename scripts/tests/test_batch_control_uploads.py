import errno
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control import materials, uploads

BIG = 10 ** 13          # "plenty of disk" for tests


class UploadsBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "uploads"
        self.staging = self.root.parent / "tg-staging"
        self.dest = self.staging / "app"
        p = mock.patch.object(uploads, "CHUNK_SIZE", 4)       # tiny chunks: 10 bytes = 3 chunks
        p.start(); self.addCleanup(p.stop)
        # write_chunk checks real free disk; "plenty" keeps these tests off the host's disk state.
        p = mock.patch.object(uploads.shutil, "disk_usage", return_value=mock.Mock(free=BIG))
        p.start(); self.addCleanup(p.stop)
        # complete() probes the staged file; these bytes are not real media.
        p = mock.patch.object(materials, "ingest", side_effect=lambda path: (path, {"kind": "video"}))
        p.start(); self.addCleanup(p.stop)

    def open(self, name="clip.mp4", size=10):
        return uploads.open_upload(self.root, name, size, free_bytes=BIG)

    def put(self, uid, n, data):
        uploads.write_chunk(self.root, uid, n, io.BytesIO(data), len(data))

    def full(self, name="clip.mp4"):
        uid = self.open(name)["upload_id"]
        for n, data in ((1, b"4567"), (0, b"0123"), (2, b"89")):
            self.put(uid, n, data)
        return uid

    def complete(self, uid):
        return uploads.complete(self.root, uid, self.staging)


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

    def test_file_name_over_200_bytes_is_bad_request(self):
        uploads.open_upload(self.root, "a" * 196 + ".mp4", 10, free_bytes=BIG)
        with self.assertRaises(uploads.UploadError) as cm:
            uploads.open_upload(self.root, "ả" * 67 + ".mp4", 10, free_bytes=BIG)   # 201+4 bytes
        self.assertEqual(cm.exception.code, "bad_request")

    def test_open_uploads_reserve_what_they_still_owe(self):
        # One 10-byte upload with 4 bytes in: it still owes 2*10 - 4 = 16 bytes
        # of disk (its remaining chunks plus the assembled copy).
        uid = self.open()["upload_id"]
        self.put(uid, 0, b"0123")
        need = uploads.DISK_HEADROOM + 16 + 2 * 10
        with self.assertRaises(uploads.UploadError) as cm:
            uploads.open_upload(self.root, "b.mp4", 10, free_bytes=need - 1)
        self.assertEqual(cm.exception.code, "no_space")
        uploads.open_upload(self.root, "b.mp4", 10, free_bytes=need)

    def test_a_completed_upload_reserves_nothing(self):
        self.complete(self.full())
        uploads.open_upload(self.root, "b.mp4", 10, free_bytes=uploads.DISK_HEADROOM + 20)

    def test_at_most_eight_unfinished_uploads(self):
        for _ in range(uploads.MAX_OPEN_UPLOADS):
            self.open()
        with self.assertRaises(uploads.UploadError) as cm:
            self.open()
        self.assertEqual(cm.exception.code, "too_many")


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

    def test_write_chunk_refuses_when_the_disk_is_nearly_full(self):
        uid = self.open()["upload_id"]
        with mock.patch.object(uploads.shutil, "disk_usage",
                               return_value=mock.Mock(free=uploads.DISK_HEADROOM + 3)):
            with self.assertRaises(uploads.UploadError) as cm:
                self.put(uid, 0, b"0123")
        self.assertEqual(cm.exception.code, "no_space")
        self.assertEqual(list((self.root / uid).glob("*.tmp")), [])

    def test_a_stalled_or_reset_chunk_is_bad_request(self):
        uid = self.open()["upload_id"]
        for exc in (TimeoutError("timed out"), ConnectionResetError(54, "reset")):
            class Stalls:
                calls = 0
                def read(self, size):
                    Stalls.calls += 1
                    if Stalls.calls == 1:
                        return b"01"
                    raise exc
            with self.assertRaises(uploads.UploadError, msg=exc) as cm:
                uploads.write_chunk(self.root, uid, 0, Stalls(), 4)
            self.assertEqual(cm.exception.code, "bad_request")
            self.assertIn("interrupted", cm.exception.message)
        self.assertEqual(list((self.root / uid).glob("*.tmp")), [])

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


class TestComplete(UploadsBase):
    def test_incomplete_is_refused(self):
        uid = self.open()["upload_id"]
        self.put(uid, 0, b"0123")
        with self.assertRaises(uploads.UploadError) as cm:
            self.complete(uid)
        self.assertEqual(cm.exception.code, "incomplete")
        self.assertFalse((self.root / uid / uploads.ASSEMBLING).exists())

    def test_byte_identical_and_cleaned_up(self):
        uid = self.full("Áo dài.mp4")
        got = self.complete(uid)
        staged = self.dest / "Ao_dai.mp4"
        self.assertEqual(got["material"]["id"], "app/Ao_dai.mp4")
        self.assertEqual(staged.read_bytes(), b"0123456789")
        # Chunks, marker and assembled file are gone; only the record of the result stays.
        self.assertEqual(sorted(p.name for p in (self.root / uid).iterdir()),
                         ["done.json", "meta.json"])

    def test_assemble_disk_full_cleanup_and_retry(self):
        uid = self.full()
        with mock.patch("shutil.copyfileobj") as mock_copy:
            mock_copy.side_effect = OSError(errno.ENOSPC, "No space left on device")
            with self.assertRaises(uploads.UploadError) as cm:
                self.complete(uid)
            self.assertEqual(cm.exception.code, "no_space")
        self.assertFalse((self.root / uid / "assembled").exists())
        self.assertFalse((self.root / uid / uploads.ASSEMBLING).exists())
        self.assertEqual(uploads.upload_status(self.root, uid)["received"], [0, 1, 2])
        self.assertEqual(self.complete(uid)["material"]["id"], "app/clip.mp4")   # retry works

    def test_unprobeable_removes_the_staged_file_and_the_upload(self):
        uid = self.full()
        with mock.patch.object(materials, "ingest",
                               side_effect=materials.MaterialError("unprobeable", "bad")):
            with self.assertRaises(materials.MaterialError):
                self.complete(uid)
        self.assertEqual(list(self.dest.iterdir()), [])
        self.assertFalse((self.root / uid).exists())


class TestCompleteIsIdempotent(UploadsBase):
    def test_complete_twice_returns_the_same_material_and_stages_once(self):
        uid = self.full()
        first = self.complete(uid)
        second = self.complete(uid)
        self.assertEqual(first, second)
        self.assertEqual([p.name for p in self.dest.iterdir()], ["clip.mp4"])

    def test_status_of_a_completed_upload_carries_the_material(self):
        uid = self.full()
        first = self.complete(uid)
        status = uploads.upload_status(self.root, uid)
        self.assertEqual(status["material"], first["material"])
        self.assertEqual(status["received"], [0, 1, 2])

    def test_a_chunk_after_complete_is_a_conflict(self):
        uid = self.full()
        self.complete(uid)
        with self.assertRaises(uploads.UploadError) as cm:
            self.put(uid, 0, b"0123")
        self.assertEqual(cm.exception.code, "conflict")

    def test_failure_after_staging_resumes_without_a_duplicate(self):
        # The file was moved into staging, then something unexpected failed
        # before done.json was written. The retry must pick up that file, not
        # re-assemble the chunks into a second copy (clip-1.mp4).
        uid = self.full()
        with mock.patch.object(materials, "ingest", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.complete(uid)
        self.assertEqual([p.name for p in self.dest.iterdir()], ["clip.mp4"])
        got = self.complete(uid)
        self.assertEqual(got["material"]["id"], "app/clip.mp4")
        self.assertEqual([p.name for p in self.dest.iterdir()], ["clip.mp4"])

    def test_failure_before_the_move_restages_under_the_same_name(self):
        # The staged name was recorded, then the move itself failed: the record
        # is stale and the retry stages normally.
        uid = self.full()
        real_replace = os.replace
        def failing_replace(src, dst):
            if Path(dst).parent == self.dest:
                raise OSError(errno.EIO, "I/O error")
            return real_replace(src, dst)
        with mock.patch.object(materials.os, "replace", side_effect=failing_replace):
            with self.assertRaises(OSError):
                self.complete(uid)
        self.assertFalse(self.dest.exists() and any(self.dest.iterdir()))
        self.assertEqual(self.complete(uid)["material"]["id"], "app/clip.mp4")
        self.assertEqual([p.name for p in self.dest.iterdir()], ["clip.mp4"])

    def test_a_marker_left_by_a_dead_process_does_not_block(self):
        uid = self.full()
        (self.root / uid / uploads.ASSEMBLING).write_text("some-earlier-process")
        self.assertEqual(self.complete(uid)["material"]["id"], "app/clip.mp4")


class TestAssemblyIsOutsideTheLock(UploadsBase):
    def test_other_uploads_proceed_while_one_assembles(self):
        a, b = self.full(), self.open()["upload_id"]
        entered, release = threading.Event(), threading.Event()
        real_copy = shutil.copyfileobj

        def slow_copy(src, dst, length=0):
            entered.set()
            release.wait(10)
            return real_copy(src, dst, length)

        result = {}
        with mock.patch.object(uploads.shutil, "copyfileobj", side_effect=slow_copy):
            worker = threading.Thread(target=lambda: result.update(r=self.complete(a)))
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                # Another upload's chunk lands while `a` is mid-copy.
                landed = threading.Event()
                threading.Thread(target=lambda: (self.put(b, 0, b"0123"), landed.set()),
                                 daemon=True).start()
                self.assertTrue(landed.wait(5), "write_chunk blocked behind an assembly")
                # The assembling upload itself refuses chunks and a second complete.
                for call in (lambda: self.put(a, 0, b"0123"), lambda: self.complete(a)):
                    with self.assertRaises(uploads.UploadError) as cm:
                        call()
                    self.assertEqual(cm.exception.code, "conflict")
            finally:
                release.set()
                worker.join(10)
        self.assertEqual(result["r"]["material"]["id"], "app/clip.mp4")
        self.assertEqual(uploads.upload_status(self.root, b)["received"], [0])

    def test_a_held_marker_refuses_chunks_for_that_upload_only(self):
        a, b = self.open()["upload_id"], self.open()["upload_id"]
        (self.root / a / uploads.ASSEMBLING).write_text(uploads._PROCESS_TOKEN)
        with self.assertRaises(uploads.UploadError) as cm:
            self.put(a, 0, b"0123")
        self.assertEqual(cm.exception.code, "conflict")
        self.put(b, 0, b"0123")


class TestPrune(UploadsBase):
    def _age(self, uid, hours, now):
        """Backdate an upload: both its created_at and every file's mtime."""
        meta = self.root / uid / "meta.json"
        m = json.loads(meta.read_text()); m["created_at"] = now - hours * 3600
        meta.write_text(json.dumps(m))
        for f in (self.root / uid).iterdir():
            os.utime(f, (now - hours * 3600, now - hours * 3600))

    def test_removes_only_stale_uploads(self):
        old, new = self.open()["upload_id"], self.open()["upload_id"]
        now = time.time()
        self._age(old, 25, now)
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

    def test_age_is_the_newest_activity_not_the_open_time(self):
        uid = self.open()["upload_id"]
        now = time.time()
        self._age(uid, 25, now)
        self.put(uid, 0, b"0123")                 # a chunk just arrived
        self.assertEqual(uploads.prune_uploads(self.root, 24 * 3600, now), [])

    def test_an_assembling_upload_is_skipped_until_its_marker_is_stale(self):
        uid = self.open()["upload_id"]
        now = time.time()
        self._age(uid, 25, now)
        marker = self.root / uid / uploads.ASSEMBLING
        marker.write_text(uploads._PROCESS_TOKEN)
        self.assertEqual(uploads.prune_uploads(self.root, 24 * 3600, now), [])
        os.utime(marker, (now - 25 * 3600, now - 25 * 3600))
        self.assertEqual(uploads.prune_uploads(self.root, 24 * 3600, now), [uid])

    def test_a_completed_upload_ages_out(self):
        uid = self.full()
        self.complete(uid)
        now = time.time()
        self._age(uid, 25, now)
        self.assertEqual(uploads.prune_uploads(self.root, 24 * 3600, now), [uid])


if __name__ == "__main__":
    unittest.main()
