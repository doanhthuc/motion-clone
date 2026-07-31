<template>
  <!-- #region ALD 22/05/2026 - Workflows list (embedded trong Settings)
       Click 1 workflow → navigate /workflows/[id] (editor full-screen).
       ALD 06/07/2026 - Liquid Glass light redesign (bỏ dark + amber, đồng bộ design system lq-*). -->
  <div class="space-y-3">
    <div class="flex items-center justify-between gap-2">
      <p class="text-xs text-gray-500">{{ wf.items.value.length }} workflow</p>
      <button
        type="button"
        class="lq-btn lq-btn--primary !rounded-full !h-9 !text-xs"
        @click="showCreate = true"
      >
        <i class="bi bi-plus-lg" />
        Tạo workflow
      </button>
    </div>

    <div v-if="wf.loading.value" class="text-center text-xs text-gray-400 py-8">
      <i class="bi bi-hourglass-split animate-pulse mr-1" /> Đang tải...
    </div>
    <div v-else-if="!wf.items.value.length" class="text-center py-12 lq-card !rounded-3xl">
      <i class="bi bi-diagram-3 text-5xl text-gray-300" />
      <p class="text-sm text-gray-500 mt-3">Chưa có workflow nào.</p>
      <p class="text-xs text-gray-400 mt-1">Tạo flow đầu tiên — vd <code>/price-matrix</code> = <code>/ocr</code> → <code>/chat</code>.</p>
    </div>
    <!-- ALD 24/05/2026 - Card v3 Apple polish: equal-height grid via min-h, large hero
         action row at footer, secondary actions on hover-revealed top-right menu. -->
    <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <article
        v-for="w in wf.items.value"
        :key="w.id"
        class="apl-wf-card"
        @click="navigateTo(`/workflows/${w.id}`)"
      >
        <!-- Hover-revealed action toolbar top-right -->
        <div class="apl-wf-tools" @click.stop>
          <button
            v-if="w.owned"
            type="button"
            class="apl-wf-tool"
            title="Sửa graph"
            @click="navigateTo(`/workflows/${w.id}`)"
          >
            <i class="bi bi-pencil" />
          </button>
          <button
            v-if="w.owned || isAdmin"
            type="button"
            class="apl-wf-tool"
            title="Lịch sử run"
            @click="navigateTo(`/workflows/${w.id}/runs`)"
          >
            <i class="bi bi-clock-history" />
          </button>
          <!-- ALD 29/06/2026 - admin xoá được MỌI workflow (kể cả của user khác), không chỉ chủ sở hữu -->
          <button
            v-if="w.owned || isAdmin"
            type="button"
            class="apl-wf-tool apl-wf-tool-danger"
            title="Xoá workflow"
            :disabled="deleting === w.id"
            @click="onDelete(w)"
          >
            <i :class="deleting === w.id ? 'bi bi-arrow-repeat apl-spin' : 'bi bi-trash'" />
          </button>
        </div>

        <!-- Header -->
        <div class="apl-wf-head">
          <div class="apl-wf-slug-row">
            <code class="apl-wf-slug">/{{ w.slug }}</code>
            <span v-if="w.is_public" class="apl-wf-badge apl-wf-badge-public">Public</span>
            <!-- ALD 29/06/2026 - admin thấy workflow của user khác → hiện chủ sở hữu để biết đang xoá của ai -->
            <span v-if="isAdmin && !w.owned" class="apl-wf-badge apl-wf-badge-owner" :title="w.owner_email || ''">
              <i class="bi bi-person-fill" />{{ w.owner_email || 'user khác' }}
            </span>
            <span v-if="!w.is_active" class="apl-wf-badge apl-wf-badge-disabled">Disabled</span>
          </div>
          <h3 class="apl-wf-title">{{ w.name }}</h3>
          <p class="apl-wf-desc">{{ w.description || '— Chưa có mô tả' }}</p>
        </div>

        <!-- Footer: hero action -->
        <button
          type="button"
          class="apl-wf-run"
          title="Mở editor + chạy workflow"
          @click.stop="navigateTo(`/workflows/${w.id}`)"
        >
          <i class="bi bi-play-fill" />
          <span>Chạy workflow</span>
          <i class="bi bi-arrow-right ms-auto" />
        </button>
      </article>
    </div>

    <!-- Create modal -->
    <Transition enter-active-class="transition duration-200" leave-active-class="transition duration-150" enter-from-class="opacity-0" leave-to-class="opacity-0">
      <div v-if="showCreate" class="fixed inset-0 z-50 lq-backdrop flex items-center justify-center p-4" @click.self="showCreate = false">
        <div class="lq-modal max-w-md w-full p-5 space-y-3">
          <h2 class="text-base font-semibold text-gray-900">Tạo workflow mới</h2>
          <div>
            <label class="lq-label">Slug (URL command)</label>
            <div class="mt-1 flex items-center gap-1.5">
              <span class="text-sm font-mono text-gray-400">/</span>
              <input v-model="newWf.slug" type="text" placeholder="price-matrix" class="lq-input flex-1 font-mono" @input="onSlugInput" @blur="normalizeSlug" />
            </div>
            <p class="lq-hint mt-1">a-z, 0-9, -. Dùng làm /command trong chat và API endpoint.</p>
          </div>
          <div>
            <label class="lq-label">Tên hiển thị</label>
            <input v-model="newWf.name" type="text" placeholder="Trích xuất bảng giá hợp đồng" class="lq-input mt-1" />
          </div>
          <div>
            <label class="lq-label">Mô tả (optional)</label>
            <textarea v-model="newWf.description" rows="2" class="lq-textarea mt-1" />
          </div>
          <p v-if="errorMsg" class="text-xs text-[#d70015]">{{ errorMsg }}</p>
          <div class="flex items-center justify-end gap-2 pt-1">
            <button type="button" class="lq-btn lq-btn--ghost" @click="showCreate = false">Huỷ</button>
            <button type="button" :disabled="creating || !newWf.slug || !newWf.name" class="lq-btn lq-btn--primary" @click="onCreate">
              <i :class="['bi', creating ? 'bi-hourglass-split animate-pulse' : 'bi-check2']" />
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
const wf = useWorkflows()
const auth = useAuth()
const toast = useToast()
const confirmDialog = useConfirm()
const config = useRuntimeConfig()

