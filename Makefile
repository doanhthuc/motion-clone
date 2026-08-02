.DEFAULT_GOAL := help
.PHONY: help setup dev down clean gpu-preflight gpu-provision gpu-wait gpu-bootstrap gpu-fe gpu-up gpu-down gpu-destroy gpu-status gpu-logs

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

gpu-preflight: ## Check root .env is complete BEFORE you spend money on a pod
	@bash scripts/gpu-preflight.sh

gpu-provision: ## Find + rent a GPU pod (dry-run; CONFIRM=yes to actually rent)
	@bash scripts/pod-provision.sh

gpu-wait: ## Wait for a freshly rented pod's SSH to come up (TIMEOUT=25 min); saves host/port to .env
	@bash scripts/pod-wait.sh

gpu-bootstrap: ## rsync motions-studio + run the SETUP_PROFILE setup script on the pod (idempotent; also deploys FE if FE_DOMAIN is set)
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

gpu-down: ## Stop the pod (DO NOT FORGET — an idle pod bills by the hour)
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
gpu-destroy: ## Permanently destroy the pod (frees the GPU, deletes its disk — irreversible)
	@test -n "$(call env,GPU_INSTANCE_ID)" || { echo "GPU_INSTANCE_ID is empty in .env — nothing to destroy"; exit 1; }
ifeq ($(shell grep -E '^GPU_PROVIDER=' .env 2>/dev/null | cut -d= -f2),runpod)
	@runpodctl pod delete $(call env,GPU_INSTANCE_ID) || true
	@sleep 3
	@if runpodctl pod list -o json 2>/dev/null | grep -q '$(call env,GPU_INSTANCE_ID)'; then \
		echo "STILL ALIVE — pod $(call env,GPU_INSTANCE_ID) was NOT deleted and is STILL BILLING."; \
		echo "Delete it by hand: runpodctl pod delete $(call env,GPU_INSTANCE_ID)"; \
		exit 1; \
	else \
		echo "destroyed — verified gone from 'runpodctl pod list'"; \
		echo "now clear GPU_INSTANCE_ID / GPU_SSH_HOST / GPU_SSH_PORT in .env"; \
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
		echo "now clear GPU_INSTANCE_ID / GPU_SSH_HOST / GPU_SSH_PORT in .env"; \
	fi
endif

gpu-volume: ## Wire models/PGDATA/MinIO onto the Network Volume (idempotent; gpu-bootstrap does this too)
	@test -n "$(call env,POD_VOLUME)" || { echo "set POD_VOLUME in .env first (see docs/gpu-pod.md#network-volume)"; exit 1; }
	@ssh -o StrictHostKeyChecking=accept-new -p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && POD_VOLUME=$(call env,POD_VOLUME) MTC_PREBUILT=$(call env,MTC_PREBUILT) \
		 MODELS_MIN_GB=$(call env,MODELS_MIN_GB) ./setup/pod-volume.sh"

gpu-volume-adopt: ## ONE-TIME: move models/PGDATA/MinIO already on the pod ONTO the volume (keeps source as .bak)
	@test -n "$(call env,POD_VOLUME)" || { echo "set POD_VOLUME in .env first"; exit 1; }
	@echo "This stops nothing, copies data onto the volume, and renames the source to .bak-<timestamp>."
	@ssh -o StrictHostKeyChecking=accept-new -p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && POD_VOLUME=$(call env,POD_VOLUME) MTC_PREBUILT=$(call env,MTC_PREBUILT) \
		 MODELS_MIN_GB=$(call env,MODELS_MIN_GB) ./setup/pod-volume.sh --adopt"

gpu-volume-check: ## Prove the volume is really in use (catches "green but re-downloading 33GB")
	@test -n "$(call env,POD_VOLUME)" || { echo "set POD_VOLUME in .env first"; exit 1; }
	@ssh -o StrictHostKeyChecking=accept-new -p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) \
		"cd ~/motion-backend && POD_VOLUME=$(call env,POD_VOLUME) \
		 MODELS_MIN_GB=$(call env,MODELS_MIN_GB) ./setup/pod-volume.sh --check"

gpu-smoke: ## Prove the pod really works end-to-end (SMOKE_REF=img SMOKE_DRIVER=vid for a real job)
	@bash scripts/pod-smoke.sh

gpu-status: ## Is the pod up, and is the backend answering?
	@curl -sf https://$(call env,DOMAIN)/health >/dev/null 2>&1 \
		&& echo "up   → https://$(call env,DOMAIN)" \
		|| echo "down → https://$(call env,DOMAIN)  (make gpu-up)"

gpu-logs: ## Tail PM2 logs on the pod (LOG=api|worker|comfyui|wf-worker|minio, default api)
	@ssh -p $(call env,GPU_SSH_PORT) root@$(call env,GPU_SSH_HOST) "pm2 logs $${LOG:-api} --lines 100 --nostream"
