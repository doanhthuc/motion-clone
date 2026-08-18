.DEFAULT_GOAL := help
.PHONY: check-comfy-nodes help setup dev down clean gpu-preflight gpu-provision gpu-wait gpu-bootstrap gpu-fe gpu-up gpu-down gpu-destroy gpu-db-dump gpu-db-check gpu-status gpu-logs batch-test batch-params check-batch-params batch-scan batch-validate batch batch-clean

help: ## Show this help
	@echo "motion-clone — make targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# --- Frontend (motions) — runs locally on this machine -------------------------

setup: ## Install FE deps + create motions/.env if missing
	cd motions && npm install
	@[ -f motions/.env ] || cp motions/.env.example motions/.env

dev: ## Run the FE dev server (http://localhost:2030)
	cd motions && npm run dev

down: ## Stop the FE dev server
	@-pkill -f "nuxt dev --port 2030" 2>/dev/null || true
	@echo "stopped motions dev server"

clean: down ## Remove FE node_modules/.nuxt/.output (keeps motions/.env)
	rm -rf motions/node_modules motions/.nuxt motions/.output
	@echo "cleaned motions/ — run 'make setup' to rebuild"

# --- Backend (motions-studio) on a rented GPU pod — vast.ai / RunPod -----------
# The app never talks to vast.ai/RunPod directly: motions/.env's NUXT_MOTION_API_URL is the only
# thing that changes once the pod is up. Root .env holds the rental + deploy config. See
# docs/gpu-pod.md for the full walkthrough (Cloudflare token, costs, gotchas).

env = $(shell grep -E '^$(1)=' .env 2>/dev/null | cut -d= -f2- | sed -E 's/[[:space:]]*\#.*$$//' | tr -d '"')

scrub-check: ## Gate: fail if any third-party credential or personal email is tracked
	@bash motions-studio/setup/scrub-secrets.sh --check

check-job-types: ## Gate: job type lists (image, setup profiles, dispatcher) must agree
	@node scripts/check-job-types.mjs

check-comfy-nodes: ## Gate: bốn danh sách custom node ComfyUI (2 image + 2 setup profile) phải khớp
	@node scripts/check-comfy-nodes.mjs

batch-test: ## Gate: unit test của batch runner (không cần pod, không tốn tiền)
	@python3 -m unittest discover -s scripts/tests -p 'test_batch_*.py'

batch-params: ## Liệt kê param một job type nhận (TYPE=motion|tryon|enhance)
	@python3 scripts/batch_params.py $${TYPE:-}

check-batch-params: ## Gate: scripts/batch-params.json phải khớp linux.py
	@python3 scripts/batch_params.py --check

batch-scan: ## Quét thư mục material → manifest nháp (DIR=~/materials MODE=pair|cross)
	@test -n "$(DIR)" || { echo "cần DIR=~/materials (4 ngăn: characters outfits backgrounds drivers)"; exit 1; }
	@python3 scripts/batch_scan.py --dir "$(DIR)" --mode "$${MODE:-pair}" $${OUT:+--out "$$OUT"} $${FORCE:+--force}

batch-validate: ## Kiểm manifest mà KHÔNG tiêu GPU (FILE=batch/….yaml)
	@test -n "$(FILE)" || { echo "cần FILE=batch/….yaml"; exit 1; }
	@python3 scripts/batch_run.py --file "$(FILE)" --validate-only

batch: ## Chạy một lô (FILE=batch/….yaml, RESUME=1 để chạy tiếp lô dở)
	@test -n "$(FILE)" || { echo "cần FILE=batch/….yaml"; exit 1; }
	@python3 scripts/batch_run.py --file "$(FILE)" $${RESUME:+--resume} $${FAIL_FAST:+--fail-fast}

batch-clean: ## Xoá file trung gian của lô cũ, giữ _final (KEEP=3 mặc định, DRY=1 để xem trước)
	@python3 scripts/batch_clean.py --keep $${KEEP:-3} $${DRY:+--dry-run}

