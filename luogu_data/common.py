from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


class PipelineError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    return digest(path.read_bytes())


def atomic_write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_bytes(data.encode('utf-8') if isinstance(data, str) else data)
    os.replace(tmp, path)


def save_json(path: Path, value) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def tokens(text: str) -> list[str]:
    return text.split()


def normalize_output(text: str) -> str:
    """Luogu default comparison: ignore line-end spaces and final newlines."""
    return '\n'.join(line.rstrip(' \t') for line in text.replace('\r\n','\n').split('\n')).rstrip('\n')


def judge_equal(a: str, b: str) -> bool:
    return normalize_output(a) == normalize_output(b)


def check_input_format(text: str, exceptions: dict | None = None) -> None:
    exceptions = exceptions or {}
    checks = {
        'non_ascii': any(c != '\n' and not 32 <= ord(c) <= 126 for c in text),
        'empty_input': not text.strip(),
        'blank_lines': text.startswith('\n') or '\n\n' in text,
        'leading_spaces': text.startswith(' ') or '\n ' in text,
        'trailing_spaces': ' \n' in text,
        'multiple_spaces': '  ' in text,
        'missing_final_newline': not text.endswith('\n'),
    }
    if '\r' in text or '\x00' in text:
        raise PipelineError('输入必须使用 LF 换行，不能包含 CR 或 NUL')
    for rule, violated in checks.items():
        if violated and rule not in exceptions:
            raise PipelineError('输入不符合洛谷默认格式且题面没有例外声明：' + rule)
