export function useApi() {
  const config = useRuntimeConfig()

  return $fetch.create({
    baseURL: config.public.apiBase,
    onRequest({ options }) {
      options.headers = new Headers(options.headers)
      options.headers.set('Accept', 'application/json')
    }
  })
}
