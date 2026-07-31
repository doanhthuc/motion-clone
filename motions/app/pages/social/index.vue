<template>
  <!-- #region ALD 05/07/2026 - Social Management v3: TẤT CẢ form tạo/sửa bài đăng dùng Modal popup hoặc Drawer
       (không còn form nổi giữa dashboard chính), step-by-step cho form dài (Tạo bài đăng, Test đăng thử), và
       KHÔNG dùng <select> gốc ở đâu nữa (native <select> vỡ theme tối, hiện lỗi "value option" — thay bằng
       <SocialDropdown> tự vẽ). Kế hoạch có phân trang (client-side, 6 slot/trang) vì có thể nhiều lịch theo thời gian. -->
  <div class="flex-1 min-h-0 flex flex-col">
    <div class="flex-1 min-h-0 flex flex-col gap-4 px-3 sm:px-6 pt-1 pb-3 overflow-hidden">

      <!-- ══════ HEADER ══════ -->
      <div class="flex items-start justify-between gap-4 flex-shrink-0">
        <div class="flex items-center gap-3 min-w-0">
          <span class="inline-flex h-11 w-11 items-center justify-center rounded-xl bg-primary-50 border border-[rgba(94,106,210,0.14)] text-primary flex-shrink-0">
            <i class="bi bi-share-fill text-lg" />
          </span>
          <div class="min-w-0">
            <h1 class="text-lg font-semibold tracking-tight text-gray-900">Social Management</h1>
            <p class="text-xs text-gray-500 truncate">Kết nối, đăng và lên lịch nội dung MXH — Facebook &amp; TikTok.</p>
          </div>
        </div>
        <div class="flex items-center gap-2 flex-shrink-0">
          <div v-if="social.items.value.length" class="hidden sm:flex -space-x-2">
            <img v-for="a in social.items.value.slice(0, 5)" :key="a.id" :src="a.avatar_url" :title="a.name"
                 class="w-7 h-7 rounded-full border-2 border-gray-300 object-cover bg-gray-100 shadow-sm" >
          </div>
          <button type="button" class="lq-btn" title="Test đăng thử 1 file bất kỳ (không qua workflow)" @click="openUploadTest">
            <i class="bi bi-flask" />
          </button>
          <button type="button" class="lq-btn" @click="openHistory">
            <i class="bi bi-clock-history" /> Lịch sử
          </button>
          <button type="button" class="lq-btn" @click="helpOpen = true">
            <i class="bi bi-question-circle" /> Hướng dẫn
          </button>
        </div>
      </div>

      <!-- ══════ MAIN GRID: Chọn nội dung để đăng (mở modal) + Tài khoản ══════ -->
      <div class="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-3 gap-4">

        <!-- ── Chọn output để tạo bài đăng — click mở modal step-by-step ── -->
        <section class="lg:col-span-2 flex flex-col min-h-0 lq-panel p-4 sm:p-5">
          <h2 class="text-sm font-semibold text-gray-900 flex items-center gap-2 flex-shrink-0 mb-3">
            <i class="bi bi-stars text-primary" /> Tạo bài đăng từ output workflow
          </h2>

          <div class="flex-1 min-h-0 overflow-y-auto pr-1">
            <p v-if="posts.loading.value" class="text-xs text-gray-400">Đang tải…</p>
            <p v-else-if="!posts.recommendations.value.length" class="text-xs text-gray-400">
              Chưa có output nào chưa đăng. Chạy workflow xong (Output có video/ảnh) sẽ xuất hiện ở đây.
            </p>
            <div v-else class="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 gap-2">
              <!-- ALD 06/07/2026 - KHÔNG autoplay video preview (đỡ nặng, đỡ nhấp nháy).
                   preload=metadata vẫn bắn loadedmetadata để lấy duration cho TikTok validation. -->
              <button
                v-for="item in posts.recommendations.value" :key="item.workflow_run_id" type="button"
                class="group relative rounded-xl overflow-hidden border border-white/[0.07] hover:border-[rgba(94,106,210,0.55)] hover:shadow-card-hover transition-all text-left shadow-sm"
                @click="openComposer(item)"
              >
                <div class="w-full aspect-square bg-gray-100 flex items-center justify-center overflow-hidden relative">
                  <video v-if="item.preview.kind === 'video'" :src="item.preview.url" class="w-full h-full object-cover" muted playsinline preload="metadata" @loadedmetadata="previewDurations[item.workflow_run_id] = $event.target.duration" />
                  <img v-else-if="item.preview.kind === 'image'" :src="item.preview.url" class="w-full h-full object-cover" >
                  <i v-else class="bi bi-file-earmark text-xl text-gray-300" />
                  <span v-if="item.preview.kind === 'video'" class="absolute inset-0 flex items-center justify-center">
                    <span class="flex h-9 w-9 items-center justify-center rounded-full bg-black/45 text-white text-base backdrop-blur-sm transition-transform group-hover:scale-110">
                      <i class="bi bi-play-fill" />
                    </span>
                  </span>
                </div>
                <p class="text-[10px] text-gray-600 truncate px-1.5 py-1 bg-gray-50">{{ item.workflow_name }}</p>
              </button>
            </div>
          </div>
        </section>

        <!-- ── Tài khoản ── -->
        <aside class="flex flex-col min-h-0 lq-panel p-4 sm:p-5">
          <h2 class="text-sm font-semibold text-gray-900 flex items-center gap-2 flex-shrink-0 mb-3">
            <i class="bi bi-person-badge text-primary" /> Tài khoản kết nối
          </h2>
          <div class="flex-1 min-h-0 overflow-y-auto space-y-4">
            <div v-for="p in ['facebook', 'tiktok']" :key="p">
              <div class="flex items-center justify-between mb-1.5">
                <span class="text-xs font-medium text-gray-700 flex items-center gap-1.5">
                  <i :class="['bi', p === 'facebook' ? 'bi-facebook text-blue-500' : 'bi-tiktok text-gray-900']" />
                  {{ p === 'facebook' ? 'Facebook Page' : 'TikTok' }}
                </span>
                <button type="button" class="text-[11px] font-medium text-primary hover:text-primary-dark disabled:opacity-40" :disabled="social.connecting.value === p" @click="doConnect(p)">
                  <i class="bi bi-plus-circle" /> Kết nối
                </button>
              </div>
              <p v-if="!accountsByPlatform(p).length" class="text-[11px] text-gray-400">Chưa kết nối tài khoản nào.</p>
              <div v-else class="space-y-1.5">
                <div v-for="a in accountsByPlatform(p)" :key="a.id" class="flex items-center gap-2 px-2 py-2 rounded-[10px] bg-gray-50 border border-white/[0.06] shadow-sm">
                  <img v-if="a.avatar_url" :src="a.avatar_url" class="w-6 h-6 rounded-full object-cover flex-shrink-0 bg-gray-100" >
                  <span class="flex-1 min-w-0 truncate text-xs text-gray-800">{{ a.name || a.external_id }}</span>
                  <button type="button" class="text-gray-400 hover:text-rose-600 flex-shrink-0" @click="doDisconnect(a.id)"><i class="bi bi-x-lg text-[10px]" /></button>
                </div>
              </div>
            </div>
            <p class="text-[10px] text-gray-400 border-t border-white/[0.06] pt-3">
              <i class="bi bi-info-circle me-1" />TikTok chưa audit Content Posting API → chỉ đăng SELF_ONLY, tài khoản phải bật Riêng tư.
            </p>
          </div>
        </aside>
      </div>

      <!-- ══════ Kế hoạch (dải ngang, có phân trang) ══════ -->
      <section class="flex-shrink-0">
        <div class="flex items-center justify-between mb-2">
          <h2 class="text-sm font-semibold text-gray-900 flex items-center gap-2">
            <i class="bi bi-calendar-check text-primary" /> Kế hoạch
          </h2>
          <div class="flex items-center gap-3">
            <div v-if="slotsTotalPages > 1" class="flex items-center gap-2">
              <button type="button" class="lq-page-btn" :disabled="slotsPage <= 1" @click="slotsPage--"><i class="bi bi-chevron-left" /></button>
              <span class="text-[11px] text-gray-500">{{ slotsPage }}/{{ slotsTotalPages }}</span>
              <button type="button" class="lq-page-btn" :disabled="slotsPage >= slotsTotalPages" @click="slotsPage++"><i class="bi bi-chevron-right" /></button>
            </div>
            <button type="button" class="text-[11px] font-medium text-primary hover:text-primary-dark" @click="openQuickSlotEditor">
              <i class="bi bi-plus-circle" /> Thêm lịch
            </button>
          </div>
        </div>
        <div class="flex gap-2 overflow-x-auto pb-1">
          <p v-if="!allSlots.length" class="text-[11px] text-gray-400 py-3">Chưa có lịch nào — bấm "Thêm lịch" để đặt N bài/ngày tự động.</p>
          <div
            v-for="slot in pagedSlots" :key="slot.id" role="button" tabindex="0"
            class="flex-shrink-0 w-44 lq-card !rounded-xl px-3 py-2.5 hover:border-[rgba(94,106,210,0.4)] transition-colors cursor-pointer"
            @click="openSlotEditor(slot.plan_id, slot)"
          >
            <div class="flex items-center justify-between">
              <span class="text-xs font-semibold text-gray-900 tabular-nums">{{ slot.time_of_day?.slice(0, 5) }}</span>
              <span class="text-[9px] text-gray-400">{{ weekdaysShort(slot.weekdays) }}</span>
            </div>
            <p class="text-[11px] text-gray-700 truncate mt-1">{{ slot.label || workflowName(slot.workflow_id) }}</p>
            <div class="flex items-center gap-1.5 mt-1.5 text-[10px] text-gray-500">
              <span v-if="slot.facebook?.enabled"><i class="bi bi-facebook" /> {{ slot.facebook.accountIds.length }}</span>
              <span v-if="slot.tiktok?.enabled"><i class="bi bi-tiktok" /> {{ slot.tiktok.accountIds.length }}</span>
              <span :class="['ml-auto px-1.5 py-0.5 rounded-full font-semibold', slot.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-400']">{{ slot.is_active ? 'Bật' : 'Tắt' }}</span>
              <button type="button" class="text-gray-400 hover:text-rose-600" @click.stop="doDeleteSlot(slot.id)"><i class="bi bi-trash" /></button>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- ══════ History drawer ══════ -->
    <Transition enter-active-class="transition-opacity duration-200" leave-active-class="transition-opacity duration-150" enter-from-class="opacity-0" leave-to-class="opacity-0">
      <div v-if="historyOpen" class="fixed inset-0 z-[1000] lq-backdrop" @click.self="historyOpen = false" />
    </Transition>
    <Transition enter-active-class="transition-transform duration-220 ease-out" leave-active-class="transition-transform duration-180 ease-in" enter-from-class="translate-x-full" leave-to-class="translate-x-full">
      <div v-if="historyOpen" class="fixed inset-y-0 right-0 z-[1001] w-full max-w-md">
        <div class="lq-drawer">
          <div class="flex items-center justify-between px-5 py-4 border-b border-white/[0.06] flex-shrink-0">
            <p class="font-semibold text-gray-900 text-sm flex items-center gap-2"><i class="bi bi-clock-history text-primary" /> Lịch sử hoạt động</p>
            <button type="button" class="text-gray-400 hover:text-gray-900" @click="historyOpen = false"><i class="bi bi-x-lg" /></button>
          </div>
          <div class="flex-1 overflow-y-auto px-4 py-3 space-y-2">
            <p v-if="!posts.history.value.length" class="text-[11px] text-gray-400 py-6 text-center">Chưa có bài đăng nào.</p>
            <div v-for="h in posts.history.value" :key="h.id" class="lq-card !rounded-xl p-3">
              <div class="flex items-center gap-1.5 mb-1">
                <i :class="['bi text-sm flex-shrink-0', h.platform === 'facebook' ? 'bi-facebook text-blue-500' : 'bi-tiktok text-gray-900']" />
                <span class="text-xs font-medium text-gray-800 truncate flex-1">{{ h.account_name }}</span>
                <span :class="['text-[9px] px-1.5 py-0.5 rounded-full font-semibold flex-shrink-0', statusDarkClass(h.status)]">{{ statusLabel(h.status) }}</span>
              </div>
              <p class="text-[11px] text-gray-500 truncate">{{ h.workflow_name }} · {{ formatTime(h.scheduled_for) }}</p>
              <p v-if="h.error_msg" class="text-[11px] text-rose-600 mt-1 line-clamp-2">{{ h.error_msg }}</p>
              <button v-if="h.status === 'scheduled'" type="button" class="text-[11px] font-semibold text-rose-600 hover:text-rose-500 mt-1.5" @click="posts.cancelPost(h.id)">Huỷ</button>
            </div>
          </div>
          <div class="flex items-center justify-between px-5 py-3 border-t border-white/[0.06] flex-shrink-0">
            <button type="button" class="lq-page-btn" :disabled="posts.historyPage.value <= 1" @click="posts.loadHistory(posts.historyPage.value - 1)"><i class="bi bi-chevron-left" /></button>
            <span class="text-[11px] text-gray-500">Trang {{ posts.historyPage.value }}/{{ historyTotalPages }} · {{ posts.historyTotal.value }} bài</span>
            <button type="button" class="lq-page-btn" :disabled="posts.historyPage.value >= historyTotalPages" @click="posts.loadHistory(posts.historyPage.value + 1)"><i class="bi bi-chevron-right" /></button>
          </div>
        </div>
      </div>
    </Transition>

    <!-- ══════ Modal: Tạo bài đăng (step-by-step) ══════ -->
    <Transition enter-active-class="transition-opacity duration-200" leave-active-class="transition-opacity duration-150" enter-from-class="opacity-0" leave-to-class="opacity-0">
      <div v-if="composerModalOpen" class="fixed inset-0 z-[1000] lq-backdrop" @click.self="closeComposer" />
    </Transition>
    <Transition enter-active-class="transition-all duration-200 ease-out" leave-active-class="transition-all duration-150 ease-in" enter-from-class="opacity-0 scale-95" leave-to-class="opacity-0 scale-95">
      <div v-if="composerModalOpen" class="fixed inset-0 z-[1001] flex items-center justify-center p-4">
        <!-- ALD 06/07/2026 - Modal CAO CỐ ĐỊNH (không nhảy chiều cao giữa các bước), preview media bên trái,
             chọn nền tảng bằng select-card + switch, tài khoản dạng chip. Video preview KHÔNG autoplay. -->
        <div class="lq-modal w-full max-w-2xl h-[min(640px,88vh)]">
          <div class="flex items-center justify-between px-5 py-4 border-b border-white/[0.06] flex-shrink-0">
            <div class="flex items-center gap-3 min-w-0">
              <span class="flex h-8 w-8 items-center justify-center rounded-[10px] bg-primary-50 text-primary flex-shrink-0"><i class="bi bi-send" /></span>
              <div class="min-w-0">
                <p class="font-semibold text-gray-900 text-sm leading-tight">Tạo bài đăng</p>
                <p class="text-[11px] text-gray-400">Bước {{ composerStepIndex }}/{{ composerStepCount }} · {{ composerStepTitle }}</p>
              </div>
            </div>
            <button type="button" class="lq-btn lq-btn--ghost lq-btn--icon !h-8 !w-8" @click="closeComposer"><i class="bi bi-x-lg" /></button>
          </div>
          <div class="flex items-center gap-1.5 px-5 pt-3 flex-shrink-0">
            <span v-for="n in composerStepCount" :key="n" class="h-1 flex-1 rounded-full transition-colors duration-300" :class="n <= composerStepIndex ? 'bg-primary' : 'bg-white/[0.08]'" />
          </div>

          <div class="flex-1 min-h-0 flex overflow-hidden">
            <!-- Preview media cố định bên trái -->
            <div class="hidden sm:flex w-52 flex-shrink-0 flex-col items-center justify-center gap-2 border-r border-white/[0.06] bg-white/[0.02] p-4">
              <div class="w-full aspect-[9/16] max-h-[380px] rounded-xl overflow-hidden bg-gray-100 border border-white/[0.06] flex items-center justify-center">
                <video v-if="composerPreview?.kind === 'video'" :src="composerPreview.url" class="w-full h-full object-cover" muted playsinline preload="metadata" controls />
                <img v-else-if="composerPreview?.kind === 'image'" :src="composerPreview.url" class="w-full h-full object-cover" >
                <i v-else class="bi bi-file-earmark text-2xl text-gray-300" />
              </div>
              <p class="text-[10px] text-gray-400 truncate w-full text-center">{{ composerPreview?.workflowName }}</p>
            </div>

            <!-- Nội dung step — scroll riêng, chiều cao không đổi -->
            <div class="flex-1 min-w-0 overflow-y-auto px-5 py-4 space-y-4">
              <!-- Step: Nền tảng & Caption -->
              <template v-if="composerStep === 1">
                <div>
                  <label class="lq-label mb-1.5">Caption</label>
                  <textarea v-model="composer.caption" rows="3" placeholder="Viết caption cho bài đăng…" class="lq-textarea" />
                </div>
                <div class="space-y-2.5">
                  <label class="lq-label">Nền tảng</label>
                  <div v-for="p in ['facebook', 'tiktok']" :key="p" :class="['lq-select-card flex-col !items-stretch', composer[p].enabled && 'is-on']">
                    <div class="flex items-center gap-2 cursor-pointer" @click="composer[p].enabled = !composer[p].enabled">
                      <i :class="['bi text-base', p === 'facebook' ? 'bi-facebook text-blue-500' : 'bi-tiktok text-gray-900']" />
                      <span class="flex-1 text-[13px] font-medium text-gray-900">{{ p === 'facebook' ? 'Facebook Page' : 'TikTok' }}</span>
                      <input type="checkbox" class="lq-switch" v-model="composer[p].enabled" @click.stop >
                    </div>
                    <div v-if="composer[p].enabled" class="mt-2.5 pt-2.5 border-t border-white/[0.05] flex flex-wrap gap-1.5">
                      <p v-if="!accountsByPlatform(p).length" class="text-[11px] text-gray-400">Chưa kết nối tài khoản nào.</p>
                      <button
                        v-for="a in accountsByPlatform(p)" :key="a.id" type="button"
                        :class="[
                          'inline-flex items-center gap-1.5 h-7 px-2 rounded-full text-[11px] font-medium border transition-colors',
                          (p === 'tiktok' ? composer.tiktok.accountIds[0] === a.id : composer[p].accountIds.includes(a.id))
                            ? 'bg-primary border-primary text-white'
                            : 'bg-gray-50 border-white/[0.1] text-gray-700 hover:border-black/[0.2]'
                        ]"
                        @click="p === 'tiktok' ? (composer.tiktok.accountIds = [a.id]) : toggleComposerAccount(p, a.id)"
                      >
                        <img v-if="a.avatar_url" :src="a.avatar_url" class="w-4 h-4 rounded-full object-cover" >
                        {{ a.name || a.external_id }}
                      </button>
                    </div>
                  </div>
                </div>
              </template>

              <!-- Step: Cài đặt TikTok (chỉ khi TikTok bật) -->
              <template v-else-if="composerStep === 2">
                <SocialTikTokFields
                  v-model="composer.tiktokFields"
                  :account-id="composer.tiktok.accountIds[0] || null"
                  :video-duration-sec="previewDurations[composerFor] || null"
                />
              </template>

              <!-- Step: Lịch đăng & Xác nhận -->
              <template v-else>
                <div>
                  <label class="lq-label mb-1.5">Thời gian đăng</label>
                  <div class="grid grid-cols-2 gap-2">
                    <button type="button" :class="['lq-select-card !py-2.5 justify-center text-[13px] font-medium', composer.scheduleMode === 'now' && 'is-on']" @click="composer.scheduleMode = 'now'">
                      <i class="bi bi-lightning-charge" /> Đăng ngay
                    </button>
                    <button type="button" :class="['lq-select-card !py-2.5 justify-center text-[13px] font-medium', composer.scheduleMode === 'later' && 'is-on']" @click="composer.scheduleMode = 'later'">
                      <i class="bi bi-clock" /> Hẹn giờ
                    </button>
                  </div>
                  <input v-if="composer.scheduleMode === 'later'" v-model="composer.scheduleAt" type="datetime-local" class="lq-input mt-2" >
                </div>
                <div>
                  <label class="lq-label mb-1.5">Tóm tắt</label>
                  <div class="lq-card !rounded-xl divide-y divide-white/[0.05]">
                    <p v-if="composer.facebook.enabled" class="flex items-center gap-2 px-3 py-2.5 text-xs text-gray-700"><i class="bi bi-facebook text-blue-500" /> Facebook — {{ composer.facebook.accountIds.length }} tài khoản</p>
                    <p v-if="composer.tiktok.enabled" class="flex items-center gap-2 px-3 py-2.5 text-xs text-gray-700"><i class="bi bi-tiktok" /> TikTok — {{ privacyLabelText(composer.tiktokFields.privacyLevel) }}</p>
                    <p class="flex items-center gap-2 px-3 py-2.5 text-xs text-gray-500"><i class="bi bi-chat-left-text" /> {{ composer.caption ? composer.caption.slice(0, 80) : '(không có caption)' }}</p>
                  </div>
                </div>
              </template>
            </div>
          </div>
          <div class="flex items-center justify-between gap-2 border-t border-white/[0.06] px-5 py-3.5 flex-shrink-0">
            <button v-if="composerStep > 1" type="button" class="lq-btn lq-btn--ghost" @click="composerBack"><i class="bi bi-arrow-left" /> Quay lại</button>
            <span v-else />
            <button v-if="composerStep < 3" type="button" class="lq-btn lq-btn--primary" @click="composerNext">Tiếp tục <i class="bi bi-arrow-right" /></button>
            <button v-else type="button" class="lq-btn lq-btn--primary" @click="submitComposer"><i class="bi bi-send" /> {{ composer.scheduleMode === 'later' ? 'Hẹn giờ đăng' : 'Đăng bài' }}</button>
          </div>
        </div>
      </div>
    </Transition>

    <!-- ══════ Modal: Test đăng thử (step-by-step) ══════ -->
    <Transition enter-active-class="transition-opacity duration-200" leave-active-class="transition-opacity duration-150" enter-from-class="opacity-0" leave-to-class="opacity-0">
      <div v-if="uploadTestOpen" class="fixed inset-0 z-[1000] lq-backdrop" @click.self="uploadTestOpen = false" />
    </Transition>
    <Transition enter-active-class="transition-all duration-200 ease-out" leave-active-class="transition-all duration-150 ease-in" enter-from-class="opacity-0 scale-95" leave-to-class="opacity-0 scale-95">
      <div v-if="uploadTestOpen" class="fixed inset-0 z-[1001] flex items-center justify-center p-4">
        <div class="lq-modal w-full max-w-lg h-[min(600px,88vh)]">
          <div class="flex items-center justify-between px-5 py-4 border-b border-white/[0.06] flex-shrink-0">
            <div class="flex items-center gap-3 min-w-0">
              <span class="flex h-8 w-8 items-center justify-center rounded-[10px] bg-primary-50 text-primary flex-shrink-0"><i class="bi bi-flask" /></span>
              <div class="min-w-0">
                <p class="font-semibold text-gray-900 text-sm leading-tight">Test đăng thử</p>
                <p class="text-[11px] text-gray-400">Bước {{ uploadTestStepIndex }}/{{ uploadTestStepCount }} · {{ uploadTestStepTitle }}</p>
              </div>
            </div>
            <button type="button" class="lq-btn lq-btn--ghost lq-btn--icon !h-8 !w-8" @click="uploadTestOpen = false"><i class="bi bi-x-lg" /></button>
          </div>
          <div class="flex items-center gap-1.5 px-5 pt-3 flex-shrink-0">
            <span v-for="n in uploadTestStepCount" :key="n" class="h-1 flex-1 rounded-full transition-colors duration-300" :class="n <= uploadTestStepIndex ? 'bg-primary' : 'bg-white/[0.08]'" />
          </div>

          <div class="flex-1 min-h-0 overflow-y-auto px-5 py-4 space-y-4">
            <!-- Step: File & nền tảng -->
            <template v-if="uploadTestStep === 1">
              <p class="text-[11px] text-gray-400">Đăng thử 1 file bất kỳ (video/ảnh) — không cần chạy workflow trước.</p>
              <div class="flex gap-3">
                <div v-if="uploadTest.previewUrl" class="w-20 h-20 rounded-xl overflow-hidden bg-gray-100 border border-white/[0.07] flex-shrink-0">
                  <video v-if="uploadTest.isVideo" :src="uploadTest.previewUrl" class="w-full h-full object-cover" muted playsinline preload="metadata" @loadedmetadata="uploadTest.durationSec = $event.target.duration" />
                  <img v-else :src="uploadTest.previewUrl" class="w-full h-full object-cover" >
                </div>
                <div class="flex-1 min-w-0">
                  <input type="file" accept="video/*,image/*" class="lq-file-input" @change="onUploadTestFile" >
                  <p v-if="uploadTest.fileName" class="text-[11px] text-emerald-600 mt-1"><i class="bi bi-check-circle" /> {{ uploadTest.fileName }}</p>
                </div>
              </div>
              <textarea v-model="uploadTest.caption" rows="2" placeholder="Caption…" class="lq-textarea" />
              <div class="space-y-2.5">
                <label class="lq-label">Nền tảng</label>
                <div v-for="p in ['facebook', 'tiktok']" :key="p" :class="['lq-select-card flex-col !items-stretch', uploadTest[p].enabled && 'is-on']">
                  <div class="flex items-center gap-2 cursor-pointer" @click="uploadTest[p].enabled = !uploadTest[p].enabled">
                    <i :class="['bi text-base', p === 'facebook' ? 'bi-facebook text-blue-500' : 'bi-tiktok text-gray-900']" />
                    <span class="flex-1 text-[13px] font-medium text-gray-900">{{ p === 'facebook' ? 'Facebook Page' : 'TikTok' }}</span>
                    <input type="checkbox" class="lq-switch" v-model="uploadTest[p].enabled" @click.stop >
                  </div>
                  <div v-if="uploadTest[p].enabled" class="mt-2.5 pt-2.5 border-t border-white/[0.05] flex flex-wrap gap-1.5">
                    <p v-if="!accountsByPlatform(p).length" class="text-[11px] text-gray-400">Chưa kết nối tài khoản nào.</p>
                    <button
                      v-for="a in accountsByPlatform(p)" :key="a.id" type="button"
                      :class="[
                        'inline-flex items-center gap-1.5 h-7 px-2 rounded-full text-[11px] font-medium border transition-colors',
                        (p === 'tiktok' ? uploadTest.tiktok.accountIds[0] === a.id : uploadTest[p].accountIds.includes(a.id))
                          ? 'bg-primary border-primary text-white'
                          : 'bg-gray-50 border-white/[0.1] text-gray-700 hover:border-black/[0.2]'
                      ]"
                      @click="p === 'tiktok' ? (uploadTest.tiktok.accountIds = [a.id]) : toggleUploadTestAccount(p, a.id)"
                    >
                      <img v-if="a.avatar_url" :src="a.avatar_url" class="w-4 h-4 rounded-full object-cover" >
                      {{ a.name || a.external_id }}
                    </button>
                  </div>
                </div>
              </div>
            </template>

            <!-- Step: Cài đặt TikTok -->
            <template v-else-if="uploadTestStep === 2">
              <SocialTikTokFields
                v-model="uploadTest.tiktokFields"
                :account-id="uploadTest.tiktok.accountIds[0] || null"
                :video-duration-sec="uploadTest.durationSec"
              />
            </template>

            <!-- Step: Xác nhận -->
            <template v-else>
              <div class="lq-card !rounded-xl p-3 text-[11px] text-gray-600 space-y-1">
                <p>File: <span class="text-gray-900">{{ uploadTest.fileName || '(chưa chọn)' }}</span></p>
                <p v-if="uploadTest.facebook.enabled"><i class="bi bi-facebook me-1" />Facebook: {{ uploadTest.facebook.accountIds.length }} tài khoản</p>
                <p v-if="uploadTest.tiktok.enabled"><i class="bi bi-tiktok me-1" />TikTok: {{ privacyLabelText(uploadTest.tiktokFields.privacyLevel) }}</p>
              </div>
            </template>
          </div>
          <div class="flex items-center justify-between gap-2 border-t border-white/[0.06] px-5 py-3.5 flex-shrink-0">
            <button v-if="uploadTestStep > 1" type="button" class="lq-btn lq-btn--ghost" @click="uploadTestBack">Quay lại</button>
            <span v-else />
            <button v-if="uploadTestStep < 3" type="button" class="lq-btn lq-btn--primary" @click="uploadTestNext">Tiếp tục <i class="bi bi-arrow-right" /></button>
            <button v-else type="button" class="lq-btn lq-btn--primary" :disabled="uploadTest.submitting" @click="submitUploadTest">
              <i :class="['bi', uploadTest.submitting ? 'bi-arrow-repeat animate-spin' : 'bi-send']" /> Đăng thử ngay
            </button>
          </div>
        </div>
      </div>
    </Transition>

    <!-- ══════ Slot editor drawer (thay bottom sheet cũ — nhất quán "modal/drawer" toàn trang) ══════ -->
    <Transition enter-active-class="transition-opacity duration-200" leave-active-class="transition-opacity duration-150" enter-from-class="opacity-0" leave-to-class="opacity-0">
      <div v-if="slotEditor.open" class="fixed inset-0 z-[1000] lq-backdrop" @click.self="slotEditor.open = false" />
    </Transition>
    <Transition enter-active-class="transition-transform duration-220 ease-out" leave-active-class="transition-transform duration-180 ease-in" enter-from-class="translate-x-full" leave-to-class="translate-x-full">
      <div v-if="slotEditor.open" class="fixed inset-y-0 right-0 z-[1001] w-full max-w-md">
        <div class="lq-drawer">
          <div class="flex items-center justify-between px-5 py-4 border-b border-white/[0.06] flex-shrink-0">
            <p class="font-semibold text-gray-900 text-sm">{{ slotEditor.slotId ? 'Sửa' : 'Thêm' }} bài đăng theo lịch</p>
            <button type="button" class="text-gray-400 hover:text-gray-900" @click="slotEditor.open = false"><i class="bi bi-x-lg" /></button>
          </div>
          <div class="flex-1 overflow-y-auto px-5 py-4 space-y-4">
            <div>
              <label class="lq-label">Tên bài (tuỳ chọn)</label>
              <input v-model="slotEditor.label" placeholder="vd: Post sáng" class="lq-input mt-1" >
            </div>
            <div>
              <label class="lq-label">Workflow</label>
              <SocialDropdown
                v-model="slotEditor.workflowId"
                :options="workflowOptions"
                placeholder="-- chọn workflow --"
                class="mt-1"
              />
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="lq-label">Giờ chạy hằng ngày</label>
                <input v-model="slotEditor.time" type="time" class="lq-input mt-1" >
              </div>
              <div>
                <label class="lq-label">Ngày trong tuần</label>
                <div class="mt-1 flex gap-1">
                  <button
                    v-for="d in WEEKDAY_LABELS" :key="d.value" type="button"
                    class="h-9 flex-1 rounded-lg text-[11px] font-semibold transition-colors"
                    :class="slotEditor.weekdays.includes(d.value) ? 'bg-primary text-white' : 'bg-white/[0.04] text-gray-500 hover:bg-white/[0.08]'"
                    @click="toggleSlotWeekday(d.value)"
                  >{{ d.label }}</button>
                </div>
              </div>
            </div>

            <div v-if="slotEditor.fields.length" class="space-y-2.5 border-t border-white/[0.06] pt-3">
              <label class="lq-label">Input đầu vào ({{ slotEditor.fields.length }})</label>
              <div v-for="f in slotEditor.fields" :key="f.nodeId">
                <label class="text-xs font-medium text-gray-600">{{ f.label || f.field }} <span class="text-gray-400">({{ f.contentType }})</span></label>
                <textarea v-if="f.contentType === 'text'" v-model="slotEditor.input[f.field]" rows="2" class="lq-textarea mt-1" />
                <div v-else class="mt-1">
                  <input type="file" class="lq-file-input" @change="onSlotFileInput(f.field, $event)" >
                  <p v-if="slotEditor.input[f.field]?.name" class="text-[11px] text-emerald-600 mt-1"><i class="bi bi-check-circle" /> {{ slotEditor.input[f.field].name }}</p>
                </div>
              </div>
            </div>
            <p v-else-if="slotEditor.workflowId" class="text-[11px] text-gray-400">Workflow này không có input cần soạn trước (session field) — chỉ cần bấm Lưu.</p>

            <div>
              <label class="lq-label">Caption</label>
              <textarea v-model="slotEditor.caption" rows="2" placeholder="Để trống → dùng text workflow trả về" class="lq-textarea mt-1" />
            </div>

            <div class="space-y-2.5">
              <label class="lq-label">Nền tảng</label>
              <div v-for="p in ['facebook', 'tiktok']" :key="p" :class="['lq-select-card flex-col !items-stretch', slotEditor[p].enabled && 'is-on']">
                <div class="flex items-center gap-2 cursor-pointer" @click="slotEditor[p].enabled = !slotEditor[p].enabled">
                  <i :class="['bi text-base', p === 'facebook' ? 'bi-facebook text-blue-500' : 'bi-tiktok text-gray-900']" />
                  <span class="flex-1 text-[13px] font-medium text-gray-900">{{ p === 'facebook' ? 'Facebook Page' : 'TikTok' }}</span>
                  <input type="checkbox" class="lq-switch" v-model="slotEditor[p].enabled" @click.stop >
                </div>
                <div v-if="slotEditor[p].enabled" class="mt-2.5 pt-2.5 border-t border-white/[0.05] flex flex-wrap gap-1.5">
                  <p v-if="!accountsByPlatform(p).length" class="text-[11px] text-gray-400">Chưa kết nối tài khoản nào.</p>
                  <button
                    v-for="a in accountsByPlatform(p)" :key="a.id" type="button"
                    :class="[
                      'inline-flex items-center gap-1.5 h-7 px-2 rounded-full text-[11px] font-medium border transition-colors',
                      (p === 'tiktok' ? slotEditor.tiktok.accountIds[0] === a.id : slotEditor[p].accountIds.includes(a.id))
                        ? 'bg-primary border-primary text-white'
                        : 'bg-gray-50 border-white/[0.1] text-gray-700 hover:border-black/[0.2]'
                    ]"
                    @click="p === 'tiktok' ? (slotEditor.tiktok.accountIds = [a.id]) : toggleSlotAccount(p, a.id)"
                  >
                    <img v-if="a.avatar_url" :src="a.avatar_url" class="w-4 h-4 rounded-full object-cover" >
                    {{ a.name || a.external_id }}
                  </button>
                </div>
              </div>
            </div>
            <SocialTikTokFields
              v-if="slotEditor.tiktok.enabled"
              v-model="slotEditor.tiktokFields"
              :account-id="slotEditor.tiktok.accountIds[0] || null"
            />
            <p v-if="slotEditor.tiktok.enabled" class="text-[10px] text-gray-400">Lưu ý: video sẽ do workflow tạo lúc chạy nên chưa kiểm tra được thời lượng trước — TikTok sẽ tự báo lỗi nếu vượt giới hạn khi đăng.</p>
          </div>
          <div class="flex items-center justify-end gap-2 border-t border-white/[0.06] px-5 py-3.5 flex-shrink-0">
            <button type="button" class="lq-btn lq-btn--ghost" @click="slotEditor.open = false">Huỷ</button>
            <button type="button" class="lq-btn lq-btn--primary" @click="saveSlot"><i class="bi bi-check2" /> Lưu</button>
          </div>
        </div>
      </div>
    </Transition>

    <SocialHelpDrawer v-model="helpOpen" @connect="onHelpConnect" />
  </div>
  <!-- #endregion -->
