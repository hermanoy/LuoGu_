"""Runs generated Python in a separate resource-limited process."""
import importlib.util
import json
from pathlib import Path
import random
import sys


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path.cwd() / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


if __name__ == '__main__':
    req = json.load(sys.stdin)
    if req['op'] == 'generate':
        g = module('generator')
        rng = random.Random(req['seed'])
        if req['small']:
            text, meta = g.small(rng, req['index'])
        else:
            text, meta = g.generate(rng, req['index'], req['points'])
        result = {'input': text, 'meta': meta}
    elif req['op'] == 'input':
        result = module('validator').validate_input(req['input'])
        if not isinstance(result, dict):
            raise ValueError('validate_input must return scale metadata dict')
    elif req['op'] == 'output':
        module('validator').validate_output(req['input'], req['output'])
        result = {'valid': True}
    elif req['op'] == 'subtask':
        module('validator').validate_subtask(req['input'], req['subtask'])
        result = {'valid': True}
    elif req['op'] == 'coverage':
        module('validator').validate_coverage(req['input'],req['tags'])
        result = {'valid': True}
    else:
        raise ValueError('Unknown worker operation')
    print(json.dumps(result, ensure_ascii=False))
