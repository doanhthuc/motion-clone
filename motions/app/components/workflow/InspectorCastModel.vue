<!-- ALD 30/06/2026 - Inspector "Tuyển mẫu (kho)": khi CHỈ có ảnh sản phẩm, node cast-model tự bốc 1 người mẫu
     từ kho (model_refs, upload ở Settings) theo giới tính + độ tuổi, CỐ ĐỊNH (theo seed) dùng cho cả phim. -->
<template>
  <div class="space-y-3">
    <div>
      <label class="apl-label">Giới tính</label>
      <div class="apl-segmented mt-1.5">
        <button v-for="g in genders" :key="g.id" type="button"
          :class="['apl-seg-btn', local.gender === g.id ? 'is-active' : '']" @click="local.gender = g.id">
          <i :class="['bi mr-1', g.icon]" />{{ g.label }}
        </button>
      </div>
    </div>

    <div>
      <label class="apl-label">Độ tuổi</label>
      <div class="apl-segmented mt-1.5">
        <button v-for="a in ages" :key="a.id" type="button"
          :class="['apl-seg-btn', local.ageGroup === a.id ? 'is-active' : '']" @click="local.ageGroup = a.id">
          {{ a.label }}
        </button>
      </div>
    </div>

    <div>
      <label class="apl-label">Chốt mẫu (seed)</label>
      <input v-model.number="local.seed" type="number" min="0" class="apl-input mt-1.5"
        placeholder="0" >
      <p class="apl-card-hint">Cùng seed → cùng người mẫu (giữ continuity). Đổi seed để chọn mẫu khác trong kho.</p>
    </div>

    <div class="apl-card">
      <div class="apl-card-header"><i class="bi bi-people-fill mr-1" />Kho người mẫu</div>
      <p v-if="loading" class="apl-card-hint">Đang đếm mẫu phù hợp…</p>
      <p v-else-if="pool.length" class="apl-card-hint">{{ pool.length }} mẫu {{ genderLabel }} · {{ ageLabel }} đang bật. Sẽ chọn mẫu #{{ (Number(local.seed) || 0) % pool.length + 1 }}.</p>
      <p v-else class="apl-card-hint apl-warn">Chưa có mẫu {{ genderLabel }} · {{ ageLabel }} trong kho. Vào Settings → Người mẫu để tải lên (sẽ nới sang giới tính/bất kỳ nếu trống).</p>
      <div v-if="pool.length" class="apl-pool mt-2">
        <img v-for="m in pool.slice(0, 6)" :key="m.id" :src="m.url" alt="" class="apl-pool-thumb" >
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({ config: { type: Object, required: true } })
const emit = defineEmits(['update:config'])
const auth = useAuth()

const genders = [{ id: 'female', label: 'Nữ', icon: 'bi-gender-female' }, { id: 'male', label: 'Nam', icon: 'bi-gender-male' }]
const ages = [{ id: 'young', label: 'Trẻ' }, { id: 'middle', label: 'Trung niên' }, { id: 'old', label: 'Lớn tuổi' }]

const local = reactive({
  gender: props.config.gender || 'female',
  ageGroup: props.config.ageGroup || props.config.age_group || 'young',
  seed: Number(props.config.seed) || 0,
  label: props.config.label || 'Người mẫu (kho)',
})
watch(local, (v) => emit('update:config', { ...v }), { deep: true })

const genderLabel = computed(() => genders.find((g) => g.id === local.gender)?.label || local.gender)
const ageLabel = computed(() => ages.find((a) => a.id === local.ageGroup)?.label || local.ageGroup)

const pool = ref([])
const loading = ref(false)
async function loadPool() {
  loading.value = true
  try {
    const res = await auth.beFetch(`/model-refs?gender=${local.gender}&age_group=${local.ageGroup}&active=1`)
    pool.value = Array.isArray(res?.items) ? res.items : []
  } catch {
    pool.value = []
  } finally {
    loading.value = false
  }
}
watch(() => [local.gender, local.ageGroup], loadPool)
onMounted(loadPool)
</script>

<style scoped>
.apl-label { display: block; font-size: 11px; font-weight: 700; color: var(--apl-label); text-transform: uppercase; letter-spacing: 0.04em; }
.apl-segmented { display: inline-flex; width: 100%; background: rgba(118,118,128,0.12); border-radius: 9px; padding: 2px; }
.apl-seg-btn { flex: 1; padding: 6px 8px; background: transparent; border: none; border-radius: 7px; font-size: 11px; font-weight: 600; color: var(--apl-label); cursor: pointer; font-family: inherit; transition: all 0.18s cubic-bezier(0.32,0.72,0,1); }
.apl-seg-btn:hover { color: var(--apl-label); }
.apl-seg-btn.is-active { background: var(--apl-bg-secondary); color: var(--apl-label); box-shadow: 0 0.5px 1px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.08); }
.apl-input { width: 100%; padding: 8px 10px; border: 0.5px solid rgba(235,236,240,0.2); border-radius: 9px; font-size: 13px; font-family: inherit; background: var(--apl-bg-secondary); }
.apl-card { padding: 12px; background: rgba(118,118,128,0.06); border-radius: 10px; border: 0.5px solid rgba(235,236,240,0.12); }
.apl-card-header { font-size: 11px; font-weight: 700; color: var(--apl-label); text-transform: uppercase; letter-spacing: 0.04em; }
.apl-card-hint { margin-top: 4px; font-size: 11px; color: var(--apl-label); line-height: 1.45; }
.apl-warn { color: #B25000; }
.apl-pool { display: flex; gap: 6px; flex-wrap: wrap; }
.apl-pool-thumb { width: 40px; height: 40px; object-fit: cover; border-radius: 8px; border: 0.5px solid rgba(235,236,240,0.14); background: rgba(118,118,128,0.08); }
</style>
