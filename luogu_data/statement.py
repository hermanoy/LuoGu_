from __future__ import annotations

import re
from .common import PipelineError


def parse(text: str) -> dict:
    """Keep math verbatim; extract fenced samples without rendering/executing KaTeX."""
    if len(text.encode('utf-8')) > 200000:
        raise PipelineError('题面超过 200KB；请移除内嵌资源')
    if '\x00' in text or not text.strip():
        raise PipelineError('题面为空或包含 NUL')
    heading = ''
    sections = []
    blocks = []
    fence = None
    content = []
    kind = None
    for line in text.splitlines():
        if fence:
            if re.fullmatch(re.escape(fence[0]) + '{' + str(len(fence)) + r',}\s*', line.strip()):
                if kind:
                    blocks.append((kind, '\n'.join(content) + '\n'))
                fence = None
            else:
                content.append(line)
            continue
        m = re.match(r'^\s*(`{3,}|~{3,})([^\s]*)\s*$', line)
        if m:
            fence, lang = m.groups()
            content = []
            label = lang.lower()
            if re.fullmatch(r'(input|in)\d*', label): kind = 'input'
            elif re.fullmatch(r'(output|out)\d*', label): kind = 'output'
            elif '样例' in heading or 'sample' in heading.lower() or 'example' in heading.lower():
                kind = 'input' if ('输入' in heading or 'input' in heading.lower()) else 'output' if ('输出' in heading or 'output' in heading.lower()) else None
            else: kind = None
        elif re.match(r'^\s*#{1,6}\s+', line):
            heading = re.sub(r'^\s*#{1,6}\s+', '', line)
            sections.append(heading)
    if fence:
        raise PipelineError('Markdown 代码围栏未闭合')
    samples = []
    for i in range(0, len(blocks), 2):
        if i + 1 >= len(blocks) or blocks[i][0] != 'input' or blocks[i+1][0] != 'output':
            raise PipelineError('样例输入输出未按顺序配对；请使用 input/output 代码围栏')
        samples.append({'input': blocks[i][1], 'output': blocks[i+1][1]})
    if not samples:
        raise PipelineError('未找到可可靠配对的样例；请用 ```input 与 ```output 标记至少一组样例')
    return {'markdown': text, 'sections': sections, 'samples': samples}