</template>

<script setup>
const social = useSocialAccounts()
const posts = useSocialPosts()
const helpOpen = ref(false)
async function onHelpConnect(platform) {
  helpOpen.value = false
  await doConnect(platform)
}
const plans = useContentPlans()
const workflows = useWorkflows()
const toast = useToast()

onMounted(() => {
  social.load()
  posts.loadRecommendations()
  posts.loadHistory(1)
  plans.load()
  workflows.load()
})

function accountsByPlatform(platform) {
  return social.items.value.filter((a) => a.platform === platform)
}
async function doConnect(platform) {
  try {
    await social.connect(platform)
    toast.success(platform === 'facebook' ? 'Đã kết nối Facebook Page' : 'Đã kết nối TikTok')
  } catch (e) {
    toast.error(e?.message || 'Kết nối thất bại')
  }
}
async function doDisconnect(id) {
  await social.disconnect(id)
  toast.info('Đã ngắt kết nối')
}
function formatTime(ts) {
  if (!ts) return ''
  try { return new Date(ts).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) }
  catch { return String(ts) }
}
function privacyLabelText(v) {
  return { SELF_ONLY: 'Chỉ mình tôi', PUBLIC_TO_EVERYONE: 'Công khai', MUTUAL_FOLLOW_FRIENDS: 'Bạn bè', FOLLOWER_OF_CREATOR: 'Người theo dõi' }[v] || (v || '(chưa chọn)')
}

