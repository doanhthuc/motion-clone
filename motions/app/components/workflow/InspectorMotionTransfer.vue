<template>
  <!-- #region ALD 24/05/2026 - Motion Transfer node inspector.
       Multi-input qua handles: image (ref nhân vật) + video (motion) + audio (optional).
       Không còn refImageSource/motionVideoSource — cấu hình đó là của node Input upstream. -->
  <div class="space-y-3">
    <!-- ALD 10/07/2026 - Provider: Self-host (Wan Animate trên box) vs DashScope (wan2.2-animate cloud, cần key).
         Chọn DashScope → node mọc cổng API Key; mọi knob self-host bên dưới (preset/pose/màu/faceLock) bị bỏ qua. -->
    <div class="apl-fm-group">
      <p class="apl-fm-heading">Provider</p>
      <div class="grid grid-cols-2 gap-1.5">
        <button type="button" :class="['apl-fm-tile', local.provider !== 'dashscope' && 'is-active']" @click="local.provider = 'qwen'">
          <span class="apl-fm-tile-label">Self-host</span>
        </button>
        <button type="button" :class="['apl-fm-tile', local.provider === 'dashscope' && 'is-active']" @click="local.provider = 'dashscope'">
          <span class="apl-fm-tile-label">DashScope</span>
        </button>
      </div>
    </div>
    <template v-if="local.provider === 'dashscope'">
      <div class="grid grid-cols-2 gap-2">
        <div class="apl-fm-group">
          <p class="apl-fm-heading">Chế độ</p>
          <!-- ALD 10/07/2026 - + happyhorse video-edit: thay người theo ảnh mẫu, GIỮ motion driver (semantics
               như Mix nhưng CHẮC CHẮN có trên region intl — animate move/mix có thể không mở intl). -->
          <select v-model="local.dashscopeModel" class="apl-fm-input">
            <option value="wan2.2-animate-move">Move — bê chuyển động vào ảnh mẫu</option>
            <option value="wan2.2-animate-mix">Mix — thay người vào video driver</option>
            <option value="happyhorse-1.0-video-edit">Edit — thay người theo mẫu, giữ motion (intl ✓)</option>
          </select>
        </div>
        <div v-if="!isDsVideoEdit" class="apl-fm-group">
          <p class="apl-fm-heading">Chất lượng</p>
          <select v-model="local.dashscopeQuality" class="apl-fm-input">
            <option value="wan-std">Standard (nhanh, rẻ)</option>
            <option value="wan-pro">Pro (25fps, mượt hơn)</option>
          </select>
        </div>
        <div v-else class="apl-fm-group">
          <p class="apl-fm-heading">Độ phân giải</p>
          <select v-model="local.dashscopeResolution" class="apl-fm-input">
            <option value="720P">720P</option>
            <option value="1080P">1080P (đắt hơn)</option>
          </select>
        </div>
      </div>
      <div v-if="isDsVideoEdit" class="apl-fm-group">
        <p class="apl-fm-heading">Yêu cầu chỉnh sửa (tuỳ chọn)</p>
        <textarea v-model="local.dashscopePrompt" rows="2" class="apl-fm-input"
          placeholder="Bỏ trống = thay người trong video bằng người trong ảnh mẫu, giữ chuyển động/bối cảnh. Viết tiếng Việt được — tự dịch." />
      </div>
      <p class="apl-fm-hint">
        <i class="bi bi-key me-1" />Nối node <b>API Key</b> (Type: <code>dashscope</code>) vào cổng API Key, hoặc đặt rời.
        Ràng buộc cloud: ảnh mẫu ≤5MB · driver 2–30s (AV1/HEVC tự transcode H.264). Audio giữ theo lựa chọn Âm thanh bên dưới; các thông số self-host khác không áp dụng.
      </p>
    </template>
    <!-- Preset / custom driver segment -->
    <div v-if="local.provider !== 'dashscope'">
      <div class="flex items-center justify-between gap-3 py-2">
        <label class="apl-fm-label">Thời lượng video</label>
        <button type="button" role="switch" :aria-checked="useDriverSegment" @click="toggleDriverSegment"
          :class="['relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors', useDriverSegment ? 'bg-primary' : 'bg-gray-300 dark:bg-gray-600']"
          title="Bật để tự chọn đoạn motion driver">
          <span :class="['inline-block h-4 w-4 transform rounded-full bg-gray-50 shadow transition-transform', useDriverSegment ? 'translate-x-4' : 'translate-x-0.5']" />
        </button>
      </div>
      <template v-if="!useDriverSegment">
        <UiDropdown
          v-model="local.preset"
          :options="presetOptions"
          full-width
          no-clear
        />
        <p v-if="currentPreset" class="apl-fm-hint">
          <!-- ALD 11/07/2026 - driverNative không có `frames` (worker tự tính) → hiện "fps & khung theo driver" thay số frame. -->
          {{ currentPreset.resolution }} · {{ currentPreset.driverNative ? 'fps & khung theo driver' : currentPreset.frames + ' frames' }} · {{ currentPreset.steps }} steps · {{ currentPreset.note }}
        </p>
      </template>
      <template v-else>
        <div class="grid grid-cols-2 gap-2 mt-1.5">
          <label class="block">
            <span class="apl-fm-label">Bắt đầu (giây)</span>
            <input v-model.number="local.driverStartSec" type="number" step="0.5" min="0" class="apl-fm-input mt-1" />
          </label>
          <label class="block">
            <span class="apl-fm-label">Thời lượng (giây · tối đa 15)</span>
            <input v-model.number="local.driverDurSec" type="number" step="0.5" min="0.5" max="15" class="apl-fm-input mt-1" @change="clampCustomDur" @blur="clampCustomDur" />
          </label>
        </div>
        <!-- ALD 30/06/2026 - Custom: chọn cứng chất lượng 720p/540p, tối đa 10s -->
        <div class="mt-2">
          <span class="apl-fm-label">Chất lượng</span>
          <div class="grid grid-cols-2 gap-1.5 mt-1">
            <button
              v-for="q in CUSTOM_QUALITIES"
              :key="q.id"
              type="button"
              :class="['apl-fm-aspect', local.quality === q.id && 'is-active']"
              :title="q.hint"
              @click="local.quality = q.id"
            >
              <span class="apl-fm-aspect-id">{{ q.label }}</span>
              <span class="apl-fm-aspect-hint">{{ q.hint }}</span>
            </button>
          </div>
        </div>
        <p class="apl-fm-hint mt-1">Lấy 1 đoạn <b>≤15s</b> của driver. <b>720p</b> nét hơn · <b>540p</b> nhẹ &amp; ổn định. Tắt toggle để quay lại preset.</p>
      </template>
    </div>

    <!-- Aspect ratio -->
    <div>
      <label class="apl-fm-label">Tỉ lệ video</label>
      <div class="grid grid-cols-3 gap-1.5 mt-1.5">
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
    </div>

    <!-- Âm thanh -->
    <div>
      <label class="apl-fm-label">Âm thanh</label>
      <div class="grid grid-cols-3 gap-1.5 mt-1.5">
        <button
          type="button"
          :class="['apl-fm-tile', audioMode === 'original' && 'is-active']"
          @click="setAudioMode('original')"
        >
          <i class="bi bi-music-note-beamed text-base" />
          <span class="apl-fm-tile-label">Âm gốc video</span>
        </button>
        <button
          type="button"
          :class="['apl-fm-tile', audioMode === 'replacement' && 'is-active']"
          @click="setAudioMode('replacement')"
        >
          <i class="bi bi-soundwave text-base" />
          <span class="apl-fm-tile-label">Âm thay thế</span>
        </button>
        <button
          type="button"
          :class="['apl-fm-tile', audioMode === 'silent' && 'is-active']"
          @click="setAudioMode('silent')"
        >
          <i class="bi bi-volume-mute text-base" />
          <span class="apl-fm-tile-label">Im lặng</span>
        </button>
      </div>
      <p class="apl-fm-hint mt-1">
        "Âm thay thế" dùng file nối vào cổng <b>audio</b>. "Im lặng" bỏ mọi âm thanh, kể cả audio gốc và audio nối vào.
      </p>
    </div>

    <!-- #region ALD 13/07/2026 - Preset kỹ thuật phát hành; không dùng để né nhãn AI/AIGC. -->
    <div>
      <label class="apl-fm-label">Đầu ra phát hành</label>
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button
          type="button"
          :class="['apl-fm-tile', local.deliveryPreset !== 'source' && 'is-active']"
          @click="local.deliveryPreset = 'tiktok'"
        >
          <i class="bi bi-tiktok text-base" />
          <span class="apl-fm-tile-label">TikTok 1080p</span>
          <span class="apl-fm-hint">9:16 · CFR30 · H.264/AAC</span>
        </button>
        <button
          type="button"
          :class="['apl-fm-tile', local.deliveryPreset === 'source' && 'is-active']"
          @click="local.deliveryPreset = 'source'"
        >
          <i class="bi bi-file-earmark-play text-base" />
          <span class="apl-fm-tile-label">Giữ file nguồn</span>
          <span class="apl-fm-hint">Không transcode phát hành</span>
        </button>
      </div>
      <p class="apl-fm-hint mt-1">
        TikTok 1080p chuẩn hoá codec và timing để upload ổn định. Preset không né hoặc bảo đảm tránh nhãn AI.
      </p>
    </div>
    <!-- #endregion -->

    <!-- ALD 27/06/2026 - Motion mặc định RAW màu Wan: không ColorMatch, không warmth/cap, không hậu kỳ màu/sáng
         để tránh flash đổi màu. Các control màu chỉ còn trong Chuyên sâu để thử nghiệm thủ công. -->


    <!-- Info -->
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5">
        <i class="bi bi-info-circle-fill text-violet-500" />
        Cách dùng
      </p>
      <p class="apl-fm-hint mt-1">Nối `image` = ảnh mẫu, `video` = video motion. Thường chỉ cần chọn preset rồi chạy. `audio` chỉ dùng khi muốn thay âm gốc.</p>
    </div>

    <details class="apl-fm-group" open>
      <summary class="apl-fm-summary">Chuyên sâu</summary>
      <div class="grid grid-cols-2 gap-2 mt-3">
        <!-- ALD 09/07/2026 - GỌN cho người mới: BỎ "Chỉnh màu hậu kỳ" (matchRef/warmth/brightCap — mặc định RAW,
             cần thì chỉnh qua config) + bỏ kiểu Transfer/Mix cũ (config.mode luôn Transfer). -->
        <!-- ALD 11/07/2026 - Toggle retarget ĐÃ GỠ HẲN theo chốt của user (worker cũng đã vô hiệu vĩnh viễn). -->
        <label class="block">
          <span class="apl-fm-label">Độ bám mặt theo motion</span>
          <input v-model.number="local.faceStrength" type="number" step="0.1" min="0" max="1.5" class="apl-fm-input mt-1" />
          <span class="apl-fm-hint mt-0.5">Cao = bám biểu cảm driver hơn. Thấp = giữ mặt gốc hơn.</span>
        </label>
        <label class="block">
          <span class="apl-fm-label">Độ bám động tác</span>
          <input v-model.number="local.poseStrength" type="number" step="0.1" min="0" max="1.5" class="apl-fm-input mt-1" />
          <span class="apl-fm-hint mt-0.5">Bám tay/dáng. Cao (0.9) = đỡ ảo nhưng dễ mờ khi nhanh. Mặc định 0.8.</span>
        </label>
        <!-- ALD 11/07/2026 - Toggle bỏ keypoint tay driver → chống "ngón tay kéo dài" -->
        <!-- <div class="col-span-2 flex items-center justify-between gap-3 py-1">
          <div class="min-w-0">
            <span class="apl-fm-label">Bám ngón tay driver</span>
            <span class="apl-fm-hint block mt-0.5">Tắt nếu <b>ngón tay bị kéo dài</b> — bỏ keypoint tay driver, Wan tự dựng bàn tay theo ảnh mẫu (cử chỉ ngón không bám driver, nhưng hết méo/dài).</span>
          </div>
          <button type="button" role="switch" :aria-checked="useDriverHands" @click="local.driverHands = !useDriverHands"
            :class="['relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors', useDriverHands ? 'bg-primary' : 'bg-gray-300 dark:bg-gray-600']"
            title="Tắt để chống ngón tay dài">
            <span :class="['inline-block h-4 w-4 transform rounded-full bg-gray-50 shadow transition-transform', useDriverHands ? 'translate-x-4' : 'translate-x-0.5']" />
          </button>
        </div> -->
        <label class="block col-span-2">
          <span class="apl-fm-label">Mô tả tăng cường (tay / chất lượng) <span class="font-normal text-gray-400">· tuỳ chọn</span></span>
          <textarea
            v-model="local.extraPositive"
            rows="2"
            spellcheck="false"
            placeholder="VD: detailed sharp hands, five clear fingers"
            class="apl-fm-input mt-1 text-xs leading-relaxed"
            style="height:auto;min-height:40px;padding:6px 9px;line-height:1.45;resize:vertical"
          />
          <span class="apl-fm-hint mt-0.5">Đã <b>viết sẵn</b> chống cháy sáng / da nhựa / tay–miệng ảo giác. Sửa/thêm thoải mái (tiếng Anh).</span>
        </label>
        <label class="block">
          <span class="apl-fm-label">Độ giữ nhận diện</span>
          <input v-model.number="local.clipStrength" type="number" step="0.1" min="0.5" max="2.0" class="apl-fm-input mt-1" />
          <span class="apl-fm-hint mt-0.5">Cao = giữ giống ảnh ref chặt hơn, nhưng có thể bớt linh hoạt.</span>
        </label>
        <label class="block">
          <span class="apl-fm-label">Tăng relight</span>
          <input v-model.number="local.loraRelight" type="number" step="0.05" min="0" max="1.5" class="apl-fm-input mt-1" />
          <span class="apl-fm-hint mt-0.5">Thường để 0. Chỉ tăng nhẹ khi cảnh ánh sáng phức tạp thật sự.</span>
        </label>
        <!-- ALD 09/07/2026 - Gỡ chọn "Ưu tiên mặt theo nguồn nào" khỏi UI, gắn cứng faceSource=driver (xem default local.faceSource bên dưới). -->
        <label class="block col-span-2">
          <span class="apl-fm-label">Bỏ frame đầu bị lỗi pose</span>
          <input v-model.number="local.skipFirstFrames" type="number" step="1" min="0" max="32" class="apl-fm-input mt-1" />
          <span class="apl-fm-hint mt-0.5">Chỉ dùng khi đầu video driver bị giật hoặc pose lệch.</span>
        </label>
        <label class="block col-span-2">
          <span class="apl-fm-label">Tăng tốc chuyển động (%)</span>
          <input v-model.number="local.motionSpeedup" type="number" step="1" min="0" max="100" class="apl-fm-input mt-1" />
          <span class="apl-fm-hint mt-0.5">0 = giữ nguyên. Chỉ tăng khi motion gốc quá chậm.</span>
        </label>
      </div>
    </details>

  </div>
  <!-- #endregion -->

  <!-- ALD 09/07/2026 - Nút "Xem thử" ẩn khỏi UI theo yêu cầu; modal + showPreview giữ nguyên phòng khi cần mở lại tay. -->
  <WorkflowMotionPreviewModal v-model="showPreview" :params="local" @update:params="(v) => Object.assign(local, v)" />
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'motion' }
})
const emit = defineEmits(['update:config'])

