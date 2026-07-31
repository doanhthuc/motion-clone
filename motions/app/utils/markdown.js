// #region ALD 20/05/2026 - Markdown renderer singleton + sanitize cho output AI
// KaTeX integrate: render `$...$` (inline) + `$$...$$` (display) + `\(...\)` + `\[...\]`
import MarkdownIt from 'markdown-it'
import katexPlugin from '@vscode/markdown-it-katex'

const md = new MarkdownIt({
  // html: true để render HTML tables/checkboxes từ Chandra OCR (tables phức tạp có
  // colspan/rowspan/merged cells — markdown table syntax không support).
  // XSS risk: AI output controlled (Chandra/Ollama), không lấy từ untrusted user.
  // Vẫn sanitize tag nguy hiểm (script/iframe/object/embed/on*-handlers) ở sanitizeUnsafeHtml().
  html: true,
  linkify: true,
  breaks: true,
  typographer: true
})

md.use(katexPlugin.default ?? katexPlugin, {
  throwOnError: false,   // công thức sai → render text raw thay vì throw, không break trang
  errorColor: '#dc2626',
  strict: false,
  output: 'html'
})

/**
 * Pre-clean noise từ vision model. GIỮ LẠI math LaTeX (KaTeX sẽ render):
 *   - `$...$`, `$$...$$` standard
 *   - `\(...\)`, `\[...\]` cũng được KaTeX plugin nhận
 *
 * STRIP non-math LaTeX (table tabular, line breaks…) trước khi parse markdown.
 * Model đôi khi wrap math bằng outer parens "(\vec{n}...)" — convert sang `$...$`.
 */
function stripLatexNoise(text) {
  let s = String(text ?? '')
  // \begin{tabular}, \begin{align}, \end{tabular}… → bỏ (giữ những env math chính)
  const KEEP_ENV = 'equation|align|matrix|cases|array|pmatrix|bmatrix|vmatrix|gathered|aligned'
  s = s.replace(new RegExp(`\\\\begin\\{(?!${KEEP_ENV})[^}]*\\}`, 'g'), '')
  s = s.replace(new RegExp(`\\\\end\\{(?!${KEEP_ENV})[^}]*\\}`, 'g'), '')
  // \hline, \toprule, \midrule, \bottomrule → bỏ (table styling)
  s = s.replace(/\\(?:hline|toprule|midrule|bottomrule)/g, '')
  // <think>...</think> của reasoning models
  s = s.replace(/<think>[\s\S]*?<\/think>/gi, '').trim()
  // Convert "(\command...)" wrapper thường thấy ở vision model → `$...$` chuẩn KaTeX.
  // Match: `(` content có ít nhất 1 LaTeX command `\word` rồi đến `)`.
  s = s.replace(
    /\(((?:\\[a-zA-Z]+|[^()\n])*?\\(?:vec|frac|sqrt|int|sum|prod|sin|cos|tan|log|ln|alpha|beta|gamma|delta|pi|theta|phi|infty|partial|nabla|cdot|times|leq|geq|neq|approx|in|mathbb|mathcal|mathrm|left|right|overrightarrow|overline|underline)[^()\n]*?)\)/g,
    (_m, inner) => `$${inner}$`
  )
  return s
}

// #region ALD 20/05/2026 - Caret ▍ wrap để blink animation theo dõi text streaming
function wrapCaret(html) {
  return html.replace(/▍/g, '<span class="caret-blink">▍</span>')
}
// #endregion

// Cho phép `<br>` qua (AI dùng để xuống dòng trong table cell — markdown table không
// support newline thật). Whitelist chỉ duy nhất `<br>` (không attribute, không script).
function allowBr(html) {
  return html.replace(/&lt;br\s*\/?\s*&gt;/gi, '<br>')
}

// Chandra OCR đôi khi output HTML với smart quotes (“”) trong attribute —
// browser không parse được `border="1"`. Replace về straight quote bên trong <...> tags.
function normalizeHtmlTagQuotes(text) {
  return text.replace(/<[^>]+>/g, (tag) =>
    tag.replace(/[“”]/g, '"').replace(/[‘’]/g, "'")
  )
}

// Sanitize: strip tag/attribute nguy hiểm dù html:true. AI output controlled nhưng
// vẫn defensive (vd model bị prompt-inject để emit <script>).
function sanitizeUnsafeHtml(html) {
  return html
    // Strip toàn bộ thẻ nguy hiểm + nội dung của chúng
    .replace(/<(script|iframe|object|embed|style|link|meta)\b[^>]*>[\s\S]*?<\/\1>/gi, '')
    .replace(/<(script|iframe|object|embed|style|link|meta)\b[^>]*\/?>/gi, '')
    // Strip on* event handler attributes (onclick, onerror, onload, ...)
    .replace(/\s+on[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '')
    // Strip javascript: URI scheme (trong href/src)
    .replace(/(href|src)\s*=\s*("|')\s*javascript:[^"']*\2/gi, '$1=""')
    // Strip data: URI scheme cho script content
    .replace(/(href|src)\s*=\s*("|')\s*data:text\/html[^"']*\2/gi, '$1=""')
}

export function renderMarkdown(text) {
  const pre = normalizeHtmlTagQuotes(stripLatexNoise(text))
  return wrapCaret(sanitizeUnsafeHtml(allowBr(md.render(pre))))
}

export function renderMarkdownInline(text) {
  const pre = normalizeHtmlTagQuotes(stripLatexNoise(text))
  return wrapCaret(sanitizeUnsafeHtml(allowBr(md.renderInline(pre))))
}
// #endregion
