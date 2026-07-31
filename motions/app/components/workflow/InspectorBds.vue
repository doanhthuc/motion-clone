<template>
  <!-- #region ALD 06/06/2026 - Inspector node bds (Time Lapse Construction) — nâng cấp: mặt bằng + clip mở đầu
       blueprint (lưới + số diện tích), vật liệu MULTI-SELECT, nhiều tỉ lệ + Auto, nhiều góc flycam, công tắc input. -->
  <div class="space-y-3">
    <!-- Mô tả -->
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5">
        <i class="bi bi-buildings-fill text-amber-500" />
        Time Lapse Construction
      </p>
      <p class="apl-fm-hint !mt-1">
        1 ảnh nhà hoàn thiện → video time-lapse xây nhà: <b>đất → móng → khung → hoàn thiện</b> + flycam.
        Công nhân xây tay, không máy móc. Local/free.
      </p>
    </div>

    <!-- #9 Tỉ lệ video (+ Auto khớp ảnh) -->
    <div>
      <label class="apl-fm-label">Tỉ lệ video</label>
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button
          v-for="a in ASPECT_RATIOS"
          :key="a.id"
          type="button"
          :class="['apl-fm-aspect', local.aspectRatio === a.id && 'is-active']"
          @click="local.aspectRatio = a.id"
          :title="a.hint"
        >
          <span class="apl-fm-aspect-id">{{ a.label }}</span>
          <span class="apl-fm-aspect-hint">{{ a.hint }}</span>
        </button>
      </div>
      <p class="apl-fm-hint mt-1">
        <b>Auto</b> = tự khớp tỉ lệ ảnh nhà bạn upload (ảnh 16:9 → video 16:9).
      </p>
    </div>

    <!-- #4 Vật liệu kết cấu (MULTI-SELECT) -->
    <div>
      <label class="apl-fm-label">Vật liệu kết cấu khung <span class="apl-fm-aspect-hint">(chọn nhiều)</span></label>
      <div class="grid grid-cols-3 gap-1.5 mt-1.5">
        <button
          v-for="m in MATERIALS"
          :key="m.id"
          type="button"
          :class="['apl-fm-tile', matActive(m.id) && 'is-active']"
          @click="toggleMaterial(m.id)"
        >
          <i :class="['bi text-base', m.icon]" />
          <span class="apl-fm-tile-label">{{ m.label }}</span>
        </button>
      </div>
      <p class="apl-fm-hint mt-1">
        Chọn nhiều = khung lai (vd <b>Thép + Bê tông</b>). Mặc định AI hay ra <b>gỗ</b> — chọn Thép/Bê tông để ép đúng (chặn gỗ).
      </p>
    </div>

    <!-- #10 Chế độ dựng (1 cảnh / nhiều cảnh) -->
    <div>
      <label class="apl-fm-label">Chế độ dựng</label>
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button
          v-for="m in SHOT_MODES"
          :key="m.id"
          type="button"
          :class="['apl-fm-tile', local.shotMode === m.id && 'is-active']"
          @click="local.shotMode = m.id"
        >
          <i :class="['bi text-base', m.icon]" />
          <span class="apl-fm-tile-label">{{ m.label }}</span>
          <span class="apl-fm-tile-hint">{{ m.hint }}</span>
        </button>
      </div>
      <p class="apl-fm-hint mt-1">
        <b>Nhiều cảnh</b>: wipe chuyển cảnh (mặc định). <b>1 cảnh liên tục</b>: dissolve 3s mượt như 1 shot, tự dùng flycam Trực diện.
      </p>
    </div>

    <!-- #5 Góc quay flycam -->
    <div>
      <label class="apl-fm-label">Góc quay flycam</label>
      <div class="grid grid-cols-3 gap-1.5 mt-1.5">
        <button
          v-for="g in FLY_ANGLES"
          :key="g.id"
          type="button"
          :class="['apl-fm-tile', local.flyAngle === g.id && 'is-active']"
          @click="local.flyAngle = g.id"
        >
          <i :class="['bi text-base', g.icon]" />
          <span class="apl-fm-tile-label">{{ g.label }}</span>
        </button>
      </div>
      <p class="apl-fm-hint mt-1">
        Đoạn bay quanh nhà cuối clip. <b>Auto</b>: pullback cinematic. <b>Trực diện</b>: 2 góc — eye-level mặt tiền → tilt lên 45°.
      </p>
    </div>

    <!-- Cảnh đêm -->
    <div>
      <label class="apl-fm-label">Cảnh nhà buổi tối (bật đèn)</label>
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button
          type="button"
          :class="['apl-fm-tile', local.nightScene && 'is-active']"
          @click="local.nightScene = true"
        >
          <i class="bi bi-moon-stars text-base" />
          <span class="apl-fm-tile-label">Có cảnh đêm</span>
        </button>
        <button
          type="button"
          :class="['apl-fm-tile', !local.nightScene && 'is-active']"
          @click="local.nightScene = false"
        >
          <i class="bi bi-sun text-base" />
          <span class="apl-fm-tile-label">Chỉ ban ngày</span>
        </button>
      </div>
      <p class="apl-fm-hint mt-1">
        Bật = thêm đoạn <b>ngày→đêm bật đèn</b> + <b>flycam ban đêm là cảnh cuối</b>. Render lâu hơn (~+7-8').
      </p>
    </div>

    <!-- Chất lượng -->
    <div>
      <label class="apl-fm-label">Chất lượng</label>
      <div class="grid grid-cols-3 gap-1.5 mt-1.5">
        <button
          v-for="q in QUALITY_OPTS"
          :key="q.id"
          type="button"
          :class="['apl-fm-tile', local.quality === q.id && 'is-active']"
          @click="local.quality = q.id"
        >
          <span class="apl-fm-tile-label">{{ q.label }}</span>
          <span class="apl-fm-aspect-hint">{{ q.hint }}</span>
        </button>
      </div>
      <p class="apl-fm-hint mt-1">
        Tăng độ nét ảnh giai đoạn + video. <b>Cao</b> nét nhất nhưng mỗi đoạn chậm hơn ~30-60%.
      </p>
    </div>

    <!-- fps + Số khung hình -->
    <div class="grid grid-cols-2 gap-2">
      <div>
        <label class="apl-fm-label">Khung hình/giây</label>
        <div class="grid grid-cols-2 gap-1.5 mt-1.5">
          <button
            v-for="f in FPS_OPTS"
            :key="f.id"
            type="button"
            :class="['apl-fm-tile', local.fps === f.id && 'is-active']"
            @click="local.fps = f.id"
          >
            <span class="apl-fm-tile-label">{{ f.label }}</span>
          </button>
        </div>
      </div>
      <div>
        <label class="apl-fm-label">Số khung hình</label>
        <div class="grid grid-cols-3 gap-1.5 mt-1.5">
          <button
            v-for="fr in FRAME_OPTS"
            :key="fr.id"
            type="button"
            :class="['apl-fm-tile', local.frames === fr.id && 'is-active']"
            @click="local.frames = fr.id"
          >
            <span class="apl-fm-tile-label">{{ fr.label }}</span>
            <span class="apl-fm-aspect-hint">{{ fr.hint }}</span>
          </button>
        </div>
      </div>
    </div>
    <p class="apl-fm-hint">
      Nhiều khung hình hơn = mỗi đoạn dài & mượt hơn nhưng render lâu hơn (121 có thể tốn RAM).
    </p>

    <!-- Advanced -->
    <details class="apl-fm-group">
      <summary class="apl-fm-summary">Advanced · tốc độ & seed</summary>
      <div class="space-y-3 mt-3">
        <!-- Model Wan -->
        <div>
          <label class="apl-fm-label">Model video AI</label>
          <div class="grid grid-cols-2 gap-1.5 mt-1.5">
            <button
              v-for="m in WAN_MODELS"
              :key="m.id"
              type="button"
              :class="['apl-fm-tile', local.wanModel === m.id && 'is-active']"
              @click="local.wanModel = m.id"
            >
              <span class="apl-fm-tile-label">{{ m.label }}</span>
              <span class="apl-fm-aspect-hint">{{ m.hint }}</span>
            </button>
          </div>
          <p class="apl-fm-hint mt-1">Wan 2.2 thử nghiệm — cần download model trên máy chủ trước.</p>
        </div>

        <label class="block">
          <span class="apl-fm-label">Tốc độ tua (build): {{ local.buildSpeed.toFixed(1) }}×</span>
          <input v-model.number="local.buildSpeed" type="range" min="1" max="4" step="0.1" class="w-full mt-1" />
          <span class="apl-fm-hint">Càng cao càng tua nhanh đoạn đất→móng→khung→nhà. Mặc định <b>1.0×</b> (tốc độ gốc, không tua).</span>
        </label>
        <label class="block">
          <span class="apl-fm-label">Tốc độ flycam: {{ local.flySpeed.toFixed(1) }}×</span>
          <input v-model.number="local.flySpeed" type="range" min="1" max="3" step="0.1" class="w-full mt-1" />
          <span class="apl-fm-hint">Đoạn bay quanh nhà hoàn thiện. Mặc định 1.0×.</span>
        </label>
        <label class="block">
          <span class="apl-fm-label">Seed</span>
          <input v-model.number="local.seed" type="number" step="1" min="0" class="apl-fm-input mt-1" />
          <span class="apl-fm-hint">Cố định để các giai đoạn ổn định; đổi seed nếu muốn dựng khác.</span>
        </label>
      </div>
    </details>

    <!-- Cảnh báo render -->
    <div class="apl-info-card">
      <p class="apl-fm-hint !mt-0">
        <i class="bi bi-clock-history text-amber-500" />
        Render <b>lâu (~20-30 phút khi bật cảnh đêm)</b> — nhiều lượt dựng trên 1 GPU. Chạy lúc box rảnh để khỏi tranh GPU.
      </p>
    </div>
  </div>
  <!-- #endregion -->
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'bds' }
})
const emit = defineEmits(['update:config'])

