"""Windows double-click launcher; secret exists only in the child process memory."""
from __future__ import annotations
import getpass
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from luogu_data.cli import main
from luogu_data.settings import read_settings


def launch():
    os.chdir(ROOT)
    settings=read_settings(ROOT/'settings.toml') if (ROOT/'settings.toml').exists() else {}
    endpoint=settings.get('base_url',os.environ.get('LLM_BASE_URL','https://api.deepseek.com'))
    local=urlsplit(endpoint).hostname in {'127.0.0.1','localhost','::1'}
    injected=False
    try:
        if not local and not (os.environ.get('LLM_API_KEY') or os.environ.get('DEEPSEEK_API_KEY')):
            if not sys.stdin.isatty():
                print('请在本地终端运行，或设置 LLM_API_KEY 环境变量。',file=sys.stderr);return 2
            key=getpass.getpass('API Key（隐藏输入，仅本次运行使用，不保存）：').strip()
            if not key:
                print('未输入密钥，已退出。');return 2
            os.environ['LLM_API_KEY']=key
            del key
            injected=True
        code=main(['--doctor'])
        if code: return code
        return main(['inbox','--watch','--output','outputs/inbox'])
    finally:
        if injected: os.environ.pop('LLM_API_KEY',None)


if __name__=='__main__':
    try: raise SystemExit(launch())
    except KeyboardInterrupt: raise SystemExit(130)
    except Exception as exc:
        print('启动失败：'+str(exc),file=sys.stderr);raise SystemExit(1)
