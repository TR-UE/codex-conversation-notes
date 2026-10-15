"""Clean, space-containing install paths; no access to the user's notes/catalog."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
archive=next((ROOT/'dist').glob('*.zip'))
with tempfile.TemporaryDirectory(prefix='notes-public-install-') as scratch:
    base=Path(scratch);extracted=base/'Extracted Package';extracted.mkdir()
    with ZipFile(archive) as z:z.extractall(extracted)
    source=extracted/'conversation-notes';sys.path.insert(0,str(source/'scripts'))
    spec=importlib.util.spec_from_file_location('notes_installer',source/'scripts/install.py');installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)
    install_home=base/'User Home';catalog=install_home/'.agents/plugins/marketplace.json';catalog.parent.mkdir(parents=True)
    other={'name':'existing-plugin','source':{'source':'local','path':'./unchanged'}}
    catalog.write_text(json.dumps({'name':'existing-market','plugins':[other]}))
    data=install_home/'.codex/conversation-notes';data.mkdir(parents=True);sentinel=data/'notes.sqlite3';sentinel.write_bytes(b'keep-existing-note-data')
    result=installer.prepare(source,install_home)
    second=installer.prepare(source,install_home)
    assert result['document_exports'] and second['document_exports']
    assert sentinel.read_bytes()==b'keep-existing-note-data'
    entries=json.loads(catalog.read_text())['plugins'];assert entries[0]==other and len(entries)==2
    assert result['plugin_id']=='conversation-notes@existing-market'
    target=Path(result['source']);runtime=json.loads((target/'export-runtime.json').read_text())
    messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{}},{'jsonrpc':'2.0','id':2,'method':'tools/list'},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'import_text','arguments':{'title':'Clean install','body':'**Demo text**'}}}]
    env={**os.environ,'CONVERSATION_NOTES_DATA':str(base/'Only Test Notes')}
    process=subprocess.run(['/bin/sh',str(target/'scripts/run-python.sh'),str(target/'server.py')],input='\n'.join(map(json.dumps,messages))+'\n',text=True,capture_output=True,env=env,check=True)
    replies=[json.loads(line) for line in process.stdout.splitlines()]
    assert replies[0]['result']['serverInfo']['version']=='0.6.1'
    names={t['name'] for t in replies[1]['result']['tools']};assert {'rename_document','edit_message','export_notes','open_notes'}<=names
    assert replies[2]['result']['structuredContent']['messages'][0]['body']=='**Demo text**'
    assert (target/'web/vendor/katex.css').is_file() and (target/'export/export-engine.mjs').is_file()
    assert sentinel.read_bytes()==b'keep-existing-note-data'
    print('PASS: clean install and repeat upgrade; existing catalog/data preserved; MCP handshake, tool discovery and import from a path containing spaces.')
