<template>
  <!-- #region ALD 24/05/2026 - Settings shell: full width, fit viewport, scroll INSIDE content panel only.
       Padding cân đối 2 bên (trước đây md:pl-0 khiến sidebar bị sát trái). -->
  <div class="flex flex-1 flex-col min-h-0 px-4 sm:px-6 md:px-8 overflow-hidden">
    <div class="w-full pt-4 pb-4 flex flex-col flex-1 min-h-0 space-y-4">
      <!-- Header (fixed, no scroll) — đồng bộ style H1 hrm-models-frontend -->
      <div class="flex items-center justify-between gap-3 flex-wrap pb-1 px-1 flex-shrink-0">
        <div class="flex items-baseline gap-3 min-w-0">
          <!-- ALD 14/06/2026 - H1 = TÊN TAB (không lặp "Cài đặt" của AppPage). Subtitle = mô tả tab. -->
          <h1 class="text-2xl font-black tracking-tighter title-gradient whitespace-nowrap">{{ activeTabTitle }}</h1>
          <p v-if="activeTabSubtitle" class="hidden sm:block text-[12px] text-gray-500 font-bold tracking-tight border-l border-gray-200 pl-3 truncate">
            {{ activeTabSubtitle }}
          </p>
        </div>
        <!-- ALD 14/06/2026 - Làm mới + Lưu cấp trang CHỈ hiện khi có thay đổi chưa lưu (tab Ollama models). Các tab
             khác (Storage/voices/…) tự có nút riêng → ẩn để khỏi DƯ + tránh "Làm mới" gọi endpoint 404. -->
        <div v-if="dirty || saving" class="flex items-center gap-2">
          <button
            type="button"
            class="press inline-flex items-center gap-2 h-10 px-4 rounded-full glass shadow-card text-sm font-semibold text-gray-700 hover:bg-gray-100 transition-colors"
            @click="loadAll"
          >
            <i class="bi bi-arrow-clockwise" />
            Làm mới
          </button>
          <button
            type="button"
            :disabled="!dirty || saving"
            :class="cn('press inline-flex items-center gap-2 h-10 px-4 rounded-full text-sm font-semibold shadow-pill transition-spring', dirty && !saving ? 'bg-primary text-white hover:bg-primary-dark' : 'bg-gray-200 text-gray-400 cursor-not-allowed')"
            @click="saveAll"
          >
            <svg v-if="saving" class="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
              <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
              <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
            </svg>
            <i v-else class="bi bi-check2" />
            Lưu
          </button>
        </div>
      </div>
      <!-- #endregion -->

      <!-- #region ALD 21/05/2026 - Vertical tabs: fill rest, content panel scrolls internal -->
      <div class="flex flex-col lg:flex-row gap-4 flex-1 min-h-0">
        <!-- Sidebar tabs (fixed height, no scroll) -->
        <aside class="lg:w-56 flex-shrink-0">
          <div class="glass shadow-card rounded-3xl p-2 flex lg:flex-col gap-1 overflow-x-auto lg:overflow-visible">
            <button
              v-for="t in tabs"
              :key="t.id"
              type="button"
              :class="cn(
                'press flex items-center gap-2 px-3 py-2.5 rounded-2xl text-sm font-semibold transition-spring whitespace-nowrap',
                'lg:w-full lg:justify-start',
                activeTab === t.id
                  ? 'bg-primary text-white shadow-pill'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
              )"
              @click="activeTab = t.id"
            >
              <i :class="['bi text-base flex-shrink-0', t.icon]" />
              <span class="flex-1 text-left">{{ t.label }}</span>
              <span
                v-if="tabHasDirty(t.id)"
                :class="cn('w-1.5 h-1.5 rounded-full flex-shrink-0', activeTab === t.id ? 'bg-gray-50' : 'bg-primary')"
                :title="'Có thay đổi chưa lưu'"
              />
            </button>
          </div>
        </aside>

        <!-- Content panel — tab table: fullscreen KHÔNG scroll trang, table tự cuộn -->
        <section :class="cn('flex-1 min-w-0 pr-1', fullscreenTableTab ? 'flex flex-col min-h-0 overflow-hidden' : 'overflow-y-auto space-y-4 pb-4')">
          <!-- ── Tab MODELS ────────────────────────────────────────────────── -->
          <template v-if="activeTab === 'models'">
            <div class="glass shadow-island rounded-3xl p-2">
              <div class="px-4 pt-3 pb-2 flex items-center gap-2">
                <i class="bi bi-cpu text-primary text-base" />
                <h3 class="text-sm font-bold text-gray-900 uppercase tracking-wide">Models mặc định</h3>
              </div>
              <div
                v-for="(cfg, idx) in configRows"
                :key="cfg.key"
                :class="cn('flex flex-col md:flex-row md:items-center gap-3 md:gap-5 p-4', idx < configRows.length - 1 && 'border-b border-white/40')"
              >
                <div class="flex-shrink-0 md:w-56">
                  <div class="flex items-center gap-2">
                    <span class="inline-flex h-9 w-9 items-center justify-center rounded-2xl bg-primary text-white shadow-pill">
                      <i :class="['bi text-base', cfg.icon]" />
                    </span>
                    <span class="text-sm font-bold text-gray-900">{{ cfg.label }}</span>
                  </div>
                  <p class="text-xs text-gray-500 mt-1 leading-relaxed">{{ cfg.hint }}</p>
                </div>
                <div class="flex-1 min-w-0">
                  <UiDropdown
                    v-model="form[cfg.key]"
                    :options="modelOptions"
                    :placeholder="loadingModels ? 'Đang tải models…' : 'Chọn model'"
                    :disabled="loadingModels"
                    full-width
                    :force-search="true"
                  />
                  <p v-if="form[cfg.key]" class="text-xs text-gray-400 mt-1.5 font-mono px-1">
                    {{ describeModel(form[cfg.key]) }}
                  </p>
                </div>
              </div>
            </div>

            <!-- Ollama server status (nằm trong tab Models) -->
            <div class="glass shadow-card rounded-3xl p-4 flex items-center justify-between gap-3 flex-wrap">
              <div class="flex items-center gap-3">
                <span class="inline-flex h-10 w-10 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-600">
                  <i class="bi bi-hdd-stack text-lg" />
                </span>
                <div>
                  <div class="text-xs font-bold text-gray-500 uppercase tracking-wide">Ollama server</div>
                  <div class="text-sm font-semibold text-gray-800 mt-0.5">{{ modelCount }} models có sẵn</div>
                </div>
              </div>
              <span :class="cn(
                'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-bold uppercase tracking-wider',
                loadingModels ? 'bg-amber-50 text-amber-700' : modelOptions.length ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
              )">
                <span :class="cn('w-2 h-2 rounded-full', loadingModels ? 'bg-amber-500' : modelOptions.length ? 'bg-emerald-500' : 'bg-rose-500')" />
                {{ loadingModels ? 'Đang tải…' : modelOptions.length ? 'Online' : 'Không kết nối' }}
              </span>
            </div>
          </template>

          <!-- ── Tab APPEARANCE (avatar AI — lưu local, per-user) ─────────── -->
          <template v-else-if="activeTab === 'appearance'">
            <div class="glass shadow-island rounded-3xl p-6">
              <div class="flex items-start gap-2 mb-5">
                <span class="inline-flex h-9 w-9 items-center justify-center rounded-2xl bg-primary-dark text-white shadow-pill flex-shrink-0">
                  <i class="bi bi-robot text-base" />
                </span>
                <div class="flex-1 min-w-0">
                  <h3 class="text-sm font-bold text-gray-900 uppercase tracking-wide">Avatar AI</h3>
                  <p class="text-xs text-gray-500 mt-1 leading-relaxed">
                    Ảnh hiển thị cạnh mọi tin nhắn AI (chat + A4 OCR). Lưu trong trình duyệt, riêng từng user.
                  </p>
                </div>
              </div>

              <!-- Preview row — 3 size để thấy hiệu ứng thực tế -->
              <div class="flex items-center justify-center gap-6 py-6 mb-5 bg-white/[0.03] rounded-2xl border border-white/40">
                <div class="flex flex-col items-center gap-2">
                  <img :src="avatarPreviewSrc" alt="" referrerpolicy="no-referrer" class="w-8 h-8 rounded-full object-cover border border-gray-200 bg-gray-100" @error="avatarPreviewErrored = true" @load="avatarPreviewErrored = false" />
                  <span class="text-[0.65rem] text-gray-400 font-mono">32px</span>
                </div>
                <div class="flex flex-col items-center gap-2">
                  <img :src="avatarPreviewSrc" alt="" referrerpolicy="no-referrer" class="w-14 h-14 rounded-full object-cover border border-gray-200 bg-gray-100 shadow-card" />
                  <span class="text-[0.65rem] text-gray-400 font-mono">56px</span>
                </div>
                <div class="flex flex-col items-center gap-2">
                  <img :src="avatarPreviewSrc" alt="" referrerpolicy="no-referrer" class="w-20 h-20 rounded-full object-cover border border-gray-200 bg-gray-100 shadow-island" />
                  <span class="text-[0.65rem] text-gray-400 font-mono">80px</span>
                </div>
              </div>

              <!-- Actions: upload file / paste URL / reset -->
              <input ref="avatarFileRef" type="file" accept="image/*" class="hidden" @change="onAvatarFile" />

              <div class="space-y-3">
                <div class="flex flex-wrap gap-2">
                  <button
                    type="button"
                    class="press inline-flex items-center gap-1.5 h-10 px-4 rounded-full bg-primary text-white text-sm font-semibold shadow-pill hover:bg-primary-dark transition-colors"
                    @click="avatarFileRef?.click()"
                  >
                    <i class="bi bi-upload" />
                    Tải ảnh lên
                  </button>
                  <button
                    v-if="assistantAvatarLocal"
                    type="button"
                    class="press inline-flex items-center gap-1.5 h-10 px-4 rounded-full text-sm font-semibold text-gray-600 hover:bg-gray-100 border border-gray-200 transition-colors"
                    @click="assistantAvatarLocal = ''"
                  >
                    <i class="bi bi-arrow-counterclockwise" />
                    Mặc định
                  </button>
                  <span class="text-[0.7rem] text-gray-400 self-center ml-auto" v-if="assistantAvatarLocal">
                    {{ avatarSourceLabel }}
                  </span>
                </div>

                <div>
                  <label class="text-xs font-semibold text-gray-700">Hoặc dán URL ảnh</label>
                  <UiInput
                    v-model="avatarUrlDraft"
                    placeholder="https://… (PNG/JPG/SVG)"
                    class="mt-1.5"
                    @blur="applyUrlDraft"
                    @keydown.enter="applyUrlDraft"
                  />
                </div>

                <p
                  v-if="assistantAvatarLocal && avatarPreviewErrored"
                  class="text-xs text-rose-500 font-semibold inline-flex items-center gap-1.5"
                >
                  <i class="bi bi-exclamation-triangle-fill" />
                  Ảnh không tải được — kiểm tra URL hoặc upload file khác.
                </p>
                <p v-else class="text-[0.7rem] text-gray-400">
                  Ảnh ≤ 5 MB. Upload file → lưu base64 trong trình duyệt; URL → lưu link trực tiếp.
                </p>
              </div>
            </div>
          </template>

          <!-- ── Tab MODEL MẪU (admin: thư viện ảnh mẫu cho create-image) ──── -->
          <template v-else-if="activeTab === 'model-refs'">
            <SettingsModelRefsTab />
          </template>

          <!-- ── Tab GIỌNG NÓI (admin: thư viện giọng mẫu cho talk/teaser/story-film) ── -->
          <template v-else-if="activeTab === 'voices'">
            <SettingsVoiceLibraryTab />
          </template>

          <template v-else-if="activeTab === 'model-files'">
            <SettingsModelsTab />
          </template>

          <!-- ── Tab USERS (admin: CRUD người dùng — fullscreen, no-scroll) ─── -->
          <template v-else-if="activeTab === 'users'">
            <SettingsUsersManager />
          </template>

          <!-- ── Tab BACKUP & RESTORE (admin: tải/khôi phục DB + MinIO ra .zip) ── -->
          <template v-else-if="activeTab === 'backup'">
            <SettingsBackupRestoreTab />
          </template>

          <!-- ── Tab AI PROVIDERS (API keys cho OpenAI/Anthropic/Google/Ollama) ── -->
          <template v-else-if="activeTab === 'providers'">
            <SettingsAiProvidersTab />
          </template>

          <!-- ── Tab WORKFLOWS (Node-RED-style flow builder) ─────────────────── -->
          <template v-else-if="activeTab === 'workflows'">
            <SettingsWorkflowsTab />
          </template>

          <!-- ── Tab MEMORY (Claude/ChatGPT-style list items) ──────────────── -->
          <template v-else-if="activeTab === 'memory'">
            <div class="glass shadow-island rounded-3xl p-5 space-y-4">
              <!-- Header -->
              <div class="flex items-start justify-between gap-3">
                <div class="flex items-start gap-2 flex-1 min-w-0">
                  <span class="inline-flex h-9 w-9 items-center justify-center rounded-2xl bg-violet-500 text-white shadow-pill flex-shrink-0">
                    <i class="bi bi-bookmark-star-fill text-base" />
                  </span>
                  <div class="min-w-0">
                    <h3 class="text-sm font-bold text-gray-900 uppercase tracking-wide">Rules</h3>
                    <p class="text-xs text-gray-500 mt-1 leading-relaxed">
                      Mỗi dòng là 1 điều AI cần ghi nhớ — vai trò, dự án, ngữ cảnh, sở thích trả lời… AI sẽ tham chiếu trong mọi phiên chat.
                    </p>
                  </div>
                </div>
                <span class="text-[0.7rem] text-gray-400 font-mono whitespace-nowrap">{{ memoryItems.length }} / 200</span>
              </div>

              <!-- Add new item -->
              <form
                class="flex items-stretch gap-2 bg-white/60 border border-gray-200/60 rounded-2xl p-1.5"
                @submit.prevent="addMemoryItem"
              >
                <input
                  ref="memoryInputRef"
                  v-model="newMemoryDraft"
                  maxlength="500"
                  type="text"
                  placeholder="Thêm 1 điều AI nên ghi nhớ về bạn…"
                  class="flex-1 min-w-0 bg-transparent px-3 text-sm text-gray-800 placeholder:text-gray-400 focus:outline-none"
                  :disabled="addingMemory"
                />
                <button
                  type="submit"
                  :disabled="!newMemoryDraft.trim() || addingMemory"
                  :class="cn(
                    'press inline-flex items-center justify-center gap-1.5 h-9 px-4 rounded-full text-xs font-semibold transition-spring flex-shrink-0',
                    newMemoryDraft.trim() && !addingMemory
                      ? 'bg-primary text-white shadow-pill hover:bg-primary-dark'
                      : 'bg-gray-200 text-gray-400 cursor-not-allowed'
                  )"
                >
                  <svg v-if="addingMemory" class="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
                  </svg>
                  <i v-else class="bi bi-plus-lg" />
                  Thêm
                </button>
              </form>

              <!-- Items list -->
              <div v-if="loadingMemory" class="text-xs text-gray-400 italic text-center py-6">
                Đang tải bộ nhớ…
              </div>
              <div
                v-else-if="!memoryItems.length"
                class="text-center py-8 text-sm text-gray-400"
              >
                <i class="bi bi-bookmark text-3xl text-gray-300 mb-2 block" />
                Chưa có ghi nhớ nào. Thêm để AI biết về bạn.
              </div>

              <ul v-else class="space-y-1.5">
                <li
                  v-for="m in memoryItems"
                  :key="m.id"
                  class="group flex items-start gap-2 p-3 bg-white/60 hover:bg-gray-100 border border-gray-200/60 rounded-2xl transition-colors"
                >
                  <i :class="['bi flex-shrink-0 mt-0.5 text-base', m.source === 'auto' ? 'bi-stars text-violet-500' : 'bi-bookmark-check text-primary']"
                     :title="m.source === 'auto' ? 'AI tự ghi nhớ' : 'User thêm'" />
                  <div class="flex-1 min-w-0">
                    <p class="text-sm text-gray-800 leading-relaxed break-words">{{ m.content }}</p>
                    <p class="text-[0.7rem] text-gray-400 mt-1">
                      {{ formatRelative(m.created_at) }}
                    </p>
                  </div>
                  <button
                    type="button"
                    class="press h-7 w-7 flex items-center justify-center rounded-lg text-gray-400 hover:text-rose-500 hover:bg-rose-50 opacity-0 group-hover:opacity-100 transition-all flex-shrink-0"
                    title="Xóa ghi nhớ"
                    @click="deleteMemoryItem(m)"
                  >
                    <i class="bi bi-trash text-sm" />
                  </button>
                </li>
              </ul>

              <!-- Bulk action -->
              <div
                v-if="memoryItems.length"
                class="flex items-center justify-end pt-2 border-t border-white/40"
              >
                <button
                  type="button"
                  class="press text-xs font-semibold text-rose-500 hover:text-rose-700 inline-flex items-center gap-1.5"
                  @click="clearAllMemory"
                >
                  <i class="bi bi-trash3" />
                  Xóa tất cả ({{ memoryItems.length }})
                </button>
              </div>
            </div>
          </template>

          <!-- ── Tab JOBS — admin xem + xoá analyze jobs (running / stuck / done) ─ -->
          <template v-else-if="activeTab === 'jobs'">
            <div class="glass shadow-island rounded-3xl p-5">
              <div class="flex items-start gap-3 mb-5">
                <span class="inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-rose-400 to-rose-600 text-white shadow-pill flex-shrink-0">
                  <i class="bi bi-activity text-lg" />
                </span>
                <div class="flex-1 min-w-0">
                  <div class="flex items-center justify-between gap-2">
                    <h3 class="text-sm font-bold text-gray-900 uppercase tracking-wide">Analyze Jobs</h3>
                    <button
                      type="button"
                      class="press inline-flex items-center gap-1.5 h-8 px-3 rounded-full text-xs font-semibold text-gray-600 bg-white/70 hover:bg-gray-100 border border-white/[0.08] transition-colors"
                      @click="loadJobs"
                    >
                      <i :class="loadingJobs ? 'bi bi-arrow-clockwise animate-spin' : 'bi bi-arrow-clockwise'" />
                      Làm mới
                    </button>
                  </div>
                  <p class="text-xs text-gray-500 mt-1 leading-relaxed">
                    Toàn bộ analyze job (mọi user). Job <b class="text-rose-600">running</b> mà
                    không tiến triển = stuck — xoá để giải phóng Redis + cancel worker.
                  </p>
                </div>
              </div>

              <p v-if="loadingJobs" class="text-xs text-gray-400 italic text-center py-6">Đang tải…</p>
              <p v-else-if="!jobs.length" class="text-xs text-gray-400 italic text-center py-6">Không có job nào.</p>

              <div v-else class="space-y-2">
                <div
                  v-for="j in jobs"
                  :key="j.jobId"
                  class="flex items-center gap-3 p-3 bg-white/60 hover:bg-gray-100 border border-white/40 rounded-2xl transition-colors"
                >
                  <span :class="cn(
                    'inline-flex h-8 w-8 items-center justify-center rounded-xl flex-shrink-0',
                    j.mode === 'pdf' ? 'bg-rose-50 text-rose-600' :
                    j.mode === 'image' ? 'bg-violet-50 text-violet-600' :
                    'bg-blue-50 text-blue-600'
                  )">
                    <i :class="['bi', j.mode === 'pdf' ? 'bi-file-earmark-pdf' : j.mode === 'image' ? 'bi-image' : 'bi-chat-dots']" />
                  </span>

                  <div class="flex-1 min-w-0">
                    <div class="flex items-center gap-2 flex-wrap">
                      <code class="text-[0.7rem] font-mono text-gray-700 truncate">{{ j.jobId.slice(0, 12) }}…</code>
                      <UiBadge :variant="badgeVariant(j.status)" size="sm" dot>{{ j.status }}</UiBadge>
                      <span v-if="j.mode === 'pdf' && j.pageTotal" class="text-[0.65rem] text-gray-500 font-mono">
                        page {{ (j.pageIdx ?? 0) + 1 }}/{{ j.pageTotal }}
                      </span>
                    </div>
                    <div class="text-[0.7rem] text-gray-500 mt-0.5 truncate">
                      {{ formatRelative(j.createdAt) }}
                      <span class="text-gray-300">·</span>
                      <span class="font-mono">{{ j.contentLen ?? 0 }} chars</span>
                      <span v-if="j.attachmentName" class="text-gray-300">·</span>
                      <span v-if="j.attachmentName" class="truncate">{{ j.attachmentName }}</span>
                    </div>
                    <div v-if="j.errorMsg" class="text-[0.7rem] text-rose-600 mt-1 truncate" :title="j.errorMsg">
                      <i class="bi bi-exclamation-triangle me-1" />{{ j.errorMsg }}
                    </div>
                  </div>

                  <button
                    type="button"
                    class="press h-8 px-3 rounded-full text-xs font-semibold text-rose-600 hover:bg-rose-50 border border-rose-200 transition-colors flex-shrink-0"
                    title="Xoá job + cleanup Redis"
                    @click="deleteJob(j)"
                  >
                    <i class="bi bi-trash" />
                    Xoá
                  </button>
                </div>
              </div>
            </div>
          </template>

          <!-- ── Tab REPORTS (admin: quality metrics + export JSON cho Claude) ── -->
          <template v-else-if="activeTab === 'reports'">
            <SettingsReportsManager />
          </template>

          <!-- ── Tab API (Apple Dynamic Island style) ──────────────────────── -->
          <template v-else-if="activeTab === 'api'">
            <!-- Hero header — glass pill -->
            <div class="glass shadow-island rounded-3xl p-5">
              <div class="flex items-start gap-3">
                <span class="inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-gradient-to-br from-emerald-400 to-emerald-600 text-white shadow-pill flex-shrink-0">
                  <i class="bi bi-code-slash text-lg" />
                </span>
                <div class="flex-1 min-w-0">
                  <h3 class="text-base font-black text-gray-900 tracking-tight">API cho app khác</h3>
                  <p class="text-xs text-gray-500 mt-1 leading-relaxed">
                    Tích hợp Local AI vào script / app khác bằng API key. Mỗi endpoint dùng cùng base URL.
                  </p>
                </div>
              </div>

              <!-- Base URL pill -->
              <div class="mt-4 flex items-center gap-3 bg-white/70 rounded-2xl px-4 py-3 border border-white/[0.08] shadow-card">
                <i class="bi bi-globe text-primary text-base flex-shrink-0" />
                <div class="flex-1 min-w-0">
                  <div class="text-[0.65rem] font-bold text-gray-500 uppercase tracking-wider">Base URL</div>
                  <code class="text-xs font-mono text-gray-800 break-all">{{ supabaseUrl }}</code>
                </div>
                <button
                  type="button"
                  class="press h-8 w-8 flex items-center justify-center rounded-xl text-gray-500 hover:text-primary hover:bg-primary-50 transition-colors flex-shrink-0"
                  title="Copy base URL"
                  @click="copyBase"
                >
                  <i :class="copiedKey === '_base' ? 'bi bi-check2 text-emerald-500' : 'bi bi-clipboard'" />
                </button>
              </div>
            </div>

            <!-- API Keys list — glass card riêng -->
            <div class="glass shadow-island rounded-3xl p-5">
              <div class="flex items-center justify-between gap-2 mb-4">
                <div class="flex items-center gap-2">
                  <span class="inline-flex h-9 w-9 items-center justify-center rounded-2xl bg-gradient-to-br from-amber-400 to-amber-500 text-white shadow-pill">
                    <i class="bi bi-key-fill text-base" />
                  </span>
                  <div>
                    <h3 class="text-sm font-bold text-gray-900 uppercase tracking-wide">API Keys của bạn</h3>
                    <p class="text-[0.7rem] text-gray-500 mt-0.5">{{ apiKeys.length }} key đã tạo</p>
                  </div>
                </div>
                <button
                  type="button"
                  class="press inline-flex items-center gap-1.5 h-9 px-4 rounded-full bg-primary text-white text-xs font-semibold shadow-pill hover:bg-primary-dark transition-colors"
                  @click="openCreateKey"
                >
                  <i class="bi bi-plus-lg" />
                  Tạo key
                </button>
              </div>

              <p v-if="!apiKeys.length && !loadingKeys" class="text-xs text-gray-400 italic text-center py-4">
                Chưa có key. Tạo key mới để bắt đầu.
              </p>
              <p v-if="loadingKeys" class="text-xs text-gray-400 italic text-center py-4">Đang tải…</p>

              <div v-if="apiKeys.length" class="space-y-1.5">
                <div
                  v-for="k in apiKeys"
                  :key="k.id"
                  class="flex items-center gap-3 py-2.5 px-3 bg-white/60 hover:bg-gray-100 border border-white/40 rounded-2xl transition-colors"
                >
                  <i :class="['bi text-base', k.is_active ? 'bi-key-fill text-emerald-500' : 'bi-key text-gray-400']" />
                  <div class="flex-1 min-w-0">
                    <div class="text-sm font-semibold text-gray-900 truncate">{{ k.name || '(không tên)' }}</div>
                    <div class="text-[0.7rem] font-mono text-gray-500 truncate">{{ k.key }}</div>
                  </div>
                  <UiBadge :variant="k.is_active ? 'success' : 'neutral'" size="sm" dot>
                    {{ k.is_active ? 'Active' : 'Revoked' }}
                  </UiBadge>
                  <button
                    v-if="k.is_active"
                    type="button"
                    class="press h-7 w-7 flex items-center justify-center rounded-lg text-gray-400 hover:text-rose-500 hover:bg-rose-50"
                    title="Thu hồi"
                    @click="revokeKey(k)"
                  >
                    <i class="bi bi-x-circle text-sm" />
                  </button>
                </div>
              </div>
            </div>

            <!-- Endpoints — group → cards riêng cho từng endpoint -->
            <div
              v-for="grp in endpointGroups"
              :key="grp.group"
              class="space-y-3"
            >
              <div class="flex items-center gap-2 px-2 pt-2">
                <span class="text-[0.7rem] font-black text-gray-500 uppercase tracking-widest">{{ grp.group }}</span>
                <div class="flex-1 h-px bg-gradient-to-r from-white/60 to-transparent" />
              </div>

              <article
                v-for="ep in grp.items"
                :key="ep.title"
                class="glass shadow-island rounded-3xl overflow-hidden transition-spring hover:shadow-island-lg"
              >
                <!-- Card header -->
                <header class="px-5 pt-4 pb-3 flex items-center justify-between gap-3 flex-wrap border-b border-white/40">
                  <div class="flex items-center gap-2 min-w-0">
                    <span :class="cn(
                      'inline-flex h-6 px-2 items-center rounded-full text-[0.65rem] font-black tracking-wider text-white shadow-pill',
                      ep.method === 'POST' ? 'bg-emerald-500' : ep.method === 'GET' ? 'bg-blue-500' : 'bg-rose-500'
                    )">{{ ep.method }}</span>
                    <code class="text-xs font-mono text-gray-800 truncate">{{ ep.path }}</code>
                    <span :class="cn('inline-flex h-5 px-2 items-center rounded-md text-[0.6rem] font-bold tracking-wider text-white flex-shrink-0', ep.tagColor)">{{ ep.tag }}</span>
                  </div>
                  <button
                    type="button"
                    class="press inline-flex items-center gap-1.5 h-8 px-3 rounded-full text-[0.7rem] font-semibold text-gray-600 bg-white/70 hover:bg-gray-100 border border-white/[0.08] transition-colors flex-shrink-0"
                    @click="copySample(ep)"
                  >
                    <i :class="copiedKey === ep.title ? 'bi bi-check2 text-emerald-500' : 'bi bi-clipboard'" />
                    {{ copiedKey === ep.title ? 'Đã copy' : 'Copy curl' }}
                  </button>
                </header>

                <!-- Description -->
                <p class="px-5 py-3 text-xs text-gray-600 leading-relaxed">{{ ep.desc }}</p>

                <!-- Code snippet — dark inner block với padding rộng -->
                <div class="px-3 pb-3">
                  <pre class="text-[0.72rem] font-mono text-gray-100 bg-gray-900 rounded-2xl p-4 overflow-x-auto shadow-card"><code>{{ ep.sample }}</code></pre>
                </div>
              </article>
            </div>
          </template>
        </section>
      </div>
      <!-- #endregion -->
    </div>

    <!-- #region ALD 20/05/2026 - SidePanel tạo key mới (giữ nguyên) -->
    <UiSidePanel v-model="createKeyOpen" title="Tạo API Key mới" subtitle="Key chỉ hiện 1 lần duy nhất khi tạo">
      <div v-if="!newlyCreatedKey" class="space-y-4">
        <div>
          <label class="text-xs font-semibold text-gray-700">Tên key *</label>
          <UiInput v-model="keyForm.name" placeholder="vd: Script OCR hàng tháng" class="mt-1" />
        </div>
        <div>
          <label class="text-xs font-semibold text-gray-700">Mô tả (tuỳ chọn)</label>
          <UiInput v-model="keyForm.description" placeholder="Mục đích sử dụng" class="mt-1" />
        </div>
      </div>
      <div v-else class="space-y-3">
        <UiCard class="!p-4 bg-emerald-50 border-emerald-200">
          <div class="flex items-start gap-3">
            <i class="bi bi-check-circle-fill text-emerald-600 text-xl flex-shrink-0 mt-0.5" />
            <div class="text-xs text-emerald-900 leading-relaxed">
              <strong>Tạo thành công.</strong> Copy ngay — sau khi đóng, key sẽ không hiển thị lại.
            </div>
          </div>
        </UiCard>
        <code class="block text-xs font-mono bg-gray-100 border border-gray-200 px-3 py-2.5 rounded-xl text-gray-800 break-all">{{ newlyCreatedKey }}</code>
        <UiButton variant="secondary" size="md" class="w-full" @click="copyKey">
          <i :class="copiedKey === '_new' ? 'bi bi-check2' : 'bi bi-clipboard'" />
          {{ copiedKey === '_new' ? 'Đã copy' : 'Copy key' }}
        </UiButton>
      </div>
      <template #footer>
        <UiButton variant="secondary" size="md" @click="closeCreateKey">
          {{ newlyCreatedKey ? 'Đóng' : 'Huỷ' }}
        </UiButton>
        <UiButton v-if="!newlyCreatedKey" variant="primary" size="md" :loading="creatingKey" :disabled="!keyForm.name.trim()" @click="onCreateKey">
          <i class="bi bi-check2" />
          Tạo key
        </UiButton>
      </template>
    </UiSidePanel>
    <!-- #endregion -->
  </div>
