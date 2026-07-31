<template>
  <!-- #region ALD 20/05/2026 - Shell layout fixed inset-0 (iOS Safari friendly), Apple feel -->
  <div class="fixed inset-0 flex">
    <!-- Brand panel: hidden mobile, glass-y -->
    <div class="hidden lg:flex w-1/2 relative p-12 flex-col justify-between overflow-hidden">
      <div class="absolute inset-0 bg-gradient-to-br from-primary via-blue-600 to-primary-dark" />
      <div class="absolute -top-1/4 -right-1/4 w-2/3 aspect-square rounded-full bg-white/10 blur-3xl" />
      <div class="absolute -bottom-1/4 -left-1/4 w-2/3 aspect-square rounded-full bg-indigo-400/30 blur-3xl" />
      <div class="relative flex items-center gap-3 text-white">
        <span class="inline-flex h-11 w-11 items-center justify-center rounded-2xl bg-white/15 backdrop-blur font-bold">
          <i class="bi bi-stars text-lg" />
        </span>
        <span class="text-lg font-bold tracking-tight">{{ appConfig.app.name }}</span>
      </div>
      <div class="relative text-white">
        <h1 class="text-5xl font-black leading-tight tracking-tight">
          Trợ lý AI<br />nội bộ 
        </h1>
        <p class="text-blue-50 mt-5 max-w-md text-lg">
          Đăng nhập bằng email công ty, hệ thống sẽ gửi mã OTP 6 chữ số.
        </p>
      </div>
      <div class="relative text-xs text-blue-200">
        © {{ new Date().getFullYear() }} Pebsteel Building Systems
      </div>
    </div>
    <!-- #endregion -->

    <!-- #region ALD 20/05/2026 - Form panel responsive -->
    <div class="flex flex-1 items-center justify-center p-4 sm:p-6 overflow-y-auto">
      <div class="w-full max-w-sm">
        <div class="mb-6 sm:mb-8 text-center lg:text-left">
          <div class="inline-flex lg:hidden h-12 w-12 items-center justify-center rounded-xl bg-primary text-white font-bold mb-4">
            P
          </div>
          <h2 class="text-2xl font-bold text-gray-900">
            {{ step === 'email' ? 'Đăng nhập' : 'Nhập mã xác thực' }}
          </h2>
          <p class="text-sm text-gray-500 mt-1">
            {{ step === 'email' ? 'Sử dụng email' : `Mã được gửi tới ${email}` }}
          </p>
        </div>

        <!-- Step 1: email -->
        <form v-if="step === 'email'" class="space-y-4" @submit.prevent="onRequestOtp">
          <label class="block">
            <span class="text-xs font-semibold text-gray-700">Email</span>
            <UiInput
              v-model="email"
              type="email"
              icon="bi-envelope"
              placeholder="name@pebsteel.com"
              class="mt-1.5"
              autocomplete="email"
            />
          </label>

          <p
            v-if="errorMsg"
            class="text-xs font-semibold text-rose-700 bg-rose-50 border border-rose-100 px-3 py-2 rounded-lg"
          >
            <i class="bi bi-exclamation-triangle me-1" />
            {{ errorMsg }}
          </p>

          <UiButton
            type="submit"
            variant="primary"
            size="lg"
            :loading="loading"
            class="w-full"
            :disabled="!email.trim()"
          >
            {{ loading ? 'Đang gửi…' : 'Gửi mã xác thực' }}
            <i v-if="!loading" class="bi bi-arrow-right" />
          </UiButton>
        </form>

        <!-- Step 2: OTP -->
        <div v-else class="space-y-4">
          <UiOtpInput
            v-model="code"
            :disabled="loading"
            @complete="onVerifyOtp"
          />

          <p
            v-if="errorMsg"
            class="text-xs font-semibold text-rose-700 bg-rose-50 border border-rose-100 px-3 py-2 rounded-lg text-center"
          >
            <i class="bi bi-exclamation-triangle me-1" />
            {{ errorMsg }}
          </p>

          <UiButton
            variant="primary"
            size="lg"
            :loading="loading"
            class="w-full"
            :disabled="code.length !== 6"
            @click="onVerifyOtp"
          >
            <i v-if="!loading" class="bi bi-shield-check" />
            {{ loading ? 'Đang xác thực…' : 'Đăng nhập' }}
          </UiButton>

          <div class="flex items-center justify-between text-xs text-gray-500">
            <button
              type="button"
              class="hover:text-gray-700 transition-colors"
              @click="backToEmail"
            >
              <i class="bi bi-arrow-left" />
              Đổi email
            </button>
            <button
              type="button"
              class="hover:text-primary transition-colors disabled:opacity-50"
              :disabled="countdown > 0 || loading"
              @click="onResend"
            >
              {{ countdown > 0 ? `Gửi lại sau ${countdown}s` : 'Gửi lại mã' }}
            </button>
          </div>
        </div>

        <p class="text-xs text-gray-500 text-center mt-6">
          Có vấn đề? Liên hệ IT Helpdesk Pebsteel.
        </p>
      </div>
    </div>
    <!-- #endregion -->
  </div>