gpu-preflight: ## Check root .env is complete BEFORE you spend money on a pod
	@bash scripts/gpu-preflight.sh

gpu-provision: ## Find + rent a GPU pod (dry-run; CONFIRM=yes to actually rent)
	@bash scripts/pod-provision.sh

gpu-wait: ## Wait for a freshly rented pod's SSH to come up (TIMEOUT=25 min); saves host/port to .env
	@bash scripts/pod-wait.sh

gpu-bootstrap: ## rsync motions-studio + run the SETUP_PROFILE setup script on the pod (idempotent)
	@bash scripts/pod-bootstrap.sh

gpu-fe: ## Re-deploy ONLY the frontend to the pod (rsync + build + PM2 restart, ~2 min)
	@bash scripts/pod-fe.sh

gpu-up: ## Start the pod and wait until the backend answers
	@test -n "$(call env,GPU_INSTANCE_ID)" || { echo "set GPU_INSTANCE_ID in .env (see docs/gpu-pod.md)"; exit 1; }
ifeq ($(shell grep -E '^GPU_PROVIDER=' .env 2>/dev/null | cut -d= -f2),runpod)
	@runpodctl pod start $(call env,GPU_INSTANCE_ID)
else
	@vastai start instance $(call env,GPU_INSTANCE_ID)
endif
	@printf "waiting for backend"
	@until curl -sf https://$(call env,DOMAIN)/health >/dev/null 2>&1; do printf "."; sleep 5; done
	@echo " ready → https://$(call env,DOMAIN)"

gpu-down: ## Pause the pod for a short break (container disk keeps billing — prefer gpu-destroy when done)
	@# Điểm dump CHÍNH: đây là lúc cuối cùng còn ssh được vào pod. Sau khi dừng, pod im lặng
	@# cho tới khi bật lại, mà volume thì chỉ mount được qua pod — nên không còn đường nào
	@# sao lưu hay kiểm tra nữa.
	@# `|| echo` là CỐ Ý: dump hỏng KHÔNG được chặn việc dừng một pod $$0,99/giờ, và gpu-down
	@# vốn không làm mất DB (container disk còn nguyên). Chặn ở đây là đốt tiền thật để giữ
	@# thứ chưa bị đe doạ.
	@# POD_VOLUME phải truyền theo: trên một pod MỚI, `.env` CỦA POD không có key đó
	@# (pod-volume.sh:309 gác khối ghi bằng `[ -f .env ]`, mà lúc nó chạy `.env` chưa tồn tại —
	@# rsync ở pod-bootstrap.sh:99-100 loại trừ cả `.env` lẫn `.env.*`). Thiếu nó thì
	@# pod-pgdump.sh die("POD_VOLUME trống") và target này in một câu trấn an SAI.
	@# Rỗng thì vô hại: pod-pgdump.sh cfg() thấy biến rỗng sẽ rơi xuống đọc `.env` của pod.
	@# PG_DUMP_KEEP cũng phải truyền, và chỉ khi CÓ giá trị. `.env.example` ở gốc repo quảng cáo
	@# núm này, nhưng pod-pgdump.sh đọc nó từ `.env` CỦA POD — mà `motions-studio/.env.example`
	@# bị rsync loại trừ nên không bao giờ tới pod. Không truyền = núm xoay chết.
	@# Dùng hàm `if` của Make (chỉ chèn khi có giá trị) để không ghi đè mặc định 20 bằng chuỗi
	@# rỗng. Chú ý: viết `$$(if …)` chứ đừng viết dạng trần trong comment — Make nở nó thật
	@# và chết ngay ở `make -n`, đúng bẫy mà M10 đã gặp với `$$0,99`.
	@ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
		-p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && POD_VOLUME='$(call env,POD_VOLUME)' \
		 $(if $(call env,PG_DUMP_KEEP),PG_DUMP_KEEP='$(call env,PG_DUMP_KEEP)') \
		 bash ./setup/pod-pgdump.sh --dump" \
		|| echo "!! sao lưu DB thất bại — vẫn dừng pod. DB còn trên container disk, chỉ mất nếu gpu-destroy."
