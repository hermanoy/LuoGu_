from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from .common import PipelineError
from .pipeline import Pipeline, STAGES


def parser():
    p=argparse.ArgumentParser(description='洛谷本地测试数据生成 MVP（Python 3.11+ / C++17）')
    p.add_argument('problem',nargs='?',type=Path,help='problem.md 或批量题目目录')
    p.add_argument('--settings',type=Path,help='本机 TOML 设置（默认读取 settings.toml）')
    p.add_argument('--review-model',default=os.environ.get('LLM_REVIEW_MODEL'),help='独立审查使用的模型')
    p.add_argument('--doctor',action='store_true',help='检查 Python、C++、API 配置及可选浏览器依赖')
    p.add_argument('--test-api',action='store_true',help='执行真实 JSON 模型连通性检查（可能产生少量费用）')
    p.add_argument('--diagnostic-report',type=Path,help='保存不含密钥的自检结果 JSON')
    p.add_argument('--check-rules',action='store_true',help='在线比较官方文档是否改变')
    p.add_argument('--watch',action='store_true',help='监听目录中新增/修改且保存稳定的 problem.md')
    p.add_argument('--watch-interval',type=float,default=2)
    p.add_argument('--upload-adapter',type=Path,help='本机洛谷页面选择器映射 JSON')
    p.add_argument('--points',type=int,default=20)
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--stress',type=int,default=100,help='边界与小规模随机对拍总数，至少20')
    p.add_argument('--output',type=Path,help='单题输出目录，或批处理输出根目录')
    p.add_argument('--provider',choices=['auto','builtin','api'],default='auto')
    p.add_argument('--base-url',default=os.environ.get('LLM_BASE_URL','https://api.deepseek.com'))
    p.add_argument('--model',default=os.environ.get('LLM_MODEL','deepseek-chat'))
    p.add_argument('--cxx',default=os.environ.get('CXX','g++'))
    p.add_argument('--until',choices=STAGES,default='package',help='运行至指定阶段，包含前置阶段')
    p.add_argument('--resume',action='store_true',help='校验哈希并复用已完成阶段')
    p.add_argument('--policy',type=Path,help='自定义本地打包限制 JSON；不是自动更新的官方规则')
    p.add_argument('--upload-preview',metavar='URL',help='生成完成后显示本地上传预览，不打开浏览器，不上传')
    p.add_argument('--upload',metavar='URL',help='可选的本机浏览器上传；登录、预览及明确确认后才选择文件')
    return p


def main(argv=None):
    if os.name == 'nt':
        # Redirected stdout/stderr otherwise default to cp1252 on English Windows.
        for stream in (sys.stdout,sys.stderr):
            if hasattr(stream,'reconfigure'):
                stream.reconfigure(encoding='utf-8',errors='backslashreplace')
    p=parser()
    prelim,_=p.parse_known_args(argv)
    settings=prelim.settings or Path('settings.toml')
    try:
        if settings.exists():
            from .settings import read_settings
            p.set_defaults(**read_settings(settings))
        elif prelim.settings: raise PipelineError('指定的设置文件不存在')
    except Exception as exc:
        print('错误：'+str(exc),file=sys.stderr); return 2
    args=p.parse_args(argv)
    if args.check_rules:
        from .rules import check_latest
        try: print(json.dumps(check_latest(Path('rules-check.json')),ensure_ascii=False,indent=2)); return 0
        except Exception as exc: print(str(exc),file=sys.stderr); return 1
    if args.doctor or args.test_api:
        from .diagnostics import diagnose
        from .common import save_json
        report=diagnose(args,test_api=args.test_api)
        if args.diagnostic_report: save_json(args.diagnostic_report,report)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        if not report['api_key_present'] and not args.test_api:
            print('工具链自检不调用模型；通用题面需设置 API 密钥或使用本地模型。')
        return 0 if report['status']=='passed' else 1
    if args.problem is None:
        p.error('请提供 problem.md 或目录，也可使用 --doctor')
    if args.stress<20 or args.stress>10000:
        print('错误：--stress 必须在 20..10000',file=sys.stderr); return 2
    if args.upload and (not args.upload_adapter or args.until!='package' or args.watch):
        print('错误：上传必须指定 --upload-adapter，完整运行到 package，并关闭目录监听',file=sys.stderr); return 2
    if args.watch:
        if args.watch_interval<.2 or args.until!='package' or args.upload_preview:
            print('监听需要完整流水线，间隔至少0.2秒，不支持批量上传预览',file=sys.stderr); return 2
        from .watch import watch
        try: watch(args.problem,args.output or Path('outputs/inbox'),args)
        except KeyboardInterrupt: print('已停止监听'); return 0
        except Exception as exc: print(str(exc),file=sys.stderr); return 1
    source=args.problem.resolve()
    if not source.exists():
        print('错误：题面路径不存在',file=sys.stderr); return 2
    batch=source.is_dir()
    if batch:
        candidates=sorted(source.rglob('problem.md'))
        problems=candidates or sorted(source.glob('*.md'))
        # Never recursively process generated reports.
        out_base=(args.output or Path('outputs')/source.name).resolve()
        problems=[p for p in problems if not p.resolve().is_relative_to(out_base)]
        if not problems:
            print('错误：目录下没有 problem.md 或 Markdown 题面',file=sys.stderr); return 2
        if args.upload_preview or args.upload:
            print('错误：上传预览仅支持单题，避免批处理关联到错误题号',file=sys.stderr); return 2
    else: problems=[source]
    failed=0
    for problem in problems:
        root=out_base/problem.relative_to(source).with_suffix('') if batch else (args.output or Path('outputs')/problem.stem)
        print(f'处理：{problem}\n输出：{root.resolve()}',flush=True)
        try:
            Pipeline(problem,root,args).execute()
            if args.upload_preview:
                from .uploader import preview
                print(json.dumps(preview(root,args.upload_preview),ensure_ascii=False,indent=2))
            if args.upload:
                from .uploader import upload
                print(json.dumps(asyncio.run(upload(root,args.upload,args.upload_adapter)),ensure_ascii=False,indent=2))
            print(f'完成；查看 {root / "report.md"}',flush=True)
        except KeyboardInterrupt:
            print('已中断；已完成阶段可通过 --resume 恢复',file=sys.stderr)
            return 130
        except Exception as exc:
            failed+=1
            print(f'失败：{exc}',file=sys.stderr)
    return 1 if failed else 0