const { PRESETS } = useMotionJobs()
// ALD 21/06/2026 - fps theo preset: preset có field fps (30/60) → node gửi params.fps. Mặc định 30.
const _presetFps = (id) => PRESETS.find((p) => p.id === id)?.fps || 30

// ALD 09/07/2026 - GATE preset theo RAM box: preset có minRamGb (vd 20s·30fps cần ≥128GB) chỉ hiện khi
// GET /workflows/capabilities báo đủ RAM. Fetch 1 lần cache useState; lỗi/chưa biết → ẨN (an toàn);
// node cũ ĐÃ lưu preset bị gate thì vẫn hiện option đó (không vỡ dropdown).
const boxRamGb = useState('wf-box-ram-gb', () => 0)
const _auth = useAuth()
onMounted(async () => {
  if (boxRamGb.value > 0) return
  try { boxRamGb.value = Number((await _auth.beFetch('/workflows/capabilities'))?.totalRamGb) || 0 }
  catch { boxRamGb.value = 0 }
})
const presetAvail = (p) => !p.minRamGb || boxRamGb.value >= p.minRamGb || p.id === local.value.preset
// ALD 10/07/2026 - happyhorse video-edit dùng resolution + prompt thay vì quality (body API khác hẳn animate).
const isDsVideoEdit = computed(() => String(local.value.dashscopeModel || '').endsWith('-video-edit'))