ifeq ($(shell grep -E '^GPU_PROVIDER=' .env 2>/dev/null | cut -d= -f2),runpod)
	@runpodctl pod stop $(call env,GPU_INSTANCE_ID)
else
	@vastai stop instance $(call env,GPU_INSTANCE_ID)
endif
	@echo "GPU pod stopped — note: storage still bills hourly while the pod EXISTS (see docs/gpu-pod.md#costs)"

# `vastai destroy` asks "[y/N]" and answers itself with N on EOF — then exits 0. Piping y is not
# skipping a safety check: typing `make gpu-destroy` IS the confirmation. What matters is the
# verify below. Without it the target printed "GPU pod destroyed" over an aborted destroy, and you
# only found out from the invoice.
gpu-destroy: ## DEFAULT when done — destroy the pod (DB is restored from the volume next time, ~5 min)
	@test -n "$(call env,GPU_INSTANCE_ID)" || { echo "GPU_INSTANCE_ID is empty in .env — nothing to destroy"; exit 1; }
	@# Cố sao lưu lần cuối. KHÔNG nuốt stderr: pod-pgdump.sh báo lỗi nghiêm trọng qua die() ra
	@# stderr, và đây là ngay trước một thao tác không hoàn tác được. Nếu pod đã dừng thì ssh tự
	@# in lỗi kết nối — ồn hơn một chút, nhưng đó là tiếng ồn TRUNG THỰC. Nuốt hết rồi đoán
	@# "pod đã dừng?" là khẳng định một nguyên nhân ta không biết, ngay lúc người dùng cần biết nhất.
	@# POD_VOLUME / PG_DUMP_KEEP: xem chú thích ở gpu-down — không truyền POD_VOLUME thì đây là
	@# no-op trên pod đầu tiên, tức đúng lúc trước một thao tác KHÔNG HOÀN TÁC ĐƯỢC.
	@ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
		-p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && POD_VOLUME='$(call env,POD_VOLUME)' \
		 $(if $(call env,PG_DUMP_KEEP),PG_DUMP_KEEP='$(call env,PG_DUMP_KEEP)') \
		 bash ./setup/pod-pgdump.sh --dump" \
		|| echo "!! sao lưu lần cuối KHÔNG thành công (lý do ở ngay trên) — vẫn XOÁ pod theo yêu cầu."
ifeq ($(shell grep -E '^GPU_PROVIDER=' .env 2>/dev/null | cut -d= -f2),runpod)
	@runpodctl pod delete $(call env,GPU_INSTANCE_ID) || true
	@sleep 3
	@if runpodctl pod list -o json 2>/dev/null | grep -q '$(call env,GPU_INSTANCE_ID)'; then \
		echo "STILL ALIVE — pod $(call env,GPU_INSTANCE_ID) was NOT deleted and is STILL BILLING."; \
		echo "Delete it by hand: runpodctl pod delete $(call env,GPU_INSTANCE_ID)"; \
		exit 1; \
	else \
		echo "destroyed — verified gone from 'runpodctl pod list'"; \
		bash scripts/env-clear-pod.sh; \
		echo "NOTE: the Network Volume still exists and still bills monthly — that is deliberate."; \
	fi
else
	@printf 'y\n' | vastai destroy instance $(call env,GPU_INSTANCE_ID)
	@sleep 3
	@if vastai show instances 2>/dev/null | grep -q '\b$(call env,GPU_INSTANCE_ID)\b'; then \
		echo "STILL ALIVE — instance $(call env,GPU_INSTANCE_ID) was NOT destroyed and is STILL BILLING."; \
		echo "Destroy it by hand: vastai destroy instance $(call env,GPU_INSTANCE_ID)"; \
		exit 1; \
	else \
		echo "destroyed — verified gone from 'vastai show instances'"; \
		bash scripts/env-clear-pod.sh; \
	fi
