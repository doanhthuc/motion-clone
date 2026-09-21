"""Telegram-free core shared by the Telegram bot and the HTTP API (spec 2026-09-21)."""
import threading

# Spec §4.3: the bot was single-threaded, the HTTP API is not. One re-entrant
# lock wraps every read-modify-write of shared control-plane state. Slow work
# (ffprobe, make batch-validate, file copies) must run outside it.
LOCK = threading.RLock()