// #9 - thêm Auto (khớp ảnh) + nhiều tỉ lệ (khớp _BDS_ASPECT bên worker.py).
const ASPECT_RATIOS = [
  { id: 'auto', label: 'Auto', hint: 'Khớp ảnh nhà' },
  { id: '9:16', label: '9:16', hint: 'Dọc — TikTok' },
  { id: '16:9', label: '16:9', hint: 'Ngang — YouTube' },
  { id: '1:1',  label: '1:1',  hint: 'Vuông — IG' },
  { id: '4:5',  label: '4:5',  hint: 'Dọc — IG' },
  { id: '3:4',  label: '3:4',  hint: 'Dọc cổ điển' },
  { id: '4:3',  label: '4:3',  hint: 'Ngang TV' },
  { id: '21:9', label: '21:9', hint: 'Điện ảnh' }
]

// #4 - vật liệu kết cấu khung MULTI-SELECT (khớp BDS_MATERIALS bên worker.py).
const MATERIALS = [
  { id: 'steel',    label: 'Thép/Sắt',  icon: 'bi-building-gear' },
  { id: 'concrete', label: 'Bê tông',   icon: 'bi-bricks' },
  { id: 'wood',     label: 'Gỗ',        icon: 'bi-tree' }
]
const MAT_IDS = MATERIALS.map((m) => m.id)
function normMat(m) {
  let a = []
  if (Array.isArray(m)) a = m
  else if (typeof m === 'string' && m) a = m.split(',').map((s) => s.trim())
  a = a.filter((x) => MAT_IDS.includes(x))
  return a.length ? a : ['steel']
}
function matActive(id) {
  return Array.isArray(local.value.material) && local.value.material.includes(id)
}
function toggleMaterial(id) {
  const cur = Array.isArray(local.value.material) ? [...local.value.material] : normMat(local.value.material)
  const i = cur.indexOf(id)
  if (i >= 0) { if (cur.length > 1) cur.splice(i, 1) }  // giữ tối thiểu 1 vật liệu
  else cur.push(id)
  local.value.material = cur
}

