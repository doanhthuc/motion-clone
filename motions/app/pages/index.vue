<template>
  <!-- #region ALD 29/06/2026 - Trang chủ redesign theo phong cách minimal (Vercel/Geist):
       bề mặt phẳng near-neutral, viền hairline, mực gần đen, nút primary đen đặc, chuyển động ~150ms.
       Giữ ràng buộc cũ: 1 màn hình, lưới auto-fill lấp đầy, KHÔNG scroll. -->
  <div class="gx">
    <!-- Header: eyebrow + tiêu đề · dải thống kê · hành động -->
    <header class="gx-header">
      <div class="gx-titles">
        <p class="gx-eyebrow">Motions · AI Studio</p>
        <h1 class="gx-title">Workflow Pipeline</h1>
      </div>

      <div class="gx-stats">
        <div v-for="s in stats" :key="s.label" class="gx-stat">
          <span class="gx-stat-value">
            <span v-if="s.dot" :class="['gx-dot', s.ok ? 'is-ok' : 'is-off']" />
            {{ s.value }}
          </span>
          <span class="gx-stat-label">{{ s.label }}</span>
        </div>
      </div>

      <div class="gx-actions">
        <button type="button" class="gx-btn gx-btn-primary" @click="openCreate">
          <i class="bi bi-plus-lg" />
          Workflow mới
        </button>
        <NuxtLink to="/workflows" class="gx-btn gx-btn-secondary">
          Editor
          <i class="bi bi-arrow-right" />
        </NuxtLink>
      </div>
    </header>

    <!-- Lưới workflow -->
    <section class="gx-section">
      <div v-if="workflows.loading.value" class="gx-state">
        <i class="bi bi-arrow-repeat gx-spin" />
      </div>

      <div v-else-if="!workflows.items.value.length" class="gx-state gx-empty">
        <i class="bi bi-diagram-3 gx-empty-icon" />
        <p class="gx-empty-title">Chưa có workflow</p>
        <p class="gx-empty-sub">Tạo workflow đầu tiên để bắt đầu pipeline.</p>
        <button type="button" class="gx-btn gx-btn-primary gx-empty-btn" @click="openCreate">
          <i class="bi bi-plus-lg" />
          Tạo workflow
        </button>
      </div>

      <div v-else class="gx-grid p-2">
        <NuxtLink
          v-for="wf in workflows.items.value"
          :key="wf.id"
          :to="`/workflows/${wf.id}`"
          class="gx-card"
        >
          <div class="gx-card-head">
            <span class="gx-card-icon"><i class="bi bi-diagram-3" /></span>
            <div class="gx-card-id">
              <div class="gx-card-name">{{ wf.name || wf.slug }}</div>
              <code class="gx-card-slug">/{{ wf.slug }}</code>
            </div>
            <i class="bi bi-arrow-right gx-card-arrow" />
          </div>
          <p v-if="wf.description" class="gx-card-desc">{{ wf.description }}</p>
        </NuxtLink>

        <!-- Thẻ tạo mới — luôn ở cuối -->
        <button type="button" class="gx-card-new" @click="openCreate">
          <i class="bi bi-plus-lg" />
          <span>Workflow mới</span>
        </button>
      </div>
    </section>

    <!-- Modal tạo workflow -->
    <Transition enter-active-class="gx-fade-in" leave-active-class="gx-fade-out">
      <div v-if="showCreate" class="gx-modal-backdrop" @click.self="closeCreate">
        <div class="gx-modal">
          <h2>Tạo workflow mới</h2>

          <div class="gx-field">
            <label class="gx-label">Slug (URL command)</label>
            <div class="gx-input-prefix">
              <span class="gx-prefix">/</span>
              <input
                v-model="newWf.slug"
                type="text"
                placeholder="motion-demo"
                class="gx-input gx-input-mono"
                @input="onSlugInput"
                @blur="normalizeSlug"
              >
            </div>
            <p class="gx-hint">a-z, 0-9, -. Dùng làm URL / API endpoint.</p>
          </div>

          <div class="gx-field">
            <label class="gx-label">Tên hiển thị</label>
            <input v-model="newWf.name" type="text" placeholder="Motion workflow" class="gx-input">
          </div>

          <div class="gx-field">
            <label class="gx-label">Mô tả (tuỳ chọn)</label>
            <textarea v-model="newWf.description" rows="2" class="gx-input gx-textarea" />
          </div>

          <p v-if="errorMsg" class="gx-error">{{ errorMsg }}</p>

          <div class="gx-modal-actions">
            <button type="button" class="gx-btn gx-btn-ghost" @click="closeCreate">Huỷ</button>
            <button
              type="button"
              class="gx-btn gx-btn-primary"
              :disabled="creating || !newWf.slug || !newWf.name"
              @click="onCreate"
            >
              <i :class="['bi', creating ? 'bi-arrow-repeat gx-spin' : 'bi-check-lg']" />
              Tạo
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </div>
  <!-- #endregion -->
