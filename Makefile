.DEFAULT_GOAL := help
.PHONY: gpu-facelock batch-mcp-check batch-coverage check-comfy-nodes help setup dev down clean gpu-preflight gpu-provision gpu-wait gpu-bootstrap gpu-fe gpu-up gpu-down gpu-destroy gpu-db-dump gpu-db-check gpu-status gpu-logs batch-test batch-params check-batch-params check-vast-models batch-scan batch-validate batch batch-clean watchdog-dry drain bot-dry api-smoke ios-test ios-audio-test ios-secrets ios-contract ios-gen ios-build ios-ui-test

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

# The provider of THIS run, in this order:
#   1. an exported/command-line GPU_PROVIDER (drain.py exports it for a Vast run, so the root
#      .env can keep saying runpod);
#   2. else the provider recorded in the lease (batch/pod-lease.json) — but only when the
#      lease's pod id is the instance id this invocation is about to destroy;
#   3. else the provider recorded in .env's GPU_INSTANCE_OWNER marker ("vast:<id>", written by
#      vast_rent.py the moment it creates an instance) — but only when the marker's id is the
#      instance id this invocation is about to destroy;
#   4. else .env's GPU_PROVIDER.
# Why the lease: a hand-typed `make gpu-destroy` after a Vast run has GPU_PROVIDER unset, so .env's
# runpod won — `runpodctl pod delete <vast id> || true`, a RunPod re-list that finds nothing, a
# "destroyed — verified" line, and .env wiped over a Vast instance that keeps billing.
# Why the owner marker: the lease is only written once a rent SUCCEEDS (batchlib_ext.lease.write_lease
# runs after `make gpu-wait`). A rent that fails AFTER creating an instance (STILL BILLING abandon,
# AmbiguousCreate, an unwind that could not destroy) leaves GPU_INSTANCE_ID set with no lease at
# all — .env's runpod would win, same failure as above, and the operator's only handle on the
# instance (its id) would be wiped by the "verified gone" RunPod branch. The marker is the id's own
# receipt of who rented it, so it survives exactly the case the lease cannot cover.
# Why the id match, in both cases: a stale lease or marker a crash left for some OTHER pod must
# never steer the destroy of the pod .env names. A lease with no "provider" key (written before
# providers existed) yields an empty provider and falls through; an owner marker with no colon or
# an empty id half is ignored the same way.
# Plain sed, not python: these lines run on every make invocation. write_lease uses
# json.dumps(indent=2), so "pod_id" and "provider" each sit on their own line.
LEASE_FILE ?= batch/pod-lease.json
LEASE_POD := $(shell sed -n 's/.*"pod_id": *"\([^"]*\)".*/\1/p' $(LEASE_FILE) 2>/dev/null)
LEASE_PROVIDER := $(shell sed -n 's/.*"provider": *"\([^"]*\)".*/\1/p' $(LEASE_FILE) 2>/dev/null)
CURRENT_INSTANCE_ID := $(or $(GPU_INSTANCE_ID),$(call env,GPU_INSTANCE_ID))
OWNER_RAW := $(call env,GPU_INSTANCE_OWNER)
OWNER_PROVIDER := $(word 1,$(subst :, ,$(OWNER_RAW)))
OWNER_ID := $(word 2,$(subst :, ,$(OWNER_RAW)))
GPU_PROVIDER_EFF := $(or $(GPU_PROVIDER),$(if $(and $(LEASE_POD),$(filter $(LEASE_POD),$(CURRENT_INSTANCE_ID))),$(LEASE_PROVIDER)),$(if $(and $(OWNER_ID),$(filter $(OWNER_ID),$(CURRENT_INSTANCE_ID))),$(OWNER_PROVIDER)),$(call env,GPU_PROVIDER))
# A Network Volume is RunPod-only — a Vast box has none, whatever .env says.
POD_VOLUME_EFF := $(if $(filter runpod,$(GPU_PROVIDER_EFF)),$(call env,POD_VOLUME))

scrub-check: ## Gate: fail if any third-party credential or personal email is tracked
	@bash motions-studio/setup/scrub-secrets.sh --check

check-job-types: ## Gate: job type lists (image, setup profiles, dispatcher) must agree
	@node scripts/check-job-types.mjs

