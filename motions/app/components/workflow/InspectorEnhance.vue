<template>
  <!-- ALD 28/06/2026 - Node "Nâng chất lượng / Upscale": 1 video (hậu Wan) → ffmpeg lanczos+làm nét lên 1080p/2K.
       Upscale RAM phẳng, không đụng GPU (bỏ ESRGAN ×4 vì OOM, bỏ 4K). Nối 1 node video (thường Motion). -->
  <!-- ALD 07/07/2026 - THÊM nút gạt MODE Video/Ảnh: mode=image → worker upscale 1 ẢNH bằng ESRGAN ×4 (bỏ fps/RIFE),
       mode=video → luồng cũ (lanczos/FlashVSR + RIFE). Worker đọc params.mode ('image'/'video'/'auto'). -->
  <div class="space-y-4">
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5"><i class="bi bi-badge-hd" /> Nâng chất lượng</p>
      <p v-if="isImage" class="text-[11px] opacity-70 mt-1">
        Nối <b>1 node ảnh</b> (Tạo ảnh / Sửa ảnh / Try-on). Node <b>upscale ×4</b> (ESRGAN)
        rồi ép về độ phân giải đích. Ảnh tĩnh nên <b>bỏ qua fps</b>.
      </p>
      <p v-else class="text-[11px] opacity-70 mt-1">
        Nối <b>1 node video</b> (thường là Motion). Node tự <b>xả RAM của Wan</b> rồi <b>nâng nét</b>
        lên độ phân giải đích (lanczos + làm nét, RAM phẳng). Wan render nhẹ 540p → node này lo độ nét.
      </p>
    </div>

    <!-- ALD 07/07/2026 - Nút gạt MODE: Video (clip) hay Ảnh (1 ảnh tĩnh) -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Chế độ</p>
      <div class="grid grid-cols-2 gap-1.5">
        <button v-for="m in MODES" :key="m.id" type="button"
          :class="['apl-fm-tile', mode === m.id && 'is-active']" @click="local.mode = m.id">
          <span class="apl-fm-tile-label"><i :class="['bi', m.icon]" /> {{ m.label }}</span>
          <span class="apl-fm-tile-sub">{{ m.sub }}</span>
        </button>
      </div>
      <p class="apl-fm-hint">{{ MODES.find(m => m.id === mode)?.hint }}</p>
    </div>

    <!-- ALD 09/07/2026 - Cách nâng (engine): Lanczos (ffmpeg, mặc định) vs FlashVSR (AI super-res). CHỈ mode video. -->
    <div v-if="!isImage" class="apl-fm-group">
      <p class="apl-fm-heading">Cách nâng chất lượng</p>
      <div class="grid grid-cols-2 gap-1.5">
        <button v-for="e in ENGINE" :key="e.id" type="button"
          :class="['apl-fm-tile', (local.engine || 'lanczos') === e.id && 'is-active']" @click="local.engine = e.id">
          <span class="apl-fm-tile-label">{{ e.label }}</span>
          <span class="apl-fm-tile-sub">{{ e.sub }}</span>
        </button>
      </div>
      <p class="apl-fm-hint">{{ ENGINE.find(e => e.id === (local.engine || 'lanczos'))?.hint }}</p>
    </div>

    <!-- ALD 07/07/2026 - Mode ảnh: chọn model ESRGAN ×4 (worker _run_enhance_image đọc params.upscaleModel) -->
    <div v-else class="apl-fm-group">
      <p class="apl-fm-heading">Model nâng nét</p>
      <div class="grid grid-cols-3 gap-1.5">
        <button v-for="m in IMG_MODELS" :key="m.id" type="button"
          :class="['apl-fm-tile', (local.upscaleModel || '4x-UltraSharp') === m.id && 'is-active']" @click="local.upscaleModel = m.id">
          <span class="apl-fm-tile-label">{{ m.label }}</span>
          <span class="apl-fm-tile-sub">{{ m.sub }}</span>
        </button>
      </div>
      <p class="apl-fm-hint">{{ IMG_MODELS.find(m => m.id === (local.upscaleModel || '4x-UltraSharp'))?.hint }}</p>
    </div>

    <!-- ALD 09/07/2026 - PHỤC HỒI MẶT (CodeFormer, chỉ mode ảnh): ESRGAN không biết mặt người → mặt sáp/nhựa/méo.
         CodeFormer dựng lại CHỈ vùng mặt sau upscale. Mặc định BẬT. Worker đọc faceRestore + faceFidelity. -->
    <template v-if="isImage">
      <label class="apl-fm-group flex items-center justify-between cursor-pointer">
        <span>
          <span class="apl-fm-label">Phục hồi khuôn mặt (AI)</span>
          <span class="apl-fm-hint block">CodeFormer dựng lại chi tiết mặt sau upscale — hết mặt "nhựa"/méo. Chỉ đắp vùng mặt, phần khác giữ nguyên.</span>
        </span>
        <input v-model="local.faceRestore" type="checkbox" class="apl-fm-switch" />
      </label>
      <div v-if="local.faceRestore" class="apl-fm-group">
        <p class="apl-fm-heading">Mức phục hồi mặt</p>
        <div class="grid grid-cols-3 gap-1.5">
          <button v-for="f in FIDELITIES" :key="f.id" type="button"
            :class="['apl-fm-tile', Math.abs((local.faceFidelity ?? 0.5) - f.id) < 0.001 && 'is-active']" @click="local.faceFidelity = f.id">
            <span class="apl-fm-tile-label">{{ f.label }}</span>
            <span class="apl-fm-tile-sub">{{ f.sub }}</span>
          </button>
        </div>
        <p class="apl-fm-hint">Đẹp hơn = AI vẽ thêm chi tiết (có thể hơi lệch mặt gốc) · Giữ gốc = bám ảnh gốc tối đa.</p>
      </div>
    </template>

    <!-- Độ phân giải đích (cả 2 mode; danh sách khác nhau) -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Độ phân giải đích</p>
      <div :class="['grid gap-1.5', isImage ? 'grid-cols-3' : 'grid-cols-2']">
        <button v-for="r in resAvail" :key="r.id || 'x4'" type="button"
          :class="['apl-fm-tile', local.targetRes === r.id && 'is-active']" @click="local.targetRes = r.id">
          <span class="apl-fm-tile-label">{{ r.label }}</span>
          <span class="apl-fm-tile-sub">{{ r.sub }}</span>
        </button>
      </div>
      <p class="apl-fm-hint">{{ resAvail.find(r => r.id === local.targetRes)?.hint }}</p>
    </div>

    <!-- Nội suy fps (RIFE) — CHỈ mode video (ảnh tĩnh không có fps) -->
    <div v-if="!isImage" class="apl-fm-group">
      <p class="apl-fm-heading">Độ mượt (fps)</p>
      <div class="grid grid-cols-3 gap-1.5">
        <button v-for="f in fpsAvail" :key="f.id || 'src'" type="button"
          :class="['apl-fm-tile', local.fpsInterp === f.id && 'is-active']" @click="local.fpsInterp = f.id">
          <span class="apl-fm-tile-label">{{ f.label }}</span>
          <span class="apl-fm-tile-sub">{{ f.sub }}</span>
        </button>
      </div>
      <p class="apl-fm-hint">{{ FPS.find(f => f.id === local.fpsInterp)?.hint }}</p>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'enhance' }
})
const emit = defineEmits(['update:config'])