</template>

<script setup>
definePageMeta({ middleware: 'auth' })

useHead({ title: 'Tổng quan — Motions' })

const workflows = useWorkflows()
const gpu = useMotionWorkerStatus()
const audioLib = useAudioFiles()
const activeJobs = useActiveJobs()
const toast = useToast()

const showCreate = ref(false)
const creating = ref(false)
const errorMsg = ref('')
const newWf = reactive({ slug: '', name: '', description: '' })

onMounted(async () => {
  gpu.start()   // ALD 02/06/2026 - poll /worker-status (trước đây quên start → badge luôn "Offline")
  try { await workflows.load() } catch { /* offline OK */ }
  try { await audioLib.load() } catch { /* offline OK */ }
  activeJobs.start(10000)
})
onBeforeUnmount(() => { gpu.stop(); activeJobs.stop() })

function openCreate() {
  errorMsg.value = ''
  showCreate.value = true
}

function closeCreate() {
  if (creating.value) return
  showCreate.value = false
}

// ALD 30/06/2026 - Lúc gõ chỉ hạ thường + đổi ký tự không hợp lệ (kể cả _ và space) thành '-'.
// KHÔNG cắt '-' đầu/cuối ở đây — trước đây cắt mỗi keystroke nên gõ '-' (đang ở cuối) bị xoá ngay → "không gõ được -".
function onSlugInput() {
  newWf.slug = newWf.slug.toLowerCase().replace(/[^a-z0-9-]/g, '-')
}
// Chuẩn hoá khi rời ô / trước khi tạo: gộp '-' liên tiếp + cắt '-' đầu/cuối (đúng SLUG_RE backend ^[a-z0-9][a-z0-9-]{1,49}$).
function normalizeSlug() {
  newWf.slug = newWf.slug.replace(/-+/g, '-').replace(/^-+|-+$/g, '')
}

async function onCreate() {
  errorMsg.value = ''
  normalizeSlug()
  creating.value = true
  try {
    const created = await workflows.create({ slug: newWf.slug, name: newWf.name, description: newWf.description })
    showCreate.value = false
    newWf.slug = ''; newWf.name = ''; newWf.description = ''
    toast.success(`Đã tạo /${created.slug}`)
    await navigateTo(`/workflows/${created.id}`)
  } catch (err) {
    errorMsg.value = err?.data?.error || err?.message || 'Tạo workflow thất bại'
  } finally {
    creating.value = false
  }
}

// ALD 29/06/2026 - Stats tối giản: số near-black + nhãn caps nhỏ + chấm trạng thái (GPU / đang chạy).
const stats = computed(() => [
  { label: 'Workflow', value: workflows.items.value.length },
  { label: 'Đang chạy', value: activeJobs.items.value.length, dot: true, ok: activeJobs.items.value.length > 0 },
  { label: 'Audio', value: audioLib.total.value || audioLib.items.value.length },
  { label: 'GPU', value: gpu.status.value.healthy ? 'Ready' : 'Off', dot: true, ok: gpu.status.value.healthy }
])
</script>

