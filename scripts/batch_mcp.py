#!/usr/bin/env python3
"""MCP server cho batch runner (spec §9). Nói JSON-RPC trên stdin/stdout.

Đăng ký ở .mcp.json của repo, nên Claude Code tự khởi động nó — bạn không chạy
file này bằng tay. Muốn thử tay:

    echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python3 scripts/batch_mcp.py
    make batch-mcp-check

Bốn tool: batch_validate · batch_run · batch_status · batch_rerun.
Hai cái giữa tiêu tiền GPU; mô tả của chúng nói thẳng điều đó (mcp_tools.py).

KHÔNG in gì ra stdout ở đây. stdout là kênh giao thức — một dòng thừa là rụng
kết nối. Cần nói gì thì nói ra stderr; Claude Code gom nó vào log MCP.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.mcp_tools import build_tools
from batchlib.rpc import run_stdio


def main() -> int:
    run_stdio(build_tools())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