// ALD 05/07/2026 - Lựa chọn TikTok compliance mặc định (Content Sharing Guidelines mục 4) — privacy BẮT BUỘC
// user tự chọn (rỗng = chưa chọn), tương tác mặc định TẮT, commercial content mặc định TẮT.
function defaultTiktokFields() {
  return { privacyLevel: '', disableDuet: true, disableComment: true, disableStitch: true, brandContentToggle: false, brandOrganicToggle: false }
}
const previewDurations = reactive({}) // workflow_run_id → duration(s), đọc từ @loadedmetadata của <video> preview

// ── History drawer ──────────────────────────────────────────────────────────────────────────────────
const historyOpen = ref(false)
function openHistory() {
  historyOpen.value = true
  posts.loadHistory(1)
}
const historyTotalPages = computed(() => Math.max(1, Math.ceil((posts.historyTotal.value || 0) / 20)))

// ── Compose modal: tạo bài đăng từ 1 output — step-by-step (1: nền tảng/caption, 2: TikTok, 3: lịch/xác nhận) ──
const composerModalOpen = ref(false)
const composerStep = ref(1)
const composerFor = ref(null)
// ALD 06/07/2026 - Giữ media preview cho panel trái của modal (kind/url/tên workflow)
const composerPreview = ref(null)
const composer = reactive({
  caption: '', scheduleMode: 'now', scheduleAt: '',
  facebook: { enabled: false, accountIds: [] },
  tiktok: { enabled: false, accountIds: [] },
  tiktokFields: defaultTiktokFields()
})
const composerVisibleSteps = computed(() => (composer.tiktok.enabled ? [1, 2, 3] : [1, 3]))
const composerStepIndex = computed(() => Math.max(1, composerVisibleSteps.value.indexOf(composerStep.value) + 1))
const composerStepCount = computed(() => composerVisibleSteps.value.length)
const composerStepTitle = computed(() => ({ 1: 'Nền tảng & Caption', 2: 'Cài đặt TikTok', 3: 'Lịch đăng & Xác nhận' }[composerStep.value] || ''))

