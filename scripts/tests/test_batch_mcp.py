"""Bốn tool MCP. Không cần pod, không tốn đồng GPU nào.

Cách test spawn nền mà không đụng pod: TIÊM đường dẫn runner. Ctx.runner trỏ vào
một script Python giả do test tự sinh — nó ghi marker, in ra vài dòng, rồi ngủ
hoặc thoát với mã cho trước. Nhờ vậy kiểm được đúng ba thứ khó: process con có
thật sự tách phiên không, log có chảy ra file không, và mã thoát có đọc lại được
sau khi con đã chết không.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.mcp_tools import (Ctx, _con_song, batch_rerun, batch_run, batch_status,
                                batch_validate, build_tools, mcp_path_for)
from batchlib.manifest import ManifestError, load_state, save_state, state_path_for

MANIFEST_OK = """\
runs:
  - id: run-mot
    pipeline: motion-enhance
    inputs:
      character: {char}
      driver:    {driver}
  - id: run-hai
    pipeline: motion-enhance
    inputs:
      character: {char}
      driver:    {driver}
"""

# Runner giả: ghi marker + argv đã nhận, in vài dòng ra stdout, rồi thoát/ngủ.
FAKE_RUNNER = """\
import json, sys, time
from pathlib import Path
here = Path(__file__).parent
(here / "da-chay.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
print("dòng một")
print("dòng hai")
sys.stdout.flush()
NGU = {ngu}
if NGU:
    time.sleep(NGU)
raise SystemExit({ma_thoat})
"""

# Runner giả KHÔNG flush — để lộ chuyện stdout bị block-buffer khi đổ vào file.
FAKE_RUNNER_KHONG_FLUSH = """\
import sys, time
print("toi in dong nay ngay tu dau")
time.sleep({ngu})
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "batch").mkdir()
        self.char = self.dir / "nhanvat.jpeg"
        self.driver = self.dir / "dandong.mp4"
        self.char.write_bytes(b"\xff\xd8\xff" + b"0" * 100)
        self.driver.write_bytes(b"0" * 100)
        self.manifest = self.dir / "batch" / "lo.yaml"
        self.manifest.write_text(MANIFEST_OK.format(char=self.char, driver=self.driver),
                                 encoding="utf-8")
        self._con = []
        self.addCleanup(self._don_dep)

    def _don_dep(self):
        for pid in self._con:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except (ProcessLookupError, ChildProcessError, PermissionError):
                pass

    def ctx(self, *, ngu=0, ma_thoat=0) -> Ctx:
        runner = self.dir / "runner_gia.py"
        runner.write_text(FAKE_RUNNER.format(ngu=ngu, ma_thoat=ma_thoat), encoding="utf-8")
        return Ctx(root=self.dir, runner=runner)

    def ctx_khong_flush(self, *, ngu) -> Ctx:
        runner = self.dir / "runner_gia.py"
        runner.write_text(FAKE_RUNNER_KHONG_FLUSH.format(ngu=ngu), encoding="utf-8")
        return Ctx(root=self.dir, runner=runner)

    def chay(self, ctx, **kw) -> dict:
        out = batch_run(ctx, file=str(self.manifest), **kw)
        if out.get("pid"):
            self._con.append(out["pid"])
        return out

    def doi_marker(self, timeout=5.0) -> list:
        marker = self.dir / "da-chay.json"
        han = time.time() + timeout
        while time.time() < han:
            if marker.is_file():
                return json.loads(marker.read_text(encoding="utf-8"))
            time.sleep(0.02)
        self.fail("runner giả không bao giờ chạy — batch_run không spawn được gì")

    def doi_ket_thuc(self, timeout=5.0) -> None:
        rc = mcp_path_for(self.manifest).with_suffix(".rc")
        han = time.time() + timeout
        while time.time() < han:
            if rc.is_file():
                return
            time.sleep(0.02)
        self.fail("runner giả không bao giờ kết thúc")