// #5 - góc quay flycam (khớp BDS_FLY_MOVES; auto = dọc cho 9:16, ngang cho 16:9).
const WAN_MODELS = [
  { id: 'wan2.1', label: 'Wan 2.1', hint: 'Mặc định' },
  { id: 'wan2.2', label: 'Wan 2.2', hint: 'Thử nghiệm' },
]

// #10 - chế độ dựng (nhiều cảnh / 1 cảnh liên tục trực diện)
const SHOT_MODES = [
  { id: 'multi',  label: 'Nhiều cảnh',     hint: 'Wipe chuyển cảnh', icon: 'bi-collection-play' },
  { id: '1-shot', label: '1 cảnh liên tục', hint: 'Dissolve mượt',   icon: 'bi-camera-reels' },
]

const FLY_ANGLES = [
  { id: 'auto',     label: 'Auto',     icon: 'bi-magic' },
  { id: 'ngang',    label: 'Ngang',    icon: 'bi-arrow-left-right' },
  { id: 'doc',      label: 'Dọc',      icon: 'bi-arrow-down-up' },
  { id: 'left',     label: '45° Trái', icon: 'bi-arrow-down-left' },
  { id: 'right',    label: '45° Phải', icon: 'bi-arrow-down-right' },
  { id: 'orbit360', label: 'Vòng 360', icon: 'bi-arrow-repeat' },
  { id: 'topdown',  label: 'Top-down', icon: 'bi-arrow-up-square' },
  { id: 'pushin',   label: 'Lao vào',  icon: 'bi-box-arrow-in-down' },
  { id: 'fpv',      label: 'FPV bổ',   icon: 'bi-lightning-charge' },
  { id: 'corner',   label: 'Chéo góc', icon: 'bi-arrows-angle-expand' },
  { id: 'pullback',    label: 'Lùi xa',    icon: 'bi-box-arrow-up-right' },
  { id: 'front-tilt', label: 'Trực diện', icon: 'bi-camera-video' },
]

