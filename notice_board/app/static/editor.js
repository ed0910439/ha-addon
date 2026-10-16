import { Editor, Node } from 'https://cdn.jsdelivr.net/npm/@tiptap/core@3.31.3/+esm'
import StarterKit from 'https://cdn.jsdelivr.net/npm/@tiptap/starter-kit@3.31.3/+esm'

const bytesLabel = (bytes) => {
  const value = Number(bytes || 0)
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

const appBasePath = (document.querySelector('meta[name="app-base-path"]')?.content || '').replace(/\/$/, '')
const appUrl = (path) => `${appBasePath}${path.startsWith('/') ? path : '/' + path}`

const safeFileUrl = (fileId, download = false) =>
  appUrl(`/files/${encodeURIComponent(String(fileId || ''))}${download ? '?download=true' : ''}`)

const PdfAttachment = Node.create({
  name: 'pdfAttachment',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: true,

  addAttributes() {
    return {
      fileId: { default: null },
      fileName: { default: 'PDF' },
      pageCount: { default: 0 },
      sizeBytes: { default: 0 },
      displayMode: { default: 'attachment' },
    }
  },

  parseHTML() {
    return [{ tag: 'figure[data-pdf-attachment]' }]
  },

  renderHTML({ node }) {
    const { fileId, fileName, pageCount, displayMode } = node.attrs
    const base = {
      'data-pdf-attachment': '',
      'data-file-id': String(fileId || ''),
      'data-display-mode': displayMode,
      class: `pdf-node pdf-${displayMode}`,
    }
    if (displayMode === 'inline') {
      return [
        'figure', base,
        ['div', { class: 'pdf-node-head' },
          ['strong', {}, String(fileName || 'PDF')],
          ['span', {}, `${Number(pageCount || 0)} 頁 · 直接顯示`],
        ],
        ['iframe', {
          src: safeFileUrl(fileId),
          title: String(fileName || 'PDF'),
          loading: 'lazy',
          class: 'pdf-frame',
        }],
        ['a', { href: safeFileUrl(fileId), target: '_blank', rel: 'noopener', class: 'pdf-open-link' }, '另開 PDF'],
      ]
    }
    return [
      'figure', base,
      ['a', { href: safeFileUrl(fileId, true), class: 'pdf-attachment-card' },
        ['span', { class: 'pdf-icon', 'aria-hidden': 'true' }, 'PDF'],
        ['span', { class: 'pdf-file-meta' },
          ['strong', {}, String(fileName || 'PDF')],
          ['span', {}, `${Number(pageCount || 0)} 頁 · 附檔`],
        ],
      ],
    ]
  },

  addNodeView() {
    return ({ editor, node, getPos }) => {
      const dom = document.createElement('div')
      dom.className = 'pdf-node-editor'
      dom.contentEditable = 'false'

      const render = (currentNode) => {
        dom.replaceChildren()
        const attrs = currentNode.attrs
        const head = document.createElement('div')
        head.className = 'pdf-node-editor-main'

        const badge = document.createElement('span')
        badge.className = 'pdf-icon'
        badge.textContent = 'PDF'

        const meta = document.createElement('div')
        meta.className = 'pdf-file-meta'
        const name = document.createElement('strong')
        name.textContent = attrs.fileName || 'PDF'
        const detail = document.createElement('span')
        detail.textContent = `${Number(attrs.pageCount || 0)} 頁 · ${bytesLabel(attrs.sizeBytes)} · ${attrs.displayMode === 'inline' ? '直接顯示' : '附檔'}`
        meta.append(name, detail)
        head.append(badge, meta)
        dom.append(head)

        if (editor.isEditable) {
          const controls = document.createElement('div')
          controls.className = 'pdf-node-controls'
          if (Number(attrs.pageCount || 0) <= 10) {
            const toggle = document.createElement('button')
            toggle.type = 'button'
            toggle.className = 'btn small-btn'
            toggle.textContent = attrs.displayMode === 'inline' ? '改成附檔' : '改成直接顯示'
            toggle.addEventListener('click', () => {
              const pos = getPos()
              if (typeof pos !== 'number') return
              editor.view.dispatch(editor.view.state.tr.setNodeMarkup(pos, undefined, {
                ...attrs,
                displayMode: attrs.displayMode === 'inline' ? 'attachment' : 'inline',
              }))
            })
            controls.append(toggle)
          }
          const remove = document.createElement('button')
          remove.type = 'button'
          remove.className = 'btn danger-outline small-btn'
          remove.textContent = '移除'
          remove.addEventListener('click', () => {
            const pos = getPos()
            if (typeof pos !== 'number') return
            editor.commands.deleteRange({ from: pos, to: pos + currentNode.nodeSize })
          })
          controls.append(remove)
          dom.append(controls)
        } else if (attrs.displayMode === 'inline') {
          const frame = document.createElement('iframe')
          frame.src = safeFileUrl(attrs.fileId)
          frame.title = attrs.fileName || 'PDF'
          frame.loading = 'lazy'
          frame.className = 'pdf-frame'
          dom.append(frame)
          const open = document.createElement('a')
          open.href = safeFileUrl(attrs.fileId)
          open.target = '_blank'
          open.rel = 'noopener'
          open.className = 'pdf-open-link'
          open.textContent = '另開 PDF'
          dom.append(open)
        } else {
          const open = document.createElement('a')
          open.href = safeFileUrl(attrs.fileId, true)
          open.className = 'pdf-open-link'
          open.textContent = '開啟附檔'
          dom.append(open)
        }
      }

      render(node)
      return {
        dom,
        update(updatedNode) {
          if (updatedNode.type.name !== 'pdfAttachment') return false
          node = updatedNode
          render(updatedNode)
          return true
        },
      }
    }
  },
})

const extensions = [StarterKit.configure({ link: false, underline: false }), PdfAttachment]

function readJsonScript(selector, fallback = { type: 'doc', content: [{ type: 'paragraph' }] }) {
  const el = document.querySelector(selector)
  if (!el) return fallback
  try { return JSON.parse(el.textContent || '') } catch { return fallback }
}

function updateToolbar(editor, toolbar) {
  toolbar?.querySelectorAll('[data-command]').forEach((button) => {
    const command = button.dataset.command
    let active = false
    if (command === 'bold') active = editor.isActive('bold')
    if (command === 'italic') active = editor.isActive('italic')
    if (command === 'strike') active = editor.isActive('strike')
    if (command === 'heading2') active = editor.isActive('heading', { level: 2 })
    if (command === 'heading3') active = editor.isActive('heading', { level: 3 })
    if (command === 'bulletList') active = editor.isActive('bulletList')
    if (command === 'orderedList') active = editor.isActive('orderedList')
    if (command === 'blockquote') active = editor.isActive('blockquote')
    button.classList.toggle('is-active', active)
  })
}

async function choosePdfMode(dialog, info) {
  if (!info.inline_allowed) return 'attachment'
  if (!dialog || typeof dialog.showModal !== 'function') {
    return window.confirm(`這份 PDF 共 ${info.page_count} 頁。\n確定＝直接顯示於公告；取消＝作為附檔。`) ? 'inline' : 'attachment'
  }
  const name = dialog.querySelector('[data-pdf-dialog-name]')
  const pages = dialog.querySelector('[data-pdf-dialog-pages]')
  if (name) name.textContent = info.file_name
  if (pages) pages.textContent = `${info.page_count} 頁`
  dialog.showModal()
  return new Promise((resolve) => {
    const buttons = Array.from(dialog.querySelectorAll('[data-pdf-mode]'))
    const onCancel = (event) => {
      event.preventDefault()
      finish('attachment')
    }
    const finish = (mode) => {
      dialog.removeEventListener('cancel', onCancel)
      buttons.forEach((button) => { button.onclick = null })
      if (dialog.open) dialog.close()
      resolve(mode)
    }
    buttons.forEach((button) => {
      button.onclick = () => finish(button.dataset.pdfMode)
    })
    dialog.addEventListener('cancel', onCancel)
  })
}

function initEditor() {
  const root = document.querySelector('[data-tiptap-editor]')
  if (!root) return
  const form = root.closest('form')
  const mount = root.querySelector('[data-editor-mount]')
  const fallback = root.querySelector('textarea[name="body"]')
  const jsonField = root.querySelector('textarea[name="body_json"]')
  const toolbar = root.querySelector('[data-editor-toolbar]')
  const fileInput = root.querySelector('input[type="file"][data-pdf-input]')
  const uploadButton = root.querySelector('[data-upload-pdf]')
  const uploadStatus = root.querySelector('[data-upload-status]')
  const dialog = document.querySelector('#pdf-display-dialog')
  const initialContent = readJsonScript('#editor-initial-content')

  let editor
  const sync = (instance) => {
    if (!instance) return
    if (jsonField) jsonField.value = JSON.stringify(instance.getJSON())
    if (fallback) fallback.value = instance.getText({ blockSeparator: '\n' })
  }

  editor = new Editor({
    element: mount,
    extensions,
    content: initialContent,
    editorProps: { attributes: { class: 'tiptap-prosemirror' } },
    onCreate: ({ editor }) => {
      root.classList.add('editor-ready')
      sync(editor)
      updateToolbar(editor, toolbar)
    },
    onUpdate: ({ editor }) => sync(editor),
    onSelectionUpdate: ({ editor }) => updateToolbar(editor, toolbar),
    onTransaction: ({ editor }) => updateToolbar(editor, toolbar),
  })

  toolbar?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-command]')
    if (!button) return
    const command = button.dataset.command
    const chain = editor.chain().focus()
    if (command === 'bold') chain.toggleBold().run()
    else if (command === 'italic') chain.toggleItalic().run()
    else if (command === 'strike') chain.toggleStrike().run()
    else if (command === 'heading2') chain.toggleHeading({ level: 2 }).run()
    else if (command === 'heading3') chain.toggleHeading({ level: 3 }).run()
    else if (command === 'bulletList') chain.toggleBulletList().run()
    else if (command === 'orderedList') chain.toggleOrderedList().run()
    else if (command === 'blockquote') chain.toggleBlockquote().run()
    else if (command === 'horizontalRule') chain.setHorizontalRule().run()
    else if (command === 'undo') chain.undo().run()
    else if (command === 'redo') chain.redo().run()
  })

  uploadButton?.addEventListener('click', () => fileInput?.click())
  fileInput?.addEventListener('change', async () => {
    const file = fileInput.files?.[0]
    fileInput.value = ''
    if (!file) return
    if (!file.name.toLowerCase().endsWith('.pdf') && file.type !== 'application/pdf') {
      if (uploadStatus) uploadStatus.textContent = '請選擇 PDF 檔案。'
      return
    }
    uploadButton.disabled = true
    if (uploadStatus) uploadStatus.textContent = `正在檢查 ${file.name}…`
    try {
      const fd = new FormData()
      fd.append('file', file)
      fd.append('csrf_token', form.querySelector('input[name="csrf_token"]').value)
      fd.append('upload_session', form.querySelector('input[name="upload_session"]').value)
      fd.append('announcement_id', form.querySelector('input[name="announcement_id"]')?.value || '')
      const response = await fetch(appUrl('/api/attachments/pdf'), { method: 'POST', body: fd, credentials: 'same-origin' })
      const info = await response.json()
      if (!response.ok) throw new Error(info.error || 'PDF 上傳失敗')
      const mode = await choosePdfMode(dialog, info)
      editor.chain().focus().insertContent({
        type: 'pdfAttachment',
        attrs: {
          fileId: info.file_id,
          fileName: info.file_name,
          pageCount: info.page_count,
          sizeBytes: info.size_bytes,
          displayMode: mode,
        },
      }).run()
      sync(editor)
      if (uploadStatus) {
        uploadStatus.textContent = info.inline_allowed
          ? `已加入 ${info.file_name}（${info.page_count} 頁，${mode === 'inline' ? '直接顯示' : '附檔'}）。`
          : `已加入 ${info.file_name}（${info.page_count} 頁）；超過 10 頁，已依規則設為附檔。`
      }
    } catch (error) {
      if (uploadStatus) uploadStatus.textContent = error?.message || 'PDF 上傳失敗。'
    } finally {
      uploadButton.disabled = false
    }
  })

  form?.addEventListener('submit', () => sync(editor))
}

function initViewers() {
  document.querySelectorAll('[data-tiptap-viewer]').forEach((element, index) => {
    const scriptId = element.dataset.contentScript
    const content = readJsonScript(`#${CSS.escape(scriptId || `viewer-content-${index}`)}`)
    element.replaceChildren()
    new Editor({
      element,
      extensions,
      content,
      editable: false,
      editorProps: { attributes: { class: 'tiptap-prosemirror tiptap-readonly' } },
    })
  })
}

initEditor()
initViewers()
