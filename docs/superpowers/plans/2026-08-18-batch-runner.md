# Batch Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thả material vào bốn thư mục vai trò trên máy local, gõ một lệnh, nhận video 1080p đã enhance — không bấm UI job nào.

**Architecture:** Một runner Python 3 chạy trên máy dev. Nó đọc manifest YAML, upload material lên pod GPU qua `POST /jobs` (multipart), poll tới khi xong, tải output về, rồi dùng output đó làm input cho chặng kế. Chaining nằm ở client; pod chỉ thấy các job rời rạc như khi bấm UI. Journal ghi sau mỗi chặng nên đứt giữa chừng thì `--resume` bắt lại đúng chỗ.

**Tech Stack:** Python 3 (stdlib: `urllib.request`, `ast`, `json`, `unittest`) + PyYAML. Không thêm dependency nào khác — `requests` và `pytest` đều KHÔNG có trên máy dev (đo 18/08/2026), nên đừng dùng.

**Spec:** `docs/superpowers/specs/2026-08-18-batch-runner-design.md`

## Global Constraints

- **Python 3, stdlib + PyYAML.** Không `requests`, không `pytest`, không thêm `package.json`. Test chạy bằng `python3 -m unittest`.
- **Repo public.** `motions-studio/setup/scrub-secrets.sh --check` phải exit 0 trước mọi commit. Không ghi domain thật, IP, cổng SSH, hay đường dẫn cá nhân vào file được track.
- **`.yaml` là của người, `.state.json` là của máy.** Runner KHÔNG BAO GIỜ ghi vào `batch/*.yaml` — PyYAML `safe_dump` xoá sạch comment.
- **Ngôn ngữ:** comment và thông báo lỗi viết tiếng Việt, theo đúng phần còn lại của `scripts/`. Thông báo lỗi phải nói **làm gì tiếp theo**, không chỉ nói cái gì sai.
- **Không tiêu tiền GPU trong test.** Mọi test chạy được khi không có pod nào tồn tại.
- **Đọc `.env` phải khớp Makefile.** Hàm `env` ở `Makefile:30` là: lấy dòng `^KEY=`, cắt từ `#` đầu tiên trở đi, xoá MỌI dấu `"`. Python phải làm y hệt — lệch nhau thì cùng một `.env` cho hai giá trị khác nhau ở `make` và ở runner.

## File Structure

```
scripts/
  batchlib/
    __init__.py         rỗng
    config.py           đọc .env + motions/.env → Settings
    pipelines.py        khai pipeline + ánh xạ field ↔ job type
    params.py           rút param từ linux.py bằng AST + validate
    manifest.py         đọc/kiểm manifest YAML; đọc/ghi state JSON
    scan.py             quét 4 thư mục vai trò → danh sách run (pair/cross)
    client.py           HTTP: submit / poll / download
    runner.py           preflight → chạy lô → journal → tổng kết
  batch-params.json     khai tay phần AST không thấy + giá trị hợp lệ
  batch_params.py       CLI: in bảng param, và cổng --check
  batch_scan.py         CLI: đẻ manifest nháp
  batch_run.py          CLI: chạy lô
  tests/
    test_batch_config.py
    test_batch_pipelines.py
    test_batch_params.py
    test_batch_manifest.py
    test_batch_scan.py
    test_batch_client.py
    test_batch_runner.py
batch/
  example.yaml          manifest mẫu (được track; dùng đường dẫn giả)
Makefile                thêm: batch-test batch-params check-batch-params batch-scan batch-validate batch batch-clean
.gitignore              thêm: out/  batch/*.yaml (trừ example)  batch/*.state.json
```

Ranh giới: `client.py` không biết pipeline là gì; `pipelines.py` không biết HTTP; `runner.py` là chỗ duy nhất biết cả hai. Test từng module chạy được mà không cần module kia.

---

### Task 1: Đọc cấu hình (`config.py`)

**Files:**
- Create: `scripts/batchlib/__init__.py` (rỗng)
- Create: `scripts/batchlib/config.py`
- Test: `scripts/tests/test_batch_config.py`

**Interfaces:**
- Consumes: —
- Produces: `env_get(path: Path, key: str) -> str`; `class ConfigError(Exception)`; `@dataclass(frozen=True) Settings(domain: str, api_key: str, instance_id: str)` với property `base_url -> str`; `load_settings(root: Path) -> Settings`; hằng `ROOT: Path`.

- [ ] **Step 1: Viết test thất bại**

```python
# scripts/tests/test_batch_config.py
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.config import ConfigError, Settings, env_get, load_settings


def _write(root: Path, root_env: str, motions_env: str = "") -> None:
    (root / "motions").mkdir(parents=True, exist_ok=True)
    (root / ".env").write_text(root_env, encoding="utf-8")
    (root / "motions" / ".env").write_text(motions_env, encoding="utf-8")


class TestEnvGet(unittest.TestCase):
    def test_khop_hanh_vi_cua_makefile(self):
        # Makefile:30 — cắt từ '#' đầu tiên, xoá MỌI dấu ". Lệch là hai giá trị khác nhau
        # cho cùng một .env ở `make` và ở runner.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text(
                'DOMAIN=api.example.test   # ghi chú\n'
                'QUOTED="giu-nguyen"\n'
                'EMPTY=\n'
                'NOTME=khong-lay\n',
                encoding="utf-8",
            )
            self.assertEqual(env_get(p, "DOMAIN"), "api.example.test")
            self.assertEqual(env_get(p, "QUOTED"), "giu-nguyen")
            self.assertEqual(env_get(p, "EMPTY"), "")
            self.assertEqual(env_get(p, "VANG_MAT"), "")

    def test_khop_bang_so_do_tu_GNU_Make_that(self):
        # ORACLE: chạy thật `$(call env,K)` của Makefile:30 trên GNU Make, 18/08/2026.
        # Hai hành vi dưới đây trông như bug và ĐÚNG LÀ bug — nhưng là bug của Makefile,
        # và env_get phải sao chép nguyên vẹn. Sửa Makefile:30 thì phải sửa bảng này
        # và env_get cùng lúc, nếu không cùng một .env sẽ có hai nghĩa.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text(
                "A=  co-space-truoc\n"
                "B=co-space-sau   \n"
                "C=binh-thuong  # ghi chu\n"
                "D=first\n"
                "D=second\n",
                encoding="utf-8",
            )
            self.assertEqual(env_get(p, "A"), "  co-space-truoc")   # đầu dòng GIỮ
            self.assertEqual(env_get(p, "B"), "co-space-sau   ")    # cuối dòng GIỮ
            self.assertEqual(env_get(p, "C"), "binh-thuong")
            self.assertEqual(env_get(p, "D"), "first second")       # $(shell) nối bằng dấu cách

    def test_file_khong_ton_tai_tra_rong_khong_no(self):
        self.assertEqual(env_get(Path("/khong/co/that/.env"), "DOMAIN"), "")


class TestLoadSettings(unittest.TestCase):
    def test_du_thi_tra_settings(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test\nGPU_INSTANCE_ID=abc123\n",
                   "NUXT_MOTION_API_KEY=mk_deadbeef\n")
            s = load_settings(root)
            self.assertEqual(s.domain, "api.example.test")
            self.assertEqual(s.api_key, "mk_deadbeef")
            self.assertEqual(s.instance_id, "abc123")
            self.assertEqual(s.base_url, "https://api.example.test")

    def test_thieu_domain_bao_lam_gi_tiep(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "GPU_INSTANCE_ID=abc\n", "NUXT_MOTION_API_KEY=mk_x\n")
            with self.assertRaises(ConfigError) as cm:
                load_settings(root)
            self.assertIn("gpu-preflight", str(cm.exception))

    def test_thieu_api_key_bao_lam_gi_tiep(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test\n", "")
            with self.assertRaises(ConfigError) as cm:
                load_settings(root)
            self.assertIn("gpu-bootstrap", str(cm.exception))

    def test_domain_co_khoang_trang_bi_chan_to_va_ro(self):
        # env_get bám Make nên nó trả về cả rác. Không chặn ở đây thì một dấu cách thừa
        # biến thành "backend không trả lời" và người dùng đi kiểm pod, tunnel, Cloudflare.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test   \n", "NUXT_MOTION_API_KEY=mk_x\n")
            with self.assertRaises(ConfigError) as cm:
                load_settings(root)
            self.assertIn(".env", str(cm.exception))
            self.assertIn("khoảng trắng", str(cm.exception))

    def test_thong_bao_loi_KHONG_lo_api_key(self):
        # Thông báo này sinh ra để người dùng đọc trên terminal — tức đúng người hay dán
        # nó lên chat hay issue. Test "chỉ cần raise ConfigError" sẽ để một lần sửa sau
        # này đưa key trở lại mà không ai biết.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test\n", "NUXT_MOTION_API_KEY=mk_secret123   \n")
            with self.assertRaises(ConfigError) as cm:
                load_settings(root)
            msg = str(cm.exception)
            self.assertNotIn("mk_secret123", msg)   # bí mật KHÔNG lọt ra
            self.assertIn("motions/.env", msg)
            # Ghim ĐÚNG chuỗi đã che: 12 dấu • rồi 3 khoảng trắng. KHÔNG dùng
            # assertIn("   ") — ba khoảng trắng đó cũng có trong thụt lề của chính
            # thông báo, nên assertion ấy vẫn xanh khi phần che đánh rơi hết khoảng
            # trắng. Ghim cả bề rộng mặt nạ lẫn khoảng trắng thì mới bắt được hồi quy.
            self.assertIn("'••••••••••••   '", msg)

    def test_gia_tri_co_dau_ngoac_nhon_khong_lam_sap_duong_bao_loi(self):
        # `.format()` trên chuỗi đã nội suy sẽ đọc `{oops}` như một trường format và
        # ném KeyError — tức đường báo lỗi tự sập, đưa ra traceback vô nghĩa thay cho
        # đúng cái chẩn đoán mà cả lớp này sinh ra để đưa. Dùng f-string toàn bộ.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test {oops}  \n", "NUXT_MOTION_API_KEY=mk_x\n")
            with self.assertRaises(ConfigError) as cm:
                load_settings(root)
            self.assertIn("{oops}", str(cm.exception))

    def test_thong_bao_loi_DOMAIN_van_in_gia_tri_that(self):
        # DOMAIN không phải bí mật, và thấy giá trị thật là cách nhanh nhất để hiểu.
        # Hai nhánh CỐ Ý khác nhau nên cả hai đều phải bị ghim.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test   \n", "NUXT_MOTION_API_KEY=mk_x\n")
            with self.assertRaises(ConfigError) as cm:
                load_settings(root)
            self.assertIn("api.example.test", str(cm.exception))

    def test_key_khai_hai_lan_bi_chan(self):
        # Makefile:30 nối hai dòng trùng khoá bằng dấu cách → giá trị có khoảng trắng.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test\n",
                   "NUXT_MOTION_API_KEY=mk_a\nNUXT_MOTION_API_KEY=mk_b\n")
            with self.assertRaises(ConfigError) as cm:
                load_settings(root)
            self.assertIn("motions/.env", str(cm.exception))

    def test_instance_id_rong_van_load_duoc(self):
        # Chưa thuê pod là trạng thái hợp lệ — preflight mới là chỗ chặn, không phải load.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _write(root, "DOMAIN=api.example.test\n", "NUXT_MOTION_API_KEY=mk_x\n")
            self.assertEqual(load_settings(root).instance_id, "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Chạy test để xác nhận nó đỏ**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_config.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batchlib'`

- [ ] **Step 3: Viết implementation tối thiểu**

```python
# scripts/batchlib/config.py
"""Đọc cấu hình từ .env gốc và motions/.env. Không làm gì khác.

env_get() CỐ Ý sao chép y hệt hàm `env` ở Makefile:30 — kể cả nhược điểm của nó
(cắt từ dấu '#' đầu tiên, xoá MỌI dấu nháy kép ở mọi vị trí). Sửa "cho đúng hơn"
ở đây sẽ tạo ra tình huống cùng một .env mà `make gpu-up` và `make batch` đọc ra
hai giá trị khác nhau — đúng loại lệch âm thầm mà scripts/check-job-types.mjs
tồn tại để chặn.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    """Cấu hình thiếu hoặc sai. Thông báo phải nói làm gì tiếp theo."""


def env_get(path: Path, key: str) -> str:
    """Y HỆT `$(call env,KEY)` ở Makefile:30, kể cả hai chỗ khó chịu của nó.

    Đo trên GNU Make thật ngày 18/08/2026 với .env:
        A=  co-space-truoc     → "  co-space-truoc"   (khoảng trắng đầu GIỮ NGUYÊN)
        B=co-space-sau         → "co-space-sau   "    (khoảng trắng cuối GIỮ NGUYÊN)
        C=binh-thuong  # ghi chu → "binh-thuong"
        D=first / D=second     → "first second"       ($(shell) nối nhiều dòng bằng dấu cách)

    KHÔNG .strip() và KHÔNG dừng ở dòng khớp đầu tiên. Sửa hai chỗ đó cho "đẹp hơn"
    nghĩa là cùng một .env cho hai giá trị khác nhau ở `make` và ở runner — đúng loại
    lệch âm thầm mà scripts/check-job-types.mjs tồn tại để chặn. Chỗ bắt .env hỏng là
    load_settings() bên dưới, không phải ở đây.

    Đổi Makefile:30 thì PHẢI đổi hàm này và bảng số đo trong test cùng lúc.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    parts = []
    for line in text.splitlines():
        if not line.startswith(f"{key}="):
            continue
        value = re.sub(r"\s*#.*$", "", line.split("=", 1)[1])
        parts.append(value.replace('"', ""))
    return " ".join(parts)


@dataclass(frozen=True)
class Settings:
    domain: str
    api_key: str
    instance_id: str

    @property
    def base_url(self) -> str:
        return f"https://{self.domain}"


def _reject_whitespace(value: str, key: str, where: str, *, secret: bool = False) -> None:
    """env_get bám sát Make nên nó TRẢ LẠI cả rác. Đây là chỗ chặn rác, và chặn to.

    Không có lớp này thì một dấu cách thừa cuối dòng DOMAIN biến thành
    "backend không trả lời" — người dùng đi kiểm pod, kiểm tunnel, kiểm Cloudflare,
    trong khi lỗi là một ký tự trong .env mà `make gpu-up` cũng đang hỏng vì nó.

    secret=True: che mọi ký tự KHÔNG phải khoảng trắng bằng '•', giữ nguyên khoảng
    trắng. Phải in ra cái gì đó thì người dùng mới THẤY được dấu cách thừa — nhưng in
    nguyên API key vào một thông báo lỗi là in nó vào đúng cái người ta hay dán lên
    chat hay issue. Che kiểu này giữ được thông tin cần và bỏ được thông tin nguy hiểm:
        'mk_abcdef  '  →  '•••••••••  '
        'mk_a mk_b'    →  '•••• •••••'
    """
    if any(c.isspace() for c in value):
        shown = "".join(c if c.isspace() else "•" for c in value) if secret else value
        raise ConfigError(
            f"{key} trong {where} có khoảng trắng: {shown!r}\n"
            f"  Thường do một trong hai: khoảng trắng thừa cuối dòng, hoặc {key} bị khai\n"
            f"  HAI LẦN (Makefile:30 nối các dòng trùng khoá bằng dấu cách, nên `make gpu-up`\n"
            f"  cũng đang hỏng vì đúng lý do này).\n"
            f"  Sửa {where} rồi chạy lại."
        )


def load_settings(root: Path = ROOT) -> Settings:
    domain = env_get(root / ".env", "DOMAIN")
    if not domain:
        raise ConfigError(
            "Thiếu DOMAIN trong .env.\n"
            "  Chạy: make gpu-preflight   (nó liệt kê mọi biến còn thiếu)"
        )
    _reject_whitespace(domain, "DOMAIN", ".env")
    api_key = env_get(root / "motions" / ".env", "NUXT_MOTION_API_KEY")
    if not api_key:
        raise ConfigError(
            "Thiếu NUXT_MOTION_API_KEY trong motions/.env.\n"
            "  Chạy: make gpu-bootstrap   (nó tự dán key của pod vào file đó)"
        )
    _reject_whitespace(api_key, "NUXT_MOTION_API_KEY", "motions/.env", secret=True)
    return Settings(
        domain=domain,
        api_key=api_key,
        instance_id=env_get(root / ".env", "GPU_INSTANCE_ID"),
    )
```