class TestValidate(Base):
    def test_manifest_hop_le_tra_so_run(self):
        out = batch_validate(self.ctx(), file=str(self.manifest))
        self.assertTrue(out["ok"])
        self.assertEqual(out["so_run"], 2)
        self.assertEqual(out["loi"], [])

    def test_manifest_sai_tra_ok_false_kem_tung_loi_chu_khong_nem(self):
        """"Đọc được nhưng sai" là dữ liệu trả về, không phải exception —
        model phải đọc được danh sách lỗi để sửa file."""
        self.manifest.write_text(
            MANIFEST_OK.format(char=self.char, driver=self.driver)
            + "    motion: { khong_co_key_nay: 1 }\n", encoding="utf-8")
        out = batch_validate(self.ctx(), file=str(self.manifest))
        self.assertFalse(out["ok"])
        self.assertTrue(any("khong_co_key_nay" in e for e in out["loi"]))

    def test_file_khong_doc_duoc_thi_nem_ManifestError(self):
        """Khác hẳn "đọc được nhưng sai" — rpc.py biến cái này thành isError."""
        with self.assertRaises(ManifestError):
            batch_validate(self.ctx(), file=str(self.dir / "khong-co.yaml"))


class TestRun(Base):
    def test_manifest_sai_thi_KHONG_spawn_gi_ca(self):
        """Cửa chặn tiền GPU: sai một dòng YAML không được đẻ ra process nào."""
        self.manifest.write_text("runs: [ { id: x, pipeline: khong-co-pipeline-nay, "
                                 "inputs: {} } ]", encoding="utf-8")
        out = self.chay(self.ctx())
        self.assertFalse(out["ok"])
        self.assertIsNone(out.get("pid"))
        self.assertFalse((self.dir / "da-chay.json").exists())

    def test_spawn_va_ghi_pid_argv_log_vao_mcp_json(self):
        out = self.chay(self.ctx(ngu=3))
        self.assertTrue(out["ok"])
        ghi = json.loads(mcp_path_for(self.manifest).read_text(encoding="utf-8"))
        self.assertEqual(ghi["pid"], out["pid"])
        self.assertIn("--file", ghi["argv"])
        self.assertTrue(Path(ghi["log"]).name.endswith(".mcp.log"))

    def test_mac_dinh_truyen_no_start_de_khong_tu_tieu_tien(self):
        self.chay(self.ctx(ngu=3))
        self.assertIn("--no-start", self.doi_marker())

    def test_allow_start_true_thi_bo_no_start(self):
        self.chay(self.ctx(ngu=3), allow_start=True)
        self.assertNotIn("--no-start", self.doi_marker())

    def test_resume_va_fail_fast_di_thang_xuong_runner(self):
        self.chay(self.ctx(ngu=3), resume=True, fail_fast=True)
        argv = self.doi_marker()
        self.assertIn("--resume", argv)
        self.assertIn("--fail-fast", argv)

    def test_con_chay_o_phien_RIENG_de_song_qua_phien_chat(self):
        """Lô chạy 30-60 phút, phiên chat thì không. Con phải tách session của cha,
        nếu không nó chết theo lúc Claude Code đóng server."""
        out = self.chay(self.ctx(ngu=3))
        self.assertNotEqual(os.getpgid(out["pid"]), os.getpgid(0))

    def test_stdout_cua_con_chay_vao_file_log(self):
        self.chay(self.ctx(ngu=0))
        self.doi_ket_thuc()
        log = Path(json.loads(mcp_path_for(self.manifest).read_text(encoding="utf-8"))["log"])
        self.assertIn("dòng một", log.read_text(encoding="utf-8"))

    def test_log_hien_ra_NGAY_chu_khong_doi_lo_ket_thuc(self):
        """stdout của con bị block-buffer khi đổ vào file, nên dòng tiến độ nằm trong
        buffer 8KB thay vì trên đĩa. Hai hậu quả, cả hai đều thấy trên pod thật:
        batch_status không có gì để báo suốt cả lô, và máy ngủ / bị kill là mất sạch
        những dòng cần nhất. Đo trên đường thật: log của lô đầu tiên qua MCP in
        stderr TRƯỚC stdout dù code in stdout trước.
        """
        self.chay(self.ctx_khong_flush(ngu=5))
        log = self.dir / "batch" / "lo.mcp.log"
        han = time.time() + 3.0
        while time.time() < han:
            if "toi in dong nay ngay tu dau" in log.read_text(encoding="utf-8"):
                return
            time.sleep(0.05)
        self.fail("3 giây rồi log vẫn rỗng dù con đã in — stdout đang bị buffer")

    def test_dang_co_lo_chay_thi_TU_CHOI_khong_spawn_cai_thu_hai(self):
        """Hai runner cùng ghi một state.json là hỏng journal, và hai job chồng nhau
        phá đúng giả định "lúc này GPU chỉ có mình tôi" của comfy_recycle (spec §5)."""
        dau = self.chay(self.ctx(ngu=5))
        self.assertTrue(dau["ok"])
        sau = self.chay(self.ctx(ngu=5))
        self.assertFalse(sau["ok"])
        self.assertIn(str(dau["pid"]), sau["loi"])
        self.assertIsNone(sau.get("pid"))

    def test_lo_truoc_da_xong_thi_chay_lo_moi_binh_thuong(self):
        dau = self.chay(self.ctx(ngu=0))
        self.doi_ket_thuc()
        (self.dir / "da-chay.json").unlink()
        sau = self.chay(self.ctx(ngu=3))
        self.assertTrue(sau["ok"], sau.get("loi"))
        self.assertNotEqual(sau["pid"], dau["pid"])


