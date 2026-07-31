<template>
  <!-- ALD 28/05/2026 - Create Image node inspector. Prompt tự do + 0..6 ảnh tham chiếu
       → 1..10 ảnh mới. 2 provider:
       - Qwen Edit (Qwen-Image-Edit-2509, default, GPU local, ~30s)
       - Gemini (Google Nano Banana, cần API key user) -->
  <div class="space-y-3">
    <div class="apl-info-card">
      <p class="font-semibold flex items-center gap-1.5">
        <i class="bi bi-info-circle-fill text-amber-500" />
        Create Image
      </p>
      <p class="text-[11px] opacity-70 mt-1">
        Nhập prompt mô tả ảnh. Ảnh tham chiếu là <b>tuỳ chọn</b>: không nối ảnh → dùng bộ model-standard mặc định; nối ảnh vào cổng bên trái → dùng ảnh của node làm tham chiếu.
      </p>
    </div>

    <!-- Prompt (field chính) -->
    <div>
      <div class="flex items-center justify-between gap-2">
        <label class="apl-fm-label flex items-center gap-1.5 !mb-0">
          Prompt <span class="text-rose-600">*</span>
        </label>
        <!-- #region ALD 13/06/2026 - Gạt Text / JSON cho ô prompt. JSON = dán cấu trúc (meta/characters/scene...),
             BE tự ghép thành prompt + lấy negative + tỉ lệ. -->
        <div class="inline-flex rounded-lg bg-gray-100 p-0.5 text-[11px] font-semibold select-none">
          <button
            type="button"
            :class="['px-2 py-1 rounded-md transition', local.promptMode !== 'json' ? 'bg-gray-50 shadow text-gray-800' : 'text-gray-500']"
            @click="local.promptMode = 'text'"
          >Text</button>
          <button
            type="button"
            :class="['px-2 py-1 rounded-md transition', local.promptMode === 'json' ? 'bg-gray-50 shadow text-violet-700' : 'text-gray-500']"
            @click="local.promptMode = 'json'"
          >JSON</button>
        </div>
        <!-- #endregion -->
      </div>

      <!-- TEXT mode -->
      <template v-if="local.promptMode !== 'json'">
        <textarea
          v-model="local.prompt"
          rows="4"
          spellcheck="false"
          :placeholder="promptPlaceholder"
          class="apl-fm-input mt-1 text-xs leading-relaxed"
          style="height:auto;min-height:92px;padding:8px 10px;line-height:1.5;resize:vertical"
        />
        <p class="apl-fm-hint">
          <template v-if="inputCount === 0">Mô tả ảnh muốn tạo (text→image). Càng chi tiết càng tốt.</template>
          <template v-else-if="inputCount === 1">Mô tả cách <b>chỉnh Ảnh gốc</b>. Nói rõ giữ/đổi gì để giữ identity.</template>
          <template v-else><b>Ảnh 1 = Ảnh mẫu</b> (tham chiếu phong cách/trang phục). Mô tả trong prompt cách áp Ảnh mẫu lên (các) đối tượng ở Ảnh 2{{ inputCount >= 3 ? ', Ảnh 3…' : '' }}.</template>
        </p>
      </template>

      <!-- JSON mode -->
      <template v-else>
        <textarea
          v-model="local.promptJson"
          rows="12"
          spellcheck="false"
          placeholder='{
  "meta": { "aspect_ratio": "3:4", "style": "photorealistic..." },
  "characters": [ { "identity": "...", "hair": "...", "clothing": "...", "pose_action": "..." } ],
  "scene": { "background": "...", "lighting": "..." },
  "negative_prompt": "blurry, deformed, ..."
}'
          class="apl-fm-input mt-1 font-mono text-[11px] leading-relaxed"
          style="height:auto;min-height:220px;padding:8px 10px;line-height:1.5;resize:vertical"
        />
        <!-- ALD 14/06/2026 - JSON mode CHỈ dùng JSON (KHÔNG dùng ô Text). Lỗi cú pháp → box đỏ nổi bật. -->
        <div v-if="jsonError" class="mt-1.5 px-2 py-2 rounded-lg bg-rose-50 border border-rose-300 text-[11px] text-rose-700 flex items-start gap-1.5">
          <i class="bi bi-exclamation-triangle-fill mt-0.5 shrink-0" />
          <span><b>JSON LỖI cú pháp — phải sửa.</b> Ở chế độ JSON, ảnh chỉ render theo JSON này (<b>KHÔNG dùng ô Text</b>). Lỗi: {{ jsonError }}</span>
        </div>
        <p v-else class="apl-fm-hint">
          <i class="bi bi-check-circle-fill me-1 text-emerald-500" />JSON hợp lệ. Chỉ dùng JSON (bỏ ô Text). BE ghép <b>meta/characters/scene/cinematic</b> → prompt, lấy <b>negative_prompt</b> + <b>aspect_ratio</b>.
        </p>
      </template>
    </div>

    <!-- #region ALD 11/06/2026 - Negative prompt riêng: trước đây user dán khối negative vào ô Prompt → bị
         nhét vào POSITIVE, model vẽ ĐÚNG những thứ cần tránh (vd "braid, twin tails" → ra tóc tết).
         Worker merge field này TRƯỚC negative realism mặc định (chỉ áp dụng provider Qwen).
         ALD 13/06/2026 - Ẩn khi JSON mode: negative_prompt đã nằm trong JSON. -->
    <div v-if="local.promptMode !== 'json'">
      <label class="apl-fm-label">Negative prompt <span class="font-normal text-gray-400">(thứ KHÔNG muốn xuất hiện — tuỳ chọn)</span></label>
      <textarea
        v-model="local.negativePrompt"
        rows="2"
        spellcheck="false"
        placeholder="VD: long hair, ponytail, braid, tattoo, hat…"
        class="apl-fm-input mt-1 text-xs leading-relaxed"
        style="height:auto;min-height:56px;padding:8px 10px;line-height:1.5;resize:vertical"
      />
      <p class="apl-fm-hint">
        <i class="bi bi-exclamation-triangle me-1 text-amber-500" />Đừng dán danh sách "cần tránh" vào ô <b>Prompt</b> phía trên — model sẽ hiểu ngược là <b>muốn vẽ</b> chúng. Liệt kê ở đây. Áp dụng cho provider Qwen.
      </p>
    </div>
    <!-- #endregion -->

    <!-- Số cổng ảnh tham chiếu (động) — 0 = sinh chỉ từ prompt (text→image) -->
    <div>
      <label class="apl-fm-label">Số cổng ảnh tham chiếu</label>
      <div class="flex items-center gap-3 mt-1.5">
        <div class="inline-flex items-center rounded-lg border border-gray-200 bg-gray-50 shadow-sm overflow-hidden select-none">
          <button
            type="button"
            class="w-9 h-9 flex items-center justify-center text-gray-600 hover:bg-gray-100 active:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed transition"
            :disabled="inputCount <= 0"
            @click="local.inputCount = Math.max(0, inputCount - 1)"
          >
            <i class="bi bi-dash-lg" />
          </button>
          <span class="w-10 text-center text-sm font-bold tabular-nums border-x border-gray-200 self-stretch flex items-center justify-center">
            {{ inputCount }}
          </span>
          <button
            type="button"
            class="w-9 h-9 flex items-center justify-center text-gray-600 hover:bg-gray-100 active:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed transition"
            :disabled="inputCount >= 6"
            @click="local.inputCount = Math.min(6, inputCount + 1)"
          >
            <i class="bi bi-plus-lg" />
          </button>
        </div>
        <span class="apl-fm-hint flex-1 !mt-0">
          <template v-if="inputCount === 0"><b>0 ảnh</b> = prompt + model-standard mặc định. Cần dùng ảnh riêng thì bấm <b>+</b>.</template>
          <template v-else>Cổng <code class="text-[10px] bg-gray-100 px-1 rounded">{{ inputCount === 1 ? 'image1' : 'image1…image' + inputCount }}</code> bên trái node. Qwen-Edit tốt nhất với ≤3 ảnh.</template>
        </span>
      </div>
    </div>

    <!-- ALD 13/06/2026 - Cách dùng ảnh ref: Tham chiếu (sinh ảnh MỚI) vs Sửa ảnh (giữ nguyên, edit). Chỉ hiện khi CÓ ảnh. -->
    <div v-if="inputCount > 0">
      <label class="apl-fm-label">Cách dùng ảnh tham chiếu</label>
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button type="button" :class="['apl-q-tile', local.imageMode !== 'edit' && 'is-active']" @click="local.imageMode = 'reference'">
          <b>Tham chiếu</b>
        </button>
        <button type="button" :class="['apl-q-tile', local.imageMode === 'edit' && 'is-active']" @click="local.imageMode = 'edit'">
          <b>Sửa ảnh</b>
        </button>
      </div>
    </div>

    <!-- Tỉ lệ khung hình — ẩn khi JSON mode (lấy từ meta.aspect_ratio trong JSON). -->
    <div v-if="local.promptMode !== 'json'">
      <label class="apl-fm-label">Tỉ lệ khung hình</label>
      <div class="grid grid-cols-3 gap-1.5 mt-1.5">
        <button
          v-for="r in ASPECT_RATIOS"
          :key="r.id"
          type="button"
          :class="['apl-ar-tile', local.aspectRatio === r.id && 'is-active']"
          @click="local.aspectRatio = r.id"
        >
          <span class="apl-ar-box" :style="{ width: r.w + 'px', height: r.h + 'px' }" />
          <span class="apl-ar-label">{{ r.label }}</span>
        </button>
      </div>
      <p v-if="inputCount > 0 && local.aspectRatio === 'auto'" class="apl-fm-hint">Tự động = giữ tỉ lệ Ảnh gốc/Ảnh mẫu.</p>
    </div>

    <!-- #region ALD 10/06/2026 - Góc máy nhiếp ảnh (20 loại): id khớp CAMERA_ANGLES worker — ghép phrase EN vào prompt.
         ALD 13/06/2026 - Ẩn khi JSON mode: góc máy đã nằm trong JSON (cinematic.camera). -->
    <div v-if="local.promptMode !== 'json'">
      <label class="apl-fm-label">Góc máy <span class="font-normal text-gray-400">(nhiếp ảnh)</span></label>
      <select v-model="local.cameraAngle" class="apl-fm-input mt-1.5 w-full text-xs" style="height:34px;padding:0 10px">
        <option value="">Mặc định (theo prompt)</option>
        <optgroup v-for="g in CAMERA_ANGLE_GROUPS" :key="g.label" :label="g.label">
          <option v-for="a in g.items" :key="a.id" :value="a.id">{{ a.label }} — {{ a.hint }}</option>
        </optgroup>
      </select>
      <p v-if="local.cameraAngle" class="apl-fm-hint">
        <i class="bi bi-camera me-1" />{{ CAMERA_ANGLE_ALL.find(a => a.id === local.cameraAngle)?.hint }}.
        Cùng một chủ thể, đổi góc máy → đổi câu chuyện.
      </p>
    </div>
    <!-- #endregion -->

    <!-- Model standard preset (Model mẫu).
         ALD 14/06/2026 - Model mẫu = bốc ngẫu nhiên 1 ảnh model NGUYÊN làm ref danh tính (1-step). Ẩn khi provider=Gemini. -->
    <div v-if="local.provider === 'qwen'">
      <label class="apl-fm-label">Tiêu chuẩn model mặc định</label>
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button
          v-for="p in MODEL_STANDARD_PRESETS"
          :key="p.id"
          type="button"
          :class="['apl-q-tile', modelStandardPreset === p.id && 'is-active']"
          @click="setModelStandardPreset(p.id)"
        >
          <b>{{ p.label }}</b>
          <small>{{ p.hint }}</small>
        </button>
      </div>
      <!-- ALD 11/06/2026 - Độ tuổi: lọc pool "Model mẫu" (Cài đặt → Model mẫu). Chỉ áp khi chọn Nữ/Nam. -->
      <div v-if="modelStandardPreset === 'female' || modelStandardPreset === 'male'" class="mt-2">
        <label class="apl-fm-label">Độ tuổi mẫu</label>
        <div class="grid grid-cols-3 gap-1.5 mt-1.5">
          <button
            v-for="a in AGE_GROUPS"
            :key="a.id"
            type="button"
            :class="['apl-q-tile', ageGroup === a.id && 'is-active']"
            @click="local.age_group = a.id"
          >
            <b>{{ a.label }}</b>
          </button>
        </div>
      </div>
      <!-- ALD 14/06/2026 - GỠ toggle "Ghép mặt 2 bước": chế độ swap mặt gây lỗi mặt đôi. Model mẫu chỉ còn 1-step nguyên ảnh. -->
    </div>

    <!-- Chất lượng -->
    <div>
      <label class="apl-fm-label">Chất lượng</label>
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button
          v-for="q in QUALITIES"
          :key="q.id"
          type="button"
          :class="['apl-q-tile', local.quality === q.id && 'is-active']"
          @click="local.quality = q.id"
        >
          <b>{{ q.label }}</b>
          <small>{{ q.hint }}</small>
        </button>
      </div>
    </div>
    <!-- ALD 14/06/2026 - GỠ ô "Xử lý hậu kỳ" (postProcess): bỏ hẳn chức năng detailer hậu kỳ. -->

    <!-- #region ALD 14/06/2026 - Tinh chỉnh nhiều bước (ChatGPT-style): AI vision (local, FREE) soi ảnh vs prompt →
         tự sửa, 0-3 vòng. 0 = tắt. -->
    <div>
      <label class="apl-fm-label"><i class="bi bi-arrow-repeat me-1" />Tinh chỉnh nhiều bước</label>
      <div class="flex items-center gap-3 mt-1.5">
        <div class="inline-flex items-center rounded-lg border border-gray-200 bg-gray-50 shadow-sm overflow-hidden select-none">
          <button type="button" class="w-9 h-9 flex items-center justify-center text-gray-600 hover:bg-gray-100 active:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed transition" :disabled="(local.refineSteps || 0) <= 0" @click="local.refineSteps = Math.max(0, (local.refineSteps || 0) - 1)"><i class="bi bi-dash-lg" /></button>
          <span class="w-10 text-center text-sm font-bold tabular-nums border-x border-gray-200 self-stretch flex items-center justify-center">{{ local.refineSteps || 0 }}</span>
          <button type="button" class="w-9 h-9 flex items-center justify-center text-gray-600 hover:bg-gray-100 active:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed transition" :disabled="(local.refineSteps || 0) >= 3" @click="local.refineSteps = Math.min(3, (local.refineSteps || 0) + 1)"><i class="bi bi-plus-lg" /></button>
        </div>
        <span class="apl-fm-hint flex-1 !mt-0">{{ (local.refineSteps || 0) === 0 ? 'Tắt' : `${local.refineSteps} vòng — AI soi ảnh & tự sửa cho khớp prompt (chậm hơn)` }}</span>
      </div>
    </div>
    <!-- #endregion -->

    <!-- Số ảnh đầu ra -->
    <div>
      <label class="apl-fm-label">Số kết quả đầu ra</label>
      <div class="flex items-center gap-3 mt-1.5">
        <div class="inline-flex items-center rounded-lg border border-gray-200 bg-gray-50 shadow-sm overflow-hidden select-none">
          <button
            type="button"
            class="w-9 h-9 flex items-center justify-center text-gray-600 hover:bg-gray-100 active:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed transition"
            :disabled="outputCount <= 1"
            @click="local.outputCount = Math.max(1, outputCount - 1)"
          >
            <i class="bi bi-dash-lg" />
          </button>
          <span class="w-10 text-center text-sm font-bold tabular-nums border-x border-gray-200 self-stretch flex items-center justify-center">
            {{ outputCount }}
          </span>
          <button
            type="button"
            class="w-9 h-9 flex items-center justify-center text-gray-600 hover:bg-gray-100 active:bg-gray-200 disabled:opacity-30 disabled:cursor-not-allowed transition"
            :disabled="outputCount >= 10"
            @click="local.outputCount = Math.min(10, outputCount + 1)"
          >
            <i class="bi bi-plus-lg" />
          </button>
        </div>
        <span class="apl-fm-hint flex-1 !mt-0">
          Cùng prompt, worker render nhiều seed khác nhau. Ảnh đầu tiên vẫn dùng để nối sang node sau; toàn bộ ảnh nằm trong <code class="text-[10px] bg-gray-100 px-1 rounded">images[]</code>.
        </span>
      </div>
    </div>

    <!-- Provider selector -->
    <!-- ALD 11/06/2026 - provider HuggingFace ĐÃ GỠ theo yêu cầu user (fal chặn nội dung lingerie = use-case
         chính; không có motion-transfer). Backend còn nhánh HF ngủ đông — bật lại = thêm lại tile ở đây. -->
    <div>
      <label class="apl-fm-label">Provider</label>
      <div class="grid grid-cols-2 gap-1.5 mt-1.5">
        <button
          type="button"
          :class="['apl-fm-tile', local.provider === 'qwen' && 'is-active']"
          @click="local.provider = 'qwen'"
        >
          <i class="bi bi-hdd-stack text-base" />
          <span class="apl-fm-tile-label">Self-host</span>
        </button>
        <button
          v-if="isAdmin"
          type="button"
          :class="['apl-fm-tile', local.provider === 'gemini' && 'is-active']"
          @click="local.provider = 'gemini'"
        >
          <i class="bi bi-google text-base" />
          <span class="apl-fm-tile-label">Gemini</span>
        </button>
      </div>
      <p class="apl-fm-hint">
        <span v-if="local.provider === 'gemini'">
          <i class="bi bi-info-circle me-1" />Gọi Gemini Nano Banana (Google). Cần API key + billing.
        </span>
        <span v-else>
          <i class="bi bi-info-circle me-1" />Self-host local. Qwen-Edit sửa/ghép ảnh; Flux chỉ text→image thuần.
        </span>
      </p>
    </div>

    <!-- ALD 14/06/2026 - Model dropdown (icon + tên ngắn, không giải thích). -->
    <div v-if="local.provider === 'qwen'" class="relative">
      <label class="apl-fm-label">Model</label>
      <button type="button" class="apl-fm-input mt-1.5 w-full flex items-center justify-between gap-2 text-xs" @click="openModelDrop = openModelDrop === 'selfhost' ? '' : 'selfhost'">
        <span class="flex items-center gap-2"><i :class="['bi', currentSelfhostModel.icon, 'text-violet-500']" />{{ currentSelfhostModel.label }}</span>
        <i :class="['bi bi-chevron-down text-gray-400 transition-transform', openModelDrop === 'selfhost' && 'rotate-180']" />
      </button>
      <template v-if="openModelDrop === 'selfhost'">
        <div class="fixed inset-0 z-10" @click="openModelDrop = ''" />
        <div class="absolute z-20 mt-1 w-full bg-gray-50 border border-gray-200 rounded-lg shadow-lg overflow-hidden py-1">
          <button
            v-for="m in SELFHOST_MODELS" :key="m.id" type="button"
            :class="['w-full flex items-center gap-2 px-3 py-2 text-xs text-left hover:bg-gray-50 transition', local.model === m.id && 'bg-violet-50 text-violet-700 font-semibold']"
            @click="local.model = m.id; openModelDrop = ''"
          ><i :class="['bi', m.icon]" />{{ m.label }}</button>
        </div>
      </template>
      <p v-if="local.model && local.model.startsWith('flux-')" class="apl-fm-hint">
        Flux chỉ chạy khi <b>Số cổng ảnh tham chiếu = 0</b> và <b>Tiêu chuẩn model mặc định = Off</b>. Có ảnh ref/model mẫu thì phải dùng Qwen-Edit.
      </p>
    </div>

    <div v-if="local.provider === 'gemini' && isAdmin" class="relative">
      <label class="apl-fm-label">Model Gemini</label>
      <button type="button" class="apl-fm-input mt-1.5 w-full flex items-center justify-between gap-2 text-xs" @click="openModelDrop = openModelDrop === 'gemini' ? '' : 'gemini'">
        <span class="flex items-center gap-2"><i :class="['bi', currentGeminiModel.icon, 'text-amber-500']" />{{ currentGeminiModel.label }}</span>
        <i :class="['bi bi-chevron-down text-gray-400 transition-transform', openModelDrop === 'gemini' && 'rotate-180']" />
      </button>
      <template v-if="openModelDrop === 'gemini'">
        <div class="fixed inset-0 z-10" @click="openModelDrop = ''" />
        <div class="absolute z-20 mt-1 w-full bg-gray-50 border border-gray-200 rounded-lg shadow-lg overflow-hidden py-1">
          <button
            v-for="m in GEMINI_MODELS" :key="m.id" type="button"
            :class="['w-full flex items-center gap-2 px-3 py-2 text-xs text-left hover:bg-gray-50 transition', local.geminiModel === m.id && 'bg-amber-50 text-amber-700 font-semibold']"
            @click="local.geminiModel = m.id; openModelDrop = ''"
          ><i :class="['bi', m.icon]" />{{ m.label }}</button>
        </div>
      </template>
    </div>

    <!-- Gemini API key (chỉ khi provider=gemini). Non-admin: báo khoá, không cho nhập key. -->
    <div v-if="local.provider === 'gemini' && !isAdmin" class="apl-info-card !bg-rose-50 !border-rose-200 !text-rose-700">
      <p class="flex items-center gap-1.5"><i class="bi bi-lock-fill" /> Gemini chỉ dành cho admin. Chọn <b>Qwen Edit</b> để chạy.</p>
    </div>
    <div v-else-if="local.provider === 'gemini'">
      <div v-if="apiKeyAlreadySaved && !editingApiKey" class="flex items-center justify-between gap-2 p-2.5 rounded-lg border border-emerald-200 bg-emerald-50">
        <div class="flex items-center gap-2 min-w-0">
          <i class="bi bi-shield-check-fill text-emerald-600 text-base" />
          <div class="min-w-0">
            <div class="text-xs font-semibold text-emerald-900">Gemini API Key đã lưu</div>
            <div class="text-[10px] text-emerald-700 truncate">Key bảo mật ở server, sẵn sàng dùng cho mọi run</div>
          </div>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button
            type="button"
            class="text-[11px] font-semibold text-primary hover:underline whitespace-nowrap inline-flex items-center gap-1"
            @click="editingApiKey = true"
          >
            <i class="bi bi-pencil-square" /> Đổi
          </button>
          <button
            type="button"
            class="text-[11px] font-semibold text-rose-600 hover:underline whitespace-nowrap inline-flex items-center gap-1"
            @click="clearApiKey"
          >
            <i class="bi bi-trash" /> Xoá
          </button>
        </div>
      </div>

      <div v-else>
        <label class="apl-fm-label flex items-center gap-1.5">
          Gemini API Key
          <span v-if="!apiKeyAlreadySaved" class="text-[10px] font-normal text-gray-400">(tuỳ chọn)</span>
          <button
            v-if="apiKeyAlreadySaved"
            type="button"
            class="ms-auto text-[10px] text-gray-500 hover:underline"
            @click="cancelChangeApiKey"
          >
            Huỷ — giữ key cũ
          </button>
        </label>
        <input
          v-model="local.apiKey"
          type="password"
          autocomplete="off"
          spellcheck="false"
          placeholder="AIzaSy..."
          class="apl-fm-input mt-1 font-mono text-xs"
        />
        <p class="apl-fm-hint">
          <i class="bi bi-shield-lock me-1" />Để trống = dùng <b>key hệ thống</b> (admin đã cấu hình sẵn). Hoặc nhập key riêng (lưu server-side) — lấy free tại
          <a href="https://aistudio.google.com/apikey" target="_blank" class="text-primary hover:underline">aistudio.google.com/apikey</a>.
        </p>
      </div>
    </div>
  </div>
