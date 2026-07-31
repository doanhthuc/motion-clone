# 📘 MOTION TRANSFER PIPELINE — FULL PLAN

**Setup ComfyUI + Wan 2.2 Animate cho Motion Transfer với Audio Passthrough**

---

## 📋 PROJECT INFO

| Field | Value |
|---|---|
| **Project Name** | Motion Transfer Pipeline |
| **Version** | 1.1 (with audio passthrough) |
| **Goal** | Sao chép chuyển động từ video tham chiếu, áp lên nhân vật trong ảnh + giữ nguyên audio |
| **Input** | 1 ảnh nhân vật + 1 video chứa motion (có audio) |
| **Output** | Video 720p/1080p, ≤ 30 giây, 16-32 fps, có audio gốc |
| **Main Model** | Wan 2.2 Animate 14B (Alibaba, Apache 2.0) |
| **Hardware** | Ubuntu 24.04.4 + RTX 5090 32GB + i7-14700K + 62GB RAM |
| **Co-tenant** | Ollama (manual unload trước workflow) |
| **Storage** | 1.6TB free, NVMe 3.4 GB/s |

---

## 🎯 DESIGN PRINCIPLES

1. **Manual control over GPU** — User quyết định khi nào unload Ollama. Không auto.
2. **Isolation** — ComfyUI venv riêng, không pollute system Python.
3. **Reproducibility** — Mọi config viết trong scripts, có thể setup lại từ đầu.
4. **Safety first** — Verify từng phase trước khi sang phase tiếp theo.
5. **Bias toward simplicity** — Không over-engineer. Web UI là interface chính.

---

## ⚠️ CRITICAL RULES

### Rule 1: Ollama Unload (Manual)
```
🔴 BẮT BUỘC trước mỗi workflow:
   1. Check VRAM với vram-check.sh
   2. Nếu < 28GB free → ollama-free.sh
   3. Verify lại VRAM
   4. Mới start ComfyUI
```

### Rule 2: PyTorch Compute Capability
```
🔴 SAU MỖI lần install/update:
   torch.cuda.get_device_capability(0) MUST == (12, 0)
   
   Nếu không → PyTorch sai version, ComfyUI sẽ crash
```

### Rule 3: Audio Passthrough
```
🔴 Output video PHẢI có audio từ source:
   - Test workflows: ComfyUI native (VHS nodes)
   - Production: FFmpeg post-merge (audio-merge.sh)
```

---

## 🏗️ ARCHITECTURE

### Directory Structure

```
~/ai/
├── ComfyUI/                              # Main application
│   ├── venv/                             # Python 3.12 isolated env
│   ├── custom_nodes/
│   │   ├── ComfyUI-Manager
│   │   ├── ComfyUI-KJNodes
│   │   ├── ComfyUI-WanVideoWrapper
│   │   ├── ComfyUI-VideoHelperSuite
│   │   ├── ComfyUI-Frame-Interpolation
│   │   └── ComfyUI_essentials
│   ├── models/                           # ~22GB models
│   │   ├── diffusion_models/
│   │   ├── text_encoders/
│   │   ├── vae/
│   │   ├── clip_vision/
│   │   └── loras/
│   ├── input/
│   ├── output/
│   └── main.py
│
├── scripts/                              # 5 helper scripts
│   ├── vram-check.sh
│   ├── ollama-free.sh
│   ├── comfy-start.sh
│   ├── comfy-stop.sh
│   └── audio-merge.sh
│
├── workflows/                            # 8 workflow JSON files
│   ├── 01-test-5s-480p.json
│   ├── 02-test-5s-720p.json
│   ├── 03-prod-30s-720p.json
│   ├── 04-prod-30s-1080p.json
│   └── variants/
│       ├── 05-quick-5s-720p-causvid.json
│       ├── 06-quality-30s-720p-hq.json
│       ├── 07-portrait-vertical-30s.json
│       └── 08-replace-mode-30s.json
│
├── sources/                              # Input materials
│   ├── images/
│   └── videos/
│
├── outputs/                              # Final processed videos
├── logs/                                 # Runtime logs
├── PLAN.md                               # This file
├── README.md                             # Quick start
├── TROUBLESHOOTING.md
├── VERSIONS.md
├── AUDIO_HANDLING.md
└── WORKFLOWS.md
```

### Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                      RTX 5090 (32 GB VRAM)                   │
│                                                              │
│  ┌──────────────┐         ┌────────────────────────────┐   │
│  │   Ollama     │         │   ComfyUI                  │   │
│  │   (15 GB)    │  ←→     │   (28 GB peak)             │   │
│  │              │  MUTEX  │                            │   │
│  └──────────────┘         │   ┌──────────────────────┐ │   │
│                           │   │ Wan 2.2 Animate FP8  │ │   │
│                           │   │  + UMT5 + VAE + CLIP │ │   │
│                           │   └──────────────────────┘ │   │
│                           └────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
        ↑                              ↑
        │ Port 11434                   │ Port 8188
        ↓                              ↓
   ┌──────────┐                   ┌──────────────┐
   │  Other   │                   │   Browser    │
   │  apps    │                   │ (LAN access) │
   └──────────┘                   └──────────────┘
```

### Pipeline Flow (with Audio)

```
INPUT
─────
📷 reference_image.jpg
🎬 motion_video.mp4 ──┬──→ frames (video)
                     └──→ audio (passthrough) ──┐
                                                 │
┌────────────────────────────────────────┐      │
│  ComfyUI + Wan 2.2 Animate             │      │
│                                        │      │
│  frames + ref_image                    │      │
│       ↓                                │      │
│  [Wan 2.2 Animate]                     │      │
│       ↓                                │      │
│  generated_frames (silent)             │      │
│       ↓                                │      │
│  [RIFE 2x interpolation]               │      │
│       ↓                                │      │
│  output_silent.mp4 ──┐                 │      │
└──────────────────────│─────────────────┘      │
                       ↓                        ↓
       ┌──────────────────────────────────────────┐
       │      FFmpeg Audio Merger                  │
       │      (audio-merge.sh)                     │
       └──────────────────────────────────────────┘
                       ↓
