<template>
  <!-- ALD 08/07/2026 - Node "Đè lộ": 2 video CÙNG người/động tác (khác bộ đồ). Keys khớp worker run_reveal.
       ALD 09/07 - mode SLIDER (mặc định): đường LINE CỨNG quét nhanh qua GIỮA clip = thanh trượt so sánh before/after. -->
  <div class="space-y-4">
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5"><i class="bi bi-layers-half" /> Đè lộ</p>
      <p class="text-[11px] opacity-70 mt-1">
        Nối 2 video <b>cùng người/động tác</b> vào <b>Nền</b> (A) + <b>Đồ mới</b> (B). Muốn khớp đẹp: 2 nhánh Motion
        dùng <b>cùng driver + cùng seed</b>. Test bằng preset motion <b>5s</b>.
      </p>
    </div>

    <!-- Kiểu lộ -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Kiểu lộ</p>
      <div class="grid grid-cols-2 gap-1.5">
        <button v-for="m in MODES" :key="m.id" type="button"
          :class="['apl-fm-tile', (local.revealMode || 'slider') === m.id && 'is-active']" @click="local.revealMode = m.id">
          <span class="apl-fm-tile-label">{{ m.label }}</span>
          <span class="apl-fm-tile-sub">{{ m.sub }}</span>
        </button>
      </div>
      <p class="apl-fm-hint">{{ MODES.find(m => m.id === (local.revealMode || 'slider'))?.hint }}</p>
    </div>

    <!-- Hướng quét (ẩn khi vortex) -->
    <div v-if="!isVortex" class="apl-fm-group">
      <p class="apl-fm-heading">Hướng quét</p>
      <div class="grid grid-cols-3 gap-1.5">
        <button v-for="d in DIRS" :key="d.id" type="button"
          :class="['apl-fm-tile', (local.direction || 'down') === d.id && 'is-active']" @click="local.direction = d.id">
          <span class="apl-fm-tile-label" style="font-size:11px">{{ d.label }}</span>
        </button>
      </div>
    </div>

    <!-- Số vòng xoáy (chỉ vortex) -->
    <div v-else class="apl-fm-group">
      <p class="apl-fm-heading">Số vòng xoáy</p>
      <div class="grid grid-cols-4 gap-1.5">
        <button v-for="t in TWISTS" :key="t.id" type="button"
          :class="['apl-fm-tile', (local.vortexTwists ?? 2) === t.id && 'is-active']" @click="local.vortexTwists = t.id">
          <span class="apl-fm-tile-label">{{ t.label }}</span>
        </button>
      </div>
    </div>

    <!-- ALD 09/07 - MỐC THỜI GIAN ACTION (MỌI kiểu lộ): tắt = slider quét GIỮA clip / wipe-cửa sổ-xoáy chạy từ ĐẦU clip.
         Bật = chọn giây action + điểm bắt đầu/kết thúc (lốc xoáy chỉ có thời điểm). -->
    <label class="apl-fm-group flex items-center justify-between cursor-pointer">
      <span>
        <span class="apl-fm-label">Tùy chỉnh thời điểm / vị trí quét</span>
        <span class="apl-fm-hint block">{{ isSlider ? 'Tắt = quét ở GIỮA clip, full khung.' : 'Tắt = hiệu ứng chạy từ ĐẦU clip (theo Tốc độ).' }} Bật = chọn giây action{{ isVortex ? '' : ' + điểm bắt đầu/kết thúc' }}.</span>
      </span>
      <input v-model="local.customSlider" type="checkbox" class="apl-fm-switch" />
    </label>
    <template v-if="local.customSlider">
      <div class="apl-fm-group">
        <label class="apl-fm-label">Action ở giây thứ (để trống = giữa clip)</label>
        <input v-model.number="local.sweepAtSec" type="number" step="0.5" min="0" max="120" class="apl-fm-input mt-1" placeholder="giữa clip" />
        <p class="apl-fm-hint mt-1">Vd clip 10s nhập <b>5</b> = hiệu ứng chạy quanh giây 5 (kéo dài theo "Tốc độ quét", mặc định 1s). Bật Lặp thì bỏ qua.</p>
      </div>
      <div v-if="!isVortex" class="apl-fm-group">
        <p class="apl-fm-heading">Điểm bắt đầu → kết thúc (%)</p>
        <div class="grid grid-cols-2 gap-2">
          <div>
            <span class="apl-fm-hint block mb-1">Bắt đầu</span>
            <input v-model.number="startPct" type="number" step="5" min="0" max="100" class="apl-fm-input" />
          </div>
          <div>
            <span class="apl-fm-hint block mb-1">Kết thúc</span>
            <input v-model.number="endPct" type="number" step="5" min="0" max="100" class="apl-fm-input" />
          </div>
        </div>
        <p class="apl-fm-hint mt-1">% chiều cao (dọc) / rộng (ngang). Vd bắt đầu <b>60%</b> = hiệu ứng xuất phát ở 60%. Cửa sổ mặc định chạy 25–85%; wipe/slider 0→100.</p>
      </div>
    </template>
    <label v-if="isSlider" class="apl-fm-group flex items-center justify-between cursor-pointer">
      <span>
        <span class="apl-fm-label">Kẻ vạch trắng</span>
        <span class="apl-fm-hint block">Hiện đường line trắng đang quét (như tay kéo thanh trượt so sánh).</span>
      </span>
      <input v-model="local.showLine" type="checkbox" class="apl-fm-switch" />
    </label>

    <!-- Lặp lại liên tục (mọi mode; slider = line dao động qua-lại giữa 2 điểm) -->
    <label class="apl-fm-group flex items-center justify-between cursor-pointer">
      <span>
        <span class="apl-fm-label">Lặp lại liên tục</span>
        <span class="apl-fm-hint block">{{ isSlider ? 'Bật = line DAO ĐỘNG qua-lại giữa điểm đầu↔cuối suốt clip (bỏ qua "giây quét").' : 'Bật = dải chạy đi-về LẶP suốt clip. Tắt = quét 1 lượt rồi giữ.' }}</span>
      </span>
      <input v-model="local.loop" type="checkbox" class="apl-fm-switch" />
    </label>

    <!-- Tốc độ -->
    <div class="apl-fm-group">
      <label class="apl-fm-label">{{ isSlider ? 'Tốc độ quét (giây · nhanh cứng)' : `Tốc độ — giây cho 1 lượt${local.loop ? ' / 1 chu kỳ' : ''} (0 = cả clip)` }}</label>
      <input v-model.number="local.sweepDuration" type="number" step="0.5" :min="isSlider ? 0.2 : 0" max="60" class="apl-fm-input mt-1" />
      <p class="apl-fm-hint mt-1">{{ isSlider ? 'Thời gian line quét từ mép này sang mép kia. Mặc định 1s = quét nhanh dứt khoát.' : 'Số càng NHỎ = quét càng NHANH. 0 = trải 1 lượt hết clip (khi bật Tùy chỉnh thời điểm: 0 = 1s quanh giây action).' }}</p>
    </div>

    <!-- Độ mềm ranh (ẩn cho slider vì line cứng) -->
    <div v-if="!isSlider" class="apl-fm-group">
      <p class="apl-fm-heading">Độ mềm ranh (feather)</p>
      <div class="grid grid-cols-3 gap-1.5">
        <button v-for="b in BANDS" :key="b.id" type="button"
          :class="['apl-fm-tile', Math.abs((local.bandPct ?? 0.25) - b.id) < 0.001 && 'is-active']" @click="local.bandPct = b.id">
          <span class="apl-fm-tile-label">{{ b.label }}</span>
          <span class="apl-fm-tile-sub">{{ b.sub }}</span>
        </button>
      </div>
      <p class="apl-fm-hint">Ranh càng mềm → che lệch mép giữa 2 bộ đồ tốt hơn (nhưng chuyển "nhòe" hơn).</p>
    </div>

    <!-- Đảo A/B -->
    <label class="apl-fm-group flex items-center justify-between cursor-pointer">
      <span>
        <span class="apl-fm-label">Đảo Nền ↔ Đồ mới</span>
        <span class="apl-fm-hint block">Bật = đổi vai A/B (bên đã quét thành Nền thay vì Đồ mới).</span>
      </span>
      <input v-model="local.swapBase" type="checkbox" class="apl-fm-switch" />
    </label>
  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'reveal' }
})
const emit = defineEmits(['update:config'])

