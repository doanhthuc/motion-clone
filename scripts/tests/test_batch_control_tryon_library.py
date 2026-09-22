import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control.tryon_library import TryonLibrary, TryonLibraryError


class TestTryonLibrary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lib = TryonLibrary(Path(self.tmp.name) / "tryon-library", owner="app")

    def _seed_image(self) -> Path:
        src = Path(self.tmp.name) / "source.png"
        src.write_bytes(b"fake-png-bytes")
        return src

    def test_empty_library_lists_nothing(self):
        self.assertEqual(self.lib.list(), [])

    def test_save_copies_the_image_and_records_material_ids(self):
        src = self._seed_image()
        record = self.lib.save(image=src, material_ids={"character": "app/c.png",
                                                         "outfit": "app/o.png"},
                               provider="gemini")
        self.assertIn("id", record)
        self.assertEqual(record["material_ids"], {"character": "app/c.png", "outfit": "app/o.png"})
        self.assertEqual(record["provider"], "gemini")
        listed = self.lib.list()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], record["id"])
        # The source is COPIED, not moved or aliased — deleting it must not touch the library.
        src.unlink()
        image = self.lib.resolve_image(record["id"])
        self.assertIsNotNone(image)
        self.assertEqual(image.read_bytes(), b"fake-png-bytes")

    def test_resolve_unknown_id_is_none_not_a_crash(self):
        self.assertIsNone(self.lib.resolve_image("nope"))

    def test_delete_removes_the_entry_and_its_image(self):
        record = self.lib.save(image=self._seed_image(),
                               material_ids={"character": "app/c.png"}, provider="gemini")
        self.lib.delete(record["id"])
        self.assertEqual(self.lib.list(), [])
        self.assertIsNone(self.lib.resolve_image(record["id"]))

    def test_delete_unknown_id_raises_not_found(self):
        with self.assertRaises(TryonLibraryError) as ctx:
            self.lib.delete("nope")
        self.assertEqual(ctx.exception.code, "not_found")

    def test_two_owners_never_see_each_others_entries(self):
        other = TryonLibrary(Path(self.tmp.name) / "tryon-library", owner="tg-1")
        self.lib.save(image=self._seed_image(), material_ids={}, provider="gemini")
        self.assertEqual(other.list(), [])


if __name__ == "__main__":
    unittest.main()
