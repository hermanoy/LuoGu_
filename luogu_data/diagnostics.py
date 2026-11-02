from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import urlsplit, urlunsplit

from .common import PipelineError, atomic_write
from .provider import Client
from .runner import run


def safe_endpoint(value: str) -> str:
    try:
        p=urlsplit(value)
        return urlunsplit((p.scheme,p.hostname or '',p.path,'',''))
    except ValueError:
        return '<invalid URL>'


def diagnose(args, *, test_api=False):
    """Run a real compile/execute check; optional paid API check is explicit."""
    checks=[]
    compiler=shutil.which(args.cxx)
    report={'python':sys.version.split()[0], 'compiler':compiler,
            'api_base_url':safe_endpoint(args.base_url),'model':args.model,
            'api_key_present':bool(os.environ.get('LLM_API_KEY') or os.environ.get('DEEPSEEK_API_KEY')),
            'playwright_installed':importlib.util.find_spec('playwright') is not None,
            'checks':checks}
    try:
        if not compiler: raise PipelineError('找不到 g++，请安装 MSYS2 UCRT64 GCC 或设置 --cxx')
        with tempfile.TemporaryDirectory(prefix='luogu-check-') as directory:
            cwd=Path(directory)/'编译检查';cwd.mkdir()
            source=cwd/'check.cpp';binary=cwd/'check.exe'
            atomic_write(source,'#include <iostream>\n#include <numeric>\nint main(){ if constexpr(sizeof(long long)>=8) std::cout << std::gcd(42,30) << "\\n"; }\n')
            run([compiler,'-std=c++17','-O2',str(source),'-o',str(binary)],cwd=cwd,timeout=60,memory_mb=1024)
            output,_=run([str(binary)],cwd=cwd,timeout=5,memory_mb=128)
            if output.replace('\r\n','\n')!='6\n': raise PipelineError('C++17 自检结果错误')
        checks.append({'name':'cpp17_compile_run_unicode_path','status':'passed'})
    except (OSError,PipelineError) as exc:
        checks.append({'name':'cpp17_compile_run_unicode_path','status':'failed','error':str(exc)})
    if test_api:
        for model in dict.fromkeys([args.model,getattr(args,'review_model',None) or args.model]):
            try:
                answer=Client(args.base_url,model).ask('Return one JSON object only: {"ok": true}.','Connectivity and JSON mode check. Return {"ok": true}.')
                if answer!={'ok':True}: raise PipelineError('模型未遵守 JSON 输出契约')
                checks.append({'name':'model_json_contract','model':model,'status':'passed'})
            except (OSError,PipelineError) as exc:
                checks.append({'name':'model_json_contract','model':model,'status':'failed','error':str(exc)})
    report['status']='failed' if any(c['status']=='failed' for c in checks) else 'passed'
    return report
