/** Real Notice Board editor initialization in Chromium with the same CDN modules/CSP.
 * This catches ProseMirror keyed-plugin collisions that the Lake-only test misses.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'
import { chromium } from 'playwright-core'

const chrome = ['/usr/bin/google-chrome','/usr/bin/google-chrome-stable','/usr/bin/chromium','/usr/bin/chromium-browser']
  .find(p => fs.existsSync(p))
assert.ok(chrome, 'Chrome required for editor integration test')
const editorJS = fs.readFileSync(new URL('../app/static/editor.bundle.js', import.meta.url), 'utf8')
const bridgeJS = fs.readFileSync(new URL('../app/static/lake_editor.js', import.meta.url), 'utf8')
const adapterJS = fs.readFileSync(new URL('../app/static/lake_adapter.js', import.meta.url), 'utf8')
assert.ok(editorJS.length > 100000, 'Production Tiptap bundle was not generated')
assert.ok(!editorJS.includes('https://cdn.jsdelivr.net/npm/@tiptap/core@3.31.3/+esm'), 'Old Tiptap CDN import is still present')
const contents = new Map([
  ['/static/editor.bundle.js', editorJS],
])
const pdfDoc = {type:'doc',content:[
  {type:'paragraph',content:[{type:'text',text:'Existing PDF stays intact'}]},
  {type:'pdfAttachment',attrs:{fileId:'01234567-89ab-cdef-0123-456789abcdef',fileName:'sample.pdf',pageCount:2,sizeBytes:123,displayMode:'attachment'}},
]}
const standardDoc = {type:'doc',content:[{type:'paragraph',content:[{type:'text',text:'A fresh Lake notice'}]}]}
const html = doc => `<!doctype html><html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="app-base-path" content="">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/lakelib@0.4.6/dist/lake.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/lakelib@0.4.6/dist/lake.min.js"></script>
</head><body>
<form id="announcement-editor-form" action="/saved" method="post">
<input type="hidden" name="csrf_token" value="test"><input type="hidden" name="upload_session" value="test">
<div class="tiptap-shell" data-tiptap-editor>
 <div class="editor-header-row"><input type="checkbox" data-markdown-toggle></div>
 <div data-editor-toolbar><button type="button" data-command="bold">Bold</button><button type="button" data-upload-pdf>PDF</button></div>
 <div data-editor-mount></div>
 <div data-markdown-panel hidden><textarea data-markdown-source></textarea></div>
 <div data-markdown-error hidden></div>
 <input data-pdf-input type="file" hidden>
 <textarea name="body"></textarea><textarea name="body_json"></textarea>
 <div data-upload-status></div>
</div><button type="submit">Save</button></form>
<dialog id="pdf-display-dialog"><form method="dialog"><button type="button" data-pdf-mode="attachment">Attach</button></form></dialog>
<script type="application/json" id="editor-initial-content">${JSON.stringify(doc)}</script>
<script type="module" src="/static/editor.bundle.js"></script>
</body></html>`
const csp = "default-src 'self'; img-src 'self' data: https:; style-src 'self' https://cdn.jsdelivr.net; script-src 'self' https://cdn.jsdelivr.net; connect-src 'self' https://cdn.jsdelivr.net; frame-src 'self'; object-src 'self'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
const server = http.createServer((req,res) => {
  res.setHeader('Content-Security-Policy',csp)
  const p = (req.url || '').split('?')[0]
  if (contents.has(p)) {
    res.writeHead(200,{'Content-Type':'text/javascript; charset=utf-8'})
    return res.end(contents.get(p))
  }
  if (p === '/new' || p === '/pdf') {
    res.writeHead(200,{'Content-Type':'text/html; charset=utf-8'})
    return res.end(html(p === '/pdf' ? pdfDoc : standardDoc))
  }
  res.writeHead(404)
  res.end('Not found')
})
await new Promise(resolve => server.listen(0,'127.0.0.1',resolve))
const port = server.address().port
const browser = await chromium.launch({executablePath:chrome,headless:true,args:['--no-sandbox']})
try {
  for (const scenario of [
    {route:'/new',expected:'lake'},
    {route:'/pdf',expected:'legacy'},
  ]) {
    const page = await browser.newPage()
    const errors = []
    page.on('pageerror', e => errors.push(e.message))
    const response = await page.goto(`http://127.0.0.1:${port}${scenario.route}`,{waitUntil:'load'})
    assert.equal(response?.status(),200)
    await page.waitForTimeout(4500)
    const state = await page.evaluate(() => ({
      lake: Boolean(document.querySelector('.lake-editor-shell') && !document.querySelector('.lake-editor-shell').hidden),
      legacy: Boolean(document.querySelector('[data-editor-mount] .ProseMirror')),
      json: document.querySelector('textarea[name="body_json"]')?.value,
      note: document.querySelector('[data-tiptap-editor]')?.textContent?.slice(0,150),
    }))
    assert.deepEqual(errors,[],`${scenario.route} script errors: ${errors.join('; ')}`)
    assert.ok(state.legacy, `${scenario.route}: initial Tiptap must mount for PDF fallback`)
    if (scenario.expected === 'lake') assert.ok(state.lake, 'Lake should initialize for supported notice')
    if (scenario.expected === 'legacy') {
      assert.ok(!state.lake, 'Legacy PDF notice must not use Lake')
      assert.ok(state.json?.includes('pdfAttachment'), 'PDF node must survive Tiptap initialization')
    }
    await page.close()
  }
  console.log('Notice Board Tiptap/Lake real-browser initialization passed, with strict CSP')
} finally {
  await browser.close()
  await new Promise((resolve,reject) => server.close(err => err ? reject(err) : resolve()))
}