check-comfy-nodes: ## Gate: bốn danh sách custom node ComfyUI (2 image + 2 setup profile) phải khớp
	@node scripts/check-comfy-nodes.mjs

batch-test: ## Gate: unit test của batch runner (không cần pod, không tốn tiền)
	@python3 -m unittest discover -s scripts/tests -p 'test_batch_*.py'

batch-coverage: ## Dòng nào của batch runner KHÔNG test nào chạm tới (FULL=1 để xem hết)
	@python3 scripts/batch_coverage.py $${FULL:+--full}

batch-params: ## Liệt kê param một job type nhận (TYPE=motion|tryon|enhance)
	@python3 scripts/batch_params.py $${TYPE:-}

check-batch-params: ## Gate: scripts/batch-params.json phải khớp linux.py
	@python3 scripts/batch_params.py --check

check-vast-models: ## Gate: every batch-manifest stage has a Vast model-registry entry, ids match the catalog
	@python3 scripts/check_vast_models.py

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

batch-mcp-check: ## Bắt tay thật với MCP server rồi in 4 tool nó khai (không tiêu GPU)
	@printf '%s\n' \
	  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
	  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
	  | python3 scripts/batch_mcp.py \
	  | python3 -c 'import json,sys; [print("  ✓", t["name"]) for l in sys.stdin if (d:=json.loads(l)).get("id")==2 for t in d["result"]["tools"]]'

watchdog-dry: ## Report what the watchdog would destroy right now — destroys nothing
	@python3 scripts/pod_watchdog.py --once --dry-run

bot-dry: ## One polling round against the local Bot API, invoking no jobs
	@python3 scripts/tgbot/bot.py --once --dry-run

api-smoke: ## Phone API through the tunnel: 403 without Access, 401 without bearer, 200 with both
	@bash scripts/vps/api-smoke.sh

drain: ## Rent a pod, run FILE, destroy it (dry run unless CONFIRM=yes; PROVIDER=vast|runpod overrides .env for this run; PHASE_A=1 stops before renting)
	@test -n "$(FILE)" || { echo "usage: make drain FILE=batch/….yaml [CONFIRM=yes] [RESUME=1] [FORCE_LOCAL=1] [PHASE_A=1] [PROVIDER=vast|runpod]"; exit 1; }
	@python3 scripts/drain.py --file "$(FILE)" \
		$(if $(filter yes,$(CONFIRM)),--yes) $(if $(RESUME),--resume) \
		$(if $(FORCE_LOCAL),--force-local) $(if $(PHASE_A),--phase-a-only) \
		$(if $(PROVIDER),--provider $(PROVIDER))

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
ifeq ($(GPU_PROVIDER_EFF),runpod)
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
		"cd ~/motion-backend && POD_VOLUME='$(POD_VOLUME_EFF)' \
		 $(if $(call env,PG_DUMP_KEEP),PG_DUMP_KEEP='$(call env,PG_DUMP_KEEP)') \
		 bash ./setup/pod-pgdump.sh --dump" \
		|| echo "!! sao lưu DB thất bại — vẫn dừng pod. DB còn trên container disk, chỉ mất nếu gpu-destroy."
ifeq ($(GPU_PROVIDER_EFF),runpod)
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
ifeq ($(GPU_PROVIDER_EFF),runpod)
	@ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 \
		-p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && POD_VOLUME='$(POD_VOLUME_EFF)' \
		 $(if $(call env,PG_DUMP_KEEP),PG_DUMP_KEEP='$(call env,PG_DUMP_KEEP)') \
		 bash ./setup/pod-pgdump.sh --dump" \
		|| echo "!! sao lưu lần cuối KHÔNG thành công (lý do ở ngay trên) — vẫn XOÁ pod theo yêu cầu."