endif

gpu-volume: ## Wire models/PGDATA/MinIO onto the Network Volume (idempotent; gpu-bootstrap does this too)
	@test -n "$(call env,POD_VOLUME)" || { echo "set POD_VOLUME in .env first (see docs/gpu-pod.md#network-volume)"; exit 1; }
	@ssh -o StrictHostKeyChecking=accept-new -p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && POD_VOLUME=$(call env,POD_VOLUME) MTC_PREBUILT=$(call env,MTC_PREBUILT) \
		 MODELS_MIN_GB=$(call env,MODELS_MIN_GB) bash ./setup/pod-volume.sh"

gpu-volume-adopt: ## ONE-TIME: move models/PGDATA/MinIO already on the pod ONTO the volume (keeps source as .bak)
	@test -n "$(call env,POD_VOLUME)" || { echo "set POD_VOLUME in .env first"; exit 1; }
	@echo "This stops nothing, copies data onto the volume, and renames the source to .bak-<timestamp>."
	@ssh -o StrictHostKeyChecking=accept-new -p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && POD_VOLUME=$(call env,POD_VOLUME) MTC_PREBUILT=$(call env,MTC_PREBUILT) \
		 MODELS_MIN_GB=$(call env,MODELS_MIN_GB) bash ./setup/pod-volume.sh --adopt"

gpu-volume-check: ## Prove the volume is really in use (catches "green but re-downloading 33GB")
	@test -n "$(call env,POD_VOLUME)" || { echo "set POD_VOLUME in .env first"; exit 1; }
	@ssh -o StrictHostKeyChecking=accept-new -p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && POD_VOLUME=$(call env,POD_VOLUME) \
		 MODELS_MIN_GB=$(call env,MODELS_MIN_GB) bash ./setup/pod-volume.sh --check"

gpu-db-dump: ## Sao lưu database sang Network Volume (pod phải đang chạy)
	@# POD_VOLUME / PG_DUMP_KEEP: xem chú thích ở gpu-down.
	@ssh -o StrictHostKeyChecking=accept-new -p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && POD_VOLUME='$(call env,POD_VOLUME)' \
		 $(if $(call env,PG_DUMP_KEEP),PG_DUMP_KEEP='$(call env,PG_DUMP_KEEP)') \
		 bash ./setup/pod-pgdump.sh --dump"

gpu-db-check: ## Bản dump mới nhất bao lâu rồi, có nạp lại được không (chạy --check + --verify)
	@# POD_VOLUME: xem chú thích ở gpu-down.
	@ssh -o StrictHostKeyChecking=accept-new -p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && POD_VOLUME='$(call env,POD_VOLUME)' bash ./setup/pod-pgdump.sh --check \
		 && POD_VOLUME='$(call env,POD_VOLUME)' bash ./setup/pod-pgdump.sh --verify"

gpu-smoke: ## Prove the pod really works end-to-end (SMOKE_REF=img SMOKE_DRIVER=vid for a motion job, +SMOKE_PRODUCT=img for tryon, SMOKE_PROMPT="..." for create-image)
	@bash scripts/pod-smoke.sh

gpu-status: ## Is the pod up, and is the backend answering?
	@curl -sf https://$(call env,DOMAIN)/health >/dev/null 2>&1 \
		&& echo "up   → https://$(call env,DOMAIN)" \
		|| echo "down → https://$(call env,DOMAIN)  (make gpu-up)"

gpu-logs: ## Tail PM2 logs on the pod (LOG=api|worker|comfyui|wf-worker|minio, default api)
	@ssh -p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) "pm2 logs $${LOG:-api} --lines 100 --nostream"