function openComposer(item) {
  composerFor.value = item.workflow_run_id
  composerPreview.value = item.preview ? { kind: item.preview.kind, url: item.preview.url, workflowName: item.workflow_name } : null
  // ALD 06/07/2026 - KHÔNG prefill URL/signed-link vào caption (trước đây preview.text đôi khi là URL output → caption rất xấu)
  const t = (item.preview?.text || '').trim()
  composer.caption = /^https?:\/\/\S+$/i.test(t) ? '' : t
  composer.scheduleMode = 'now'
  composer.scheduleAt = ''
  composer.facebook = { enabled: false, accountIds: [] }
  composer.tiktok = { enabled: false, accountIds: [] }
  composer.tiktokFields = defaultTiktokFields()
  composerStep.value = 1
  composerModalOpen.value = true
}
function closeComposer() {
  composerModalOpen.value = false
  composerFor.value = null
  composerPreview.value = null
}
function toggleComposerAccount(platform, id) {
  const list = composer[platform].accountIds
  const i = list.indexOf(id)
  if (i >= 0) list.splice(i, 1); else list.push(id)
}
function composerNext() {
  if (composerStep.value === 1) {
    if (!composer.facebook.enabled && !composer.tiktok.enabled) { toast.error('Chọn ít nhất 1 nền tảng'); return }
    composerStep.value = composer.tiktok.enabled ? 2 : 3
  } else if (composerStep.value === 2) {
    if (!composer.tiktokFields.privacyLevel) { toast.error('TikTok: chưa chọn chế độ hiển thị (privacy)'); return }
    composerStep.value = 3
  }
}
function composerBack() {
  if (composerStep.value === 3) composerStep.value = composer.tiktok.enabled ? 2 : 1
  else composerStep.value = 1
}
async function submitComposer() {
  try {
    await posts.createPost({
      workflow_run_id: composerFor.value,
      caption: composer.caption,
      facebook: composer.facebook,
      tiktok: { ...composer.tiktok, ...composer.tiktokFields },
      scheduled_for: composer.scheduleMode === 'later' && composer.scheduleAt ? new Date(composer.scheduleAt).toISOString() : undefined
    })
    toast.success(composer.scheduleMode === 'later' ? 'Đã hẹn giờ đăng bài' : 'Đã xếp hàng đăng bài (đăng trong giây lát)')
    closeComposer()
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Tạo bài đăng thất bại')
  }
}