OUTPUT
──────
🎬 output_final.mp4 (video + audio)
```

### Software Stack

| Layer | Component | Version | Status |
|---|---|---|---|
| OS | Ubuntu | 24.04.4 LTS | ✅ Đã có |
| GPU Driver | NVIDIA | 595.71.05 | ✅ Đã có |
| CUDA System | CUDA Toolkit | 13.2 | ✅ Đã có |
| CUDA Runtime | (PyTorch built-in) | 12.8 | 🔄 Sẽ cài |
| Python | Python | 3.12.3 | ✅ Đã có |
| ML Framework | PyTorch nightly | 2.7.0.dev+cu128 | 🔄 Sẽ cài |
| Acceleration | Triton + SageAttention 2 | Latest | 🔄 Sẽ cài |
| App | ComfyUI | Latest main | 🔄 Sẽ cài |
| Extensions | 6 custom nodes | Latest | 🔄 Sẽ cài |
| Models | Wan 2.2 Animate FP8 | 14B | 🔄 Sẽ tải |

---

## 🎬 PHASES

### Overview

| Phase | Mục tiêu | Thời gian | Checkpoint |
|---|---|---|---|
| 0 | Pre-flight & verification | 15 min | CP1 |
| 1 | System dependencies | 5 min | — |
| 2 | CUDA PATH setup | 5 min | — |
| 3 | ComfyUI base install | 5 min | — |
| 4 | PyTorch nightly cu128 ★ | 10 min | **CP2** |
| 5 | Extensions (6 nodes) | 20-30 min | — |
| 6 | Helper scripts (5 files) | 15 min | — |
| 7 | Download models (~22GB) | 30 min - 3 hours | **CP3** |
| 8 | Test & production | 1.5-2.5 hours | **CP4 → CP5** |

**Total: 2.5 - 5 hours**

---

### PHASE 0 — Pre-flight Check

**Mục tiêu:** Verify môi trường trước khi cài.

**Tasks:**
- 0.1. Test internet speed (3 sources: speedtest, HuggingFace, GitHub)
- 0.2. Verify CUDA 13.2 và nvcc
- 0.3. Verify Ollama API responsive
- 0.4. Tạo skeleton `~/ai/`
- 0.5. Verify tmux installed

**Acceptance:**
- ✅ Internet ≥ 1 MB/s từ ít nhất 1 source
- ✅ `nvcc --version` works
- ✅ Ollama API returns 200 OK
- ✅ Directory tree created

**Commands:** See `EXECUTION.md` Phase 0 section.

---

### PHASE 1 — System Dependencies

**Mục tiêu:** Cài packages OS.

**Packages:**
- ffmpeg, cmake, build-essential
- python3.12-venv, python3.12-dev, python3-pip
- libgl1, libglib2.0-0, libsm6, libxext6, libxrender-dev, libgomp1
- wget, curl, htop, nvtop, tmux, aria2, jq, bc

**Acceptance:**
- ✅ ffmpeg, cmake, nvtop, aria2 all installed

---

### PHASE 2 — CUDA Environment

**Mục tiêu:** Setup PATH cho CUDA 13.2.

**Tasks:**
- Thêm CUDA_HOME, PATH, LD_LIBRARY_PATH vào ~/.bashrc
- Thêm aliases cho ComfyUI helpers
- Verify nvcc accessible

**Note:** System CUDA 13.2 ≠ PyTorch CUDA 12.8 runtime. Không xung đột.

**Acceptance:**
- ✅ `nvcc --version` returns CUDA 13.2
- ✅ `$CUDA_HOME` = `/usr/local/cuda`

---

### PHASE 3 — ComfyUI Base Install

**Mục tiêu:** Clone ComfyUI + tạo venv.

**Tasks:**
- `git clone ComfyUI` vào `~/ai/ComfyUI`
- Create Python 3.12 venv
- Upgrade pip, wheel, setuptools

**Acceptance:**
- ✅ `which python` (after activate) → venv path
- ✅ Python 3.12.3 active

---

### PHASE 4 — PyTorch nightly cu128 ★ CRITICAL — CP2

**Mục tiêu:** Cài PyTorch hỗ trợ Blackwell sm_120.

**🛑 CHECKPOINT 2 — Critical phase**

**Tasks:**
- Install PyTorch nightly cu128
- Install ComfyUI requirements.txt
- **VERIFY Compute capability = (12, 0)** ← BẮT BUỘC
- Run GPU compute test
- Test launch ComfyUI

**Acceptance (BẮT BUỘC):**
- ✅ `torch.__version__` chứa `dev` (nightly)
- ✅ `torch.version.cuda == '12.8'`
- ✅ **`torch.cuda.get_device_capability(0) == (12, 0)`**
- ✅ Matrix multiplication test passes
- ✅ ComfyUI starts on port 8188

**❌ Nếu Compute ≠ (12, 0) → STOP, fix trước khi tiếp.**

---

### PHASE 5 — Extensions

**Mục tiêu:** Cài 6 custom nodes + 2 acceleration libs.

**Install order:**
1. ComfyUI-Manager
2. Triton (pip)
3. SageAttention (pip)
4. ComfyUI-KJNodes (cho "Patch Sage Attention" node)
5. ComfyUI-WanVideoWrapper (CORE)
6. ComfyUI-VideoHelperSuite (load/save video + audio)
7. ComfyUI-Frame-Interpolation (RIFE)
8. ComfyUI_essentials

**Sau khi cài: RESTORE PYTORCH**
- Custom nodes thường downgrade torch
- Phải re-install nightly cu128 và re-verify Compute (12, 0)

**Acceptance:**
- ✅ All extensions import OK
- ✅ ComfyUI launches without errors
- ✅ Compute capability vẫn (12, 0)

---

### PHASE 6 — Helper Scripts

**Mục tiêu:** Tạo 5 scripts hỗ trợ daily workflow.

**Scripts:**

| Script | Purpose |
|---|---|
| `vram-check.sh` | Check VRAM ≥ 28GB free |
| `ollama-free.sh` | Unload Ollama (3 fallback methods) |
| `comfy-start.sh` | Start ComfyUI (auto pre-check) |
| `comfy-stop.sh` | Stop ComfyUI + optional Ollama restart |
| `audio-merge.sh` ★ | Merge audio source → silent video |

**Philosophy:** Tools, not auto-orchestration. User controls decisions.

**Acceptance:**
- ✅ All 5 scripts executable
- ✅ vram-check detects Ollama VRAM usage
- ✅ audio-merge handles no-audio source

---

### PHASE 7 — Download Models — CP3

**🛑 CHECKPOINT 3 — Verify internet before 22GB download**

**Mục tiêu:** Tải ~22GB models với resume capability.

**Required models:**

| File | Size | Folder |
|---|---|---|
| Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors | 14 GB | diffusion_models/ |
| umt5-xxl-enc-bf16.safetensors | 6.7 GB | text_encoders/ |
| Wan2_1_VAE_bf16.safetensors | 250 MB | vae/ |
| clip_vision_h.safetensors | 1.2 GB | clip_vision/ |
| Wan22Animate_relight_lora_fp16.safetensors | 500 MB | loras/ |

**Optional:**

| File | Size | Folder |
|---|---|---|
| Wan21_CausVid_14B_T2V_lora_rank32.safetensors | 600 MB | loras/ |

**Strategy:**
- `hf_transfer` cho multi-thread download
- `tmux` để không mất khi disconnect SSH
- Parallel download (5 files)
- Auto-resume on network drop

**Fallback if slow:**
- Mirror: `hf-mirror.com`
- aria2c với 16 connections
- VPN nếu rate limited

**Acceptance:**
- ✅ All files đúng size (verify với `du -sh`)
- ✅ Total ~22-23 GB

---

### PHASE 8 — Test & Production — CP4 → CP5

**🛑 CHECKPOINT 4 — Test 5s OK trước khi run 30s**

**Mục tiêu:** Validate pipeline từ test → production.

**Sub-phases:**

#### 8.1. Chuẩn bị inputs
- ~/ai/sources/images/ (reference characters)
- ~/ai/sources/videos/ (motion videos with audio)

#### 8.2. Smoke test 5s/480p (Workflow 01)
- Resolution: 832×480
- Frames: 81 (5s @ 16fps)
- Steps: 20
- Expected: 1-2 min, VRAM peak ~18-20 GB
- Audio: ComfyUI native passthrough

#### 8.3. Quality test 5s/720p (Workflow 02)
- Resolution: 720×1280
- Frames: 81
- Steps: 30
- Expected: 3-5 min, VRAM peak ~22-24 GB

#### 8.4. Audio sync verification
- Check duration match
- Manual playback check

#### 8.5. Production 30s/720p (Workflow 03)
- Resolution: 720×1280
- Frames: 481 (30s @ 16fps)
- Steps: 30
- Context window: 81, overlap: 16
- Expected: 15-25 min, VRAM peak ~25-27 GB
- Audio: FFmpeg post-merge

#### 8.6. Production 30s/1080p (Workflow 04, optional)
- Option A: Native 1080p với block swap (~40 min)
- Option B: 720p generate + upscale (~22 min) — recommended

#### 8.7. Test variants
- 05: Quick mode (CausVid + TeaCache, 8 steps)
- 06: Quality mode (40 steps, no acceleration)
- 07: Portrait 720x1280 (Reels/TikTok)
- 08: Replace mode (thay người trong video)

**🛑 CHECKPOINT 5 — Final review & sign-off**

**Acceptance:**
- ✅ All 4 core workflows produce valid output
- ✅ All 4 variants tested
- ✅ Audio properly synced in all outputs
- ✅ No OOM errors
- ✅ Generation times within estimates

---

## 📊 WORKFLOWS

### Workflow Structure (Common)

```
[Load Reference Image]
        ↓
