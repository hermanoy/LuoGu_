from __future__ import annotations
import copy
from pathlib import Path
import time
import json
from .common import file_hash, digest
from .pipeline import Pipeline


def watch(folder: Path, output: Path, args):
    folder=folder.resolve(); output=output.resolve()
    folder.mkdir(parents=True,exist_ok=True)
    settings={k:str(getattr(args,k,None)) for k in ('points','seed','stress','provider','base_url','model','review_model','cxx')}
    settings['policy']=file_hash(Path(args.policy)) if getattr(args,'policy',None) else file_hash(Path(__file__).with_name('policy.json'))
    settings['code']=digest(b''.join(p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py'))))
    configuration=digest(json.dumps(settings,sort_keys=True).encode())[:12]
    previous={}; processed=set()
    print(f'监听 {folder}，放入 problem.md 或 子目录/problem.md；Ctrl+C 退出。',flush=True)
    while True:
        current={}
        for path in sorted(folder.rglob('problem.md')):
            if path.resolve().is_relative_to(output): continue
            try: sha=file_hash(path)
            except OSError: continue
            current[path]=sha
            key=(str(path),sha)
            if previous.get(path)!=sha or key in processed: continue
            # A stable file needs two observations, so an editor's partial save is ignored.
            root=output/path.relative_to(folder).parent/(sha[:16]+'-'+configuration)
            local=copy.copy(args); local.resume=True
            try:
                Pipeline(path,root,local).execute()
                print('已生成：'+str(root/'testcases.zip'),flush=True)
            except Exception as exc:
                print('处理失败（报告保留；修正题面或重启监听后重试）：'+str(exc),flush=True)
            processed.add(key)
        previous=current
        time.sleep(args.watch_interval)
