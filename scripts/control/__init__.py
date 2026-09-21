"""Telegram-free core shared by the Telegram bot and the HTTP API (spec 2026-09-21)."""
import threading

# Spec §4.3: the bot was single-threaded, the HTTP API is not. One re-entrant
# lock wraps every read-modify-write of shared control-plane state. Slow work
# (ffprobe, make batch-validate, file copies) must run outside it.
LOCK = threading.RLock()

# Spec §5.8: the bot loop holds this around each handle() and each tick round,
# and the HTTP thread around every call into the bot's run functions, so the
# check-then-start of a paid drain can never interleave. Separate from LOCK:
# the bot holds this one through slow Telegram work (a 2 GB staging copy, a
# 120 s validate), which must not stall the phone's draft edits.
BOT_LOCK = threading.RLock()
# The HTTP side gives up after this and answers 503: Cloudflare cuts a request
# that sends nothing for 100 s.
BOT_LOCK_TIMEOUT_SEC = 60