[Resize to target resolution]
        ↓
[Load Motion Video] ──→ frames + audio
                              ↓
[Load Wan 2.2 Animate Model] (FP8)
        ↓
[Patch Sage Attention] ← KJ Nodes
        ↓
[Load Text Encoder (UMT5)]
        ↓
[Load VAE]
        ↓
[Load CLIP Vision]
        ↓
[WanVideo Animate Embeds] ← merge image + motion
        ↓
[Context Options] ← only for long videos
        ↓
[WanVideo Sampler] ← steps, cfg, scheduler
        ↓
[WanVideo Decode] ← VAE decode
        ↓
[RIFE Frame Interpolation] ← 16fps → 32fps
        ↓
[Video Combine] ← encode MP4 (silent)
        ↓
[Save Video]
        ↓
(External: audio-merge.sh)
        ↓
final.mp4 (with audio)
```

### Workflow Parameters Matrix

| Param | 01 (5s/480p) | 02 (5s/720p) | 03 (30s/720p) | 04 (30s/1080p) |
|---|---|---|---|---|
| Resolution | 832×480 | 720×1280 | 720×1280 | 1280×720→upscale |
| Frames | 81 | 81 | 481 | 481 |
| Duration | 5s | 5s | 30s | 30s |
| Steps | 20 | 30 | 30 | 30 |
| CFG | 6.0 | 6.0 | 6.0 | 6.0 |
| Shift | 8.0 | 8.0 | 8.0 | 8.0 |
| Scheduler | unipc | unipc | unipc | unipc |
| Context window | OFF | OFF | 81/16 | 81/16 |
| Block swap | OFF | OFF | OFF | ON (20) |
| TeaCache | OFF | OFF | optional | optional |
| RIFE | 2x | 2x | 2x | 2x |
| Upscale | NO | NO | NO | YES (RealESRGAN) |
| Audio method | VHS native | VHS native | FFmpeg | FFmpeg |
| Expected time | 1-2 min | 3-5 min | 15-25 min | 35-50 min |
| VRAM peak | ~18 GB | ~22 GB | ~26 GB | ~28 GB |

### Variants

| # | File | Purpose | Special |
|---|---|---|---|
| 05 | `05-quick-5s-720p-causvid.json` | Fast prototype | TeaCache + CausVid LoRA, 8 steps, ~3 min |
| 06 | `06-quality-30s-720p-hq.json` | Portfolio output | 40 steps, no acceleration, ~30-40 min |
| 07 | `07-portrait-vertical-30s.json` | Social media | 720×1280 portrait |
| 08 | `08-replace-mode-30s.json` | Person swap | Replace mode (keep video bg) |

---

## 🔊 AUDIO HANDLING

### Strategy

Wan 2.2 Animate **chỉ sinh video frames, không xử lý audio**. Cần pipeline audio passthrough riêng.

### 3 Approaches

**Approach 1: ComfyUI Native (VHS nodes)**
- VHS_LoadVideo có output `audio`
- VHS_VideoCombine accepts input `audio`
- Pros: Single workflow
- Cons: Cần verify với context windowing 30s

**Approach 2: FFmpeg Post-Processing**
```bash
audio-merge.sh source.mp4 generated_silent.mp4 final.mp4
```
- Pros: Reliable, full control
- Cons: Cần 2 steps

**Approach 3: Hybrid**
- ComfyUI generates silent video
- audio-merge.sh tự động merge sau

### Usage by Workflow

| Workflow | Audio Method | Reason |
|---|---|---|
| 01, 02 (test 5s) | VHS native | Đơn giản, đủ cho test |
| 03, 04 (prod 30s) | FFmpeg | Reliability for long video |
| 05 (quick) | VHS native | Quick test |
| 06 (HQ) | FFmpeg | Production quality |
| 07 (vertical) | FFmpeg | Production |
| 08 (replace) | VHS native | Audio đã có trong source |

### Edge Cases

| Case | Handling |
|---|---|
| Source no audio | Skip merge, output silent |
| Audio > Video duration | Truncate audio to video length |
| Audio < Video duration | Use `-shortest` flag |
| Multi-channel (5.1) | Preserve as-is or downmix stereo |
| HEVC/AV1 codec | FFmpeg handles all codecs |
| FPS mismatch | Audio không bị stretch (RIFE chỉ tăng FPS không đổi duration) |

---

## ⚠️ RISK REGISTER

| # | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| R1 | PyTorch nightly bug | Medium | High | Pin version sau khi confirm; backup wheels |
| R2 | Custom nodes downgrade torch | High | Medium | Verify Compute (12,0) sau mỗi install |
| R3 | Internet chậm | Unknown | High | Test 3 sources; có aria2 backup |
| R4 | HuggingFace rate limit | Low | Medium | Mirror hf-mirror.com |
| R5 | OOM khi 30s/1080p | Medium | Medium | Block swap; fallback 720p+upscale |
| R6 | Ollama không unload sạch | Low | Medium | 3 fallback methods trong script |
| R7 | SageAttention compile fail | Low | High | Skip, dùng PyTorch SDPA |
| R8 | Disk cạn | Low | High | 1.6TB free, OK |
| R9 | Triton vs CUDA mismatch | Medium | Medium | Document versions; rollback plan |
| R10 | Custom node breaking | Medium | Low | Pin git commit hashes |
| R11 ★ | Audio drift sau 30s | Low | Medium | Test với lip-sync video |
| R12 ★ | VHS audio merge fail | Low | Low | Fallback FFmpeg post-merge |
| R13 ★ | Source codec lạ | Low | Low | FFmpeg supports all |
| R14 ★ | Audio bitrate giảm | Medium | Low | `-c:a copy` thay vì re-encode |

---

## ✅ ACCEPTANCE CRITERIA

### Functional
- [ ] ComfyUI accessible at `http://localhost:8188`
- [ ] All 4 core workflows produce valid MP4 output
- [ ] All 4 variants tested and working
- [ ] Output video có audio đồng bộ với source
- [ ] Identity nhân vật match với reference image
- [ ] No audio drift sau 30s
- [ ] Source no-audio → silent output (graceful)