// ALD 29/06/2026 - Admin: hiện nút xoá/lịch sử trên MỌI workflow (kể cả của user khác) + badge chủ sở hữu.
const isAdmin = computed(() => decodeJwtPayload(auth.token.value)?.role === 'admin')

const showCreate = ref(false)
const creating = ref(false)
const errorMsg = ref('')
const newWf = reactive({ slug: '', name: '', description: '' })

// ALD 30/06/2026 - Lúc gõ chỉ hạ thường + đổi ký tự không hợp lệ (kể cả _ và space) thành '-'.
// KHÔNG cắt '-' đầu/cuối ở đây — trước đây cắt mỗi keystroke nên gõ '-' (đang ở cuối) bị xoá ngay → "không gõ được -".
function onSlugInput() {
  newWf.slug = newWf.slug.toLowerCase().replace(/[^a-z0-9-]/g, '-')
}
// Chuẩn hoá khi rời ô / trước khi tạo: gộp '-' liên tiếp + cắt '-' đầu/cuối (đúng SLUG_RE backend ^[a-z0-9][a-z0-9-]{1,49}$).
function normalizeSlug() {
  newWf.slug = newWf.slug.replace(/-+/g, '-').replace(/^-+|-+$/g, '')
}

function openCreate() {
  errorMsg.value = ''
  showCreate.value = true
}

onMounted(() => {
  wf.load()
  window.addEventListener('motions:wf:new', openCreate)
})

onBeforeUnmount(() => {
  window.removeEventListener('motions:wf:new', openCreate)
})

async function onCreate() {
  errorMsg.value = ''
  normalizeSlug()
  creating.value = true
  try {
    const created = await wf.create({ slug: newWf.slug, name: newWf.name, description: newWf.description })
    showCreate.value = false
    newWf.slug = ''; newWf.name = ''; newWf.description = ''
    toast.success(`Đã tạo /${created.slug}`)
    navigateTo(`/workflows/${created.id}`)
  } catch (err) {
    errorMsg.value = err.data?.error || err.message
  } finally {
    creating.value = false
  }
}

// ALD 24/06/2026 - Xoá workflow: thêm try/catch + toast lỗi + trạng thái đang-xoá.
// Trước đây thiếu try/catch → khi remove lỗi (mạng/quyền) thì im lặng, user tưởng "không xoá được".
const deleting = ref(null)
async function onDelete(w) {
  const ok = await confirmDialog.ask({
    title: `Xoá workflow /${w.slug}?`,
    message: 'Lịch sử run + endpoint API sẽ bị xoá. Hành động không hoàn tác.',
    confirmText: 'Xoá',
    variant: 'danger'
  })
  if (!ok) return
  deleting.value = w.id
  try {
    await wf.remove(w.id)
    toast.success(`Đã xoá /${w.slug}`)
  } catch (err) {
    toast.error(err?.data?.error || err?.message || 'Xoá thất bại')
  } finally {
    deleting.value = null
  }
}

