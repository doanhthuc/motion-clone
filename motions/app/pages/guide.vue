<template>
  <!-- #region ALD 06/07/2026 - Trang Hướng dẫn: tài liệu sử dụng các node + thông số khuyến nghị.
       TOC trái (sticky) + nội dung phải. Nội dung viết tay theo catalog node trong editor. -->
  <div class="flex-1 min-h-0 flex px-3 sm:px-6 pt-1 pb-3 gap-4 overflow-hidden">
    <!-- TOC -->
    <aside class="hidden lg:flex w-60 flex-shrink-0 flex-col lq-panel p-3 overflow-y-auto">
      <div class="lq-search mb-3">
        <i class="bi bi-search" />
        <input v-model="q" type="text" placeholder="Tìm node…" >
      </div>
      <nav class="space-y-3">
        <div v-for="sec in filteredSections" :key="sec.id">
          <p class="lq-sub px-2 mb-1">{{ sec.label }}</p>
          <a
            v-for="n in sec.nodes" :key="n.id"
            :href="`#node-${n.id}`"
            class="flex items-center gap-2 px-2 py-1.5 rounded-lg text-[12.5px] font-medium text-gray-600 hover:bg-white/[0.04] hover:text-gray-900 transition-colors"
            @click.prevent="scrollTo(n.id)"
          >
            <span class="flex h-5 w-5 items-center justify-center rounded-md text-[10px] flex-shrink-0" :style="{ background: n.soft, color: n.color }">
              <i :class="['bi', n.icon]" />
            </span>
            <span class="truncate">{{ n.name }}</span>
          </a>
        </div>
      </nav>
    </aside>

    <!-- Nội dung -->
    <main ref="contentRef" class="flex-1 min-w-0 overflow-y-auto scroll-smooth">
      <div class="max-w-3xl mx-auto space-y-6 pb-10">
        <!-- Hero -->
        <header class="lq-panel p-6 sm:p-8">
          <p class="lq-sub mb-2">Motions · AI Studio</p>
          <h1 class="text-2xl font-semibold tracking-tight text-gray-900">Hướng dẫn sử dụng Workflow</h1>
          <p class="text-sm text-gray-500 mt-2 leading-relaxed max-w-xl">
            Workflow là một chuỗi node nối với nhau: <b class="text-gray-700">Input</b> đưa dữ liệu vào →
            các node <b class="text-gray-700">xử lý</b> (tạo ảnh, motion, lồng tiếng…) → <b class="text-gray-700">Output</b> trả kết quả.
            Kéo node từ panel trái vào canvas, nối cổng ra → cổng vào, bấm <b class="text-gray-700">Chạy workflow</b>.
          </p>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-5">
            <div v-for="s in QUICK_STEPS" :key="s.n" class="lq-card !rounded-xl p-3.5">
              <span class="flex h-7 w-7 items-center justify-center rounded-full bg-primary text-white text-xs font-semibold">{{ s.n }}</span>
              <p class="text-[13px] font-medium text-gray-900 mt-2">{{ s.t }}</p>
              <p class="text-[11.5px] text-gray-500 mt-0.5 leading-snug">{{ s.d }}</p>
            </div>
          </div>
        </header>

        <!-- Mẹo chung -->
        <section class="lq-panel p-5 sm:p-6">
          <h2 class="text-base font-semibold text-gray-900 flex items-center gap-2"><i class="bi bi-lightbulb text-amber-500" /> Nguyên tắc nhanh</h2>
          <ul class="mt-3 space-y-2">
            <li v-for="(tip, i) in GENERAL_TIPS" :key="i" class="flex items-start gap-2 text-[13px] text-gray-600 leading-relaxed">
              <i class="bi bi-check-circle-fill text-emerald-500 mt-0.5 flex-shrink-0" />
              <span v-html="tip" />
            </li>
          </ul>
        </section>

        <!-- Node docs theo nhóm -->
        <template v-for="sec in filteredSections" :key="sec.id">
          <div class="flex items-center gap-3 pt-2">
            <h2 class="text-lg font-semibold tracking-tight text-gray-900 whitespace-nowrap">{{ sec.label }}</h2>
            <div class="h-px flex-1 bg-white/[0.07]" />
          </div>

          <article
            v-for="n in sec.nodes" :key="n.id"
            :id="`node-${n.id}`"
            class="lq-panel p-5 sm:p-6 scroll-mt-4"
          >
            <div class="flex items-start gap-3.5">
              <span class="flex h-11 w-11 items-center justify-center rounded-xl text-lg flex-shrink-0" :style="{ background: n.soft, color: n.color }">
                <i :class="['bi', n.icon]" />
              </span>
              <div class="min-w-0 flex-1">
                <h3 class="text-[15px] font-semibold text-gray-900">{{ n.name }}</h3>
                <p class="text-[13px] text-gray-500 mt-0.5 leading-relaxed">{{ n.what }}</p>
              </div>
            </div>

            <!-- Cổng vào/ra -->
            <div v-if="n.io" class="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-2">
              <div class="lq-card !rounded-xl p-3">
                <p class="lq-sub mb-1.5"><i class="bi bi-box-arrow-in-right me-1" />Đầu vào</p>
                <p class="text-[12.5px] text-gray-600 leading-relaxed">{{ n.io.in }}</p>
              </div>
              <div class="lq-card !rounded-xl p-3">
                <p class="lq-sub mb-1.5"><i class="bi bi-box-arrow-right me-1" />Đầu ra</p>
                <p class="text-[12.5px] text-gray-600 leading-relaxed">{{ n.io.out }}</p>
              </div>
            </div>

            <!-- Thông số -->
            <div v-if="n.params?.length" class="mt-4">
              <p class="lq-sub mb-2">Thông số chính</p>
              <div class="lq-card !rounded-xl divide-y divide-white/[0.05] overflow-hidden">
                <div v-for="p in n.params" :key="p.name" class="flex flex-col sm:flex-row sm:items-baseline gap-1 sm:gap-4 px-3.5 py-2.5">
                  <span class="w-44 flex-shrink-0 text-[12.5px] font-semibold text-gray-900">{{ p.name }}</span>
                  <span class="flex-1 text-[12.5px] text-gray-600 leading-relaxed">
                    {{ p.desc }}
                    <span v-if="p.rec" class="lq-chip lq-chip--blue ml-1 !text-[10px] align-middle">Nên dùng: {{ p.rec }}</span>
                  </span>
                </div>
              </div>
            </div>

            <!-- Tips -->
            <div v-if="n.tips?.length" class="mt-4 apl-info-card !text-[12.5px]">
              <p class="font-semibold text-gray-800 mb-1.5"><i class="bi bi-stars me-1 text-primary" />Mẹo</p>
              <ul class="space-y-1 list-disc pl-4">
                <li v-for="(t, i) in n.tips" :key="i" v-html="t" />
              </ul>
            </div>
          </article>
        </template>

        <p v-if="!filteredSections.length" class="text-center text-sm text-gray-400 py-10">Không có node khớp "{{ q }}"</p>
      </div>
    </main>
  </div>
  <!-- #endregion -->
