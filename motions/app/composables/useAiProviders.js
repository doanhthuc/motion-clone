// #region ALD 22/05/2026 - Composable quản lý AI provider keys (OpenAI/Anthropic/Google/Ollama)
// Endpoints: /ai-providers (CRUD) + /test/:provider (validate key)
//
// Surface:
//   - items: ref([]) — list provider rows (api_key bị mask "abc…1234")
//   - load(), save(provider, payload), remove(provider), test(provider)
//
// Caller pattern: const ai = useAiProviders(); onMounted(() => ai.load())
export function useAiProviders() {
  const auth = useAuth()
  const items = useState('aiProviders.items', () => [])
  const loading = useState('aiProviders.loading', () => false)

  async function load() {
    loading.value = true
    try {
      const res = await auth.beFetch('/ai-providers')
      items.value = res?.items ?? []
    } finally {
      loading.value = false
    }
  }

  /**
   * Upsert provider config.
   * @param {string} provider - openai|anthropic|google|ollama
   * @param {object} payload - { api_key, base_url?, default_model?, is_active? }
   */
  async function save(provider, payload) {
    const res = await auth.beFetch(`/ai-providers/${provider}`, {
      method: 'PUT',
      body: payload,
      headers: { 'Content-Type': 'application/json' }
    })
    await load()
    return res
  }

  async function remove(provider) {
    await auth.beFetch(`/ai-providers/${provider}`, { method: 'DELETE' })
    await load()
  }

  /**
   * Gọi /test/:provider — BE thử request thật với 1 message rất nhỏ.
   * Trả { ok, model, reply } hoặc { ok: false, error }.
   */
  async function test(provider) {
    return await auth.beFetch(`/ai-providers/test/${provider}`)
  }

  /**
   * Liệt kê model của provider (cho picker). ollama → /api/tags; custom → /models.
   * Trả mảng tên model (string[]); rỗng nếu lỗi/không cấu hình.
   */
  async function listModels(provider = 'ollama') {
    const res = await auth.beFetch(`/ai-providers/${provider}/models`)
    return res?.models ?? []
  }

  return { items, loading, load, save, remove, test, listModels }
}
// #endregion