// ALD 07/07/2026 - MODE: video (clip, luồng cũ) hay image (1 ảnh tĩnh → ESRGAN ×4). Worker đọc params.mode.
const MODES = [
  { id: 'video', label: 'Video', icon: 'bi-camera-video', sub: 'clip → upscale', hint: 'Nâng nét 1 CLIP video (thường sau Motion/Wan): xả RAM Wan rồi upscale + nội suy fps (RIFE).' },
  { id: 'image', label: 'Ảnh', icon: 'bi-image', sub: '1 ảnh → ×4', hint: 'Nâng nét 1 ẢNH tĩnh (Tạo ảnh / Sửa ảnh / Try-on) bằng ESRGAN ×4. Bỏ qua fps.' }
]

// Độ phân giải đích: video giữ 1080p/2K (đã bỏ 4K vì OOM); ảnh nhẹ hơn → cho 2K/4K + giữ ×4 gốc.
const RES_VIDEO = [
  { id: '1080p', label: 'Full HD', sub: '1080p', hint: 'Full HD — nhanh, đủ đẹp cho web/social. Phóng to lanczos + làm nét từ 540p.' },
  { id: '2k', label: '2K', sub: '1440p', hint: '2K (1440p) — nét hơn, file lớn hơn 1080p một chút.' }
]
const RES_IMAGE = [
  { id: '', label: 'Giữ ×4', sub: 'gốc', hint: 'Giữ nguyên kết quả ×4 của ESRGAN (không ép cỡ). Ảnh to → file lớn.' },
  { id: '2k', label: '2K', sub: '≤2560px', hint: 'ESRGAN ×4 rồi ép cạnh dài ≤ 2560px. Cân bằng nét / dung lượng.' },
  { id: '4k', label: '4K', sub: '≤3840px', hint: 'ESRGAN ×4 rồi ép cạnh dài ≤ 3840px. Nét nhất, file lớn.' }
]