endif
ifeq ($(GPU_PROVIDER_EFF),runpod)
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
	@# `|| true`: an already-gone instance makes this exit non-zero (RunPod's branch already
	@# has the same guard on `pod delete`). The verify below IS the check — a destroy error
	@# here must not abort make before it runs and .env never gets cleared over an instance
	@# that is, in fact, already gone (F3/I1, 2026-09-19).
	@printf 'y\n' | vastai destroy instance $(call env,GPU_INSTANCE_ID) || true
	@sleep 3
	@# Verify against the same listing VastCtl.list_pods reads (instances-v1, --all because the
	@# default page hides instances). If the listing itself fails we know nothing: say so and keep
	@# .env, whose id is the only handle for destroying it by hand. The id is matched as a JSON
	@# value so 12345 does not match 123456. Two more ways to know nothing: output that exits 0 but
	@# is not a listing (an auth error has no instance id either, and read as "gone"; the real shape,
	@# checked 2026-09-19, is {"instances": [...], "next_token": ..., "success": true}), and an id
	@# with trailing whitespace in .env, which never matched a row (the id is stripped before use).
	@listing="$$(vastai show instances-v1 --raw --all 2>&1)"; rc=$$?; \
	case "$$listing" in *'"instances"'*) shape=ok;; *) shape=bad;; esac; \
	if [ $$rc -ne 0 ] || [ "$$shape" != ok ]; then \
		echo "COULD NOT VERIFY — instance $(strip $(call env,GPU_INSTANCE_ID)) may still be billing."; \
		echo "$$listing"; \
		echo "Check by hand: vastai show instances-v1 --raw --all (then vastai destroy instance $(strip $(call env,GPU_INSTANCE_ID)))"; \
		exit 1; \
	elif printf '%s\n' "$$listing" | grep -Eq '"id": *$(strip $(call env,GPU_INSTANCE_ID))([^0-9]|$$)'; then \
		echo "STILL ALIVE — instance $(strip $(call env,GPU_INSTANCE_ID)) was NOT destroyed and is STILL BILLING."; \
		echo "Destroy it by hand: vastai destroy instance $(strip $(call env,GPU_INSTANCE_ID))"; \
		exit 1; \
	else \
		echo "destroyed — verified gone from 'vastai show instances-v1'"; \
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

gpu-facelock: ## Install faceLock on the pod (insightface swap; needed before faceLock=1 does anything)
	@bash scripts/pod-facelock.sh

gpu-status: ## Is the pod up, and is the backend answering?
	@curl -sf https://$(call env,DOMAIN)/health >/dev/null 2>&1 \
		&& echo "up   → https://$(call env,DOMAIN)" \
		|| echo "down → https://$(call env,DOMAIN)  (make gpu-up)"

gpu-logs: ## Tail PM2 logs on the pod (LOG=api|worker|comfyui|wf-worker|minio, default api)
	@ssh -p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) "pm2 logs $${LOG:-api} --lines 100 --nostream"

ios-test: ## iPhone app logic tests (swift test on the Mac, no simulator)
	cd ios/MotionKit && swift test

ios-audio-test: ## Verify media audio ignores Silent Mode (requires a booted iOS simulator)
	@mkdir -p ios/.build-sim
	@simulator_sdk="$$(xcrun --sdk iphonesimulator --show-sdk-path)"; \
		xcrun --sdk iphonesimulator swiftc -parse-as-library -sdk "$$simulator_sdk" \
		-target arm64-apple-ios26.0-simulator \
		ios/MotionApp/PlaybackAudioSession.swift \
		ios/MotionAppTests/PlaybackAudioSessionTestMain.swift \
		-o ios/.build-sim/motion-playback-audio-test; \
		xcrun simctl spawn booted "$$(pwd)/ios/.build-sim/motion-playback-audio-test"

ios-secrets: ## Write ios/Secrets.xcconfig (gitignored) from the root .env
	@bash scripts/ios-secrets.sh

ios-contract: ## Decode the live phone API with the app's models (GET only, spends nothing)
	cd ios/MotionKit && swift run -q motion-contract ../../.env

ios-gen: ## Generate ios/MotionApp.xcodeproj from ios/project.yml (needs ios/Secrets.xcconfig)
	@[ -f ios/Secrets.xcconfig ] || bash scripts/ios-secrets.sh
	cd ios && xcodegen generate --quiet

ios-build: ios-gen ## Compile the app for the iOS simulator (automatic ad-hoc signing)
	xcodebuild -project ios/MotionApp.xcodeproj -scheme MotionApp \
	  -destination 'generic/platform=iOS Simulator' -quiet build

ios-ui-test: ios-gen ## Boot an iPhone Simulator and run the live Phase 3 UI smoke
	@bash scripts/ios-ui-test.sh
