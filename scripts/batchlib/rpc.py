"""JSON-RPC 2.0 trên stdio cho MCP. Không biết gì về batch — đó là điểm của file này.

Ranh giới: module này cầm GIAO THỨC (khung message, mã lỗi, vòng đọc stdin).
mcp_tools.py cầm VIỆC (validate, chạy lô, đọc journal). Đổi được cái này mà
không đụng cái kia là lý do chúng tách ra.

BẪY SỐ MỘT: stdout là kênh giao thức. Một dòng print() lọt vào đó không phải
một dòng log thừa — nó là một message JSON-RPC hỏng, và client rụng kết nối
ngay. Nên `serve()` là nơi DUY NHẤT trong cả cây batchlib được ghi stdout, mọi
thứ khác đi stderr. `test_moi_dong_stdout_deu_la_json_hop_le` ghim điều đó.

Hai loại lỗi, cố ý không trộn:
  JSON-RPC error   = lỗi GIAO THỨC (JSON hỏng, method lạ, tên tool không có).
                     Client coi đây là "server này hỏng".
  result.isError   = lỗi VIỆC (manifest sai, pod không trả lời, lô đang chạy).
                     Model đọc được, sửa được, gọi lại được — không phải rụng
                     phiên chat để sửa một dòng YAML.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Callable, TextIO

# Bản giao thức MCP mà server này khai. Client thường chấp nhận bản khác và tự
# hạ xuống, nhưng khai đúng một bản có thật vẫn hơn khai bừa.
PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "motion-batch"
SERVER_VERSION = "1.0.0"

PARSE_ERROR = -32700
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict          # JSON Schema của arguments, trả nguyên vẹn trong tools/list
    fn: Callable[..., dict]   # nhận **arguments, trả một dict tuần tự hoá được


def _ok(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _content(payload: dict, *, is_error: bool = False) -> dict:
    """content[] là thứ model ĐỌC; structuredContent là thứ chương trình đọc.

    Trả cả hai: text phải là JSON parse được (không phải repr của Python), vì
    model sẽ trích số liệu từ đó.
    """
    result = {"content": [{"type": "text",
                           "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
              "structuredContent": payload}
    if is_error:
        result["isError"] = True
    return result


def _call_tool(msg_id, params: dict, tools: dict[str, Tool]) -> dict:
    name = params.get("name")
    tool = tools.get(name)
    if tool is None:
        return _err(msg_id, INVALID_PARAMS,
                    f"không có tool tên {name!r}. Có: {', '.join(sorted(tools))}")
    arguments = params.get("arguments") or {}
    try:
        return _ok(msg_id, _content(tool.fn(**arguments)))
    except TypeError as exc:
        # Sai chữ ký (thiếu tham số bắt buộc, thừa tham số lạ). Là lỗi VIỆC theo
        # nghĩa model sửa được ngay ở lần gọi sau, nên không giết kết nối.
        return _ok(msg_id, _content({"loi": f"tham số sai cho {name}: {exc}"}, is_error=True))
    except Exception as exc:  # noqa: BLE001 — xem docstring module
        return _ok(msg_id, _content({"loi": str(exc)}, is_error=True))


def handle(message: dict, *, tools: dict[str, Tool]) -> dict | None:
    """Một message vào, một reply ra. None nghĩa là notification — KHÔNG được trả lời."""
    msg_id = message.get("id")
    if msg_id is None:
        return None
    method = message.get("method")

    if method == "initialize":
        return _ok(msg_id, {"protocolVersion": PROTOCOL_VERSION,
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}})
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": [{"name": t.name, "description": t.description,
                                       "inputSchema": t.schema}
                                      for t in tools.values()]})
    if method == "tools/call":
        return _call_tool(msg_id, message.get("params") or {}, tools)
    return _err(msg_id, METHOD_NOT_FOUND, f"method không hỗ trợ: {method}")


def serve(stdin: TextIO, stdout: TextIO, *, tools: dict[str, Tool]) -> None:
    """Vòng đọc newline-delimited JSON. Không bao giờ thoát vì một message hỏng.

    Một dòng hỏng kết thúc phiên nghĩa là gõ nhầm một lần phải khởi động lại cả
    phiên chat — nên dòng hỏng trả -32700 rồi đọc tiếp dòng sau.
    """
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError as exc:
            reply = _err(None, PARSE_ERROR, f"JSON hỏng: {exc}")
        else:
            reply = handle(message, tools=tools)
        if reply is None:
            continue
        try:
            stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            stdout.flush()
        except BrokenPipeError:
            # Client tắt server bằng cách đóng ống. Không có gì hỏng cả — nhưng để
            # nó nở thành traceback thì dòng cuối trong log MCP là một lỗi giả, đúng
            # thứ người ta đọc đầu tiên khi đi tìm một lỗi thật.
            return


def run_stdio(tools: dict[str, Tool]) -> None:
    serve(sys.stdin, sys.stdout, tools=tools)
