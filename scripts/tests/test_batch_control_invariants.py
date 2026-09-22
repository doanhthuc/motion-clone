"""Static check on where the two paid actions can be started from.

`start_drain`/`start_phase_a` (`tgbot/run.py`) are the only things in this repo
that spend money — a pod rental or a Gemini phase-A call. Slice 4 added a
second caller of the bot's run machinery (`AppRuns`, the phone's HTTP
routes), and the whole design leans on that caller going through the SAME
four functions (`_do_confirm`/`_do_resume`/`_regen_tryon`/
`_start_phase_a_and_report`) rather than a shortcut that skips the lock or
the idempotency store. This parses every module with `ast` instead of
importing `tgbot.bot` (a ~7k-line module with side effects at import time)
so a third call site anywhere — including one added to `control/` or
`httpapi/` by a future change that reaches straight for the money instead of
going through `AppRuns` — turns red here instead of drifting unnoticed.

Slice 5 adds the two destructive calls the phone can reach: `_do_kill`
(`make gpu-destroy`) and `_start_migration` (`volume_migrate.py`, which deletes
the Network Volume). They are pinned the same way, and the strings
`gpu-destroy` / `volume_migrate` must never appear in `control/` or
`httpapi/` at all: the HTTP layer reaches them only through `AppPod`.
"""
from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
_TARGETS = ("start_drain", "start_phase_a", "_do_kill", "_start_migration")
_FORBIDDEN_STRINGS = ("gpu-destroy", "volume_migrate")


class _CallCollector(ast.NodeVisitor):
    """Every direct call to a name in `targets`: the name, the function it
    was made from (`None` at module level), that function's class (`None`
    for a plain function), and the source of every `if`/`elif` test whose
    BODY (not its else-chain) the call sits in — so "the `_CB_KILL_GO`
    branch" is checkable, not just "somewhere in `_handle_callback`"."""

    def __init__(self, targets=_TARGETS):
        self._targets = targets
        self._stack: list[str] = []
        self._current_class: str | None = None
        self._guards: list[str] = []
        self.calls: list[tuple[str, str | None, str | None, tuple[str, ...]]] = []

    def _enter(self, node):
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_ClassDef(self, node):
        outer, self._current_class = self._current_class, node.name
        self.generic_visit(node)
        self._current_class = outer

    def visit_FunctionDef(self, node):
        self._enter(node)

    def visit_AsyncFunctionDef(self, node):
        self._enter(node)

    def visit_If(self, node):
        self.visit(node.test)
        self._guards.append(ast.unparse(node.test))
        for child in node.body:
            self.visit(child)
        self._guards.pop()
        # An `elif` is an If nested in orelse: its earlier tests are NOT
        # guards of the later branches, so they are popped before this.
        for child in node.orelse:
            self.visit(child)

    def visit_Call(self, node):
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            name = None
        if name in self._targets:
            self.calls.append((name, self._stack[-1] if self._stack else None,
                               self._current_class,
                               tuple(self._guards)))
        self.generic_visit(node)


def _collect(source: str, filename: str = "<src>", targets=_TARGETS):
    collector = _CallCollector(targets)
    collector.visit(ast.parse(source, filename=filename))
    return collector.calls


def _production_sources():
    """(relpath, source) of every non-test .py under scripts/."""
    for path in sorted(SCRIPTS_ROOT.rglob("*.py")):
        rel = path.relative_to(SCRIPTS_ROOT)
        if rel.parts[0] == "tests":
            continue
        yield rel, path.read_text(encoding="utf-8")


def _call_sites() -> dict[str, list[tuple[str, str | None]]]:
    """{"start_drain": [(relpath, enclosing_function), ...], ...}"""
    sites: dict[str, list[tuple[str, str | None]]] = {name: [] for name in _TARGETS}
    for rel, source in _production_sources():
        for name, func, _cls, _guards in _collect(source, str(rel)):
            sites[name].append((rel.as_posix(), func))
    return sites


def _detailed_sites(target: str) -> list[tuple[str, str | None, str | None, tuple[str, ...]]]:
    """[(relpath, function, class, guards)] for one target, tests excluded."""
    return [(rel.as_posix(), func, cls, guards)
            for rel, source in _production_sources()
            for name, func, cls, guards in _collect(source, str(rel), (target,))
            if name == target]


def _files_with_forbidden_strings(paths) -> list[tuple[str, str]]:
    return [(str(path), needle) for path in paths
            for needle in _FORBIDDEN_STRINGS
            if needle in Path(path).read_text(encoding="utf-8")]


