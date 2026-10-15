// Shared Markdown parsing and deterministic equation/image assets for both exports.
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {parse,innerMap} from './rich-entry.mjs';
import {mathjax} from 'mathjax-full/js/mathjax.js';
import {TeX} from 'mathjax-full/js/input/tex.js';
import {SVG} from 'mathjax-full/js/output/svg.js';
import {liteAdaptor} from 'mathjax-full/js/adaptors/liteAdaptor.js';
import {RegisterHTMLHandler} from 'mathjax-full/js/handlers/html.js';
import {AllPackages} from 'mathjax-full/js/input/tex/AllPackages.js';

const config=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const require=createRequire(import.meta.url),sharp=require(config.node_modules+'/sharp');
const adaptor=liteAdaptor();RegisterHTMLHandler(adaptor);
// Omit packages capable of reading external URLs or changing surrounding HTML.
const packages=AllPackages.filter(p=>!['require','autoload','html'].includes(p));
const math=mathjax.document('',{InputJax:new TeX({packages,maxBuffer:20000,maxMacros:1000}),OutputJax:new SVG({fontCache:'local'})});
const mathCache=new Map();
async function formula(value,display) {
  const key=display+'\n'+value;if(mathCache.has(key))return mathCache.get(key);
  if(value.length>20000)throw new Error('公式较长，请分成几段后导出');
  const html=adaptor.outerHTML(math.convert(value,{display}));
  if(/data-mjx-error=/.test(html))return {error:'这条公式暂时无法排版',latex:value};
  let svg=html.slice(html.indexOf('<svg'),html.indexOf('</svg>')+6);
  const attr=name=>svg.match(new RegExp('(?:^|\\s)'+name+'="([^"]+)"'))?.[1]||'';
  const exSize=(display?12:11)*.45;
  const metric=text=>parseFloat(text)*(text.endsWith('ex')?exSize:text.endsWith('em')?exSize*2:1);
  const width=Math.max(1,metric(attr('width'))),height=Math.max(1,metric(attr('height')));
  const depth=Math.max(0,-metric(svg.match(/vertical-align:\s*([\d.e+\-]+(?:ex|em|px))/)?.[1]||'0'));
  const rasterScale=Math.min(4,2600/width,1800/height);
  svg=svg.replace(/\bwidth="[^"]+"/,`width="${Math.ceil(width*rasterScale)}px"`).replace(/\bheight="[^"]+"/,`height="${Math.ceil(height*rasterScale)}px"`).replace(/currentColor/g,'#202020');
  const png=await sharp(Buffer.from(svg)).png().toBuffer();
  const result={blob:png.toString('base64'),width,height,depth,latex:value};mathCache.set(key,result);return result;
}
async function picture(item) {
  if(!item.blob)return item;
  try {
    const image=sharp(Buffer.from(item.blob,'base64'),{limitInputPixels:40000000,animated:false});
    const meta=await image.metadata();
    const data=await image.rotate().resize({width:2600,height:2600,fit:'inside',withoutEnlargement:true}).png().toBuffer({resolveWithObject:true});
    return {...item,blob:data.data.toString('base64'),mime:'image/png',width:data.info.width,height:data.info.height};
  } catch{return {error:'这张图片无法转换为文档图片',filename:item.filename};}
}
async function prepare(documents) {
  for(const item of documents)for(const message of item.messages) {
    const ast=parse(message.body);
    async function walk(node) {
      if(['text','html','inlineCode','code'].includes(node.type))node.map=innerMap(node,message.body);
      if(['math','inlineMath'].includes(node.type))node.formula=await formula(node.value,node.type==='math');
      for(const child of node.children||[])await walk(child);
    }
    await walk(ast);message.ast=ast;
    for(const [key,value] of Object.entries(message.resources||{}))message.resources[key]=await picture(value);
    message.attachments=await Promise.all((message.attachments||[]).map(picture));
  }
  return documents;
}
function imageManifest(documents) {
  const result={};
  for(const item of documents)for(const message of item.messages){
    const ast=parse(message.body),definitions=new Map(ast.children.filter(n=>n.type==='definition').map(n=>[n.identifier,n]));const urls=[];
    function walk(node){if(node.type==='image')urls.push(node.url);if(node.type==='imageReference'&&definitions.has(node.identifier))urls.push(definitions.get(node.identifier).url);for(const c of node.children||[])walk(c);}walk(ast);
    result[message.id]=[...new Set(urls)];
  }
  return result;
}
try {const payload=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(process.argv[3]==='--images'?imageManifest(payload.documents):await prepare(payload.documents)));}
catch(error){process.stderr.write(error.message+'\n');process.exitCode=1;}