</template>

<script setup>
import { useStorage } from '@vueuse/core'

definePageMeta({ middleware: ['auth'] })
useHead({ title: 'Cài đặt — Local AI' })

const auth = useAuth()
const toast = useToast()
const confirm = useConfirm()
const config = useRuntimeConfig()
const supabaseUrl = computed(() => config.public.motionBackendUrl)

// #region ALD 21/05/2026 - Tabs: filter theo role (user thường chỉ Storage + Giao diện)
const isAdmin = computed(() => {
  try {
    return JSON.parse(atob((auth.token.value || '').split('.')[1] ?? ''))?.role === 'admin'
  } catch { return false }
})
// ALD 24/05/2026 - Tab chat-side (models / providers / workflows-tab / appearance /
// memory / api) đã ẩn vì motions chỉ cần Storage + Jobs + Reports. Markup giữ trong
// file cho rollback dễ. Reports tab thay thế trang /runs (đã xóa).
// ALD 24/05/2026 - Cắt tab Jobs (đã ẩn theo y/c). Reports giờ dùng workflow_runs
// thay vì motion-jobs cũ (component ReportsManager đồng bộ sau).
// ALD 31/05/2026 - Reports chuyển ra sidebar trái (/reports). Settings chỉ còn Storage.
const ALL_TABS = [
  // ALD 06/07/2026 - BỎ tab Storage (đã có page /storage riêng trong sidebar — để cả 2 nơi là dư).
  // Thêm tab Cá nhân hoá (avatar AI) làm tab non-admin mặc định.
  { id: 'appearance', label: 'Cá nhân hoá', icon: 'bi-person-circle',  admin: false },
  // ALD 11/06/2026 - Thư viện ảnh model mẫu cho create-image (upload + tự xóa nền + phân loại).
  { id: 'model-refs', label: 'Model mẫu',  icon: 'bi-person-bounding-box', admin: true },
  // ALD 13/06/2026 - Thư viện giọng nói: upload file mẫu MP3/WAV → viXTTS clone lúc TTS (talk/teaser/story-film).
  { id: 'voices',     label: 'Giọng nói',  icon: 'bi-mic-fill',       admin: true },
  // ALD 15/06/2026 - Models AI: catalog cài model on-demand (ComfyUI + Ollama, bấm Download) + upload model custom.
  { id: 'model-files', label: 'Models AI',  icon: 'bi-boxes',          admin: true },
  { id: 'users',      label: 'Người dùng', icon: 'bi-people-fill',     admin: true },
  // ALD 13/06/2026 - Backup & Restore: tải toàn bộ DB + MinIO ra 1 file .zip, restore lên VPS mới (upsert).
  { id: 'backup',     label: 'Backup & Restore', icon: 'bi-box-arrow-in-down', admin: true }
]
const tabs = computed(() => ALL_TABS.filter((t) => !t.admin || isAdmin.value))
// Default tab theo role: admin → 'models'; user thường → 'storage'.
// Override qua URL query (?tab=workflows) — dùng cho deep-link từ /workflows/[id] back link.
const route = useRoute()
const initialTab = (() => {
  const q = String(route.query.tab || '').trim()
  // Chỉ nhận tab tồn tại VÀ user có quyền (admin tab → phải là admin).
  if (q && ALL_TABS.some((t) => t.id === q && (!t.admin || isAdmin.value))) return q
  // ALD 06/07/2026 - Storage đã tách ra page riêng; deep-link ?tab=storage cũ → chuyển hướng.
  if (q === 'storage') { navigateTo('/storage', { replace: true }) }
  return 'appearance'
})()
const activeTab = ref(initialTab)

