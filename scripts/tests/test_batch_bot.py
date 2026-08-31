import subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import state_path_for
import tgbot.bot as bot
from tgbot.bot import allowed
from tgbot.ingest import Probe

ME = 12345


def update_from(user_id: int) -> dict:
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id}, "text": "/start"}}


def cmd_from(user_id: int, text: str) -> dict:
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id}, "text": text}}


def doc_from(user_id: int, file_id: str, file_name: str | None = None) -> dict:
    # file_name is genuinely optional in the Bot API — some clients omit it —
    # and when it is absent the bot falls back to the basename of what getFile
    # returned. Omitting it here is what makes the staged copies keep the test
    # fixtures' own names.
    doc = {"file_id": file_id}
    if file_name is not None:
        doc["file_name"] = file_name
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id},
                        "document": doc}}


def media_from(user_id: int, kind: str) -> dict:
    # `photo` is the one kind the Bot API delivers as a list (one entry per
    # generated thumbnail size); the rest are plain objects. Only the presence
    # of the key matters to the branch under test.
    payload = [{"file_id": "x"}] if kind == "photo" else {"file_id": "x"}
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id},
                        kind: payload}}


class FakeTg:
    """Records calls instead of touching the network. `call()` only needs to
    answer getFile — /tryon, /result and the document-ingest flow (Task 7)
    are the only handlers that call it, and getFile is the only method any
    of them use."""
    def __init__(self):
        self.messages: list[str] = []
        self.documents: list[tuple] = []
        self.file_paths: dict[str, str] = {}   # file_id -> local path, set by the test

    def send_message(self, chat_id, text):
        self.messages.append(text)
        return 1

    def send_document(self, chat_id, path, caption=""):
        self.documents.append((path, caption))

    def call(self, method, **params):
        if method == "getFile":
            return {"file_path": self.file_paths[params["file_id"]]}
        raise AssertionError(f"FakeTg.call: unexpected method {method!r}")


class TestAllowed(unittest.TestCase):
    def test_the_owner_is_allowed(self):
        self.assertTrue(allowed(update_from(ME), ME))

    def test_anyone_else_is_refused(self):
        # This whitelist is the only thing between a stranger and a button that
        # rents a $0.99/hour GPU. It must be an allowlist, never a blocklist.
        self.assertFalse(allowed(update_from(99999), ME))

    def test_an_update_with_no_sender_is_refused(self):
        # channel_post, edited_channel_post and service updates have no
        # message.from. Defaulting those to allowed would open the door.
        self.assertFalse(allowed({"channel_post": {"chat": {"id": ME}}}, ME))

    def test_a_malformed_update_is_refused_not_crashed(self):
        self.assertFalse(allowed({}, ME))
        self.assertFalse(allowed({"message": {}}, ME))

    def test_the_owner_speaking_in_a_group_is_refused(self):
        # A private chat has chat.id == the user's id; a group does not. If the
        # owner adds this bot to a group, the sender check alone passes and the
        # bot would reply into the group with manifests, file paths and the
        # finished video. Tightened 2026-08-31: the reply surface must be as
        # narrow as the send surface.
        group = {"message": {"from": {"id": ME}, "chat": {"id": -1001234567890},
                             "text": "/start"}}
        self.assertFalse(allowed(group, ME))


