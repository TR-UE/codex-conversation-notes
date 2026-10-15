const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..'),context={window:{},console,document:{compatMode:'CSS1Compat',createElement:()=>({set innerHTML(value){this.textContent=value.replace(/&amp;/g,'&').replace(/&semi;/g,';');}})}};vm.createContext(context);
for(const f of ['web/vendor/rich-vendor.js','web/rich-text.js']){try{vm.runInContext(fs.readFileSync(path.join(root,f),'utf8'),context);}catch(error){throw new Error(error.message);}}
const R=context.window.NotesRich;
const text='开头 **🌸重点**，还有 *斜体* 和 `代码`。\n\n| 名称 | 数值 |\n| --- | ---: |\n| 测试 | 42 |\n\n公式 $x^2$ 和 \\(y+1\\)。\n\n\\[\na^2+b^2=c^2\n\\]\n\n```python\nprint("hi")\n```';
const ast=R.tree(text),all=[];function walk(n){all.push(n);for(const c of n.children||[])walk(c);}walk(ast);
for(const type of ['strong','emphasis','inlineCode','table','inlineMath','math','code'])assert.ok(all.some(n=>n.type===type),type);
const formula=all.find(n=>n.type==='math');assert.equal(text.slice(formula.position.start.offset,formula.position.end.offset),'\\[\na^2+b^2=c^2\n\\]');
assert.equal(R.excerpt(text,0,12),'开头 🌸重点，');
const cut=R.deletion('**前半后半**',2,4);assert.deepEqual(JSON.parse(JSON.stringify(cut.ranges)),[[2,4]]);
const full=R.deletion('**前半后半**',2,6);assert.deepEqual(JSON.parse(JSON.stringify(full.ranges)),[[0,8]]);
const entities=R.textMap('A &amp; B \\* C',0,14,'A & B * C');assert.equal(entities.map(p=>p.value).join(''),'A & B * C');
console.log('Markdown AST, math source spans, emoji, entities and format-safe deletion passed.');