const TAB_SUBTITLES = {
  models: 'Models mặc định cho chat / vision / OCR',
  providers: 'API key cho OpenAI / Anthropic / Google / Ollama — node workflow chọn provider',
  workflows: 'Custom flow ghép OCR / Chat / Image — export ra API auto',
  appearance: 'Avatar AI cá nhân hoá hiển thị',
  'model-refs': 'Upload và quản lý model mẫu cho workflow tạo ảnh',
  voices: 'Upload và quản lý giọng nói mẫu (viXTTS clone) cho talk / teaser / story-film',
  'model-files': 'Upload & quản lý file model AI tự train (LoRA/checkpoint…) cho ComfyUI — gom 1 nơi, dễ dọn dẹp',
  users: 'Quản lý người dùng — vai trò, phòng ban, trạng thái (chỉ admin)',
  backup: 'Sao lưu & khôi phục toàn bộ dữ liệu (Postgres + MinIO) ra/vào 1 file .zip',
  memory: 'Rules + bộ nhớ ngữ cảnh AI cần ghi nhớ',
  jobs: 'Theo dõi + dừng analyze jobs đang chạy',
  reports: 'Báo cáo chất lượng AI — token / timing / output để evaluate',
  api: 'API keys cho integration ngoài'
}
const activeTabSubtitle = computed(() => TAB_SUBTITLES[activeTab.value] ?? '')
// ALD 14/06/2026 - tiêu đề H1 = tên tab hiện tại (không lặp "Cài đặt" của AppPage).
const activeTabTitle = computed(() => (ALL_TABS.find((t) => t.id === activeTab.value)?.label) || 'Cài đặt')
const fullscreenTableTab = computed(() => ['users', 'model-refs', 'voices', 'model-files'].includes(activeTab.value))
// #endregion

