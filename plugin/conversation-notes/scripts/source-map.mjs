import {decodeString} from 'micromark-util-decode-string';
export function textMap(source, start, end, value) {
    const raw = source.slice(start,end), result = []; let i = 0, j = 0;
    function token(at) {
      if (raw[at] === '\\' && /[!"#$%&'()*+,\-./:;<=>?@[\]\\^_`{|}~]/.test(raw[at+1] || '')) return [raw[at+1],2];
      if (raw[at] === '&') {const m = raw.slice(at).match(/^&(?:#[xX][\da-fA-F]+|#\d+|[A-Za-z][\w]+);/); if (m) {const decoded = decodeString(m[0]); if (decoded !== m[0]) return [decoded,m[0].length];}}
      if (raw.slice(at,at+2) === '\r\n') return ['\n',2];
      const c = String.fromCodePoint(raw.codePointAt(at)); return [c,c.length];
    }
    while (j < value.length && i < raw.length) {
      const [word,size] = token(i);
      if (value.startsWith(word,j) || (word === '\n' && value[j] === ' ')) {
        const shown = word === '\n' && value[j] === ' ' ? ' ' : word;
        const last = result[result.length-1];
        if (size === shown.length && last && last.b === start+i && last.b-last.a === last.value.length) {last.b += size; last.value += shown;}
        else result.push({a:start+i,b:start+i+size,value:shown});
        i += size; j += shown.length;
      } else i += size; // quote/list indentation omitted by the Markdown parser
    }
    if (j < value.length) result.push({a:start+i,b:end,value:value.slice(j)});
    return result;
  }
export function innerMap(node, source) {
    let a = node.position.start.offset, b = node.position.end.offset;
    if (node.type === 'inlineCode') {const ticks = source.slice(a,b).match(/^`+/)?.[0].length || 1; a += ticks; b -= ticks;}
    if (node.type === 'code') {const m = source.slice(a,b).match(/^ {0,3}(?:`{3,}|~{3,})[^\n]*\n/); if (m) a += m[0].length;}
    return textMap(source,a,b,node.value || '');
  }
