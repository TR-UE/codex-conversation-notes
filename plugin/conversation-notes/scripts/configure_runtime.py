#!/usr/bin/env python3
"""Configure optional document exports using this user's bundled runtime."""
import argparse
import json
from pathlib import Path
import os
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def configure(plugin_root=ROOT, runtime_root=None, overrides=None):
    runtime_root=Path(runtime_root or os.environ.get('CONVERSATION_NOTES_RUNTIME_ROOT') or Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies').expanduser()
    values={'python':str(runtime_root/'python/bin/python3'),'node':str(runtime_root/'node/bin/node'),'node_modules':str(runtime_root/'node/node_modules'),'soffice':str(runtime_root/'bin/override/soffice')}
    target=Path(plugin_root)/'export-runtime.json'
    if target.is_file():
        saved=json.loads(target.read_text())
        if all(Path(saved.get(k,'/nonexistent')).exists() for k in values):values.update(saved)
    values.update({k:str(Path(v).expanduser().resolve()) for k,v in (overrides or {}).items() if v})
    missing=[k for k,v in values.items() if not Path(v).exists()]
    if missing:raise ValueError('PDF / Word 运行环境未齐备：'+', '.join(missing)+'。请查看 docs/INSTALL.md，基础阅读和批注功能仍可使用。')
    subprocess.run([values['python'],'-c','import docx,PIL,pypdf'],check=True,capture_output=True,timeout=15)
    subprocess.run([values['node'],'-e',"require(process.argv[1]+'/sharp');if(Number(process.versions.node.split('.')[0])<22)process.exit(1)",values['node_modules']],check=True,capture_output=True,timeout=15)
    target.write_text(json.dumps(values,indent=2)+'\n');target.chmod(0o600)
    return {'document_exports':True,'config':str(target)}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--runtime-root')
    for key in ('python','node','node-modules','soffice'):parser.add_argument('--'+key)
    args=parser.parse_args()
    try:print(json.dumps(configure(runtime_root=args.runtime_root,overrides={k:getattr(args,k) for k in ('python','node','node_modules','soffice')}),ensure_ascii=False))
    except (ValueError,OSError,subprocess.SubprocessError) as error:parser.exit(2,str(error)+'\n')