// Key groups — phục vụ dirty detection từng tab
// model.pdf đã bỏ — Chandra OCR 2 (vLLM) tự lo OCR PDF, không cần Ollama vision model nữa.
const MODEL_KEYS = ['model.chat', 'model.image']
// #endregion

// ── Models ──────────────────────────────────────────────────────────────
const modelOptions = ref([])
const loadingModels = ref(false)
const initialSettings = ref({})
const form = ref({ 'model.chat': '', 'model.image': '' })

// #region ALD 20/05/2026 - Avatar AI (lưu localStorage qua useStorage, per-user)
const ASSISTANT_AVATAR_DEFAULT = 'https://ui-avatars.com/api/?name=AI&background=1e3a8a&color=fff&bold=true&size=64&rounded=true'
const MAX_AVATAR_BYTES = 5 * 1024 * 1024 // 5MB — đủ cho ảnh chất lượng cao

const assistantAvatarLocal = useStorage('peb_ai_avatar', '')
const avatarFileRef = ref(null)
const avatarUrlDraft = ref('')
const avatarPreviewErrored = ref(false)

// Đồng bộ draft từ giá trị hiện tại (chỉ khi không phải base64) để user thấy URL trong input
watch(assistantAvatarLocal, (val) => {
  avatarPreviewErrored.value = false
  if (!val || val.startsWith('data:')) {
    avatarUrlDraft.value = ''
  } else {
    avatarUrlDraft.value = val
  }
}, { immediate: true })

