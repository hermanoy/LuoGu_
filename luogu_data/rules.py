from datetime import datetime, timezone
from pathlib import Path
import urllib.request
from .common import PipelineError, digest, load_json, save_json


def check_latest(destination: Path):
    policy=load_json(Path(__file__).with_name('policy.json'))
    evidence={}
    for name,expected in policy['document_hashes'].items():
        print('检查洛谷官方文档：'+name,flush=True)
        url='https://raw.githubusercontent.com/luogu-dev/docs/master/docs/manual/luogu/problem/'+name
        try:
            with urllib.request.urlopen(url,timeout=20) as response:
                raw=response.read(1000001)
        except Exception as exc:
            raise PipelineError('无法在线核实规则：'+type(exc).__name__) from None
        if len(raw)>1000000: raise PipelineError('官方文档超过合理大小')
        actual=digest(raw)
        evidence[name]={'url':url,'sha256':actual,'unchanged':actual==expected}
    result={'checked_at':datetime.now(timezone.utc).isoformat(),'documents':evidence,
            'unchanged':all(v['unchanged'] for v in evidence.values())}
    save_json(destination,result)
    if not result['unchanged']:
        raise PipelineError('官方规则文档已变化；检查 rules-check.json 的来源后更新实现，不能自动宣称兼容')
    return result