// ── Modal test đăng thử: upload file bất kỳ (không qua workflow) — step-by-step riêng, tách khỏi composer chính ──
const uploadTestOpen = ref(false)
const uploadTestStep = ref(1)
function openUploadTest() {
  uploadTestStep.value = 1
  uploadTestOpen.value = true
}
const uploadTest = reactive({
  file: null, fileName: '', caption: '', submitting: false,
  previewUrl: '', isVideo: false, durationSec: null,
  facebook: { enabled: false, accountIds: [] },
  tiktok: { enabled: false, accountIds: [] },
  tiktokFields: defaultTiktokFields()
})
const uploadTestVisibleSteps = computed(() => (uploadTest.tiktok.enabled ? [1, 2, 3] : [1, 3]))
const uploadTestStepIndex = computed(() => Math.max(1, uploadTestVisibleSteps.value.indexOf(uploadTestStep.value) + 1))
const uploadTestStepCount = computed(() => uploadTestVisibleSteps.value.length)
const uploadTestStepTitle = computed(() => ({ 1: 'File & Nền tảng', 2: 'Cài đặt TikTok', 3: 'Xác nhận' }[uploadTestStep.value] || ''))

function uploadTestNext() {
  if (uploadTestStep.value === 1) {
    if (!uploadTest.file) { toast.error('Chọn 1 file video/ảnh trước'); return }
    if (!uploadTest.facebook.enabled && !uploadTest.tiktok.enabled) { toast.error('Chọn ít nhất 1 nền tảng'); return }
    uploadTestStep.value = uploadTest.tiktok.enabled ? 2 : 3
  } else if (uploadTestStep.value === 2) {
    if (!uploadTest.tiktokFields.privacyLevel) { toast.error('TikTok: chưa chọn chế độ hiển thị (privacy)'); return }
    uploadTestStep.value = 3
  }
}
function uploadTestBack() {
  if (uploadTestStep.value === 3) uploadTestStep.value = uploadTest.tiktok.enabled ? 2 : 1
  else uploadTestStep.value = 1
}
function onUploadTestFile(ev) {
  const file = ev.target.files?.[0]
  if (!file) return
  if (uploadTest.previewUrl) URL.revokeObjectURL(uploadTest.previewUrl)
  uploadTest.file = file
  uploadTest.fileName = file.name
  uploadTest.isVideo = file.type.startsWith('video/')
  uploadTest.durationSec = null
  uploadTest.previewUrl = URL.createObjectURL(file) // chỉ để preview trong UI — không gửi lên server
}
function toggleUploadTestAccount(platform, id) {
  const list = uploadTest[platform].accountIds
  const i = list.indexOf(id)
  if (i >= 0) list.splice(i, 1); else list.push(id)
}
async function submitUploadTest() {
  uploadTest.submitting = true
  try {
    await posts.uploadTestPost(uploadTest.file, {
      caption: uploadTest.caption, facebook: uploadTest.facebook,
      tiktok: { ...uploadTest.tiktok, ...uploadTest.tiktokFields }
    })
    toast.success('Đã xếp hàng đăng thử (đăng trong giây lát) — xem ở Lịch sử')
    if (uploadTest.previewUrl) URL.revokeObjectURL(uploadTest.previewUrl)
    uploadTest.file = null
    uploadTest.fileName = ''
    uploadTest.caption = ''
    uploadTest.previewUrl = ''
    uploadTest.isVideo = false
    uploadTest.durationSec = null
    uploadTest.facebook = { enabled: false, accountIds: [] }
    uploadTest.tiktok = { enabled: false, accountIds: [] }
    uploadTest.tiktokFields = defaultTiktokFields()
    uploadTestOpen.value = false
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Đăng thử thất bại')
  } finally {
    uploadTest.submitting = false
  }
}

