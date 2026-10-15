#!/usr/bin/env python3
"""Local-only MCP app and SQLite store for conversation annotations (stdlib only)."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import uuid
import base64
from content_edits import projection, original_range, project_note, merge, length
from media_store import references, save_image, load_image, content_images, remote_image
import time
from edit_anchors import reanchor

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('CONVERSATION_NOTES_DATA', str(Path.home() / '.codex/conversation-notes')))
COLORS = ('yellow', 'rose', 'green', 'blue', 'purple')
UI_URI = 'ui://conversation-notes/reader-v6.html'
UUID_RE = re.compile(r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def uid():
    return str(uuid.uuid4())


def text(value, label='文字', maximum=2000000, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError(f'{label}不能为空且不能超过 {maximum} 字符')
    return value


def utf16_slice(value, start, end):
    if type(start) is not int or type(end) is not int or start < 0 or end <= start:
        raise ValueError('请选择有效的文字范围')
    encoded = value.encode('utf-16-le')
    if end * 2 > len(encoded):
        raise ValueError('文字范围超出原文')
    try:
        return encoded[start * 2:end * 2].decode('utf-16-le')
    except UnicodeDecodeError:
        raise ValueError('选择范围不能拆开一个字符') from None


class Store:
    def __init__(self, directory=DATA):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'notes.sqlite3'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            if self.path.stat().st_size and db.execute('PRAGMA user_version').fetchone()[0] == 1:
                backup_path = self.directory / 'before-0.3.0.sqlite3'
                if not backup_path.exists():
                    with sqlite3.connect(backup_path) as backup:
                        db.backup(backup)
                    os.chmod(backup_path, 0o600)
            db.executescript('''
            CREATE TABLE IF NOT EXISTS documents (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, thread_id TEXT NOT NULL DEFAULT '',
              source_kind TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
              role TEXT NOT NULL, body TEXT NOT NULL, source_id TEXT NOT NULL DEFAULT '',
              ordinal INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS annotations (
              id TEXT PRIMARY KEY, message_id TEXT NOT NULL REFERENCES messages(id),
              start INTEGER NOT NULL, end INTEGER NOT NULL, quote TEXT NOT NULL,
              color TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '[]',
              version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              deleted_at TEXT);
            CREATE INDEX IF NOT EXISTS annotation_message ON annotations(message_id);
            CREATE INDEX IF NOT EXISTS message_document ON messages(document_id);
            CREATE TABLE IF NOT EXISTS deletion_history (
              id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
              label TEXT NOT NULL, snapshot TEXT NOT NULL, created_at TEXT NOT NULL, undone_at TEXT);
            CREATE TABLE IF NOT EXISTS preferences (
              key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
            ''')
            for table, column, definition in (
                ('documents', 'deleted_at', 'TEXT'),
                ('documents', 'revision', 'INTEGER NOT NULL DEFAULT 1'),
                ('messages', 'deleted_at', 'TEXT'),
                ('messages', 'cuts', "TEXT NOT NULL DEFAULT '[]'"),
                ('messages', 'attachments', "TEXT NOT NULL DEFAULT '[]'"),
                ('messages', 'source_body', 'TEXT'),
                ('messages', 'edited', 'INTEGER NOT NULL DEFAULT 0'),
                ('messages', 'version', 'INTEGER NOT NULL DEFAULT 1')):
                if column not in {r['name'] for r in db.execute(f'PRAGMA table_info({table})')}:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
            db.execute('PRAGMA user_version=2')
            db.execute('''CREATE TABLE IF NOT EXISTS image_cache (
              message_id TEXT NOT NULL REFERENCES messages(id), reference TEXT NOT NULL,
              metadata TEXT NOT NULL, PRIMARY KEY(message_id,reference))''')
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def library(self, **_):
        with self.connect() as db:
            docs = [dict(r) for r in db.execute('''SELECT d.*,
              (SELECT count(*) FROM messages m WHERE m.document_id=d.id AND m.deleted_at IS NULL) AS message_count,
              (SELECT count(*) FROM annotations a JOIN messages m ON m.id=a.message_id
               WHERE m.document_id=d.id AND m.deleted_at IS NULL AND a.deleted_at IS NULL) AS note_count
              FROM documents d WHERE d.deleted_at IS NULL ORDER BY d.updated_at DESC, d.id''')]
            trash = [dict(r) for r in db.execute('SELECT id,title FROM documents WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC')]
        return {'documents': docs, 'trash': trash}

    def document(self, document_id, backup=False, **_):
        with self.connect() as db:
            row = db.execute('SELECT * FROM documents WHERE id=?', (document_id,)).fetchone()
            if not row or (row['deleted_at'] and not backup):
                raise ValueError('找不到这份原文，请重新选择')
            messages = [dict(r) for r in db.execute('SELECT * FROM messages WHERE document_id=? ORDER BY ordinal', (document_id,))]
            notes = [dict(r) for r in db.execute('''SELECT a.* FROM annotations a JOIN messages m
              ON a.message_id=m.id WHERE m.document_id=? ORDER BY a.created_at,a.id''', (document_id,))]
            history = [dict(r) for r in db.execute('SELECT * FROM deletion_history WHERE document_id=? ORDER BY rowid', (document_id,))]
            cached = {m['id']: [dict(r) for r in db.execute('SELECT reference,metadata FROM image_cache WHERE message_id=?', (m['id'],))] for m in messages} if backup else {}
        for n in notes:
            n['tags'] = json.loads(n['tags'])
        for m in messages:
            m['attachments'] = json.loads(m['attachments'])
        if backup:
            for m in messages:
                m['attachments'] = [self._backup_image(a) for a in m['attachments']]
                m['resources'] = [{'reference': r['reference'], **self._backup_image(json.loads(r['metadata']))} for r in cached[m['id']]]
            return {'document': dict(row), 'messages': messages, 'annotations': notes, 'history': history}
        projected, visible_notes = [], []
        for message in messages:
            if message['deleted_at']:
                continue
            body, view_ranges = projection(message)
            if not body.strip() and not message['attachments'] and not message['edited']:
                continue
            projected.append({k: v for k, v in {**message, 'body': body}.items() if k != 'cuts'})
            for note in notes:
                if note['message_id'] == message['id'] and not note['deleted_at']:
                    value = project_note(note, body, view_ranges)
                    if value:
                        visible_notes.append(value)
        actions = [{'id': h['id'], 'label': h['label']} for h in history if not h['undone_at']]
        return {'document': dict(row), 'messages': projected, 'annotations': visible_notes,
                'undo': actions[-1] if actions else None}

    def import_text(self, title, body, thread_id='', **_):
        text(title, '标题', 200)
        text(body, '原文')
        if thread_id and not UUID_RE.fullmatch(thread_id):
            raise ValueError('聊天编号格式不正确')
        return self._insert(title, [{'role': 'excerpt', 'body': body}], thread_id, 'paste')

    def rename_document(self, document_id, title, expected_revision, **_):
        title = text(title, '标题', 200).strip()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._editable(db,document_id,expected_revision)
            db.execute('UPDATE documents SET title=?,updated_at=?,revision=revision+1 WHERE id=?',(title,now(),document_id))
        return self.document(document_id)

    def _insert(self, title, messages, thread_id='', source_kind='paste'):
        doc_id, stamp = uid(), now()
        with self.connect() as db:
            db.execute('INSERT INTO documents (id,title,thread_id,source_kind,created_at,updated_at) VALUES (?,?,?,?,?,?)', (doc_id, title.strip(), thread_id, source_kind, stamp, stamp))
            for i, m in enumerate(messages):
                mid = uid()
                db.execute('INSERT INTO messages (id,document_id,role,body,source_id,ordinal) VALUES (?,?,?,?,?,?)', (mid, doc_id, m['role'], m['body'], m.get('source_id', ''), i))
                self._sync_images(db, mid, m.get('images', []))
                self._cache_images(db, mid, m['body'])
        return self.document(doc_id)

    def _sync_images(self, db, message_id, values):
        if not values: return False
        old = json.loads(db.execute('SELECT attachments FROM messages WHERE id=?', (message_id,)).fetchone()[0])
        keys = {a['key'] for a in old}
        added = False
        for value in values:
            key = hashlib.sha256(value.encode()).hexdigest()
            if key in keys: continue
            if value.startswith(('https://', 'http://')):
                meta = {'url': value, 'filename': '远程图片'}
            else:
                try: meta = save_image(self.directory, value)
                except ValueError as error: meta = {'error': str(error), 'filename': '图片'}
            old.append({**meta, 'key': key}); keys.add(key); added = True
        if added:
            db.execute('UPDATE messages SET attachments=? WHERE id=?', (json.dumps(old), message_id))
        return added

    def _backup_image(self, item):
        if item.get('asset'):
            try: return {**item, **load_image(self.directory, item)}
            except ValueError: pass
        return item

    def _cache_images(self, db, message_id, body):
        for reference in references(body):
            if reference.startswith(('http://','https://')): continue
            if db.execute('SELECT 1 FROM image_cache WHERE message_id=? AND reference=?', (message_id,reference)).fetchone(): continue
            try: meta = save_image(self.directory,reference)
            except ValueError: continue
            db.execute('INSERT INTO image_cache VALUES (?,?,?)',(message_id,reference,json.dumps(meta)))

    def get_message_image(self, message_id, reference, **_):
        with self.connect() as db:
            row = db.execute('SELECT m.* FROM messages m JOIN documents d ON d.id=m.document_id WHERE m.id=? AND m.deleted_at IS NULL AND d.deleted_at IS NULL', (message_id,)).fetchone()
            if not row: raise ValueError('找不到这段原文')
            attachments = json.loads(row['attachments'])
            if reference.startswith('attachment:'):
                item = next((a for a in attachments if a['key'] == reference[11:]), None)
                if not item: raise ValueError('图片不存在')
                if item.get('error'): raise ValueError(item['error'])
                return load_image(self.directory, item)
            # A caller cannot supply arbitrary local paths: the exact image
            # destination must already be in this message's visible Markdown.
            body, _ = projection(dict(row))
            if reference not in references(body): raise ValueError('图片不在这段原文中')
            cached = db.execute('SELECT metadata FROM image_cache WHERE message_id=? AND reference=?', (message_id, reference)).fetchone()
            if cached: return load_image(self.directory, json.loads(cached['metadata']))
            if reference.startswith(('http://', 'https://')): raise ValueError('请在界面中点击加载远程图片')
            meta = save_image(self.directory, reference)
            db.execute('INSERT OR IGNORE INTO image_cache VALUES (?,?,?)', (message_id, reference, json.dumps(meta)))
        return load_image(self.directory, meta)

    def import_thread(self, thread_id, title='这段对话', **_):
        """Only read the exact locally stored chat that the user explicitly selects."""
        if not isinstance(thread_id, str) or not UUID_RE.fullmatch(thread_id):
            raise ValueError('请输入完整的本机聊天编号')
        text(title, '标题', 200)
        session_root = Path.home() / '.codex/sessions'
        matches = list(session_root.glob('**/*' + thread_id + '.jsonl'))
        if len(matches) != 1:
            raise ValueError('未找到这段本机对话。云端对话请复制需要标记的原文导入。')
        messages, seen = [], set()
        with matches[0].open() as file:
            for line in file:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue  # A live session may end with a partial JSONL record.
                p = row.get('payload', {})
                if row.get('type') != 'response_item' or p.get('type') != 'message':
                    continue
                role = p.get('role')
                if role not in ('user', 'assistant'):
                    continue
                if role == 'assistant' and p.get('phase') not in (None, 'final_answer'):
                    continue
                parts = []
                for c in p.get('content', []):
                    if c.get('type') not in ('input_text', 'output_text'):
                        continue
                    value = c.get('text', '')
                    if role == 'user' and value.lstrip().startswith((
                        '# AGENTS.md instructions', '<environment_context>',
                        '<external_codex_apps_', '<system-reminder>', '<permissions instructions>')):
                        continue
                    parts.append(value)
                body = '\n'.join(parts).strip()
                images = content_images(p.get('content', []))
                if not body and not images:
                    continue
                source_id = p.get('id') or hashlib.sha256((role + (body or '\n'.join(images))).encode()).hexdigest()
                if source_id in seen:
                    continue
                seen.add(source_id)
                messages.append({'role': role, 'body': body, 'source_id': source_id, 'images': images})
        if not messages:
            raise ValueError('没有找到可导入的对话正文，请使用复制导入')
        with self.connect() as db:
            old = db.execute("SELECT id,deleted_at FROM documents WHERE thread_id=? AND source_kind='local-chat' ORDER BY created_at LIMIT 1", (thread_id,)).fetchone()
            if old:
                if old['deleted_at']:
                    raise ValueError('这段对话已在墨记中删除，请先从回收站恢复')
                old_ids = {r['source_id']: r['id'] for r in db.execute('SELECT id,source_id FROM messages WHERE document_id=?', (old['id'],))}
                ordinal = db.execute('SELECT coalesce(max(ordinal),-1)+1 FROM messages WHERE document_id=?', (old['id'],)).fetchone()[0]
                first_ordinal = ordinal
                media_added = False
                for m in messages:
                    if m['source_id'] not in old_ids:
                        mid = uid()
                        db.execute('INSERT INTO messages (id,document_id,role,body,source_id,ordinal) VALUES (?,?,?,?,?,?)', (mid, old['id'], m['role'], m['body'], m['source_id'], ordinal))
                        ordinal += 1
                        old_ids[m['source_id']] = mid
                    media_added = self._sync_images(db, old_ids[m['source_id']], m['images']) or media_added
                    self._cache_images(db,old_ids[m['source_id']],m['body'])
                if ordinal > first_ordinal or media_added:
                    db.execute('UPDATE documents SET updated_at=?,revision=revision+1 WHERE id=?', (now(), old['id']))
                doc_id = old['id']
            else:
                doc_id = None
        return self.document(doc_id) if doc_id else self._insert(title, messages, thread_id, 'local-chat')

    def add_annotation(self, message_id, start, end, quote, color='yellow', comment='', tags=None, expected_version=None, **_):
        if color not in COLORS:
            raise ValueError('不支持的高亮颜色')
        text(comment, '批注', 50000, empty=True)
        tags = self._tags(tags or [])
        with self.connect() as db:
            m = db.execute('SELECT * FROM messages WHERE id=?', (message_id,)).fetchone()
            if not m or m['deleted_at']:
                raise ValueError('这段原文已删除，请刷新后重试')
            if expected_version is not None and m['version'] != expected_version:
                raise ValueError('原文已在其他面板更新，请刷新后重新选择')
            body, ranges = projection(dict(m))
            if utf16_slice(body, start, end) != quote:
                raise ValueError('选中文字与原文不一致，请重新选择')
            start, end = original_range(ranges, start, end)
            quote = utf16_slice(m['body'], start, end)
            note_id, stamp = uid(), now()
            db.execute('''INSERT INTO annotations
              (id,message_id,start,end,quote,color,comment,tags,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?,?,?)''', (note_id, message_id, start, end, quote, color, comment, json.dumps(tags), stamp, stamp))
            db.execute('UPDATE documents SET updated_at=? WHERE id=?', (stamp, m['document_id']))
        return {'annotation': self._visible_note(note_id)}

    @staticmethod
    def _tags(tags):
        if not isinstance(tags, list) or len(tags) > 20:
            raise ValueError('标签最多 20 个')
        return list(dict.fromkeys(text(t, '标签', 40).strip() for t in tags))

    def _note(self, annotation_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM annotations WHERE id=?', (annotation_id,)).fetchone()
        if not row:
            raise ValueError('批注不存在')
        result = dict(row)
        result['tags'] = json.loads(result['tags'])
        return result

    def _visible_note(self, annotation_id):
        note = self._note(annotation_id)
        with self.connect() as db:
            message = dict(db.execute('SELECT * FROM messages WHERE id=?', (note['message_id'],)).fetchone())
        body, ranges = projection(message)
        return project_note(note, body, ranges) or note

    def update_annotation(self, annotation_id, expected_version, comment=None, color=None, tags=None, deleted=None, **_):
        if type(expected_version) is not int:
            raise ValueError('缺少批注版本，请刷新后重试')
        fields, args = [], []
        if comment is not None:
            fields.append('comment=?'); args.append(text(comment, '批注', 50000, empty=True))
        if color is not None:
            if color not in COLORS:
                raise ValueError('不支持的高亮颜色')
            fields.append('color=?'); args.append(color)
        if tags is not None:
            fields.append('tags=?'); args.append(json.dumps(self._tags(tags)))
        if deleted is not None:
            if type(deleted) is not bool:
                raise ValueError('无效的删除状态')
            fields.append('deleted_at=?'); args.append(now() if deleted else None)
        stamp = now()
        fields.extend(['updated_at=?', 'version=version+1']); args.append(stamp)
        args.extend([annotation_id, expected_version])
        with self.connect() as db:
            result = db.execute('UPDATE annotations SET ' + ','.join(fields) + ' WHERE id=? AND version=?', args)
            if not result.rowcount:
                raise ValueError('这条批注已在其他面板更新。请刷新后重试；未保存文字仍留在输入框中。')
            db.execute('''UPDATE documents SET updated_at=? WHERE id=(SELECT m.document_id FROM messages m
              JOIN annotations a ON a.message_id=m.id WHERE a.id=?)''', (stamp, annotation_id))
        return {'annotation': self._visible_note(annotation_id)}

    @staticmethod
    def _editable(db, document_id, expected_revision):
        row = db.execute('SELECT * FROM documents WHERE id=?', (document_id,)).fetchone()
        if not row or row['deleted_at']:
            raise ValueError('这份原文已删除，请刷新后重试')
        if type(expected_revision) is not int or row['revision'] != expected_revision:
            raise ValueError('原文已在其他面板更新，请刷新后重试')
        return row

    @staticmethod
    def _history(db, document_id, label, messages=(), document_deleted_at=None, edit_stamp=None):
        snapshot = {'document_deleted_at': document_deleted_at, 'messages': [
            {k: m[k] for k in ('id', 'body', 'source_body', 'edited', 'cuts', 'deleted_at')} for m in messages]}
        if edit_stamp:
            snapshot['edit_stamp'] = edit_stamp
            snapshot['anchors'] = [dict(n) for m in messages for n in db.execute('SELECT id,message_id,start,end,quote,deleted_at FROM annotations WHERE message_id=?', (m['id'],))]
        action_id = uid()
        db.execute('INSERT INTO deletion_history VALUES (?,?,?,?,?,NULL)',
                   (action_id, document_id, label, json.dumps(snapshot), now()))
        db.execute('UPDATE documents SET updated_at=?,revision=revision+1 WHERE id=?', (now(), document_id))
        return action_id

    def delete_text(self, message_id, start, end, quote, expected_version, ranges=None, **_):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT m.* FROM messages m JOIN documents d ON d.id=m.document_id '
                             'WHERE m.id=? AND d.deleted_at IS NULL AND m.deleted_at IS NULL', (message_id,)).fetchone()
            if not row or type(expected_version) is not int or row['version'] != expected_version:
                raise ValueError('原文已变化，请刷新后重新选择')
            message = dict(row)
            body, view_ranges = projection(message)
            if utf16_slice(body, start, end) != quote:
                raise ValueError('选中文字与原文不一致，请重新选择')
            requested = ranges if ranges is not None else [[start,end]]
            if not isinstance(requested,list) or not requested or len(requested)>10000:
                raise ValueError('选区无效，请重新选择')
            added = []
            for pair in requested:
                if not isinstance(pair,list) or len(pair)!=2 or pair[0]<start or pair[1]>end:
                    raise ValueError('删减范围超出选区')
                utf16_slice(body,*pair)
                added.append(list(original_range(view_ranges,*pair)))
            cuts = json.dumps(merge(json.loads(message['cuts']) + added))
            self._history(db, message['document_id'], '删除选中文字', [message])
            db.execute('UPDATE messages SET cuts=?,version=version+1,deleted_at=? WHERE id=?',
                       (cuts, now() if merge(requested) == [[0,length(body)]] else None, message_id))
        return self.document(message['document_id'])

    def edit_message(self, message_id, body, expected_version, **_):
        text(body, '消息正文', empty=True)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT m.* FROM messages m JOIN documents d ON d.id=m.document_id WHERE m.id=? AND m.deleted_at IS NULL AND d.deleted_at IS NULL', (message_id,)).fetchone()
            if not row or type(expected_version) is not int or row['version'] != expected_version:
                raise ValueError('这段消息已在其他面板修改。你的文字保留在编辑框中，请先复制后刷新核对。')
            before = dict(row)
            current, _ = projection(before)
            if body == current: return self.document(before['document_id'])
            stamp = now()
            self._history(db,before['document_id'],'编辑消息',[before],edit_stamp=stamp)
            after = {**before,'body':body,'cuts':'[]','edited':1}
            notes = [dict(n) for n in db.execute('SELECT * FROM annotations WHERE message_id=?', (message_id,))]
            for n in reanchor(before,after,notes,stamp):
                db.execute('UPDATE annotations SET start=?,end=?,quote=?,deleted_at=?,version=version+1,updated_at=? WHERE id=?', (n['start'],n['end'],n['quote'],n['deleted_at'],stamp,n['id']))
            db.execute('UPDATE messages SET source_body=coalesce(source_body,body),body=?,cuts=\'[]\',edited=1,version=version+1 WHERE id=?', (body,message_id))
            self._cache_images(db,message_id,body)
        return self.document(before['document_id'])

    def delete_messages(self, document_id, message_ids, expected_revision, **_):
        if not isinstance(message_ids, list) or not message_ids or len(message_ids) > 10000 or any(not isinstance(m, str) for m in message_ids):
            raise ValueError('请先选择需要删除的消息')
        ids = set(message_ids)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._editable(db, document_id, expected_revision)
            messages = [dict(r) for r in db.execute('SELECT * FROM messages WHERE document_id=? AND deleted_at IS NULL', (document_id,)) if r['id'] in ids]
            if len(messages) != len(ids):
                raise ValueError('部分消息已变化，请刷新后重试')
            self._history(db, document_id, f'删除 {len(messages)} 段消息', messages)
            db.executemany('UPDATE messages SET deleted_at=?,version=version+1 WHERE id=?', [(now(), m['id']) for m in messages])
        return self.document(document_id)

    def delete_document(self, document_id, expected_revision, **_):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._editable(db, document_id, expected_revision)
            action_id = self._history(db, document_id, '删除整份对话')
            db.execute('UPDATE documents SET deleted_at=? WHERE id=?', (now(), document_id))
        return {**self.library(), 'undo': {'id': action_id, 'label': '删除整份对话'}}

    def undo_delete(self, action_id, **_):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            action = db.execute('SELECT * FROM deletion_history WHERE id=? AND undone_at IS NULL', (action_id,)).fetchone()
            if not action:
                raise ValueError('这次操作已撤销，请刷新后查看')
            latest = db.execute('SELECT id FROM deletion_history WHERE document_id=? AND undone_at IS NULL ORDER BY rowid DESC LIMIT 1', (action['document_id'],)).fetchone()
            if latest['id'] != action_id:
                raise ValueError('请先撤销这份原文中较新的操作')
            snapshot = json.loads(action['snapshot'])
            for message in snapshot['messages']:
                current = dict(db.execute('SELECT * FROM messages WHERE id=?', (message['id'],)).fetchone())
                if 'body' in message:
                    if snapshot.get('edit_stamp'):
                        old_ids = {n['id'] for n in snapshot.get('anchors', [])}
                        newer = [dict(n) for n in db.execute('SELECT * FROM annotations WHERE message_id=?', (message['id'],)) if n['id'] not in old_ids]
                        for n in reanchor(current,{**current,**message},newer,now()):
                            db.execute('UPDATE annotations SET start=?,end=?,quote=?,deleted_at=?,version=version+1,updated_at=? WHERE id=?', (n['start'],n['end'],n['quote'],n['deleted_at'],now(),n['id']))
                    db.execute('UPDATE messages SET body=?,source_body=?,edited=? WHERE id=?', (message['body'],message.get('source_body'),message.get('edited',0),message['id']))
                db.execute('UPDATE messages SET cuts=?,deleted_at=?,version=version+1 WHERE id=? AND document_id=?',
                           (message['cuts'], message['deleted_at'], message['id'], action['document_id']))
            for n in snapshot.get('anchors', []):
                current = db.execute('SELECT deleted_at,updated_at FROM annotations WHERE id=?',(n['id'],)).fetchone()
                if current:
                    deleted = n['deleted_at'] if current['deleted_at'] == snapshot['edit_stamp'] else current['deleted_at']
                    db.execute('UPDATE annotations SET start=?,end=?,quote=?,deleted_at=?,version=version+1,updated_at=? WHERE id=?', (n['start'],n['end'],n['quote'],deleted,now(),n['id']))
            db.execute('UPDATE documents SET deleted_at=?,updated_at=?,revision=revision+1 WHERE id=?',
                       (snapshot['document_deleted_at'], now(), action['document_id']))
            db.execute('UPDATE deletion_history SET undone_at=? WHERE id=?', (now(), action_id))
        return {**self.library(), **self.document(action['document_id'])}

    def restore_document(self, document_id, **_):
        with self.connect() as db:
            row = db.execute('SELECT deleted_at FROM documents WHERE id=?', (document_id,)).fetchone()
            action = db.execute('SELECT id FROM deletion_history WHERE document_id=? AND undone_at IS NULL ORDER BY rowid DESC LIMIT 1', (document_id,)).fetchone()
        if not row or not row['deleted_at'] or not action:
            raise ValueError('这份对话已恢复，请重新选择')
        return self.undo_delete(action['id'])

    def export_notes(self, format='json', document_id=None, **_):
        docs = [self.document(document_id)] if document_id else [self.document(d['id']) for d in self.library()['documents']]
        if format in ('pdf', 'docx'):
            config = ROOT / 'export-runtime.json'
            if not config.is_file():
                raise ValueError('未配置 PDF 和 Word 导出环境，请重新安装墨记。')
            runtime = json.loads(config.read_text())['python']
            if not Path(runtime).is_file():
                raise ValueError('文档导出运行环境已移动，请重新配置墨记的导出环境。')
            warnings = self._export_images(docs)
            try:
                result = subprocess.run([runtime, str(ROOT / 'document_export.py')],
                    input=json.dumps({'documents': docs, 'format': format, 'appearance': self.get_appearance()['appearance']}, ensure_ascii=False),
                    capture_output=True, text=True, timeout=180)
            except subprocess.TimeoutExpired:
                raise ValueError('文档较长，导出超时，请稍后重试或先分段导入。') from None
            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError:
                raise ValueError('文档导出失败，请检查本机文档运行环境。') from None
            if result.returncode or data.get('error'):
                raise ValueError(data.get('error') or '文档导出失败')
            if warnings: data['warnings'] = list(dict.fromkeys([*data.get('warnings',[]),*warnings]))
            return data
        if format == 'json':
            with self.connect() as db:
                ids = [document_id] if document_id else [r['id'] for r in db.execute('SELECT id FROM documents ORDER BY created_at,id')]
            full_docs = [self.document(i, backup=True) for i in ids]
            content = json.dumps({'schema': 'conversation-notes/v3', 'exported_at': now(), 'documents': full_docs}, ensure_ascii=False, indent=2)
            return {'filename': '墨记备份.json', 'mime': 'application/json', 'content': content}
        if format != 'markdown':
            raise ValueError('不支持的导出格式')
        blocks = ['# 墨记 · 对话批注\n']
        for d in docs:
            blocks.append('## ' + d['document']['title'] + '\n')
            if d['document']['thread_id']:
                blocks.append('[返回原聊天](codex://threads/' + d['document']['thread_id'] + ')\n')
            for m in d['messages']:
                for n in [a for a in d['annotations'] if a['message_id'] == m['id']]:
                    blocks.append('> ' + n['quote'].replace('\n', '\n> ') + '\n\n' + (n['comment'] or '（仅高亮）') + '\n')
                    if n['tags']:
                        blocks.append('标签：' + '、'.join(n['tags']) + '\n')
        return {'filename': '墨记批注.md', 'mime': 'text/markdown', 'content': '\n'.join(blocks)}

    def _export_images(self, docs):
        warnings=[];deadline=time.monotonic()+30;total=0
        config=json.loads((ROOT/'export-runtime.json').read_text())
        if not config.get('node') or not Path(config['node']).is_file():raise ValueError('富格式导出环境未配置，请更新墨记')
        manifest=subprocess.run([config['node'],str(ROOT/'export/export-engine.mjs'),str(ROOT/'export-runtime.json'),'--images'],input=json.dumps({'documents':docs},ensure_ascii=False),capture_output=True,text=True,timeout=30)
        if manifest.returncode:raise ValueError('图片引用解析失败，请检查导出环境')
        image_refs=json.loads(manifest.stdout)
        for item in docs:
            for message in item['messages']:
                resources={};message['resources']=resources
                def image(reference,filename):
                    nonlocal total
                    try:
                        if total>80000000: raise ValueError('本次图片总量较大，请分批导出')
                        try: data=self.get_message_image(message['id'],reference)
                        except ValueError:
                            if not reference.startswith(('https://','http://')): raise
                            if time.monotonic()>deadline: raise ValueError('远程图片加载超时')
                            meta=save_image(self.directory,remote_image(reference,timeout=min(5,max(.2,deadline-time.monotonic()))),filename)
                            with self.connect() as db:
                                db.execute('INSERT OR REPLACE INTO image_cache VALUES (?,?,?)',(message['id'],reference,json.dumps(meta)))
                            data=load_image(self.directory,meta)
                        total+=len(data['blob']);return data
                    except ValueError as error:
                        warnings.append(str(error));return {'error':str(error),'filename':filename}
                for reference in image_refs.get(message['id'],[]):
                    resources[reference]=image(reference,Path(urlparse(reference).path).name or '图片')
                for attachment in message.get('attachments',[]):
                    attachment.update(image(attachment.get('url') or 'attachment:'+attachment['key'],attachment.get('filename','图片')))
        return list(dict.fromkeys(warnings))

    def restore_notes(self, archive, **_):
        if isinstance(archive, str):
            text(archive, '备份', 128000000)
            archive = json.loads(archive)
        if not isinstance(archive, dict) or archive.get('schema') not in ('conversation-notes/v1', 'conversation-notes/v2', 'conversation-notes/v3'):
            raise ValueError('这不是墨记的备份文件')
        docs = archive.get('documents')
        if not isinstance(docs, list) or len(docs) > 1000:
            raise ValueError('备份最多包含 1000 份原文')
        # Restore as independent documents: never overwrite existing user annotations.
        count, stamp = 0, now()
        with self.connect() as db:
            for item in docs:
                d = item['document']; title = text(d['title'], '标题', 200)
                thread = d.get('thread_id', '')
                if thread and not UUID_RE.fullmatch(thread):
                    raise ValueError('备份中的聊天编号格式不正确')
                doc_id = uid()
                is_v2 = archive['schema'] in ('conversation-notes/v2', 'conversation-notes/v3')
                db.execute('INSERT INTO documents (id,title,thread_id,source_kind,created_at,updated_at,deleted_at) VALUES (?,?,?,?,?,?,?)',
                           (doc_id, title, thread, 'backup', stamp, stamp, d.get('deleted_at') if is_v2 else None))
                mapping, bodies, note_mapping = {}, {}, {}
                if not isinstance(item['messages'], list) or len(item['messages']) > 10000:
                    raise ValueError('备份的原文数量过多')
                for i, m in enumerate(item['messages']):
                    body = text(m['body'], '原文', empty=bool(m.get('attachments') or m.get('edited'))); message_id = uid()
                    if m['id'] in mapping:
                        raise ValueError('备份中有重复的原文编号')
                    mapping[m['id']] = message_id; bodies[m['id']] = body
                    role = m.get('role', 'excerpt')
                    if role not in ('user', 'assistant', 'excerpt'):
                        raise ValueError('备份中的消息角色无效')
                    cuts = m.get('cuts', '[]') if is_v2 else '[]'
                    self._validate_cuts(body, cuts)
                    db.execute('INSERT INTO messages (id,document_id,role,body,source_id,ordinal,cuts,deleted_at) VALUES (?,?,?,?,?,?,?,?)',
                               (message_id, doc_id, role, body, str(m.get('source_id', ''))[:200], i, cuts, m.get('deleted_at') if is_v2 else None))
                    if archive['schema'] == 'conversation-notes/v3':
                        source_body = m.get('source_body')
                        if source_body is not None: text(source_body, '原文副本', empty=True)
                        db.execute('UPDATE messages SET source_body=?,edited=? WHERE id=?', (source_body, int(bool(m.get('edited'))), message_id))
                        attachments = [self._restore_image(a) for a in m.get('attachments', [])]
                        db.execute('UPDATE messages SET attachments=? WHERE id=?', (json.dumps(attachments), message_id))
                        for resource in m.get('resources', []):
                            text(resource['reference'], '图片引用', 20000000)
                            meta = self._restore_image(resource)
                            db.execute('INSERT INTO image_cache VALUES (?,?,?)', (message_id, resource['reference'], json.dumps(meta)))
                if not isinstance(item['annotations'], list) or len(item['annotations']) > 50000:
                    raise ValueError('备份的批注数量过多')
                for n in item['annotations']:
                    if n['message_id'] not in mapping or n.get('color') not in COLORS:
                        raise ValueError('备份中的批注无效')
                    if not n.get('deleted_at') and utf16_slice(bodies[n['message_id']], n['start'], n['end']) != n['quote']:
                        raise ValueError('备份中的高亮与原文不匹配')
                    comment = text(n.get('comment', ''), '批注', 50000, empty=True)
                    tags = self._tags(n.get('tags', []))
                    note_mapping[n['id']] = uid()
                    db.execute('''INSERT INTO annotations
                      (id,message_id,start,end,quote,color,comment,tags,created_at,updated_at,deleted_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (note_mapping[n['id']], mapping[n['message_id']], n['start'], n['end'], n['quote'], n['color'], comment, json.dumps(tags), stamp, stamp, n.get('deleted_at') if is_v2 else None))
                history = item.get('history', []) if is_v2 else []
                if not isinstance(history, list) or len(history) > 100000:
                    raise ValueError('备份中的删除记录无效')
                for action in history:
                    snapshot = json.loads(action['snapshot'])
                    for m in snapshot['messages']:
                        snapshot_body = m.get('body', bodies[m['id']])
                        text(snapshot_body, '历史正文', empty=True)
                        self._validate_cuts(snapshot_body, m['cuts'])
                        m['id'] = mapping[m['id']]
                    for n in snapshot.get('anchors', []):
                        n['id'] = note_mapping[n['id']]
                        n['message_id'] = mapping[n['message_id']]
                    db.execute('INSERT INTO deletion_history VALUES (?,?,?,?,?,?)',
                               (uid(), doc_id, text(action['label'], '删除说明', 200), json.dumps(snapshot), stamp, action.get('undone_at')))
                count += 1
        return {'restored': count, **self.library()}

    def _restore_image(self, item):
        if not isinstance(item, dict): raise ValueError('备份中的图片无效')
        meta = {k: item[k] for k in ('key','url','filename','error','asset','mime') if k in item}
        if item.get('blob'):
            try: data = base64.b64decode(item['blob'], validate=True)
            except ValueError: raise ValueError('备份中的图片数据无效') from None
            meta.update(save_image(self.directory, data, item.get('filename', '图片')))
        return meta

    @staticmethod
    def _validate_cuts(body, cuts):
        values = json.loads(cuts)
        if not isinstance(values, list) or len(values) > 100000:
            raise ValueError('备份中的删减记录无效')
        for value in values:
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError('备份中的删减范围无效')
            utf16_slice(body, *value)

    def capabilities(self, **_):
        return {'native_selection': 'not-exposed-in-public-protocol',
                'native_message_jump': 'not-exposed-in-public-protocol',
                'chat_link': 'codex://threads/<thread-id>',
                'panel_selection': True, 'panel_exact_jump': True,
                'persistence': 'local SQLite', 'database': str(self.path)}

    def get_appearance(self, **_):
        with self.connect() as db:
            row = db.execute("SELECT value FROM preferences WHERE key='appearance'").fetchone()
        return {'appearance': json.loads(row['value']) if row else {'style': 'codex', 'mode': 'auto'}}

    def set_appearance(self, style, mode, **_):
        if style not in ('codex', 'paper', 'mist') or mode not in ('auto', 'light', 'dark'):
            raise ValueError('请选择支持的外观和明暗模式')
        value = {'style': style, 'mode': mode}
        with self.connect() as db:
            db.execute("INSERT INTO preferences (key,value,updated_at) VALUES ('appearance',?,?) "
                       'ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at',
                       (json.dumps(value), now()))
        return {'appearance': value}


def schema(properties=None, required=None):
    return {'type': 'object', 'properties': properties or {}, 'required': required or [], 'additionalProperties': False}


S = {'type': 'string'}
I = {'type': 'integer'}
TOOLS = [
    ('open_notes', '打开对话批注', '打开本机保存的高亮与批注面板。传入用户指定的 thread_id 可导入该段本机对话；不要扫描未指定的聊天。', schema({'thread_id': S, 'title': S}), False),
    ('list_documents', '列出原文', '列出已经导入墨记的原文标题和批注数量。', schema(), False),
    ('get_document', '读取原文批注', '读取用户选择的一份已导入原文及其高亮和批注。', schema({'document_id': S}, ['document_id']), False),
    ('rename_document', '修改对话标题', '修改用户选择的墨记对话副本标题，保留正文、高亮、批注和同步关联。', schema({'document_id':S,'title':S,'expected_revision':I}, ['document_id','title','expected_revision']), True),
    ('import_text', '加入原文', '保存用户明确选择用于批注的原文片段。', schema({'title': S, 'body': S, 'thread_id': S}, ['title', 'body']), True),
    ('import_thread', '导入指定本机对话', '只读取用户指定编号的本机 Codex 对话正文，保存为独立批注副本；同步新消息并保留用户删减和批注。', schema({'thread_id': S, 'title': S}, ['thread_id']), True),
    ('add_annotation', '添加高亮批注', '在已导入原文的指定 UTF-16 位置保存高亮和批注；必须提供匹配的原句。', schema({'message_id': S, 'start': I, 'end': I, 'quote': S, 'expected_version': I, 'color': {'enum': list(COLORS)}, 'comment': S, 'tags': {'type': 'array', 'items': S}}, ['message_id', 'start', 'end', 'quote']), True),
    ('update_annotation', '修改批注', '保存批注、颜色和标签；删除可通过 deleted=false 恢复。版本不一致时拒绝覆盖。', schema({'annotation_id': S, 'expected_version': I, 'comment': S, 'color': {'enum': list(COLORS)}, 'tags': {'type': 'array', 'items': S}, 'deleted': {'type': 'boolean'}}, ['annotation_id', 'expected_version']), True),
    ('delete_text', '删除选中文字', '从墨记副本删去选中文字，可撤销。只影响阅读与导出，不修改原聊天。', schema({'message_id': S, 'start': I, 'end': I, 'quote': S, 'ranges': {'type':'array','items':{'type':'array','items':I,'minItems':2,'maxItems':2}}, 'expected_version': I}, ['message_id', 'start', 'end', 'quote', 'expected_version']), True),
    ('edit_message', '编辑消息正文', '保存用户在墨记里修改的消息副本，并调整高亮锚点。支持撤销，不修改 Codex 原聊天。', schema({'message_id':S,'body':S,'expected_version':I}, ['message_id','body','expected_version']), True),
    ('delete_messages', '删除选中消息', '从墨记副本中删除一段或多段消息及其显示的批注，可撤销。', schema({'document_id': S, 'message_ids': {'type': 'array', 'items': S}, 'expected_revision': I}, ['document_id', 'message_ids', 'expected_revision']), True),
    ('delete_document', '删除整份对话', '将墨记中的整份对话移入回收站，可恢复。不删除原聊天。', schema({'document_id': S, 'expected_revision': I}, ['document_id', 'expected_revision']), True),
    ('undo_delete', '撤销编辑或删减', '撤销指定的最新编辑或删除操作，恢复原文、位置和批注。操作历史跨刷新保留。', schema({'action_id': S}, ['action_id']), True),
    ('restore_document', '恢复对话', '从墨记回收站恢复已删除的对话副本。', schema({'document_id': S}, ['document_id']), True),
    ('export_notes', '导出批注', '导出已保存的原文与批注为 PDF、带原生批注的 Word DOCX、JSON 备份或 Markdown。可指定 document_id 仅导出当前原文。', schema({'format': {'enum': ['json', 'markdown', 'pdf', 'docx']}, 'document_id': S}), False),
    ('restore_notes', '恢复备份', '恢复用户选择的墨记 JSON 备份为新原文，不覆盖现有批注。', schema({'archive': S}, ['archive']), True),
    ('check_capabilities', '检查批注能力', '说明原聊天选区和消息跳转的公开接口边界，以及本机保存位置。', schema(), False),
    ('get_appearance', '读取墨记外观', '读取本机保存的阅读风格和明暗偏好。', schema(), False),
    ('set_appearance', '设置墨记外观', '保存用户选择的阅读风格与明暗模式，不改动原文和批注。', schema({'style': {'enum': ['codex', 'paper', 'mist']}, 'mode': {'enum': ['auto', 'light', 'dark']}}, ['style', 'mode']), True),
    ('get_message_image', '读取原文图片', '读取已导入消息中明确引用的本机图片或保存的图片附件。只返回图片，不接受任意文件路径。', schema({'message_id': S, 'reference': S}, ['message_id', 'reference']), False),
]


def tool_definitions():
    result = []
    for name, title, description, input_schema, write in TOOLS:
        tool = {'name': name, 'title': title, 'description': description, 'inputSchema': input_schema,
                'annotations': {'readOnlyHint': not write, 'destructiveHint': False, 'openWorldHint': False}}
        if name == 'open_notes':
            # thread_id optionally imports, so the tool is conservatively labeled a write.
            tool['annotations']['readOnlyHint'] = False
            tool['_meta'] = {'ui': {'resourceUri': UI_URI, 'visibility': ['model', 'app']},
                             'openai/ui': {'entrypoints': [{'type': 'thread'}]},
                             'openai/outputTemplate': UI_URI}
        else:
            tool['_meta'] = {'ui': {'visibility': ['model', 'app']}}
        result.append(tool)
    return result


def call_tool(store, name, arguments):
    definition = next((t for t in TOOLS if t[0] == name), None)
    if not definition or not isinstance(arguments, dict):
        raise ValueError('无效的操作')
    allowed = definition[3]['properties']
    if set(arguments) - set(allowed) or any(x not in arguments for x in definition[3]['required']):
        raise ValueError('操作参数不完整或包含未知字段')
    if name == 'open_notes':
        data = store.import_thread(arguments['thread_id'], arguments.get('title', '这段对话')) if arguments.get('thread_id') else {}
        return {**store.library(), **data}
    methods = {'list_documents': store.library, 'get_document': store.document, 'check_capabilities': store.capabilities}
    method = methods.get(name) or getattr(store, name)
    return method(**arguments)


def html(local_token=''):
    content = (ROOT / 'web/index.html').read_text()
    styles = '\n'.join((ROOT / f).read_text() for f in ('web/style.css','web/themes.css','web/rich-text.css','web/vendor/katex.css'))
    content = content.replace('/*__CSS__*/', styles)
    for marker, name in [('/*__VENDOR__*/','web/vendor/rich-vendor.js'),('/*__RICH__*/','web/rich-text.js'),('/*__JS__*/','web/app.js')]:
        content = content.replace(marker, (ROOT/name).read_text().replace('</script', '<\\/script'))
    return content.replace('__LOCAL_TOKEN__', local_token)


def mcp_result(data):
    if 'blob' in data:
        summary = {key: data[key] for key in ('filename', 'mime')}
        return {'content': [{'type': 'text', 'text': '文档已生成，可在墨记面板下载。'}],
                'structuredContent': summary, '_meta': {'download': data}}
    return {'content': [{'type': 'text', 'text': json.dumps(data, ensure_ascii=False)}], 'structuredContent': data}


def serve_stdio(store):
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            if 'id' not in request:
                continue
            method, params = request.get('method'), request.get('params') or {}
            if method == 'initialize':
                result = {'protocolVersion': params.get('protocolVersion', '2025-11-25'),
                          'capabilities': {'tools': {}, 'resources': {}},
                          'serverInfo': {'name': 'conversation-notes', 'version': '0.6.1'}}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': tool_definitions()}
            elif method == 'tools/call':
                try:
                    result = mcp_result(call_tool(store, params.get('name'), params.get('arguments', {})))
                except (ValueError, KeyError, TypeError, sqlite3.Error) as error:
                    result = {'isError': True, 'content': [{'type': 'text', 'text': str(error)}]}
            elif method == 'resources/list':
                result = {'resources': [{'uri': UI_URI, 'name': '墨记 · 对话批注', 'mimeType': 'text/html;profile=mcp-app'}]}
            elif method == 'resources/templates/list':
                result = {'resourceTemplates': []}
            elif method == 'resources/read' and params.get('uri') == UI_URI:
                result = {'contents': [{'uri': UI_URI, 'mimeType': 'text/html;profile=mcp-app', 'text': html(),
                                       '_meta': {'ui': {'prefersBorder': False, 'csp': {'connectDomains': [], 'resourceDomains': []}},
                                                 'openai/ui': {'preferredDisplayMode': 'fullscreen', 'availableDisplayModes': ['fullscreen', 'inline']}}}]}
            else:
                print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'error': {'code': -32601, 'message': 'Method not found'}}), flush=True)
                continue
            print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}, ensure_ascii=False), flush=True)
        except Exception as error:
            print(str(error), file=sys.stderr)
            if 'id' in request:
                print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'error': {'code': -32603, 'message': 'Request failed'}}), flush=True)


def serve_web(store, port):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, code, data, mime='application/json; charset=utf-8'):
            body = data.encode() if isinstance(data, str) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data: https: http:; font-src data:; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def valid_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def do_GET(self):
            if not self.valid_host():
                return self.respond(403, {'error': 'Invalid host'})
            if urlparse(self.path).path == '/':
                return self.respond(200, html(token), 'text/html; charset=utf-8')
            if self.path == '/health':
                return self.respond(200, {'ok': True, 'app': 'conversation-notes'})
            self.respond(404, {'error': 'Not found'})

        def do_POST(self):
            origin = self.headers.get('Origin')
            expected_origin = f'http://{self.headers.get("Host")}'
            if not self.valid_host() or (origin and origin != expected_origin) or not secrets.compare_digest(self.headers.get('X-Notes-Token', ''), token):
                return self.respond(403, {'error': '请求来源无效'})
            if self.path != '/api/tool':
                return self.respond(404, {'error': 'Not found'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 130000000:
                    raise ValueError('文件过大或内容为空')
                request = json.loads(self.rfile.read(length))
                self.respond(200, call_tool(store, request.get('name'), request.get('arguments', {})))
            except (ValueError, KeyError, TypeError, sqlite3.Error) as error:
                self.respond(400, {'error': str(error)})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    runtime = {'url': f'http://127.0.0.1:{server.server_port}', 'pid': os.getpid()}
    (store.directory / 'preview.json').write_text(json.dumps(runtime))
    print(json.dumps({'url': f'http://127.0.0.1:{server.server_port}', 'database': str(store.path)}), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--web', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    if args.web:
        serve_web(Store(), args.port)
    else:
        serve_stdio(Store())