### Performance
- [ ] 5s/720p: ≤ 5 phút
- [ ] 30s/720p: ≤ 25 phút
- [ ] VRAM peak: ≤ 28 GB
- [ ] Zero OOM errors

### Operational
- [ ] All 5 helper scripts working
- [ ] VRAM check phát hiện đúng Ollama usage
- [ ] Ollama-free thực sự giải phóng VRAM
- [ ] Logs saved to ~/ai/logs/
- [ ] Audio merge handle edge cases

### Documentation
- [ ] PLAN.md (this file)
- [ ] README.md (quick start)
- [ ] TROUBLESHOOTING.md
- [ ] VERSIONS.md (pinned versions)
- [ ] AUDIO_HANDLING.md
- [ ] WORKFLOWS.md (when to use which)

---

## 🚦 CHECKPOINTS

```
✅ CP1: Before Phase 0
       → User approve plan (DONE)
       
🛑 CP2: Before Phase 4 (PyTorch nightly cu128)
       → Critical phase, verify Phase 1-3 OK
       
🛑 CP3: Before Phase 7 (Download 22GB)
       → Verify internet speed adequate
       
🛑 CP4: Before Phase 8.5 (Production 30s)
       → Confirm test 5s working OK
       
🛑 CP5: After completion
       → Final review & sign-off
```