// ── Kế hoạch: plan + slot — dashboard gộp không hiện khái niệm "Plan" riêng, tự tạo 1 plan mặc định
// đứng sau hậu trường. Phân trang client-side (allSlots đã load hết từ plans.load()). ─────────────────
const allSlots = computed(() =>
  plans.items.value
    .flatMap((p) => (p.slots || []).map((s) => ({ ...s, plan_id: p.id })))
    .sort((a, b) => (a.time_of_day || '').localeCompare(b.time_of_day || ''))
)
const SLOTS_PAGE_SIZE = 6
const slotsPage = ref(1)
const slotsTotalPages = computed(() => Math.max(1, Math.ceil(allSlots.value.length / SLOTS_PAGE_SIZE)))
const pagedSlots = computed(() => {
  if (slotsPage.value > slotsTotalPages.value) slotsPage.value = slotsTotalPages.value
  const start = (slotsPage.value - 1) * SLOTS_PAGE_SIZE
  return allSlots.value.slice(start, start + SLOTS_PAGE_SIZE)
})

async function ensureDefaultPlanId() {
  if (plans.items.value.length) return plans.items.value[0].id
  const item = await plans.createPlan('Kế hoạch')
  return item?.id
}
async function openQuickSlotEditor() {
  const planId = await ensureDefaultPlanId()
  if (!planId) { toast.error('Không tạo được kế hoạch'); return }
  openSlotEditor(planId)
}
async function doDeleteSlot(id) {
  if (!confirm('Xoá slot này?')) return
  await plans.deleteSlot(id)
}
function workflowName(id) {
  return workflows.items.value.find((w) => w.id === id)?.name || '(workflow đã xoá)'
}
const workflowOptions = computed(() => workflows.items.value.map((w) => ({ value: w.id, label: w.name })))
const WEEKDAY_LABELS = [
  { value: 0, label: 'CN' }, { value: 1, label: 'T2' }, { value: 2, label: 'T3' }, { value: 3, label: 'T4' },
  { value: 4, label: 'T5' }, { value: 5, label: 'T6' }, { value: 6, label: 'T7' }
]
function weekdaysShort(arr) {
  if (!arr || arr.length === 7) return 'Mỗi ngày'
  return WEEKDAY_LABELS.filter((d) => arr.includes(d.value)).map((d) => d.label).join(',')
}

