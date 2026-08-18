"""Lớp giao thức JSON-RPC/stdio. Không có batch nào ở đây — đó là điểm của file này.

Bẫy chết người của MCP stdio: MỌI thứ lọt vào stdout mà không phải JSON-RPC sẽ
giết cả kết nối. Nên `serve()` phải là nơi duy nhất được ghi stdout, và test
cuối file ghim đúng điều đó.
"""
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.rpc import PROTOCOL_VERSION, Tool, handle, serve


def _tool(name="thu", fn=None, description="tool thử", schema=None):
    return Tool(name=name, description=description,
                schema=schema or {"type": "object", "properties": {}},
                fn=fn or (lambda **kw: {"nhan": kw}))


def _tools(*tools):
    return {t.name: t for t in tools}


class TestHandshake(unittest.TestCase):
    def test_initialize_tra_protocol_version_va_kha_nang_tools(self):
        reply = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                       tools=_tools(_tool()))
        self.assertEqual(reply["id"], 1)
        self.assertEqual(reply["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertIn("tools", reply["result"]["capabilities"])
        self.assertIn("name", reply["result"]["serverInfo"])

    def test_notification_khong_duoc_tra_loi(self):
        """Message không có "id" là notification. Trả lời nó là sai giao thức."""
        self.assertIsNone(handle({"jsonrpc": "2.0", "method": "notifications/initialized"},
                                 tools=_tools(_tool())))

    def test_ping_tra_result_rong(self):
        reply = handle({"jsonrpc": "2.0", "id": 7, "method": "ping"}, tools=_tools(_tool()))
        self.assertEqual(reply["result"], {})

    def test_method_la_tra_loi_32601(self):
        reply = handle({"jsonrpc": "2.0", "id": 2, "method": "khong/co"}, tools=_tools(_tool()))
        self.assertEqual(reply["error"]["code"], -32601)
        self.assertNotIn("result", reply)


class TestToolsList(unittest.TestCase):
    def test_liet_ke_ten_mo_ta_va_input_schema(self):
        schema = {"type": "object", "properties": {"file": {"type": "string"}},
                  "required": ["file"]}
        reply = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
                       tools=_tools(_tool(name="batch_validate", description="kiểm manifest",
                                          schema=schema)))
        [entry] = reply["result"]["tools"]
        self.assertEqual(entry["name"], "batch_validate")
        self.assertEqual(entry["description"], "kiểm manifest")
        self.assertEqual(entry["inputSchema"], schema)


class TestToolsCall(unittest.TestCase):
    def test_goi_dung_ham_voi_arguments_va_tra_structured_content(self):
        reply = handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "thu", "arguments": {"file": "a.yaml"}}},
                       tools=_tools(_tool(fn=lambda **kw: {"nhan": kw})))
        self.assertEqual(reply["result"]["structuredContent"], {"nhan": {"file": "a.yaml"}})
        self.assertNotIn("isError", reply["result"])

    def test_content_text_la_json_doc_duoc(self):
        """Model đọc `content`, không phải structuredContent. Nó phải là JSON thật."""
        reply = handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                        "params": {"name": "thu", "arguments": {}}},
                       tools=_tools(_tool(fn=lambda **kw: {"ok": True, "vi": "tiếng Việt"})))
        [block] = reply["result"]["content"]
        self.assertEqual(block["type"], "text")
        self.assertEqual(json.loads(block["text"]), {"ok": True, "vi": "tiếng Việt"})

    def test_thieu_arguments_van_goi_duoc_tool_khong_tham_so(self):
        reply = handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                        "params": {"name": "thu"}},
                       tools=_tools(_tool(fn=lambda **kw: {"nhan": kw})))
        self.assertEqual(reply["result"]["structuredContent"], {"nhan": {}})

    def test_ten_tool_la_tra_loi_32602_kem_danh_sach_ten_that(self):
        reply = handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                        "params": {"name": "batch_xoa_het", "arguments": {}}},
                       tools=_tools(_tool(name="batch_run")))
        self.assertEqual(reply["error"]["code"], -32602)
        self.assertIn("batch_run", reply["error"]["message"])

    def test_tool_nem_exception_thanh_isError_chu_khong_giet_server(self):
        """Lỗi TOOL là result isError; chỉ lỗi GIAO THỨC mới là JSON-RPC error.

        Trộn hai cái này nghĩa là một manifest sai đường dẫn cũng làm rụng
        kết nối MCP — người dùng phải khởi động lại phiên chat để sửa một dòng YAML.
        """
        def no(**_kw):
            raise RuntimeError("pod không trả lời")

        reply = handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                        "params": {"name": "thu", "arguments": {}}},
                       tools=_tools(_tool(fn=no)))
        self.assertNotIn("error", reply)
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("pod không trả lời", reply["result"]["content"][0]["text"])


class TestServeLoop(unittest.TestCase):
    def _chay(self, dong_vao: list[str]):
        out = io.StringIO()
        serve(io.StringIO("".join(f"{d}\n" for d in dong_vao)), out,
              tools=_tools(_tool(fn=lambda **kw: {"nhan": kw})))
        return [json.loads(d) for d in out.getvalue().splitlines() if d.strip()]

    def test_moi_dong_stdout_deu_la_json_hop_le(self):
        """Ghim bẫy số 1: một dòng rác trên stdout là giết cả kết nối."""
        replies = self._chay([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ])
        self.assertEqual([r["id"] for r in replies], [1, 2])  # notification không sinh dòng nào
        for r in replies:
            self.assertEqual(r["jsonrpc"], "2.0")

    def test_json_hong_tra_32700_va_server_van_song(self):
        """Một dòng hỏng không được kết thúc phiên: dòng SAU nó vẫn phải chạy."""
        replies = self._chay([
            "{ đây không phải json",
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "ping"}),
        ])
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertIsNone(replies[0]["id"])
        self.assertEqual(replies[1]["id"], 3)

    def test_client_dong_ong_giua_chung_thi_thoat_im_lang(self):
        """Client tắt server bằng cách đóng ống. Nếu stdout đóng trước stdin,
        write/flush ném BrokenPipeError — và một traceback ở đây là dòng cuối cùng
        nằm trong log MCP, tức thứ người ta đọc đầu tiên khi đi tìm lỗi thật.
        """
        class OngDongRoi(io.StringIO):
            def write(self, _s):
                raise BrokenPipeError(32, "Broken pipe")

        serve(io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n"),
              OngDongRoi(), tools=_tools(_tool()))

    def test_dong_trong_bi_bo_qua_khong_thanh_loi(self):
        self.assertEqual(self._chay(["", "   ",
                                     json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping"})]),
                         [{"jsonrpc": "2.0", "id": 4, "result": {}}])


if __name__ == "__main__":
    unittest.main()