const avatarPreviewSrc = computed(() => {
  if (avatarPreviewErrored.value || !assistantAvatarLocal.value) return ASSISTANT_AVATAR_DEFAULT
  return assistantAvatarLocal.value
})

const avatarSourceLabel = computed(() => {
  const v = assistantAvatarLocal.value
  if (!v) return ''
  if (v.startsWith('data:')) {
    const kb = Math.round((v.length * 0.75) / 1024) // base64 → bytes ratio ~0.75
    return `File local · ~${kb} KB`
  }
  return 'URL bên ngoài'
})

function applyUrlDraft() {
  const url = (avatarUrlDraft.value || '').trim()
  if (!url) return
  if (!/^https?:\/\//i.test(url) && !url.startsWith('data:')) {
    toast.error('URL phải bắt đầu bằng http(s)://')
    return
  }
  assistantAvatarLocal.value = url
}

function onAvatarFile(e) {
  const file = e.target.files?.[0]
  e.target.value = '' // cho phép chọn lại cùng file
  if (!file) return
  if (!file.type.startsWith('image/')) {
    toast.error('File phải là ảnh')
    return
  }
  if (file.size > MAX_AVATAR_BYTES) {
    const mb = (file.size / (1024 * 1024)).toFixed(1)
    toast.error(`Ảnh quá lớn (${mb} MB). Tối đa 5 MB.`)
    return
  }
  const reader = new FileReader()
  reader.onload = (ev) => {
    assistantAvatarLocal.value = String(ev.target?.result || '')
    toast.success('Đã cập nhật avatar AI')
  }
  reader.onerror = () => toast.error('Không đọc được file')
  reader.readAsDataURL(file)
}
// #endregion

