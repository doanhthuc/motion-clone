import itertools, json, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import state_path_for
import tgbot.bot as bot
from tgbot.bot import allowed
from tgbot.ingest import Probe

ME = 12345


_MESSAGE_IDS = itertools.count(1)


def next_message_id() -> int:
    """One id space shared by the user's messages and the bot's.

    That is how a real private chat numbers them, and _show_panel's drift check
    is arithmetic on those ids — `newest_seen - panel_id`. Two separate
    counters would have made the difference meaningless and the drift branch
    untestable, which is precisely the branch that decides whether the panel is
    still on screen.
    """
    return next(_MESSAGE_IDS)


def update_from(user_id: int) -> dict:
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id},
                        "message_id": next_message_id(), "text": "/start"}}


def cmd_from(user_id: int, text: str) -> dict:
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id},
                        "message_id": next_message_id(), "text": text}}


def doc_from(user_id: int, file_id: str, file_name: str | None = None) -> dict:
    # file_name is genuinely optional in the Bot API — some clients omit it —
    # and when it is absent the bot falls back to the basename of what getFile
    # returned. Omitting it here is what makes the staged copies keep the test
    # fixtures' own names.
    doc = {"file_id": file_id}
    if file_name is not None:
        doc["file_name"] = file_name
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id},
                        "message_id": next_message_id(), "document": doc}}


def media_from(user_id: int, kind: str) -> dict:
    # `photo` is the one kind the Bot API delivers as a list (one entry per
    # generated thumbnail size); the rest are plain objects. Only the presence
    # of the key matters to the branch under test.
    payload = [{"file_id": "x"}] if kind == "photo" else {"file_id": "x"}
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id},
                        "message_id": next_message_id(), kind: payload}}


def cb_from(user_id: int, data: str, cb_id: str = "cb1") -> dict:
    # A callback_query keeps its sender at callback_query.from and its chat at
    # callback_query.message.chat — NOT at message.from. That difference is
    # what made every button press fail the allowlist before _identify.
    return {"callback_query": {"id": cb_id, "from": {"id": user_id},
                               "message": {"chat": {"id": user_id},
                                           "message_id": 7},
                               "data": data}}


# The control panel is the only thing that opens with the clapperboard and a
# bold pipeline name, so this identifies it without the tests having to track
# message ids through sends, edits, bumps and freezes.
PANEL_MARK = "🎬 <b>"


def panel_text(tg):
    """The most recent rendering of the control panel, whether sent or edited."""
    panels = [t for t in tg.screen if t.startswith(PANEL_MARK)]
    assert panels, "the bot never rendered a control panel"
    return panels[-1]


def reset_bot_state():
    """Clear every per-chat dict in bot.py between tests.

    One function rather than the three hand-written `.clear()` calls this
    replaced (2026-09-01). Those were duplicated across five setUp methods, so
    every dict added to bot.py had five places to be remembered in and none of
    them failed loudly when it was not — the control-panel dicts would have
    leaked message ids from one test into the next, which reads as a panel that
    mysteriously refuses to be created.
    """
    for name in ("_STATE", "_LOADED", "_PENDING", "_LAST_VALIDATE",
                 "_CONFIRM_WARNED", "_PANEL", "_PANEL_NOTE", "_LAST_SEEN",
                 "_FRAME", "_ANIM_PAUSE"):
        # getattr, not bot._STATE etc: a name that disappears from bot.py
        # should fail here loudly rather than be silently skipped.
        getattr(bot, name).clear()


class FakeTg:
    """Records calls instead of touching the network. `call()` only needs to
    answer getFile — /tryon, /result and the document-ingest flow (Task 7)
    are the only handlers that call it, and getFile is the only method any
    of them use."""
    def __init__(self):
        self.messages: list[str] = []
        self.documents: list[tuple] = []
        self.file_paths: dict[str, str] = {}   # file_id -> local path, set by the test
        # Buttons are recorded per message, so a test can assert what was
        # offered as well as what was said. `answered` records every
        # answerCallbackQuery: skipping it leaves the client spinning, so it
        # has to be observable.
        self.buttons: list[list[list[tuple[str, str]]] | None] = []
        self.answered: list[str] = []
        self.parse_modes: list[str | None] = []
        self.actions: list[str] = []
        # Edits are what the progress message and the control panel do —
        # recorded separately from `messages`, because the whole point of both
        # is that they do NOT send a new message on every change.
        self.edits: list[tuple[int, str]] = []
        self.edit_buttons: list[list[list[tuple[str, str]]] | None] = []
        # Every text that reached the chat, sent or edited, in order. `messages`
        # alone stopped being enough once the control panel arrived: the panel's
        # first render is a send and every one after it is an edit, so an
        # assertion against `messages` silently stops seeing the panel from the
        # second update onwards — which is most of a job.
        self.screen: list[str] = []
        self.deleted: list[int] = []
        # Message ids increment, as they do in a real chat: _show_panel's drift
        # check is arithmetic on ids, so a fake that returns a constant would
        # make that logic untestable — and would have hidden a panel that never
        # moves no matter how far up the chat it goes.
        # Flipped by a test to simulate the user deleting the message the bot
        # keeps editing; the real client returns False for that case.
        self.edit_ok = True
        # Set to a TgError to make the next edit raise, as a 429 does.
        self.edit_raises = None

    # The tags bot.py actually uses. Anything outside this set is either a
    # typo or unescaped user text, and Telegram rejects the whole message for
    # either — so the user sees nothing at all.
    ALLOWED_TAGS = ("<b>", "</b>", "<i>", "</i>", "<code>", "</code>",
                    "<pre>", "</pre>", "<blockquote expandable>", "<blockquote>",
                    "</blockquote>")

    def send_message(self, chat_id, text, *, buttons=None, parse_mode=None):
        self._check_markup(text, parse_mode)
        self.messages.append(text)
        self.screen.append(text)
        self.buttons.append(buttons)
        # Recorded so a test can assert that HTML-formatted bodies actually
        # declare it: HTML sent without parse_mode shows the raw <b> tags,
        # and HTML declared without escaping makes Telegram reject the whole
        # message so nothing arrives at all.
        self.parse_modes.append(parse_mode)
        return next_message_id()

    def send_chat_action(self, chat_id, action="typing"):
        self.actions.append(action)

    def edit_message(self, chat_id, message_id, text, *, buttons=None,
                     parse_mode=None):
        if self.edit_raises is not None:
            raise self.edit_raises
        self._check_markup(text, parse_mode)
        self.edits.append((message_id, text))
        self.screen.append(text)
        self.edit_buttons.append(buttons)
        return self.edit_ok

    def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)

    def _check_markup(self, text, parse_mode):
        """Fail here rather than let Telegram silently reject the message.

        Two failure modes, both invisible in production and both caught by
        running this on every message the suite produces:

        1. HTML in the body with no parse_mode — the user reads literal
           "<b>character</b>" instead of bold text.
        2. parse_mode=HTML with an unescaped `<` or an unknown tag — Telegram
           rejects the ENTIRE sendMessage, so nothing arrives. That is the same
           silence class as the NON_FILE_MEDIA bug, and no unit test would see
           it without this check.
        """
        looks_like_html = any(t in text for t in self.ALLOWED_TAGS)
        if parse_mode is None:
            assert not looks_like_html, (
                f"HTML tags sent without parse_mode: {text[:120]!r}")
            return
        assert parse_mode == "HTML", f"unexpected parse_mode {parse_mode!r}"
        stripped = text
        for tag in self.ALLOWED_TAGS:
            stripped = stripped.replace(tag, "")
        # &lt; / &gt; / &amp; are what _esc produces and are legal; bare
        # brackets are not.
        for entity in ("&lt;", "&gt;", "&amp;"):
            stripped = stripped.replace(entity, "")
        for ch in "<>":
            assert ch not in stripped, (
                f"unescaped {ch!r} in an HTML message — Telegram would reject "
                f"the whole thing: {text[:160]!r}")

    def answer_callback_query(self, callback_id, text=""):
        self.answered.append(callback_id)

    def callback_data(self):
        """Every callback_data offered so far, flattened.

        Spans sends AND edits (2026-09-01). The Run button lives on the control
        panel, which is sent once and edited from then on — so a version of
        this that read `self.buttons` alone reported that Run had never been
        offered on any job the user did not build in a single update.
        """
        return [d for rows in self.buttons + self.edit_buttons if rows
                for row in rows for _, d in row]

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

    def test_the_owner_pressing_a_button_is_allowed(self):
        # Buttons arrived 2026-08-31. Before _identify, allowed() read only
        # message.from, so this returned False and every press did nothing —
        # silently, which looks exactly like a broken bot.
        self.assertTrue(allowed(cb_from(ME, "run:ask"), ME))

    def test_a_stranger_pressing_a_button_is_refused(self):
        # The button that spends $0.99/hour is reachable by callback_query, so
        # the allowlist has to be as strict in this shape as in the other.
        self.assertFalse(allowed(cb_from(99999, "run:go:1"), ME))

    def test_a_button_press_from_a_group_is_refused(self):
        press = cb_from(ME, "run:ask")
        press["callback_query"]["message"]["chat"]["id"] = -1001234567890
        self.assertFalse(allowed(press, ME))

    def test_a_button_press_with_no_sender_is_refused(self):
        press = cb_from(ME, "run:ask")
        del press["callback_query"]["from"]
        self.assertFalse(allowed(press, ME))

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

    def test_every_refusal_names_send_as_file(self):
        # Verified 2026-08-31 that the iOS picker offers "Send as File", so the
        # refusal has an answer that costs one tap. Pointing at Files instead
        # sends the user off to save the picture first, and that friction is
        # what makes the rule feel arbitrary enough to argue with.
        for kind in bot.NON_FILE_MEDIA:
            with self.subTest(kind=kind):
                tg = FakeTg()
                bot.handle(tg, media_from(ME, kind), allowed_user_id=ME)
                self.assertIn("Send as File", tg.messages[0])

    def test_the_photo_refusal_quotes_the_image_measurement(self):
        # Images are damaged differently from video — downscaled AND converted
        # PNG->JPEG — so quoting the video bitrate figure at them, as the first
        # version of this branch did, is a wrong claim in a user-facing string.
        tg = FakeTg()
        bot.handle(tg, media_from(ME, "photo"), allowed_user_id=ME)
        self.assertIn("1445x2560", tg.messages[0])
        self.assertNotIn("6,603", tg.messages[0])

    def test_the_video_refusal_quotes_the_video_measurement(self):
        tg = FakeTg()
        bot.handle(tg, media_from(ME, "video"), allowed_user_id=ME)
        self.assertIn("6,603", tg.messages[0])
        self.assertNotIn("1445x2560", tg.messages[0])

    def test_an_unmeasured_kind_does_not_claim_a_measurement(self):
        # `voice` has no row in _RECOMPRESSION_COST. Saying "measured" about
        # something never measured is exactly how this spec's 20-30MB error
        # got in, so the absence has to stay visible rather than borrow a
        # neighbouring number.
        tg = FakeTg()
        bot.handle(tg, media_from(ME, "voice"), allowed_user_id=ME)
        self.assertNotIn("measured", tg.messages[0])
        self.assertIn("outside the File path", tg.messages[0])

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


