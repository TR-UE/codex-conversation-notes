#!/usr/bin/env python3
"""Install only this plugin; preserve the personal catalog and all note data."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from configure_runtime import configure

ROOT=Path(__file__).resolve().parents[1]


def find_codex(install_home):
    candidates=[os.environ.get('CODEX_BINARY'),os.environ.get('CODEX_CLI_PATH'),shutil.which('codex'),'/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex']
    candidates+=map(str,sorted((install_home/'.nvm/versions/node').glob('*/bin/codex'),reverse=True))
    return next((c for c in candidates if c and Path(c).is_file() and os.access(c,os.X_OK)),None)


def prepare(source,install_home,runtime_root=None):
    source,install_home=Path(source).resolve(),Path(install_home).resolve()
    catalog_path=install_home/'.agents/plugins/marketplace.json'
    catalog=json.loads(catalog_path.read_text()) if catalog_path.exists() else {'name':'conversation-notes-local','interface':{'displayName':'我的本地工具'},'plugins':[]}
    if not isinstance(catalog.get('plugins'),list) or not catalog.get('name'):raise ValueError('现有个人插件目录无效，已保留原文件，请先修复目录')
    target=install_home/'.local/share/codex-conversation-notes/plugin/conversation-notes'
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.notes-install-',dir=target.parent) as folder:
        staging=Path(folder)/'plugin'
        shutil.copytree(source,staging,ignore=shutil.ignore_patterns('__pycache__','*.pyc','node_modules','.git','export-runtime.json','.DS_Store'))
        if (target/'export-runtime.json').is_file():shutil.copy2(target/'export-runtime.json',staging/'export-runtime.json')
        warning=None
        try:configure(staging,runtime_root=runtime_root)
        except (ValueError,OSError,subprocess.SubprocessError) as error:warning=str(error)
        if target.exists():
            backup=target.with_name('conversation-notes.previous')
            if backup.exists():shutil.rmtree(backup)
            target.rename(backup)
        staging.rename(target)
    entry={'name':'conversation-notes','source':{'source':'local','path':'./'+target.relative_to(install_home).as_posix()},'policy':{'installation':'AVAILABLE','authentication':'ON_INSTALL'},'category':'Productivity'}
    current=next((p for p in catalog['plugins'] if p.get('name')=='conversation-notes'),None)
    if current:current.update(entry)
    else:catalog['plugins'].append(entry)
    catalog_path.parent.mkdir(parents=True,exist_ok=True)
    if catalog_path.exists():shutil.copy2(catalog_path,catalog_path.with_suffix('.json.before-notes'))
    temporary=catalog_path.with_suffix('.json.notes-tmp');temporary.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n');temporary.replace(catalog_path)
    return {'source':str(target),'marketplace':catalog['name'],'plugin_id':'conversation-notes@'+catalog['name'],'document_exports':not warning,**({'warning':warning} if warning else {})}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--prepare-only',action='store_true');parser.add_argument('--destination-home',type=Path,default=Path.home());parser.add_argument('--runtime-root')
    args=parser.parse_args();codex=find_codex(args.destination_home)
    if not args.prepare_only and not codex:raise ValueError('未找到 Codex CLI，请先安装 Codex 桌面应用或 CLI，再重新运行安装')
    result=prepare(ROOT,args.destination_home,args.runtime_root)
    if not args.prepare_only:
        env=os.environ.copy();node_bin=Path(args.runtime_root or Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies')/'node/bin'
        env['PATH']=str(node_bin)+os.pathsep+str(Path(codex).parent)+os.pathsep+env.get('PATH','')
        def run(arguments):return json.loads(subprocess.check_output([codex,*arguments],text=True,env=env,stderr=subprocess.PIPE))
        markets=run(['plugin','marketplace','list','--json']).get('marketplaces',[])
        if not any(m.get('name')==result['marketplace'] and Path(m.get('root','')).resolve()==args.destination_home.resolve() for m in markets):run(['plugin','marketplace','add',str(args.destination_home),'--json'])
        result['installed']=run(['plugin','add',result['plugin_id'],'--json'])
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not args.prepare_only:print('墨记已安装。请重启 Codex，在新聊天中选择墨记，或双击“打开墨记.command”。')


if __name__=='__main__':
    try:main()
    except (ValueError,OSError,subprocess.SubprocessError) as error:raise SystemExit('安装未完成：'+str(error))
