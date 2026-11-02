from __future__ import annotations
from pathlib import Path
import tomllib
from .common import PipelineError


def read_settings(path: Path) -> dict:
    data=tomllib.loads(path.read_text(encoding='utf-8-sig'))
    allowed={'model':{'base_url','model','review_model'},
             'generation':{'points','seed','stress','cxx'}}
    values={}
    for section,fields in data.items():
        if section not in allowed or not isinstance(fields,dict) or set(fields)-allowed[section]:
            raise PipelineError('settings.toml 存在未知字段；密钥请放在环境变量中')
        values.update(fields)
    for key,value in values.items():
        if key in {'points','seed','stress'}:
            if type(value) is not int: raise PipelineError('设置必须为整数：'+key)
        elif not isinstance(value,str): raise PipelineError('设置必须为字符串：'+key)
    return values