---

## 🔄 ROLLBACK PLAN

| Phase | Rollback Action |
|---|---|
| 1-2 | `sudo apt remove` packages |
| 3 | `rm -rf ~/ai/ComfyUI` |
| 4 | `pip uninstall torch torchvision torchaudio` |
| 5 | `rm -rf` từng custom_node folder |
| 6 | `rm` script files |
| 7 | `rm` model files (keep structure) |
| 8 | Re-run với settings nhẹ hơn |

**Full reset:** `rm -rf ~/ai/` → restart Phase 0.

---

## 📅 DAILY OPERATIONS

### Workflow Hàng Ngày

```
1. Open terminal
2. ~/ai/scripts/vram-check.sh
3. (If needed) ~/ai/scripts/ollama-free.sh
4. ~/ai/scripts/comfy-start.sh
5. Open browser: http://localhost:8188
6. Load workflow JSON
7. Set inputs (image + video)
8. Queue Prompt
9. Wait for generation (5-25 min)
10. Download output from ~/ai/ComfyUI/output/
11. (If 30s) Run audio-merge.sh
12. Ctrl+C to stop ComfyUI
13. (Optional) Restart Ollama via comfy-stop.sh
```

### Weekly Maintenance

- Update ComfyUI: `git pull` in `~/ai/ComfyUI/`
- Update custom nodes via Manager UI
- Verify Compute (12, 0) after updates
- Clean old logs in `~/ai/logs/`
- Clean old outputs if disk getting full

