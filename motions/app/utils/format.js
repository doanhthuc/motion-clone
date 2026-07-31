export function formatDate(value, locale = 'vi-VN') {
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short'
  }).format(new Date(value))
}

export function truncate(text, max = 120) {
  return text.length > max ? `${text.slice(0, max)}…` : text
}