class TestCallSiteInvariants(unittest.TestCase):
    def test_start_drain_is_called_from_exactly_confirm_and_resume(self):
        sites = _call_sites()["start_drain"]
        expected = {("tgbot/bot.py", "_do_confirm"), ("tgbot/bot.py", "_do_resume")}
        self.assertEqual(sorted(sites), sorted(expected),
                         f"start_drain call sites {sorted(sites)} do not match the expected "
                         f"{sorted(expected)} — unexpected: {sorted(set(sites) - expected)}, "
                         f"missing: {sorted(expected - set(sites))}")

    def test_start_phase_a_is_called_from_exactly_regen_and_report(self):
        sites = _call_sites()["start_phase_a"]
        expected = {("tgbot/bot.py", "_regen_tryon"), ("tgbot/bot.py", "_start_phase_a_and_report")}
        self.assertEqual(sorted(sites), sorted(expected),
                         f"start_phase_a call sites {sorted(sites)} do not match the expected "
                         f"{sorted(expected)} — unexpected: {sorted(set(sites) - expected)}, "
                         f"missing: {sorted(expected - set(sites))}")

    def test_neither_is_called_from_control_or_httpapi(self):
        offenders = [(target, file, func) for target, calls in _call_sites().items()
                     for file, func in calls
                     if file.startswith("control/") or file.startswith("httpapi/")]
        self.assertEqual(offenders, [],
                         f"start_drain/start_phase_a/_do_kill/_start_migration must only be reached through AppRuns/AppPod's own "
                         f"call into tgbot/bot.py, never directly from control/ or httpapi/: "
                         f"{offenders}")


    def test_do_kill_is_called_from_exactly_the_telegram_button_and_the_kill_worker(self):
        sites = _detailed_sites("_do_kill")
        self.assertEqual(
            sorted((f, fn, cls) for f, fn, cls, _ in sites),
            sorted([("tgbot/bot.py", "_handle_callback", None),
                    ("tgbot/bot.py", "_kill_worker", "AppPod")]),
            f"_do_kill call sites drifted: {sites}")
        (guards,) = [g for _f, fn, _c, g in sites if fn == "_handle_callback"]
        self.assertTrue(guards and "_CB_KILL_GO" in guards[-1],
                        f"the Telegram call must sit in the _CB_KILL_GO branch, not {guards}")

    def test_start_migration_is_called_from_exactly_the_telegram_button_and_migrate(self):
        sites = _detailed_sites("_start_migration")
        self.assertEqual(
            sorted((f, fn, cls) for f, fn, cls, _ in sites),
            sorted([("tgbot/bot.py", "_handle_callback", None),
                    ("tgbot/bot.py", "migrate", "AppPod")]),
            f"_start_migration call sites drifted: {sites}")
        (guards,) = [g for _f, fn, _c, g in sites if fn == "_handle_callback"]
        self.assertTrue(guards and "_CB_MIGRATE_GO" in guards[-1],
                        f"the Telegram call must sit in the _CB_MIGRATE_GO branch, not {guards}")

    def test_neither_destructive_string_appears_in_control_or_httpapi(self):
        paths = [p for top in ("control", "httpapi")
                 for p in sorted((SCRIPTS_ROOT / top).rglob("*.py"))]
        self.assertTrue(paths, "found no control/ or httpapi/ sources to scan")
        self.assertEqual(_files_with_forbidden_strings(paths), [],
                         "control/ and httpapi/ must reach the destroy and the volume "
                         "migration only through tgbot.bot.AppPod, never by name")


class TestTheInvariantsCanFail(unittest.TestCase):
    """Each check above is only worth having if it can go red. These feed
    the same helpers a synthetic source instead of editing real code."""

    def test_a_third_do_kill_call_site_is_seen(self):
        src = ("def _handle_callback(data):\n"
               "    if data == _CB_KILL_GO:\n        _do_kill(tg, 1)\n"
               "class AppPod:\n    def _kill_worker(self):\n        _do_kill(tg, 1)\n"
               "def sneaky():\n    _do_kill(tg, 1)\n")
        calls = _collect(src, targets=("_do_kill",))
        self.assertEqual([(fn, cls) for _n, fn, cls, _g in calls],
                         [("_handle_callback", None), ("_kill_worker", "AppPod"),
                          ("sneaky", None)])

    def test_the_guard_is_the_branch_the_call_is_in_not_an_earlier_elif_test(self):
        src = ("def _handle_callback(data):\n"
               "    if data == _CB_KILL_ASK:\n        pass\n"
               "    elif data == _CB_WIPE_GO:\n        _do_kill(tg, 1)\n"
               "    elif data == _CB_KILL_GO:\n        pass\n")
        ((_n, _fn, _cls, guards),) = _collect(src, targets=("_do_kill",))
        self.assertEqual(len(guards), 1)
        self.assertIn("_CB_WIPE_GO", guards[-1])
        self.assertNotIn("_CB_KILL_ASK", guards[-1])

    def test_a_forbidden_string_in_a_file_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            clean, dirty = Path(tmp) / "clean.py", Path(tmp) / "dirty.py"
            clean.write_text("x = 1\n", encoding="utf-8")
            dirty.write_text("cmd = ['make', 'gpu-destroy']\n", encoding="utf-8")
            other = Path(tmp) / "other.py"
            other.write_text("import volume_migrate\n", encoding="utf-8")
            found = _files_with_forbidden_strings([clean, dirty, other])
        self.assertEqual(found, [(str(dirty), "gpu-destroy"), (str(other), "volume_migrate")])


if __name__ == "__main__":
    unittest.main()