### Monitoring

- Real-time GPU: `nvtop`
- Disk usage: `du -sh ~/ai/*`
- Temperature: `nvidia-smi`

---

## 📦 DELIVERABLES

### Scripts (5 files)
- `vram-check.sh`
- `ollama-free.sh`
- `comfy-start.sh`
- `comfy-stop.sh`
- `audio-merge.sh` ★

### Workflows (8 files)
- Core: 01, 02, 03, 04
- Variants: 05, 06, 07, 08

### Documentation (6 files)
- PLAN.md (this)
- README.md
- TROUBLESHOOTING.md
- VERSIONS.md
- AUDIO_HANDLING.md
- WORKFLOWS.md

### Models (~23 GB)
- Wan2.2-Animate-14B FP8 (14 GB)
- umt5-xxl text encoder (6.7 GB)
- Wan VAE (250 MB)
- CLIP Vision H (1.2 GB)
- Relight LoRA (500 MB)
- CausVid LoRA (600 MB, optional)

---

## 🚫 OUT OF SCOPE

Để tránh scope creep, **KHÔNG** làm:

- ❌ Auto-orchestration Ollama/ComfyUI
- ❌ REST API wrapper
- ❌ Custom web interface
- ❌ Multi-GPU setup
- ❌ Model training/fine-tuning
- ❌ Other video models (HunyuanVideo, LTX, Mochi)
- ❌ Image generation
- ❌ TTS / voice synthesis
- ❌ Audio editing/effects (chỉ passthrough)
- ❌ Cloud deployment
- ❌ Multi-user management

---

## 📈 SUCCESS METRICS

### Setup Phase
- ⏱️ Total setup time: 2-4 hours
- 💾 Disk usage: ~25 GB
- 📚 Knowledge: User understands pipeline

### Production Phase
- 🎬 First successful 30s video: within 1 hour after setup
- ⚡ Average generation: 20 min for 30s/720p
- 🎯 Success rate: ≥ 95%
- 🔄 Iteration time: < 5 min for tests

---

## 📞 SUPPORT

### If something breaks:
1. Check `~/ai/logs/comfyui-*.log`
2. Verify Compute capability: `python -c "import torch; print(torch.cuda.get_device_capability(0))"`
3. Verify VRAM: `~/ai/scripts/vram-check.sh`
4. Check Ollama not stealing VRAM: `nvidia-smi`

### Common Issues:
- **OOM:** Reduce resolution or enable block swap
- **Black output:** Check Sage Attention setup (KJ patch node, not flag)
- **Slow:** Enable TeaCache, use CausVid LoRA
- **Audio out of sync:** Use audio-merge.sh post-processing
- **PyTorch downgraded:** Re-install nightly cu128

---

## 📝 VERSION HISTORY

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-05-23 | Initial plan |
| 1.1 | 2026-05-23 | Added audio passthrough, 8 workflows, audio-merge.sh |

---

## ✍️ APPROVAL

- [x] Plan reviewed by user
- [x] Audio handling added
- [x] 8 workflow files confirmed
- [x] 5 checkpoints confirmed
- [x] Ready to execute Phase 0

---

**Generated:** 2026-05-23  
**Owner:** ubuntu@bridgellm-02  
**Hardware:** RTX 5090 32GB + i7-14700K + 62GB RAM  
**Target:** Motion Transfer, 720p/1080p, ≤30s, with audio