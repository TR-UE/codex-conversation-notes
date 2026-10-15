import {fromMarkdown} from 'mdast-util-from-markdown';
import {gfm} from 'micromark-extension-gfm';
import {gfmFromMarkdown} from 'mdast-util-gfm';
import {math} from 'micromark-extension-math';
import {mathFromMarkdown} from 'mdast-util-math';
export {decodeString} from 'micromark-util-decode-string';
export {default as katex} from 'katex';
export {default as highlight} from 'highlight.js/lib/common';

const options = {extensions: [gfm(), math()], mdastExtensions: [gfmFromMarkdown(), mathFromMarkdown()]};
export function parse(source) {
  const first = fromMarkdown(source, options), protectedRanges = [];
  function walk(node) {
    if (['code','inlineCode','math','inlineMath'].includes(node.type)) protectedRanges.push([node.position.start.offset,node.position.end.offset]);
    for (const child of node.children || []) walk(child);
  }
  walk(first);
  const chars = source.split(''); let changed = false;
  for (const match of source.matchAll(/\\\[([\s\S]*?)\\\]|\\\(([\s\S]*?)\\\)/g)) {
    const a = match.index, b = a + match[0].length;
    if (protectedRanges.some(([c,d]) => a < d && b > c) || source[a-1] === '\\') continue;
    const block = match[1] !== undefined;
    chars[a] = '$'; chars[a+1] = block ? '$' : ' ';
    chars[b-2] = block ? '$' : ' '; chars[b-1] = '$'; changed = true;
  }
  return changed ? fromMarkdown(chars.join(''), options) : first;
}

export {textMap,innerMap} from './source-map.mjs';