const MODES = [
  { id: 'slider', label: 'Slider', sub: 'line cứng · so sánh', hint: 'Đường LINE CỨNG quét nhanh (mặc định 1s) qua GIỮA clip: trước = Nền, sau = Đồ mới — như thanh trượt so sánh before/after. KHÔNG mờ dần.' },
  { id: 'wipe', label: 'Wipe', sub: 'lộ mềm dần', hint: 'Ranh mềm lộ dần cả clip, kết thúc full Đồ mới. Kiểu "lột" từ từ.' },
  { id: 'scan', label: 'Cửa sổ', sub: 'dải xuyên', hint: 'Dải nhìn-xuyên trượt trong 25–85%. Bật Lặp = chạy lên-xuống liên tục.' },
  { id: 'vortex', label: 'Lốc xoáy', sub: 'xoắn từ tâm', hint: 'Reveal xoắn ốc quay từ tâm ra ngoài. Nặng — test clip ngắn 5s.' }
]
const DIRS = [
  { id: 'down', label: '↓ Trên→Dưới' },
  { id: 'up', label: '↑ Dưới→Trên' },
  { id: 'right', label: '→ Trái→Phải' },
  { id: 'left', label: '← Phải→Trái' },
  { id: 'diagtl', label: '↘ Chéo 45° trái' },
  { id: 'diagtr', label: '↙ Chéo 45° phải' }
]
const BANDS = [
  { id: 0.15, label: '15%', sub: 'ranh gọn' },
  { id: 0.25, label: '25%', sub: 'cân bằng' },
  { id: 0.40, label: '40%', sub: 'rất mềm' }
]
const TWISTS = [
  { id: 1, label: '1 vòng' }, { id: 2, label: '2 vòng' }, { id: 3, label: '3 vòng' }, { id: 4, label: '4 vòng' }
]