class TestPipelineCommand(unittest.TestCase):
    """Added 2026-08-31 after the first real run from the phone: the user
    assembled a job, read the manifest, and wanted character-swap instead of
    motion — the other feature this repo has. The pipeline was a module
    constant, so the phone could reach every part of the flow except the
    choice of what the flow is.
    """

    def setUp(self):
        reset_bot_state()
        bot._LAST_VALIDATE.clear()
        bot._CONFIRM_WARNED.clear()
        self._orig_default = bot._DEFAULT_PIPELINE
        bot._DEFAULT_PIPELINE = "tryon-motion-enhance"
        # ROOT must move: handle() persists the draft on every update now, and
        # without this the suite wrote batch/tg-12345.draft.json into the real
        # repo — which a later run would then rehydrate, making these tests
        # pass or fail on a leftover from a previous run.
        self._orig_root = bot.ROOT
        self.root = Path(tempfile.mkdtemp())
        (self.root / "batch").mkdir()
        bot.ROOT = self.root

    def tearDown(self):
        bot._DEFAULT_PIPELINE = self._orig_default
        bot.ROOT = self._orig_root

    def test_bare_pipeline_lists_the_real_names(self):
        # The names are close enough that guessing fails: the user asked for
        # "swap-character-enhance", which is not any of them.
        tg = FakeTg()
        bot.handle(tg, cmd_from(ME, "/pipeline"), allowed_user_id=ME)
        self.assertIn("tryon-motion-enhance", tg.messages[0])
        self.assertIn("character-swap-enhance", tg.messages[0])
        self.assertNotIn("swap-character-enhance", tg.messages[0])

    def test_an_unknown_name_is_refused_and_changes_nothing(self):
        tg = FakeTg()
        bot.handle(tg, cmd_from(ME, "/pipeline swap-character-enhance"),
                   allowed_user_id=ME)
        self.assertIn("no pipeline called that", tg.messages[0])
        self.assertEqual(bot._job_for(ME).pipeline, "tryon-motion-enhance")

    def test_switching_keeps_slots_the_new_pipeline_still_uses(self):
        # Both of these need character/driver/outfit, so a switch between them
        # must not throw away material the user already labelled.
        job = bot._job_for(ME)
        job.slots.update({"character": Path("c.png"), "driver": Path("d.mp4"),
                          "outfit": Path("o.png")})
        with mock.patch.object(bot, "_maybe_show_manifest"):
            tg = FakeTg()
            bot.handle(tg, cmd_from(ME, "/pipeline tryon-character-swap-enhance"),
                       allowed_user_id=ME)
        self.assertEqual(bot._job_for(ME).pipeline, "tryon-character-swap-enhance")
        self.assertEqual(sorted(bot._job_for(ME).slots), ["character", "driver", "outfit"])

    def test_switching_drops_a_slot_the_new_pipeline_cannot_consume(self):
        # character-swap-enhance has no tryon stage, so an outfit would ride
        # into the manifest with nothing to consume it — a run that silently
        # ignores a file the user deliberately labelled.
        job = bot._job_for(ME)
        job.slots.update({"character": Path("c.png"), "driver": Path("d.mp4"),
                          "outfit": Path("o.png")})
        with mock.patch.object(bot, "_maybe_show_manifest"):
            tg = FakeTg()
            bot.handle(tg, cmd_from(ME, "/pipeline character-swap-enhance"),
                       allowed_user_id=ME)
        self.assertNotIn("outfit", bot._job_for(ME).slots)
        # _maybe_show_manifest is patched out, so the note is asserted where it
        # is written rather than where it would have been drawn.
        self.assertIn("dropped outfit", bot._PANEL_NOTE[ME])

    def test_switching_invalidates_the_cached_validation(self):
        # _LAST_VALIDATE is what /confirm trusts. Left set across a switch, a
        # /confirm would act on a validation that never ran against the
        # manifest about to be submitted.
        bot._LAST_VALIDATE[ME] = True
        with mock.patch.object(bot, "_maybe_show_manifest"):
            bot.handle(FakeTg(), cmd_from(ME, "/pipeline character-swap-enhance"),
                       allowed_user_id=ME)
        self.assertNotIn(ME, bot._LAST_VALIDATE)

    def test_the_listing_offers_a_button_per_other_pipeline(self):
        tg = FakeTg()
        bot.handle(tg, cmd_from(ME, "/pipeline"), allowed_user_id=ME)
        offered = tg.callback_data()
        self.assertIn(bot._CB_PIPE + "character-swap-enhance", offered)
        # Not the current one: a button that does nothing is worse than absent,
        # and the text already marks which is active.
        self.assertNotIn(bot._CB_PIPE + "tryon-motion-enhance", offered)

    def test_tapping_a_pipeline_button_switches_like_the_command_does(self):
        with mock.patch.object(bot, "_maybe_show_manifest"):
            tg = FakeTg()
            bot.handle(tg, cb_from(ME, bot._CB_PIPE + "character-swap-enhance"),
                       allowed_user_id=ME)
        self.assertEqual(bot._job_for(ME).pipeline, "character-swap-enhance")
        self.assertEqual(tg.answered, ["cb1"])

    def test_every_pipeline_name_fits_in_callback_data(self):
        # The 64-byte cap is on the DATA, and a single over-long button makes
        # the whole listing message fail to send — i.e. /pipeline would answer
        # nothing. A pipeline added later with a long name breaks this here
        # rather than silently on the phone.
        from tgbot.tgclient import Tg as RealTg
        RealTg.keyboard([[(n, bot._CB_PIPE + n)] for n in bot.PIPELINES])

    def test_switching_to_the_current_pipeline_is_a_no_op(self):
        tg = FakeTg()
        bot.handle(tg, cmd_from(ME, "/pipeline tryon-motion-enhance"),
                   allowed_user_id=ME)
        self.assertIn("already on", tg.messages[0])