// PDF dùng Chandra OCR 2 (vLLM standalone) — không cần Ollama model nữa, bỏ row model.pdf.
const configRows = [
  { key: 'model.chat',  label: 'Trò chuyện',    icon: 'bi-chat-dots', hint: 'Dùng cho mọi câu hỏi text + extraction từ OCR PDF.' },
  { key: 'model.image', label: 'Phân tích ảnh', icon: 'bi-image',     hint: 'Cần model vision (qwen2.5vl:7b).' }
]

const modelCount = computed(() => modelOptions.value.length)

function describeModel(value) {
  const m = modelOptions.value.find((o) => o.value === value)
  if (!m) return ''
  return [m.paramSize, m.quant, m.family].filter(Boolean).join(' · ')
}

// ── Memory items (Claude/ChatGPT style list) ────────────────────────────
const memoryItems = ref([])
const loadingMemory = ref(false)
const newMemoryDraft = ref('')
const addingMemory = ref(false)
const memoryInputRef = ref(null)

// ── API Keys ────────────────────────────────────────────────────────────
const apiKeys = ref([])
const loadingKeys = ref(false)
const createKeyOpen = ref(false)
const creatingKey = ref(false)
const keyForm = ref({ name: '', description: '' })
const newlyCreatedKey = ref('')
const copiedKey = ref('')