class TestStatus(Base):
    def test_chua_chay_bao_gio_thi_noi_ro_chua_chay(self):
        out = batch_status(self.ctx(), file=str(self.manifest))
        self.assertFalse(out["dang_chay"])
        self.assertIsNone(out["lo"])
        self.assertEqual(out["tong"]["con_lai"], 2)

    def test_dang_chay_thi_bao_dang_chay_kem_pid(self):
        dau = self.chay(self.ctx(ngu=5))
        out = batch_status(self.ctx(), file=str(self.manifest))
        self.assertTrue(out["dang_chay"])
        self.assertEqual(out["pid"], dau["pid"])

    def test_con_da_chet_thi_KHONG_duoc_bao_dang_chay(self):
        """Con là process con của chính server này nên nó thành zombie khi thoát —
        os.kill(pid, 0) trần vẫn báo "còn sống". Đây là chỗ đó phải bị bắt."""
        self.chay(self.ctx(ngu=0))
        self.doi_ket_thuc()
        out = batch_status(self.ctx(), file=str(self.manifest))
        self.assertFalse(out["dang_chay"])

    def test_bi_kill_giua_chung_thi_thoi_bao_dang_chay(self):
        """SIGKILL không kịp ghi mã thoát, nên câu trả lời chỉ còn dựa vào pid — và
        đúng chỗ đó có cái bẫy: runner là CON của chính process này, thoát mà không
        ai wait() là thành ZOMBIE, và os.kill(pid, 0) trần báo "còn sống" mãi mãi.
        Người dùng kill lô rồi hỏi lại sẽ được trả lời sai, vĩnh viễn.
        """
        out = self.chay(self.ctx(ngu=5))
        os.killpg(os.getpgid(out["pid"]), signal.SIGKILL)
        han = time.time() + 3.0
        while time.time() < han:
            if not batch_status(self.ctx(), file=str(self.manifest))["dang_chay"]:
                return
            time.sleep(0.02)
        self.fail("kill xong 3 giây rồi vẫn báo đang chạy — pid zombie chưa được reap")

    def test_bao_ma_thoat_cua_lo_da_ket_thuc(self):
        self.chay(self.ctx(ngu=0, ma_thoat=1))
        self.doi_ket_thuc()
        out = batch_status(self.ctx(), file=str(self.manifest))
        self.assertEqual(out["ma_thoat"], 1)

    def test_file_ma_thoat_bi_rac_thi_bao_khong_biet_chu_khong_no(self):
        """`.rc` do shell ghi; đĩa đầy hoặc kill giữa lúc ghi là ra file cụt.
        int() nổ ở đây nghĩa là batch_status chết hẳn — mất luôn đường xem tiến độ."""
        self.chay(self.ctx(ngu=0))
        self.doi_ket_thuc()
        mcp_path_for(self.manifest).with_suffix(".rc").write_text("khong-phai-so\n",
                                                                 encoding="utf-8")
        self.assertIsNone(batch_status(self.ctx(), file=str(self.manifest))["ma_thoat"])

    def test_doc_tien_do_tung_chang_tu_journal(self):
        save_state(state_path_for(self.manifest), {
            "version": 1, "batch": "2026-08-18-1430",
            "runs": {"run-mot": {"status": "done", "stages": {
                        "motion": {"job_id": "j1", "status": "done",
                                   "elapsed_sec": 144, "bytes": 480_000},
                        "enhance": {"job_id": "j2", "status": "done",
                                    "elapsed_sec": 79, "bytes": 3_224_000}}},
                     "run-hai": {"status": "error", "error": "mp4 rác", "stages": {
                        "motion": {"job_id": "j3", "status": "error"}}}}})
        out = batch_status(self.ctx(), file=str(self.manifest))
        self.assertEqual(out["lo"], "2026-08-18-1430")
        self.assertEqual(out["tong"], {"xong": 1, "hong": 1, "con_lai": 0})
        [mot, hai] = out["runs"]
        self.assertEqual([c["ten"] for c in mot["chang"]], ["motion", "enhance"])
        self.assertEqual(mot["chang"][0]["job_id"], "j1")
        self.assertEqual(hai["loi"], "mp4 rác")

    def test_tra_ve_may_dong_cuoi_cua_log(self):
        self.chay(self.ctx(ngu=0))
        self.doi_ket_thuc()
        out = batch_status(self.ctx(), file=str(self.manifest), so_dong_log=1)
        self.assertEqual(out["log"], ["dòng hai"])


