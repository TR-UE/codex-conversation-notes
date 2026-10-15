/* Render a source-positioned Markdown AST. Untrusted HTML is always text. */
(() => {
  const V = NotesRichVendor, cache = new Map(), images = new Map();
  const el = (tag, cls = '', text = '') => {const e = document.createElement(tag); e.className = cls; e.textContent = text; return e;};
  function tree(source) {
    if (!cache.has(source)) {if (cache.size > 24) cache.delete(cache.keys().next().value); cache.set(source, V.parse(source));}
    return cache.get(source);
  }
  const {textMap,innerMap} = V;
  function noteAt(notes,a,b,active) {const found = notes.filter(n=>n.start < b && n.end > a); return found.find(n=>n.id === active) || found.at(-1);}
  function sourceRun(parent,piece,notes,active) {
    const bounds = piece.b-piece.a === piece.value.length ? [...new Set([piece.a,piece.b,...notes.flatMap(n=>[n.start,n.end]).filter(p=>p>piece.a && p<piece.b)])].sort((a,b)=>a-b) : [piece.a,piece.b];
    for (let i=0;i<bounds.length-1;i++) {
      const a=bounds[i],b=bounds[i+1],linear=piece.b-piece.a===piece.value.length;
      const note=noteAt(notes,a,b,active), span=el(note?'mark':'span','source-run'+(note?.id===active?' focused':''),linear?piece.value.slice(a-piece.a,b-piece.a):piece.value);
      span.dataset.sourceStart=a; span.dataset.sourceEnd=b;
      if(note){span.dataset.noteId=note.id;span.dataset.color=note.color;span.title=note.comment||'点击查看批注';}
      parent.append(span);
    }
  }
  function mapped(parent,map,notes,active) {for(const p of map)sourceRun(parent,p,notes,active);}
  function atom(node,options,tag='span',cls='') {
    const a=node.position.start.offset,b=node.position.end.offset,note=noteAt(options.notes,a,b,options.active);
    const box=el(note?'mark':tag,'rich-atomic '+cls+(note?.id===options.active?' focused':'')); box.dataset.sourceStart=a;box.dataset.sourceEnd=b;
    if(note){box.dataset.noteId=note.id;box.dataset.color=note.color;}
    return box;
  }
  function safeUrl(url) {try {const u=new URL(url);return ['http:','https:','codex:'].includes(u.protocol)?u.href:null;}catch{return url?.startsWith('#')?url:null;}}
  function openImage(src,name,options,data) {
    let dialog=document.getElementById('rich-image-dialog');
    if(!dialog){dialog=el('dialog','rich-image-dialog');dialog.id='rich-image-dialog';dialog.setAttribute('aria-label','查看图片');document.body.append(dialog);}
    dialog.replaceChildren();const heading=el('div','image-dialog-heading'),close=el('button','icon-button','×');close.setAttribute('aria-label','关闭图片');close.onclick=()=>dialog.close();heading.append(el('span','',name||'图片'),close);
    const img=el('img');img.src=src;img.alt=name||'图片';dialog.append(heading,img);
    if(data){const save=el('button','quiet','保存图片');save.onclick=()=>options.download(data).catch(e=>options.toast(e.message));dialog.append(save);}
    dialog.showModal();
  }
  function image(node, options, reference, name, attachment=false) {
    const box=attachment?el('figure','image-panel'):atom(node,options,'figure','image-panel');
    const img=el('img','message-image');img.alt=name||'对话图片';img.loading='lazy';img.referrerPolicy='no-referrer';
    const status=el('div','image-status','正在读取图片…'),caption=el('figcaption','image-caption'),label=el('span','',name||'图片');
    const remote=/^https?:\/\//i.test(reference); let data=null;
    caption.append(label);
    if(!attachment){const note=el('button','quiet','批注图片');note.onclick=e=>{e.preventDefault();options.select(node.position.start.offset,node.position.end.offset,'已选择图片');};caption.append(note);}
    box.append(status,caption);
    async function load() {
      status.textContent='正在读取图片…';
      try {
        let src;
        if(remote) src=reference;
        else {const key=options.messageId+'\n'+reference;if(!images.has(key))images.set(key,options.image(reference).catch(e=>{images.delete(key);throw e;}));data=await images.get(key);src='data:'+data.mime+';base64,'+data.blob;}
        if(!box.isConnected)return;
        img.onload=()=>{status.remove();options.imageLoaded?.();};
        img.onerror=()=>{img.remove();status.replaceChildren(el('span','','图片暂时无法显示。'));const retry=el('button','quiet','重试');retry.onclick=load;status.append(retry);};
        img.src=src;img.onclick=()=>openImage(src,name,options,data);box.insertBefore(img,caption);
      } catch(error){status.replaceChildren(el('span','',error.message));const retry=el('button','quiet','重试');retry.onclick=load;status.append(retry);}
    }
    if(remote){status.replaceChildren(el('span','','远程图片'));const button=el('button','quiet','点击加载');button.onclick=load;status.append(button);const link=el('button','quiet','查看原图 ↗');link.onclick=()=>options.open(reference);caption.append(link);}
    else {const observer=new IntersectionObserver(entries=>{if(entries.some(e=>e.isIntersecting)){observer.disconnect();load();}},{rootMargin:'350px'});observer.observe(box);}
    return box;
  }
  function render(root,source,options) {
    options={notes:[],...options};root.classList.add('rich-text');root.replaceChildren();
    if(options.sourceMode){root.classList.add('source-view');mapped(root,[{a:0,b:source.length,value:source}],options.notes,options.active);return;}
    root.classList.remove('source-view');const ast=tree(source),definitions=new Map(),footnotes=[];
    for(const n of ast.children){if(n.type==='definition')definitions.set(n.identifier,n);if(n.type==='footnoteDefinition')footnotes.push(n);}
    function children(node,target){for(const c of node.children||[])target.append(draw(c));}
    function draw(node) {
      let e;
      switch(node.type){
        case 'text': case 'html': e=document.createDocumentFragment();mapped(e,innerMap(node,source),options.notes,options.active);return e;
        case 'paragraph': e=el('p');break;
        case 'heading': e=el('h'+Math.min(6,node.depth));break;
        case 'strong':e=el('strong');break;case 'emphasis':e=el('em');break;case 'delete':e=el('del');break;
        case 'blockquote':e=el('blockquote');break;
        case 'list':e=el(node.ordered?'ol':'ul');if(node.ordered&&node.start)e.start=node.start;break;
        case 'listItem':e=el('li');if(typeof node.checked==='boolean'){const input=el('input');input.type='checkbox';input.checked=node.checked;input.disabled=true;input.setAttribute('aria-label',node.checked?'已完成':'未完成');e.append(input);e.classList.add('task-item');}break;
        case 'thematicBreak':return el('hr');case 'break':return el('br');
        case 'inlineCode':e=el('code','inline-code');mapped(e,innerMap(node,source),options.notes,options.active);return e;
        case 'code':{
          const box=el('div','code-block'),bar=el('div','code-heading'),copy=el('button','quiet','复制');bar.append(el('span','',node.lang||'代码'),copy);copy.onclick=()=>navigator.clipboard.writeText(node.value).then(()=>{copy.textContent='已复制';}).catch(()=>options.toast('请选中代码后复制'));
          const pre=el('pre'),code=el('code','hljs');let html;
          try{if(node.lang&&V.highlight.getLanguage(node.lang)&&node.value.length<100000)html=V.highlight.highlight(node.value,{language:node.lang,ignoreIllegals:true}).value;}catch{}
          if(html)code.innerHTML=html;else code.textContent=node.value;
          const map=innerMap(node,source),walker=document.createTreeWalker(code,NodeFilter.SHOW_TEXT),texts=[];while(walker.nextNode())texts.push(walker.currentNode);
          let offset=0;for(const t of texts){const fragment=document.createDocumentFragment(),limit=offset+t.length;let cursor=0;for(const p of map){const x=Math.max(offset,cursor),y=Math.min(limit,cursor+p.value.length);if(x<y){const linear=p.b-p.a===p.value.length;mapped(fragment,[{a:linear?p.a+x-cursor:p.a,b:linear?p.a+y-cursor:p.b,value:p.value.slice(x-cursor,y-cursor)}],options.notes,options.active);}cursor+=p.value.length;}offset=limit;t.replaceWith(fragment);}
          pre.append(code);box.append(bar,pre);return box;
        }
        case 'math':case 'inlineMath':{
          e=atom(node,options,node.type==='math'?'div':'span','math-node'+(node.type==='math'?' math-block':''));e.tabIndex=0;e.setAttribute('role','button');e.setAttribute('aria-label','选择公式并批注');e.title=node.value;
          const formula=el('span','math-content');formula.innerHTML=V.katex.renderToString(node.value,{displayMode:node.type==='math',throwOnError:false,trust:false,strict:'ignore',maxExpand:1000,maxSize:20,output:'htmlAndMathml'});e.append(formula);
          const select=()=>options.select(node.position.start.offset,node.position.end.offset,'已选择公式');e.onclick=select;e.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select();}};return e;
        }
        case 'link':case 'linkReference':{
          const def=node.type==='link'?node:definitions.get(node.identifier),url=safeUrl(def?.url);
          e=el(url?'a':'span');if(url){e.href=url;e.rel='noopener noreferrer';e.onclick=event=>{event.preventDefault();if(window.getSelection()?.isCollapsed)options.open(url);};}if(def?.title)e.title=def.title;break;
        }
        case 'image':case 'imageReference':{const def=node.type==='image'?node:definitions.get(node.identifier);return image(node,options,def?.url||'',node.alt);}
        case 'table':{const wrap=el('div','table-scroll'),table=el('table');for(let i=0;i<node.children.length;i++){const section=el(i===0?'thead':'tbody'),tr=el('tr');for(let j=0;j<node.children[i].children.length;j++){const td=el(i===0?'th':'td');if(['left','center','right'].includes(node.align?.[j]))td.style.textAlign=node.align[j];children(node.children[i].children[j],td);tr.append(td);}section.append(tr);table.append(section);}wrap.append(table);return wrap;}
        case 'footnoteReference':{e=el('sup');const a=el('a','',node.label||node.identifier);a.href='#'+options.messageId+'-fn-'+node.identifier;e.append(a);return e;}
        case 'definition':case 'footnoteDefinition':return document.createDocumentFragment();
        default:e=el('span');
      }
      children(node,e);return e;
    }
    for(const node of ast.children)root.append(draw(node));
    if(footnotes.length){const section=el('section','footnotes');section.append(el('hr'));const list=el('ol');for(const n of footnotes){const item=el('li');item.id=options.messageId+'-fn-'+n.identifier;children(n,item);list.append(item);}section.append(list);root.append(section);}
    for(const a of options.attachments||[]){if(a.error){root.append(el('p','image-error',a.error));continue;}root.append(image(null,options,a.url||'attachment:'+a.key,a.filename||'对话图片',true));}
  }
  function point(body,node,offset,ending) {
    const parent=node.nodeType===Node.ELEMENT_NODE?node:node.parentElement;
    const item=parent?.closest('.source-run,.rich-atomic');
    if(item&&body.contains(item)){
      const a=Number(item.dataset.sourceStart),b=Number(item.dataset.sourceEnd);
      if(item.classList.contains('rich-atomic'))return ending?b:a;
      const before=document.createRange();before.selectNodeContents(item);before.setEnd(node,offset);const n=before.toString().length;
      return b-a===item.textContent.length?a+n:(ending?b:a);
    }
    const items=[...body.querySelectorAll('.source-run,.rich-atomic')];
    if(ending)items.reverse();
    for(const e of items){const r=document.createRange();r.selectNodeContents(e);const side=r.comparePoint(node,offset);if(ending?side>=0:side<=0)return Number(e.dataset[ending?'sourceEnd':'sourceStart']);}
    return ending?Number(items[0]?.dataset.sourceEnd||0):0;
  }
  function selection(body,range,source){const start=point(body,range.startContainer,range.startOffset,false),end=point(body,range.endContainer,range.endOffset,true);return end>start?{start,end,quote:source.slice(start,end)}:null;}
  function leaves(source){const result=[];function walk(n){if(['text','html','inlineCode','code'].includes(n.type))result.push(...innerMap(n,source));else if(['math','inlineMath','image','imageReference'].includes(n.type))result.push({a:n.position.start.offset,b:n.position.end.offset,value:n.value||n.alt||'图片'});else for(const c of n.children||[])walk(c);}walk(tree(source));return result;}
  function excerpt(source,start,end){let result='',previous=-1;for(const p of leaves(source)){const a=Math.max(start,p.a),b=Math.min(end,p.b);if(a>=b)continue;if(previous>=0&&/\n/.test(source.slice(previous,p.a)))result+='\n';result+=p.b-p.a===p.value.length?p.value.slice(a-p.a,b-p.a):p.value;previous=p.b;}return result||source.slice(start,end);}
  function deletion(source,start,end){let ranges=leaves(source).filter(p=>p.a<end&&p.b>start).map(p=>[p.b-p.a===p.value.length?Math.max(start,p.a):p.a,p.b-p.a===p.value.length?Math.min(end,p.b):p.b]);
    function expand(n){for(const c of n.children||[])expand(c);if(!['strong','emphasis','delete','link','linkReference','inlineCode','math','inlineMath','image','imageReference'].includes(n.type))return;const a=n.position.start.offset,b=n.position.end.offset,inside=leaves(source.slice(a,b));if(inside.length&&start<=a+inside[0].a&&end>=a+inside.at(-1).b)ranges.push([a,b]);}expand(tree(source));
    if(!ranges.length)ranges=[[start,end]];ranges.sort((a,b)=>a[0]-b[0]);const merged=[];for(const r of ranges){if(merged.length&&r[0]<=merged.at(-1)[1])merged.at(-1)[1]=Math.max(merged.at(-1)[1],r[1]);else merged.push(r);}return {start:merged[0][0],end:merged.at(-1)[1],quote:source.slice(merged[0][0],merged.at(-1)[1]),ranges:merged};
  }
  window.NotesRich={render,selection,excerpt,deletion,tree,textMap};
})();