// ALD 11/07/2026 - preset giờ CHỈ còn lựa chọn SỐ GIÂY (tất cả driverNative) → list phẳng, bỏ nhóm fps
// (Base 16fps / Native 30fps đã gỡ). fps/frame/tỉ lệ do worker probe theo driver. Vẫn lọc theo RAM (drv-30s gate).
const presetOptions = computed(() => {
  const opt = (p) => ({ value: p.id, label: p.label, hint: `${p.eta}${p.note ? ' · ' + p.note : ''}` })
  return PRESETS.filter((p) => presetAvail(p)).map(opt)
})

// ALD 09/07/2026 - BỎ MODES (Kiểu render) + COLORMATCH_METHODS: UI đã gỡ cho gọn người mới —
// mode luôn 'transfer' (config mặc định), màu luôn RAW (matchRef=false).
const ASPECT_RATIOS = [
  { id: '9:16', label: '9:16', hint: 'Reels/TikTok' },
  { id: '16:9', label: '16:9', hint: 'YouTube' },
  { id: '1:1',  label: '1:1',  hint: 'Square' },
  { id: '3:4',  label: '3:4',  hint: 'Portrait' },
  { id: '4:3',  label: '4:3',  hint: 'Landscape' },
  { id: '21:9', label: '21:9', hint: 'Ultrawide' }
]

