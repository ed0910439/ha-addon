/**
 * Actual Lakelib (0.4.6) browser integration, not just helper-function tests.
 * Chrome is preinstalled on GitHub hosted Ubuntu runners.
 */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { chromium } from 'playwright-core'
const chrome = ['/usr/bin/google-chrome','/usr/bin/google-chrome-stable','/usr/bin/chromium','/usr/bin/chromium-browser']
  .find(path=>fs.existsSync(path))
assert.ok(chrome,'No system Chrome available for Lake browser test')
const browser = await chromium.launch({executablePath:chrome,headless:true,args:['--no-sandbox']})
try {
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror', error=>errors.push(error.message))
  await page.setContent('<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><div id="toolbar"></div><div id="editor"></div></body></html>')
  await page.addStyleTag({url:'https://cdn.jsdelivr.net/npm/lakelib@0.4.6/dist/lake.min.css'})
  await page.addScriptTag({url:'https://cdn.jsdelivr.net/npm/lakelib@0.4.6/dist/lake.min.js'})
  const html = await page.evaluate(()=>{
    const {Editor, Toolbar} = window.Lake || {}
    if(!Editor || !Toolbar)throw Error('Pinned Lakelib bundle missing Editor/Toolbar exports')
    const toolbar = new Toolbar({root:'#toolbar',items:['undo','redo','|','heading','bold','italic','bulletedList']})
    const editor = new Editor({root:'#editor',toolbar,value:'<h2>標題</h2><p>文字 <strong>粗體</strong></p>'})
    editor.render()
    window.testLakeEditor=editor
    const value=editor.getValue()
    if(!value.includes('標題')) throw Error('Lake failed to load original content')
    if(!editor.getHTML().includes('粗體')) throw Error('Lake standard HTML export missing text')
    editor.setValue('<p>後續測試</p>')
    if(!editor.getHTML().includes('後續測試')) throw Error('Lake setValue/getHTML round trip failed')
    editor.setValue('<h2>標題</h2><p>文字 <strong>粗體</strong></p>')
    return editor.getHTML()
  })
  const adapter = fs.readFileSync(new URL('../app/static/lake_adapter.js', import.meta.url),'utf8')
  const bridge = await page.evaluate(async source=>{
    const module=await import('data:text/javascript;base64,'+btoa(unescape(encodeURIComponent(source))))
    const doc=module.lakeHTMLToTiptap(document.querySelector('#editor')?window.testLakeEditor.getHTML():'')
    const original=module.tiptapToLakeHTML(doc)
    return {count:doc.content.length,original,eligible:module.eligibleForLake(doc),hasTitle:doc.content.some(n=>n.type==='heading')}
  },adapter)
  assert.ok(html.includes('標題'))
  assert.ok(bridge.eligible && bridge.hasTitle && bridge.original.includes('粗體'))
  assert.deepEqual(errors,[])
  await page.close()
  console.log('Real Lake Editor/Toolbar + HTML/Tiptap browser roundtrip passed')
} finally { await browser.close() }
