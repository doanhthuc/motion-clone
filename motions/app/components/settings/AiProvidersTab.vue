<template>
  <!-- #region ALD 22/05/2026 - AI Providers tab content (embedded trong Settings) -->
  <div class="space-y-3">
    <div
      v-for="p in providers"
      :key="p.id"
      class="glass shadow-card rounded-3xl p-4 space-y-3"
    >
      <div class="flex items-center gap-3">
        <span :class="['inline-flex h-10 w-10 items-center justify-center rounded-2xl text-white shadow-pill', p.color]">
          <i :class="['bi text-lg', p.icon]" />
        </span>
        <div class="flex-1 min-w-0">
          <h3 class="text-sm font-bold text-gray-900">{{ p.label }}</h3>
          <p class="text-xs text-gray-500">{{ p.hint }}</p>
        </div>
        <span v-if="existing(p.id)" class="text-[11px] font-bold text-emerald-600 inline-flex items-center gap-1">
          <i class="bi bi-check-circle-fill" /> Đã cấu hình
        </span>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
        <div v-if="p.needsKey">
          <label class="text-[11px] font-bold text-gray-600 uppercase tracking-wide">API Key</label>
          <input
            v-model="forms[p.id].api_key"
            type="password"
            :placeholder="existing(p.id)?.api_key || 'sk-...'"
            class="mt-1 w-full px-3 py-2 rounded-2xl bg-gray-50 border border-gray-200 text-sm font-mono focus:outline-none focus:border-primary"
          />
        </div>
        <div>
          <label class="text-[11px] font-bold text-gray-600 uppercase tracking-wide">Base URL (optional)</label>
          <input
            v-model="forms[p.id].base_url"
            type="text"
            :placeholder="p.defaultBaseUrl"
            class="mt-1 w-full px-3 py-2 rounded-2xl bg-gray-50 border border-gray-200 text-sm font-mono focus:outline-none focus:border-primary"
          />
        </div>
        <div :class="p.needsKey ? '' : 'md:col-span-2'">
          <label class="text-[11px] font-bold text-gray-600 uppercase tracking-wide">Default Model</label>
          <input
            v-model="forms[p.id].default_model"
            type="text"
            :placeholder="p.defaultModel"
            class="mt-1 w-full px-3 py-2 rounded-2xl bg-gray-50 border border-gray-200 text-sm font-mono focus:outline-none focus:border-primary"
          />
        </div>
      </div>

      <div class="flex items-center justify-end gap-2 pt-1">
        <button
          v-if="existing(p.id)"
          type="button"
          class="press inline-flex items-center gap-1.5 h-9 px-3 rounded-full bg-rose-50 text-rose-700 text-xs font-semibold hover:bg-rose-100"
          @click="onDelete(p.id)"
        >
          <i class="bi bi-trash" />
          Xoá
        </button>
        <button
          v-if="existing(p.id)"
          type="button"
          :disabled="testing[p.id]"
          class="press inline-flex items-center gap-1.5 h-9 px-3 rounded-full bg-blue-50 text-blue-700 text-xs font-semibold hover:bg-blue-100 disabled:opacity-50"
          @click="onTest(p.id)"
        >
          <i :class="['bi', testing[p.id] ? 'bi-hourglass-split animate-pulse' : 'bi-lightning-charge']" />
          {{ testing[p.id] ? 'Đang test…' : 'Test' }}
        </button>
        <button
          type="button"
          class="press inline-flex items-center gap-1.5 h-9 px-4 rounded-full bg-primary text-white text-xs font-bold shadow-pill hover:bg-primary-dark"
          @click="onSave(p.id)"
        >
          <i class="bi bi-check2" />
          Lưu
        </button>
      </div>
    </div>
  </div>
  <!-- #endregion -->
</template>

<script setup>
const ai = useAiProviders()
const toast = useToast()
const confirmDialog = useConfirm()

const providers = [
  { id: 'ollama', label: 'Ollama (local)', hint: 'Self-hosted local LLM, không cần API key',
    icon: 'bi-hdd-network', color: 'bg-violet-600', needsKey: false,
    defaultBaseUrl: 'http://host.docker.internal:11434', defaultModel: 'qwen2.5:7b' },
  { id: 'custom', label: 'Custom API', hint: 'Bất kỳ OpenAI-compatible: OpenAI, vLLM, LocalAI, OpenRouter, …',
    icon: 'bi-cloud-fill', color: 'bg-blue-600', needsKey: true,
    defaultBaseUrl: 'https://api.openai.com/v1', defaultModel: 'gpt-4o-mini' }
]

const forms = reactive(Object.fromEntries(providers.map((p) => [p.id, { api_key: '', base_url: '', default_model: '' }])))
const testing = reactive({})

function existing(provider) {
  return ai.items.value.find((r) => r.provider === provider) || null
}

onMounted(async () => {
  await ai.load()
  for (const r of ai.items.value) {
    forms[r.provider] = {
      api_key: '',
      base_url: r.base_url || '',
      default_model: r.default_model || ''
    }
  }
})

async function onSave(provider) {
  const f = forms[provider]
  const payload = {
    api_key: f.api_key || null,
    base_url: f.base_url || null,
    default_model: f.default_model || null
  }
  try {
    await ai.save(provider, payload)
    toast.success(`Đã lưu ${provider}`)
  } catch (err) {
    toast.error(err.data?.error || err.message)
  }
}

async function onDelete(provider) {
  const ok = await confirmDialog.ask({
    title: `Xoá cấu hình ${provider}?`,
    message: 'Workflow đang dùng provider này sẽ lỗi khi chạy.',
    confirmText: 'Xoá',
    variant: 'danger'
  })
  if (!ok) return
  await ai.remove(provider)
  toast.success('Đã xoá')
}
async function onTest(provider) {
  testing[provider] = true
  try {
    const res = await ai.test(provider)
    if (res.ok) {
      toast.success(`${provider} OK — model ${res.model} trả: ${res.reply.slice(0, 50)}`)
    } else {
      toast.error(`${provider} fail: ${res.error}`)
    }
  } catch (err) {
    toast.error(err.data?.error || err.message)
  } finally {
    testing[provider] = false
  }
}
</script>
