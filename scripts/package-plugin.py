#!/usr/bin/env python3
"""Build a shareable package from an explicit plugin-only allowlist."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
source=ROOT/'plugin/conversation-notes'
version=json.loads((source/'plugin.json').read_text())['version']
destination=ROOT/'dist'/f'codex-conversation-notes-{version}-macOS.zip'
destination.parent.mkdir(exist_ok=True)
excluded={'__pycache__','node_modules','.git','data','images'}
with ZipFile(destination,'w',ZIP_DEFLATED,compresslevel=9) as archive:
    for file in sorted(source.rglob('*')):
        relative=file.relative_to(source)
        if not file.is_file() or set(relative.parts)&excluded or file.name in {'export-runtime.json','.DS_Store'} or file.suffix in {'.pyc','.sqlite3','.db'}:continue
        archive.write(file,Path('conversation-notes')/relative)
    archive.write(ROOT/'README.md','conversation-notes/README.md') if not (source/'README.md').is_file() else None
    archive.write(ROOT/'CHANGELOG.md','conversation-notes/CHANGELOG.md')
    for file in sorted((ROOT/'docs').iterdir()):
        if file.suffix == '.md' or file.name in {'demo-reader.png', 'demo-editor.png'}:
            archive.write(file,Path('conversation-notes/docs')/file.name)
with ZipFile(destination) as archive:
    installer=archive.getinfo('conversation-notes/安装.command')
    assert installer.flag_bits&0x800 and (installer.external_attr>>16)&0o111
    assert archive.testzip() is None
    for name in archive.namelist():assert not any(part in name for part in ['export-runtime.json','sqlite3','__pycache__','notes-data'])
digest=hashlib.sha256(destination.read_bytes()).hexdigest()
(destination.parent/'SHA256SUMS').write_text(digest+'  '+destination.name+'\n')
print(destination)