// ── Endpoint samples (tách analyze-create thành 3 use case riêng) ─────
const endpoints = computed(() => {
  const base = supabaseUrl.value
  const exampleKey = apiKeys.value[0]?.key || 'YOUR_API_KEY'
  return [
    {
      title: 'analyze-chat',
      group: 'Tạo job',
      tag: 'Chat',
      tagColor: 'bg-blue-500',
      method: 'POST',
      path: '/functions/v1/analyze-jobs',
      desc: 'Job chat thuần text — gửi mảng messages, AI trả lời.',
      sample: `curl -X POST ${base}/functions/v1/analyze-jobs \\
  -H "x-api-key: ${exampleKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"messages":[{"role":"user","content":"Hỏi gì đó"}]}'`
    },
    {
      title: 'analyze-image',
      group: 'Tạo job',
      tag: 'Image',
      tagColor: 'bg-violet-500',
      method: 'POST',
      path: '/functions/v1/analyze-jobs',
      desc: 'Upload ảnh → vision model mô tả / trả lời câu hỏi. Field tùy chọn: prompt (câu hỏi), imageMode=true (label, không đổi behavior).',
      sample: `curl -X POST ${base}/functions/v1/analyze-jobs \\
  -H "x-api-key: ${exampleKey}" \\
  -F file=@./screenshot.png \\
  -F prompt="Trích xuất text từ ảnh"`
    },
    {
      title: 'analyze-pdf-ocr',
      group: 'Tạo job',
      tag: 'PDF · OCR',
      tagColor: 'bg-rose-500',
      method: 'POST',
      path: '/functions/v1/analyze-jobs',
      desc: 'OCR mode — trả về markdown raw từng trang. Dùng khi: KHÔNG có prompt, hoặc ocrMode=true (kể cả có prompt thì vẫn show OCR + extraction).',
      sample: `# OCR thuần — không câu hỏi, không cần ocrMode
curl -X POST ${base}/functions/v1/analyze-jobs \\
  -H "x-api-key: ${exampleKey}" \\
  -F file=@./tailieu.pdf

# OCR + extraction (cần markdown raw để verify) — set ocrMode=true
curl -X POST ${base}/functions/v1/analyze-jobs \\
  -H "x-api-key: ${exampleKey}" \\
  -F file=@./tailieu.pdf \\
  -F ocrMode=true \\
  -F prompt="Lấy giúp các trường: Tax code, Total amount"`
    },
    {
      title: 'analyze-pdf-extract',
      group: 'Tạo job',
      tag: 'PDF · Extract',
      tagColor: 'bg-emerald-500',
      method: 'POST',
      path: '/functions/v1/analyze-jobs',
      desc: 'Extraction mode — OCR chạy ngầm, chỉ trả về kết quả trích xuất theo câu hỏi user. Trigger: có prompt + KHÔNG set ocrMode (hoặc ocrMode=false). Output gọn, không kèm markdown OCR.',
      sample: `curl -X POST ${base}/functions/v1/analyze-jobs \\
  -H "x-api-key: ${exampleKey}" \\
  -F file=@./hoa-don.pdf \\
  -F prompt="Lấy MST, ngày, tổng tiền VAT"

# Response (sau khi poll/stream tới status=done):
# { "status": "done", "content": "| Trường | Giá trị |\\n| --- | --- |\\n| MST | ... |", ... }`
    },
    {
      title: 'analyze-status',
      group: 'Theo dõi',
      tag: 'Poll',
      tagColor: 'bg-amber-500',
      method: 'GET',
      path: '/functions/v1/analyze-jobs/:jobId',
      desc: 'Snapshot { status, content, currentStep, logs, errorMsg? } — poll mỗi 1-2s cho tới khi status=done. Hoặc dùng /stream (SSE) để nhận push realtime.',
      sample: `curl ${base}/functions/v1/analyze-jobs/<JOB_ID> \\
  -H "x-api-key: ${exampleKey}"

# Response:
# {
#   "status": "running" | "done" | "error" | "cancelled",
#   "content": "...",          // markdown OCR hoặc extraction result
#   "currentStep": "bi-cpu|Trang 2/3 — AI đang OCR...",
#   "logs": ["[10:00:01] ...", ...],
#   "mode": "pdf",
#   "ocrMode": false,           // chỉ ra mode đang dùng
#   "pageIdx": 1, "pageTotal": 3,
#   "finishedAt": 1747...
# }`
    },
    {
      title: 'analyze-stream',
      group: 'Theo dõi',
      tag: 'SSE',
      tagColor: 'bg-indigo-500',
      method: 'GET',
      path: '/functions/v1/analyze-jobs/:jobId/stream',
      desc: 'Server-Sent Events — BE push snapshot mỗi khi content/status thay đổi. Tiết kiệm hơn poll. Auth qua ?token= (EventSource không support custom header).',
      sample: `# JS (browser / Node) với EventSource:
const es = new EventSource(
  '${base}/functions/v1/analyze-jobs/<JOB_ID>/stream?token=${exampleKey}'
)
es.onmessage = (e) => {
  const snap = JSON.parse(e.data)
  console.log(snap.status, snap.content.length)
  if (snap.status !== 'running') es.close()
}

# Heartbeat ': ping' 15s/lần — bỏ qua các dòng bắt đầu bằng ':'`
    },
    {
      title: 'analyze-cancel',
      group: 'Theo dõi',
      tag: 'Cancel',
      tagColor: 'bg-rose-500',
      method: 'DELETE',
      path: '/functions/v1/analyze-jobs/:jobId',
      desc: 'Huỷ job đang chạy. Snapshot vẫn lấy được nội dung partial sau khi cancel.',
      sample: `curl -X DELETE ${base}/functions/v1/analyze-jobs/<JOB_ID> \\
  -H "x-api-key: ${exampleKey}"`
    },
    {
      title: 'models-list',
      group: 'Khác',
      tag: 'Models',
      tagColor: 'bg-emerald-500',
      method: 'GET',
      path: '/functions/v1/ollama-models',
      desc: 'Danh sách model có sẵn trên Ollama server.',
      sample: `curl ${base}/functions/v1/ollama-models \\
  -H "x-api-key: ${exampleKey}"`
    }
  ]
})

// Nhóm theo group để render thành section riêng — đẹp hơn list phẳng
const endpointGroups = computed(() => {
  const map = new Map()
  for (const ep of endpoints.value) {
    if (!map.has(ep.group)) map.set(ep.group, [])
    map.get(ep.group).push(ep)
  }
  return Array.from(map, ([group, items]) => ({ group, items }))
})

const saving = ref(false)

// #region ALD 20/05/2026 - Dirty detection per-tab (memory + appearance tự lưu nên không tính)
function groupDirty(keys) {
  return keys.some((k) => (form.value[k] ?? '') !== (initialSettings.value[k] ?? ''))
}
const modelsDirty = computed(() => groupDirty(MODEL_KEYS))
const dirty       = computed(() => modelsDirty.value)

function tabHasDirty(id) {
  if (id === 'models') return modelsDirty.value
  return false
}
// #endregion

// #region ALD 20/05/2026 - Load
async function loadAll() {
  loadingModels.value = true
  loadingKeys.value = true
  loadingMemory.value = true
  try {
    const [modelsRes, settingsRes, memoryRes, keysRes] = await Promise.all([
      auth.apiFetch('/functions/v1/ollama-models').catch(() => ({ items: [] })),
      auth.apiFetch('/functions/v1/app-settings').catch(() => ({ settings: {} })),
      auth.apiFetch('/functions/v1/memories').catch(() => ({ items: [] })),
      auth.apiFetch('/functions/v1/api-keys').catch(() => ({ items: [] }))
    ])
    modelOptions.value = modelsRes.items ?? []
    initialSettings.value = settingsRes.settings ?? {}
    form.value = { ...form.value, ...initialSettings.value }
    memoryItems.value = memoryRes.items ?? []
    apiKeys.value = keysRes.items ?? []
  } finally {
    loadingModels.value = false
    loadingKeys.value = false
    loadingMemory.value = false
  }
}
// #endregion

// #region ALD 20/05/2026 - Save (chỉ models — memory & appearance tự lưu)
async function saveAll() {
  if (!dirty.value) return
  saving.value = true
  try {
    await auth.apiFetch('/functions/v1/app-settings', { method: 'PUT', body: { settings: form.value } })
    initialSettings.value = { ...form.value }
    toast.success('Đã lưu cài đặt')
  } catch (err) {
    toast.error(err?.data?.error || 'Lưu thất bại.')
  } finally {
    saving.value = false
  }
}
// #endregion