</template>

<script setup>
definePageMeta({ middleware: 'auth', title: 'Hướng dẫn', subtitle: 'Cách dùng các node + thông số khuyến nghị' })
useHead({ title: 'Hướng dẫn — Motions' })

const q = ref('')
const contentRef = ref(null)

function scrollTo(id) {
  document.getElementById(`node-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const QUICK_STEPS = [
  { n: 1, t: 'Kéo node vào canvas', d: 'Chọn từ panel trái, kéo thả vào giữa màn hình.' },
  { n: 2, t: 'Nối cổng & cấu hình', d: 'Kéo từ cổng ra → cổng vào. Click node để mở Inspector bên phải.' },
  { n: 3, t: 'Chạy & theo dõi', d: 'Bấm "Chạy workflow" — tiến độ realtime từng node, kết quả ở Output.' }
]

const GENERAL_TIPS = [
  'Mọi workflow nên bắt đầu bằng node <b>Input</b> và kết thúc bằng node <b>Output</b> — thiếu Output sẽ không nhận được kết quả.',
  'GPU dùng chung cho nhiều dịch vụ — job đầu tiên sau thời gian nghỉ có thể <b>chậm hơn (cold-start)</b>, các job sau nhanh hơn.',
  'Video motion nên giữ <b>≤ 10 giây / đoạn</b>; dài hơn hãy cắt thành nhiều node rồi <b>Ghép cảnh</b>.',
  'Prompt cho node video (Wan / LTX) nên viết bằng <b>tiếng Anh</b> — model hiểu tốt hơn hẳn.',
  'Lưu workflow (⌘S) trước khi chạy; mỗi lần chạy tạo 1 tab phiên riêng, xem lại được lịch sử.'
]

// ── Tài liệu node — đồng bộ catalog trong editor ─────────────────────────────
const SECTIONS = [
  {
    id: 'io', label: 'Nguồn / Kết quả',
    nodes: [
      {
        id: 'input-text', name: 'Input Text', icon: 'bi-chat-left-text', color: '#34C759', soft: 'rgba(52,199,89,0.15)',
        what: 'Đưa văn bản vào workflow — nhập trực tiếp, lấy từ URL, hoặc điền lúc chạy (session).',
        io: { in: 'Không có — đây là node nguồn.', out: 'Chuỗi text cho node phía sau (prompt, lời thoại, caption…).' },
        params: [
          { name: 'Nguồn', desc: 'Session = người chạy nhập lúc chạy · Static = cố định · URL = tải từ link.', rec: 'Session' },
          { name: 'Field', desc: 'Tên trường khi dùng session — hiện thành ô nhập khi chạy / lên lịch đăng bài.' }
        ],
        tips: ['Dùng <b>Session</b> khi muốn workflow tái sử dụng nhiều lần với nội dung khác nhau.']
      },
      {
        id: 'input-image', name: 'Input Image', icon: 'bi-image', color: '#34C759', soft: 'rgba(52,199,89,0.15)',
        what: 'Đưa ảnh vào workflow — upload từ máy hoặc dán URL.',
        io: { in: 'Không có.', out: 'Ảnh cho các node Create Image / Try-on / Motion Transfer…' },
        params: [
          { name: 'Nguồn ảnh', desc: 'Upload device / URL / session (người chạy tự chọn ảnh).', rec: 'Upload' }
        ],
        tips: ['Ảnh nhân vật nên <b>rõ mặt, đủ sáng, đứng thẳng</b> — chất lượng ảnh vào quyết định chất lượng video ra.']
      },
      {
        id: 'input-video', name: 'Input Video', icon: 'bi-film', color: '#34C759', soft: 'rgba(52,199,89,0.15)',
        what: 'Đưa video vào workflow — thường là video motion (điệu nhảy, cử động mẫu).',
        io: { in: 'Không có.', out: 'Video cho Motion Transfer / Phụ đề / Lồng tiếng…' },
        tips: ['Video motion nên quay <b>dọc 9:16, 1 người, toàn thân</b>, nền càng gọn càng tốt.']
      },
      {
        id: 'input-audio', name: 'Input Audio', icon: 'bi-music-note-beamed', color: '#34C759', soft: 'rgba(52,199,89,0.15)',
        what: 'Đưa audio (MP3/WAV/M4A) vào — nhạc nền hoặc giọng thay thế. Chọn được từ Audio Library.',
        io: { in: 'Không có.', out: 'Audio nối vào cổng "audio" của node video.' },
        tips: ['File hay dùng nên upload vào <b>Audio Library</b> một lần, các workflow sau chọn lại từ thư viện.']
      },
      {
        id: 'input-file', name: 'Input File', icon: 'bi-file-earmark', color: '#34C759', soft: 'rgba(52,199,89,0.15)',
        what: 'File tổng quát (PDF/ZIP…) cho các node xử lý file hoặc HTTP.',
        io: { in: 'Không có.', out: 'File thô cho node phía sau.' }
      },
      {
        id: 'output', name: 'Output', icon: 'bi-box-arrow-right', color: '#8E8E93', soft: 'rgba(255,255,255,0.08)',
        what: 'Điểm kết thúc — gom kết quả trả về cho người dùng / API / Social Management.',
        io: { in: 'Bất kỳ node nào (video, ảnh, text).', out: 'Kết quả cuối cùng của workflow.' },
        tips: ['Output có video/ảnh sẽ tự xuất hiện ở <b>Social Management → Tạo bài đăng</b>.']
      }
    ]
  },
  {
    id: 'image', label: 'Ảnh',
    nodes: [
      {
        id: 'create-image', name: 'Create Image', icon: 'bi-images', color: '#AF52DE', soft: 'rgba(175,82,222,0.16)',
        what: 'Tạo ảnh mới từ prompt + 1–3 ảnh tham chiếu (Qwen-Edit / Gemini). ETA ~30 giây.',
        io: { in: '1–3 ảnh tham chiếu + prompt.', out: '1 ảnh mới.' },
        params: [
          { name: 'Engine', desc: 'Qwen-Edit (self-host, miễn phí) hoặc Gemini (cần API Key).', rec: 'Qwen-Edit' },
          { name: 'Prompt', desc: 'Mô tả ảnh muốn tạo — càng cụ thể bối cảnh/ánh sáng càng tốt.' }
        ],
        tips: ['Muốn giữ nhận dạng nhân vật, luôn nối kèm <b>ảnh tham chiếu</b> thay vì chỉ dùng prompt.']
      },
      {
        id: 'edit-image', name: 'Sửa ảnh', icon: 'bi-pencil-square', color: '#5AC8FA', soft: 'rgba(90,200,250,0.15)',
        what: 'Sửa từng ảnh trong list theo mô tả, giữ nguyên bố cục và nhân dạng. Mỗi ảnh tối đa 5 version, hiện dần khi xong.',
        io: { in: 'List ảnh + mô tả chỉnh sửa.', out: 'List ảnh đã sửa.' },
        tips: ['Chế độ <b>GHÉP</b> thay được node "Đặt sản phẩm" cũ — ghép sản phẩm vào ảnh người mẫu.']
      },
      {
        id: 'cast-model', name: 'Tuyển mẫu (kho)', icon: 'bi-people-fill', color: '#0A84FF', soft: 'rgba(10,132,255,0.15)',
        what: 'Khi chỉ có ảnh sản phẩm: tự chọn 1 người mẫu từ kho (Settings → Model mẫu) theo giới tính + độ tuổi, dùng cố định cho cả phim.',
        io: { in: 'Ảnh sản phẩm.', out: 'Ảnh người mẫu đã chọn → nối vào Sửa ảnh (ghép) / Try-on.' },
        params: [
          { name: 'Giới tính / Độ tuổi', desc: 'Bộ lọc chọn mẫu từ kho.' }
        ]
      },
      {
        id: 'tryon', name: 'Try-on', icon: 'bi-person-vcard', color: '#FF9500', soft: 'rgba(255,149,0,0.15)',
        what: 'Thay trang phục: ảnh người mẫu + ảnh sản phẩm → ảnh người mẫu mặc sản phẩm. ETA ~30 giây.',
        io: { in: 'Cổng model (người) + cổng product (đồ). Tuỳ chọn cổng background.', out: '1 ảnh đã thay đồ.' },
        tips: ['Ảnh sản phẩm nên là <b>ảnh phẳng hoặc trên nền trắng</b> để tách đồ chính xác.']
      }
    ]
  },
  {
    id: 'video', label: 'Video từ ảnh / prompt',
    nodes: [
      {
        id: 'motion', name: 'Motion Transfer', icon: 'bi-film', color: '#FF2D55', soft: 'rgba(255,45,85,0.15)',
        what: 'Wan 2.2 Animate — chuyển động từ video mẫu sang nhân vật trong ảnh: ảnh đứng yên "nhảy" theo video motion.',
        io: { in: 'Cổng image (ảnh nhân vật) + cổng video (motion). Tuỳ chọn audio (thay âm).', out: 'Video nhân vật chuyển động.' },
        params: [
          { name: 'Thời lượng video', desc: 'Toggle BẬT = tự chọn đoạn driver (bắt đầu + số giây, tối đa 10s). TẮT = dùng preset.', rec: 'Preset' },
          { name: 'Chất lượng', desc: '540p nhẹ & ổn định · 720p nét hơn nhưng chậm hơn (offload RAM).', rec: '540p khi thử, 720p bản final' },
          { name: 'Tỉ lệ video', desc: '9:16 Reels/TikTok · 16:9 YouTube · 1:1 / 3:4 / 4:3 / 21:9.', rec: '9:16' },
          { name: 'Âm thanh', desc: 'Âm gốc video · Âm thay thế (nối file vào cổng audio) · Im lặng.' },
          { name: 'Chỉnh màu hậu kỳ', desc: 'Mặc định TẮT để giữ output Wan nguyên thủy, tránh flash/đổi màu.' }
        ],
        tips: [
          'Mỗi lần chạy lấy <b>1 đoạn ≤10s</b> của driver — video dài hãy dùng nhiều node Motion rồi <b>Ghép cảnh</b>.',
          'Nhân vật trong ảnh nên có <b>tư thế gần giống frame đầu</b> của video motion để chuyển cảnh mượt.'
        ]
      },
      {
        id: 'teen-flycam', name: 'Teen Flycam', icon: 'bi-camera-video', color: '#FF2D55', soft: 'rgba(255,45,85,0.15)',
        what: '1 ảnh người mẫu → video social 10 giây / 5 shot, góc máy flycam + pose tự nhiên. Preset trọn gói, gần như không cần chỉnh.',
        io: { in: '1 ảnh người mẫu.', out: 'Video 10s hoàn chỉnh.' },
        tips: ['Node "một chạm" — phù hợp làm content nhanh hằng ngày kết hợp <b>lịch đăng tự động</b> ở Social.']
      },
      {
        id: 'ss', name: 'LoRA (LTX-2.3)', icon: 'bi-film', color: '#5856D6', soft: 'rgba(88,86,214,0.16)',
        what: 'Engine LTX-2.3 + LoRA custom. 3 chế độ: I2V (ảnh→video), T2V (text→video), V2V (video→restyle).',
        io: { in: 'Ảnh hoặc video hoặc chỉ prompt — tuỳ mode.', out: 'Video ngắn.' },
        params: [
          { name: 'Mode', desc: 'I2V / T2V / V2V — chọn trong Inspector.', rec: 'I2V' },
          { name: 'LoRA', desc: 'Model LoRA tự train (quản lý ở Settings → Models AI).' }
        ]
      },
      {
        id: 'wan-i2v', name: 'Ảnh → Video', icon: 'bi-camera-reels', color: '#FF2D55', soft: 'rgba(255,45,85,0.15)',
        what: 'Nối 1 ảnh + prompt → video chuyển động. Provider: Wan 2.1/2.2 self-host (miễn phí) hoặc DashScope cloud (happyhorse / wan2.7-i2v — có audio + ảnh cuối, cần API key). Hợp time-lapse, cảnh vật, BĐS.',
        io: { in: '1 ảnh + prompt (EN). wan2.7: thêm audio (opt).', out: 'Video ngắn.' },
        tips: ['Prompt <b>bắt buộc tiếng Anh</b>, mô tả chuyển động camera: "slow aerial zoom out, golden hour…".', 'Chọn provider DashScope + model <b>wan2.7-i2v</b> → node hiện cổng Audio: nối wav/mp3 2-30s → video diễn/nhép theo audio.']
      },
      {
        id: 'text-to-video', name: 'Text → Video', icon: 'bi-camera-reels', color: '#FF2D55', soft: 'rgba(255,45,85,0.15)',
        what: 'Chỉ cần prompt → video ngắn (Wan 2.2 T2V / LTX). Không cần ảnh đầu vào.',
        io: { in: 'Prompt (EN).', out: 'Video ngắn.' }
      }
    ]
  },
  {
    id: 'talk', label: 'Người nói (lip-sync)',
    nodes: [
      {
        id: 'talk', name: 'Nói (lip-sync)', icon: 'bi-mic-fill', color: '#34C759', soft: 'rgba(52,199,89,0.15)',
        what: 'MultiTalk — ảnh nhân vật + câu thoại → video nhân vật NÓI, nhép miệng đúng khẩu hình tiếng Việt.',
        io: { in: 'Ảnh nhân vật + text lời thoại.', out: 'Video nói chuyện.' },
        params: [
          { name: 'Giọng', desc: 'Nam / Nữ có sẵn, hoặc giọng clone từ Settings → Giọng nói.', rec: 'Giọng thư viện' },
          { name: 'Lời thoại', desc: 'Text tiếng Việt — câu ngắn, có dấu câu để ngắt nghỉ tự nhiên.' }
        ],
        tips: ['Thêm giọng riêng: <b>Settings → Giọng nói</b>, upload mẫu 5–20s nói rõ, ít vang.']
      },
      {
        id: 'voiceover', name: 'Lồng tiếng (đọc mô tả)', icon: 'bi-soundwave', color: '#34C759', soft: 'rgba(52,199,89,0.15)',
        what: 'Ghép giọng đọc tiếng Việt lên 1 clip có sẵn, giữ nguyên hình và độ dài. Không cần khuôn mặt.',
        io: { in: '1 clip video + lời thuyết minh + giọng.', out: 'Video đã lồng tiếng.' },
        tips: ['Khác node Nói: <b>không lip-sync</b> — dùng cho video sản phẩm, phong cảnh, review.']
      }
    ]
  },
  {
    id: 'film', label: 'Dựng phim / ghép',
    nodes: [
      {
        id: 'concat', name: 'Ghép cảnh', icon: 'bi-collection-play-fill', color: '#5856D6', soft: 'rgba(88,86,214,0.16)',
        what: 'Ghép ≥2 clip thành 1 video, giữ tiếng từng cảnh. Nối cổng clip1, clip2… theo thứ tự.',
        io: { in: 'clip1, clip2, … (từ Motion / Nói / LoRA…). Tuỳ chọn audio nền.', out: '1 video hoàn chỉnh.' },
        tips: ['Đây là node chốt của phim dài: nhiều đoạn ≤10s → Ghép cảnh → <b>Nâng chất lượng</b> → Output.']
      },
      {
        id: 'subtitle', name: 'Phụ đề + Dịch', icon: 'bi-badge-cc', color: '#FF9500', soft: 'rgba(255,149,0,0.15)',
        what: 'Nhận lời thoại (Whisper) + dịch. 3 chế độ: cháy phụ đề (giữ tiếng gốc) / lồng tiếng Việt (thay giọng) / cả hai.',
        io: { in: '1 video có lời thoại.', out: 'Video có phụ đề / đã lồng tiếng.' },
        params: [
          { name: 'Chế độ', desc: 'Phụ đề · Lồng tiếng · Cả hai.', rec: 'Phụ đề' }
        ]
      },
      {
        id: 'enhance', name: 'Nâng chất lượng', icon: 'bi-badge-hd', color: '#FF9500', soft: 'rgba(255,149,0,0.15)',
        what: 'Upscale video ESRGAN ×4 lên 1080p/2K/4K + nội suy fps (RIFE) tuỳ chọn. Tự xả GPU/RAM trước khi chạy.',
        io: { in: '1 video (thường sau Motion / Ghép cảnh).', out: 'Video nét hơn.' },
        params: [
          { name: 'Độ phân giải', desc: '1080p / 2K / 4K.', rec: '1080p cho social' },
          { name: 'Nội suy fps', desc: 'RIFE làm mượt chuyển động — bật khi video giật.' }
        ],
        tips: ['Đặt <b>cuối chuỗi</b> (sau Ghép cảnh) — upscale 1 lần thay vì từng đoạn.']
      }
    ]
  },
  {
    id: 'tools', label: 'Tiện ích / Luồng',
    nodes: [
      {
        id: 'http', name: 'HTTP', icon: 'bi-cloud-arrow-up-fill', color: '#5856D6', soft: '#ECEBFB',
        what: 'Gọi REST API ngoài — gửi kết quả sang hệ thống khác hoặc lấy dữ liệu về.',
        io: { in: 'Dữ liệu bất kỳ (đưa vào body/query).', out: 'Response từ API.' }
      },
      {
        id: 'api-key', name: 'API Key', icon: 'bi-key-fill', color: '#FFCC00', soft: 'rgba(255,204,0,0.15)',
        what: 'Khai báo API key (Gemini / Veo / custom). Nối vào cổng API Key của node đích, hoặc đặt rời trên canvas = tự phân bổ cho mọi node cùng provider.',
        io: { in: 'Không có.', out: 'Key cho các node dùng provider tương ứng.' },
        tips: ['Node engine <b>Self-host (Qwen/Wan/LTX)</b> không cần key — chỉ Gemini/Veo mới cần.']
      },
      {
        id: 'condition', name: 'Condition', icon: 'bi-shuffle', color: '#FF9500', soft: 'rgba(255,149,0,0.15)',
        what: 'Rẽ nhánh if-else theo expression — đúng đi nhánh 1, sai đi nhánh 2.',
        io: { in: 'Dữ liệu cần kiểm tra.', out: '2 nhánh true / false.' }
      },
      {
        id: 'validate', name: 'Validate', icon: 'bi-check2-square', color: '#7CDDAA', soft: 'rgba(52,199,89,0.18)',
        what: 'Kiểm tra field bắt buộc + phép tính sau LLM — chặn kết quả sai trước khi trả về.',
        io: { in: 'Output LLM / JSON.', out: 'Dữ liệu đã kiểm tra (fail → báo lỗi).' }
      },
      {
        id: 'debug', name: 'Debug', icon: 'bi-bug-fill', color: '#FF9500', soft: 'rgba(255,149,0,0.15)',
        what: 'Pass-through + log preview giữa các bước — xem dữ liệu đang chảy qua mà không đổi gì.',
        io: { in: 'Bất kỳ.', out: 'Y nguyên đầu vào.' },
        tips: ['Chèn tạm giữa 2 node khi kết quả không như mong đợi — xem log rồi gỡ ra.']
      },
      {
        id: 'gpu-warmup', name: 'GPU Warmup / Free', icon: 'bi-lightning-charge-fill', color: '#34C759', soft: 'rgba(52,199,89,0.15)',
        what: 'Warmup chuẩn bị vRAM trước bước nặng; Free đợi vRAM giải phóng sau đó. Dùng khi workflow chạy nhiều engine liên tiếp.',
        io: { in: 'Nối tiếp trong chuỗi.', out: 'Pass-through.' }
      },
      {
        id: 'workflow', name: 'Workflow (nested)', icon: 'bi-diagram-3-fill', color: '#FF2D55', soft: 'rgba(255,45,85,0.15)',
        what: 'Gọi 1 workflow khác như 1 node — tách quy trình lớn thành các khối tái sử dụng.',
        io: { in: 'Input của workflow con.', out: 'Output của workflow con.' }
      }
    ]
  }
]

const filteredSections = computed(() => {
  const query = q.value.trim().toLowerCase()
  if (!query) return SECTIONS
  return SECTIONS
    .map((s) => ({ ...s, nodes: s.nodes.filter((n) => (n.name + ' ' + n.what).toLowerCase().includes(query)) }))
    .filter((s) => s.nodes.length)
})
</script>
