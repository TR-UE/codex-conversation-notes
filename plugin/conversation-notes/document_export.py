"""Document exporters executed with the configured bundled artifact runtime."""
from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
from xml.sax.saxutils import escape

PALETTE = {'yellow': 'F9E998', 'rose': 'F6CBD1', 'green': 'C5E4C8', 'blue': 'C8DDF6', 'purple': 'DDCFF6'}
ROLES = {'user': '你', 'assistant': 'Codex', 'excerpt': '原文摘录'}


def clean(value):
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value))


def segments(body, notes):
    encoded = body.encode('utf-16-le')
    bounds = sorted({0, len(encoded) // 2, *(n[k] for n in notes for k in ('start', 'end'))})
    for start, end in zip(bounds, bounds[1:]):
        active = [n for n in notes if n['start'] <= start and n['end'] >= end]
        yield start, end, encoded[start * 2:end * 2].decode('utf-16-le'), active


def ordered_notes(item):
    order = {m['id']: i for i, m in enumerate(item['messages'])}
    return sorted(item['annotations'], key=lambda n: (order[n['message_id']], n['start'], n['end'], n['created_at'], n['id']))


def comment_text(note):
    value = note['comment'] or '仅高亮'
    if note['tags']:
        value += '\n标签：' + '、'.join(note['tags'])
    return clean(value)


def display_message(message, notes):
    body = message['body']
    # Ambient browser metadata is not part of the human's message. Preserve any
    # explicitly annotated prefix, otherwise omit it and translate UTF-16 ranges.
    prefix = re.match(r'\s*<in-app-browser-context\b.*?</in-app-browser-context>\s*## My request:\s*', body, re.S)
    if prefix and not message.get('edited'):
        offset = len(body[:prefix.end()].encode('utf-16-le')) // 2
        if all(n['start'] >= offset for n in notes):
            body = body[prefix.end():]
            notes = [{**n, 'start': n['start'] - offset, 'end': n['end'] - offset} for n in notes]
    return body, notes


def docx_bytes(documents, appearance=None):
    from rich_document import docx_bytes as build
    return build(documents, appearance)


def graphemes(value):
    """Keep common emoji ZWJ sequences, selectors, modifiers, and flags together."""
    result = []
    for char in value:
        code = ord(char)
        joining = (unicodedata.combining(char) or char in ('\ufe0f', '\ufe0e', '\u200d') or 0x1F3FB <= code <= 0x1F3FF)
        flag_pair = (0x1F1E6 <= code <= 0x1F1FF and result and len(result[-1]) == 1 and 0x1F1E6 <= ord(result[-1]) <= 0x1F1FF)
        if result and (joining or result[-1].endswith('\u200d') or flag_pair):
            result[-1] += char
        else:
            result.append(char)
    return result


def pdf_bytes(documents, appearance=None):
    from rich_document import pdf_from_docx
    return pdf_from_docx(docx_bytes(documents, appearance))


def export(documents, format, appearance=None):
    if not documents:
        raise ValueError('请先选择一份原文再导出')
    if format not in ('pdf', 'docx'):
        raise ValueError('不支持的文档格式')
    from rich_document import build,pdf_from_docx
    word,warnings=build(documents,appearance)
    value=pdf_from_docx(word) if format=='pdf' else word
    name = documents[0]['document']['title'] if len(documents) == 1 else '墨记对话批注'
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip(' .')[:100] or '墨记对话批注'
    mime = 'application/pdf' if format == 'pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    return {'filename': name + '.' + format, 'mime': mime, 'blob': base64.b64encode(value).decode('ascii'), **({'warnings':warnings} if warnings else {})}


if __name__ == '__main__':
    try:
        payload = json.load(sys.stdin)
        print(json.dumps(export(payload['documents'], payload['format'], payload.get('appearance')), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False)); sys.exit(1)
