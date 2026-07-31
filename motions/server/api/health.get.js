export default defineEventHandler(() => {
  return {
    status: 'ok',
    service: 'motions',
    timestamp: new Date().toISOString()
  }
})
