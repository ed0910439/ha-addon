/**
 * Lake 0.4.6 compatibility bridge for legacy Notice Board Tiptap JSON.
 *
 * Lake natively edits HTML-like LML, not Tiptap JSON. Existing PDF-bearing
 * documents stay in the legacy editor until the special attachment blocks
 * have an end-to-end, browser-tested migration. Never silently discard data.
 */
const safeText = (s) => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')
const NODE_TYPES = new Set(['doc','paragraph','heading','text','bulletList','orderedList','listItem','blockquote','horizontalRule','codeBlock','hardBreak'])
const MARK_TYPES = new Set(['bold','italic','strike','code'])

export function eligibleForLake(doc) {
  if (!doc || doc.type !== 'doc') return false
  let safe = true
  const visit = node => {
    if (!node || !NODE_TYPES.has(node.type)) { safe = false; return }
    if (node.type === 'heading' && ![1,2,3].includes(node.attrs?.level || 2)) safe = false
    if ((node.marks || []).some(m => !MARK_TYPES.has(m.type))) safe = false
    for (const child of node.content || []) visit(child)
  }
  visit(doc)
  return safe
}

export function tiptapToLakeHTML(doc) {
  if (!eligibleForLake(doc)) throw new Error('公告包含 Lake 尚未驗證的格式或 PDF 附件，保留舊編輯器處理。')
  function walk(n) {
    const children = (n.content || []).map(walk).join('')
    switch(n.type) {
      case 'doc': return children || '<p><br /></p>'
      case 'paragraph': return '<p>'+(children || '<br />')+'</p>'
      case 'heading': return '<h'+(n.attrs?.level||2)+'>'+children+'</h'+(n.attrs?.level||2)+'>'
      case 'bulletList': return '<ul>'+children+'</ul>'
      case 'orderedList': return '<ol>'+children+'</ol>'
      case 'listItem': return '<li>'+children+'</li>'
      case 'blockquote': return '<blockquote>'+children+'</blockquote>'
      case 'horizontalRule': return '<hr />'
      case 'codeBlock': return '<pre><code>'+safeText((n.content||[]).map(c=>c.text||'').join(''))+'</code></pre>'
      case 'hardBreak': return '<br />'
      case 'text': {
        let value = safeText(n.text||'')
        for (const mark of (n.marks||[]).slice().reverse()) {
          const tag = {bold:'strong',italic:'em',strike:'s',code:'code'}[mark.type]
          value='<'+tag+'>'+value+'</'+tag+'>'
        }
        return value
      }
      default: throw new Error('無法轉換的公告節點')
    }
  }
  return walk(doc)
}

export function lakeHTMLToTiptap(html, Parser=globalThis.DOMParser) {
  if (!Parser) throw new Error('此瀏覽器無法安全解析編輯器內容。')
  const tree = new Parser().parseFromString('<!doctype html><html><body>'+String(html)+'</body></html>', 'text/html')
  const safeMarks = (marks) => [...new Map(marks.map(m=>[m.type,m])).values()]
  const children = node => Array.from(node.childNodes||[])
  function inline(node, marks=[]) {
    if (node.nodeType === 3) return node.textContent ? [{type:'text',text:node.textContent,...(marks.length?{marks:safeMarks(marks)}:{})}] : []
    if (node.nodeType !== 1) return []
    const name = node.tagName.toLowerCase()
    if (name === 'br') return [{type:'hardBreak'}]
    if (['img','iframe','script','style','lake-box','video','audio','svg'].includes(name)) throw new Error('內容包含不支援的嵌入物件，已阻止儲存。')
    const next = marks.slice()
    const kind = {strong:'bold',b:'bold',em:'italic',i:'italic',s:'strike',del:'strike',strike:'strike',code:'code'}[name]
    if (kind) next.push({type:kind})
    return children(node).flatMap(c=>inline(c,next))
  }
  function block(node) {
    if (node.nodeType === 3) return node.textContent.trim()?[{type:'paragraph',content:inline(node)}]:[]
    if (node.nodeType !== 1) return []
    const name = node.tagName.toLowerCase()
    if (['script','style','img','iframe','lake-box','video','svg','object','form'].includes(name)) throw new Error('Lake 輸出包含不支援的內容，已阻止儲存。')
    if (name==='hr') return [{type:'horizontalRule'}]
    if (name==='pre') return [{type:'codeBlock',content:[{type:'text',text:node.textContent||''}]}]
    if (name==='p') {
      const content=children(node).flatMap(c=>inline(c))
      return [content.length?{type:'paragraph',content}:{type:'paragraph'}]
    }
    if (/^h[1-3]$/.test(name)) {
      const content=children(node).flatMap(c=>inline(c))
      return [{type:'heading',attrs:{level:Number(name[1])},...(content.length?{content}:{})}]
    }
    if (name==='ul' || name==='ol') {
      const items=children(node).filter(n=>n.nodeType===1 && n.tagName.toLowerCase()==='li')
        .map(li=>({type:'listItem',content:children(li).flatMap(c=>block(c)).length?
          children(li).flatMap(c=>block(c)):[{type:'paragraph',content:children(li).flatMap(c=>inline(c))}]}))
      return [{type:name==='ul'?'bulletList':'orderedList',content:items}]
    }
    if (name==='blockquote') return [{type:'blockquote',content:children(node).flatMap(block)}]
    if (['div','section','article','body'].includes(name)) return children(node).flatMap(block)
    // Ignore harmless layout wrappers but never silently remove embedded widgets.
    if (['span','strong','em','b','i','s','del','code','a'].includes(name)) {
      const content=inline(node)
      return content.length?[{type:'paragraph',content}]:[]
    }
    throw new Error('Lake 輸出包含尚未支援的排版（'+name+'），保留原編輯內容。')
  }
  const result = children(tree.body).flatMap(block)
  let size=0
  const countText = n=>{
    if(n.type==='text') size+=(n.text||'').length
    for(const c of n.content||[]) countText(c)
  }
  result.forEach(countText)
  if(size>50000)throw new Error('公告文字超過 50,000 字。')
  return {type:'doc',content:result.length?result:[{type:'paragraph'}]}
}