</template>

<script setup>
const props = defineProps({
  config: { type: Object, required: true },
  nodeType: { type: String, default: 'create-image' }
})
const emit = defineEmits(['update:config'])

const normalizeProvider = (p) => {
  const v = String(p || '').toLowerCase()
  // ALD 11/06/2026 - HF đã gỡ: workflow cũ lỡ lưu 'huggingface' tự lành về self-host qwen.
  return v === 'gemini' ? 'gemini' : 'qwen'
}

// ALD 05/06/2026 - Gemini chỉ dành cho admin (BE chặn run; FE ẩn tile + key field cho non-admin).
// Role nằm trong JWT claim (khớp settings.vue/layouts/default.vue) — KHÔNG phải auth.user.role.
const auth = useAuth()
const isAdmin = computed(() => {
  return decodeJwtPayload(auth.token.value)?.role === 'admin'
})

const clearedApiKey = ref(false)
const apiKeyAlreadySaved = computed(() => !clearedApiKey.value && Boolean(
  props.config?.__apiKey_isSet || props.config?.__geminiApiKey_isSet,
))

const editingApiKey = ref(false)
function cancelChangeApiKey() {
  editingApiKey.value = false
  local.value.apiKey = ''
}
// ALD 05/06/2026 - Xoá hẳn key đã lưu → emit cờ __apiKey_clear (BE để rỗng, không khôi phục key cũ).
function clearApiKey() {
  clearedApiKey.value = true
  editingApiKey.value = false
  local.value = { ...local.value, apiKey: '' }
}