// #region ALD 20/05/2026 - Memory items: add / delete / clear all (tự lưu, không cần Save button)
async function addMemoryItem() {
  const content = newMemoryDraft.value.trim()
  if (!content || addingMemory.value) return
  addingMemory.value = true
  try {
    const res = await auth.apiFetch('/functions/v1/memories', {
      method: 'POST',
      body: { content }
    })
    if (res?.item) {
      memoryItems.value = [res.item, ...memoryItems.value]
      newMemoryDraft.value = ''
      await nextTick()
      memoryInputRef.value?.focus()
    }
  } catch (err) {
    toast.error(err?.data?.error || 'Không thêm được ghi nhớ')
  } finally {
    addingMemory.value = false
  }
}

async function deleteMemoryItem(item) {
  const ok = await confirm.ask({
    title: 'Xóa ghi nhớ này?',
    message: `"${item.content.slice(0, 100)}${item.content.length > 100 ? '…' : ''}"`,
    confirmText: 'Xóa',
    variant: 'danger'
  })
  if (!ok) return
  try {
    await auth.apiFetch(`/functions/v1/memories/${item.id}`, { method: 'DELETE' })
    memoryItems.value = memoryItems.value.filter((m) => m.id !== item.id)
    toast.success('Đã xóa')
  } catch (err) {
    toast.error(err?.data?.error || 'Xóa thất bại')
  }
}

async function clearAllMemory() {
  const ok = await confirm.ask({
    title: 'Xóa TẤT CẢ Rules?',
    message: `Bạn sẽ mất ${memoryItems.value.length} ghi nhớ. Hành động không thể hoàn tác.`,
    confirmText: 'Xóa tất cả',
    variant: 'danger'
  })
  if (!ok) return
  try {
    await auth.apiFetch('/functions/v1/memories', { method: 'DELETE' })
    memoryItems.value = []
    toast.success('Đã xóa toàn bộ bộ nhớ')
  } catch (err) {
    toast.error(err?.data?.error || 'Xóa thất bại')
  }
}
// #endregion

// #region ALD 20/05/2026 - API Keys CRUD
function openCreateKey() {
  keyForm.value = { name: '', description: '' }
  newlyCreatedKey.value = ''
  copiedKey.value = ''
  createKeyOpen.value = true
}
function closeCreateKey() {
  createKeyOpen.value = false
  if (newlyCreatedKey.value) {
    newlyCreatedKey.value = ''
    loadKeys()
  }
}
async function onCreateKey() {
  if (!keyForm.value.name.trim() || creatingKey.value) return
  creatingKey.value = true
  try {
    const res = await auth.apiFetch('/functions/v1/api-keys', { method: 'POST', body: keyForm.value })
    newlyCreatedKey.value = res?.item?.key || ''
    toast.success('Đã tạo key. Copy ngay!')
  } catch (err) {
    toast.error(err?.data?.error || 'Tạo key thất bại')
  } finally {
    creatingKey.value = false
  }
}
async function loadKeys() {
  loadingKeys.value = true
  try {
    const res = await auth.apiFetch('/functions/v1/api-keys')
    apiKeys.value = res.items ?? []
  } finally {
    loadingKeys.value = false
  }
}
async function revokeKey(k) {
  const ok = await confirm.ask({
    title: 'Thu hồi API key?',
    message: `Key "${k.name}" sẽ bị vô hiệu hóa ngay lập tức. App đang dùng key này sẽ ngừng hoạt động.`,
    confirmText: 'Thu hồi',
    variant: 'danger'
  })
  if (!ok) return
  try {
    await auth.apiFetch(`/functions/v1/api-keys/${k.id}`, { method: 'DELETE' })
    toast.success('Đã thu hồi key')
    loadKeys()
  } catch (err) {
    toast.error(err?.data?.error || 'Thu hồi thất bại')
  }
}
async function copyKey() {
  try {
    await copyText(newlyCreatedKey.value)
    copiedKey.value = '_new'
    setTimeout(() => (copiedKey.value = ''), 2000)
  } catch { /* ignore */ }
}
async function copySample(ep) {
  try {
    await copyText(ep.sample)
    copiedKey.value = ep.title
    setTimeout(() => (copiedKey.value = ''), 2000)
  } catch { /* ignore */ }
}
async function copyBase() {
  try {
    await copyText(supabaseUrl.value)
    copiedKey.value = '_base'
    setTimeout(() => (copiedKey.value = ''), 2000)
  } catch { /* ignore */ }
}
// #endregion

function formatRelative(ts) {
  if (!ts) return ''
  const t = typeof ts === 'string' ? Date.parse(ts) : ts
  const diff = Date.now() - t
  const min = Math.floor(diff / 60000)
  if (min < 1) return 'vừa xong'
  if (min < 60) return `${min} phút trước`
  const hour = Math.floor(min / 60)
  if (hour < 24) return `${hour} giờ trước`
  const day = Math.floor(hour / 24)
  if (day < 7) return `${day} ngày trước`
  return new Date(t).toLocaleDateString('vi-VN')
}

// #region ALD 20/05/2026 - Admin Jobs tab (list + delete stuck/all jobs)
const jobs = ref([])
const loadingJobs = ref(false)

function badgeVariant(status) {
  if (status === 'running') return 'warning'
  if (status === 'done') return 'success'
  if (status === 'cancelled') return 'neutral'
  return 'danger' // error
}

async function loadJobs() {
  loadingJobs.value = true
  try {
    const res = await auth.apiFetch('/functions/v1/analyze-jobs/_admin/jobs')
    jobs.value = res?.items ?? []
  } catch (err) {
    toast.error(err?.data?.error || 'Không tải được jobs')
  } finally {
    loadingJobs.value = false
  }
}

async function deleteJob(j) {
  const ok = await confirm.ask({
    title: 'Xoá job này?',
    message: `Job ${j.jobId.slice(0, 12)}… (${j.status}). Worker đang chạy sẽ bị cancel, Redis state bị xoá. Hành động không hoàn tác.`,
    confirmText: 'Xoá',
    variant: 'danger'
  })
  if (!ok) return
  try {
    await auth.apiFetch(`/functions/v1/analyze-jobs/_admin/jobs/${j.jobId}`, { method: 'DELETE' })
    jobs.value = jobs.value.filter((x) => x.jobId !== j.jobId)
    toast.success('Đã xoá job')
  } catch (err) {
    toast.error(err?.data?.error || 'Xoá thất bại')
  }
}

// Auto-load khi mở tab Jobs lần đầu
watch(activeTab, (id) => {
  if (id === 'jobs' && !jobs.value.length && !loadingJobs.value) loadJobs()
})
// #endregion

// ALD 31/05/2026 - Bỏ onMounted(loadAll): các endpoint ollama-models/app-settings/memories/
// api-keys là của local-ai (404 trên motion-backend). Settings giờ chỉ có Storage (tự load).
</script>