class TestNonFileMediaIsRefused(unittest.TestCase):
    """Measured 2026-08-31 by sending the same driver from the same iPhone
    twice. As a File: 24,558,897 bytes in and out, sha256 identical. Through
    the Photo/Video tab's "1080p" option: 1088x1920 and 444 frames both
    survive, the bitrate is halved (13,196 -> 6,603 kbps) and 49.8% of the
    bytes are gone — a loss invisible in a file listing and in a still.

    Before NON_FILE_MEDIA only `photo` was checked. A `video` — which is what
    that tab produces for a driver, and the first mistake made while taking
    the measurement above — matched neither media branch, fell through to the
    text handlers as "", matched no command, and drew NO reply at all. The
    user waits on a bot that never saw the file.
    """

    def test_a_video_is_refused_rather_than_silently_ignored(self):
        tg = FakeTg()
        bot.handle(tg, media_from(ME, "video"), allowed_user_id=ME)
        self.assertEqual(len(tg.messages), 1)
        self.assertIn("not a File", tg.messages[0])
        self.assertIn("video", tg.messages[0])

    def test_a_photo_is_still_refused(self):
        tg = FakeTg()
        bot.handle(tg, media_from(ME, "photo"), allowed_user_id=ME)
        self.assertEqual(len(tg.messages), 1)
        self.assertIn("paperclip", tg.messages[0])

    def test_every_non_file_kind_draws_exactly_one_reply(self):
        # The point is coverage of the whole tuple: any kind added later that
        # forgets a reply reintroduces the silence this class exists for.
        for kind in bot.NON_FILE_MEDIA:
            with self.subTest(kind=kind):
                tg = FakeTg()
                bot.handle(tg, media_from(ME, kind), allowed_user_id=ME)
                self.assertEqual(len(tg.messages), 1, f"{kind} drew no reply")
                # Ingest must not be reached at all: FakeTg.call() raises on
                # any method, so a getFile here would fail this test loudly.
                self.assertEqual(tg.documents, [])

    def test_a_stranger_sending_a_video_still_gets_silence(self):
        # allowed() runs before this branch and must keep doing so — a refusal
        # message would confirm to a stranger that the bot exists.
        tg = FakeTg()
        bot.handle(tg, media_from(99999, "video"), allowed_user_id=ME)
        self.assertEqual(tg.messages, [])


class TestSafeName(unittest.TestCase):
    """Finding C (2026-08-31): _safe_name re-spelled the whole basename in one
    pass, so a stem made entirely of characters outside the alphabet collapsed
    and the strip("._-") then ate the dot in front of the extension.
    `_safe_name('写真.heic')` returned `'heic'` — a name with NO suffix, so
    to_png_if_heic never fired and probe() rejected the file. The user's
    material comes from a Vietnamese-language workflow, so non-Latin filenames
    are the ordinary case, not the exotic one.
    """

    def test_a_non_ascii_stem_keeps_its_extension(self):
        self.assertEqual(bot._safe_name("写真.heic"), "file.heic")

    def test_a_dotfile_keeps_its_name(self):
        # Path('.hidden').suffix is '' — the whole thing is a stem, so the
        # result is a plain stem too, not an extension promoted to a name.
        self.assertEqual(bot._safe_name(".hidden"), "hidden")

    def test_a_name_stripped_to_nothing_falls_back(self):
        self.assertEqual(bot._safe_name("写真"), "file")
        self.assertEqual(bot._safe_name("___"), "file")

    def test_an_ordinary_name_is_unchanged_apart_from_the_alphabet(self):
        self.assertEqual(bot._safe_name("my driver:v1.mp4"), "my_driver_v1.mp4")


