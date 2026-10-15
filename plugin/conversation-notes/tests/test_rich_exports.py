import base64
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from PIL import Image
from pypdf import PdfReader
from server import Store,length,call_tool
from media_store import remote_image

NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


class RichExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name)

    def tearDown(self):self.tmp.cleanup()

    def test_rename_persists_conflicts_and_backup_uses_new_title(self):
        d=self.store.import_text('旧标题','正文内容');mid=d['messages'][0]['id']
        self.store.add_annotation(mid,0,2,'正文',comment='保留批注')
        renamed=call_tool(self.store,'rename_document',{'document_id':d['document']['id'],'title':'  新标题  ','expected_revision':1})
        self.assertEqual(renamed['document']['title'],'新标题')
        self.assertEqual(renamed['annotations'][0]['comment'],'保留批注')
        with self.assertRaises(ValueError):self.store.rename_document(d['document']['id'],'过期修改',1)
        with self.assertRaises(ValueError):self.store.rename_document(d['document']['id'],'   ',2)
        self.assertEqual(Store(self.tmp.name).library()['documents'][0]['title'],'新标题')
        restored=Store(Path(self.tmp.name)/'restore').restore_notes(self.store.export_notes('json')['content'])
        self.assertEqual(restored['documents'][0]['title'],'新标题')

    def test_rich_word_pdf_preserve_structure_highlights_images_and_comments(self):
        path=Path(self.tmp.name)/'中文图.svg';path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="80"><rect width="240" height="80" fill="#6688aa"/></svg>')
        body=f'## 标题\n\n**重要想法**和*斜体*。\n\n| 项目 | 数值 |\n| --- | ---: |\n| 重点单元格 | $x^2$ |\n\n\\[\n\\frac{{1}}{{3}}\n\\]\n\n![示例图片](<{path}>)'
        d=self.store.import_text('富格式',body);mid=d['messages'][0]['id']
        selections=[('重要想法','yellow'),('重点单元格','rose'),('\\[\n\\frac{1}{3}\n\\]','blue'),(f'![示例图片](<{path}>)','green')]
        for quote,color in selections:
            a=body.index(quote);self.store.add_annotation(mid,length(body[:a]),length(body[:a+len(quote)]),quote,color=color,comment='对应批注 '+color)
        renamed=self.store.rename_document(d['document']['id'],'新导出标题',1)
        word=self.store.export_notes('docx',d['document']['id'])
        self.assertEqual(word['filename'],'新导出标题.docx');self.assertNotIn('warnings',word)
        with ZipFile(BytesIO(base64.b64decode(word['blob']))) as z:
            xml=ET.fromstring(z.read('word/document.xml'));comments=ET.fromstring(z.read('word/comments.xml'))
            self.assertEqual(len(xml.findall('.//w:tbl',NS)),1)
            self.assertEqual(len(xml.findall('.//w:tblHeader',NS)),1)
            self.assertTrue(any('重要想法' in ''.join(r.itertext()) and r.find('w:rPr/w:b',NS) is not None for r in xml.findall('.//w:r',NS)))
            self.assertEqual(len(comments.findall('w:comment',NS)),4)
            self.assertEqual(len(xml.findall('.//w:commentRangeStart',NS)),4)
            images=[n for n in z.namelist() if n.startswith('word/media/')]
            self.assertGreaterEqual(len(images),3)
            self.assertTrue(any(Image.open(BytesIO(z.read(n))).width/Image.open(BytesIO(z.read(n))).height>2 for n in images))
            self.assertNotIn('**重要想法**',''.join(xml.itertext()))
        pdf=self.store.export_notes('pdf',d['document']['id']);reader=PdfReader(BytesIO(base64.b64decode(pdf['blob'])))
        plain='\n'.join(p.extract_text() for p in reader.pages)
        for value in ['新导出标题','重要想法','重点单元格','对应批注']:self.assertIn(value,plain)
        notes=[a.get_object() for p in reader.pages for a in p.get('/Annots',[]) if a.get_object().get('/Subtype')=='/Text']
        self.assertEqual(len(notes),4)
        self.assertTrue(all(float(n['/Rect'][0])>550 for n in notes))
        self.assertTrue(all('对应批注' in n['/Contents'] for n in notes))

    def test_missing_image_produces_visible_reason_and_warning(self):
        d=self.store.import_text('缺失图片','保留正文\n\n![缺失](/nonexistent/image.png)')
        result=self.store.export_notes('docx',d['document']['id'])
        self.assertTrue(result['warnings'])
        with ZipFile(BytesIO(base64.b64decode(result['blob']))) as z:
            plain=''.join(ET.fromstring(z.read('word/document.xml')).itertext())
            self.assertIn('保留正文',plain);self.assertIn('找不到',plain)

    def test_export_remote_fetch_rejects_local_and_private_destinations(self):
        for url in ['http://127.0.0.1/image.png','http://169.254.169.254/latest','http://10.0.0.1/a','file:///etc/passwd']:
            with self.assertRaises(ValueError):remote_image(url)

    def test_image_examples_in_code_are_not_loaded(self):
        d=self.store.import_text('图片语法示例','```markdown\n![仅示例](/nonexistent/example.png)\n```\n\n:codex-file-citation{path="/nonexistent/example.pdf" purpose="output"}')
        with patch.object(self.store,'get_message_image',side_effect=AssertionError('Non-rendered image must not be fetched')):
            result=self.store.export_notes('docx',d['document']['id'])
        self.assertNotIn('warnings',result)


if __name__=='__main__':unittest.main()