<style scoped>
/* ── ALD 06/07/2026 - Liquid Glass tokens (theo design system global main.css) ── */
.gx {
  --accent: var(--primary);

  flex: 1;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 20px 24px;
}

/* ── Header ───────────────────────────────────────────────────────────────── */
.gx-header {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
}
.gx-titles { flex: 1 1 auto; min-width: 0; }
.gx-eyebrow {
  margin: 0;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-3);
}
.gx-title {
  margin: 4px 0 0;
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.025em;
  line-height: 1.1;
  color: var(--ink);
}

/* ── Stats strip — glass ──────────────────────────────────────────────────── */
.gx-stats {
  display: flex;
  align-items: stretch;
  background: var(--glass-bg);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: var(--glass-edge), 0 1px 2px rgba(0,0,0,0.03);
  overflow: hidden;
}
.gx-stat {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 3px;
  padding: 8px 18px;
  min-width: 78px;
  border-left: 1px solid var(--line);
}
.gx-stat:first-child { border-left: 0; }
.gx-stat-value {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--ink);
  font-variant-numeric: tabular-nums;
  line-height: 1;
}
.gx-stat-label {
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--ink-3);
  line-height: 1;
}
.gx-dot {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: #d4d4d4;
  flex-shrink: 0;
}
.gx-dot.is-ok { background: #248a3d; box-shadow: 0 0 0 3px rgba(36, 138, 61, 0.14); }
.gx-dot.is-off { background: #34343B; }

/* ── Buttons — Apple pill ─────────────────────────────────────────────────── */
.gx-actions { display: flex; gap: 8px; flex-shrink: 0; }
.gx-btn {
  height: 36px;
  padding: 0 16px;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  border-radius: 999px;
  font-size: 13.5px;
  font-weight: 500;
  letter-spacing: -0.01em;
  white-space: nowrap;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease, transform 0.1s ease;
}
.gx-btn:active { transform: scale(0.98); }
.gx-btn-primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.gx-btn-primary:hover { background: #6B76E5; border-color: #6B76E5; }
.gx-btn-primary:disabled { background: #26262C; border-color: #26262C; color: #62666D; cursor: default; transform: none; }
.gx-btn-secondary { background: var(--surface); color: var(--ink); border-color: var(--line-2); }
.gx-btn-secondary:hover { background: rgba(255,255,255,0.08); border-color: rgba(0, 0, 0, 0.24); }
.gx-btn-ghost { background: transparent; color: var(--ink-2); }
.gx-btn-ghost:hover { background: rgba(255,255,255,0.07); color: var(--ink); }

/* ── Section / grid ───────────────────────────────────────────────────────── */
.gx-section { flex: 1; min-height: 0; overflow: hidden; }
.gx-grid {
  height: 100%;
  display: grid;
  gap: 12px;
  align-content: start;
  grid-template-columns: repeat(auto-fill, minmax(248px, 1fr));
  grid-auto-rows: minmax(132px, max-content);
  overflow: hidden;
}

/* ── Workflow card — glass ────────────────────────────────────────────────── */
.gx-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
  min-height: 0;
  overflow: hidden;
  background: var(--glass-bg);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid var(--line);
  border-radius: 16px;
  box-shadow: var(--glass-edge), 0 1px 2px rgba(0,0,0,0.03);
  transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s var(--spring);
}
.gx-card:hover {
  border-color: var(--line-2);
  box-shadow: var(--glass-edge), var(--shadow-card-hover);
  transform: translateY(-2px);
}
.gx-card:active { transform: scale(0.99); }
.gx-card-head { display: flex; align-items: center; gap: 12px; }
.gx-card-icon {
  width: 38px;
  height: 38px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 11px;
  background: var(--color-primary-50, #f0f7ff);
  border: 1px solid rgba(94, 106, 210, 0.14);
  color: var(--accent);
  font-size: 16px;
}
.gx-card-id { min-width: 0; flex: 1; }
.gx-card-name {
  font-size: 14px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.gx-card-slug {
  display: block;
  margin-top: 2px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: var(--ink-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.gx-card-arrow {
  flex-shrink: 0;
  font-size: 13px;
  color: #3A3A42;
  transition: color 0.15s ease, transform 0.15s ease;
}
.gx-card:hover .gx-card-arrow { color: var(--accent); transform: translateX(2px); }
.gx-card-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ink-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* ── Thẻ tạo mới ──────────────────────────────────────────────────────────── */
.gx-card-new {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 0;
  border: 1px dashed var(--line-2);
  border-radius: 12px;
  background: transparent;
  color: var(--ink-3);
  cursor: pointer;
  transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
}
.gx-card-new { border-radius: 16px; }
.gx-card-new:hover { border-color: var(--accent); color: var(--accent); background: rgba(94, 106, 210, 0.03); }
.gx-card-new i { font-size: 18px; }
.gx-card-new span { font-size: 12px; font-weight: 500; }

/* ── States ───────────────────────────────────────────────────────────────── */
.gx-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--ink-3);
}
.gx-empty-icon { font-size: 30px; color: #3A3A42; }
.gx-empty-title { margin: 14px 0 0; font-size: 14px; font-weight: 600; color: var(--ink); }
.gx-empty-sub { margin: 4px 0 0; font-size: 12.5px; color: var(--ink-3); }
.gx-empty-btn { margin-top: 18px; }
.gx-spin { display: inline-block; animation: gx-spin 0.8s linear infinite; }
@keyframes gx-spin { to { transform: rotate(360deg); } }

/* ── Modal ────────────────────────────────────────────────────────────────── */
.gx-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  background: rgba(20, 20, 22, 0.32);
  backdrop-filter: blur(6px) saturate(140%);
  -webkit-backdrop-filter: blur(6px) saturate(140%);
}
.gx-modal {
  width: 100%;
  max-width: 420px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 20px;
  background: var(--glass-bg-solid);
  backdrop-filter: blur(28px) saturate(180%);
  -webkit-backdrop-filter: blur(28px) saturate(180%);
  border: 1px solid var(--line);
  border-radius: 18px;
  box-shadow: var(--glass-edge), var(--shadow-modal);
}
.gx-modal h2 { margin: 0; font-size: 16px; font-weight: 600; letter-spacing: -0.01em; color: var(--ink); }
.gx-field { display: flex; flex-direction: column; gap: 6px; }
.gx-label {
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--ink-2);
}
.gx-input {
  height: 36px;
  padding: 0 11px;
  border-radius: 10px;
  border: 1px solid var(--line-2);
  background: var(--surface);
  color: var(--ink);
  font-size: 13.5px;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.gx-input::placeholder { color: var(--ink-3); }
.gx-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3.5px rgba(94, 106, 210, 0.14); }
.gx-input-mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.gx-textarea { height: auto; padding: 9px 11px; resize: vertical; line-height: 1.5; }
.gx-input-prefix { display: flex; align-items: center; gap: 6px; }
.gx-prefix { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13.5px; color: var(--ink-3); }
.gx-input-prefix .gx-input { flex: 1; }
.gx-hint { margin: 0; font-size: 11px; color: var(--ink-3); }
.gx-error { margin: 0; font-size: 12.5px; color: var(--color-danger, #d70015); }
.gx-modal-actions { display: flex; justify-content: flex-end; gap: 8px; padding-top: 2px; }

/* ── Motion (Geist: nhanh, tinh tế) ───────────────────────────────────────── */
.gx-fade-in { animation: gx-fade 0.15s ease; }
.gx-fade-out { animation: gx-fade 0.12s ease reverse; }
@keyframes gx-fade { from { opacity: 0; } to { opacity: 1; } }

@media (prefers-reduced-motion: reduce) {
  .gx-btn, .gx-card, .gx-card-arrow, .gx-card-new, .gx-input { transition: none; }
  .gx-spin { animation: none; }
  .gx-fade-in, .gx-fade-out { animation: none; }
}
</style>
