export default defineEventHandler((event) => {
  const method = getMethod(event)
  const url = getRequestURL(event).pathname
  console.log(`[${new Date().toISOString()}] ${method} ${url}`)
})