const showPreview = ref(false)  // ALD 25/06/2026 - preview modal

// ALD 09/07/2026 - Prompt tăng cường VIẾT SẴN (append vào positive, worker đọc extraPositive): chống "quá tráng"
// (cháy sáng/washed-out), da "nhựa" (plastic/doll), tay ảo giác (thừa/dính ngón), miệng ảo giác (méo/răng ảo).
// Sync với defaultConfig('motion') ở workflows/[id]/index.vue. User xoá/sửa thoải mái trong ô.
// ALD 12/07/2026 - VIẾT LẠI THÀNH KHẲNG ĐỊNH: Wan chạy distill (cfg≈1) BỎ QUA negative + bám text yếu; câu
// phủ định ("no/not gloved/no nail polish") nhét vào POSITIVE phản tác dụng (model cân DƯƠNG token
// "gloved/nail polish/teeth"). Tả thẳng cái MUỐN THẤY (bare hands, matte skin…) mới có chút tác dụng.
const DEFAULT_EXTRA_POSITIVE = 'soft even matte lighting with retained detail in bright areas, natural matte skin with visible pores and realistic texture, stable natural mouth and lips, steady well-formed bare hands with five clearly separated fingers, natural fingertips, clean short natural fingernails'
// Các bản default CŨ (phủ định) — nếu node đang lưu đúng 1 trong số này thì tự nâng cấp lên bản khẳng định trên.
const _OLD_DEFAULT_EXTRA_POSITIVES = [
  'no overexposed or washed-out highlights, natural skin tone with visible pores, not plastic, not doll-like, stable natural mouth and lips, no warped or flickering mouth, no hallucinated teeth, steady well-formed fingers, no extra or fused fingers',
]