class TestRerun(Base):
    def _journal_hai_run_da_xong(self):
        save_state(state_path_for(self.manifest), {
            "version": 1, "batch": "2026-08-18-1430",
            "runs": {"run-mot": {"status": "done", "stages": {"motion": {"job_id": "j1"}}},
                     "run-hai": {"status": "done", "stages": {"motion": {"job_id": "j2"}}}}})

    def test_xoa_dung_mot_entry_va_giu_nguyen_run_khac(self):
        """Đây là toàn bộ lý do tool này tồn tại: RESUME=1 bỏ qua run status=done,
        nên video ra xấu thì không có đường nào chạy lại nó."""
        self._journal_hai_run_da_xong()
        out = batch_rerun(self.ctx(ngu=3), file=str(self.manifest), run_id="run-mot")
        self._con.append(out["pid"])
        con_lai = load_state(state_path_for(self.manifest))["runs"]
        self.assertNotIn("run-mot", con_lai)
        self.assertEqual(con_lai["run-hai"]["status"], "done")

    def test_luon_chay_voi_resume_de_khong_lam_lai_run_khac(self):
        self._journal_hai_run_da_xong()
        out = batch_rerun(self.ctx(ngu=3), file=str(self.manifest), run_id="run-mot")
        self._con.append(out["pid"])
        self.assertIn("--resume", self.doi_marker())

    def test_run_id_khong_co_thi_bao_loi_kem_danh_sach_id_that(self):
        self._journal_hai_run_da_xong()
        out = batch_rerun(self.ctx(), file=str(self.manifest), run_id="run-mmot")
        self.assertFalse(out["ok"])
        self.assertIn("run-mot", out["loi"])
        self.assertIsNone(out.get("pid"))
        self.assertIn("run-mot", load_state(state_path_for(self.manifest))["runs"])

    def test_manifest_sai_thi_KHONG_xoa_entry_va_KHONG_spawn(self):
        """Xoá entry rồi mới phát hiện manifest sai = mất bản ghi của một run đã chạy
        xong, đổi lấy không gì cả."""
        self._journal_hai_run_da_xong()
        self.manifest.write_text(
            MANIFEST_OK.format(char=self.char, driver=self.driver)
            + "    motion: { khong_co_key_nay: 1 }\n", encoding="utf-8")
        out = batch_rerun(self.ctx(), file=str(self.manifest), run_id="run-hai")
        self.assertFalse(out["ok"])
        self.assertIsNone(out.get("pid"))
        self.assertIn("run-hai", load_state(state_path_for(self.manifest))["runs"])
        self.assertFalse((self.dir / "da-chay.json").exists())

    def test_dang_co_lo_chay_thi_TU_CHOI_va_KHONG_dung_vao_journal(self):
        """Xoá entry trong lúc runner đang ghi cùng file đó là hỏng journal."""
        self._journal_hai_run_da_xong()
        dau = self.chay(self.ctx(ngu=5))
        out = batch_rerun(self.ctx(ngu=5), file=str(self.manifest), run_id="run-mot")
        self.assertFalse(out["ok"])
        self.assertIn(str(dau["pid"]), out["loi"])
        self.assertIn("run-mot", load_state(state_path_for(self.manifest))["runs"])