// ALD 31/05/2026 - tỉ lệ khung (box w/h px chỉ để vẽ icon preview) + chất lượng (→ W/H ở worker)
const ASPECT_RATIOS = [
  { id: 'auto',  label: 'Tự động', w: 22, h: 22 },
  { id: '1:1',   label: '1:1',     w: 22, h: 22 },
  { id: '4:5',   label: '4:5',     w: 18, h: 22 },
  { id: '3:4',   label: '3:4',     w: 17, h: 22 },
  { id: '9:16',  label: '9:16',    w: 13, h: 22 },
  { id: '16:9',  label: '16:9',    w: 24, h: 14 },
]
const QUALITIES = [
  { id: 'standard', label: 'Tiêu chuẩn', hint: '~1MP · nhanh' },
  { id: 'high',     label: 'Cao',        hint: '~1.6MP · nét' },
]
// ALD 13/06/2026 - Model self-host (id khớp run_create_image worker). qwen-edit = mặc định + DUY NHẤT sửa/ghép ảnh.
// ALD 14/06/2026 - Dropdown model: chỉ icon + tên ngắn (không giải thích).
const SELFHOST_MODELS = [
  { id: 'qwen-edit', label: 'Qwen-Edit', icon: 'bi-images' },
  { id: 'flux-dev', label: 'Flux.1 Dev', icon: 'bi-stars' },
  { id: 'flux-schnell', label: 'Flux.1 Schnell', icon: 'bi-lightning-charge-fill' },
]
const GEMINI_MODELS = [
  { id: 'nano-banana-pro', label: 'Nano Banana Pro', icon: 'bi-stars' },
  { id: 'nano-banana', label: 'Nano Banana', icon: 'bi-lightning-charge-fill' },
]
const openModelDrop = ref('')   // '' | 'selfhost' | 'gemini' — dropdown nào đang mở
const currentSelfhostModel = computed(() => SELFHOST_MODELS.find((m) => m.id === local.value.model) || SELFHOST_MODELS[0])
const currentGeminiModel = computed(() => GEMINI_MODELS.find((m) => m.id === local.value.geminiModel) || GEMINI_MODELS[0])
const MODEL_STANDARD_PRESETS = [
  { id: 'female', label: 'Nữ', hint: 'Hotgirl / beauty' },
  { id: 'male', label: 'Nam', hint: 'Hotboy / model' },
  { id: 'off', label: 'Tắt', hint: 'Prompt thuần' },
]
const normalizeModelStandardPreset = (v) => {
  const s = String(v || '').toLowerCase()
  return ['female', 'male', 'custom', 'off'].includes(s) ? s : 'female'
}
// ALD 11/06/2026 - Độ tuổi lọc pool "Model mẫu" (id khớp model_refs.age_group worker/API).
const AGE_GROUPS = [
  { id: 'young', label: 'Trẻ' },
  { id: 'middle', label: 'Trung niên' },
  { id: 'old', label: 'Già' },
]
const normalizeAgeGroup = (v) => {
  const s = String(v || '').toLowerCase()
  return ['young', 'middle', 'old'].includes(s) ? s : 'young'
}