class TestResultAndTryonPathSafety(unittest.TestCase):
    """Regression tests for the path-containment finding: the allowlist
    restricts WHO can message the bot, not WHAT they type — a single
    mistyped or pasted path must never let /result or /tryon read or send a
    file outside batch/ or out/.
    """

    def setUp(self):
        self._orig_root = bot.ROOT
        self.root = Path(tempfile.mkdtemp())
        (self.root / "batch").mkdir()
        (self.root / "out").mkdir()
        # One level above batch/ — reachable via "../" or an absolute path if
        # /result does not reject either, and must never be reachable.
        (self.root / "secret.yaml").write_text("runs: []", encoding="utf-8")
        # One level above out/, shaped like a real try-on image — reachable
        # via "../" if /tryon's batch id is not rejected.
        evil_run = self.root / "evil" / "runs" / "job"
        evil_run.mkdir(parents=True)
        (evil_run / "01-tryon.png").write_bytes(b"not a real png, just a probe")
        bot.ROOT = self.root

    def tearDown(self):
        bot.ROOT = self._orig_root

    def test_result_refuses_an_absolute_path(self):
        tg = FakeTg()
        payload = str(self.root / "secret.yaml")
        bot.handle(tg, cmd_from(ME, f"/result {payload}"), allowed_user_id=ME)
        self.assertEqual(tg.documents, [])
        self.assertEqual(len(tg.messages), 1)
        self.assertIn("not allowed", tg.messages[0])
        self.assertNotIn(payload, tg.messages[0])

    def test_result_refuses_dotdot_traversal(self):
        tg = FakeTg()
        bot.handle(tg, cmd_from(ME, "/result ../secret.yaml"), allowed_user_id=ME)
        self.assertEqual(tg.documents, [])
        self.assertEqual(len(tg.messages), 1)
        self.assertIn("not allowed", tg.messages[0])

    def test_tryon_refuses_dotdot_traversal(self):
        tg = FakeTg()
        bot.handle(tg, cmd_from(ME, "/tryon ../evil"), allowed_user_id=ME)
        # Pre-fix, this sent evil/runs/job/01-tryon.png — a real file one
        # level above out/ — straight to send_document.
        self.assertEqual(tg.documents, [])
        self.assertEqual(len(tg.messages), 1)
        self.assertIn("not allowed", tg.messages[0])

    def test_result_reports_not_found_for_a_valid_but_missing_name(self):
        tg = FakeTg()
        bot.handle(tg, cmd_from(ME, "/result does-not-exist.yaml"),
                  allowed_user_id=ME)
        self.assertEqual(tg.documents, [])
        self.assertEqual(len(tg.messages), 1)
        # A valid (bare) name that just doesn't exist is a plain "not found",
        # never the safety refusal.
        self.assertNotIn("not allowed", tg.messages[0])

    def test_refusal_does_not_echo_the_offending_input(self):
        tg = FakeTg()
        payload = "../../../../etc/passwd"
        bot.handle(tg, cmd_from(ME, f"/result {payload}"), allowed_user_id=ME)
        self.assertEqual(tg.documents, [])
        self.assertNotIn(payload, tg.messages[0])
        self.assertNotIn("passwd", tg.messages[0])