</template>

<script setup>
definePageMeta({ layout: false })

const appConfig = useAppConfig()
const route = useRoute()
const auth = useAuth()

const step = ref('email')
const email = ref('')
const code = ref('')
const errorMsg = ref('')
const loading = ref(false)
const countdown = ref(0)
let countdownTimer = null

useHead({ title: 'Đăng nhập — Local AI' })

// #region ALD 20/05/2026 - Countdown 60s cho nút Resend
function startCountdown(sec = 60) {
  clearInterval(countdownTimer)
  countdown.value = sec
  countdownTimer = setInterval(() => {
    if (countdown.value <= 1) {
      clearInterval(countdownTimer)
      countdown.value = 0
    } else {
      countdown.value -= 1
    }
  }, 1000)
}
onBeforeUnmount(() => clearInterval(countdownTimer))
// #endregion

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

// #region ALD 20/05/2026 - Step 1: gửi OTP. USER_NOT_FOUND → giữ ở step email với thông báo
async function onRequestOtp() {
  errorMsg.value = ''
  const e = email.value.trim().toLowerCase()
  if (!EMAIL_RE.test(e)) {
    errorMsg.value = 'Email không hợp lệ.'
    return
  }
  loading.value = true
  try {
    await auth.requestOtp(e)
    step.value = 'code'
    code.value = ''
    startCountdown()
  } catch (err) {
    const data = err?.data ?? err?.response?._data
    const errCode = data?.code
    if (errCode === 'USER_NOT_FOUND') {
      errorMsg.value = 'Email chưa được đăng ký. Liên hệ admin để cấp quyền.'
    } else if (errCode === 'ACCOUNT_INACTIVE') {
      errorMsg.value = 'Tài khoản đã bị khóa. Liên hệ quản trị viên.'
    } else if (errCode === 'RATE_LIMITED') {
      errorMsg.value = 'Quá nhiều yêu cầu. Vui lòng thử lại sau 1 giờ.'
    } else {
      errorMsg.value = data?.error || 'Không gửi được mã. Vui lòng thử lại.'
    }
  } finally {
    loading.value = false
  }
}
// #endregion

// #region ALD 20/05/2026 - Step 2: verify OTP, set token cookie, redirect callbackUrl
async function onVerifyOtp() {
  if (loading.value || code.value.length !== 6) return
  errorMsg.value = ''
  loading.value = true
  try {
    const res = await auth.verifyOtp(email.value.trim().toLowerCase(), code.value)
    if (res?.success && res?.token) {
      const callback = route.query.callbackUrl || '/'
      await navigateTo(callback, { replace: true })
    }
  } catch (err) {
    const data = err?.data ?? err?.response?._data
    const errCode = data?.code
    if (errCode === 'OTP_EXPIRED') {
      errorMsg.value = 'Mã đã hết hạn. Bấm "Gửi lại mã".'
    } else if (errCode === 'OTP_INVALID') {
      errorMsg.value = 'Mã không đúng. Vui lòng kiểm tra lại.'
    } else if (errCode === 'ACCOUNT_INACTIVE') {
      errorMsg.value = 'Tài khoản đã bị khóa.'
      step.value = 'email'
    } else {
      errorMsg.value = data?.error || 'Không xác thực được. Vui lòng thử lại.'
    }
    code.value = ''
  } finally {
    loading.value = false
  }
}
// #endregion

async function onResend() {
  if (countdown.value > 0 || loading.value) return
  errorMsg.value = ''
  loading.value = true
  try {
    await auth.requestOtp(email.value.trim().toLowerCase())
    code.value = ''
    startCountdown()
  } catch (err) {
    const data = err?.data ?? err?.response?._data
    errorMsg.value = data?.error || 'Không gửi lại được mã.'
  } finally {
    loading.value = false
  }
}

function backToEmail() {
  step.value = 'email'
  code.value = ''
  errorMsg.value = ''
  clearInterval(countdownTimer)
  countdown.value = 0
}
</script>