// #region ALD 10/06/2026 - 20 góc máy nhiếp ảnh ("20 loại góc máy cùng một chủ thể") — id khớp CAMERA_ANGLES worker.
const CAMERA_ANGLE_GROUPS = [
  { label: 'Khoảng cách', items: [
    { id: 'can-mat',        label: '1. Cận mặt',         hint: 'khuôn mặt hoặc chi tiết rất gần' },
    { id: 'can-nhat',       label: '2. Cận nhất',        hint: 'tập trung một chi tiết nhỏ' },
    { id: 'trung-canh',     label: '3. Trung cảnh',      hint: 'chủ thể từ eo trở lên' },
    { id: 'toan-than',      label: '4. Toàn thân',       hint: 'toàn bộ cơ thể từ đầu tới chân' },
    { id: 'toan-canh',      label: '5. Toàn cảnh',       hint: 'chủ thể + nhiều khung cảnh xung quanh' },
    { id: 'toan-canh-rong', label: '6. Toàn cảnh rộng',  hint: 'chủ thể rất nhỏ trong khung cảnh rộng lớn' },
  ]},
  { label: 'Độ cao / độ nghiêng', items: [
    { id: 'goc-cao',      label: '9. Góc cao',          hint: 'chụp từ trên cao để chủ thể trông nhỏ hơn' },
    { id: 'goc-thap',     label: '10. Góc thấp',        hint: 'chụp từ dưới lên — chủ thể mạnh mẽ hơn' },
    { id: 'tren-cao',     label: '11. Chụp từ trên cao', hint: 'thẳng từ trên cao xuống (bird’s-eye)' },
    { id: 'goc-nghieng',  label: '12. Góc nghiêng',     hint: 'nghiêng máy tạo cảm giác kịch tính' },
  ]},
  { label: 'Vị trí / hướng nhìn', items: [
    { id: 'qua-vai',           label: '7. Qua vai',            hint: 'khung cảnh từ sau lưng một người' },
    { id: 'goc-nhin',          label: '8. Góc nhìn chủ thể',   hint: 'những gì chủ thể đang nhìn thấy (POV)' },
    { id: 'doi-dien',          label: '16. Góc đối diện',      hint: 'phía đối diện của cuộc trò chuyện' },
    { id: 'chan-dung-nghieng', label: '17. Chân dung nghiêng', hint: 'chủ thể từ góc nghiêng (profile)' },
    { id: 'tu-sau',            label: '18. Chụp từ sau',       hint: 'chủ thể từ phía sau' },
    { id: 'theo-doi',          label: '19. Góc theo dõi',      hint: 'chuyển động — máy di chuyển theo chủ thể' },
  ]},
  { label: 'Bối cảnh / chi tiết', items: [
    { id: 'boi-canh',     label: '14. Thiết lập bối cảnh', hint: 'bối cảnh và vị trí trước' },
    { id: 'hai-nguoi',    label: '15. Hai người',          hint: 'hai chủ thể cùng nhau' },
    { id: 'chi-tiet',     label: '13. Chi tiết',           hint: 'một chi tiết nhỏ hoặc vật thể rõ ràng' },
    { id: 'chi-tiet-nho', label: '20. Chi tiết nhỏ',       hint: 'chi tiết rất nhỏ, quan trọng (macro)' },
  ]},
]
const CAMERA_ANGLE_ALL = CAMERA_ANGLE_GROUPS.flatMap((g) => g.items)
// #endregion