- [ ] **Step 4: Chạy test để xác nhận nó xanh**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_config.py' -v`
Expected: PASS — 11 test

- [ ] **Step 5: Commit**

```bash
git add scripts/batchlib/__init__.py scripts/batchlib/config.py scripts/tests/test_batch_config.py
git commit -m "batch: đọc .env khớp y hệt hàm env của Makefile

env_get() sao chép cả nhược điểm của Makefile:30 (cắt từ '#' đầu tiên, xoá mọi
dấu nháy). Sửa cho đúng hơn ở đây sẽ tạo tình huống cùng một .env mà make và
runner đọc ra hai giá trị khác nhau."
```

---

### Task 2: Khai pipeline (`pipelines.py`)

**Files:**
- Create: `scripts/batchlib/pipelines.py`
- Test: `scripts/tests/test_batch_pipelines.py`

**Interfaces:**
- Consumes: —
- Produces: `@dataclass(frozen=True) Stage(name: str, job_type: str, inputs: dict[str, str], output_ext: str, min_bytes: int, timeout_min: int)`; `STAGES: dict[str, Stage]`; `PIPELINES: dict[str, list[str]]`; `required_roles(pipeline: str) -> set[str]`; `optional_roles(pipeline: str) -> set[str]`; `class PipelineError(Exception)`.

Cú pháp của `Stage.inputs`: khoá là **tên field gửi lên API**, giá trị là nguồn.
- `"material:character"` — lấy từ `run.inputs["character"]`, bắt buộc.
- `"material:background?"` — như trên nhưng thiếu thì bỏ qua field.
- `"prev"` — output của chặng ngay trước.
- `"prev|material:character"` — output chặng trước nếu có, không thì material.

- [ ] **Step 1: Viết test thất bại**

```python
# scripts/tests/test_batch_pipelines.py
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.pipelines import (PIPELINES, STAGES, PipelineError, optional_roles, required_roles)


class TestKhaiBao(unittest.TestCase):
    def test_moi_chang_trong_pipeline_deu_co_trong_STAGES(self):
        for name, stages in PIPELINES.items():
            for s in stages:
                self.assertIn(s, STAGES, f"pipeline {name} nhắc chặng {s} không có khai báo")

    def test_field_khop_ten_worker_that_doc(self):
        # linux.py:4734,4735,4744,4765 · pod-smoke.sh:294-295 · linux.py:9544
        self.assertEqual(set(STAGES["tryon"].inputs), {"model", "product", "background"})
        self.assertEqual(set(STAGES["motion"].inputs), {"ref", "motion"})
        self.assertEqual(set(STAGES["enhance"].inputs), {"input"})

    def test_job_type_khop_PIPELINES_cua_worker(self):
        self.assertEqual(STAGES["tryon"].job_type, "tryon")
        self.assertEqual(STAGES["motion"].job_type, "motion")
        self.assertEqual(STAGES["enhance"].job_type, "enhance")


class TestRoles(unittest.TestCase):
    def test_tryon_motion_enhance_can_character_outfit_driver(self):
        self.assertEqual(required_roles("tryon-motion-enhance"), {"character", "outfit", "driver"})
        self.assertEqual(optional_roles("tryon-motion-enhance"), {"background"})

    def test_motion_enhance_khong_can_outfit(self):
        self.assertEqual(required_roles("motion-enhance"), {"character", "driver"})
        self.assertEqual(optional_roles("motion-enhance"), set())

    def test_prev_khong_bi_tinh_la_material(self):
        # enhance chỉ ăn output chặng trước — không được đòi thêm material nào.
        self.assertNotIn("input", required_roles("motion-enhance"))

    def test_pipeline_la_bao_loi_kem_danh_sach_co_that(self):
        with self.assertRaises(PipelineError) as cm:
            required_roles("khong-co-that")
        self.assertIn("motion-enhance", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Chạy test để xác nhận nó đỏ**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_pipelines.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batchlib.pipelines'`

- [ ] **Step 3: Viết implementation tối thiểu**

```python
# scripts/batchlib/pipelines.py
"""Pipeline là DỮ LIỆU: một danh sách chặng. Thêm pipeline = thêm một dòng.

Tên field phải khớp đúng cái worker thật đọc — đã đối chiếu ngày 18/08/2026:
  tryon    motions-studio/worker/worker_runtime/linux.py:4734,4735,4744,4765
             inputs.get("model") or ref or image
             inputs.get("product") or garment
             inputs.get("product2") or garment2
             inputs.get("background") or bg or scene
  motion   scripts/pod-smoke.sh:294-295        -F ref=@… -F motion=@…
  enhance  linux.py:9544                       inputs.get("input") or video or motion or image

Gõ sai tên field ở đây KHÔNG gây lỗi HTTP: api/src/routes/jobs.js:118-129 nhận
mọi fieldname và cứ thế ghi vào inputs. Worker mới là chỗ phát hiện thiếu, và nó
phát hiện SAU khi job đã được nhận, đã vào hàng đợi, đã đánh thức GPU.
"""
from __future__ import annotations

from dataclasses import dataclass


class PipelineError(Exception):
    """Pipeline không tồn tại hoặc khai báo sai."""


@dataclass(frozen=True)
class Stage:
    name: str
    job_type: str
    inputs: dict[str, str]   # tên field API -> nguồn
    output_ext: str
    min_bytes: int           # sàn kích thước tải về; dưới ngưỡng = MinIO trả về rỗng
    timeout_min: int


# min_bytes lấy đúng hai ngưỡng pod-smoke.sh đã dùng và đã chứng minh:
#   mp4 100_000 (pod-smoke.sh:293) · ảnh 5_000 (pod-smoke.sh:44-49, đo thật tryon 1378 KB).
STAGES: dict[str, Stage] = {
    "tryon": Stage(
        name="tryon", job_type="tryon",
        inputs={"model": "material:character",
                "product": "material:outfit",
                "background": "material:background?"},
        output_ext=".png", min_bytes=5_000, timeout_min=20,
    ),
    "motion": Stage(
        name="motion", job_type="motion",
        inputs={"ref": "prev|material:character",
                "motion": "material:driver"},
        output_ext=".mp4", min_bytes=100_000, timeout_min=60,
    ),
    # enhance 1080p60 nội suy RIFE ×4 rồi encode lại — luôn lâu hơn motion sinh ra nó.
    "enhance": Stage(
        name="enhance", job_type="enhance",
        inputs={"input": "prev"},
        output_ext=".mp4", min_bytes=100_000, timeout_min=90,
    ),
}

PIPELINES: dict[str, list[str]] = {
    "motion-enhance": ["motion", "enhance"],
    "tryon-motion-enhance": ["tryon", "motion", "enhance"],
}


def _stages(pipeline: str) -> list[Stage]:
    if pipeline not in PIPELINES:
        raise PipelineError(
            f"Pipeline không có thật: {pipeline!r}\n"
            f"  Có: {', '.join(sorted(PIPELINES))}"
        )
    return [STAGES[s] for s in PIPELINES[pipeline]]


def _roles(pipeline: str, want_optional: bool) -> set[str]:
    found: set[str] = set()
    for index, stage in enumerate(_stages(pipeline)):
        for source in stage.inputs.values():
            for alt in source.split("|"):
                # "prev" chỉ là material ở chặng ĐẦU (chưa có gì đứng trước nó).
                if alt == "prev" and index > 0:
                    break
                if not alt.startswith("material:"):
                    continue
                role = alt[len("material:"):]
                if role.endswith("?"):
                    if want_optional:
                        found.add(role[:-1])
                elif not want_optional:
                    found.add(role)
    return found


def required_roles(pipeline: str) -> set[str]:
    return _roles(pipeline, want_optional=False)


def optional_roles(pipeline: str) -> set[str]:
    return _roles(pipeline, want_optional=True)
```

- [ ] **Step 4: Chạy test để xác nhận nó xanh**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_pipelines.py' -v`
Expected: PASS — 7 test

- [ ] **Step 5: Commit**

```bash
git add scripts/batchlib/pipelines.py scripts/tests/test_batch_pipelines.py
git commit -m "batch: pipeline là dữ liệu, tên field khoá theo worker thật

Test khoá tên field vào đúng cái linux.py đọc. Cần vì gõ sai fieldname không
gây lỗi HTTP — jobs.js:118-129 nhận mọi fieldname; worker mới phát hiện thiếu,
và nó phát hiện sau khi job đã vào hàng đợi và đánh thức GPU."
```

---

### Task 3: Tra param + cổng chống trôi (`params.py`)

**Files:**
- Create: `scripts/batchlib/params.py`
- Create: `scripts/batch-params.json`
- Create: `scripts/batch_params.py`
- Modify: `Makefile` (thêm `batch-params`, `check-batch-params`, `batch-test`; cập nhật `.PHONY:1`)
- Test: `scripts/tests/test_batch_params.py`

**Interfaces:**
- Consumes: —
- Produces: `@dataclass(frozen=True) ParamInfo(name: str, default: object, line: int, source: str)`; `extract_from_ast(linux_py: Path) -> dict[str, dict[str, ParamInfo]]`; `dynamic_param_names(linux_py: Path) -> dict[str, set[str]]`; `load_curated(path: Path) -> dict`; `known_params(job_type: str, ...) -> dict[str, ParamInfo]`; `validate_params(job_type, params: dict, ...) -> list[str]`; `check_drift(...) -> list[str]`.

`source` là `"ast"` (worker đọc trực tiếp), `"extra"` (worker đọc động, AST mù) hoặc `"api"`
(worker KHÔNG đọc — `api/src/routes/jobs.js` tiêu thụ/dịch trước khi ghi DB).

**Bề mặt param có hai tầng, và đây là chỗ dễ sai nhất của cả kế hoạch.** `quality` là ví dụ:
`run_motion` không hề gọi `params.get("quality")` — `enforceMotionResolution`
(`api/src/motion-resolution.js:23-38`, gọi từ `jobs.js:105`) dịch nó thành width/height trước khi
job vào DB. Nhưng `pod-smoke.sh:292`, FE và `batch/example.yaml` đều gửi nó. Nếu `known_params()`
chỉ lấy từ AST của worker thì runner sẽ **từ chối một param mà chính repo đang dùng**. Khối `api`
tồn tại cho đúng lớp đó.

- [ ] **Step 1: Viết test thất bại**

```python
# scripts/tests/test_batch_params.py
import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.params import (check_drift, dynamic_param_names, extract_from_ast,
                             known_params, load_curated, validate_params)

REPO = Path(__file__).resolve().parents[2]
LINUX_PY = REPO / "motions-studio" / "worker" / "worker_runtime" / "linux.py"
CURATED = REPO / "scripts" / "batch-params.json"

FAKE = '''
def run_motion(job):
    params = job.get("params", {}) or {}
    preset = params.get("preset")
    frames = params.get("frames", 81)
    fps = params.get("render_fps", 16)

def run_enhance(job):
    params = job.get("params", {}) or {}
    target = params.get("targetRes") or params.get("target_res")
    _k = next((k for k in ("fpsInterp", "fps_interp", "fpsTarget") if k in params), None)
'''


class TestExtractor(unittest.TestCase):
    def test_rut_duoc_ten_default_va_dong(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            got = extract_from_ast(p)
            self.assertEqual(set(got), {"motion", "enhance"})
            self.assertEqual(got["motion"]["frames"].default, 81)
            self.assertIsNone(got["motion"]["preset"].default)
            self.assertGreater(got["motion"]["render_fps"].line, 0)
            self.assertEqual(got["motion"]["frames"].source, "ast")

    def test_khong_thay_param_doc_dong(self):
        # Đây LÀ cái lỗ, và test này khoá nó lại để không ai tưởng extractor đủ.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            self.assertNotIn("fpsInterp", extract_from_ast(p)["enhance"])

    def test_dynamic_param_names_bat_duoc_dung_cai_lo_do(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            self.assertEqual(dynamic_param_names(p)["enhance"],
                             {"fpsInterp", "fps_interp", "fpsTarget"})

    def test_chay_duoc_tren_linux_py_that(self):
        got = extract_from_ast(LINUX_PY)
        self.assertIn("motion", got)
        self.assertIn("preset", got["motion"])
        self.assertIn("targetRes", got["enhance"])
        self.assertIn("garmentType", got["tryon"])


class TestValidate(unittest.TestCase):
    def setUp(self):
        self.ast = extract_from_ast(LINUX_PY)
        self.curated = load_curated(CURATED)

    def test_key_hop_le_thi_khong_loi(self):
        self.assertEqual(
            validate_params("enhance", {"targetRes": "1080p", "fpsInterp": "60"},
                            ast_params=self.ast, curated=self.curated), [])

    def test_key_la_bi_chan_kem_goi_y(self):
        errs = validate_params("enhance", {"targetres": "1080p"},
                               ast_params=self.ast, curated=self.curated)
        self.assertEqual(len(errs), 1)
        self.assertIn("targetRes", errs[0])

    def test_gia_tri_ngoai_danh_sach_bi_chan(self):
        errs = validate_params("enhance", {"fpsInterp": "120"},
                               ast_params=self.ast, curated=self.curated)
        self.assertEqual(len(errs), 1)
        self.assertIn("60", errs[0])

    def test_fpsInterp_duoc_chap_nhan_du_AST_khong_thay(self):
        self.assertIn("fpsInterp", known_params("enhance", ast_params=self.ast, curated=self.curated))

    def test_quality_duoc_chap_nhan_du_worker_khong_doc_no(self):
        # quality là param TẦNG API: run_motion không gọi params.get("quality"),
        # enforceMotionResolution (motion-resolution.js:23) dịch nó thành width/height.
        # pod-smoke.sh:292 và batch/example.yaml đều gửi nó — chặn nó là chặn nhầm.
        self.assertNotIn("quality", self.ast.get("motion", {}))
        known = known_params("motion", ast_params=self.ast, curated=self.curated)
        self.assertIn("quality", known)
        self.assertEqual(known["quality"].source, "api")
        self.assertEqual(
            validate_params("motion", {"quality": "540p", "frames": 33},
                            ast_params=self.ast, curated=self.curated), [])

    def test_quality_sai_gia_tri_van_bi_chan(self):
        errs = validate_params("motion", {"quality": "4k"},
                               ast_params=self.ast, curated=self.curated)
        self.assertEqual(len(errs), 1)
        self.assertIn("720p", errs[0])


class TestCongChongTroi(unittest.TestCase):
    def test_repo_hien_tai_khong_troi(self):
        self.assertEqual(check_drift(LINUX_PY, CURATED), [])

    def test_param_doc_dong_moi_ma_chua_khai_thi_do(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            c = Path(d) / "curated.json"
            c.write_text('{"enhance": {"extra": {}, "allowed": {}}}', encoding="utf-8")
            errs = check_drift(p, c)
            self.assertTrue(any("fpsInterp" in e for e in errs))

    def test_api_khong_bi_doi_phai_co_trong_AST(self):
        # Key ở .api KHÔNG BAO GIỜ xuất hiện trong AST của worker — đó là định nghĩa
        # của nó. Cổng đòi điều đó là đòi một điều không bao giờ đúng.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            c = Path(d) / "curated.json"
            c.write_text(
                '{"motion": {"extra": {}, "api": {"quality": {"where": "x.js:1"}},'
                ' "allowed": {"quality": ["540p"]}},'
                ' "enhance": {"extra": {"fpsInterp": {"why": "x"}, "fps_interp": {"why": "x"},'
                ' "fpsTarget": {"why": "x"}}, "api": {}, "allowed": {}}}',
                encoding="utf-8")
            self.assertEqual(check_drift(p, c), [])

    def test_khai_tay_thua_thi_do(self):
        # Key đã hiện ra trong AST mà vẫn nằm ở "extra" = khai tay đã cũ, phải gỡ.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "fake.py"
            p.write_text(FAKE, encoding="utf-8")
            c = Path(d) / "curated.json"
            c.write_text(
                '{"motion": {"extra": {"frames": {"why": "cu"}}, "allowed": {}},'
                ' "enhance": {"extra": {"fpsInterp": {"why": "doc dong"},'
                ' "fps_interp": {"why": "x"}, "fpsTarget": {"why": "x"}}, "allowed": {}}}',
                encoding="utf-8")
            errs = check_drift(p, c)
            self.assertTrue(any("frames" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Chạy test để xác nhận nó đỏ**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_params.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batchlib.params'`

- [ ] **Step 3: Viết `batch-params.json`**