class TestConSong(unittest.TestCase):
    """`_con_song` phải đúng cả khi pid KHÔNG phải con của process này — tình huống
    thường nhất trong đời thật: server MCP bị khởi động lại giữa lô, pid trong
    .mcp.json vẫn đó nhưng quan hệ cha-con thì mất."""

    def test_pid_khong_phai_con_ta_va_da_chet_thi_False(self):
        proc = subprocess.Popen([sys.executable, "-c", ""])
        proc.wait()   # đã reap ở đây → pid vừa chết vừa không còn là con ta
        self.assertFalse(_con_song(proc.pid))

    def test_pid_cua_process_khac_user_van_tinh_la_song(self):
        """pid 1 (launchd) tồn tại nhưng ta không có quyền signal nó. "Không được
        phép" không phải "đã chết" — trả False ở đây là cho phép gửi lô thứ hai
        chồng lên một lô đang chạy."""
        self.assertTrue(_con_song(1))


class TestBuildTools(Base):
    def test_dung_bon_tool_dung_ten_spec_9(self):
        self.assertEqual(sorted(build_tools(ctx=self.ctx())),
                         ["batch_rerun", "batch_run", "batch_status", "batch_validate"])

    def test_moi_tool_co_schema_va_mo_ta_khong_rong(self):
        for name, tool in build_tools(ctx=self.ctx()).items():
            self.assertEqual(tool.schema["type"], "object", name)
            self.assertIn("file", tool.schema["properties"], name)
            self.assertTrue(tool.description.strip(), name)

    def test_hai_tool_tieu_tien_phai_noi_ra_dieu_do_trong_mo_ta(self):
        """Mô tả tool là thứ DUY NHẤT model đọc trước khi gọi. Không nói ở đây thì
        không có chỗ nào khác nói."""
        tools = build_tools(ctx=self.ctx())
        for name in ("batch_run", "batch_rerun"):
            self.assertIn("GPU", tools[name].description, name)

    def test_ctx_di_theo_tool_nen_goi_qua_rpc_khong_can_truyen_gi_them(self):
        tools = build_tools(ctx=self.ctx())
        out = tools["batch_validate"].fn(file=str(self.manifest))
        self.assertTrue(out["ok"])


class TestEntryPoint(unittest.TestCase):
    """Chạy scripts/batch_mcp.py như Claude Code chạy nó: một process thật, nói
    chuyện qua stdin/stdout. Đây là test duy nhất bắt được loại lỗi "một import
    nào đó in ra stdout" — thứ giết kết nối MCP mà mọi unit test đều không thấy.
    """
    def _phien(self, *messages) -> list[dict]:
        entry = Path(__file__).resolve().parents[1] / "batch_mcp.py"
        proc = subprocess.run(
            [sys.executable, str(entry)],
            input="".join(json.dumps(m) + "\n" for m in messages),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return [json.loads(d) for d in proc.stdout.splitlines() if d.strip()]

    def test_handshake_that_qua_stdin_stdout(self):
        replies = self._phien(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertIn("tools", replies[0]["result"]["capabilities"])
        self.assertEqual(sorted(t["name"] for t in replies[1]["result"]["tools"]),
                         ["batch_rerun", "batch_run", "batch_status", "batch_validate"])

    def test_khong_mot_byte_rac_nao_tren_stdout(self):
        """Banner, warning, print() debug — bất cứ dòng nào không phải JSON-RPC ở
        đây là rụng kết nối, không phải một dòng log thừa."""
        entry = Path(__file__).resolve().parents[1] / "batch_mcp.py"
        proc = subprocess.run(
            [sys.executable, str(entry)],
            input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n",
            capture_output=True, text=True, timeout=30)
        for dong in proc.stdout.splitlines():
            if not dong.strip():
                continue
            json.loads(dong)   # nổ ở đây = có rác trên stdout
        self.assertEqual(len(proc.stdout.strip().splitlines()), 1)

    def test_manifest_that_trong_repo_validate_duoc_qua_tool(self):
        """batch/example.yaml trỏ vào .smoke/ (gitignore) nên có thể thiếu file —
        cái được ghim ở đây là tool CHẠY tới nơi và trả lời có cấu trúc, không phải
        nó nói hợp lệ."""
        replies = self._phien(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "batch_validate", "arguments": {"file": "batch/example.yaml"}}})
        ket_qua = replies[1]["result"]
        self.assertIn("ok", ket_qua["structuredContent"])
        self.assertEqual(ket_qua["structuredContent"]["so_run"], 2)


if __name__ == "__main__":
    unittest.main()
