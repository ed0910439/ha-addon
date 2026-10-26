import { eligibleForLake, tiptapToLakeHTML, lakeHTMLToTiptap } from './lake_adapter.js'

/**
 * Lake is the preferred editor for supported Tiptap content.
 * Complex existing documents (especially PDFs) remain on the legacy editor.
 * Both paths continue to submit existing body_json and body fields.
 */
export function initializeLakeEditor(root, legacyEditor) {
  if (!root || !legacyEditor || !window.Lake?.Editor || !window.Lake?.Toolbar) return false
  const form = root.closest('form')
  const initial = legacyEditor.getJSON()
  if (!eligibleForLake(initial)) {
    const note = document.createElement('p')
    note.className = 'small muted'
    note.textContent = '這篇公告包含尚未完成相容性驗證的內容（例如 PDF）。為保護原始內容，暫時使用原編輯器。'
    root.prepend(note)
    return false
  }

  const oldToolbar = root.querySelector('[data-editor-toolbar]')
  const oldMount = root.querySelector('[data-editor-mount]')
  const switchRow = root.querySelector('.editor-header-row')
  const oldToggle = root.querySelector('[data-markdown-toggle]')
  const oldPanel = root.querySelector('[data-markdown-panel]')
  if (!oldMount || !oldToolbar) return false

  const shell = document.createElement('section')
  shell.className = 'lake-editor-shell'
  const bar = document.createElement('div')
  bar.className = 'lake-editor-toolbar'
  bar.setAttribute('aria-label', 'Lake 富文字編輯工具列')
  const region = document.createElement('div')
  region.className = 'lake-editor-region'
  region.setAttribute('aria-label', '公告內文')
  shell.append(bar, region)
  oldToolbar.before(shell)

  let lake
  try {
    const toolbar = new window.Lake.Toolbar({
      root: bar,
      items: [
        'undo', 'redo', '|', 'heading', 'paragraph', '|',
        'bold', 'italic', 'strikethrough', 'code', '|',
        'bulletedList', 'numberedList', 'blockQuote', 'hr',
      ],
    })
    lake = new window.Lake.Editor({
      root: region,
      toolbar,
      value: tiptapToLakeHTML(initial),
      lang: 'en-US',
      spellcheck: true,
    })
    lake.render()
  } catch (error) {
    console.error('Lake 無法初始化，已保留 Tiptap 編輯器：', error)
    shell.remove()
    return false
  }

  const note = document.createElement('div')
  note.className = 'small muted lake-info'
  note.textContent = 'Lake 已啟用：支援直接輸入 #、-、> 等 Markdown 快捷排版。加入 PDF 時會切換到原附件編輯器。'
  shell.append(note)
  const actions = document.createElement('div')
  actions.className = 'lake-editor-actions'
  const pdf = document.createElement('button')
  pdf.type = 'button'
  pdf.className = 'btn'
  pdf.textContent = '加入 PDF'
  actions.append(pdf)
  shell.append(actions)

  function convertLake() {
    const doc = lakeHTMLToTiptap(lake.getHTML())
    // PDF nodes are explicitly excluded from eligibleForLake(), so Lake cannot
    // silently remove an existing PDF during this conversion.
    if (!eligibleForLake(doc)) throw new Error('Lake 轉換產生未支援的文件類型。')
    return doc
  }

  const status = document.createElement('div')
  status.className = 'alert danger'
  status.hidden = true
  shell.append(status)
  function warn(message) {
    status.textContent = message
    status.hidden = !message
  }

  oldToolbar.hidden = true
  oldMount.hidden = true
  if (switchRow) switchRow.hidden = true
  if (oldPanel) oldPanel.hidden = true

  pdf.addEventListener('click', () => {
    try {
      const doc = convertLake()
      legacyEditor.commands.setContent(doc)
      shell.hidden = true
      oldToolbar.hidden = false
      oldMount.hidden = false
      if (switchRow) switchRow.hidden = false
      if (oldToggle) oldToggle.checked = false
      if (root.querySelector('[data-upload-pdf]')) root.querySelector('[data-upload-pdf]').click()
    } catch (error) {
      warn(error.message || '內容轉換失敗，保留 Lake 編輯器與現有文字。')
    }
  })

  form.addEventListener('submit', event => {
    if (shell.hidden || event.defaultPrevented) return
    try {
      const doc = convertLake()
      const json = root.querySelector('textarea[name="body_json"]')
      const plain = root.querySelector('textarea[name="body"]')
      if (json) json.value = JSON.stringify(doc)
      if (plain) plain.value = lake.getHTML().replace(/<[^>]+>/g,' ').trim()
    } catch (error) {
      event.preventDefault()
      warn(error.message || '編輯內容無法安全轉換，已阻止儲存。')
    }
  })

  return true
}