```json
{
  "_note": "Khai TAY ba thứ AST của linux.py không thấy. 'extra' = param worker đọc ĐỘNG (không phải params.get('ten')) — nếu AST đã thấy thì cổng báo thừa. 'api' = param TẦNG API: worker không bao giờ đọc, api/src/routes/jobs.js tiêu thụ hoặc dịch nó trước khi ghi DB — cổng KHÔNG đòi các key này xuất hiện trong AST. 'allowed' = giá trị hợp lệ, key phải nằm trong AST ∪ extra ∪ api.",
  "motion": {
    "extra": {},
    "api": {
      "quality": {
        "where": "motions-studio/api/src/motion-resolution.js:23",
        "why": "worker KHÔNG đọc params.get(\"quality\") — enforceMotionResolution (jobs.js:105) dịch nó thành width/height/resolution trước khi ghi DB. Đo bằng AST 18/08/2026: 'quality' không nằm trong 39 key của run_motion. pod-smoke.sh:292 và FE đều gửi param này, nên chặn nó là chặn nhầm."
      }
    },
    "allowed": {
      "quality": ["540p", "720p"],
      "render_profile": ["fast", "max"]
    }
  },
  "tryon": {
    "extra": {},
    "api": {},
    "allowed": {
      "provider": ["qwen", "gemini"]
    }
  },
  "enhance": {
    "api": {},
    "extra": {
      "fpsInterp": {
        "line": 9569,
        "why": "đọc động qua next((k for k in (\"fpsInterp\",\"fps_interp\",\"fpsTarget\") if k in params)) nên AST không thấy — mà đây đúng là param quan trọng nhất của quy trình này (48/60fps)"
      },
      "fps_interp": { "line": 9569, "why": "biến thể snake_case của fpsInterp" },
      "fpsTarget": { "line": 9569, "why": "biến thể thứ ba của fpsInterp" }
    },
    "allowed": {
      "fpsInterp": ["", "30", "48", "60"],
      "targetRes": ["1080p", "2k"],
      "engine": ["flashvsr", "seedvr2"],
      "mode": ["auto", "image", "video"]
    }
  }
}
```

- [ ] **Step 4: Viết implementation tối thiểu**

```python
# scripts/batchlib/params.py
"""Trả lời câu "param của chặng này gồm những gì" mà không bắt ai đi đọc linux.py.

Hai nguồn, cộng lại:
  1. AST của worker_runtime/linux.py — mọi params.get("ten") trong run_<type>.
  2. scripts/batch-params.json — khai TAY phần (1) không thấy, + giá trị hợp lệ.

Vì sao cần (2): param đọc động thì AST mù. Ví dụ linux.py:9569
    _fps_key = next((k for k in ("fpsInterp","fps_interp","fpsTarget") if k in params), None)
tức là ĐÚNG cái param quan trọng nhất của quy trình này (48/60fps) lại vô hình.
check_drift() bắt được đúng lớp bug đó bằng cách đọc chính các tuple kiểu này.

Vì sao validate đáng tồn tại: worker nhận CẢ HAI kiểu viết (targetRes và
target_res, cleanOnly và clean_only). Gõ sai biến thể thứ ba thì params.get()
trả None, job VẪN chạy, VẪN tính tiền, và ra kết quả của giá trị mặc định —
không lỗi, không log, không ai biết.
"""
from __future__ import annotations

import ast
import difflib
import json
from dataclasses import dataclass
from pathlib import Path

_PREFIX = "run_"
# Tên biến giữ dict params trong linux.py.
_PARAM_VARS = {"params", "p"}


@dataclass(frozen=True)
class ParamInfo:
    name: str
    default: object
    line: int
    source: str          # "ast" | "curated"


def _job_type(func_name: str) -> str:
    return func_name[len(_PREFIX):].replace("_", "-")


def _param_get_calls(node: ast.AST):
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        if not isinstance(sub.func, ast.Attribute) or sub.func.attr != "get":
            continue
        base = sub.func.value
        name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
        if name in _PARAM_VARS:
            yield sub


def extract_from_ast(linux_py: Path) -> dict[str, dict[str, ParamInfo]]:
    tree = ast.parse(linux_py.read_text(encoding="utf-8", errors="replace"))
    out: dict[str, dict[str, ParamInfo]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith(_PREFIX):
            continue
        found: dict[str, ParamInfo] = {}
        for call in _param_get_calls(node):
            if not call.args or not isinstance(call.args[0], ast.Constant):
                continue
            key = call.args[0].value
            if not isinstance(key, str) or key in found:
                continue
            default: object = None
            if len(call.args) > 1:
                try:
                    default = ast.literal_eval(call.args[1])
                except (ValueError, SyntaxError):
                    default = "<biểu thức>"
            found[key] = ParamInfo(key, default, call.lineno, "ast")
        if found:
            out[_job_type(node.name)] = found
    return out


def dynamic_param_names(linux_py: Path) -> dict[str, set[str]]:
    """Các tên param đọc động: next((k for k in (…) if k in params), …).

    Đây là lỗ của extract_from_ast(), và đọc chính cái idiom đó là cách duy nhất
    bắt được nó mà không phải nhớ bằng đầu.
    """
    tree = ast.parse(linux_py.read_text(encoding="utf-8", errors="replace"))
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith(_PREFIX):
            continue
        names: set[str] = set()
        for gen in (n for n in ast.walk(node) if isinstance(n, ast.GeneratorExp)):
            uses_params = any(
                isinstance(c, ast.Compare)
                and isinstance(c.ops[0], ast.In)
                and isinstance(c.comparators[0], ast.Name)
                and c.comparators[0].id in _PARAM_VARS
                for c in ast.walk(gen) if isinstance(c, ast.Compare) and c.ops
            )
            if not uses_params:
                continue
            for comp in gen.generators:
                if isinstance(comp.iter, (ast.Tuple, ast.List)):
                    for element in comp.iter.elts:
                        if isinstance(element, ast.Constant) and isinstance(element.value, str):
                            names.add(element.value)
        if names:
            out[_job_type(node.name)] = names
    return out


def load_curated(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def known_params(job_type: str, *, ast_params: dict, curated: dict) -> dict[str, ParamInfo]:
    """AST ∪ extra ∪ api.

    'api' là tầng thứ hai mà AST của worker không bao giờ nhìn thấy: param do
    api/src/routes/jobs.js tiêu thụ hoặc dịch trước khi ghi DB. Ví dụ 'quality'
    — run_motion không hề gọi params.get("quality"); enforceMotionResolution
    (motion-resolution.js:23) đổi nó thành width/height. Bỏ tầng này thì runner
    chặn đúng những param mà chính repo đang gửi (pod-smoke.sh:292).
    """
    block = curated.get(job_type, {})
    out = dict(ast_params.get(job_type, {}))
    for source in ("extra", "api"):
        for name, meta in (block.get(source, {}) or {}).items():
            out.setdefault(name, ParamInfo(name, None, int(meta.get("line") or 0), source))
    return out


def validate_params(job_type: str, params: dict, *, ast_params: dict, curated: dict) -> list[str]:
    known = known_params(job_type, ast_params=ast_params, curated=curated)
    allowed = curated.get(job_type, {}).get("allowed", {}) or {}
    errors: list[str] = []
    for key, value in (params or {}).items():
        if key not in known:
            near = difflib.get_close_matches(key, list(known), n=3, cutoff=0.6)
            hint = f" — ý bạn là {', '.join(near)}?" if near else \
                   f" — xem: make batch-params TYPE={job_type}"
            errors.append(f"{job_type}: param không có thật {key!r}{hint}")
            continue
        if key in allowed and str(value) not in [str(v) for v in allowed[key]]:
            shown = ", ".join(repr(v) for v in allowed[key])
            errors.append(f"{job_type}.{key}: {value!r} không hợp lệ — chỉ nhận {shown}")
    return errors


def check_drift(linux_py: Path, curated_path: Path) -> list[str]:
    """Cổng: khai tay phải khớp linux.py. Cùng vai trò với check-job-types.mjs."""
    ast_params = extract_from_ast(linux_py)
    dynamic = dynamic_param_names(linux_py)
    curated = load_curated(curated_path)
    errors: list[str] = []

    for job_type, names in dynamic.items():
        declared = set(curated.get(job_type, {}).get("extra", {}) or {})
        seen_by_ast = set(ast_params.get(job_type, {}))
        for name in sorted(names - declared - seen_by_ast):
            errors.append(
                f"{job_type}: {name!r} đọc động trong linux.py nên AST không thấy, "
                f"mà batch-params.json cũng chưa khai → validate sẽ chặn nhầm nó. "
                f"Thêm vào \"{job_type}\".extra."
            )

    for job_type, block in curated.items():
        seen_by_ast = set(ast_params.get(job_type, {}))
        for name in sorted(set(block.get("extra", {}) or {}) & seen_by_ast):
            errors.append(
                f"{job_type}: {name!r} khai tay ở .extra nhưng AST ĐÃ thấy nó → "
                f"khai báo thừa, gỡ khỏi batch-params.json."
            )
        # .api CỐ Ý không bị kiểm theo AST: worker không bao giờ đọc những key đó
        # (jobs.js dịch/tiêu thụ chúng trước), nên đòi chúng xuất hiện trong AST là
        # đòi một điều không bao giờ đúng. Chúng được kiểm bằng trường "where" trỏ
        # tới dòng code API thật, và bằng mắt người khi thêm.
        known = seen_by_ast | set(block.get("extra", {}) or {}) | set(block.get("api", {}) or {})
        for name in sorted(set(block.get("allowed", {}) or {}) - known):
            errors.append(
                f"{job_type}: .allowed có {name!r} nhưng không nằm trong AST, .extra hay .api "
                f"→ khai báo cũ, gỡ đi."
            )
    return errors
```

- [ ] **Step 5: Viết CLI `batch_params.py`**

```python
#!/usr/bin/env python3
"""In bảng param của một job type, và làm cổng chống trôi.

    python3 scripts/batch_params.py motion      # bảng param
    python3 scripts/batch_params.py --check     # cổng (make check-batch-params)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.params import check_drift, extract_from_ast, known_params, load_curated

ROOT = Path(__file__).resolve().parents[1]
LINUX_PY = ROOT / "motions-studio" / "worker" / "worker_runtime" / "linux.py"
CURATED = ROOT / "scripts" / "batch-params.json"


def main(argv: list[str]) -> int:
    if not LINUX_PY.is_file():
        print(f"✗ không thấy {LINUX_PY.relative_to(ROOT)}", file=sys.stderr)
        return 1

    if "--check" in argv:
        errors = check_drift(LINUX_PY, CURATED)
        if errors:
            print("✗ batch-params.json đã trôi khỏi linux.py:", file=sys.stderr)
            for e in errors:
                print(f"    {e}", file=sys.stderr)
            return 1
        print("✓ batch-params.json khớp linux.py")
        return 0

    ast_params = extract_from_ast(LINUX_PY)
    curated = load_curated(CURATED)
    job_type = next((a for a in argv if not a.startswith("-")), "")
    if not job_type:
        print(f"Job type có param: {', '.join(sorted(ast_params))}")
        print("Dùng: make batch-params TYPE=motion")
        return 0
    if job_type not in ast_params and job_type not in curated:
        print(f"✗ không có job type {job_type!r}", file=sys.stderr)
        return 1

    known = known_params(job_type, ast_params=ast_params, curated=curated)
    allowed = curated.get(job_type, {}).get("allowed", {}) or {}
    print(f"{job_type} — {len(known)} param\n")
    print(f"  {'param':28} {'mặc định':18} {'giá trị hợp lệ':26} nguồn")
    print(f"  {'-' * 28} {'-' * 18} {'-' * 26} -----")
    api_block = curated.get(job_type, {}).get("api", {}) or {}
    for name in sorted(known):
        info = known[name]
        values = "/".join(str(v) if v != "" else "(rỗng)" for v in allowed.get(name, [])) or "—"
        if info.source == "ast":
            where = f"linux.py:{info.line}"
        elif info.source == "api":
            where = str(api_block.get(name, {}).get("where") or "tầng API")
        else:
            where = f"linux.py:{info.line} (đọc động)"
        print(f"  {name:28} {str(info.default):18} {values:26} {where}")
    print("\n  Đọc comment gốc ở đúng dòng trên — repo này comment dày, đó là tài liệu thật.")
    if api_block:
        print("  Dòng 'tầng API' là param worker KHÔNG đọc: jobs.js dịch/tiêu thụ nó trước khi ghi DB.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 6: Thêm target vào Makefile**

Sửa dòng `.PHONY` ở `Makefile:2` — thêm vào cuối: ` batch-test batch-params check-batch-params`

Chèn ngay sau target `check-job-types` (`Makefile:35-36`):

```makefile
batch-test: ## Gate: unit test của batch runner (không cần pod, không tốn tiền)
	@python3 -m unittest discover -s scripts/tests -p 'test_batch_*.py'

batch-params: ## Liệt kê param một job type nhận (TYPE=motion|tryon|enhance)
	@python3 scripts/batch_params.py $${TYPE:-}

check-batch-params: ## Gate: scripts/batch-params.json phải khớp linux.py
	@python3 scripts/batch_params.py --check
```

- [ ] **Step 7: Chạy test để xác nhận nó xanh**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_params.py' -v`
Expected: PASS — 13 test

Nếu `test_repo_hien_tai_khong_troi` đỏ: đọc thông báo, nó nói đúng key nào thiếu/thừa trong `batch-params.json`. Sửa file JSON, không sửa test — đó chính là việc cổng này tồn tại để làm.

- [ ] **Step 8: Chạy thật, mắt nhìn**

Run: `make batch-params TYPE=enhance && make check-batch-params`
Expected: bảng param có `fpsInterp` với giá trị hợp lệ `(rỗng)/30/48/60` nguồn `khai tay:9569`; cổng in `✓ batch-params.json khớp linux.py`

- [ ] **Step 9: Commit**

```bash
git add scripts/batchlib/params.py scripts/batch_params.py scripts/batch-params.json \
        scripts/tests/test_batch_params.py Makefile
git commit -m "batch: tra param bằng AST, và khai thẳng lỗ của nó

extract_from_ast đọc mọi params.get(\"ten\") trong run_<type> — motion 39 key,
tryon 14, enhance 7. Nhưng nó MÙ với param đọc động, và đúng cái quan trọng
nhất của quy trình này lại đọc động: fpsInterp (48/60fps) qua
next((k for k in (...) if k in params)) tại linux.py:9569.

Nên dynamic_param_names() đọc chính idiom đó, và check_drift() đỏ khi linux.py
thêm một param đọc động chưa khai trong batch-params.json — cùng vai trò với
check-job-types.mjs, cùng lý do: danh sách chép hai chỗ thì lệch âm thầm."
```

---

### Task 4: Manifest + journal (`manifest.py`)

**Files:**
- Create: `scripts/batchlib/manifest.py`
- Test: `scripts/tests/test_batch_manifest.py`

**Interfaces:**
- Consumes: `pipelines.PIPELINES`, `pipelines.required_roles`, `pipelines.optional_roles`, `params.validate_params`
- Produces: `@dataclass Run(id: str, pipeline: str, inputs: dict[str, Path], stage_params: dict[str, dict])`; `@dataclass Manifest(path: Path, runs: list[Run])`; `load_manifest(path: Path) -> Manifest`; `validate_manifest(m: Manifest, *, ast_params, curated) -> list[str]`; `dump_runs(runs: list[Run]) -> str`; `state_path_for(manifest_path: Path) -> Path`; `load_state(path: Path) -> dict`; `save_state(path: Path, state: dict) -> None`; `class ManifestError(Exception)`.

Hình dạng state:
```json
{"version": 1, "batch": "2026-08-18-1430",
 "runs": {"<run-id>": {"status": "done|error|pending",
                       "stages": {"motion": {"job_id": "…", "status": "done",
                                             "elapsed_sec": 310, "file": "out/…/01-motion.mp4"}}}}}
```

- [ ] **Step 1: Viết test thất bại**