// Introspect input nodes (source=session) của workflow để render form nhập trước — cùng convention với
// engine handleInput (session.field) trong wf-worker/handlers.js.
const workflowDefsCache = reactive({})
async function loadWorkflowDef(id) {
  if (workflowDefsCache[id]) return workflowDefsCache[id]
  const wf = await workflows.get(id)
  const def = wf?.definition || { nodes: [] }
  workflowDefsCache[id] = def
  return def
}
function sessionInputFieldsOf(definition) {
  return (definition?.nodes || [])
    .filter((n) => {
      const t = n.type
      const isInput = t === 'input' || t === 'inputText' || t === 'inputImage' || t === 'inputFile' || t === 'inputHistory'
      if (!isInput) return false
      const source = n.data?.config?.source || 'session'
      return source === 'session'
    })
    .map((n) => ({
      nodeId: n.id,
      field: n.data?.config?.field || (n.data?.config?.contentType || 'text'),
      contentType: n.data?.config?.contentType || ({ inputText: 'text', inputImage: 'image', inputFile: 'file', inputHistory: 'history' }[n.type] || 'text'),
      label: n.data?.config?.label || ''
    }))
}

function defaultSlotEditor() {
  return {
    open: false, planId: null, slotId: null, workflowId: '', label: '', time: '08:00',
    weekdays: [0, 1, 2, 3, 4, 5, 6], caption: '', input: {}, fields: [],
    facebook: { enabled: false, accountIds: [] }, tiktok: { enabled: false, accountIds: [] },
    tiktokFields: defaultTiktokFields()
  }
}
const slotEditor = reactive(defaultSlotEditor())

