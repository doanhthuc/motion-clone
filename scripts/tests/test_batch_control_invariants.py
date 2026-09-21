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
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
_TARGETS = ("start_drain", "start_phase_a")


class _CallCollector(ast.NodeVisitor):
    """Every direct call to a name in `_TARGETS`, paired with the name of
    the function it was made from (`None` at module level)."""

    def __init__(self):
        self._stack: list[str] = []
        self.calls: list[tuple[str, str | None]] = []

    def _enter(self, node):
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node):
        self._enter(node)

    def visit_AsyncFunctionDef(self, node):
        self._enter(node)

    def visit_Call(self, node):
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            name = None
        if name in _TARGETS:
            self.calls.append((name, self._stack[-1] if self._stack else None))
        self.generic_visit(node)


def _call_sites() -> dict[str, list[tuple[str, str | None]]]:
    """{"start_drain": [(relpath, enclosing_function), ...], "start_phase_a": [...]}"""
    sites: dict[str, list[tuple[str, str | None]]] = {name: [] for name in _TARGETS}
    for path in sorted(SCRIPTS_ROOT.rglob("*.py")):
        rel = path.relative_to(SCRIPTS_ROOT)
        if rel.parts[0] == "tests":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(rel))
        collector = _CallCollector()
        collector.visit(tree)
        for name, func in collector.calls:
            sites[name].append((rel.as_posix(), func))
    return sites


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
                         f"start_drain/start_phase_a must only be reached through AppRuns's own "
                         f"call into tgbot/bot.py, never directly from control/ or httpapi/: "
                         f"{offenders}")


if __name__ == "__main__":
    unittest.main()