class TestDraftPersistence(unittest.TestCase):
    """Added 2026-08-31 after this bit three times in one session.

    `_STATE` and `_PENDING` were memory-only, so an ordinary restart discarded
    every slot LABEL while leaving the staged FILES on disk. Material the user
    had already answered questions about became unreachable — no message, and
    no way to re-attach it short of sending it again. motion-bot.service is
    `Restart=always`, so on the VPS nobody has to restart it by hand for this
    to happen.

    "Restart" in these tests is exactly what the process loses: `_STATE`,
    `_PENDING` and `_LOADED` cleared, ROOT left alone.
    """

    def setUp(self):
        self._orig_root = bot.ROOT
        self.root = Path(tempfile.mkdtemp())
        (self.root / "batch").mkdir()
        bot.ROOT = self.root
        self._restart()
        self.driver = Probe(kind="video", width=1080, height=1920, duration_s=15.0,
                            bitrate_kbps=865, size_bytes=1_622_500)
        self.image = Probe(kind="image", width=1536, height=2720, duration_s=0.0,
                           bitrate_kbps=0, size_bytes=4_873_992)

    def tearDown(self):
        bot.ROOT = self._orig_root
        self._restart()

    def _restart(self):
        reset_bot_state()
        bot._LAST_VALIDATE.clear()
        bot._CONFIRM_WARNED.clear()

    def _seed(self):
        job = bot._job_for(ME)
        job.pipeline = "tryon-character-swap-enhance"
        job.slots.update({"character": Path("/s/c.png"), "driver": Path("/s/d.mp4")})
        job.probes.update({"character": self.image, "driver": self.driver})
        bot._PENDING[ME] = [(Path("/s/o.png"), self.image)]
        bot._save_draft(ME)

    def test_a_draft_survives_a_restart(self):
        self._seed()
        self._restart()
        bot._load_draft(ME)
        job = bot._STATE[ME]
        self.assertEqual(job.pipeline, "tryon-character-swap-enhance")
        self.assertEqual(sorted(job.slots), ["character", "driver"])
        self.assertEqual(job.slots["driver"], Path("/s/d.mp4"))
        # The probes must come back too: estimate_minutes and the manifest's
        # preset are derived from the driver's duration, so a job restored
        # without them is not restored.
        self.assertEqual(job.probes["driver"], self.driver)

    def test_the_unanswered_queue_survives_a_restart(self):
        # The queue is the half that mattered most in the real incident: a file
        # parked awaiting its label is staged on disk but reachable only
        # through _PENDING.
        self._seed()
        self._restart()
        bot._load_draft(ME)
        self.assertEqual(bot._PENDING[ME], [(Path("/s/o.png"), self.image)])

    def test_the_validation_verdict_is_deliberately_not_persisted(self):
        # /confirm treats a missing verdict as "never attempted" and re-runs
        # the free validate, re-sending the manifest before anything spends. A
        # cached pass carried across a restart is a verdict about a process
        # that no longer exists.
        self._seed()
        bot._LAST_VALIDATE[ME] = True
        bot._save_draft(ME)
        self._restart()
        bot._load_draft(ME)
        self.assertNotIn(ME, bot._LAST_VALIDATE)

    def test_clearing_the_state_removes_the_file(self):
        # /confirm clears _STATE after submitting. If the draft outlived it,
        # the next restart would resurrect a job that is already running.
        self._seed()
        self.assertTrue(bot._draft_path(ME).exists())
        bot._STATE.pop(ME)
        bot._PENDING.pop(ME)
        bot._save_draft(ME)
        self.assertFalse(bot._draft_path(ME).exists())

    def test_a_corrupt_draft_is_reported_and_does_not_crash(self):
        bot._draft_path(ME).write_text("{ not json", encoding="utf-8")
        with mock.patch.object(bot, "log") as logged:
            bot._load_draft(ME)
        self.assertNotIn(ME, bot._STATE)
        self.assertTrue(logged.called)
        self.assertIn("unreadable", logged.call_args[0][0])

    def test_a_corrupt_draft_is_moved_aside_not_left_to_be_deleted(self):
        """The destructive interaction, found 2026-08-31.

        _load_draft used to log and skip. Skipping leaves _STATE empty, and
        handle()'s `finally: _save_draft` then sees no state and unlinks the
        file — so the ONE record of the job was destroyed by the line after the
        one that failed to read it. Reconstructing a draft by hand from the
        staged files is possible (it was done once, earlier the same day) but
        only while the file still exists.
        """
        path = bot._draft_path(ME)
        path.write_text('{"pipeline": "tryon-motion-enhance", "slots": {', encoding="utf-8")
        tg = FakeTg()
        with mock.patch.object(bot, "log"):
            bot.handle(tg, cmd_from(ME, "/status"), allowed_user_id=ME)
        self.assertFalse(path.exists())
        salvaged = path.with_suffix(path.suffix + ".bad")
        self.assertTrue(salvaged.exists(), "the unreadable draft was destroyed")
        self.assertIn("slots", salvaged.read_text())
        # And the user is told, not only the log: otherwise /job answers
        # "nothing assembled yet" for a job they know they built.
        self.assertIn("could not be read", tg.messages[0])
        self.assertIn(salvaged.name, tg.messages[0])

    def test_a_draft_naming_an_unknown_pipeline_is_refused(self):
        # A pipeline renamed or removed between restarts would otherwise be
        # loaded and then fail much later, during manifest validation.
        self._seed()
        import json
        path = bot._draft_path(ME)
        payload = json.loads(path.read_text())
        payload["pipeline"] = "swap-character-enhance"
        path.write_text(json.dumps(payload), encoding="utf-8")
        self._restart()
        with mock.patch.object(bot, "log"):
            bot._load_draft(ME)
        self.assertNotIn(ME, bot._STATE)

    def test_handle_loads_the_draft_before_acting_on_the_update(self):
        # The end-to-end property: after a restart, the first message must see
        # the restored job rather than a fresh empty one.
        self._seed()
        self._restart()
        tg = FakeTg()
        bot.handle(tg, cmd_from(ME, "/pipeline"), allowed_user_id=ME)
        self.assertIn("tryon-character-swap-enhance", tg.messages[0])

    def test_handle_saves_even_when_the_handler_raises(self):
        # main() logs and continues past a failing update, so a draft change
        # made before the raise must not be lost.
        self._seed()
        self._restart()
        with mock.patch.object(bot, "_handle", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                bot.handle(FakeTg(), cmd_from(ME, "/status"), allowed_user_id=ME)
        self.assertTrue(bot._draft_path(ME).exists())

    def test_a_stranger_never_creates_a_draft(self):
        bot.handle(FakeTg(), cmd_from(99999, "/status"), allowed_user_id=ME)
        self.assertFalse(bot._draft_path(99999).exists())


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
        reset_bot_state()
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
        reset_bot_state()
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

    # ---- the control panel (2026-09-01) ------------------------------------
    #
    # One message per chat, edited in place. Everything below guards a
    # property that has no other enforcement: the suite was fully green
    # immediately after the panel was written and before any of these existed,
    # which says only that nothing OLD broke.

    def _send_driver(self):
        with mock.patch("tgbot.bot.probe", return_value=self.driver_probe):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)

    def test_a_second_file_edits_the_panel_rather_than_sending_another(self):
        """The whole reason the panel exists: no growing pile of fragments."""
        probe_fn = self._probe_for({
            self.driver_path.name: self.driver_probe,
            self.character_path.name: self.image_probe,
        })
        with mock.patch("tgbot.bot.probe", side_effect=probe_fn):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
            bot.handle(self.tg, doc_from(ME, "character-id"), allowed_user_id=ME)
        sent = [t for t in self.tg.messages if t.startswith(PANEL_MARK)]
        self.assertEqual(len(sent), 1, "a second panel was SENT, not edited")
        self.assertTrue(any(t.startswith(PANEL_MARK) for _, t in self.tg.edits))

    def test_the_panel_is_moved_down_once_it_has_drifted_off_screen(self):
        """Editing is silent and preferred — but not at any distance.

        A panel five messages up is a panel the user has to scroll to find,
        which is the exact complaint the panel was built to answer.
        """
        self._send_driver()
        first = bot._PANEL[ME]
        bot._LAST_SEEN[ME] = first + bot._PANEL_DRIFT_MAX + 1
        bot._show_panel(self.tg, ME)
        self.assertIn(first, self.tg.deleted)
        self.assertNotEqual(bot._PANEL[ME], first)
        # Exactly one panel survives: the old one is deleted, not just orphaned
        # with its buttons still live.
        self.assertEqual(len(self.tg.deleted), 1)

    def test_a_panel_the_user_deleted_is_rebuilt_rather_than_edited_into_the_void(self):
        self._send_driver()
        first = bot._PANEL[ME]
        self.tg.edit_ok = False          # what the real client returns for a gone message
        bot._show_panel(self.tg, ME)
        self.assertNotEqual(bot._PANEL[ME], first)

    def test_job_bumps_the_panel_to_the_bottom(self):
        """/job is an explicit "show me now" — a silent edit above would read
        as the command having done nothing."""
        self._send_driver()
        first = bot._PANEL[ME]
        bot.handle(self.tg, cmd_from(ME, "/job"), allowed_user_id=ME)
        self.assertIn(first, self.tg.deleted)
        self.assertNotEqual(bot._PANEL[ME], first)

    def test_run_is_not_offered_until_the_manifest_has_actually_validated(self):
        """Stricter than the screen it replaced, which offered Run beside a
        manifest that had just failed and leaned on _do_confirm to refuse."""
        job = bot._job_for(ME)
        job.slots.update({"character": Path("c.png"), "outfit": Path("o.png"),
                          "driver": Path("d.mp4")})
        flat = lambda rows: [d for row in rows for _, d in row]
        bot._LAST_VALIDATE.pop(ME, None)
        self.assertNotIn(bot._CB_RUN_ASK, flat(bot._panel_buttons(ME, job)))
        bot._LAST_VALIDATE[ME] = False
        self.assertNotIn(bot._CB_RUN_ASK, flat(bot._panel_buttons(ME, job)))
        bot._LAST_VALIDATE[ME] = True
        self.assertIn(bot._CB_RUN_ASK, flat(bot._panel_buttons(ME, job)))

    def test_confirm_freezes_the_panel_with_the_inputs_and_no_buttons(self):
        """The transcript invariant, under the new shape.

        Nothing may spend $0.99/hour without the exact inputs it spent on
        being in the transcript. An edited message keeps only its latest
        version, so the panel has to STOP being edited at the moment money is
        committed — otherwise the record of what was submitted would be
        overwritten by whatever the user assembled next.
        """
        self._confirm_and_start()
        frozen = panel_text(self.tg)
        self.assertIn("submitted", frozen)
        for name in ("character.jpg", "outfit.jpg", "driver.mp4"):
            self.assertIn(name, frozen)
        # The keyboard goes with it: a Run button on an already-running job is
        # a second line of defence behind _run_token, not a decoration.
        index = [i for i, (_, t) in enumerate(self.tg.edits)
                 if "submitted" in t][-1]
        self.assertIsNone(self.tg.edit_buttons[index])
        # And it is untracked, so nothing can edit that record afterwards.
        self.assertNotIn(ME, bot._PANEL)

    def test_a_later_job_cannot_overwrite_the_frozen_record(self):
        """The property the freeze exists for, exercised end to end."""
        self._confirm_and_start()
        frozen_id = [mid for mid, t in self.tg.edits if "submitted" in t][-1]
        touched = sum(1 for mid, _ in self.tg.edits if mid == frozen_id)
        with mock.patch("tgbot.bot.drain_running", return_value=False):
            self._send_driver()
        # The next job builds its own panel and never reaches back to this id.
        self.assertEqual(sum(1 for mid, _ in self.tg.edits if mid == frozen_id),
                         touched)
        self.assertNotEqual(bot._PANEL.get(ME), frozen_id)

    def test_clear_deletes_the_panel_rather_than_leaving_it_stale(self):
        self._send_driver()
        first = bot._PANEL[ME]
        with mock.patch("tgbot.bot.drain_running", return_value=False):
            bot.handle(self.tg, cb_from(ME, bot._CB_CLEAR_GO), allowed_user_id=ME)
        self.assertIn(first, self.tg.deleted)
        self.assertNotIn(ME, bot._PANEL)

    def test_the_panel_id_survives_a_restart(self):
        """motion-bot.service is Restart=always. A bot that came back without
        the id would send a SECOND panel while the first sat above it with
        live buttons — two keyboards for one job."""
        self._send_driver()
        saved = bot._PANEL[ME]
        for holder in (bot._STATE, bot._PENDING, bot._PANEL, bot._PANEL_NOTE,
                       bot._LOADED):
            holder.clear()
        self.assertIsNone(bot._load_draft(ME))
        self.assertEqual(bot._PANEL[ME], saved)

    def test_a_draft_written_before_the_panel_existed_still_loads(self):
        """Forward compatibility, and the reason `panel` is read with .get().

        A draft from yesterday's bot has no "panel" key. Refusing to load an
        otherwise perfectly good job over a missing COSMETIC field would set it
        aside as corrupt — the single outcome _load_draft exists to prevent.
        """
        self._send_driver()
        path = bot._draft_path(ME)
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key in ("panel", "note", "last_seen"):
            payload.pop(key, None)
        path.write_text(json.dumps(payload), encoding="utf-8")
        for holder in (bot._STATE, bot._PENDING, bot._PANEL, bot._LOADED):
            holder.clear()
        self.assertIsNone(bot._load_draft(ME), "an older draft was set aside")
        self.assertIn("driver", bot._STATE[ME].slots)

    def test_the_slot_question_stacks_its_buttons_and_marks_what_is_filled(self):
        """Three buttons on one row have their labels truncated on a phone,
        and an unmarked role gives no warning that tapping it overwrites."""
        probe_fn = self._probe_for({
            self.character_path.name: self.image_probe,
            self.outfit_path.name: self.image_probe,
        })
        with mock.patch("tgbot.bot.probe", side_effect=probe_fn):
            bot.handle(self.tg, doc_from(ME, "character-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "character"), allowed_user_id=ME)
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
        rows = [b for b in self.tg.buttons if b
                and any(d.startswith(bot._CB_SLOT) for row in b for _, d in row)][-1]
        self.assertTrue(all(len(row) <= 2 for row in rows), rows)
        labels = {d: label for row in rows for label, d in row}
        self.assertIn(bot.ICON_OK, labels[bot._CB_SLOT + "character"])
        self.assertNotIn(bot.ICON_OK, labels[bot._CB_SLOT + "outfit"])

    def test_successive_ticks_actually_move_the_progress_message(self):
        """Otherwise the 2s poll is 25x the API calls for no visible change.

        Every edit would be swallowed as "message is not modified", and the
        cost of the fast cadence would buy nothing at all.
        """
        self._confirm_and_start()
        with mock.patch("tgbot.bot.drain_running", return_value=True):
            bot.tick_progress(self.tg, ME)
            bot.tick_progress(self.tg, ME)
        self.assertNotEqual(self.tg.edits[-2][1], self.tg.edits[-1][1])

    def test_a_throttled_edit_pauses_the_animation_but_never_delivery(self):
        """Telegram's own backoff is honoured, and it cannot hold up a result.

        A cosmetic rate limit delaying the delivery of a finished render would
        be the animation costing the user the thing they paid for.
        """
        from tgbot.tgclient import TgError
        self._confirm_and_start()
        self.tg.edit_raises = TgError("Too Many Requests", retry_after=42.0)
        with mock.patch("tgbot.bot.drain_running", return_value=True):
            bot.tick_progress(self.tg, ME)
        self.assertGreater(bot._ANIM_PAUSE[ME], time.time() + 40)
        # Still paused: a second tick must not argue with flood control.
        edits = len(self.tg.edits)
        with mock.patch("tgbot.bot.drain_running", return_value=True):
            bot.tick_progress(self.tg, ME)
        self.assertEqual(len(self.tg.edits), edits)
        # But the finished job is delivered regardless of the pause.
        self.tg.edit_raises = None
        with mock.patch("tgbot.bot.drain_running", return_value=False), \
             mock.patch("tgbot.bot.deliver_result") as deliver:
            bot.tick_progress(self.tg, ME)
        deliver.assert_called_once()

    def test_a_deleted_progress_message_is_rebuilt_with_its_new_id(self):
        self._confirm_and_start()
        before = json.loads(bot._progress_path(ME).read_text())["message_id"]
        self.tg.edit_ok = False
        with mock.patch("tgbot.bot.drain_running", return_value=True):
            bot.tick_progress(self.tg, ME)
        after = json.loads(bot._progress_path(ME).read_text())["message_id"]
        self.assertNotEqual(after, before)

    def test_a_video_fills_the_driver_slot_without_asking(self):
        with mock.patch("tgbot.bot.probe", return_value=self.driver_probe):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        self.assertIn("driver", panel_text(self.tg))
        self.assertNotIn("which", panel_text(self.tg).lower())
        self.assertEqual(bot._STATE[ME].slots.get("driver"), self.staged("driver.mp4"))

    def test_an_image_is_asked_about_and_the_answer_fills_the_slot(self):
        with mock.patch("tgbot.bot.probe", return_value=self.image_probe):
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
        self.assertIn("which", self.tg.messages[-1].lower())
        self.assertNotIn("outfit", bot._STATE[ME].slots)

        bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME)
        self.assertEqual(bot._STATE[ME].slots.get("outfit"), self.staged("outfit.jpg"))
        self.assertIn("outfit", panel_text(self.tg))

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
        # `screen`, not `messages`: the panel is sent once and edited after.
        joined = "\n".join(self.tg.screen)
        # Asserts the REVIEW CONTENT, not the YAML syntax it used to be echoed
        # in (changed 2026-08-31 with _manifest_summary). The rule this guards
        # is "nothing may spend $0.99/hour without the exact inputs it spent on
        # being in the transcript", so what has to be present is the pipeline,
        # every role, and the file name in each — not the word "runs:".
        self.assertIn("tryon-motion-enhance", joined)
        for role, name in (("character", "character.jpg"), ("outfit", "outfit.jpg"),
                           ("driver", "driver.mp4")):
            self.assertIn(role, joined)
            self.assertIn(name, joined)
        self.assertIn("$0.99/hour", joined)
        # And the Run button, since that is now the offered way to spend.
        self.assertIn(bot._CB_RUN_ASK, self.tg.callback_data())

    def test_the_slot_question_offers_a_button_per_role(self):
        with mock.patch("tgbot.bot.probe", return_value=self.image_probe):
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
        offered = self.tg.callback_data()
        for role in bot._askable_roles(bot._job_for(ME).pipeline):
            self.assertIn(bot._CB_SLOT + role, offered)

    def test_tapping_a_slot_button_fills_the_slot_like_typing_does(self):
        # _answer_slot is the single body both paths run; this pins that the
        # button path actually reaches it, including finding I7's overwrite
        # handling inside _fill_slot.
        with mock.patch("tgbot.bot.probe", return_value=self.image_probe):
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
        bot.handle(self.tg, cb_from(ME, bot._CB_SLOT + "character"),
                   allowed_user_id=ME)
        # The file sent was outfit.jpg and the button tapped was `character`,
        # and the slot holds outfit.jpg: the label is the user's answer, never
        # inferred from the filename. That is job.slot_for's refusal to guess,
        # and the button path must not quietly reintroduce a name heuristic.
        self.assertEqual(bot._STATE[ME].slots["character"],
                         self.staged("outfit.jpg"))
        self.assertNotIn(ME, bot._PENDING)
        # Acknowledged, or the client spins on the button forever.
        self.assertEqual(self.tg.answered, ["cb1"])

    def test_a_slot_button_tapped_with_nothing_queued_says_so(self):
        # Telegram keeps old keyboards tappable forever, with no expiry and no
        # way to make one single-use, so this arrives in normal use.
        bot.handle(self.tg, cb_from(ME, bot._CB_SLOT + "character"),
                   allowed_user_id=ME)
        self.assertIn("no file is waiting", self.tg.messages[-1])
        self.assertEqual(self.tg.answered, ["cb1"])

    def test_the_run_button_needs_a_second_tap_before_it_spends(self):
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
            bot.handle(self.tg, cb_from(ME, bot._CB_RUN_ASK), allowed_user_id=ME)
            start_drain.assert_not_called()     # first tap only asks
            self.assertIn("$0.99/hour", self.tg.messages[-1])
            token = bot._run_token(ME)
            bot.handle(self.tg, cb_from(ME, bot._CB_RUN_GO + token),
                       allowed_user_id=ME)
            start_drain.assert_called_once()
            self.assertIs(start_drain.call_args.kwargs["dry_run"], False)

    def test_a_run_button_from_a_changed_job_cannot_spend(self):
        """The stale-keyboard guard, and the reason _run_token exists.

        Telegram never expires an inline keyboard. Without the token, a Run
        button offered for one manifest stays live after the job changes, and
        tapping it would rent a $0.99/hour GPU for inputs the user never
        reviewed.
        """
        with mock.patch("tgbot.bot.start_drain") as start_drain, \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
            stale = bot._CB_RUN_GO + "1"       # never this manifest's mtime_ns
            bot.handle(self.tg, cb_from(ME, stale), allowed_user_id=ME)
            start_drain.assert_not_called()
        self.assertIn("changed since that button was sent", self.tg.messages[-1])

    def test_cancelling_spends_nothing(self):
        with mock.patch("tgbot.bot.start_drain") as start_drain:
            bot.handle(self.tg, cb_from(ME, bot._CB_RUN_NO), allowed_user_id=ME)
            start_drain.assert_not_called()
        self.assertIn("cancelled", self.tg.messages[-1])

    def test_an_unknown_callback_is_answered_not_ignored(self):
        bot.handle(self.tg, cb_from(ME, "nonsense:42"), allowed_user_id=ME)
        self.assertIn("older version", self.tg.messages[-1])
        self.assertEqual(self.tg.answered, ["cb1"])

    def test_every_command_in_the_menu_actually_answers(self):
        """Drift guard for BOT_COMMANDS, in the spirit of make check-job-types.

        setMyCommands publishes these names to Telegram, so they appear as
        tappable suggestions. One that no branch handles falls through to the
        text handlers, matches nothing, and answers NOTHING — the same silence
        that NON_FILE_MEDIA was added to fix, except advertised by the bot
        itself.
        """
        for name, _desc in bot.BOT_COMMANDS:
            with self.subTest(command=name):
                tg = FakeTg()
                bot.handle(tg, cmd_from(ME, f"/{name}"), allowed_user_id=ME)
                self.assertTrue(tg.messages, f"/{name} answered nothing")

    # ---- /job, re-label, /clear, /again (added 2026-08-31) ---------------

    def test_job_with_nothing_assembled_says_so(self):
        bot.handle(self.tg, cmd_from(ME, "/job"), allowed_user_id=ME)
        self.assertIn("Nothing assembled yet", self.tg.messages[-1])

    def test_job_names_what_is_filled_and_what_is_missing(self):
        # The gap this closes: on 2026-08-31 a slot label went missing and the
        # only way to find out what the bot held was a screenshot of the chat.
        with mock.patch("tgbot.bot.probe", return_value=self.driver_probe):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        bot.handle(self.tg, cmd_from(ME, "/job"), allowed_user_id=ME)
        body = self.tg.messages[-1]
        # Every role of the pipeline appears, filled or not: listing only what
        # is present hides the empty required slot, which is the single thing
        # the user most needs to see.
        self.assertIn(f"{bot.ICON_OK} {bot.ROLE_ICON['driver']} driver", body)
        for role in ("character", "outfit"):
            self.assertIn(f"{bot.ICON_EMPTY} {bot.ROLE_ICON[role]} {role}", body)
        # An optional slot is marked as such, so an empty one is not mistaken
        # for something still owed.
        self.assertIn("background — optional", body)
        # The filename lives in the collapsed detail block, not the headline.
        self.assertIn("driver.mp4", body)
        self.assertEqual(self.tg.parse_modes[-1], bot.PARSE_HTML)

    def test_job_lists_files_still_waiting_for_a_label(self):
        with mock.patch("tgbot.bot.probe", return_value=self.image_probe):
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
        bot.handle(self.tg, cmd_from(ME, "/job"), allowed_user_id=ME)
        self.assertIn("waiting for a label", self.tg.messages[-1])
        self.assertIn("outfit.jpg", self.tg.messages[-1])

    def test_job_offers_a_relabel_button_per_filled_slot(self):
        with mock.patch("tgbot.bot.probe", return_value=self.driver_probe):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        self.tg.buttons.clear()
        bot.handle(self.tg, cmd_from(ME, "/job"), allowed_user_id=ME)
        offered = self.tg.callback_data()
        self.assertIn(bot._CB_REDO + "driver", offered)
        self.assertIn(bot._CB_CLEAR_ASK, offered)

    def test_relabelling_moves_a_file_back_to_the_queue_and_re_asks(self):
        with mock.patch("tgbot.bot.probe", return_value=self.image_probe):
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
        bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME)
        self.assertIn("outfit", bot._STATE[ME].slots)
        bot.handle(self.tg, cb_from(ME, bot._CB_REDO + "outfit"), allowed_user_id=ME)
        self.assertNotIn("outfit", bot._STATE[ME].slots)
        self.assertEqual(len(bot._PENDING[ME]), 1)
        self.assertIn("Which slot is this?", self.tg.messages[-1])
        # And it may be answered straight into a different role.
        bot.handle(self.tg, cb_from(ME, bot._CB_SLOT + "character"), allowed_user_id=ME)
        self.assertEqual(bot._STATE[ME].slots["character"],
                         self.staged("outfit.jpg"))

    def test_relabelling_invalidates_the_cached_validation(self):
        # _LAST_VALIDATE is what /confirm trusts; the job just changed.
        with mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
        self.assertTrue(bot._LAST_VALIDATE.get(ME))
        bot.handle(self.tg, cb_from(ME, bot._CB_REDO + "outfit"), allowed_user_id=ME)
        self.assertNotIn(ME, bot._LAST_VALIDATE)

    def test_relabelling_a_video_explains_instead_of_re_asking(self):
        # slot_for gives a video to `driver` structurally, so the question
        # would have exactly one answer.
        with mock.patch("tgbot.bot.probe", return_value=self.driver_probe):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        bot.handle(self.tg, cb_from(ME, bot._CB_REDO + "driver"), allowed_user_id=ME)
        self.assertIn("only be the driver", self.tg.messages[-1])
        self.assertIn("driver", bot._STATE[ME].slots)   # unchanged

    def test_relabelling_an_empty_slot_says_so(self):
        bot.handle(self.tg, cb_from(ME, bot._CB_REDO + "outfit"), allowed_user_id=ME)
        self.assertIn("nothing is in outfit", self.tg.messages[-1])

    def test_clear_asks_before_deleting_anything(self):
        with mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
        bot.handle(self.tg, cmd_from(ME, "/clear"), allowed_user_id=ME)
        self.assertIn("Delete this job", self.tg.messages[-1])
        self.assertIn(bot._CB_CLEAR_GO, self.tg.callback_data())
        self.assertIn(ME, bot._STATE)                   # nothing gone yet
        self.assertTrue(self.staged("driver.mp4").exists())

    def test_confirming_clear_deletes_the_staged_files_and_the_draft(self):
        with mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
        with mock.patch("tgbot.bot.drain_running", return_value=False):
            bot.handle(self.tg, cb_from(ME, bot._CB_CLEAR_GO), allowed_user_id=ME)
        self.assertNotIn(ME, bot._STATE)
        self.assertFalse(self.staged("driver.mp4").exists())
        self.assertFalse(bot._draft_path(ME).exists())
        self.assertIn("cleared", self.tg.messages[-1])

    def test_clear_is_refused_while_a_drain_is_running(self):
        """The staged files ARE the running job's inputs.

        The manifest points straight at batch/tg-staging/<chat>/, so deleting
        them mid-drain breaks a run already being paid for at $0.99/hour.
        """
        with mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
        with mock.patch("tgbot.bot.drain_running", return_value=True):
            bot.handle(self.tg, cb_from(ME, bot._CB_CLEAR_GO), allowed_user_id=ME)
        self.assertIn("a drain is running", self.tg.messages[-1])
        self.assertTrue(self.staged("driver.mp4").exists())
        self.assertIn(ME, bot._STATE)

    def test_clear_with_nothing_to_clear_does_not_ask(self):
        bot.handle(self.tg, cmd_from(ME, "/clear"), allowed_user_id=ME)
        self.assertIn("nothing to clear", self.tg.messages[-1])

    def test_confirm_records_the_job_so_again_can_reuse_it(self):
        with mock.patch("tgbot.bot.start_drain"), \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
        self.assertNotIn(ME, bot._STATE)                # submitted, cleared
        self.assertFalse(bot._draft_path(ME).exists())  # cannot be resurrected
        self.assertTrue(bot._last_path(ME).exists())    # but is repeatable

    def test_again_rebuilds_the_last_job_from_the_same_files(self):
        with mock.patch("tgbot.bot.start_drain"), \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "/again"), allowed_user_id=ME)
        self.assertEqual(sorted(bot._STATE[ME].slots),
                         ["character", "driver", "outfit"])
        # And the probes come back, or the manifest's preset and the estimate
        # would both be wrong.
        self.assertEqual(bot._STATE[ME].probes["driver"], self.driver_probe)

    def test_again_refuses_to_overwrite_a_job_in_progress(self):
        with mock.patch("tgbot.bot.start_drain"), \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
        with mock.patch("tgbot.bot.probe", return_value=self.driver_probe):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        bot.handle(self.tg, cmd_from(ME, "/again"), allowed_user_id=ME)
        self.assertIn("job in progress", self.tg.messages[-1])

    def test_again_names_the_files_that_have_gone_missing(self):
        # `make batch-clean` and /clear both remove staged files, so a recorded
        # job can outlive its inputs. "some files are missing" is not
        # actionable; the role and the filename are.
        with mock.patch("tgbot.bot.start_drain"), \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
        self.staged("driver.mp4").unlink()
        bot.handle(self.tg, cmd_from(ME, "/again"), allowed_user_id=ME)
        self.assertIn("no longer on disk", self.tg.messages[-1])
        self.assertIn("driver.mp4", self.tg.messages[-1])
        self.assertNotIn(ME, bot._STATE)

    def test_again_with_nothing_recorded_says_so(self):
        bot.handle(self.tg, cmd_from(ME, "/again"), allowed_user_id=ME)
        self.assertIn("nothing to repeat", self.tg.messages[-1])

    def test_a_low_bitrate_driver_is_flagged_on_arrival_and_at_the_gate(self):
        """The gap spec section 4.3 left open: it measured but never judged.

        On 2026-08-31 a driver arrived at 865 kbps for 1080x1920 — 15x below
        the file it stood in for — and the bot printed the number and accepted
        it in silence. Sent as a File, so Telegram had not touched it: the rule
        guarantees the channel did no damage, not that the bytes were good.
        """
        weak = Probe(kind="video", width=1080, height=1920, duration_s=15.0,
                     bitrate_kbps=865, size_bytes=1_622_500)
        with mock.patch("tgbot.bot.probe", return_value=weak):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        self.assertIn("low bitrate", panel_text(self.tg))
        # And again once the job is complete: the panel is one message, so the
        # warning has to still be on it at the moment the money decision is
        # made — it cannot rely on an earlier message still being in view.
        with mock.patch("tgbot.bot.probe", return_value=self.image_probe), \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            bot.handle(self.tg, doc_from(ME, "character-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "character"), allowed_user_id=ME)
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME)
        self.assertIn("low bitrate", panel_text(self.tg))
        self.assertIn("$0.99/hour", panel_text(self.tg))

    def test_a_normal_driver_is_not_flagged(self):
        # The threshold has to leave real material alone: s1.mp4, the lowest
        # legitimate driver in the 64-file survey, measures 1397 kbps/Mpx.
        with mock.patch("tgbot.bot.probe", return_value=self.driver_probe):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        self.assertNotIn("low bitrate", self.tg.messages[-1])


    def test_a_filename_with_a_bracket_cannot_break_the_message(self):
        """_esc is the guard, and this is why it is applied to everything.

        A `<` reaching Telegram inside parse_mode=HTML makes it reject the
        WHOLE sendMessage, so the user gets nothing. _safe_name already strips
        brackets from staged names, so this is defence in depth rather than a
        live hole — and the point is that it stays defended when _safe_name
        changes.
        """
        job = bot._job_for(ME)
        job.slots["character"] = Path("we<ird>.png")
        job.probes["character"] = self.image_probe
        self.assertNotIn("<ird", bot._panel_text(ME, job))
        # FakeTg._check_markup would have failed the send; assert the escape
        # directly too, so the reason is visible when this breaks.
        self.assertIn("&lt;ird&gt;", bot._details_block(ME, job))

    def test_uploading_a_file_shows_a_chat_action_before_the_slow_part(self):
        # ffprobe on a 25MB video plus the staging copy takes long enough that
        # a silent bot reads as a stuck one.
        with mock.patch("tgbot.bot.probe", return_value=self.driver_probe):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        self.assertIn("upload_document", self.tg.actions)

    def test_validating_shows_a_chat_action(self):
        with mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
        self.assertIn("typing", self.tg.actions)

    def test_the_review_screen_shows_a_warning_outside_the_collapsed_block(self):
        # On /job the detail is one tap away; here it must not be. This is the
        # last thing read before $0.99/hour is committed, and anything needing
        # a tap to reveal is something that gets skipped.
        weak = Probe(kind="video", width=1080, height=1920, duration_s=15.0,
                     bitrate_kbps=865, size_bytes=1_622_500)
        with mock.patch("tgbot.bot.probe", return_value=weak):
            bot.handle(self.tg, doc_from(ME, "driver-id"), allowed_user_id=ME)
        with mock.patch("tgbot.bot.probe", return_value=self.image_probe), \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            bot.handle(self.tg, doc_from(ME, "character-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "character"), allowed_user_id=ME)
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME)
        review = panel_text(self.tg)
        before_block = review.split("<blockquote")[0]
        self.assertIn("low bitrate", before_block)

    def test_the_fix_buttons_are_two_per_row(self):
        # Four stacked full-width buttons pushed the message they belong to off
        # the top of a phone screen — that was the original complaint.
        with mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
        rows = bot._fix_buttons(bot._STATE[ME])
        self.assertTrue(all(len(r) <= 2 for r in rows), rows)
        self.assertEqual(rows[-1][0][1], bot._CB_CLEAR_ASK)


    # ---- the completion poll (added 2026-08-31) --------------------------

    def _confirm_and_start(self):
        with mock.patch("tgbot.bot.start_drain"), \
             mock.patch("tgbot.bot.drain_running", return_value=False):
            self._fill_required_slots()
            bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)

    def test_confirm_records_a_progress_message_to_keep_editing(self):
        self._confirm_and_start()
        self.assertTrue(bot._progress_path(ME).exists())
        import json
        payload = json.loads(bot._progress_path(ME).read_text())
        # The stage list is captured HERE, from the pipeline, because the
        # journal only records stages that have already begun — without a
        # denominator the bar would read 1/1 at the first stage and never move.
        self.assertEqual(payload["stages"], ["tryon", "motion", "enhance"])

    def test_a_tick_edits_the_message_instead_of_sending_a_new_one(self):
        """The whole point: one message, updated in place, never a stream.

        A 5-minute throttle sat here briefly and was removed on the user's
        instruction — an edit sends no notification and adds no message to the
        chat, so there is nothing to be spammed by. Every poll re-edits.
        """
        self._confirm_and_start()
        before = len(self.tg.messages)
        edits_before = len(self.tg.edits)
        with mock.patch("tgbot.bot.drain_running", return_value=True):
            bot.tick_progress(self.tg, ME)
            bot.tick_progress(self.tg, ME)
        self.assertEqual(len(self.tg.messages), before)   # nothing new sent
        # Sliced from `edits_before`: confirming the job now freezes the panel,
        # which is itself an edit, so a count over the whole list would be
        # asserting about the panel rather than about the progress message.
        ticked = self.tg.edits[edits_before:]
        self.assertEqual(len(ticked), 2)
        self.assertEqual(len({mid for mid, _ in ticked}), 1)  # the same one
        self.assertTrue(bot._progress_path(ME).exists())

    def test_when_the_drain_ends_the_result_is_delivered_unasked(self):
        """The gap deliver_result's own docstring described.

        It said: "nothing in this bot polls a drain to completion and fires a
        callback when it finishes ... This is the reachable close-the-loop hook
        until a completion poll exists." Until now the user had to remember to
        type /result, with nothing telling them the job had finished — or
        failed.
        """
        self._confirm_and_start()
        with mock.patch("tgbot.bot.drain_running", return_value=False), \
             mock.patch("tgbot.bot.deliver_result") as deliver:
            bot.tick_progress(self.tg, ME)
            deliver.assert_called_once()
        # And the tracking file is gone, so it cannot deliver twice.
        self.assertFalse(bot._progress_path(ME).exists())

    def test_a_finished_drain_is_never_delivered_twice(self):
        self._confirm_and_start()
        with mock.patch("tgbot.bot.drain_running", return_value=False), \
             mock.patch("tgbot.bot.deliver_result") as deliver:
            bot.tick_progress(self.tg, ME)
            bot.tick_progress(self.tg, ME)
            self.assertEqual(deliver.call_count, 1)

    def test_the_progress_file_is_dropped_if_delivery_raises(self):
        # Removed BEFORE delivery on purpose: a raising deliver_result must not
        # leave a file that re-delivers everything on the next tick, every 50
        # seconds, for as long as the bot runs.
        self._confirm_and_start()
        with mock.patch("tgbot.bot.drain_running", return_value=False), \
             mock.patch("tgbot.bot.deliver_result", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                bot.tick_progress(self.tg, ME)
        self.assertFalse(bot._progress_path(ME).exists())

    def test_a_tick_with_no_drain_tracked_does_nothing(self):
        bot.tick_progress(self.tg, ME)
        self.assertEqual(self.tg.edits, [])

    def test_an_unreadable_progress_file_is_dropped_and_logged(self):
        bot._progress_path(ME).write_text("{ not json", encoding="utf-8")
        with mock.patch.object(bot, "log") as logged:
            bot.tick_progress(self.tg, ME)
        self.assertFalse(bot._progress_path(ME).exists())
        self.assertTrue(logged.called)

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
        reset_bot_state()
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
        # 12 hex chars, not 64 (shortened 2026-08-31): the full digest took two
        # lines on a phone and pushed resolution/bitrate/size — the numbers
        # that actually reveal recompression — off the top of the message.
        self.assertIn(hashlib.sha256(b"d").hexdigest()[:12], self.tg.messages[0])
        self.assertNotIn(hashlib.sha256(b"d").hexdigest(), self.tg.messages[0])
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
        self.assertIn("replaced the previous driver", panel_text(self.tg))

    def test_answering_a_filled_role_says_it_is_replacing_it(self):
        # Finding I7: this path pops the queue head AND overwrites the slot,
        # so the displaced file is unrecoverable — it must at least be named.
        with mock.patch("tgbot.bot.probe", return_value=self.image_probe):
            bot.handle(self.tg, doc_from(ME, "outfit-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME)
            bot.handle(self.tg, doc_from(ME, "character-id"), allowed_user_id=ME)
            bot.handle(self.tg, cmd_from(ME, "outfit"), allowed_user_id=ME)
        self.assertIn("replaced the previous outfit", panel_text(self.tg))

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
        # Reworded 2026-08-31: /status now falls through to the assembly state
        # instead of dead-ending on "nothing started", which is true but
        # useless while a job is being put together — the state /status is
        # most often asked in.
        self.assertIn("Nothing running", self.tg.messages[-1])
        self.assertIn("Nothing assembled yet", self.tg.messages[-1])

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


class TestNoDuplicateDefinitions(unittest.TestCase):
    """A file cannot define the same name twice and still mean one thing.

    Added 2026-08-31 after a patch inserted an entire block of functions that
    was already present. Python simply re-binds each name, so the LATER
    definition wins and every test kept passing — 151 duplicated lines,
    including a second copy of the job-clearing logic, invisible to the whole
    suite. Editing the earlier copy would have changed nothing at runtime,
    which is the kind of bug that costs an afternoon.
    """

    MODULES = ["tgbot/bot.py", "tgbot/job.py", "tgbot/ingest.py",
               "tgbot/run.py", "tgbot/tgclient.py"]

    def test_no_module_reaches_for_a_custom_emoji_entity(self):
        """`<tg-emoji>` does nothing here, and does it silently.

        Measured 2026-09-01 against this bot on the real API: sendMessage
        returns ok:true, and the message comes back with entities:null and only
        the fallback glyph. The Bot API grants custom emoji to bots that bought
        a username on Fragment, and this one has not — so there is no error to
        catch, no rejected send, and nothing renders wrong. It just never
        animates. That is invisible to every test that asserts on the text the
        bot BUILT rather than on what Telegram kept, which is all of them.
        """
        import ast
        root = Path(__file__).resolve().parents[1]
        for rel in self.MODULES:
            with self.subTest(module=rel):
                tree = ast.parse((root / rel).read_text(encoding="utf-8"))
                # String literals only, not raw file text: both modules carry a
                # `#` comment recording WHY this is forbidden, and a grep-style
                # check would fire on the explanation rather than on a use.
                literals = [n.value for n in ast.walk(tree)
                            if isinstance(n, ast.Constant)
                            and isinstance(n.value, str)]
                for text in literals:
                    self.assertNotIn("<tg-emoji", text,
                                     f"{rel} builds a custom-emoji entity — it is "
                                     "stripped for this bot; motion comes from "
                                     "re-editing (run._SPIN)")

    def test_no_module_defines_a_top_level_name_twice(self):
        import ast
        root = Path(__file__).resolve().parents[1]
        for rel in self.MODULES:
            with self.subTest(module=rel):
                tree = ast.parse((root / rel).read_text(encoding="utf-8"))
                names = []
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                         ast.ClassDef)):
                        names.append(node.name)
                    elif isinstance(node, ast.Assign):
                        names += [t.id for t in node.targets
                                  if isinstance(t, ast.Name)]
                    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                        names.append(node.target.id)
                dupes = sorted({n for n in names if names.count(n) > 1})
                self.assertEqual(dupes, [], f"{rel} defines {dupes} more than once")


class TestDeliverResult(unittest.TestCase):
    """What the user is shown when a job ends.

    Never covered before 2026-08-31, when this stopped being something the
    user had to ask for: tick_progress now calls it the moment the drain
    finishes, so its wording is the whole report on both success and failure.
    """

    def setUp(self):
        self._orig_root = bot.ROOT
        self.root = Path(tempfile.mkdtemp())
        (self.root / "batch").mkdir()
        (self.root / "out").mkdir()
        bot.ROOT = self.root
        self.manifest = self.root / "batch" / "tg-1.yaml"
        self.manifest.write_text("runs: []\n", encoding="utf-8")
        self.tg = FakeTg()

    def tearDown(self):
        bot.ROOT = self._orig_root

    def _write_state(self, payload):
        state_path_for(self.manifest).write_text(json.dumps(payload),
                                                encoding="utf-8")

    def _batch_dir(self, batch_id):
        d = self.root / "out" / batch_id
        (d / "_final").mkdir(parents=True)
        (d / "runs" / "job").mkdir(parents=True)
        return d

    def test_success_names_the_batch_and_sends_every_final_file(self):
        d = self._batch_dir("2026-08-31-2140")
        (d / "_final" / "job.mp4").write_bytes(b"video")
        self._write_state({"batch": "2026-08-31-2140",
                           "runs": {"job": {"status": "done", "stages": {
                               "enhance": {"status": "done", "sec": 114}}}}})
        bot.deliver_result(self.tg, ME, self.manifest)
        joined = "\n".join(self.tg.messages)
        self.assertIn("Done", joined)
        self.assertIn("2026-08-31-2140", joined)
        self.assertEqual([c for _, c in self.tg.documents], ["job.mp4"])

    def test_failure_names_the_stage_that_broke(self):
        # "the run failed" sends the user off to read a log for something the
        # journal already knows.
        d = self._batch_dir("2026-08-31-2200")
        (d / "runs" / "job" / "pod-job.log").write_text(
            "loading model\nCUDA out of memory\n", encoding="utf-8")
        self._write_state({"batch": "2026-08-31-2200",
                           "runs": {"job": {"status": "error", "stages": {
                               "tryon": {"status": "done", "sec": 351},
                               "enhance": {"status": "error", "job_id": "j-9"}}}}})
        bot.deliver_result(self.tg, ME, self.manifest)
        joined = "\n".join(self.tg.messages)
        self.assertIn("job failed", joined)
        self.assertIn("enhance", joined)
        # The tail is inlined, because opening a .log on a phone is several
        # taps and an app switch.
        self.assertIn("CUDA out of memory", joined)
        # And still attached, for the whole thing.
        self.assertIn("job/pod-job.log", [c for _, c in self.tg.documents])

    def test_a_drain_that_never_started_says_so_instead_of_going_quiet(self):
        self._write_state({"runs": {}})
        bot.deliver_result(self.tg, ME, self.manifest)
        self.assertIn("No batch was ever recorded", self.tg.messages[0])
        self.assertIn("drain.log", self.tg.messages[0])

    def test_no_outputs_and_no_recorded_failure_is_still_reported(self):
        # The silent case: a batch directory exists, nothing was produced, and
        # nothing is marked failed. Saying nothing would leave the user
        # waiting on a job that has already ended.
        self._batch_dir("2026-08-31-2300")
        self._write_state({"batch": "2026-08-31-2300", "runs": {}})
        bot.deliver_result(self.tg, ME, self.manifest)
        self.assertIn("Nothing to send", "\n".join(self.tg.messages))

    def test_the_log_tail_is_capped_so_the_send_cannot_fail(self):
        # A Telegram message is 4096 characters and a pod log is megabytes.
        # Sending the whole thing would make the send fail — an error report
        # that becomes a second error.
        big = self.root / "big.log"
        big.write_text("x" * 100_000, encoding="utf-8")
        self.assertEqual(len(bot._tail(big)), bot.TAIL_CHARS)

    def test_an_unreadable_log_reports_itself_rather_than_raising(self):
        self.assertIn("could not read", bot._tail(self.root / "missing.log"))


if __name__ == "__main__":
    unittest.main()