```python
# scripts/tests/test_batch_manifest.py
import json, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import (ManifestError, dump_runs, load_manifest, load_state,
                               save_state, state_path_for, validate_manifest)
from batchlib.params import extract_from_ast, load_curated

REPO = Path(__file__).resolve().parents[2]
AST = extract_from_ast(REPO / "motions-studio" / "worker" / "worker_runtime" / "linux.py")
CURATED = load_curated(REPO / "scripts" / "batch-params.json")

GOOD = """
defaults:
  enhance: { targetRes: 1080p, fpsInterp: "60" }
runs:
  - id: mauA
    pipeline: tryon-motion-enhance
    inputs:
      character: char.jpg
      outfit: vay.jpg
      background: bg.jpg
      driver: drv.mp4
    motion: { preset: drv-30s }
  - id: mauB
    pipeline: motion-enhance
    inputs:
      character: char.jpg
      driver: drv.mp4
    enhance: { fpsInterp: "48" }
"""


def _fixture(tmp: Path, text: str = GOOD) -> Path:
    for name in ("char.jpg", "vay.jpg", "bg.jpg", "drv.mp4"):
        (tmp / name).write_bytes(b"x")
    p = tmp / "b.yaml"
    p.write_text(text, encoding="utf-8")
    return p


class TestLoad(unittest.TestCase):
    def test_doc_duoc_run_va_giai_duong_dan_tuong_doi(self):
        with tempfile.TemporaryDirectory() as d:
            m = load_manifest(_fixture(Path(d)))
            self.assertEqual([r.id for r in m.runs], ["mauA", "mauB"])
            self.assertTrue(m.runs[0].inputs["character"].is_absolute())
            self.assertTrue(m.runs[0].inputs["character"].is_file())

    def test_defaults_duoc_ap_va_run_ghi_de_duoc(self):
        with tempfile.TemporaryDirectory() as d:
            m = load_manifest(_fixture(Path(d)))
            self.assertEqual(m.runs[0].stage_params["enhance"]["fpsInterp"], "60")
            self.assertEqual(m.runs[0].stage_params["enhance"]["targetRes"], "1080p")
            self.assertEqual(m.runs[1].stage_params["enhance"]["fpsInterp"], "48")
            self.assertEqual(m.runs[1].stage_params["enhance"]["targetRes"], "1080p")

    def test_defaults_khong_lan_sang_pipeline_khong_chay_chang_do(self):
        # defaults cho tryon KHÔNG được dính vào run motion-enhance, nếu không validate
        # sẽ báo sai "có param cho chặng tryon" cho một mặc định hoàn toàn vô hại.
        with tempfile.TemporaryDirectory() as d:
            text = GOOD.replace("defaults:\n", "defaults:\n  tryon: { provider: qwen }\n")
            m = load_manifest(_fixture(Path(d), text))
            self.assertIn("tryon", m.runs[0].stage_params)          # tryon-motion-enhance
            self.assertNotIn("tryon", m.runs[1].stage_params)       # motion-enhance
            self.assertEqual(validate_manifest(m, ast_params=AST, curated=CURATED), [])

    def test_param_ghi_thang_cho_chang_khong_chay_van_bi_bat(self):
        # Khác với defaults: viết thẳng `tryon:` vào một run motion-enhance là lỗi cố ý,
        # và im lặng bỏ qua nó nghĩa là người dùng tưởng mình đã chỉnh được cái gì đó.
        with tempfile.TemporaryDirectory() as d:
            text = GOOD.replace("    enhance: { fpsInterp: \"48\" }",
                                "    enhance: { fpsInterp: \"48\" }\n    tryon: { provider: qwen }")
            errs = validate_manifest(load_manifest(_fixture(Path(d), text)),
                                     ast_params=AST, curated=CURATED)
            self.assertTrue(any("tryon" in e for e in errs))

    def test_trung_id_bi_chan(self):
        with tempfile.TemporaryDirectory() as d:
            text = GOOD.replace("id: mauB", "id: mauA")
            with self.assertRaises(ManifestError) as cm:
                load_manifest(_fixture(Path(d), text))
            self.assertIn("mauA", str(cm.exception))

    def test_khong_co_runs_bi_chan(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.yaml"
            p.write_text("defaults: {}\n", encoding="utf-8")
            with self.assertRaises(ManifestError):
                load_manifest(p)


class TestValidate(unittest.TestCase):
    def test_manifest_tot_thi_khong_loi(self):
        with tempfile.TemporaryDirectory() as d:
            m = load_manifest(_fixture(Path(d)))
            self.assertEqual(validate_manifest(m, ast_params=AST, curated=CURATED), [])

    def test_thieu_file_bi_bat_truoc_khi_ton_gpu(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp)
            (tmp / "drv.mp4").unlink()
            errs = validate_manifest(load_manifest(p), ast_params=AST, curated=CURATED)
            self.assertTrue(any("drv.mp4" in e for e in errs))

    def test_thieu_material_bat_buoc_bi_bat(self):
        with tempfile.TemporaryDirectory() as d:
            text = GOOD.replace("      outfit: vay.jpg\n", "")
            errs = validate_manifest(load_manifest(_fixture(Path(d), text)),
                                     ast_params=AST, curated=CURATED)
            self.assertTrue(any("outfit" in e for e in errs))

    def test_param_la_bi_bat(self):
        with tempfile.TemporaryDirectory() as d:
            text = GOOD.replace('fpsInterp: "48"', 'fpsinterp: "48"')
            errs = validate_manifest(load_manifest(_fixture(Path(d), text)),
                                     ast_params=AST, curated=CURATED)
            self.assertTrue(any("fpsInterp" in e for e in errs))

    def test_pipeline_la_bi_bat(self):
        with tempfile.TemporaryDirectory() as d:
            text = GOOD.replace("pipeline: motion-enhance", "pipeline: khong-co")
            errs = validate_manifest(load_manifest(_fixture(Path(d), text)),
                                     ast_params=AST, curated=CURATED)
            self.assertTrue(any("khong-co" in e for e in errs))

    def test_gom_HET_loi_chu_khong_dung_o_cai_dau_tien(self):
        with tempfile.TemporaryDirectory() as d:
            text = GOOD.replace('fpsInterp: "48"', 'fpsinterp: "48"') \
                       .replace("pipeline: tryon-motion-enhance", "pipeline: khong-co")
            errs = validate_manifest(load_manifest(_fixture(Path(d), text)),
                                     ast_params=AST, curated=CURATED)
            self.assertGreaterEqual(len(errs), 2)


class TestState(unittest.TestCase):
    def test_duong_dan_state_nam_canh_manifest(self):
        self.assertEqual(state_path_for(Path("/x/batch/a.yaml")), Path("/x/batch/a.state.json"))

    def test_chua_co_state_thi_tra_rong_khong_no(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(load_state(Path(d) / "chua-co.json"), {"version": 1, "runs": {}})

    def test_ghi_roi_doc_lai_khop(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            state = {"version": 1, "batch": "2026-08-18-1430",
                     "runs": {"mauA": {"status": "done", "stages": {
                         "motion": {"job_id": "j1", "status": "done",
                                    "elapsed_sec": 310, "file": "out/x/02-motion.mp4"}}}}}
            save_state(p, state)
            self.assertEqual(load_state(p), state)

    def test_ghi_la_atomic_khong_de_lai_file_tam(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            save_state(p, {"version": 1, "runs": {}})
            self.assertEqual([f.name for f in Path(d).iterdir()], ["s.json"])

    def test_state_hong_khong_giet_lo_dang_chay(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            p.write_text("{ khong phai json", encoding="utf-8")
            self.assertEqual(load_state(p), {"version": 1, "runs": {}})


class TestDumpRuns(unittest.TestCase):
    def test_dump_roi_load_lai_ra_dung_the(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            m = load_manifest(_fixture(tmp))
            out = tmp / "again.yaml"
            out.write_text(dump_runs(m.runs), encoding="utf-8")
            again = load_manifest(out)
            self.assertEqual([r.id for r in again.runs], ["mauA", "mauB"])
            self.assertEqual(again.runs[1].stage_params["enhance"]["fpsInterp"], "48")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Chạy test để xác nhận nó đỏ**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_manifest.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batchlib.manifest'`

- [ ] **Step 3: Viết implementation tối thiểu**

```python
# scripts/batchlib/manifest.py
"""Manifest (của người, .yaml) và journal (của máy, .state.json).

RANH GIỚI CỨNG: module này KHÔNG BAO GIỜ ghi vào file .yaml. PyYAML safe_dump
xoá sạch comment, mà comment chính là chỗ người dùng ghi "preset này cho khách
A, đừng đổi". Ghi đè một lần là mất hết, không lấy lại được. dump_runs() chỉ
dùng cho batch_scan.py — lúc đó file còn chưa tồn tại nên chưa có gì để mất.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .params import validate_params
from .pipelines import PIPELINES, optional_roles, required_roles

STAGE_KEYS = set()
for _stages in PIPELINES.values():
    STAGE_KEYS.update(_stages)


class ManifestError(Exception):
    """Manifest không đọc được. Khác với 'đọc được nhưng sai' — cái đó là validate."""


@dataclass
class Run:
    id: str
    pipeline: str
    inputs: dict[str, Path] = field(default_factory=dict)
    stage_params: dict[str, dict] = field(default_factory=dict)


@dataclass
class Manifest:
    path: Path
    runs: list[Run]


def load_manifest(path: Path) -> Manifest:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path}: YAML hỏng — {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{path}: gốc file phải là một mapping có khoá 'runs:'")

    entries = raw.get("runs")
    if not isinstance(entries, list) or not entries:
        raise ManifestError(f"{path}: không có 'runs:' nào — chạy make batch-scan để đẻ ra")

    defaults = raw.get("defaults") or {}
    base = path.parent.resolve()
    runs: list[Run] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ManifestError(f"{path}: runs[{index}] phải là mapping")
        run_id = str(entry.get("id") or "").strip()
        if not run_id:
            raise ManifestError(f"{path}: runs[{index}] thiếu 'id'")
        if run_id in seen:
            raise ManifestError(
                f"{path}: hai run cùng id {run_id!r} — output sẽ đè lên nhau, đổi một cái"
            )
        seen.add(run_id)

        inputs: dict[str, Path] = {}
        for role, value in (entry.get("inputs") or {}).items():
            expanded = Path(str(value)).expanduser()
            inputs[str(role)] = expanded if expanded.is_absolute() else (base / expanded).resolve()

        pipeline = str(entry.get("pipeline") or "")
        # defaults CHỈ áp cho chặng mà pipeline của run thật sự chạy. Merge cho mọi chặng
        # thì đặt `defaults: { tryon: {...} }` sẽ làm MỌI run motion-enhance bị validate
        # báo sai "có param cho chặng tryon nhưng pipeline không chạy chặng đó" — người
        # dùng đặt một mặc định vô hại và cả lô bị chặn với lý do khó hiểu.
        # Param ghi THẲNG trong run thì vẫn merge bất kể pipeline: đó là lỗi cố ý của
        # người dùng và validate phải nói ra, khác hẳn với một default vô tình lan sang.
        # Pipeline lạ → merge hết; validate sẽ bắt chính cái pipeline đó trước.
        applicable = set(PIPELINES.get(pipeline, STAGE_KEYS))
        stage_params: dict[str, dict] = {}
        for stage in STAGE_KEYS:
            merged = dict(defaults.get(stage) or {}) if stage in applicable else {}
            merged.update(entry.get(stage) or {})
            if merged:
                stage_params[stage] = merged

        runs.append(Run(id=run_id, pipeline=pipeline,
                        inputs=inputs, stage_params=stage_params))
    return Manifest(path=path, runs=runs)


def validate_manifest(m: Manifest, *, ast_params: dict, curated: dict) -> list[str]:
    """Gom HẾT lỗi rồi trả về, không dừng ở cái đầu tiên.

    Dừng sớm nghĩa là sửa một lỗi, chạy lại, gặp lỗi kế — với manifest 12 run thì
    đó là 12 vòng. Mà mục đích của validate là nổ TRƯỚC khi job đầu tiên tiêu GPU.
    """
    errors: list[str] = []
    for run in m.runs:
        where = f"run {run.id!r}"
        if run.pipeline not in PIPELINES:
            errors.append(
                f"{where}: pipeline không có thật {run.pipeline!r} — có: {', '.join(sorted(PIPELINES))}"
            )
            continue

        needed = required_roles(run.pipeline)
        for role in sorted(needed - set(run.inputs)):
            errors.append(f"{where}: pipeline {run.pipeline} cần material {role!r} nhưng inputs không có")
        for role in sorted(set(run.inputs) - needed - optional_roles(run.pipeline)):
            errors.append(f"{where}: material {role!r} không được pipeline {run.pipeline} dùng tới")
        for role, path in sorted(run.inputs.items()):
            if not path.is_file():
                errors.append(f"{where}: không thấy file {role} → {path}")

        for stage in PIPELINES[run.pipeline]:
            errors.extend(
                f"{where}: {msg}"
                for msg in validate_params(stage, run.stage_params.get(stage, {}),
                                           ast_params=ast_params, curated=curated)
            )
        for stage in sorted(set(run.stage_params) - set(PIPELINES[run.pipeline])):
            errors.append(
                f"{where}: có param cho chặng {stage!r} nhưng pipeline {run.pipeline} "
                f"không chạy chặng đó — param sẽ bị bỏ qua âm thầm"
            )
    return errors


def dump_runs(runs: list[Run]) -> str:
    """Sinh YAML cho batch_scan.py. KHÔNG dùng để ghi đè manifest đã có."""
    payload = []
    for run in runs:
        entry: dict = {"id": run.id, "pipeline": run.pipeline,
                       "inputs": {k: str(v) for k, v in run.inputs.items()}}
        for stage, values in run.stage_params.items():
            entry[stage] = values
        payload.append(entry)
    return yaml.safe_dump({"runs": payload}, allow_unicode=True, sort_keys=False, width=100)


def state_path_for(manifest_path: Path) -> Path:
    return manifest_path.with_suffix(".state.json")


def load_state(path: Path) -> dict:
    """State hỏng KHÔNG được giết lô đang chạy — tệ nhất là mất quyền resume."""
    empty = {"version": 1, "runs": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    return data if isinstance(data, dict) and "runs" in data else empty


def save_state(path: Path, state: dict) -> None:
    """Ghi atomic: state ghi sau MỖI chặng, nên Ctrl-C giữa lúc ghi là chuyện sẽ xảy ra."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
```

- [ ] **Step 4: Chạy test để xác nhận nó xanh**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_manifest.py' -v`
Expected: PASS — 17 test

- [ ] **Step 5: Commit**

```bash
git add scripts/batchlib/manifest.py scripts/tests/test_batch_manifest.py
git commit -m "batch: manifest của người, state của máy — không lẫn vào nhau

Module này không bao giờ ghi vào .yaml: PyYAML safe_dump xoá sạch comment, mà
comment là chỗ người dùng ghi 'preset này cho khách A, đừng đổi'. Journal đi
sang .state.json.

validate gom HẾT lỗi rồi mới trả về. Dừng ở lỗi đầu tiên nghĩa là manifest 12
run phải chạy 12 vòng sửa — mà cả điểm của validate là nổ trước khi job đầu
tiên tiêu GPU."
```

---

### Task 5: Quét thư mục vai trò (`scan.py` + `batch_scan.py`)

**Files:**
- Create: `scripts/batchlib/scan.py`
- Create: `scripts/batch_scan.py`
- Create: `batch/example.yaml`
- Modify: `Makefile` (thêm `batch-scan`; cập nhật `.PHONY:1`)
- Modify: `.gitignore`
- Test: `scripts/tests/test_batch_scan.py`

**Interfaces:**
- Consumes: `manifest.Run`, `manifest.dump_runs`
- Produces: `ROLE_DIRS: dict[str, str]`; `slugify(text: str) -> str`; `collect(materials_dir: Path) -> dict[str, list[Path]]`; `build_runs(found: dict[str, list[Path]], mode: str) -> list[Run]`; `class ScanError(Exception)`.

Quy tắc ghép, phải khớp thông báo `batch_scan.py` in ra:
- `pair` — số run = min(số character, số driver, số outfit nếu có outfit).
- `cross` — tích Descartes của character × outfit × driver (background KHÔNG nhân).
- **`background` luôn xoay vòng** (`bgs[i % len(bgs)]`), không giới hạn số run. Nền thường chỉ có một cái dùng chung; để nó giới hạn thì một file nền biến lô 12 run thành 1 run.
- Thứ tự file: `sorted()` theo tên, để hai lần quét cùng thư mục ra cùng manifest.

- [ ] **Step 1: Viết test thất bại**