class TestFlow(unittest.TestCase):
    """The state machine Task 7 adds: files in, an ambiguous image asked
    about (never guessed), the manifest shown once every required slot is
    filled, and /confirm as the only reachable path to start_drain.

    Real files on disk, real `make batch-validate`: only `probe()` is faked
    (no real ffprobe/media needed) — everything downstream of it, including
    the manifest text and the free validation gate, runs for real, against
    the real repo (`bot._REPO_ROOT`), so a passing test here is evidence the
    generated manifest actually validates, not just that a string was built.
    """

    def setUp(self):
        self._orig_root = bot.ROOT
        self.root = Path(tempfile.mkdtemp())
        (self.root / "batch").mkdir()
        (self.root / "out").mkdir()
        bot.ROOT = self.root
        bot._STATE.clear()
        bot._PENDING.clear()
        bot._LAST_VALIDATE.clear()
        bot._CONFIRM_WARNED.clear()

        # Real, if empty, files: validate_manifest checks path.is_file().
        # These stand in for what the Bot API server wrote — the bot copies
        # them into batch/tg-staging/<chat>/ before touching them (findings
        # C2/I5), so every assertion below about a slot is about the STAGED
        # path, never this one.
        self.driver_path = self.root / "driver.mp4"
        self.driver_path.write_bytes(b"d")
        self.character_path = self.root / "character.jpg"
        self.character_path.write_bytes(b"c")
        self.outfit_path = self.root / "outfit.jpg"
        self.outfit_path.write_bytes(b"o")

        self.tg = FakeTg()
        self.tg.file_paths = {
            "driver-id": str(self.driver_path),
            "character-id": str(self.character_path),
            "outfit-id": str(self.outfit_path),
        }
        self.driver_probe = Probe(kind="video", width=1080, height=1920, duration_s=5.0,
                                  bitrate_kbps=3000, size_bytes=1_500_000)
        self.image_probe = Probe(kind="image", width=1024, height=1024, duration_s=0.0,
                                 bitrate_kbps=0, size_bytes=800_000)

    def tearDown(self):
        bot.ROOT = self._orig_root
        bot._STATE.clear()
        bot._PENDING.clear()
        bot._LAST_VALIDATE.clear()
        bot._CONFIRM_WARNED.clear()

    def staged(self, name: str) -> Path:
        """Where _stage_file puts a file of this name for this chat."""
        return self.root / "batch" / bot.STAGING_DIR_NAME / str(ME) / name

    def _probe_for(self, mapping):
        # Keyed by NAME, not by the source path: probe() now runs on the
        # staged copy, so the path it is handed is not the one the test wrote.
        def fake_probe(path):
            return mapping[Path(path).name]
        return fake_probe

    def _fill_required_slots(self):
        """driver (auto), character and outfit (each an ambiguous image,
        answered by name) — every required role of the pipeline the bot
        hardcodes, per docs/superpowers/specs/2026-08-30-…: "four labelled
        slots". Background is optional and deliberately left unfilled."""
        probe_fn = self._probe_for({
            self.driver_path.name: self.driver_probe,
            self.character_path.name: self.image_probe,
            self.outfit_path.name: self.image_probe,
        })
        with mock.patch("tgbot.bot.probe", side_effect=probe_fn):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
            bot.handle(self.tg, doc_from(ME, "character-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "character"), allowed_user_id=ME)
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME)

    def test_a_video_fills_the_driver_slot_without_asking(self):
        with mock.patch("tgbot.bot.probe", return_value=self.driver_probe):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        self.assertEqual(len(self.tg.messages), 1)
        self.assertIn("driver", self.tg.messages[0])
        self.assertNotIn("which", self.tg.messages[0].lower())
        self.assertEqual(bot._STATE[ME].slots.get("driver"), self.staged("driver.mp4"))

    def test_an_image_is_asked_about_and_the_answer_fills_the_slot(self):
        with mock.patch("tgbot.bot.probe", return_value=self.image_probe):
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
        self.assertIn("which", self.tg.messages[-1].lower())
        self.assertNotIn("outfit", bot._STATE[ME].slots)

        bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME)
        self.assertEqual(bot._STATE[ME].slots.get("outfit"), self.staged("outfit.jpg"))
        self.assertIn("outfit", self.tg.messages[-1])

    def test_an_unrecognised_slot_answer_is_re_asked_not_guessed(self):
        with mock.patch("tgbot.bot.probe", return_value=self.image_probe):
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
        bot.handle(self.tg, cmd_from(ME, "banana"), allowed_user_id=ME)
        self.assertNotIn("outfit", bot._STATE[ME].slots)
        self.assertNotIn("character", bot._STATE[ME].slots)
        self.assertEqual(len(bot._PENDING[ME]), 1)   # still parked, never guessed
        last = self.tg.messages[-1].lower()
        self.assertIn("didn't recognise", last)
        self.assertIn("outfit", last)          # re-asks with the same options, not silence

    def test_the_manifest_is_shown_before_anything_is_confirmed(self):
        with mock.patch("tgbot.bot.start_drain") as start_drain:
            self._fill_required_slots()
            start_drain.assert_not_called()
        joined = "\n".join(self.tg.messages)
        self.assertIn("pipeline", joined)
        self.assertIn("runs:", joined)

    def test_confirm_calls_start_drain_once_with_dry_run_false(self):
        # Renamed from "...and_nothing_else_does": with drain_running mocked
        # to False, this test is identical with or without that guard — it
        # does NOT pin the guard (see
        # test_confirm_is_refused_while_a_drain_is_already_running for
        # that). What this pins is the call itself: exactly once, with the
        # right dry_run value, only reachable after /confirm.
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
            start_drain.assert_not_called()
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
        start_drain.assert_called_once()
        _, kwargs = start_drain.call_args
        self.assertEqual(kwargs.get("dry_run"), False)

    def test_two_ambiguous_images_sent_back_to_back_do_not_clobber_each_other(self):
        """Regression (Task 7 fix round 1, Finding 1): Telegram delivers a
        multi-file send as consecutive updates inside one get_updates()
        batch, with no chance to answer between them — the natural way to
        do the Goal line's "send four files". Pre-fix, `_PENDING_SLOT` held
        a single Path, so parking a second ambiguous image before the first
        was answered silently discarded the first: answering "character"
        then assigned the SECOND file to it, and the answer meant for the
        first file's real slot went nowhere (no pending entry left to
        consume, and no error). This pins the fix: a queue, where only the
        head is ever asked about and answers apply in arrival order.
        """
        with mock.patch("tgbot.bot.probe", return_value=self.image_probe):
            bot.handle(self.tg, doc_from(ME, "character-id"), allowed_user_id=ME)
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)

        bot.handle(self.tg, cmd_from(ME, "character"), allowed_user_id=ME)
        bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME)

        self.assertEqual(bot._STATE[ME].slots.get("character"), self.staged("character.jpg"))
        self.assertEqual(bot._STATE[ME].slots.get("outfit"), self.staged("outfit.jpg"))

    def test_confirm_is_refused_after_a_failed_validation(self):
        """Regression (Task 7 fix round 1, Finding 2): _maybe_show_manifest
        only skipped the confirm PROMPT on a failed `make batch-validate` —
        /confirm's own guard never re-checked, so a user who ignored the
        failure message and typed /confirm anyway reached start_drain on a
        manifest already known to be invalid. Forcing the cached outcome to
        False (as if the last render failed validation) must refuse."""
        with mock.patch("tgbot.bot.start_drain") as start_drain:
            self._fill_required_slots()
            bot._LAST_VALIDATE[ME] = False
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
        start_drain.assert_not_called()

    def test_confirm_is_refused_while_a_drain_is_already_running(self):
        # The slots are filled with drain_running FALSE, so the manifest is
        # written and validated as normal and /confirm reaches its own guard.
        # (Before finding C1 was fixed the whole fill happened under
        # drain_running=True, which meant /confirm was actually being refused
        # by the _LAST_VALIDATE guard and this test passed without ever
        # exercising the drain guard it is named for.)
        with mock.patch("tgbot.bot.start_drain") as start_drain:
            self._fill_required_slots()
            with mock.patch("tgbot.bot.drain_running", return_value=True):
                bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
        start_drain.assert_not_called()
        self.assertIn("already running", self.tg.messages[-1])

    def test_confirm_without_a_complete_job_is_refused(self):
        with mock.patch("tgbot.bot.start_drain") as start_drain:
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
        start_drain.assert_not_called()

    def test_a_live_drain_blocks_the_next_job_from_overwriting_the_manifest(self):
        """Regression for finding C1 (2026-08-31): the /confirm chain was
        guarded but the WRITE path was not.

        scripts/drain.py:220-248 runs batch_run.py as a separate process
        twice, re-reading batch/tg-<chat>.yaml from disk each time — phase A
        (--no-start), then provision + bootstrap, then phase B (--resume). For
        the ~10-35 minutes in between, that one deterministic path is live
        input. Assembling the next job during the render (the most ordinary
        thing there is) filled the last slot, _maybe_show_manifest wrote over
        the file, and phase B loaded job B onto the pod rented for job A with
        nobody asked. The manifest bytes must not move while a drain owns it.
        """
        with mock.patch("tgbot.bot.start_drain"):
            self._fill_required_slots()
        manifest = bot._job_manifest_path(ME)
        before = manifest.read_bytes()

        # A visibly DIFFERENT second job, so "unchanged" cannot pass by
        # coincidence: a 20-second driver renders a different comment line and
        # a different preset from the 5-second one above.
        bot._STATE.clear()
        bot._PENDING.clear()
        bot._LAST_VALIDATE.clear()
        second_driver = self.root / "driver2.mp4"
        second_driver.write_bytes(b"dd")
        self.tg.file_paths["driver2-id"] = str(second_driver)
        long_probe = Probe(kind="video", width=1080, height=1920, duration_s=20.0,
                           bitrate_kbps=9000, size_bytes=9_000_000)
        # Matched by prefix, not by exact name: staging never overwrites, so
        # the second copy of character.jpg lands as character-1.jpg.
        def probe_fn(path):
            return long_probe if Path(path).name.startswith("driver2") else self.image_probe
        with mock.patch("tgbot.bot.drain_running", return_value=True), \
             mock.patch("tgbot.bot.probe", side_effect=probe_fn):
            bot.handle(self.tg, doc_from(ME, "driver2-id"), allowed_user_id=ME)
            bot.handle(self.tg, doc_from(ME, "character-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "character"), allowed_user_id=ME)
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME)

        self.assertEqual(manifest.read_bytes(), before)
        self.assertIn("drain is still running", self.tg.messages[-1])

    def test_staged_paths_replace_the_bot_api_path_that_carries_the_token(self):
        """Findings C2 and I5: a local Bot API file path is
        /var/lib/telegram-bot-api/<TOKEN>/documents/file_5.mp4 — the token is
        a directory component. That path used to go into Job.slots, then into
        the manifest, then back over Telegram verbatim.
        """
        token_path = self.root / "telegram-bot-api" / "SECRETTOKEN" / "documents"
        token_path.mkdir(parents=True)
        arrival = token_path / "file_5.mp4"
        arrival.write_bytes(b"d")
        self.tg.file_paths["tok-id"] = str(arrival)

        with mock.patch("tgbot.bot.probe", return_value=self.driver_probe):
            bot.handle(self.tg, doc_from(ME, "tok-id", file_name="my driver:v1.mp4"),
                       allowed_user_id=ME)

        staged = bot._STATE[ME].slots["driver"]
        self.assertEqual(staged.parent, self.root / "batch" / bot.STAGING_DIR_NAME / str(ME))
        self.assertNotIn("SECRETTOKEN", str(staged))
        self.assertNotIn("SECRETTOKEN", "\n".join(self.tg.messages))
        # The user's own name survives, minus anything that would break the
        # plain (unquoted) YAML scalar job.py emits — job.py is protected, so
        # the quoting has to happen by never producing a name that needs it.
        self.assertEqual(staged.name, "my_driver_v1.mp4")
        self.assertTrue(staged.is_file())

    def test_the_accepted_file_reply_carries_the_sha256_and_byte_count(self):
        # Finding I4: /sha was removed by Task 7, which left the README's
        # acceptance procedure unrunnable. A6 compares this digest against
        # out/<batch>/_final/<run>.mp4.
        import hashlib
        with mock.patch("tgbot.bot.probe", return_value=self.driver_probe):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        self.assertIn(hashlib.sha256(b"d").hexdigest(), self.tg.messages[0])
        self.assertIn("1 bytes", self.tg.messages[0])

    def test_a_failing_conversion_is_reported_not_swallowed(self):
        """Finding I1: to_png_if_heic and getFile were outside the try, so
        every message they raise escaped handle(), was swallowed by main()'s
        `except Exception: log(...)`, and the user got nothing at all."""
        with mock.patch("tgbot.bot.to_png_if_heic",
                        side_effect=RuntimeError("ffmpeg is not installed")):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        self.assertIn("ffmpeg is not installed", self.tg.messages[-1])
        self.assertNotIn(ME, bot._STATE)

    def test_a_getfile_failure_is_reported_not_swallowed(self):
        from tgbot.tgclient import TgError
        with mock.patch.object(self.tg, "call", side_effect=TgError("getFile rejected")):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        self.assertIn("getFile rejected", self.tg.messages[-1])

    def test_resending_a_video_says_it_is_replacing_the_driver(self):
        # Finding I7: the second video silently replaced the first.
        with mock.patch("tgbot.bot.probe", return_value=self.driver_probe):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        self.assertIn("replacing the previous driver", self.tg.messages[-1])

    def test_answering_a_filled_role_says_it_is_replacing_it(self):
        # Finding I7: this path pops the queue head AND overwrites the slot,
        # so the displaced file is unrecoverable — it must at least be named.
        with mock.patch("tgbot.bot.probe", return_value=self.image_probe):
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME)
            bot.handle(self.tg, doc_from(ME, "character-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME)
        self.assertIn("replacing the previous outfit", self.tg.messages[-1])

    def test_confirm_with_files_still_unassigned_refuses_once_then_runs(self):
        """Finding I6: /confirm succeeded with files still queued and dropped
        them silently on the state clear. Refuse once naming the count; a
        second /confirm runs without them, and says so."""
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
            with mock.patch("tgbot.bot.probe", return_value=self.image_probe):
                bot.handle(self.tg, doc_from(ME, "character-id"), allowed_user_id=ME)

            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
            start_drain.assert_not_called()
            self.assertIn("1 file(s) still unassigned", self.tg.messages[-1])

            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
        start_drain.assert_called_once()
        self.assertIn("running without 1 unassigned file(s)",
                      "\n".join(self.tg.messages))

    def test_status_reports_progress_from_the_journal(self):
        # Finding I3: progress_text existed, was tested, and had no caller —
        # after /confirm the user got one message and then silence.
        import json
        with mock.patch("tgbot.bot.start_drain"), \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
        manifest = bot._job_manifest_path(ME)
        state_path_for(manifest).write_text(json.dumps(
            {"batch": "2026-08-31-2140",
             "runs": {"job": {"status": "running",
                              "stages": {"motion": {"status": "done", "sec": 247}}}}}),
            encoding="utf-8")
        bot.handle(self.tg, cmd_from(ME, "/status"), allowed_user_id=ME)
        self.assertIn("2026-08-31-2140", self.tg.messages[-1])
        self.assertIn("motion", self.tg.messages[-1])

    def test_status_before_anything_started_is_reported_not_crashed(self):
        bot.handle(self.tg, cmd_from(ME, "/status"), allowed_user_id=ME)
        self.assertIn("nothing started", self.tg.messages[-1])

    def test_a_heic_upload_never_overwrites_an_already_accepted_png(self):
        """Regression for finding A (2026-08-31).

        _stage_file's never-overwrite counter only ever checked the INCOMING
        file's own staged name. ingest.to_png_if_heic then writes
        `path.with_suffix('.png')` (ingest.py:145) with no collision check of
        its own, so `photo.png` (already accepted, already sitting in
        Job.slots) had its bytes replaced by the conversion of a later
        `photo.heic`. No message, no manifest change, and both slots then
        pointed at the same file — the wrong image inside a $0.99/hour render.
        Created by the staging change: before it, conversion happened against
        Telegram's unique `file_N.heic` names.
        """
        png_src = self.root / "photo.png"
        png_src.write_bytes(b"the original png the user accepted")
        heic_src = self.root / "photo.heic"
        heic_src.write_bytes(b"heic bytes")
        self.tg.file_paths["png-id"] = str(png_src)
        self.tg.file_paths["heic-id"] = str(heic_src)

        def fake_convert(path: Path) -> Path:
            # Mirrors ingest.to_png_if_heic's naming rule exactly (it writes
            # `dest = path.with_suffix('.png')`), without needing sips/ffmpeg
            # or a real HEIC file. The naming rule IS the bug.
            if path.suffix.lower() not in (".heic", ".heif"):
                return path
            dest = path.with_suffix(".png")
            dest.write_bytes(b"converted from heic")
            return dest

        with mock.patch("tgbot.bot.probe", return_value=self.image_probe), \
             mock.patch("tgbot.bot.to_png_if_heic", side_effect=fake_convert):
            bot.handle(self.tg, doc_from(ME, "png-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "character"), allowed_user_id=ME)
            bot.handle(self.tg, doc_from(ME, "heic-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME)

        slots = bot._STATE[ME].slots
        self.assertEqual(self.staged("photo.png").read_bytes(),
                         b"the original png the user accepted")
        self.assertEqual(slots["character"], self.staged("photo.png"))
        self.assertNotEqual(slots["character"], slots["outfit"])
        self.assertTrue(slots["outfit"].is_file())

    def test_confirm_recovers_from_the_write_guard_instead_of_stranding_the_user(self):
        """Regression for finding B (2026-08-31).

        The C1 write guard refuses to render while a drain is live and says
        "your files are kept; send /status" — but it left _LAST_VALIDATE
        unset. The job is already complete at that point, so no further slot
        fill re-enters _maybe_show_manifest and nothing ever sets it. /confirm
        then refused with "the manifest hasn't passed validation ... Fix the
        error already shown" — and no error had ever been shown. The only
        escape was to re-send a file, which nothing tells the user.
        """
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=True):
            self._fill_required_slots()
        self.assertIn("drain is still running", self.tg.messages[-1])
        self.assertNotIn(ME, bot._LAST_VALIDATE)

        # The drain has since finished, so the write guard now passes.
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
        start_drain.assert_called_once()
        self.assertIn("started", self.tg.messages[-1])
        # The manifest was never shown before money was spent — show it now,
        # so the transcript still records what was paid for.
        self.assertIn("runs:", "\n".join(self.tg.messages))

    def test_confirm_after_a_real_validation_failure_names_the_real_reason(self):
        """The other half of finding B: recovering from the write guard must
        not turn into "retry until it runs". A manifest that genuinely fails
        `make batch-validate` is still refused, and the refusal describes what
        actually happened rather than pointing at an error nobody was sent."""
        failed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="run 'job': character: not a file", stderr="")
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=False), \
             mock.patch("tgbot.bot.subprocess.run", return_value=failed):
            self._fill_required_slots()
            self.assertIn("character: not a file", self.tg.messages[-1])
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
        start_drain.assert_not_called()
        last = self.tg.messages[-1]
        self.assertIn("batch-validate", last)
        # Must not claim an error was shown when the point of finding B is
        # that sometimes none was.
        self.assertNotIn("already shown", last)

    def test_a_fidelity_read_failure_is_reported_not_swallowed(self):
        """Finding D (2026-08-31): _fidelity_line sat one line below the try
        that finding I1 widened, so an OSError from its open()/stat() escaped
        handle() into main()'s blanket `except Exception: log(...)` — exactly
        the silence I1 existed to remove."""
        with mock.patch("tgbot.bot.probe", return_value=self.driver_probe), \
             mock.patch("tgbot.bot._fidelity_line",
                        side_effect=OSError("Input/output error")):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        self.assertIn("Input/output error", self.tg.messages[-1])
        self.assertNotIn(ME, bot._STATE)


class TestSafeNameFoldsDiacritics(unittest.TestCase):
    """Vietnamese names survive staging instead of being deleted down to the
    separators between their letters.

    Before this, `_SAFE_NAME_RE` deleted every accented letter outright:
    `áo dài.jpg` became `o_d_i.jpg`. That is not tidiness — the manifest is
    what the user reads on a phone before confirming a $0.99/hour render, and
    a filename they cannot recognise is a file they cannot check is the right
    one. Staging exists partly to make those names readable.
    """

    def test_accents_fold_to_their_base_letter(self):
        self.assertEqual(bot._safe_name("áo dài.jpg"), "ao_dai.jpg")
        self.assertEqual(bot._safe_name("Nguyễn.png"), "Nguyen.png")
        self.assertEqual(bot._safe_name("cà phê sữa đá.mov"), "ca_phe_sua_da.mov")

    def test_d_with_stroke_is_mapped_by_hand(self):
        # đ (U+0111) is a distinct letter, not d + a combining mark, so NFD
        # leaves it whole and the filter would delete it. It is the only
        # Vietnamese letter needing an explicit mapping.
        self.assertEqual(bot._safe_name("đầm dạ hội.MP4"), "dam_da_hoi.MP4")
        self.assertEqual(bot._safe_name("Đ.jpg"), "D.jpg")

    def test_a_script_with_no_latin_base_still_falls_back(self):
        # Folding must not rescue what has no Latin base — the finding-C
        # fallback still applies, and the extension still survives.
        self.assertEqual(bot._safe_name("写真.heic"), "file.heic")

    def test_normalisation_never_manufactures_a_path_separator(self):
        # The regression guard for the ONE way this fold could be made unsafe.
        # NFKD applies compatibility mappings: normalize("NFKD", "／") is "/"
        # and normalize("NFKD", "．") is "." (measured 2026-08-31). Folding
        # with NFKD would therefore create separators and dots out of input
        # that had none, upstream of _SAFE_NAME_RE and of every reason
        # _safe_child() refuses them. NFD does not, and this pins that.
        self.assertEqual(bot._fold_diacritics("／"), "／")
        self.assertEqual(bot._fold_diacritics("．"), "．")
        self.assertEqual(bot._safe_name("..／..／etc／passwd.jpg"), "etc_passwd.jpg")
        for hostile in ("a／b.jpg", "ｄ．．/x.jpg", "..／x.png"):
            out = bot._safe_name(hostile)
            self.assertNotIn("/", out)
            self.assertNotIn("\\", out)
            self.assertNotIn("..", out)


if __name__ == "__main__":
    unittest.main()