// ALD 09/07/2026 - nhãn hết giả định gốc 16fps: motion "Theo driver" giờ ra 16/20/24/30fps tuỳ nguồn.
// Worker tự tính bội RIFE theo fps THẬT video vào + tự BỎ nội suy nếu vào đã ≥ đích.
const FPS = [
  { id: '', label: 'Gốc', sub: 'theo video vào', hint: 'GIỮ NGUYÊN fps của video truyền vào (16/20/24/30… tuỳ nguồn) — không nội suy, nét nhất, nhanh nhất.' },
  { id: '30', label: '30fps', sub: 'nội suy RIFE', hint: 'Nội suy lên ~30fps (bội số tự tính theo fps vào; video vào đã ≥30fps thì tự bỏ qua).' },
  { id: '48', label: '48fps', sub: 'nội suy RIFE', hint: 'Nội suy lên ~48fps — mượt cinematic, cân bằng blur. Khuyên dùng khi cần mượt.' },
  { id: '60', label: '60fps', sub: 'nội suy RIFE', hint: 'Nội suy lên ~60fps — mượt nhất nhưng dễ blur/ghost khi tay/chuyển động nhanh. RAM ổn (đã chunk).' }
]

// ALD 09/07/2026 - Engine nâng chất lượng. lanczos = ffmpeg (mặc định, mọi clip). flashvsr = AI super-res 1-step. BE đọc params.engine.
const ENGINE = [
  // ALD 09/07/2026 - SeedVR2 GỠ theo lệnh user (chậm quá; FlashVSR thay). Worker còn nhánh engine=seedvr2 ngủ đông (env-only).
  { id: 'lanczos', label: 'Nhanh (Lanczos)', sub: 'ffmpeg · mọi clip', hint: 'Phóng to + làm nét bằng ffmpeg (RAM phẳng, không đụng GPU). KHÔNG sinh chi tiết mới — chỉ nội suy. Nhanh, ổn định, hợp mọi clip. Mặc định.' },
  { id: 'flashvsr', label: 'FlashVSR (AI)', sub: 'nhanh · nét thật', hint: 'AI super-res 1-step (FlashVSR) — SINH chi tiết thật, nhanh, hợp GPU đang share. Cần cài node+model trên box; thiếu/lỗi tự về Lanczos.' }
]

// ALD 07/07/2026 - Model ESRGAN ×4 cho mode ảnh (worker chấp nhận 4x-UltraSharp / 4x_foolhardy_Remacri / RealESRGAN_x4plus).
const IMG_MODELS = [
  { id: '4x-UltraSharp', label: 'UltraSharp', sub: 'sắc, chi tiết', hint: '4x-UltraSharp — thêm chi tiết, sắc, ít "nhựa". Mặc định, hợp hầu hết ảnh.' },
  { id: '4x_foolhardy_Remacri', label: 'Remacri', sub: 'mềm, tự nhiên', hint: '4x_foolhardy_Remacri — mềm/tự nhiên hơn, đỡ gắt. Hợp da người, chân dung.' },
  { id: 'RealESRGAN_x4plus', label: 'RealESRGAN', sub: 'trơn, an toàn', hint: 'RealESRGAN_x4plus — trơn, an toàn, ít artifact. Hợp ảnh nhiễu/chất lượng thấp.' }
]

