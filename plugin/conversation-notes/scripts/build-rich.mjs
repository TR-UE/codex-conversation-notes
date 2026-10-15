// Rebuild offline browser assets from the pinned npm project passed as argv[2].
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const project = path.resolve(process.argv[2]);
const require = createRequire(path.join(project, 'package.json'));
const {build} = require('esbuild');
const target = path.join(root, 'web/vendor'); fs.mkdirSync(target, {recursive:true});
await build({entryPoints:[path.join(root,'scripts/rich-entry.mjs')], outfile:path.join(target,'rich-vendor.js'),
  bundle:true, minify:true, format:'iife', globalName:'NotesRichVendor', platform:'browser', target:'es2020',
  nodePaths:[path.join(project,'node_modules')], legalComments:'eof'});
await build({entryPoints:[path.join(root,'scripts/export-entry.mjs')],outfile:path.join(root,'export/export-engine.mjs'),
  bundle:true,minify:true,format:'esm',platform:'node',target:'node22',
  banner:{js:"import {createRequire as __cr} from 'node:module'; const require=__cr(import.meta.url);"},
  nodePaths:[path.join(project,'node_modules')],legalComments:'eof'});
const katex = path.join(project,'node_modules/katex/dist');
let css = fs.readFileSync(path.join(katex,'katex.min.css'),'utf8');
css = css.replace(/src:([^;]+);/g, (all,value) => {
  const match = value.match(/url\(([^)]+\.woff2)\)/);
  return match ? 'src:url(data:font/woff2;base64,'+fs.readFileSync(path.join(katex,match[1])).toString('base64')+') format("woff2");' : all;
});
fs.writeFileSync(path.join(target,'katex.css'),css);
const licenses = [];
function collect(folder) {
  for (const name of fs.readdirSync(folder)) {
    if (name.startsWith('.')) continue;
    const p = path.join(folder,name);
    if (!fs.statSync(p).isDirectory()) continue;
    if (name.startsWith('@')) {collect(p);continue;}
    const pkg = path.join(p,'package.json'); if (!fs.existsSync(pkg)) continue;
    const meta = JSON.parse(fs.readFileSync(pkg,'utf8'));
    for (const f of fs.readdirSync(p)) if (/^(license|copying)(\.|$)/i.test(f) && fs.statSync(path.join(p,f)).isFile()) {
      licenses.push(meta.name+' '+meta.version+'\n'+fs.readFileSync(path.join(p,f),'utf8'));
    }
  }
}
collect(path.join(project,'node_modules'));
fs.writeFileSync(path.join(target,'THIRD-PARTY-LICENSES.txt'),licenses.join('\n\n---\n\n'));
for (const name of ['package.json','package-lock.json']) fs.copyFileSync(path.join(project,name),path.join(root,'scripts','rich-'+name));
console.log('Built Markdown, KaTeX, syntax colors and embedded fonts.');