const local = ref({
  preset: 'drv-15s',   // ALD 09/07/2026 - default "Theo driver" 15s (fps/frame/tỉ lệ 1:1 theo driver, user chỉ chọn giây)
  quality: '',          // ALD 30/06/2026 - chỉ dùng ở Custom: '720p'|'540p' (cứng), rỗng = theo preset
  mode: 'transfer',
  aspectRatio: '9:16',
  audioMode: 'original',       // original | replacement | silent
  audioPassthrough: true,   // true = âm gốc video motion · false = âm thay thế (cổng input audio)
  deliveryPreset: 'tiktok', // ALD 13/07/2026 - 1080×1920/CFR30/H.264/AAC; 'source' = giữ output pipeline.
  renderProfile: 'fast',
  faceStrength: 0.6,
  faceSource: 'driver',   // ALD 09/07/2026 - user chốt mặc định DRIVER (bám biểu cảm/lipsync); 'ref' = chọn tay.
  poseStrength: 0.8,   // ALD 25/06/2026 - 1.0→0.8: bớt bám sát keypoint → giảm bóng mờ tay khi chuyển động nhanh.
  driverHands: true,   // ALD 11/07/2026 (chiều) - REVERT về true: false (bỏ keypoint tay) làm Wan dựng NẮM TAY / mất ngón (tệ hơn ngón dài). Giữ bám driver để có tay. Trị ngón dài đi hướng khác (không bỏ tay).
  extraPositive: DEFAULT_EXTRA_POSITIVE,   // ALD 09/07/2026 - viết sẵn chống cháy sáng/da nhựa/tay-miệng ảo giác.
  clipStrength: 1.2,
  loraRelight: 0,      // ALD 21/06/2026 - MẶC ĐỊNH 0 (TẮT): Relight LoRA làm TỐI NỀN + DỒN SÁNG vào mẫu = cháy da. User chốt tắt. Cần relight (đèn phức tạp) thì tự kéo lên 0.1-0.3.
  matchRef: false,     // ALD 27/06/2026 - RAW Wan mặc định: tắt ColorMatch để tránh flash/đổi màu theo thời gian.
  matchRefStrength: 0,
  matchRefMethod: 'reinhard',  // ALD 27/06/2026 - reinhard giữ màu tự nhiên hơn mkl cho motion close-up.
  warmth: 0,
  brightCap: 1.0,
  skipFirstFrames: 0,
  driverStartSec: 0,  // ALD 24/06/2026 - cắt driver trước khi Wan Animate (multi-outfit: ref i = đoạn i).
  driverDurSec: 0,    // 0/trống = không giới hạn duration.
  motionSpeedup: 0,    // ALD 12/06/2026 - % tăng tốc chuyển động driver (0 = giữ nguyên)
  ...props.config,
  // ALD 09/07/2026 - gắn cứng driver (sau spread): node cũ lỡ lưu faceSource='ref' từ UI đã gỡ → ép về driver.
  faceSource: 'driver',
  // ALD 11/06/2026 - HF đã gỡ: workflow cũ lỡ lưu provider 'huggingface' → tự lành về self-host (sau spread).
  // ALD 10/07/2026 - 'dashscope' = wan2.2-animate-move/mix cloud (giữ nguyên nếu đã lưu).
  provider: String(props.config?.provider || '').toLowerCase() === 'huggingface' ? 'qwen' : (props.config?.provider || 'qwen'),
  dashscopeModel: props.config?.dashscopeModel || 'wan2.2-animate-move',
  dashscopeQuality: props.config?.dashscopeQuality || 'wan-std',
  dashscopeResolution: props.config?.dashscopeResolution || '720P',
  dashscopePrompt: props.config?.dashscopePrompt || '',
  // ALD 13/07/2026 - Chỉ dùng LightX2V 4 bước. Profile Natural đã gỡ; node cũ tự lành về fast.
  renderProfile: 'fast',
  render_profile: 'fast',
  hq: false,
  // ALD 21/06/2026 - fps THEO PRESET (30 cho preset thường, 60 cho preset -60fps). UI không còn nút fps → fps
  // luôn bám preset đang chọn (ghi đè fps cũ đã lưu) → run_motion RIFE ×2 (30) / ×4 (60).
  fps: _presetFps(props.config?.preset),
})
// ALD 09/07/2026 - node cũ lưu extraPositive RỖNG (spread đè mất default) → tự điền prompt viết sẵn khi mở Inspector.
// ALD 12/07/2026 - node cũ đang giữ NGUYÊN bản default phủ định → tự nâng cấp lên bản khẳng định (giữ prompt user tự sửa).
{
  const _cur = String(local.value.extraPositive || '').trim()
  if (!_cur || _OLD_DEFAULT_EXTRA_POSITIVES.includes(_cur)) local.value.extraPositive = DEFAULT_EXTRA_POSITIVE
}