const local = ref({ revealMode: 'slider', direction: 'down', customSlider: false, sweepAtSec: null, startPos: 0, endPos: 1, showLine: true, loop: false, bandPct: 0.25, sweepDuration: 1, vortexTwists: 2, swapBase: false, ...props.config })
const isVortex = computed(() => (local.value.revealMode || 'slider') === 'vortex')
const isSlider = computed(() => (local.value.revealMode || 'slider') === 'slider')
// startPos/endPos lưu FRAC (0-1) cho worker; UI nhập % (0-100).
const startPct = computed({ get: () => Math.round((local.value.startPos ?? 0) * 100), set: (v) => { local.value.startPos = Math.max(0, Math.min(1, (Number(v) || 0) / 100)) } })
const endPct = computed({ get: () => Math.round((local.value.endPos ?? 1) * 100), set: (v) => { local.value.endPos = Math.max(0, Math.min(1, (Number(v) || 0) / 100)) } })

watch(local, (v) => emit('update:config', { ...v }), { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) local.value = { ...local.value, ...v }
})
</script>

<style scoped>
.apl-info-card { background: rgba(88,86,214,0.08); border: 0.5px solid rgba(88,86,214,0.28); border-radius: 12px; padding: 11px 12px; }
.apl-fm-group { background: var(--apl-fill); border: 0.5px solid rgba(235,236,240,0.12); border-radius: 14px; padding: 12px; }
.apl-fm-heading { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--apl-label); margin-bottom: 8px; }
.apl-fm-hint { margin-top: 6px; font-size: 10.5px; color: var(--apl-label); line-height: 1.4; }
.apl-fm-label { font-size: 12px; font-weight: 600; color: var(--apl-text); }
.apl-fm-input { width: 100%; background: var(--apl-bg-secondary); border: 0.5px solid rgba(235,236,240,0.18); border-radius: 10px; padding: 7px 10px; font-size: 13px; color: var(--apl-text); }
.apl-fm-switch { width: 38px; height: 22px; flex-shrink: 0; -webkit-appearance: none; appearance: none; background: rgba(235,236,240,0.22); border-radius: 999px; position: relative; cursor: pointer; transition: background 0.18s; }
.apl-fm-switch:checked { background: #5856D6; }
.apl-fm-switch::after { content: ''; position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; background: var(--apl-bg-secondary); transition: transform 0.18s; }
.apl-fm-switch:checked::after { transform: translateX(16px); }
.apl-fm-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; height: 44px; border-radius: 12px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; padding: 0 4px; }
.apl-fm-tile:hover { border-color: rgba(88,86,214,0.45); }
.apl-fm-tile.is-active { border-color: #5856D6; background: rgba(88,86,214,0.10); color: #4B49C8; box-shadow: 0 0 0 1px #5856D6 inset; }
.apl-fm-tile-label { font-size: 12px; font-weight: 700; }
.apl-fm-tile-sub { font-size: 9.5px; opacity: 0.7; }
</style>
