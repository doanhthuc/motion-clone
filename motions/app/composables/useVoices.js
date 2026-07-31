// #region ALD 13/06/2026 - Composable thư viện "Giọng nói" (voice library). List giọng đã upload để
// picker node Nói / Teaser / Story-film thêm vào lựa chọn (value 'voicelib:<id>' — worker route qua _tts).
// GET /voices mở cho MỌI user authed (BE gate sessionAuth), nên picker của user thường vẫn thấy giọng.
//   const voices = useVoices()
//   await voices.load()        → voices.items populated
//   voices.options.value       → [{ value:'voicelib:<id>', label, gender, audioUrl, ... }]
export function useVoices() {
  const auth = useAuth()
  const items = useState('voices.items', () => [])
  const loading = useState('voices.loading', () => false)
  const loaded = useState('voices.loaded', () => false)

  async function load(force = false) {
    if (loading.value) return items.value
    if (loaded.value && !force) return items.value
    loading.value = true
    try {
      const res = await auth.beFetch('/voices')
      items.value = res?.items ?? []
      loaded.value = true
    } catch {
      // im lặng — picker chỉ mất mục "Thư viện giọng", giọng built-in vẫn dùng được
      items.value = []
    } finally {
      loading.value = false
    }
    return items.value
  }

  // Options cho UiDropdown / tile picker. value = 'voicelib:<id>' (worker _tts nhận dạng tiền tố).
  const options = computed(() => items.value.map((v) => ({
    value: `voicelib:${v.id}`,
    label: v.name + (v.gender === 'female' ? ' (nữ)' : v.gender === 'male' ? ' (nam)' : ''),
    id: v.id,
    gender: v.gender,
    audioUrl: v.audioUrl
  })))

  return { items, loading, loaded, load, options }
}
// #endregion