// SocialDropdown chỉ emit update:modelValue (không có @change như <select> gốc) → dùng watch để load input fields.
watch(() => slotEditor.workflowId, () => { if (slotEditor.open) onSlotWorkflowChange() })

async function openSlotEditor(planId, slot = null) {
  Object.assign(slotEditor, defaultSlotEditor())
  slotEditor.open = true
  slotEditor.planId = planId
  if (slot) {
    slotEditor.slotId = slot.id
    slotEditor.workflowId = slot.workflow_id || ''
    slotEditor.label = slot.label || ''
    slotEditor.time = (slot.time_of_day || '08:00').slice(0, 5)
    slotEditor.weekdays = slot.weekdays?.length ? [...slot.weekdays] : [0, 1, 2, 3, 4, 5, 6]
    slotEditor.caption = slot.caption || ''
    slotEditor.input = slot.input ? { ...slot.input } : {}
    slotEditor.facebook = slot.facebook ? { enabled: !!slot.facebook.enabled, accountIds: [...(slot.facebook.accountIds || [])] } : { enabled: false, accountIds: [] }
    slotEditor.tiktok = slot.tiktok ? { enabled: !!slot.tiktok.enabled, accountIds: [...(slot.tiktok.accountIds || [])] } : { enabled: false, accountIds: [] }
    slotEditor.tiktokFields = slot.tiktok ? {
      privacyLevel: slot.tiktok.privacyLevel || '',
      disableDuet: slot.tiktok.disableDuet !== false, disableComment: slot.tiktok.disableComment !== false, disableStitch: slot.tiktok.disableStitch !== false,
      brandContentToggle: !!slot.tiktok.brandContentToggle, brandOrganicToggle: !!slot.tiktok.brandOrganicToggle
    } : defaultTiktokFields()
  }
  if (slotEditor.workflowId) await onSlotWorkflowChange()
}
async function onSlotWorkflowChange() {
  if (!slotEditor.workflowId) { slotEditor.fields = []; return }
  const def = await loadWorkflowDef(slotEditor.workflowId)
  slotEditor.fields = sessionInputFieldsOf(def)
}
function toggleSlotWeekday(d) {
  const i = slotEditor.weekdays.indexOf(d)
  if (i >= 0) slotEditor.weekdays.splice(i, 1); else slotEditor.weekdays.push(d)
}
function toggleSlotAccount(platform, id) {
  const list = slotEditor[platform].accountIds
  const i = list.indexOf(id)
  if (i >= 0) list.splice(i, 1); else list.push(id)
}
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',').pop())
    r.onerror = reject
    r.readAsDataURL(file)
  })
}
async function onSlotFileInput(field, ev) {
  const file = ev.target.files?.[0]
  if (!file) return
  const data = await fileToBase64(file)
  slotEditor.input[field] = { name: file.name, mimeType: file.type, data }
}
async function saveSlot() {
  if (!slotEditor.workflowId) { toast.error('Chọn workflow'); return }
  if (!slotEditor.time) { toast.error('Chọn giờ chạy'); return }
  if (slotEditor.tiktok.enabled && !slotEditor.tiktokFields.privacyLevel) { toast.error('TikTok: chưa chọn chế độ hiển thị (privacy)'); return }
  const payload = {
    label: slotEditor.label, workflow_id: slotEditor.workflowId, input: slotEditor.input,
    time_of_day: slotEditor.time, weekdays: slotEditor.weekdays, caption: slotEditor.caption,
    facebook: slotEditor.facebook, tiktok: { ...slotEditor.tiktok, ...slotEditor.tiktokFields }, is_active: true
  }
  try {
    if (slotEditor.slotId) await plans.updateSlot(slotEditor.slotId, payload)
    else await plans.addSlot(slotEditor.planId, payload)
    toast.success('Đã lưu lịch đăng')
    slotEditor.open = false
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Lưu thất bại')
  }
}

// ── Lịch sử (status labels) ─────────────────────────────────────────────────────────────────────────
function statusLabel(s) {
  return { scheduled: 'Chờ đăng', posting: 'Đang đăng', processing: 'Đang xử lý', posted: 'Đã đăng', error: 'Lỗi', cancelled: 'Đã huỷ' }[s] || s
}
function statusDarkClass(s) {
  return {
    scheduled: 'bg-blue-50 text-blue-700',
    posting: 'bg-amber-50 text-amber-700',
    processing: 'bg-amber-50 text-amber-700',
    posted: 'bg-emerald-50 text-emerald-700',
    error: 'bg-rose-50 text-rose-600',
    cancelled: 'bg-gray-100 text-gray-400'
  }[s] || 'bg-gray-100 text-gray-400'
}
</script>

<style scoped>
/* ALD 06/07/2026 - Liquid Glass light: toàn bộ control dùng lq-* global (main.css), không còn dark utility. */
</style>