const audioMode = computed(() => {
  const mode = String(local.value.audioMode || '').toLowerCase()
  if (['original', 'replacement', 'silent'].includes(mode)) return mode
  return local.value.audioPassthrough === false ? 'replacement' : 'original'
})

function setAudioMode(mode) {
  local.value.audioMode = mode
  local.value.audioPassthrough = mode === 'original'
}

// ALD 27/06/2026 - Toggle ẩn 2 field "Driver start/duration" (đa số job dùng CẢ video → dễ hiểu lầm là bắt buộc).
// Khởi tạo theo config có sẵn: node multi-outfit đã set start/dur → bật; node thường (0/0) → tắt. Tắt = reset về 0.
// ALD 30/06/2026 - Custom segment: chọn cứng chất lượng (540p nhẹ/nhanh · 720p nét/chậm-offload), thời lượng tối đa 10s.
const CUSTOM_QUALITIES = [
  { id: '540p', label: '540p', hint: 'Nhẹ · nhanh' },
  { id: '720p', label: '720p', hint: 'Nét hơn · chậm' }
]
function clampCustomDur() {
  const d = Number(local.value.driverDurSec)
  local.value.driverDurSec = (!Number.isFinite(d) || d <= 0) ? 5 : Math.max(0.5, Math.min(15, d))  // ALD 09/07 - chốt 15s (user)
}
const useDriverSegment = ref(!!(local.value.driverStartSec || local.value.driverDurSec))
// ALD 11/07/2026 - true = còn bám ngón tay driver; false = bỏ (chống ngón dài). Node cũ chưa có field → mặc định true.
const useDriverHands = computed(() => local.value.driverHands !== false)
function toggleDriverSegment() {
  useDriverSegment.value = !useDriverSegment.value
  if (useDriverSegment.value) {
    if (!local.value.quality) local.value.quality = '540p'
    if (!(Number(local.value.driverDurSec) > 0)) local.value.driverDurSec = 5
    clampCustomDur()
  } else {
    local.value.driverStartSec = 0; local.value.driverDurSec = 0; local.value.quality = ''
  }
}

watch(local, (v) => emit('update:config', { ...v }), { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) {
    local.value = {
      ...local.value,
      ...v,
      renderProfile: 'fast',
      render_profile: 'fast',
      hq: false,
      fps: _presetFps(v.preset || local.value.preset),
    }
    useDriverSegment.value = !!(local.value.driverStartSec || local.value.driverDurSec)
  }
})
// fps bám preset: đổi preset → cập nhật fps (30/60) cho khớp.
watch(() => local.value.preset, (id) => { local.value.fps = _presetFps(id) })

const currentPreset = computed(() => PRESETS.find((p) => p.id === local.value.preset))
</script>