const local = ref({
  prompt: '',
  model: 'qwen-edit',  // ALD 14/06/2026 - Model self-host (qwen-edit | flux-dev | flux-schnell). SD3.5 đã bỏ. Chỉ áp khi provider qwen.
  geminiModel: 'nano-banana-pro',  // ALD 13/06/2026 - Model Gemini (nano-banana-pro = Gemini 3 Pro Image | nano-banana = 2.5 Flash Image). Chỉ áp khi provider gemini.
  promptMode: 'text',  // ALD 13/06/2026 - 'text' | 'json'. JSON = dán cấu trúc, BE ghép thành prompt + negative + tỉ lệ.
  promptJson: '',      // ALD 13/06/2026 - nội dung JSON khi promptMode='json' (giữ riêng để gạt qua lại không mất text).
  negativePrompt: '',  // ALD 11/06/2026 - thứ KHÔNG muốn xuất hiện; worker merge với negative realism mặc định
  inputCount: 0,   // ALD 31/05/2026 - 0 = text→image thuần (không cần ảnh input); 1-6 = ref
  useModelStandard: false, // ALD 13/06/2026 - MẶC ĐỊNH OFF: dùng pool mẫu đè cảnh/prompt (ra studio, bỏ qua mô tả); OFF = bám prompt + mặt AI đẹp tự nhiên. Bật lại nếu muốn kiểu mặt-pool (08/06: 0 ảnh vẫn dùng bộ chuẩn ở worker).
  modelStandardPreset: 'female',
  age_group: 'young',   // ALD 11/06/2026 - lọc pool "Model mẫu" theo độ tuổi (young|middle|old).
  modelStandardPrompt: '',
  realismPreset: 'real_photo',
  outputCount: 1,  // ALD 13/06/2026 - 1 biến thể mặc định; chỉnh 1-10 được nếu muốn re-roll.
  aspectRatio: 'auto',   // auto = giữ tỉ lệ ảnh gốc (khi có ảnh) / 1:1 (text→image)
  quality: 'standard',
  refineSteps: 0,       // ALD 14/06/2026 - Tinh chỉnh nhiều bước (AI vision soi & tự sửa): 0 (tắt) - 3 vòng.
  cameraAngle: '',  // ALD 10/06/2026 - '' = theo prompt; id ∈ CAMERA_ANGLES (worker ghép phrase EN)
  imageMode: 'reference',  // ALD 13/06/2026 - khi có ảnh ref: 'reference' (lấy nhận diện, sinh MỚI) | 'edit' (giữ nguyên ảnh, sửa theo prompt)
  ...props.config,
  provider: normalizeProvider(props.config?.provider),
  apiKey: String(props.config?.apiKey || '').trim() || '',
})
// Clamp 0-6 (fix bug 0||3): 0 = chỉ prompt, 1-6 = số ảnh tham chiếu.
const inputCount = computed(() => Math.max(0, Math.min(6, Number(local.value.inputCount) || 0)))
const outputCount = computed(() => Math.max(1, Math.min(10, Number(local.value.outputCount) || 1)))
const modelStandardPreset = computed(() => {
  if (local.value.useModelStandard === false) return 'off'
  return normalizeModelStandardPreset(local.value.modelStandardPreset)
})
function setModelStandardPreset(id) {
  local.value.modelStandardPreset = id
  local.value.useModelStandard = id !== 'off'
}
const ageGroup = computed(() => normalizeAgeGroup(local.value.age_group))
// ALD 13/06/2026 - Validate JSON khi promptMode='json' để báo lỗi tại chỗ (không chặn lưu, BE cũng tự fallback).
const jsonError = computed(() => {
  if (local.value.promptMode !== 'json') return ''
  const raw = String(local.value.promptJson || '').trim()
  if (!raw) return 'trống — dán JSON mô tả ảnh vào đây'
  try { JSON.parse(raw); return '' } catch (e) { return String(e.message || e).replace(/^JSON\.parse:\s*/, '') }
})
// Placeholder prompt gợi ý theo số ảnh (0 = sinh, 1 = sửa ảnh gốc, 2+ = áp ảnh mẫu lên đối tượng).
const promptPlaceholder = computed(() => {
  if (inputCount.value === 0) return 'VD: Công nhân nhà máy thép Pebsteel đội mũ bảo hộ, ảnh chân thực, ánh sáng công nghiệp, độ chi tiết cao.'
  if (inputCount.value === 1) return 'VD: Đổi nền Ảnh gốc thành nhà máy thép, giữ nguyên người và trang phục.'
  return 'VD: Áp phong cách và trang phục của Ảnh mẫu (Ảnh 1) lên người trong Ảnh 2, giữ nguyên gương mặt và dáng người.'
})

