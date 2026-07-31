<template>
  <!-- #region ALD 05/07/2026 - Help drawer Social Management: hướng dẫn step-by-step connect Facebook/TikTok +
       cách dùng. Drawer bên phải, rộng 2/3 (w-full mobile, sm:w-2/3 desktop). Dark theme — đồng bộ với dashboard
       Social Management redesign (Geist dark tokens + accent amber). -->
  <Transition enter-active-class="transition-opacity duration-200" leave-active-class="transition-opacity duration-200" enter-from-class="opacity-0" leave-to-class="opacity-0">
    <div v-if="modelValue" class="fixed inset-0 z-[1000] bg-black/60 backdrop-blur-sm" @click.self="$emit('update:modelValue', false)" />
  </Transition>
  <Transition enter-active-class="transition-transform duration-220 ease-out" leave-active-class="transition-transform duration-180 ease-in" enter-from-class="translate-x-full" leave-to-class="translate-x-full">
    <aside v-if="modelValue" class="fixed inset-y-0 right-0 z-[1001] w-full sm:w-2/3 flex flex-col bg-gray-50 border-l border-white/[0.07] shadow-2xl text-gray-800">
      <header class="flex-shrink-0 flex items-center justify-between gap-2 px-5 sm:px-7 h-16 border-b border-white/[0.07]">
        <div class="flex items-center gap-2 min-w-0">
          <span class="inline-flex h-9 w-9 items-center justify-center rounded-2xl bg-primary-50 border border-[rgba(94,106,210,0.14)] text-primary flex-shrink-0">
            <i class="bi bi-question-circle text-lg" />
          </span>
          <div class="min-w-0">
            <p class="font-semibold text-gray-900 truncate">Hướng dẫn kết nối &amp; sử dụng</p>
            <p class="text-xs text-gray-500 truncate">Thiết lập từng bước — không cần sửa file server</p>
          </div>
        </div>
        <button type="button" class="h-9 w-9 flex items-center justify-center rounded-xl text-gray-500 hover:bg-white/[0.05] hover:text-gray-900" @click="$emit('update:modelValue', false)">
          <i class="bi bi-x-lg" />
        </button>
      </header>

      <div class="flex-1 min-h-0 overflow-y-auto px-5 sm:px-7 py-5 space-y-6">
        <div v-if="!appConfig.config.value" class="text-sm text-gray-400">Đang tải…</div>

        <template v-else>
          <!-- ══════ PUBLIC BASE URL (chung cho cả Facebook + TikTok) ══════ -->
          <section>
            <h3 class="flex items-center gap-2 font-semibold text-gray-900 text-sm mb-2">
              <i class="bi bi-globe2 text-primary text-lg" /> Public Base URL
            </h3>
            <p class="text-sm text-gray-600 mb-3">
              Domain HTTPS công khai của server (vd: <code class="text-[11px] bg-gray-100 rounded px-1">https://motion-server.datools.info</code>) —
              Facebook/TikTok cần cái này để biết redirect về đâu sau khi bạn cấp quyền. Nhập 1 lần, dùng chung cho cả 2 nền tảng bên dưới.
            </p>
            <p v-if="!appConfig.config.value.publicBaseUrl" class="flex items-start gap-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2.5 mb-3">
              <i class="bi bi-exclamation-triangle-fill mt-0.5" />
              Chưa nhập Public Base URL — Facebook/TikTok chưa có nơi redirect về. Nhập ở form dưới rồi bấm Lưu.
            </p>
            <div class="p-3.5 rounded-2xl bg-white/[0.02] border border-white/[0.07] space-y-2.5">
              <div>
                <label class="text-xs font-semibold text-gray-500 uppercase tracking-wide">Public Base URL</label>
                <input v-model="baseForm.publicBaseUrl" :disabled="!isAdmin" placeholder="https://motion-server.datools.info" class="lq-input mt-1 font-mono disabled:opacity-50" >
              </div>
              <button v-if="isAdmin" type="button" class="lq-btn lq-btn--primary" :disabled="savingBase" @click="saveBaseUrl">
                <i :class="['bi', savingBase ? 'bi-arrow-repeat animate-spin' : 'bi-check2']" /> Lưu Public Base URL
              </button>
              <p v-else class="text-[11px] text-gray-400"><i class="bi bi-lock me-1" />Chỉ admin sửa được (dùng chung cả team).</p>
            </div>
          </section>

          <!-- ══════ FACEBOOK ══════ -->
          <section class="pt-5 border-t border-white/[0.07]">
            <h3 class="flex items-center gap-2 font-semibold text-gray-900 text-sm mb-3">
              <i class="bi bi-facebook text-blue-400 text-lg" /> Kết nối Facebook Page
            </h3>
            <ol class="space-y-3">
              <li class="flex gap-3">
                <span class="step-num">1</span>
                <div class="text-sm text-gray-700">
                  Vào <a href="https://developers.facebook.com/apps" target="_blank" rel="noopener" class="text-primary font-medium hover:underline">developers.facebook.com/apps <i class="bi bi-box-arrow-up-right text-[10px]" /></a>
                  → Tạo App, chọn loại <b class="text-gray-900">Business</b>.
                </div>
              </li>
              <li class="flex gap-3">
                <span class="step-num">2</span>
                <div class="text-sm text-gray-700">Trong App vừa tạo, thêm sản phẩm <b class="text-gray-900">Facebook Login for Business</b>.</div>
              </li>
              <li class="flex gap-3">
                <span class="step-num">3</span>
                <div class="text-sm text-gray-700 flex-1 min-w-0">
                  Vào <b class="text-gray-900">App Settings → Basic</b>, copy <b class="text-gray-900">App ID</b> và <b class="text-gray-900">App Secret</b> dán vào form bên dưới.
                </div>
              </li>
              <li class="flex gap-3">
                <span class="step-num">4</span>
                <div class="text-sm text-gray-700 flex-1 min-w-0">
                  Vào <b class="text-gray-900">Facebook Login → Settings → Valid OAuth Redirect URIs</b>, dán ĐÚNG URL này:
                  <SocialCopyField :value="appConfig.config.value.facebook.redirectUri || '(nhập Public Base URL ở trên trước)'" />
                </div>
              </li>
              <li class="flex gap-3">
                <span class="step-num">5</span>
                <div class="text-sm text-gray-700">
                  Xin các quyền: <code class="text-[11px] bg-gray-100 rounded px-1">pages_show_list</code>,
                  <code class="text-[11px] bg-gray-100 rounded px-1">pages_read_engagement</code>,
                  <code class="text-[11px] bg-gray-100 rounded px-1">pages_manage_posts</code>,
                  <code class="text-[11px] bg-gray-100 rounded px-1">business_management</code>.
                  Ở chế độ Development, chỉ tài khoản Admin/Developer/Tester của App mới đăng nhập được — muốn mở cho cả team cần App Review (Business Verification) của Meta.
                </div>
              </li>
            </ol>

            <div class="mt-4 p-3.5 rounded-2xl bg-white/[0.02] border border-white/[0.07] space-y-2.5">
              <div>
                <label class="text-xs font-semibold text-gray-500 uppercase tracking-wide">App ID</label>
                <input v-model="fbForm.appId" :disabled="!isAdmin" placeholder="vd: 1234567890123456" class="lq-input mt-1 font-mono disabled:opacity-50" >
              </div>
              <div>
                <label class="text-xs font-semibold text-gray-500 uppercase tracking-wide">
                  App Secret
                  <span v-if="appConfig.config.value.facebook.hasSecret" class="text-emerald-600 normal-case font-semibold ml-1"><i class="bi bi-check-circle" /> đã lưu</span>
                </label>
                <input v-model="fbForm.appSecret" :disabled="!isAdmin" type="password" autocomplete="off" :placeholder="appConfig.config.value.facebook.hasSecret ? 'Để trống = giữ secret cũ' : 'App secret…'" class="lq-input mt-1 font-mono disabled:opacity-50" >
              </div>
              <button v-if="isAdmin" type="button" class="lq-btn lq-btn--primary" :disabled="savingFb" @click="saveFacebook">
                <i :class="['bi', savingFb ? 'bi-arrow-repeat animate-spin' : 'bi-check2']" /> Lưu cấu hình Facebook
              </button>
              <p v-else class="text-[11px] text-gray-400"><i class="bi bi-lock me-1" />Chỉ admin sửa được (dùng chung cả team).</p>
            </div>

            <div class="mt-3 flex justify-end">
              <button type="button" class="lq-btn" @click="$emit('connect', 'facebook')">
                <i class="bi bi-plug" /> Kết nối Page ngay
              </button>
            </div>
          </section>

          <!-- ══════ TIKTOK ══════ -->
          <section class="pt-5 border-t border-white/[0.07]">
            <h3 class="flex items-center gap-2 font-semibold text-gray-900 text-sm mb-3">
              <i class="bi bi-tiktok text-lg" /> Kết nối TikTok
            </h3>
            <ol class="space-y-3">
              <li class="flex gap-3">
                <span class="step-num">1</span>
                <div class="text-sm text-gray-700">
                  Vào <a href="https://developers.tiktok.com/apps" target="_blank" rel="noopener" class="text-primary font-medium hover:underline">developers.tiktok.com/apps <i class="bi bi-box-arrow-up-right text-[10px]" /></a> → Tạo app mới.
                </div>
              </li>
              <li class="flex gap-3">
                <span class="step-num">2</span>
                <div class="text-sm text-gray-700">Thêm sản phẩm <b class="text-gray-900">Content Posting API</b> (kèm Login Kit).</div>
              </li>
              <li class="flex gap-3">
                <span class="step-num">3</span>
                <div class="text-sm text-gray-700 flex-1 min-w-0">Copy <b class="text-gray-900">Client Key</b> và <b class="text-gray-900">Client Secret</b> dán vào form bên dưới.</div>
              </li>
              <li class="flex gap-3">
                <span class="step-num">4</span>
                <div class="text-sm text-gray-700 flex-1 min-w-0">
                  Khai Redirect URI đúng URL này:
                  <SocialCopyField :value="appConfig.config.value.tiktok.redirectUri || '(nhập Public Base URL ở trên trước)'" />
                </div>
              </li>
              <li class="flex gap-3">
                <span class="step-num">5</span>
                <div class="text-sm text-gray-700">
                  App <b class="text-gray-900">chưa được TikTok audit</b> → chỉ đăng được ở chế độ riêng tư (<code class="text-[11px] bg-gray-100 rounded px-1">SELF_ONLY</code>, chỉ người đăng xem được trong app TikTok) — <b class="text-gray-900">tài khoản đăng phải bật Riêng tư</b> trong cài đặt TikTok.
                  Muốn đăng công khai, nộp app xin TikTok duyệt Content Posting API.
                </div>
              </li>
            </ol>

            <div class="mt-4 p-3.5 rounded-2xl bg-white/[0.02] border border-white/[0.07] space-y-2.5">
              <div>
                <label class="text-xs font-semibold text-gray-500 uppercase tracking-wide">Client Key</label>
                <input v-model="ttForm.clientKey" :disabled="!isAdmin" placeholder="vd: aw1a2b3c4d5e…" class="lq-input mt-1 font-mono disabled:opacity-50" >
              </div>
              <div>
                <label class="text-xs font-semibold text-gray-500 uppercase tracking-wide">
                  Client Secret
                  <span v-if="appConfig.config.value.tiktok.hasSecret" class="text-emerald-600 normal-case font-semibold ml-1"><i class="bi bi-check-circle" /> đã lưu</span>
                </label>
                <input v-model="ttForm.clientSecret" :disabled="!isAdmin" type="password" autocomplete="off" :placeholder="appConfig.config.value.tiktok.hasSecret ? 'Để trống = giữ secret cũ' : 'Client secret…'" class="lq-input mt-1 font-mono disabled:opacity-50" >
              </div>
              <button v-if="isAdmin" type="button" class="lq-btn lq-btn--primary" :disabled="savingTt" @click="saveTiktok">
                <i :class="['bi', savingTt ? 'bi-arrow-repeat animate-spin' : 'bi-check2']" /> Lưu cấu hình TikTok
              </button>
              <p v-else class="text-[11px] text-gray-400"><i class="bi bi-lock me-1" />Chỉ admin sửa được (dùng chung cả team).</p>
            </div>

            <div class="mt-3 flex justify-end">
              <button type="button" class="lq-btn" @click="$emit('connect', 'tiktok')">
                <i class="bi bi-plug" /> Kết nối tài khoản ngay
              </button>
            </div>
          </section>

          <!-- ══════ CÁCH SỬ DỤNG ══════ -->
          <section class="pt-5 border-t border-white/[0.07]">
            <h3 class="flex items-center gap-2 font-semibold text-gray-900 text-sm mb-3">
              <i class="bi bi-stars text-lg text-primary" /> Cách dùng Social Management
            </h3>
            <div class="space-y-3 text-sm text-gray-700">
              <div class="flex gap-3">
                <span class="usage-icon"><i class="bi bi-person-badge" /></span>
                <p><b class="text-gray-900">Tài khoản</b> — panel bên phải: kết nối Page Facebook / tài khoản TikTok. Có thể kết nối nhiều Page/tài khoản, chọn dùng cái nào khi tạo bài đăng.</p>
              </div>
              <div class="flex gap-3">
                <span class="usage-icon"><i class="bi bi-stars" /></span>
                <p><b class="text-gray-900">Tạo bài đăng</b> — chọn nguồn "Từ workflow" (output đã chạy xong, chưa có bài đăng nào) hoặc "Upload file" (test nhanh không cần chạy workflow), viết caption, chọn nền tảng/tài khoản, đăng ngay hoặc hẹn giờ.</p>
              </div>
              <div class="flex gap-3">
                <span class="usage-icon"><i class="bi bi-calendar-check" /></span>
                <p><b class="text-gray-900">Kế hoạch</b> — đặt lịch N bài/ngày. Thêm từng "slot" (1 slot = 1 bài): chọn workflow, giờ chạy hằng ngày, ngày trong tuần, soạn sẵn input (prompt/kịch bản hoặc file — tuỳ workflow), caption, tài khoản đăng. Đúng giờ, hệ thống tự chạy workflow rồi tự đăng kết quả.</p>
              </div>
              <div class="flex gap-3">
                <span class="usage-icon"><i class="bi bi-clock-history" /></span>
                <p><b class="text-gray-900">Hoạt động gần đây</b> — theo dõi trạng thái từng bài (chờ đăng/đang đăng/TikTok đang xử lý/đã đăng/lỗi). Bài "chờ đăng" chưa publish có thể huỷ.</p>
              </div>
            </div>
          </section>
        </template>
      </div>
    </aside>
  </Transition>
  <!-- #endregion -->
</template>

<script setup>
const props = defineProps({ modelValue: { type: Boolean, default: false } })
defineEmits(['update:modelValue', 'connect'])

const auth = useAuth()
const toast = useToast()
const appConfig = useSocialAppConfig()
const isAdmin = computed(() => decodeJwtPayload(auth.token.value)?.role === 'admin')

const baseForm = reactive({ publicBaseUrl: '' })
const fbForm = reactive({ appId: '', appSecret: '' })
const ttForm = reactive({ clientKey: '', clientSecret: '' })
const savingBase = ref(false)
const savingFb = ref(false)
const savingTt = ref(false)

watch(() => props.modelValue, async (open) => {
  if (!open) return
  await appConfig.load()
  baseForm.publicBaseUrl = appConfig.config.value?.publicBaseUrl || ''
  fbForm.appId = appConfig.config.value?.facebook?.appId || ''
  fbForm.appSecret = ''
  ttForm.clientKey = appConfig.config.value?.tiktok?.clientKey || ''
  ttForm.clientSecret = ''
}, { immediate: true })

async function saveBaseUrl() {
  savingBase.value = true
  try {
    await appConfig.save({ publicBaseUrl: baseForm.publicBaseUrl })
    toast.success('Đã lưu Public Base URL')
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Lưu thất bại')
  } finally {
    savingBase.value = false
  }
}
async function saveFacebook() {
  savingFb.value = true
  try {
    await appConfig.save({ facebook: { appId: fbForm.appId, appSecret: fbForm.appSecret } })
    fbForm.appSecret = ''
    toast.success('Đã lưu cấu hình Facebook')
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Lưu thất bại')
  } finally {
    savingFb.value = false
  }
}
async function saveTiktok() {
  savingTt.value = true
  try {
    await appConfig.save({ tiktok: { clientKey: ttForm.clientKey, clientSecret: ttForm.clientSecret } })
    ttForm.clientSecret = ''
    toast.success('Đã lưu cấu hình TikTok')
  } catch (e) {
    toast.error(e?.data?.error || e?.message || 'Lưu thất bại')
  } finally {
    savingTt.value = false
  }
}
</script>

<style scoped>
/* ALD 06/07/2026 - Liquid Glass light: button/input dùng lq-* global (main.css) */
.step-num {
  flex-shrink: 0; width: 22px; height: 22px; border-radius: 50%;
  background: rgba(94, 106, 210, 0.1); color: var(--primary);
  font-size: 11px; font-weight: 700; display: flex; align-items: center; justify-content: center;
}
.usage-icon {
  flex-shrink: 0; width: 30px; height: 30px; border-radius: 10px;
  background: rgba(255,255,255,0.07); color: var(--ink-2);
  display: flex; align-items: center; justify-content: center;
}
</style>