```python
# scripts/tests/test_batch_scan.py
import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.scan import ScanError, build_runs, collect, slugify


def _materials(tmp: Path, chars=2, outfits=0, bgs=0, drivers=2) -> Path:
    root = tmp / "materials"
    for name, count, ext in (("characters", chars, ".jpg"), ("outfits", outfits, ".jpg"),
                             ("backgrounds", bgs, ".jpg"), ("drivers", drivers, ".mp4")):
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            (d / f"{name[:-1]}-{i}{ext}").write_bytes(b"x")
    return root


class TestSlugify(unittest.TestCase):
    def test_bo_dau_cach_va_ky_tu_la(self):
        self.assertEqual(slugify("Vay Đỏ (2).jpg"), "vay-do-2-jpg")

    def test_gop_dau_noi_lien_tiep_va_cat_hai_dau(self):
        self.assertEqual(slugify("--a__  b--"), "a-b")

    def test_rong_thi_tra_chuoi_thay_the_chu_khong_tra_rong(self):
        self.assertEqual(slugify("!!!"), "x")


class TestCollect(unittest.TestCase):
    def test_gom_theo_thu_muc_vai_tro_va_sap_xep_on_dinh(self):
        with tempfile.TemporaryDirectory() as d:
            found = collect(_materials(Path(d), chars=3, drivers=2))
            self.assertEqual(len(found["character"]), 3)
            self.assertEqual([p.name for p in found["character"]], sorted(p.name for p in found["character"]))
            self.assertEqual(found["outfit"], [])

    def test_bo_qua_file_an_va_thu_muc_con(self):
        with tempfile.TemporaryDirectory() as d:
            root = _materials(Path(d))
            (root / "characters" / ".DS_Store").write_bytes(b"x")
            (root / "characters" / "sub").mkdir()
            self.assertEqual(len(collect(root)["character"]), 2)

    def test_thu_muc_khong_ton_tai_bao_ro(self):
        with self.assertRaises(ScanError) as cm:
            collect(Path("/khong/co/that"))
        self.assertIn("characters", str(cm.exception))

    def test_khong_co_character_hoac_driver_thi_bao(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ScanError) as cm:
                collect(_materials(Path(d), chars=0, drivers=2))
            self.assertIn("characters", str(cm.exception))


class TestBuildRuns(unittest.TestCase):
    def test_pair_dung_o_ngan_ngan_nhat(self):
        with tempfile.TemporaryDirectory() as d:
            runs = build_runs(collect(_materials(Path(d), chars=3, outfits=2, drivers=2)), "pair")
            self.assertEqual(len(runs), 2)
            self.assertEqual(runs[0].pipeline, "tryon-motion-enhance")

    def test_pair_khong_outfit_thi_pipeline_ngan(self):
        with tempfile.TemporaryDirectory() as d:
            runs = build_runs(collect(_materials(Path(d), chars=2, drivers=2)), "pair")
            self.assertEqual([r.pipeline for r in runs], ["motion-enhance"] * 2)
            self.assertNotIn("outfit", runs[0].inputs)

    def test_cross_la_tich_descartes_khong_tinh_background(self):
        with tempfile.TemporaryDirectory() as d:
            runs = build_runs(collect(_materials(Path(d), chars=3, outfits=2, bgs=1, drivers=2)), "cross")
            self.assertEqual(len(runs), 12)

    def test_background_xoay_vong_khong_gioi_han_so_run(self):
        # Một file nền dùng chung KHÔNG được bóp lô 12 run xuống 1 run.
        with tempfile.TemporaryDirectory() as d:
            runs = build_runs(collect(_materials(Path(d), chars=3, outfits=2, bgs=1, drivers=2)), "pair")
            self.assertEqual(len(runs), 2)
            self.assertTrue(all("background" in r.inputs for r in runs))

    def test_khong_co_background_thi_khong_co_khoa_do(self):
        with tempfile.TemporaryDirectory() as d:
            runs = build_runs(collect(_materials(Path(d), chars=2, outfits=2, drivers=2)), "pair")
            self.assertNotIn("background", runs[0].inputs)

    def test_id_sinh_tu_ten_material(self):
        with tempfile.TemporaryDirectory() as d:
            runs = build_runs(collect(_materials(Path(d), chars=2, outfits=2, drivers=2)), "pair")
            self.assertEqual(runs[0].id, "character-0__outfit-0__driver-0")

    def test_id_trung_thi_them_hau_to(self):
        with tempfile.TemporaryDirectory() as d:
            root = _materials(Path(d), chars=0, drivers=1)
            for name in ("a.jpg", "a.png"):     # cùng stem -> cùng slug
                (root / "characters" / name).write_bytes(b"x")
            (root / "drivers" / "drv.mp4").write_bytes(b"x")
            runs = build_runs(collect(root), "cross")
            self.assertEqual(len({r.id for r in runs}), len(runs))
            self.assertTrue(any(r.id.endswith("-2") for r in runs))

    def test_mode_la_bi_chan(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ScanError):
                build_runs(collect(_materials(Path(d))), "khong-co")

    def test_ket_qua_on_dinh_giua_hai_lan_quet(self):
        with tempfile.TemporaryDirectory() as d:
            found = collect(_materials(Path(d), chars=3, outfits=2, drivers=2))
            self.assertEqual([r.id for r in build_runs(found, "cross")],
                             [r.id for r in build_runs(found, "cross")])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Chạy test để xác nhận nó đỏ**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_scan.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batchlib.scan'`

- [ ] **Step 3: Viết implementation tối thiểu**

```python
# scripts/batchlib/scan.py
"""Quét bốn thư mục vai trò → danh sách run. Không đặt tên file, chỉ thả đúng ngăn.

  ~/materials/characters/  outfits/  backgrounds/  drivers/

Ghép:
  pair   số run = min(character, driver, và outfit nếu có outfit)
  cross  tích Descartes character × outfit × driver

background KHÔNG tham gia giới hạn hay nhân — nó XOAY VÒNG. Nền thường chỉ có
một cái dùng chung; nếu để nó giới hạn thì một file nền biến lô 12 run thành 1
run, và người dùng sẽ không hiểu vì sao.

Kết quả phải ỔN ĐỊNH giữa hai lần quét cùng một thư mục (sorted ở mọi chỗ) — nếu
không, sinh lại manifest sẽ đẻ diff giả và không ai biết cái nào đã chạy.
"""
from __future__ import annotations

import itertools
import re
from pathlib import Path

from .manifest import Run

ROLE_DIRS = {
    "character": "characters",
    "outfit": "outfits",
    "background": "backgrounds",
    "driver": "drivers",
}
MODES = ("pair", "cross")


class ScanError(Exception):
    """Thư mục material không dùng được."""


def slugify(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", _strip_accents(text).lower()).strip("-")
    return out or "x"


def _strip_accents(text: str) -> str:
    import unicodedata
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D")


def collect(materials_dir: Path) -> dict[str, list[Path]]:
    root = Path(materials_dir).expanduser().resolve()
    found: dict[str, list[Path]] = {}
    for role, dirname in ROLE_DIRS.items():
        d = root / dirname
        if not d.is_dir():
            found[role] = []
            continue
        found[role] = sorted(
            (p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")),
            key=lambda p: p.name,
        )
    for role in ("character", "driver"):
        if not found[role]:
            raise ScanError(
                f"Không có file nào trong {root / ROLE_DIRS[role]}\n"
                f"  Cần tối thiểu: {ROLE_DIRS['character']}/ và {ROLE_DIRS['driver']}/\n"
                f"  Bốn ngăn: {', '.join(ROLE_DIRS.values())}"
            )
    return found


def build_runs(found: dict[str, list[Path]], mode: str) -> list[Run]:
    if mode not in MODES:
        raise ScanError(f"MODE không có thật: {mode!r} — chỉ nhận {' | '.join(MODES)}")

    chars, outfits = found["character"], found["outfit"]
    bgs, drivers = found["background"], found["driver"]
    pipeline = "tryon-motion-enhance" if outfits else "motion-enhance"

    if mode == "cross":
        combos = list(itertools.product(chars, outfits or [None], drivers))
    else:
        n = min(len(chars), len(drivers), len(outfits) if outfits else len(chars))
        combos = [(chars[i], outfits[i] if outfits else None, drivers[i]) for i in range(n)]

    runs: list[Run] = []
    used: dict[str, int] = {}
    for index, (character, outfit, driver) in enumerate(combos):
        parts = [slugify(character.stem)]
        if outfit is not None:
            parts.append(slugify(outfit.stem))
        parts.append(slugify(driver.stem))
        base = "__".join(parts)
        used[base] = used.get(base, 0) + 1
        run_id = base if used[base] == 1 else f"{base}-{used[base]}"

        inputs = {"character": character, "driver": driver}
        if outfit is not None:
            inputs["outfit"] = outfit
        if bgs:
            inputs["background"] = bgs[index % len(bgs)]
        runs.append(Run(id=run_id, pipeline=pipeline, inputs=inputs, stage_params={}))
    return runs
```

- [ ] **Step 4: Chạy test để xác nhận nó xanh**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_scan.py' -v`
Expected: PASS — 15 test

- [ ] **Step 5: Viết CLI `batch_scan.py`**

```python
#!/usr/bin/env python3
"""Quét thư mục material → đẻ manifest nháp để bạn soát.

    make batch-scan DIR=~/materials MODE=pair
    make batch-scan DIR=~/materials MODE=cross OUT=batch/thu-nghiem.yaml

Manifest sinh ra là BẢN NHÁP, không phải lệnh chạy: bạn xoá bớt dòng không muốn
rồi mới `make batch`. Đó là lý do bước này tách khỏi bước chạy — đoán sai chỉ
tốn một lần liếc mắt, không tốn tiền GPU.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.manifest import dump_runs
from batchlib.scan import ROLE_DIRS, ScanError, build_runs, collect

ROOT = Path(__file__).resolve().parents[1]

HEADER = """\
# Sinh bởi: make batch-scan DIR={dir} MODE={mode}
# Đây là BẢN NHÁP — xoá bớt run không muốn, chỉnh param, rồi:
#     make batch-validate FILE={out}
#     make batch FILE={out}
#
# Param đều TÙY CHỌN. Bỏ trống = chạy đúng mặc định như bấm UI.
# Tra param: make batch-params TYPE=motion   (hoặc tryon / enhance)
#
# Ví dụ thêm vào một run:
#     motion:  {{ preset: drv-30s }}
#     enhance: {{ targetRes: 1080p, fpsInterp: "60" }}
#
# Hoặc áp cho MỌI run bằng khối defaults ở đầu file:
#     defaults:
#       enhance: {{ targetRes: 1080p, fpsInterp: "60" }}
"""

# Ước lượng THÔ để cảnh báo quy mô, không phải để hứa hẹn. Số đo 17/08/2026 trên
# ab-results/run1: motion 81 frame ≈ 3 phút. Clip drv-30s dài gấp ~6 lần, cộng
# enhance 1080p60 thường lâu hơn chính motion sinh ra nó.
MINUTES_PER_RUN = {"motion-enhance": 25, "tryon-motion-enhance": 30}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Quét thư mục material → manifest nháp")
    ap.add_argument("--dir", required=True, help="thư mục chứa 4 ngăn: " + ", ".join(ROLE_DIRS.values()))
    ap.add_argument("--mode", default="pair", help="pair (ghép theo thứ tự) | cross (tích Descartes)")
    ap.add_argument("--out", default="", help="mặc định batch/<hôm nay>.yaml")
    ap.add_argument("--force", action="store_true", help="cho phép ghi đè file đã có")
    args = ap.parse_args(argv)

    out = Path(args.out) if args.out else ROOT / "batch" / f"{date.today():%Y-%m-%d}.yaml"
    if out.exists() and not args.force:
        print(f"✗ {out} đã tồn tại. Đặt --out khác, hoặc --force nếu chắc chắn muốn đè.",
              file=sys.stderr)
        return 1

    try:
        runs = build_runs(collect(Path(args.dir)), args.mode)
    except ScanError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    minutes = sum(MINUTES_PER_RUN.get(r.pipeline, 30) for r in runs)
    print(f"  {len(runs)} run · pipeline {runs[0].pipeline} · MODE={args.mode}")
    print(f"  Ước tính THÔ: ~{minutes} phút GPU (~{minutes / 60:.1f} giờ). Con số này để bạn")
    print("  giật mình khi cross đẻ ra 60 run, không phải để tin.")
    if len(runs) > 20:
        print(f"  ⚠ {len(runs)} run là nhiều. Soát kỹ file trước khi make batch.")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        HEADER.format(dir=args.dir, mode=args.mode, out=out) + "\n" + dump_runs(runs),
        encoding="utf-8",
    )
    print(f"\n  → {out}")
    print(f"  Soát xong thì: make batch-validate FILE={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 6: Thêm target Makefile + .gitignore + manifest mẫu**

Sửa `.PHONY` ở `Makefile:2` — thêm ` batch-scan`. Chèn sau `check-batch-params`:

```makefile
batch-scan: ## Quét thư mục material → manifest nháp (DIR=~/materials MODE=pair|cross)
	@test -n "$(DIR)" || { echo "cần DIR=~/materials (4 ngăn: characters outfits backgrounds drivers)"; exit 1; }
	@python3 scripts/batch_scan.py --dir "$(DIR)" --mode "$${MODE:-pair}" $${OUT:+--out "$$OUT"} $${FORCE:+--force}
```

Thêm vào cuối `.gitignore`:

```gitignore

