from __future__ import annotations

import re
from pathlib import Path
import zipfile

from .common import PipelineError, atomic_write


def config_text(spec: dict, points: int) -> str:
    lines = []
    for name, values in expected_config(spec, points).items():
        lines.append(name + ':')
        lines.extend(f'  {key}: {value}' for key,value in values.items())
    return '\n'.join(lines) + '\n'


def expected_config(spec: dict, points: int) -> dict:
    base,extra=divmod(100,points)
    result={}
    for i in range(1,points+1):
        row={'timeLimit':spec['time_ms'],'memoryLimit':spec['memory_mb']*1024,
             'score':base+(i<=extra)}
        for group in spec['subtasks']:
            if i in group['cases']:
                row.update(score=group['score'],subtaskId=group['id'])
        result[f'{i}.in']=row
    return result


def scoring_scripts(spec: dict) -> dict:
    """Explicit all-or-nothing group scripts and dependency-aware total script.

    Config.yml cannot carry scoring scripts. They must be applied through the UI.
    """
    groups=spec['subtasks']
    if not groups: return {}
    result={}
    def script(ids, score):
        condition=' and '.join(f'@status{i} == AC' for i in ids)
        lines=['@total_score = 0;', '@final_status = UNAC;', '@final_time = 0;', '@final_memory = 0;',
               f'if {condition}; then', f'    @total_score = {score};', '    @final_status = AC;', 'fi']
        for i in ids:
            lines += [f'if @time{i} > @final_time; then',f'    @final_time = @time{i};','fi',
                      f'if @memory{i} > @final_memory; then',f'    @final_memory = @memory{i};','fi']
        return '\n'.join(lines)+'\n'
    for g in groups: result[str(g['id'])]=script(g['cases'],g['score'])
    lines=['@total_score = 0;', '@final_status = AC;', '@final_time = 0;', '@final_memory = 0;']
    for g in groups:
        i=g['id']
        condition=' and '.join(f'@status{x} == AC' for x in [i,*g.get('depends',[])])
        lines += [f'if {condition}; then',f'    @total_score = @total_score + {g["score"]};',
                  'else',f'    @score{i} = 0;',f'    @status{i} = UNAC;', '    @final_status = UNAC;','fi',
                  f'if @time{i} > @final_time; then',f'    @final_time = @time{i};','fi',
                  f'if @memory{i} > @final_memory; then',f'    @final_memory = @memory{i};','fi']
    result['total']='\n'.join(lines)+'\n'
    return result


def members(root: Path, points: int, policy: dict) -> list[tuple[str, Path]]:
    result = [('config.yml', root / 'config.yml')]
    expected = {f'{i}.{ext}' for i in range(1,points+1) for ext in ('in','out')}
    actual = {p.name for p in (root/'cases').iterdir()}
    if actual != expected:
        raise PipelineError('输入输出未成对或存在多余文件')
    total = 0
    for name in sorted(expected, key=lambda s:(int(s.split('.')[0]),s)):
        if not re.fullmatch(r'[1-9][0-9]*\.(in|out)',name):
            raise PipelineError('非法测试点文件名')
        result.append((name, root/'cases'/name))
    for _, path in result:
        if path.is_symlink() or not path.is_file(): raise PipelineError('产物必须是普通文件')
        size = path.stat().st_size
        if not 0 < size <= policy['max_file_bytes']: raise PipelineError('单文件为空或超过限制：'+path.name)
        total += size
    if total > policy['max_total_bytes']: raise PipelineError('解压后总大小超过限制')
    return result


def pack(root: Path, points: int, policy: dict) -> None:
    items = members(root, points, policy)
    target = root/'testcases.zip'
    tmp = root/'testcases.zip.tmp'
    try:
        with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
            for name,path in items:
                info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
                info.compress_type=zipfile.ZIP_DEFLATED
                info.create_system=3
                info.external_attr=0o100644<<16
                archive.writestr(info,path.read_bytes(),compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
        if tmp.stat().st_size>policy['max_zip_bytes']: raise PipelineError('压缩包超过本地策略大小限制')
        with zipfile.ZipFile(tmp) as archive:
            if archive.namelist()!=[n for n,_ in items] or archive.testzip() is not None:
                raise PipelineError('ZIP 目录或 CRC 校验失败')
            for name,path in items:
                if archive.read(name)!=path.read_bytes(): raise PipelineError('ZIP 文件内容不一致')
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
