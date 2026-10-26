import assert from 'node:assert/strict'
import {DOMParser} from 'linkedom'
import { eligibleForLake, tiptapToLakeHTML, lakeHTMLToTiptap } from '../app/static/lake_adapter.js'

const doc = {
  type:'doc',
  content:[
    {type:'heading',attrs:{level:2},content:[{type:'text',text:'公告 <標題>'}]},
    {type:'paragraph',content:[{type:'text',text:'**這裡**是粗體示範',marks:[{type:'bold'}]}]},
    {type:'bulletList',content:[{type:'listItem',content:[{type:'paragraph',content:[{type:'text',text:'第一點'}]}]}]},
  ],
}
assert.equal(eligibleForLake(doc),true)
const html=tiptapToLakeHTML(doc)
assert.ok(html.includes('&lt;標題&gt;'))
assert.ok(html.includes('<strong>'))
const converted=lakeHTMLToTiptap(html,DOMParser)
assert.deepEqual(converted,doc)

const pdf={type:'doc',content:[{type:'pdfAttachment',attrs:{fileId:'protected-pdf'}}]}
assert.equal(eligibleForLake(pdf),false)
assert.throws(()=>tiptapToLakeHTML(pdf),/PDF/)
assert.throws(()=>lakeHTMLToTiptap('<p>OK</p><lake-box name="file"></lake-box>',DOMParser),/不支援|未支援/)
assert.throws(()=>lakeHTMLToTiptap('<p>OK</p><iframe src="https://example.com"></iframe>',DOMParser),/不支援|未支援/)
console.log('Lake bridge strict text and PDF fallback checks passed')