# batch runner — material và kết quả là dữ liệu cá nhân, không vào repo public
out/
batch/*.yaml
!batch/example.yaml
batch/*.state.json
```

Tạo `batch/example.yaml` (dùng đường dẫn giả, KHÔNG dùng `~/` thật của ai):

```yaml
# Manifest mẫu. Chép đi rồi sửa, hoặc để make batch-scan đẻ ra bản của bạn.
#
#     make batch-validate FILE=batch/example.yaml
#     make batch FILE=batch/example.yaml
#
# Chỉ 'inputs' là bắt buộc. Param tra bằng: make batch-params TYPE=motion
#
# LƯU Ý: file này trỏ vào ../.smoke/ — thư mục ĐÃ gitignore (media cá nhân, repo public).
# Trên máy đã chạy gpu-smoke thì có sẵn; trên một bản clone mới thì chưa, và
# batch-validate sẽ báo "không thấy file". Đó là đúng: validate tồn tại để nói ra
# điều đó trước khi tiêu GPU. Thay bằng đường dẫn của bạn, hoặc dùng make batch-scan.

defaults:
  enhance: { targetRes: 1080p, fpsInterp: "60" }

runs:
  - id: vidu-day-du
    pipeline: tryon-motion-enhance
    inputs:
      character:  ../.smoke/nhanvat.jpeg
      outfit:     ../.smoke/sanpham.jpeg
      driver:     ../.smoke/dandong.mp4
    motion: { quality: 540p, frames: 33 }

  - id: vidu-chi-motion
    pipeline: motion-enhance
    inputs:
      character: ../.smoke/nhanvat.jpeg
      driver:    ../.smoke/dandong.mp4
    enhance: { fpsInterp: "48" }
```

- [ ] **Step 7: Chạy thật, mắt nhìn**

```bash
mkdir -p /tmp/mat/characters /tmp/mat/outfits /tmp/mat/drivers
cp .smoke/nhanvat.jpeg /tmp/mat/characters/
cp .smoke/sanpham.jpeg /tmp/mat/outfits/
cp .smoke/dandong.mp4  /tmp/mat/drivers/
make batch-scan DIR=/tmp/mat MODE=cross OUT=/tmp/thu.yaml
cat /tmp/thu.yaml
```

Expected: in `1 run · pipeline tryon-motion-enhance`, file có `id: nhanvat__sanpham__dandong` và khối comment hướng dẫn ở đầu.

- [ ] **Step 8: Xác nhận cổng secret còn xanh, rồi commit**

```bash
make batch-test
bash motions-studio/setup/scrub-secrets.sh --check
git add scripts/batchlib/scan.py scripts/batch_scan.py scripts/tests/test_batch_scan.py \
        batch/example.yaml Makefile .gitignore
git commit -m "batch: nạp bằng 4 thư mục vai trò, khỏi đặt tên file

background XOAY VÒNG chứ không giới hạn số run: nền thường chỉ có một cái dùng
chung, để nó tham gia min() thì một file nền bóp lô 12 run xuống 1 run và không
ai hiểu vì sao.

Manifest sinh ra là bản nháp để soát, không phải lệnh chạy — đó là lý do bước
sinh tách khỏi bước chạy. Đoán sai tốn một lần liếc mắt, không tốn tiền GPU."
```

---

### Task 6: HTTP client (`client.py`)

**Files:**
- Create: `scripts/batchlib/client.py`
- Test: `scripts/tests/test_batch_client.py`

**Interfaces:**
- Consumes: `config.Settings`
- Produces: `class JobError(Exception)`; `encode_multipart(fields: dict[str, str], files: dict[str, Path]) -> tuple[bytes, str]`; `health_ok(s: Settings, timeout: int = 15) -> bool`; `submit_job(s: Settings, job_type: str, params: dict, files: dict[str, Path]) -> str`; `poll_job(s, job_id, timeout_min, on_progress=None, sleep=time.sleep, now=time.time) -> dict`; `download_output(s, job_id, dest: Path, min_bytes: int) -> int`.

`sleep` và `now` là cổng tiêm cho test — không có chúng thì mỗi test poll phải chờ thật 10 giây,
và test timeout phải chờ thật một phút.

Test dựng một `http.server.HTTPServer` giả trong thread — không cần pod, không cần mạng ra ngoài.

- [ ] **Step 1: Viết test thất bại**

```python
# scripts/tests/test_batch_client.py
import json, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.client import (JobError, download_output, encode_multipart, health_ok,
                             poll_job, submit_job)
from batchlib.config import Settings

STATE = {"poll_calls": 0, "statuses": [], "last_post": None, "output": b"", "health": 200}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):  # im lặng trong test
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_response(STATE["health"]); self.send_header("content-length", "0"); self.end_headers()
        elif self.path.endswith("/download"):
            self.send_response(200)
            self.send_header("content-length", str(len(STATE["output"])))
            self.end_headers()
            self.wfile.write(STATE["output"])
        else:
            i = min(STATE["poll_calls"], len(STATE["statuses"]) - 1)
            STATE["poll_calls"] += 1
            self._json(200, STATE["statuses"][i])

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["content-length"]))
        STATE["last_post"] = (self.headers.get("content-type", ""), raw,
                              self.headers.get("x-api-key", ""))
        self._json(202, {"id": "job-1", "status": "queued"})


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        host, port = cls.server.server_address
        cls.settings = Settings(domain=f"{host}:{port}", api_key="mk_test", instance_id="i-1")
        # Server giả không có TLS nên base_url phải là http://. Đây là vá ở cấp LỚP, nên
        # PHẢI khôi phục ở tearDownClass: `unittest discover` chạy mọi module trong CÙNG
        # một tiến trình, và một base_url rò rỉ biến cả suite thành phụ thuộc thứ tự chạy.
        cls._base_url_goc = Settings.base_url
        Settings.base_url = property(lambda s: f"http://{s.domain}")

    @classmethod
    def tearDownClass(cls):
        Settings.base_url = cls._base_url_goc
        cls.server.shutdown()

    def setUp(self):
        STATE.update(poll_calls=0, statuses=[], last_post=None, output=b"", health=200)


class TestMultipart(unittest.TestCase):
    def test_giu_nguyen_byte_nhi_phan(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.bin"
            blob = bytes(range(256)) * 4
            p.write_bytes(blob)
            body, ctype = encode_multipart({"type": "motion"}, {"ref": p})
            self.assertIn("multipart/form-data; boundary=", ctype)
            self.assertIn(blob, body)
            self.assertIn(b'name="type"', body)
            self.assertIn(b'name="ref"; filename="a.bin"', body)
            boundary = ctype.split("boundary=")[1].encode()
            self.assertTrue(body.rstrip().endswith(b"--" + boundary + b"--"))

    def test_boundary_khong_dung_lai_giua_hai_lan(self):
        b1 = encode_multipart({"a": "1"}, {})[1]
        b2 = encode_multipart({"a": "1"}, {})[1]
        self.assertNotEqual(b1, b2)


class TestHealth(ServerCase):
    def test_200_la_up(self):
        self.assertTrue(health_ok(self.settings))

    def test_503_la_down(self):
        STATE["health"] = 503
        self.assertFalse(health_ok(self.settings))


class TestSubmit(ServerCase):
    def test_gui_api_key_va_params_json_roi_tra_id(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ref.jpg"
            p.write_bytes(b"x" * 10)
            job_id = submit_job(self.settings, "motion", {"quality": "540p"}, {"ref": p})
            self.assertEqual(job_id, "job-1")
            ctype, raw, key = STATE["last_post"]
            self.assertEqual(key, "mk_test")
            self.assertIn(b'name="type"', raw)
            self.assertIn(b"motion", raw)
            self.assertIn(b'{"quality": "540p"}', raw)


class TestPoll(ServerCase):
    def test_chay_toi_khi_done(self):
        STATE["statuses"] = [{"status": "queued", "progress": 0},
                             {"status": "running", "progress": 0.4},
                             {"status": "done", "progress": 1}]
        seen = []
        result = poll_job(self.settings, "job-1", timeout_min=5,
                          on_progress=lambda d: seen.append(d["status"]), sleep=lambda _s: None)
        self.assertEqual(result["status"], "done")
        self.assertIn("running", seen)

    def test_error_thi_nem_kem_ly_do_cua_worker(self):
        STATE["statuses"] = [{"status": "error", "error": "ComfyUI 400 node type not found"}]
        with self.assertRaises(JobError) as cm:
            poll_job(self.settings, "job-1", timeout_min=5, sleep=lambda _s: None)
        self.assertIn("node type not found", str(cm.exception))

    def test_cancelled_cung_la_loi(self):
        STATE["statuses"] = [{"status": "cancelled"}]
        with self.assertRaises(JobError):
            poll_job(self.settings, "job-1", timeout_min=5, sleep=lambda _s: None)

    def test_qua_han_thi_nem_kem_job_id_de_resume(self):
        STATE["statuses"] = [{"status": "running", "progress": 0.5}]
        clock = iter([0, 10_000, 20_000])
        with self.assertRaises(JobError) as cm:
            poll_job(self.settings, "job-1", timeout_min=1,
                     sleep=lambda _s: None, now=lambda: next(clock))
        self.assertIn("job-1", str(cm.exception))


class TestDownload(ServerCase):
    def test_tai_ve_va_tra_so_byte(self):
        STATE["output"] = b"v" * 200_000
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "out.mp4"
            self.assertEqual(download_output(self.settings, "job-1", dest, 100_000), 200_000)
            self.assertEqual(dest.stat().st_size, 200_000)

    def test_duoi_san_thi_nem_va_xoa_file_rac(self):
        # "job báo done nhưng MinIO trả về gần rỗng" — đúng bẫy pod-smoke.sh dựng sàn để bắt.
        STATE["output"] = b"v" * 10
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "out.mp4"
            with self.assertRaises(JobError) as cm:
                download_output(self.settings, "job-1", dest, 100_000)
            self.assertIn("10", str(cm.exception))
            self.assertFalse(dest.exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Chạy test để xác nhận nó đỏ**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_client.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batchlib.client'`

- [ ] **Step 3: Viết implementation tối thiểu**

```python
# scripts/batchlib/client.py
"""Nói chuyện với API trên pod. Không biết pipeline là gì.

Chỉ stdlib: `requests` KHÔNG có trên máy dev (đo 18/08/2026) và thêm dependency
cho ba lời gọi HTTP là không đáng.

Hợp đồng API (motions-studio/api/src/routes/jobs.js):
  POST /jobs                 multipart: type, params(JSON), + file theo fieldname → 202 {id,…}
  GET  /jobs/<id>            → {status, progress, current_step, error, …}  (rowToDto, jobs.js:73-87)
  GET  /jobs/<id>/download   → bytes
Xác thực: header x-api-key ở cả ba.
"""
from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from .config import Settings

POLL_SECONDS = 10


class JobError(Exception):
    """Job hỏng, quá hạn, hoặc output không dùng được."""


def encode_multipart(fields: dict[str, str], files: dict[str, Path]) -> tuple[bytes, str]:
    boundary = f"----batchrunner{uuid.uuid4().hex}"
    sep = f"--{boundary}\r\n".encode()
    body = bytearray()
    for name, value in fields.items():
        body += sep
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += f"{value}\r\n".encode()
    for name, path in files.items():
        filename = path.name
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body += sep
        body += (f'Content-Disposition: form-data; name="{name}"; '
                 f'filename="{filename}"\r\n').encode()
        body += f"Content-Type: {ctype}\r\n\r\n".encode()
        body += path.read_bytes()
        body += b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _request(s: Settings, path: str, *, data: bytes | None = None,
             content_type: str = "", timeout: int = 60) -> tuple[int, bytes]:
    req = urllib.request.Request(f"{s.base_url}{path}", data=data)
    req.add_header("x-api-key", s.api_key)
    if content_type:
        req.add_header("content-type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def health_ok(s: Settings, timeout: int = 15) -> bool:
    try:
        code, _ = _request(s, "/health", timeout=timeout)
    except (urllib.error.URLError, OSError):
        return False
    return code == 200


def submit_job(s: Settings, job_type: str, params: dict, files: dict[str, Path]) -> str:
    fields = {"type": job_type, "params": json.dumps(params or {}, ensure_ascii=False)}
    body, ctype = encode_multipart(fields, files)
    code, raw = _request(s, "/jobs", data=body, content_type=ctype, timeout=300)
    if code not in (200, 202):
        raise JobError(f"POST /jobs → {code}: {raw[:300].decode('utf-8', 'replace')}")
    try:
        job_id = (json.loads(raw) or {}).get("id", "")
    except ValueError:
        job_id = ""
    if not job_id:
        raise JobError(f"POST /jobs không trả về id: {raw[:300].decode('utf-8', 'replace')}")
    return str(job_id)


def poll_job(s: Settings, job_id: str, timeout_min: int,
             on_progress: Callable[[dict], None] | None = None,
             sleep: Callable[[float], None] = time.sleep,
             now: Callable[[], float] = time.time) -> dict:
    deadline = now() + timeout_min * 60
    misses = 0
    while now() < deadline:
        sleep(POLL_SECONDS)
        try:
            code, raw = _request(s, f"/jobs/{job_id}", timeout=30)
            data = json.loads(raw) if code == 200 else {}
        except (urllib.error.URLError, OSError, ValueError):
            # Rớt mạng KHÔNG được giết job đang chạy trên pod — nó vẫn chạy tiếp.
            misses += 1
            if misses > 30:
                raise JobError(f"mất liên lạc với API quá lâu; job {job_id} có thể vẫn đang chạy — "
                               f"chạy lại với RESUME=1")
            continue
        misses = 0
        if on_progress:
            on_progress(data)
        status = str(data.get("status") or "")
        if status == "done":
            return data
        if status in ("error", "cancelled"):
            raise JobError(f"job {job_id} {status}: {data.get('error') or 'không có lý do'}")
    raise JobError(
        f"job {job_id} chưa xong sau {timeout_min} phút. Nó VẪN đang chạy trên pod — "
        f"chạy lại với RESUME=1 để bắt tiếp, đừng submit job mới."
    )


def download_output(s: Settings, job_id: str, dest: Path, min_bytes: int) -> int:
    code, raw = _request(s, f"/jobs/{job_id}/download", timeout=600)
    if code != 200:
        raise JobError(f"tải output job {job_id} → {code}")
    if len(raw) < min_bytes:
        # Bẫy pod-smoke.sh dựng sàn để bắt: job báo done nhưng MinIO trả về gần rỗng.
        raise JobError(
            f"output job {job_id} chỉ {len(raw)} byte (cần ≥ {min_bytes}) — "
            f"job báo done nhưng MinIO không có gì dùng được"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    return len(raw)
```

- [ ] **Step 4: Chạy test để xác nhận nó xanh**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_client.py' -v`
Expected: PASS — 11 test

- [ ] **Step 5: Commit**

```bash
git add scripts/batchlib/client.py scripts/tests/test_batch_client.py
git commit -m "batch: client HTTP chỉ bằng stdlib, test bằng server giả

requests không có trên máy dev nên multipart tự mã hoá — test khoá tính an toàn
nhị phân và boundary không lặp lại.

Hai hành vi cố ý: rớt mạng lúc poll KHÔNG giết job (nó vẫn chạy trên pod, bảo
người dùng RESUME=1 chứ không submit lại), và sàn kích thước tải về bắt đúng bẫy
pod-smoke.sh dựng để bắt — job báo done nhưng MinIO trả về gần rỗng."
```

---

### Task 7: Chạy lô (`runner.py` + `batch_run.py`)

**Files:**
- Create: `scripts/batchlib/runner.py`
- Create: `scripts/batch_run.py`
- Modify: `Makefile` (thêm `batch-validate`, `batch`; cập nhật `.PHONY:1`)
- Test: `scripts/tests/test_batch_runner.py`

**Interfaces:**
- Consumes: mọi module trên
- Produces: `@dataclass BatchResult(batch_id: str, out_dir: Path, done: list[str], failed: dict[str, str], gpu_seconds: int)`; `batch_id_now(now) -> str`; `stage_files(run, stage, prev_output, ...) -> dict[str, Path]`; `run_one(...) -> Path`; `run_batch(...) -> BatchResult`; `write_index(out_dir, state) -> None`.

Bố cục output (spec §6), `run_one` phải đẻ ra đúng thế này:
```
out/<batch>/_final/<run-id>.mp4      hardlink
out/<batch>/_index.tsv
out/<batch>/manifest.yaml            chép nguyên văn, kể cả comment
out/<batch>/runs/<run-id>/NN-<stage>.<ext> · run.json · run.log
```

- [ ] **Step 1: Viết test thất bại**

```python
# scripts/tests/test_batch_runner.py
import json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.client import JobError
from batchlib.config import Settings
from batchlib.manifest import load_manifest, load_state, state_path_for
from batchlib.runner import batch_id_now, run_batch, write_index

SETTINGS = Settings(domain="x.test", api_key="mk_test", instance_id="i-1")

MANIFEST = """
runs:
  - id: runA
    pipeline: motion-enhance
    inputs:
      character: char.jpg
      driver: drv.mp4
  - id: runB
    pipeline: motion-enhance
    inputs:
      character: char.jpg
      driver: drv.mp4
"""


def _fixture(tmp: Path, text: str = MANIFEST) -> Path:
    (tmp / "char.jpg").write_bytes(b"x")
    (tmp / "drv.mp4").write_bytes(b"x")
    p = tmp / "b.yaml"
    p.write_text(text, encoding="utf-8")
    return p


class FakePod:
    """Pod giả: mỗi job xong ngay, output là byte đủ lớn."""

    def __init__(self, fail_on: set[str] | None = None):
        self.fail_on = fail_on or set()
        self.submitted: list[tuple[str, dict, dict]] = []

    def submit(self, _s, job_type, params, files):
        self.submitted.append((job_type, params, {k: v.name for k, v in files.items()}))
        return f"job-{len(self.submitted)}"

    def poll(self, _s, job_id, *_a, **_k):
        if job_id in self.fail_on:
            raise JobError(f"job {job_id} error: pod giả cố ý hỏng")
        return {"status": "done", "progress": 1}

    def download(self, _s, _job_id, dest: Path, _min_bytes):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"v" * 200_000)
        return 200_000


def _run(tmp: Path, pod: FakePod, **kwargs):
    manifest_path = kwargs.pop("manifest_path", None) or _fixture(tmp)
    with mock.patch("batchlib.runner.submit_job", pod.submit), \
         mock.patch("batchlib.runner.poll_job", pod.poll), \
         mock.patch("batchlib.runner.download_output", pod.download):
        return run_batch(
            settings=SETTINGS,
            manifest=load_manifest(manifest_path),
            out_root=tmp / "out",
            batch_id="2026-08-18-1430",
            **kwargs,
        )


class TestBatchId(unittest.TestCase):
    def test_dinh_dang_sap_xep_duoc_theo_thu_tu_chu(self):
        import datetime
        self.assertEqual(batch_id_now(datetime.datetime(2026, 8, 18, 14, 30)), "2026-08-18-1430")


class TestChayThanhCong(unittest.TestCase):
    def test_chay_het_va_de_ra_bo_cuc_dung(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            result = _run(tmp, FakePod())
            out = tmp / "out" / "2026-08-18-1430"
            self.assertEqual(result.done, ["runA", "runB"])
            self.assertEqual(result.failed, {})
            self.assertTrue((out / "_final" / "runA.mp4").is_file())
            self.assertTrue((out / "_final" / "runB.mp4").is_file())
            self.assertTrue((out / "_index.tsv").is_file())
            self.assertTrue((out / "manifest.yaml").is_file())
            self.assertTrue((out / "runs" / "runA" / "01-motion.mp4").is_file())
            self.assertTrue((out / "runs" / "runA" / "02-enhance.mp4").is_file())
            self.assertTrue((out / "runs" / "runA" / "run.json").is_file())

    def test_final_la_hardlink_khong_ton_them_dia(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _run(tmp, FakePod())
            out = tmp / "out" / "2026-08-18-1430"
            self.assertEqual((out / "_final" / "runA.mp4").stat().st_ino,
                             (out / "runs" / "runA" / "02-enhance.mp4").stat().st_ino)

    def test_output_chang_truoc_thanh_input_chang_sau(self):
        with tempfile.TemporaryDirectory() as d:
            pod = FakePod()
            _run(Path(d), pod)
            enhance_calls = [c for c in pod.submitted if c[0] == "enhance"]
            self.assertTrue(enhance_calls)
            self.assertEqual(list(enhance_calls[0][2]), ["input"])
            self.assertTrue(enhance_calls[0][2]["input"].startswith("01-motion"))

    def test_manifest_goc_khong_bi_sua(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp)
            before = p.read_text(encoding="utf-8")
            _run(tmp, FakePod(), manifest_path=p)
            self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_run_json_ghi_param_that_da_gui(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _run(tmp, FakePod())
            data = json.loads((tmp / "out" / "2026-08-18-1430" / "runs" / "runA" / "run.json")
                              .read_text(encoding="utf-8"))
            self.assertIn("motion", data["stages"])
            self.assertIn("params_sent", data["stages"]["motion"])
            self.assertIn("job_id", data["stages"]["motion"])


class TestHong(unittest.TestCase):
    def test_mot_run_hong_khong_giet_ca_lo(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            result = _run(tmp, FakePod(fail_on={"job-1"}))
            self.assertEqual(result.done, ["runB"])
            self.assertIn("runA", result.failed)

    def test_fail_fast_thi_dung_ngay(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            result = _run(tmp, FakePod(fail_on={"job-1"}), fail_fast=True)
            self.assertEqual(result.done, [])
            self.assertEqual(list(result.failed), ["runA"])

    def test_hong_van_ghi_state_de_resume(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp)
            _run(tmp, FakePod(fail_on={"job-1"}), manifest_path=p)
            state = load_state(state_path_for(p))
            self.assertEqual(state["runs"]["runA"]["status"], "error")
            self.assertEqual(state["runs"]["runB"]["status"], "done")


class TestResume(unittest.TestCase):
    def test_resume_bo_qua_run_da_done(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp)
            _run(tmp, FakePod(), manifest_path=p)
            pod2 = FakePod()
            _run(tmp, pod2, manifest_path=p, resume=True)
            self.assertEqual(pod2.submitted, [])

    def test_resume_chi_chay_lai_chang_con_thieu(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp)
            _run(tmp, FakePod(fail_on={"job-2"}), manifest_path=p)   # motion xong, enhance hỏng
            pod2 = FakePod()
            _run(tmp, pod2, manifest_path=p, resume=True)
            self.assertTrue(all(c[0] == "enhance" for c in pod2.submitted),
                            f"chỉ được chạy lại enhance, nhưng chạy: {[c[0] for c in pod2.submitted]}")

    def test_khong_resume_thi_chay_lai_tu_dau(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            p = _fixture(tmp)
            _run(tmp, FakePod(), manifest_path=p)
            pod2 = FakePod()
            _run(tmp, pod2, manifest_path=p, resume=False)
            self.assertEqual(len(pod2.submitted), 4)


class TestIndex(unittest.TestCase):
    def test_index_tsv_co_header_va_mot_dong_moi_run(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            _run(tmp, FakePod())
            lines = (tmp / "out" / "2026-08-18-1430" / "_index.tsv").read_text(
                encoding="utf-8").strip().splitlines()
            self.assertTrue(lines[0].startswith("run\t"))
            self.assertEqual(len(lines), 3)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Chạy test để xác nhận nó đỏ**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_runner.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batchlib.runner'`

- [ ] **Step 3: Viết implementation tối thiểu**

```python
# scripts/batchlib/runner.py
"""Chạy một lô: từng run, từng chặng, ghi journal sau MỖI chặng.

Ghi journal sau mỗi chặng chứ không cuối run: một run ba chặng đứt ở chặng ba
thì resume chỉ chạy lại chặng ba. Ghi cuối run nghĩa là mất cả ba.

Tuần tự, không song song. Pod có một GPU, và run_enhance gọi comfy_recycle để xả
RAM/VRAM của Wan trước mỗi pha nặng (linux.py:9586/9650/9691/9740) — hai job
chồng nhau phá đúng giả định "lúc này GPU chỉ có mình tôi" mà các lời gọi đó dựa vào.
"""
from __future__ import annotations

import datetime as _dt
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .client import JobError, download_output, poll_job, submit_job
from .config import Settings
from .manifest import Manifest, Run, load_state, save_state, state_path_for
from .pipelines import PIPELINES, STAGES


@dataclass
class BatchResult:
    batch_id: str
    out_dir: Path
    done: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    gpu_seconds: int = 0


def batch_id_now(now: _dt.datetime | None = None) -> str:
    return f"{now or _dt.datetime.now():%Y-%m-%d-%H%M}"


def _resolve_files(run: Run, stage_name: str, prev_output: Path | None) -> dict[str, Path]:
    """Dịch Stage.inputs thành các file thật sẽ upload."""
    files: dict[str, Path] = {}
    for api_field, source in STAGES[stage_name].inputs.items():
        for alt in source.split("|"):
            if alt == "prev":
                if prev_output is not None:
                    files[api_field] = prev_output
                    break
                continue
            role = alt[len("material:"):]
            optional = role.endswith("?")
            role = role.rstrip("?")
            path = run.inputs.get(role)
            if path is not None:
                files[api_field] = path
                break
            if optional:
                break
        else:
            raise JobError(
                f"run {run.id!r} chặng {stage_name}: không có nguồn nào cho field {api_field!r} "
                f"(khai: {source})"
            )
    return files


def run_one(*, settings: Settings, run: Run, out_dir: Path, state: dict,
            state_file: Path, resume: bool,
            log: Callable[[str], None], now: Callable[[], float] = time.time) -> Path:
    run_dir = out_dir / "runs" / run.id
    run_dir.mkdir(parents=True, exist_ok=True)
    entry = state["runs"].setdefault(run.id, {"status": "pending", "stages": {}})
    entry["status"] = "running"

    prev_output: Path | None = None
    for index, stage_name in enumerate(PIPELINES[run.pipeline], start=1):
        stage = STAGES[stage_name]
        dest = run_dir / f"{index:02d}-{stage_name}{stage.output_ext}"
        recorded = entry["stages"].get(stage_name) or {}

        if resume and recorded.get("status") == "done" and dest.is_file():
            log(f"    {stage_name}: bỏ qua (đã xong, {dest.name})")
            prev_output = dest
            continue

        files = _resolve_files(run, stage_name, prev_output)
        params = run.stage_params.get(stage_name, {})
        started = now()
        log(f"    {stage_name}: gửi ({', '.join(f'{k}={v.name}' for k, v in files.items())})")
        job_id = submit_job(settings, stage.job_type, params, files)
        entry["stages"][stage_name] = {"job_id": job_id, "status": "running",
                                       "params_sent": params}
        save_state(state_file, state)

        try:
            poll_job(settings, job_id, stage.timeout_min,
                     on_progress=lambda d: log(
                         f"      {d.get('status')} {round((d.get('progress') or 0) * 100)}% "
                         f"{d.get('current_step') or ''}"))
            size = download_output(settings, job_id, dest, stage.min_bytes)
        except JobError:
            entry["stages"][stage_name]["status"] = "error"
            entry["stages"][stage_name]["elapsed_sec"] = int(now() - started)
            save_state(state_file, state)
            raise

        elapsed = int(now() - started)
        entry["stages"][stage_name].update(status="done", elapsed_sec=elapsed,
                                           file=str(dest), bytes=size)
        save_state(state_file, state)
        log(f"    {stage_name}: xong {elapsed}s · {size // 1024} KB → {dest.name}")
        prev_output = dest

    if prev_output is None:
        raise JobError(f"run {run.id!r}: pipeline không có chặng nào")

    final_dir = out_dir / "_final"
    final_dir.mkdir(parents=True, exist_ok=True)
    final = final_dir / f"{run.id}{prev_output.suffix}"
    if final.exists():
        final.unlink()
    try:
        final.hardlink_to(prev_output)
    except OSError:
        # Khác filesystem hoặc FS không hỗ trợ hardlink — chép, tốn đĩa nhưng vẫn đúng.
        shutil.copy2(prev_output, final)

    entry["status"] = "done"
    save_state(state_file, state)
    (run_dir / "run.json").write_text(
        json.dumps({"id": run.id, "pipeline": run.pipeline,
                    "inputs": {k: str(v) for k, v in run.inputs.items()},
                    "stages": entry["stages"], "final": str(final)},
                   ensure_ascii=False, indent=2),
        encoding="utf-8")
    return final


def write_index(out_dir: Path, state: dict) -> None:
    lines = ["run\tstatus\tstage\tjob_id\telapsed_sec\tbytes\tparams_sent"]
    for run_id, entry in state["runs"].items():
        for stage_name, s in (entry.get("stages") or {}).items():
            lines.append("\t".join([
                run_id, str(entry.get("status", "")), stage_name,
                str(s.get("job_id", "")), str(s.get("elapsed_sec", "")),
                str(s.get("bytes", "")),
                json.dumps(s.get("params_sent") or {}, ensure_ascii=False),
            ]))
    (out_dir / "_index.tsv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_batch(*, settings: Settings, manifest: Manifest, out_root: Path,
              batch_id: str | None = None, resume: bool = False, fail_fast: bool = False,
              log: Callable[[str], None] = print,
              now: Callable[[], float] = time.time) -> BatchResult:
    batch_id = batch_id or batch_id_now()
    out_dir = out_root / batch_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Chép NGUYÊN VĂN, không qua PyYAML — comment của người dùng phải sống sót.
    shutil.copyfile(manifest.path, out_dir / "manifest.yaml")

    state_file = state_path_for(manifest.path)
    state = load_state(state_file) if resume else {"version": 1, "runs": {}}
    state["batch"] = batch_id

    result = BatchResult(batch_id=batch_id, out_dir=out_dir)
    for position, run in enumerate(manifest.runs, start=1):
        recorded = (state["runs"].get(run.id) or {})
        if resume and recorded.get("status") == "done":
            log(f"[{position}/{len(manifest.runs)}] {run.id}: bỏ qua (đã xong)")
            result.done.append(run.id)
            continue
        log(f"[{position}/{len(manifest.runs)}] {run.id} · {run.pipeline}")
        started = now()
        try:
            run_one(settings=settings, run=run, out_dir=out_dir, state=state,
                    state_file=state_file, resume=resume, log=log, now=now)
            result.done.append(run.id)
        except JobError as exc:
            state["runs"][run.id]["status"] = "error"
            state["runs"][run.id]["error"] = str(exc)
            save_state(state_file, state)
            result.failed[run.id] = str(exc)
            log(f"    ✗ {exc}")
            if fail_fast:
                result.gpu_seconds += int(now() - started)
                break
        result.gpu_seconds += int(now() - started)

    save_state(state_file, state)
    write_index(out_dir, state)
    latest = out_root / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(out_dir.name)
    return result
```

- [ ] **Step 4: Chạy test để xác nhận nó xanh**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_runner.py' -v`
Expected: PASS — 13 test

- [ ] **Step 5: Viết CLI `batch_run.py`**

```python
#!/usr/bin/env python3
"""Chạy một lô.

    make batch-validate FILE=batch/2026-08-18.yaml   # chỉ kiểm, không tiêu GPU
    make batch          FILE=batch/2026-08-18.yaml
    make batch          FILE=batch/2026-08-18.yaml RESUME=1
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.client import health_ok
from batchlib.config import ConfigError, load_settings
from batchlib.manifest import ManifestError, load_manifest, validate_manifest
from batchlib.params import extract_from_ast, load_curated
from batchlib.runner import batch_id_now, run_batch

ROOT = Path(__file__).resolve().parents[1]
LINUX_PY = ROOT / "motions-studio" / "worker" / "worker_runtime" / "linux.py"
CURATED = ROOT / "scripts" / "batch-params.json"


def preflight(settings, *, allow_start: bool) -> bool:
    if health_ok(settings):
        print(f"  ✓ pod đang chạy → {settings.base_url}")
        return True
    if not settings.instance_id:
        print("✗ Backend không trả lời, và .env chưa có GPU_INSTANCE_ID.\n"
              "  Chưa thuê pod thì chạy: make gpu-provision   (nó hỏi xác nhận trước khi tiêu tiền)",
              file=sys.stderr)
        return False
    if not allow_start:
        print("✗ Backend không trả lời. Chạy: make gpu-up", file=sys.stderr)
        return False
    print("  pod đang dừng → make gpu-up (bật là thao tác đảo được, nên tự làm)")
    if subprocess.run(["make", "gpu-up"], cwd=ROOT).returncode != 0:
        print("✗ make gpu-up thất bại — đọc output ở trên", file=sys.stderr)
        return False
    return health_ok(settings)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Chạy một lô material")
    ap.add_argument("--file", required=True)
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--fail-fast", action="store_true")
    ap.add_argument("--no-start", action="store_true", help="không tự make gpu-up")
    args = ap.parse_args(argv)

    try:
        manifest = load_manifest(Path(args.file))
    except ManifestError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    errors = validate_manifest(manifest, ast_params=extract_from_ast(LINUX_PY),
                               curated=load_curated(CURATED))
    if errors:
        print(f"✗ {len(errors)} lỗi trong {args.file} — chưa tiêu đồng GPU nào:", file=sys.stderr)
        for e in errors:
            print(f"    {e}", file=sys.stderr)
        return 1
    print(f"  ✓ manifest hợp lệ · {len(manifest.runs)} run")
    if args.validate_only:
        return 0

    try:
        settings = load_settings(ROOT)
    except ConfigError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    if not preflight(settings, allow_start=not args.no_start):
        return 1

    result = run_batch(settings=settings, manifest=manifest, out_root=ROOT / "out",
                       batch_id=batch_id_now(), resume=args.resume, fail_fast=args.fail_fast)

    minutes = result.gpu_seconds / 60
    print(f"\n  Lô {result.batch_id}: {len(result.done)} xong · {len(result.failed)} hỏng "
          f"· ~{minutes:.0f} phút GPU")
    print(f"  Kết quả: {result.out_dir / '_final'}")
    print(f"  Bảng tra: {result.out_dir / '_index.tsv'}")
    for run_id, why in result.failed.items():
        print(f"    ✗ {run_id}: {why}")

    if result.failed:
        print("\n  Pod VẪN chạy — có run hỏng thì đây đúng là lúc cần nó nhất:")
        print("      make gpu-logs LOG=worker")
        print(f"      make batch FILE={args.file} RESUME=1     (chỉ chạy lại phần thiếu)")
        return 1

    print("\n  Xong hết. Pod vẫn đang chạy và vẫn tính tiền — tắt khi không dùng nữa:")
    print("      make gpu-destroy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 6: Thêm target Makefile**

Sửa `.PHONY` ở `Makefile:2` — thêm ` batch-validate batch`. Chèn sau `batch-scan`:

```makefile
batch-validate: ## Kiểm manifest mà KHÔNG tiêu GPU (FILE=batch/….yaml)
	@test -n "$(FILE)" || { echo "cần FILE=batch/….yaml"; exit 1; }
	@python3 scripts/batch_run.py --file "$(FILE)" --validate-only

batch: ## Chạy một lô (FILE=batch/….yaml, RESUME=1 để chạy tiếp lô dở)
	@test -n "$(FILE)" || { echo "cần FILE=batch/….yaml"; exit 1; }
	@python3 scripts/batch_run.py --file "$(FILE)" $${RESUME:+--resume} $${FAIL_FAST:+--fail-fast}
```

- [ ] **Step 7: Chạy thật (chỉ phần không cần pod)**

```bash
make batch-test
make batch-validate FILE=batch/example.yaml
```

Expected: test xanh; validate in `✓ manifest hợp lệ · 2 run` (đường dẫn `../.smoke/` có thật trong repo).

- [ ] **Step 8: Commit**

```bash
git add scripts/batchlib/runner.py scripts/batch_run.py scripts/tests/test_batch_runner.py Makefile
git commit -m "batch: chạy lô, journal sau mỗi chặng, resume theo chặng

Journal ghi sau MỖI chặng chứ không cuối run: run ba chặng đứt ở chặng ba thì
resume chỉ chạy lại chặng ba, không phải cả ba.

Manifest gốc được chép nguyên văn bằng shutil.copyfile chứ không qua PyYAML —
comment của người dùng phải sống sót sang bản lưu.

Chạy tuần tự: run_enhance gọi comfy_recycle xả RAM/VRAM của Wan trước mỗi pha
nặng (linux.py:9586/9650/9691/9740); hai job chồng nhau phá đúng giả định đó."
```

---

### Task 8: Dọn đĩa + tài liệu

**Files:**
- Create: `scripts/batch_clean.py`
- Modify: `Makefile` (thêm `batch-clean`; cập nhật `.PHONY:1`)
- Modify: `README.md` (thêm mục dùng batch runner)
- Modify: `docs/gpu-pod.md` (liên kết từ Runbook)
- Test: `scripts/tests/test_batch_clean.py`

**Interfaces:**
- Consumes: —
- Produces: `prune(out_root: Path, keep: int, dry_run: bool = False) -> list[Path]` — trả danh sách thư mục `runs/` đã (hoặc sẽ) xoá.

- [ ] **Step 1: Viết test thất bại**

```python
# scripts/tests/test_batch_clean.py
import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batch_clean import prune


def _batches(root: Path, names: list[str]) -> None:
    for name in names:
        (root / name / "runs" / "r1").mkdir(parents=True)
        (root / name / "runs" / "r1" / "01-motion.mp4").write_bytes(b"x")
        (root / name / "_final").mkdir(parents=True)
        (root / name / "_final" / "r1.mp4").write_bytes(b"x")


class TestPrune(unittest.TestCase):
    def test_giu_n_lo_moi_nhat_xoa_runs_cua_phan_con_lai(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _batches(root, ["2026-08-01-1000", "2026-08-02-1000", "2026-08-03-1000"])
            removed = prune(root, keep=2)
            self.assertEqual([p.parent.name for p in removed], ["2026-08-01-1000"])
            self.assertFalse((root / "2026-08-01-1000" / "runs").exists())
            self.assertTrue((root / "2026-08-02-1000" / "runs").exists())

    def test_final_khong_bao_gio_bi_dung_toi(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _batches(root, ["2026-08-01-1000", "2026-08-02-1000"])
            prune(root, keep=1)
            self.assertTrue((root / "2026-08-01-1000" / "_final" / "r1.mp4").is_file())

    def test_dry_run_liet_ke_ma_khong_xoa(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _batches(root, ["2026-08-01-1000", "2026-08-02-1000"])
            removed = prune(root, keep=1, dry_run=True)
            self.assertEqual(len(removed), 1)
            self.assertTrue((root / "2026-08-01-1000" / "runs").exists())

    def test_it_hon_keep_thi_khong_xoa_gi(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _batches(root, ["2026-08-01-1000"])
            self.assertEqual(prune(root, keep=3), [])

    def test_keep_0_bi_chan(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                prune(Path(d), keep=0)

    def test_bo_qua_symlink_latest(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _batches(root, ["2026-08-01-1000", "2026-08-02-1000"])
            (root / "latest").symlink_to("2026-08-02-1000")
            removed = prune(root, keep=1)
            self.assertEqual([p.parent.name for p in removed], ["2026-08-01-1000"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Chạy test để xác nhận nó đỏ**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_clean.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batch_clean'`

- [ ] **Step 3: Viết implementation tối thiểu**

```python
#!/usr/bin/env python3
"""Xoá file TRUNG GIAN của các lô cũ. Không bao giờ đụng _final/.

    make batch-clean            # giữ 3 lô gần nhất
    make batch-clean KEEP=1
    make batch-clean DRY=1      # chỉ liệt kê

Vì sao chỉ xoá runs/: file trung gian tồn tại để trả lời "tryon ra ảnh gì" khi
kết quả cuối xấu. Sau vài lô thì câu hỏi đó không còn ai hỏi nữa, nhưng bản
cuối thì vẫn phải giữ — nên hai thứ có vòng đời khác nhau và được dọn khác nhau.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prune(out_root: Path, keep: int, dry_run: bool = False) -> list[Path]:
    if keep < 1:
        raise ValueError("KEEP phải ≥ 1 — giữ 0 lô nghĩa là xoá cả lô vừa chạy xong")
    if not out_root.is_dir():
        return []
    batches = sorted(
        (p for p in out_root.iterdir() if p.is_dir() and not p.is_symlink()),
        key=lambda p: p.name,
    )
    removed: list[Path] = []
    for batch in batches[:-keep] if len(batches) > keep else []:
        runs = batch / "runs"
        if not runs.is_dir():
            continue
        removed.append(runs)
        if not dry_run:
            shutil.rmtree(runs)
    return removed


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Xoá file trung gian của lô cũ")
    ap.add_argument("--keep", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        removed = prune(ROOT / "out", args.keep, args.dry_run)
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    verb = "sẽ xoá" if args.dry_run else "đã xoá"
    if not removed:
        print(f"  Không có gì để dọn (giữ {args.keep} lô gần nhất)")
        return 0
    for path in removed:
        print(f"  {verb}: {path.relative_to(ROOT)}")
    print(f"  _final/ của các lô đó được giữ nguyên.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Chạy test để xác nhận nó xanh**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_clean.py' -v`
Expected: PASS — 6 test

- [ ] **Step 5: Thêm target Makefile**

Sửa `.PHONY` ở `Makefile:2` — thêm ` batch-clean`. Chèn sau `batch`:

```makefile
batch-clean: ## Xoá file trung gian của lô cũ, giữ _final (KEEP=3 mặc định, DRY=1 để xem trước)
	@python3 scripts/batch_clean.py --keep $${KEEP:-3} $${DRY:+--dry-run}
```

- [ ] **Step 6: Viết tài liệu**

Chèn vào `README.md` ngay trước mục `## Nguồn gốc code`:

```markdown
## Chạy lô material (khỏi bấm UI)

Thả material vào bốn ngăn rồi gõ một lệnh:

```bash
~/materials/characters/  outfits/  backgrounds/  drivers/

make batch-scan DIR=~/materials MODE=pair    # đẻ batch/<hôm nay>.yaml — SOÁT rồi mới chạy
make batch-validate FILE=batch/2026-08-18.yaml   # kiểm, không tiêu GPU
make batch FILE=batch/2026-08-18.yaml
make batch FILE=batch/2026-08-18.yaml RESUME=1   # chạy tiếp lô dở
```

Kết quả ở `out/latest/_final/`. Param của từng chặng tra bằng
`make batch-params TYPE=motion` (hoặc `tryon` / `enhance`) — bỏ trống thì chạy mặc định.

Thiết kế và các đánh đổi: `docs/superpowers/specs/2026-08-18-batch-runner-design.md`.
```

Chèn vào `docs/gpu-pod.md`, cuối mục **Runbook**:

```markdown
Chạy nhiều job một lượt thay vì bấm UI từng cái: xem §"Chạy lô material" trong `README.md`.
Runner tự `make gpu-up` nếu pod đang dừng, nhưng **không bao giờ tự `gpu-destroy`** —
và đặc biệt không destroy khi có run hỏng, vì đó đúng là lúc cần pod để đọc `pm2 logs worker`.
```

- [ ] **Step 7: Chạy toàn bộ cổng**

```bash
make batch-test
make check-batch-params
make check-job-types
bash motions-studio/setup/scrub-secrets.sh --check
```

Expected: cả bốn xanh.

- [ ] **Step 8: Commit**

```bash
git add scripts/batch_clean.py scripts/tests/test_batch_clean.py Makefile README.md docs/gpu-pod.md
git commit -m "batch: dọn file trung gian, giữ _final; tài liệu

batch-clean chỉ xoá runs/. File trung gian tồn tại để trả lời 'tryon ra ảnh gì'
khi kết quả cuối xấu — sau vài lô thì không ai hỏi nữa, nhưng bản cuối vẫn phải
giữ. Hai vòng đời khác nhau nên dọn khác nhau."
```

---

### Task 9: Nghiệm thu trên pod thật

Không viết code. Đây là chỗ chứng minh mọi thứ trên chạy thật — và là chỗ duy nhất tiêu tiền GPU.

**Files:**
- Modify: `docs/superpowers/specs/2026-08-18-batch-runner-design.md` (đổi dòng **Trạng thái**)

- [ ] **Step 1: Dựng manifest smoke**

```bash
mkdir -p /tmp/mat-smoke/{characters,outfits,drivers}
cp .smoke/nhanvat.jpeg /tmp/mat-smoke/characters/
cp .smoke/sanpham.jpeg /tmp/mat-smoke/outfits/
cp .smoke/dandong.mp4  /tmp/mat-smoke/drivers/
make batch-scan DIR=/tmp/mat-smoke MODE=cross OUT=/tmp/smoke.yaml
```

Sửa `/tmp/smoke.yaml`: thêm `motion: { quality: 540p, frames: 33 }` vào run — job nhỏ nhất mà vẫn chạy hết pipeline, cùng cấu hình `pod-smoke.sh:293` dùng. Thêm run thứ hai `pipeline: motion-enhance` bằng tay để nghiệm thu cả hai quy trình.

- [ ] **Step 2: Validate trước, và kiểm rằng nó BẮT được lỗi**

```bash
make batch-validate FILE=/tmp/smoke.yaml            # phải xanh
sed -i.bak 's/fpsInterp/fpsinterp/' /tmp/smoke.yaml
make batch-validate FILE=/tmp/smoke.yaml            # phải ĐỎ, gợi ý "fpsInterp"
mv /tmp/smoke.yaml.bak /tmp/smoke.yaml
```

Expected: lần hai in `param không có thật 'fpsinterp' — ý bạn là fpsInterp?` và exit 1. Nếu nó xanh thì lớp validate vô dụng và đừng chạy tiếp — sửa Task 3/4 trước.

- [ ] **Step 3: Chạy thật**

```bash
make gpu-status          # nếu down, bước sau tự gpu-up
make batch FILE=/tmp/smoke.yaml
```

Expected: cả hai run `done`; `out/latest/_final/` có 2 file mp4 > 100 KB; `out/latest/_index.tsv` có job id thật; `out/latest/runs/<id>/run.json` có `params_sent` và **`detailUpscale` KHÔNG có trong đó** với giá trị `true` (API ép `false`, `jobs.js:110-113` — đây là lý do `run.json` ghi param thật).

- [ ] **Step 4: Nghiệm thu resume — cách duy nhất biết nó chạy**

Chạy lại lô, Ctrl-C khi thấy dòng `motion: xong`, rồi:

```bash
make batch FILE=/tmp/smoke.yaml RESUME=1
```

Expected: run đầu in `bỏ qua (đã xong)`; run bị ngắt in `motion: bỏ qua (đã xong, 01-motion.mp4)` rồi chỉ chạy `enhance`. Nếu nó chạy lại `motion` thì resume hỏng — đó là 5 phút GPU mất mỗi lần đứt, và test `test_resume_chi_chay_lai_chang_con_thieu` đang nói dối.

- [ ] **Step 5: Kiểm điều khoản "không destroy khi hỏng"**

Sửa `/tmp/smoke.yaml` cho một run trỏ vào file driver hỏng (`printf 'x' > /tmp/hong.mp4`), chạy lại và xác nhận: lô vẫn chạy nốt run kia, kết thúc in `✗`, exit code 1, và **không** có lời gọi `gpu-destroy` nào.

- [ ] **Step 6: Ghi số đo thật vào spec, rồi commit**

Sửa dòng `**Ngày:** 18/08/2026 · **Trạng thái:** thiết kế, chưa triển khai.` thành trạng thái đã nghiệm thu, kèm **số đo thật**: mấy run, mỗi chặng bao nhiêu giây, dung lượng output, và có/không bắt được lỗi ở Step 2. Số đo, không phải tính từ.

```bash
bash motions-studio/setup/scrub-secrets.sh --check
git add docs/superpowers/specs/2026-08-18-batch-runner-design.md
git commit -m "batch: nghiệm thu trên pod thật — số đo, không phải lời hứa"
```

---

## Self-Review

**Spec coverage** — mọi mục của spec đều có task:

| Spec § | Task |
|---|---|
| §1 kiến trúc, Python 3 | 1–8 (Global Constraints khoá stdlib+PyYAML) |
| §2 thư mục vai trò, pair/cross, cảnh báo số run | 5 |
| §3 manifest, `defaults`, chỉ `inputs` bắt buộc | 4 |
| §4 pipeline là dữ liệu, bảng ánh xạ field | 2 |
| §4 byte đi vòng qua local | 7 (`_resolve_files` + `prev`) |
| §5 preflight, tự `gpu-up`, không tự destroy | 7 (`preflight`, tổng kết), 9 Step 5 |
| §5 chạy tuần tự | 7 (vòng lặp tuần tự, có ghi lý do) |
| §6 layout output, `_final` hardlink, `run.json` param thật | 7 |
| §6 journal `.state.json`, không ghi vào `.yaml` | 4, 7 (`test_manifest_goc_khong_bi_sua`) |
| §6 `batch-clean KEEP=3` | 8 |
| §7 `batch-params`, lỗ của extractor, cổng chống trôi | 3 |
| §7 validate chặn key lạ, chạy trước job đầu tiên | 3, 4, 7 (`--validate-only` trước `preflight`) |
| §8 bảng sai sót | 6 (poll/download), 7 (fail-fast, resume) |
| §9 MCP | **cố ý ngoài phạm vi** — spec §9 xếp nó giai đoạn 2, sau khi CLI chạy thật một lô |
| §10 test không cần pod, smoke thật, cổng | 1–8 (unittest), 9 (smoke), 3+8 (cổng) |

**Placeholder scan** — không có "TBD"/"tương tự Task N"/"thêm xử lý lỗi phù hợp"; mọi step code đều có code thật.

**Type consistency** — đã đối chiếu: `Run`/`Manifest` (Task 4) dùng nguyên ở Task 5 và 7; `Stage.inputs` (Task 2) được `_resolve_files` (Task 7) giải đúng cú pháp `prev|material:x?`; `ParamInfo.source` (Task 3) dùng ở `batch_params.py`; `Settings.base_url` (Task 1) dùng ở `client.py` (Task 6) và bị test Task 6 vá thành `http://` để chạy server giả; `state_path_for`/`load_state`/`save_state` (Task 4) dùng ở Task 7 với đúng hình dạng `{"version","batch","runs"}`.

**Một chỗ lệch cố ý, ghi ra để người triển khai không tưởng là lỗi:** `Task 6` test vá `Settings.base_url` sang `http://` vì server giả không có TLS, rồi khôi phục ở `tearDownClass` — bắt buộc, vì `unittest discover` chạy mọi module trong cùng một tiến trình. Nếu sau này `base_url` đổi thành thuộc tính thường (không phải `property`), test đó sẽ hỏng — sửa test, không sửa `config.py`.

## Sửa sau pre-flight scan (18/08/2026)

Bốn thay đổi so với bản commit `a6291b0`, đầy đủ lý do trong ledger
`.superpowers/sdd/2026-08-18-batch-runner/progress.md`:

- **R7 (load-bearing):** bề mặt param có **hai tầng**. `quality` không phải param của worker —
  `run_motion` không hề gọi `params.get("quality")` (đo bằng AST); `enforceMotionResolution`
  (`api/src/motion-resolution.js:23-38`) dịch nó thành width/height trước khi ghi DB. Bản đầu khai
  `quality` trong `motion.allowed` sẽ làm cổng `check_drift` **đỏ vĩnh viễn** và làm validate **từ
  chối** đúng cái param mà `pod-smoke.sh:292`, `batch/example.yaml` và smoke Task 9 đều dùng. Thêm
  khối `api` vào `batch-params.json`; `known_params()` = AST ∪ extra ∪ api.
- **R4:** `defaults` chỉ áp cho chặng thuộc pipeline của run. Bản đầu merge cho mọi chặng, nên một
  `defaults: { tryon: … }` vô hại sẽ làm mọi run `motion-enhance` bị validate báo sai.
- **R3:** Task 6 phải khôi phục `Settings.base_url` ở `tearDownClass`. Bản đầu vá ở cấp lớp mà
  không trả lại → rò sang mọi test module chạy sau.
- **R2:** dòng Interfaces của Task 6 thiếu tham số `now` của `poll_job` (code và test đều có).