// ALD 09/07/2026 - mức CodeFormer fidelity: thấp = AI vẽ đẹp/bịa nhiều, cao = bám ảnh gốc.
const FIDELITIES = [
  { id: 0.3, label: 'Đẹp hơn', sub: 'AI vẽ nhiều' },
  { id: 0.5, label: 'Cân bằng', sub: 'khuyên dùng' },
  { id: 0.7, label: 'Giữ gốc', sub: 'bám ảnh gốc' }
]

const local = ref({ mode: 'video', targetRes: '1080p', fpsInterp: '48', engine: 'lanczos', upscaleModel: '4x-UltraSharp', faceRestore: true, faceFidelity: 0.5, ...props.config })
// ALD 09/07/2026 - SeedVR2 đã gỡ khỏi UI: node cũ lưu engine='seedvr2' tự lành về lanczos (tránh không tile nào active).
if (local.value.engine === 'seedvr2') local.value.engine = 'lanczos'
// ALD 28/06/2026 - config cũ lưu '4k' → kẹp về '2k' (4K đã bỏ Ở MODE VIDEO). ALD 07/07 - mode ảnh giữ 4K.
if (local.value.mode !== 'image' && local.value.targetRes === '4k') local.value.targetRes = '2k'

const isImage = computed(() => (local.value.mode || 'video') === 'image')
const mode = computed(() => local.value.mode || 'video')
const resAvail = computed(() => isImage.value ? RES_IMAGE : RES_VIDEO)
// ALD 28/06/2026 - bỏ 4K → 1080p/2K đều hỗ trợ đủ 30/48/60fps (RIFE đã chunk, RAM phẳng).
const fpsAvail = computed(() => FPS)

// ALD 07/07/2026 - Đổi mode → coerce targetRes về danh sách hợp lệ của mode đó (tránh không tile nào active).
watch(() => local.value.mode, (m) => {
  const ids = (m === 'image' ? RES_IMAGE : RES_VIDEO).map(r => r.id)
  if (!ids.includes(local.value.targetRes)) local.value.targetRes = (m === 'image' ? '2k' : '1080p')
})

watch(local, (v) => emit('update:config', { ...v }), { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) local.value = { ...local.value, ...v }
})
</script>

<style scoped>
.apl-info-card { background: rgba(255,149,0,0.08); border: 0.5px solid rgba(255,149,0,0.28); border-radius: 12px; padding: 11px 12px; }
.apl-fm-group { background: var(--apl-fill); border: 0.5px solid rgba(235,236,240,0.12); border-radius: 14px; padding: 12px; }
.apl-fm-heading { font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; color: var(--apl-label); margin-bottom: 8px; }
.apl-fm-hint { margin-top: 6px; font-size: 10.5px; color: var(--apl-label); line-height: 1.4; }
.apl-fm-switch { width: 38px; height: 22px; flex-shrink: 0; -webkit-appearance: none; appearance: none; background: rgba(235,236,240,0.22); border-radius: 999px; position: relative; cursor: pointer; transition: background 0.18s; }
.apl-fm-switch:checked { background: #FF9500; }
.apl-fm-switch::after { content: ''; position: absolute; top: 2px; left: 2px; width: 18px; height: 18px; border-radius: 50%; background: var(--apl-bg-secondary); transition: transform 0.18s; }
.apl-fm-switch:checked::after { transform: translateX(16px); }
.apl-fm-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; height: 44px; border-radius: 12px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-fm-tile:hover { border-color: rgba(255,149,0,0.45); }
.apl-fm-tile.is-active { border-color: #FF9500; background: rgba(255,149,0,0.08); color: #B36800; box-shadow: 0 0 0 1px #FF9500 inset; }
.apl-fm-tile-label { font-size: 12.5px; font-weight: 700; }
.apl-fm-tile-sub { font-size: 9.5px; opacity: 0.7; }
</style>