const QUALITY_OPTS = [
  { id: 'nhanh', label: 'Nhanh', hint: '18 steps' },
  { id: 'chuan', label: 'Chuẩn', hint: '25 steps' },
  { id: 'cao',   label: 'Cao',   hint: '32 steps' }
]

const FPS_OPTS = [
  { id: 30, label: '30fps' },
  { id: 60, label: '60fps' }
]

const FRAME_OPTS = [
  { id: 49,  label: '49',  hint: 'Nhanh' },
  { id: 81,  label: '81',  hint: 'Chuẩn' },
  { id: 121, label: '121', hint: 'Mượt' }
]

const local = ref({
  aspectRatio: 'auto',
  flyAngle: 'auto',
  quality: 'chuan',
  buildSpeed: 1.0,
  flySpeed: 1.0,
  seed: 42,
  ...props.config,
  // ALD 06/06/2026 - ép kiểu + default (node cũ chưa có các field này; material cũ là string → list).
  material: normMat(props.config?.material),
  fps: Number(props.config?.fps) || 60,
  frames: Number(props.config?.frames) || 145,
  nightScene: props.config?.nightScene !== false,
  wanModel: props.config?.wanModel || 'wan2.1',
  shotMode: props.config?.shotMode || 'multi',   // ALD 10/06/2026
})

watch(local, (v) => emit('update:config', { ...v }), { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) {
    local.value = { ...local.value, ...v, material: normMat(v.material ?? local.value.material) }
  }
})
</script>
