# `ltx-ss-prebuilt/` — Drop-zone model cho node **SS** (LTX-2.3 Ảnh/Text→Video)

Đặt **2 file đã-xử-lý-sẵn** vào ĐÂY **trước khi chạy setup**. Setup (`setup/setup-pm2.sh`) tự copy chúng vào
ComfyUI → **end-user KHÔNG cần tải checkpoint 46GB, không cần xử lý gì** (đĩa cuối ~38GB thay vì ~82GB).

| File cần đặt vào đây | Dung lượng | Là gì |
|---|---|---|
| `ltx-2.3-vae.safetensors` | ~1.4GB | Video VAE LTX-2.3 (đã tách kèm config) → `models/vae/` |
| `ltx-2.3-ckpt-min.safetensors` | ~4.1GB | Checkpoint đã **cắt transformer** (giữ audio-vae + projection) → `models/checkpoints/ltx-2.3-22b-distilled-1.1.safetensors` |

> 2 file này **KHÔNG có sẵn công khai** (là bản tự xử lý từ checkpoint gốc). Lấy từ người triển khai / kho nội bộ
> của bạn. Phần còn lại của node SS (GGUF unet ~20GB + Gemma text-encoder ~13GB) thì setup **tự tải từ HuggingFace**
> theo `comfyui/models.txt`.

## Cách dùng

1. Copy 2 file trên vào thư mục này (`motion-backend/ltx-ss-prebuilt/`).
   - Hoặc để nơi khác và set env `LTX_SS_PREBUILT_DIR=/đường/dẫn` trước khi chạy setup.
2. Chạy `./setup/setup-pm2.sh` như bình thường → log sẽ báo *"Node SS: dùng 2 file prebuilt … (khỏi tải 46GB)"*.

## Không có 2 file? (fallback tự-tải)

Bỏ `#` ở dòng `checkpoints | ltx-2.3-22b-distilled-1.1.safetensors | …` trong `comfyui/models.txt`.
Setup sẽ tải checkpoint 46GB rồi tự **cắt transformer** (`comfyui/ltx-ss-postprocess.py`) còn ~4GB — đĩa cuối vẫn
~38GB, chỉ tốn băng thông tải ~79GB lần đầu.

> Các `*.safetensors` ở thư mục này được `.gitignore` (quá lớn, không commit vào git).
