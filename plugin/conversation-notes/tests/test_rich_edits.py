import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server import Store, length, call_tool
import server
from media_store import references, image_bytes

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jPtQAAAAASUVORK5CYII=')


class RichEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)

    def tearDown(self): self.tmp.cleanup()

    def test_keyboard_edit_moves_utf16_note_and_undo_keeps_later_comment(self):
        d=self.store.import_text('编辑','前言 **🌸重点** 后续');m=d['messages'][0]
        n=self.store.add_annotation(m['id'],5,9,'🌸重点',comment='保留想法')['annotation']
        changed=call_tool(self.store,'edit_message',{'message_id':m['id'],'body':'新增一段\n前言 **🌸重点** 后续','expected_version':1})
        self.assertEqual(changed['annotations'][0]['quote'],'🌸重点')
        self.assertEqual(changed['annotations'][0]['start'],10)
        note=changed['annotations'][0]
        self.store.update_annotation(n['id'],note['version'],comment='编辑后的新批注')
        old=self.store.undo_delete(changed['undo']['id'])
        self.assertEqual(old['messages'][0]['body'],m['body'])
        self.assertEqual(old['annotations'][0]['start'],5)
        self.assertEqual(old['annotations'][0]['comment'],'编辑后的新批注')
        with self.assertRaises(ValueError): self.store.edit_message(m['id'],'过时版本',1)

    def test_cleared_body_backup_and_nested_undo_restore_erased_note(self):
        d=self.store.import_text('清空','前言重点后续');m=d['messages'][0]
        self.store.add_annotation(m['id'],2,4,'重点',comment='仍可撤销')
        first=self.store.edit_message(m['id'],'前言后续',1)
        self.assertEqual(first['annotations'],[])
        second=self.store.edit_message(m['id'],'',2)
        archive=self.store.export_notes('json')['content']
        other=Store(Path(self.tmp.name)/'restored');restored=other.restore_notes(archive)
        out=other.document(restored['documents'][0]['id'])
        self.assertEqual(out['messages'][0]['body'],'')
        out=other.undo_delete(out['undo']['id']);self.assertEqual(out['messages'][0]['body'],'前言后续')
        out=other.undo_delete(out['undo']['id']);self.assertEqual(out['annotations'][0]['quote'],'重点')
        self.assertEqual(out['annotations'][0]['comment'],'仍可撤销')

    def test_deleted_text_then_keyboard_edit_backup_undo(self):
        d=self.store.import_text('先删后改','abcdef 重点');m=d['messages'][0]
        cut=self.store.delete_text(m['id'],0,6,'abcdef',1)
        edited=self.store.edit_message(m['id'],'新 '+cut['messages'][0]['body'],2)
        out=self.store.undo_delete(edited['undo']['id'])
        self.assertEqual(out['messages'][0]['body'],' 重点')
        out=self.store.undo_delete(out['undo']['id']);self.assertEqual(out['messages'][0]['body'],m['body'])

    def test_rendered_delete_preserves_delimiters_and_undo(self):
        d=self.store.import_text('格式','**前半后半**');m=d['messages'][0]
        cut=call_tool(self.store,'delete_text',{'message_id':m['id'],'start':2,'end':4,'quote':'前半','ranges':[[2,4]],'expected_version':1})
        self.assertEqual(cut['messages'][0]['body'],'**后半**')
        self.assertEqual(self.store.undo_delete(cut['undo']['id'])['messages'][0]['body'],m['body'])
        with self.assertRaises(ValueError):self.store.delete_text(m['id'],2,4,'前半',3,ranges=[[0,4]])

    def test_local_and_native_images_snapshot_backup_authorization(self):
        image=Path(self.tmp.name)/'有 空格(1).png';image.write_bytes(PNG)
        d=self.store._insert('图片',[{'role':'user','body':f'图片 ![示例](<{image}>)','images':['data:image/png;base64,'+base64.b64encode(PNG).decode()]}]);m=d['messages'][0]
        result=self.store.get_message_image(m['id'],str(image));self.assertEqual(base64.b64decode(result['blob']),PNG)
        with self.assertRaises(ValueError):self.store.get_message_image(m['id'],'/etc/passwd')
        image.unlink()
        self.assertEqual(self.store.get_message_image(m['id'],str(image))['mime'],'image/png')
        archive=self.store.export_notes('json')['content']
        other=Store(Path(self.tmp.name)/'restore');restored=other.restore_notes(archive)
        m2=other.document(restored['documents'][0]['id'])['messages'][0]
        self.assertEqual(other.get_message_image(m2['id'],str(image))['blob'],result['blob'])
        key=m2['attachments'][0]['key'];self.assertEqual(other.get_message_image(m2['id'],'attachment:'+key)['blob'],result['blob'])

    def test_image_only_message_and_svg_sanitization(self):
        d=self.store._insert('仅图片',[{'role':'user','body':'','images':['data:image/png;base64,'+base64.b64encode(PNG).decode()]}])
        self.assertEqual(len(d['messages']),1)
        sanitized,mime=image_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><rect onclick="bad()" fill="url(https://bad)" width="3"/><image href="https://bad"/></svg>')
        self.assertEqual(mime,'image/svg+xml')
        for forbidden in (b'script',b'onclick',b'https://bad'): self.assertNotIn(forbidden,sanitized)
        image=Path(self.tmp.name)/'sample.svg';image.write_bytes(sanitized)
        d=self.store.import_text('SVG',f'![SVG]({image})')
        self.assertEqual(self.store.get_message_image(d['messages'][0]['id'],str(image))['mime'],'image/svg+xml')

    def test_protocol_text_is_literal_after_intentional_edit(self):
        prefix='<in-app-browser-context>metadata</in-app-browser-context>\n## My request:\n'
        d=self.store._insert('手动编辑',[{'role':'user','body':prefix+'原问题'}]);m=d['messages'][0]
        self.assertEqual(m['body'],'原问题')
        new=self.store.edit_message(m['id'],prefix+'我在引用这个格式',1)
        self.assertEqual(new['messages'][0]['body'],prefix+'我在引用这个格式')
        out=self.store.undo_delete(new['undo']['id']);self.assertEqual(out['messages'][0]['body'],'原问题')

    def test_sync_backfills_image_without_replacing_keyboard_edit(self):
        thread='00000000-1111-2222-3333-444444444444'
        home=Path(self.tmp.name)/'home';folder=home/'.codex/sessions';folder.mkdir(parents=True)
        content=[{'type':'input_text','text':'原文'}]
        row={'type':'response_item','payload':{'type':'message','id':'u1','role':'user','content':content}}
        file=folder/('rollout-'+thread+'.jsonl');file.write_text(json.dumps(row)+'\n')
        with patch.object(server.Path,'home',return_value=home):
            first=self.store.import_thread(thread);m=first['messages'][0]
            self.store.edit_message(m['id'],'手动整理后的内容',1)
            content.append({'type':'input_image','image_url':'data:image/png;base64,'+base64.b64encode(PNG).decode()})
            file.write_text(json.dumps(row)+'\n')
            second=self.store.import_thread(thread)
            self.assertEqual(len(second['messages']),1)
            self.assertEqual(second['messages'][0]['body'],'手动整理后的内容')
            self.assertEqual(len(second['messages'][0]['attachments']),1)

    def test_reference_images_are_authorized_exactly(self):
        path=Path(self.tmp.name)/'图.png';path.write_bytes(PNG)
        body=f'![引用][图]\n\n[图]: <{path}>'
        self.assertIn(str(path),references(body))
        d=self.store.import_text('引用图片',body)
        self.assertEqual(self.store.get_message_image(d['messages'][0]['id'],str(path))['mime'],'image/png')


if __name__=='__main__':unittest.main()