watch(local, (v) => {
  const out = { ...v, realismPreset: 'real_photo' }
  if (clearedApiKey.value && !String(v.apiKey || '').trim()) out.__apiKey_clear = true  // đã bấm Xoá & chưa nhập key mới
  emit('update:config', out)
}, { deep: true })
watch(() => props.config, (v) => {
  if (v && JSON.stringify(v) !== JSON.stringify(local.value)) {
    local.value = { ...local.value, ...v, realismPreset: 'real_photo', provider: normalizeProvider(v.provider) }
  }
})
</script>

<style scoped>
/* Tỉ lệ khung hình */
.apl-ar-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; height: 56px; border-radius: 12px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-ar-tile:hover { border-color: rgba(88,86,214,0.4); }
.apl-ar-tile.is-active { border-color: #5856D6; background: rgba(88,86,214,0.06); color: #3E3CA8; box-shadow: 0 0 0 1px #5856D6 inset; }
.apl-ar-box { display: block; border: 1.5px solid currentColor; border-radius: 3px; opacity: 0.7; }
.apl-ar-label { font-size: 10.5px; font-weight: 700; }
/* Chất lượng */
.apl-q-tile { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 1px; height: 44px; border-radius: 12px; border: 0.5px solid rgba(235,236,240,0.18); background: var(--apl-bg-secondary); color: var(--apl-label); transition: all 0.15s; }
.apl-q-tile b { font-size: 12px; font-weight: 700; }
.apl-q-tile small { font-size: 9.5px; opacity: 0.6; }
.apl-q-tile:hover { border-color: rgba(88,86,214,0.4); }
.apl-q-tile.is-active { border-color: #5856D6; background: rgba(88,86,214,0.06); color: #3E3CA8; box-shadow: 0 0 0 1px #5856D6 inset; }
</style>
