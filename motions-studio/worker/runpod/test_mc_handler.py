"""Test mc_handler bằng stub — không cần GPU, ComfyUI hay mạng."""
import sys, types, pytest

@pytest.fixture
def stub(monkeypatch):
    """Dựng module worker_runtime.linux giả trước khi mc_handler import nó."""
    calls = {"claim": [], "patch": [], "ran": [], "startup": 0}
    mod = types.ModuleType("worker_runtime.linux")

    def api_claim(active_ids):
        calls["claim"].append(active_ids)
        return calls.get("next_job")

    def api_patch(job_id, **fields):
        calls["patch"].append((job_id, fields))

    def _startup():
        calls["startup"] += 1

    def run_motion(job):
        calls["ran"].append(job["id"])

    mod.api_claim = api_claim
    mod.api_patch = api_patch
    mod._startup = _startup
    mod.PIPELINES = {"motion": run_motion}
    pkg = types.ModuleType("worker_runtime")
    pkg.linux = mod
    monkeypatch.setitem(sys.modules, "worker_runtime", pkg)
    monkeypatch.setitem(sys.modules, "worker_runtime.linux", mod)
    monkeypatch.setitem(sys.modules, "runpod", types.ModuleType("runpod"))
    sys.modules.pop("mc_handler", None)
    return calls


def test_khong_co_job_thi_thoat_ngay(stub):
    stub["next_job"] = None
    import mc_handler
    assert mc_handler.handler({}) == {"ok": True, "claimed": False}
    assert stub["ran"] == []


def test_co_job_thi_chay_dung_pipeline(stub):
    stub["next_job"] = {"id": "j1", "type": "motion"}
    import mc_handler
    out = mc_handler.handler({})
    assert out["ok"] is True and out["job"] == "j1"
    assert stub["ran"] == ["j1"]


def test_job_type_la_khong_ho_tro_thi_bao_error_chu_khong_treo(stub):
    stub["next_job"] = {"id": "j2", "type": "khong-ton-tai"}
    import mc_handler
    out = mc_handler.handler({})
    assert out["ok"] is False
    assert stub["patch"] == [("j2", {"status": "error",
                                     "error": "worker khong ho tro type 'khong-ton-tai'"})]


def test_pipeline_nem_loi_thi_job_ve_error_kem_thong_diep(stub):
    def no(job):
        raise RuntimeError("bung")
    stub["next_job"] = {"id": "j3", "type": "motion"}
    import mc_handler
    mc_handler.PIPELINES["motion"] = no
    out = mc_handler.handler({})
    assert out["ok"] is False and "bung" in out["error"]
    assert stub["patch"][0][0] == "j3"
    assert stub["patch"][0][1]["status"] == "error"