function onCopyApi(w) {
  const url = `${config.public.motionBackendUrl}/functions/v1/workflows/${w.slug}/invoke`
  copyText(url).then(
    () => toast.success('Đã copy API URL'),
    () => toast.error('Copy failed')
  )
}
</script>

<style scoped>
/* ALD 24/06/2026 - spinner nút xoá khi đang xoá workflow */
.apl-spin { display: inline-block; animation: apl-spin 0.9s linear infinite; }
@keyframes apl-spin { to { transform: rotate(360deg); } }
.apl-wf-tool:disabled { opacity: 0.6; cursor: default; }
/* ALD 06/07/2026 - Workflow card, Liquid Glass light (bỏ dark + amber) */
.apl-wf-card {
  position: relative;
  background: var(--glass-bg);
  backdrop-filter: blur(20px) saturate(180%);
  -webkit-backdrop-filter: blur(20px) saturate(180%);
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 18px 18px 14px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-height: 180px;
  box-shadow: var(--glass-edge), 0 1px 2px rgba(0,0,0,0.03);
  transition: transform 0.18s cubic-bezier(0.32, 0.72, 0, 1),
              box-shadow 0.18s cubic-bezier(0.32, 0.72, 0, 1),
              border-color 0.18s cubic-bezier(0.32, 0.72, 0, 1);
}
.apl-wf-card:hover {
  transform: translateY(-3px);
  border-color: var(--line-2);
  box-shadow: var(--glass-edge), var(--shadow-card-hover);
}
.apl-wf-card:active { transform: translateY(-1px); }

/* Tools toolbar top-right */
.apl-wf-tools {
  position: absolute;
  top: 12px;
  right: 12px;
  display: flex;
  gap: 2px;
  opacity: 0;
  transform: translateY(-4px);
  transition: opacity 0.18s, transform 0.18s;
  background: var(--glass-bg-solid);
  padding: 2px;
  border-radius: 10px;
  border: 1px solid var(--line);
  box-shadow: 0 4px 14px rgba(0,0,0,0.08);
  backdrop-filter: blur(10px);
}
.apl-wf-card:hover .apl-wf-tools { opacity: 1; transform: translateY(0); }
.apl-wf-tool {
  width: 28px; height: 28px;
  display: inline-flex; align-items: center; justify-content: center;
  background: transparent; border: none;
  border-radius: 7px;
  color: var(--ink-3);
  cursor: pointer;
  font-size: 12px;
  transition: all 0.14s;
}
.apl-wf-tool:hover {
  background: rgba(94, 106, 210, 0.08);
  color: var(--primary);
}
.apl-wf-tool-danger:hover { background: var(--color-danger-light); color: #d70015; }

/* Header */
.apl-wf-head {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding-right: 100px; /* leave space for hover toolbar */
}
.apl-wf-slug-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.apl-wf-slug {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12.5px;
  font-weight: 700;
  color: var(--primary);
  letter-spacing: -0.01em;
}
.apl-wf-badge {
  font-size: 9.5px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 2px 6px;
  border-radius: 999px;
}
.apl-wf-badge-public  { background: var(--color-info-light); color: #9AA2F2; }
.apl-wf-badge-disabled { background: var(--color-gray-100); color: var(--ink-3); }
/* ALD 29/06/2026 - badge chủ sở hữu (chỉ admin thấy, trên workflow của user khác) */
.apl-wf-badge-owner { display: inline-flex; align-items: center; gap: 3px; max-width: 180px; background: var(--color-info-light); color: #9AA2F2; text-transform: none; letter-spacing: 0; }
.apl-wf-badge-owner > span, .apl-wf-badge-owner { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.apl-wf-badge-owner i { font-size: 9px; opacity: 0.8; }

.apl-wf-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--ink);
  letter-spacing: -0.022em;
  line-height: 1.2;
  margin: 0;
}
.apl-wf-desc {
  font-size: 12.5px;
  font-weight: 400;
  color: var(--ink-2);
  line-height: 1.45;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Hero run button */
.apl-wf-run {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 42px;
  padding: 0 16px;
  border: none;
  border-radius: 12px;
  background: var(--primary);
  color: #fff;
  font-size: 13.5px;
  font-weight: 600;
  letter-spacing: -0.01em;
  cursor: pointer;
  transition: all 0.18s cubic-bezier(0.32, 0.72, 0, 1);
}
.apl-wf-run i.bi-play-fill { font-size: 16px; }
.apl-wf-run i.bi-arrow-right { font-size: 13px; opacity: 0.75; transition: transform 0.18s; }
.apl-wf-run:hover { background: #6B76E5; }
.apl-wf-run:hover i.bi-arrow-right { transform: translateX(3px); opacity: 1; }
.apl-wf-run:active { transform: scale(0.985); }
</style>
