// Node-side regression tests for the browser editor's PDF marker safety.
import fs from 'node:fs'
import vm from 'node:vm'
import assert from 'node:assert/strict'

const source = fs.readFileSync(new URL('../app/static/editor.js', import.meta.url), 'utf8')
  .replace(/^import .+$/gm, '')
const context = vm.createContext({
  Node: {create: obj => obj},
  StarterKit: {configure: () => ({})},
  Markdown: {},
  document: {querySelector: () => null, querySelectorAll: () => []},
})
const {PdfAttachment, rememberPdfNodes, scanPdfMarkers} = vm.runInContext(
  source + '\n;({PdfAttachment,rememberPdfNodes,scanPdfMarkers})', context,
)
const id = '2710783d-86a4-472b-9602-1a6577cf435d'
const attrs = {fileId: id, fileName:'file.pdf', pageCount:3, sizeBytes:90, displayMode:'inline'}
rememberPdfNodes({type:'doc', content:[{type:'pdfAttachment', attrs}]})
assert.equal(typeof PdfAttachment.markdownTokenizer.tokenize,'function')
const marker = PdfAttachment.renderMarkdown({attrs})
const token = PdfAttachment.markdownTokenizer.tokenize(marker)
assert.equal(token.fileId,id)
assert.equal(token.displayMode,'inline')
assert.equal(PdfAttachment.parseMarkdown(token).attrs.fileId,id)
assert.equal(scanPdfMarkers(marker).length,1)
assert.throws(() => scanPdfMarkers(marker.replace(']', '')), /PDF 標記不完整/)
assert.throws(() => scanPdfMarkers(marker+':::notice-pdf[invalid'), /PDF 標記不完整/)
console.log('PDF tokenization and incomplete-marker safety checks passed')
