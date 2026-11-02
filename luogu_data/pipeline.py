from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import sys

from .common import PipelineError, atomic_write, digest, file_hash, load_json, save_json, judge_equal, check_input_format
from .packaging import config_text, members, pack, scoring_scripts
from .provider import obtain
from .runner import run
from .statement import parse

STAGES = ('analyze','build','generate','verify','package')
WORKER = Path(__file__).with_name('worker.py').resolve()


@contextmanager
def locked(root: Path):
    root.mkdir(parents=True,exist_ok=True)
    with (root/'.lock').open('a+b') as handle:
        handle.seek(0)
        if handle.read(1)==b'': handle.write(b'0'); handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            raise PipelineError('该输出目录已有任务运行') from exc
        try: yield
        finally:
            handle.seek(0)
            if os.name=='nt': msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else: fcntl.flock(handle,fcntl.LOCK_UN)


class Pipeline:
    def __init__(self, problem: Path, root: Path, args):
        self.problem,self.root,self.args=problem.resolve(),root.resolve(),args
        self.state={}
        self.policy=load_json(Path(__file__).with_name('policy.json'))
        if args.policy:
            custom=load_json(Path(args.policy))
            for key in ('max_zip_bytes','max_file_bytes','max_total_bytes','max_points'):
                if key in custom:
                    if type(custom[key]) is not int or custom[key]<=0:
                        raise PipelineError('非法打包策略字段：'+key)
                    self.policy[key]=min(self.policy[key],custom[key])
        for key in ('max_zip_bytes','max_file_bytes','max_total_bytes','max_points'):
            if type(self.policy.get(key)) is not int or self.policy[key]<=0:
                raise PipelineError('非法打包策略字段：'+key)
        if not 1<=args.points<=min(100,self.policy['max_points']):
            raise PipelineError('测试点数量须在 1..min(100,policy.max_points)')

    def worker(self, request: dict):
        raw,_=run([sys.executable,str(WORKER)],cwd=self.root,
                  input_text=json.dumps(request,ensure_ascii=False),timeout=30,memory_mb=1024,
                  max_bytes=self.policy['max_file_bytes']*2+65536)
        try: return json.loads(raw)
        except ValueError as exc: raise PipelineError('生成器/验证器未返回合法 JSON（可能打印了调试信息）') from exc

    def validate_input(self, text: str) -> dict:
        if not isinstance(text,str):
            raise PipelineError('输入须为非空文本，不含 NUL，且以换行结束')
        if len(text.encode())>self.policy['max_file_bytes']: raise PipelineError('输入超过大小限制')
        check_input_format(text,load_json(self.root/'spec.json').get('format_exceptions',{}))
        scale=self.worker({'op':'input','input':text})
        if not isinstance(scale,dict): raise PipelineError('验证器规模信息无效')
        return scale

    def validate_output(self, text: str, output: str):
        if not output.strip() or '\x00' in output or not output.endswith('\n'):
            raise PipelineError('标准输出为空、包含 NUL 或缺少末尾换行')
        self.worker({'op':'output','input':text,'output':output})

    def solve(self, text: str, brute=False):
        spec=load_json(self.root/'spec.json')
        output,elapsed=run([str(self.root/('brute.exe' if brute else 'standard.exe'))],cwd=self.root,
                   input_text=text,timeout=10 if brute else max(2,spec['time_ms']/1000*3),
                   memory_mb=spec['memory_mb'],max_bytes=self.policy['max_file_bytes'])
        return output.replace('\r\n','\n'),elapsed

    def generate_one(self, index: int, small=False):
        seed=self.args.seed+index*1000003+(7000000001 if small else 0)
        value=self.worker({'op':'generate','seed':seed,'index':index,'points':self.args.points,'small':small})
        if not isinstance(value,dict) or not isinstance(value.get('meta'),dict) or not isinstance(value['meta'].get('method'),str) or not value['meta']['method'].strip():
            raise PipelineError('生成器缺少构造说明 metadata.method')
        scale=self.validate_input(value.get('input'))
        return value['input'],value['meta'],scale

    def mark(self, stage: str, files: list[Path]):
        self.state['stages'][stage]={str(p.relative_to(self.root)):file_hash(p) for p in files}
        self.state['status']='in_progress'
        self.state.pop('error',None)
        save_json(self.root/'state.json',self.state)

    def check_artifacts(self):
        for stage,files in list(self.state['stages'].items()):
            for name,want in files.items():
                p=self.root/name
                if not p.is_file() or p.is_symlink() or file_hash(p)!=want:
                    # Prior validation is no longer applicable after any upstream change.
                    self.state['invalidated']=stage
                    for dependent in STAGES[STAGES.index(stage):]:
                        self.state['stages'].pop(dependent,None)
                    raise PipelineError(f'{stage} 产物已修改或丢失：{name}；请用新的输出目录重新生成')

    def analyze(self):
        document=parse(self.statement)
        bundle=obtain(self.statement,self.args.points,self.args.provider,self.args.base_url,self.args.model,
                      cache=self.root/'model_cache',review_model=getattr(self.args,'review_model',None))
        save_json(self.root/'parsed.json',document)
        save_json(self.root/'spec.json',bundle['spec'])
        save_json(self.root/'review.json',bundle['review'])
        for name,source in bundle['sources'].items(): atomic_write(self.root/name,source)
        self.mark('analyze',[self.root/n for n in ['parsed.json','spec.json','review.json',*bundle['sources']]])

    def build(self):
        compiler=shutil.which(self.args.cxx)
        if not compiler: raise PipelineError('找不到 C++17 编译器；安装 MSYS2 UCRT64 GCC 并加入 PATH，或使用 --cxx 指定 g++')
        version,_=run([compiler,'--version'],cwd=self.root,timeout=10,memory_mb=1024)
        self.state['compiler']=version.splitlines()[0]
        names=['standard','brute']+[Path(w['file']).stem for w in load_json(self.root/'spec.json')['wrong_solutions']]
        for name in names:
            run([compiler,'-std=c++17','-O2','-Wall','-Wextra',name+'.cpp','-o',name+'.exe'],cwd=self.root,timeout=120,memory_mb=2048)
        self.mark('build',[self.root/(name+'.exe') for name in names])

    def generate(self):
        case_dir=self.root/'cases'
        case_dir.mkdir(exist_ok=True)
        spec=load_json(self.root/'spec.json')
        cases=[]
        progress=self.state.setdefault('generation_progress',{})
        for i in range(1,self.args.points+1):
            if str(i) in progress:
                record=progress[str(i)]
                for ext in ('in','out'):
                    path=case_dir/f'{i}.{ext}'
                    if not path.is_file() or file_hash(path)!=record[ext]:
                        raise PipelineError('未完成阶段的已验证测试点发生改动；请使用新输出目录')
                cases.append(record['row'])
                print(f'  恢复已完成测试点 {i}/{self.args.points}',flush=True)
                continue
            print(f'  生成测试点 {i}/{self.args.points}',flush=True)
            text,meta,scale=self.generate_one(i)
            # Check every generator result twice, including metadata.
            text2,meta2,scale2=self.generate_one(i)
            if (text,meta,scale)!=(text2,meta2,scale2): raise PipelineError('生成器不确定，不能复现相同数据')
            for group in spec['subtasks']:
                if i in group['cases']:
                    if meta.get('subtask')!=group['id']: raise PipelineError('生成器子任务归属错误')
                    self.worker({'op':'subtask','input':text,'subtask':group['id']})
            tags=meta.get('coverage')
            if not isinstance(tags,list) or any(not isinstance(t,str) or t not in spec['coverage'] for t in tags):
                raise PipelineError('正式数据缺少合法覆盖标签')
            self.worker({'op':'coverage','input':text,'tags':tags})
            output,seconds=self.solve(text)
            self.validate_output(text,output)
            atomic_write(case_dir/f'{i}.in',text)
            atomic_write(case_dir/f'{i}.out',output)
            cases.append({'id':i,'method':meta['method'],'coverage':tags,'scale':scale,'seconds':round(seconds,6),
                          'input_bytes':len(text.encode()),'output_bytes':len(output.encode())})
            progress[str(i)]={'in':file_hash(case_dir/f'{i}.in'),'out':file_hash(case_dir/f'{i}.out'),'row':cases[-1]}
            save_json(self.root/'state.json',self.state)
        save_json(self.root/'cases.json',cases)
        self.mark('generate',[self.root/'cases.json',*sorted(case_dir.iterdir())])

    def compare(self, text: str, expected: str|None=None):
        actual=brute=None
        try:
            self.validate_input(text)
            actual,_=self.solve(text)
            self.validate_output(text,actual)
            brute,_=self.solve(text,brute=True)
            self.validate_output(text,brute)
            if not judge_equal(actual,brute) or (expected is not None and not judge_equal(actual,expected)):
                raise PipelineError('样例/独立暴力对拍失败，反例保存在 failure/')
        except Exception:
            failure=self.root/'failure'
            failure.mkdir(exist_ok=True)
            atomic_write(failure/'counterexample.in',text)
            if actual is not None: atomic_write(failure/'standard.out',actual)
            if brute is not None: atomic_write(failure/'brute.out',brute)
            if expected is not None: atomic_write(failure/'expected.out',expected)
            raise

    def verify(self):
        samples=load_json(self.root/'parsed.json')['samples']
        # Samples can exceed brute limits: sample expected output is its own oracle.
        for sample in samples:
            self.validate_input(sample['input'])
            actual,_=self.solve(sample['input'])
            self.validate_output(sample['input'],actual)
            if not judge_equal(actual,sample['output']):
                failure=self.root/'failure'; failure.mkdir(exist_ok=True)
                atomic_write(failure/'sample.in',sample['input'])
                atomic_write(failure/'actual.out',actual)
                atomic_write(failure/'expected.out',sample['output'])
                raise PipelineError('标准解与题面样例不一致')
        for i in range(self.state.get('stress_completed',0),self.args.stress):
            if i%20==0: print(f'  边界/随机暴力对拍 {i}/{self.args.stress}',flush=True)
            text,meta,scale=self.generate_one(i,small=True)
            again=self.generate_one(i,small=True)
            if (text,meta,scale)!=again: raise PipelineError('小数据生成器不确定')
            self.compare(text)
            self.state['stress_completed']=i+1
            save_json(self.root/'state.json',self.state)
        for i in range(1,self.args.points+1):
            text=(self.root/'cases'/f'{i}.in').read_text(encoding='utf-8')
            output=(self.root/'cases'/f'{i}.out').read_text(encoding='utf-8')
            self.validate_input(text)
            self.validate_output(text,output)
            again,_=self.solve(text)
            if again!=output: raise PipelineError('标准解结果不可复现')
        spec=load_json(self.root/'spec.json')
        covered={tag for row in load_json(self.root/'cases.json') for tag in row['coverage']}
        missing=set(spec['coverage'])-covered
        if missing: raise PipelineError('测试点未完成覆盖计划：'+','.join(sorted(missing)))
        mutations=[]
        for wrong in spec['wrong_solutions']:
            killed=None
            for i in range(1,self.args.points+1):
                text=(self.root/'cases'/f'{i}.in').read_text(encoding='utf-8')
                expected=(self.root/'cases'/f'{i}.out').read_text(encoding='utf-8')
                try:
                    actual,_=run([str(self.root/Path(wrong['file']).with_suffix('.exe'))],cwd=self.root,
                                 input_text=text,timeout=max(2,spec['time_ms']/1000*3),memory_mb=spec['memory_mb'],max_bytes=self.policy['max_file_bytes'])
                    reason='WA' if not judge_equal(actual,expected) else None
                except PipelineError as exc:
                    reason='运行失败或超限：'+str(exc)
                if reason:
                    killed={'file':wrong['file'],'description':wrong['description'],'case':i,'result':reason}
                    break
            if killed is None: raise PipelineError('数据强度检查失败，错误解未被正式数据击败：'+wrong['file'])
            mutations.append(killed)
        atomic_write(self.root/'config.yml',config_text(spec,self.args.points))
        save_json(self.root/'scoring.json',scoring_scripts(spec))
        members(self.root,self.args.points,self.policy)
        save_json(self.root/'validation.json',{'passed':True,'samples':len(samples),'differential_cases':self.args.stress,
            'input_output_pairs':self.args.points,'generator_determinism':True,'standard_determinism':True,
            'official_rules_verified':self.policy.get('official_rules_verified',False),
            'coverage':sorted(covered),'wrong_solutions':mutations,'scoring_scripts_required':bool(spec['subtasks']),
            'scope':'本地样例、输入约束、格式、独立小规模对拍和重复运行；非形式化证明、非洛谷验收'})
        self.mark('verify',[self.root/'validation.json',self.root/'config.yml',self.root/'scoring.json'])

    def package(self):
        self.check_artifacts()
        if 'verify' not in self.state['stages'] or load_json(self.root/'validation.json').get('passed') is not True:
            raise PipelineError('未完成验证，禁止打包')
        pack(self.root,self.args.points,self.policy)
        self.mark('package',[self.root/'testcases.zip'])

    def report(self):
        status=self.state.get('status','failed')
        lines=['# 测试数据生成报告','',f'状态：**{status}**',
               f'题面 SHA256：`{self.state.get("fingerprint",{}).get("statement", "unknown")}`',
               f'随机种子：{self.args.seed}；测试点：{self.args.points}',
               '完成阶段：'+', '.join(self.state.get('stages',{})), '']
        if status!='passed': lines+=['**尚未通过完整流水线，不能将当前数据视为已验证压缩包。**','']
        if self.state.get('error'): lines+=['失败原因：', '```text',self.state['error'],'```','']
        if 'analyze' in self.state.get('stages',{}) and (self.root/'spec.json').exists():
            spec=load_json(self.root/'spec.json')
            if spec['subtasks']:
                lines += ['**本题有 Subtask：scoring.json 中的组内及整题计分脚本必须在洛谷配置页应用并核验；config.yml 无法携带计分脚本。**','']
            lines += ['## 解法与验证','',spec['algorithm'],'',spec['proof'],'',
                      '独立暴力：'+spec['brute_algorithm'],'',
                      f'配置：{spec["time_ms"]}ms，{spec["memory_mb"]}MB。{spec["limits_source"]}',
                      '本机运行限制允许至少2秒或配置时间的3倍；耗时不可直接推算洛谷时限。',
                      '内存为进程资源上限；尚未采集峰值内存，不能据此证明满足评测机内存限制。','',
                      '覆盖计划：'+'；'.join(spec['coverage']),'','## 风险','',
                      *['- '+r for r in spec['risks']]]
        lines += ['','- 生成的 Python/C++ 在本机执行。超时、输出限制与 Job Object/rlimit 不是安全沙箱；建议在独立 Windows 账户或 Windows Sandbox 中运行不可信模型代码。',
                  '- 通用题面的完整语义与约束依赖模型分析及复核，可能有共同遗漏；发现疑问必须停用产物。',
                  '- 模型温度为0仍不保证重新请求返回相同代码。严格复现请保留本次源码、state.json，并使用 --resume；相同源码、种子、工具链下数据可复现。',
                  '- 最新洛谷规则在线核实状态：'+str(self.policy.get('official_rules_verified',False))+'。'+str(self.policy.get('note','')),
                  '- 官方参考：'+str(self.policy.get('official_url','')),
                  f'- 本地上限：ZIP {self.policy["max_zip_bytes"]} 字节、单文件 {self.policy["max_file_bytes"]} 字节、总解压 {self.policy["max_total_bytes"]} 字节。',
                  '- 本报告的 passed 仅指本地验证通过，不代表全部隐藏边界正确或已在洛谷上传验收。']
        if (self.root/'validation.json').exists() and 'verify' in self.state.get('stages',{}):
            lines+=['','## 验证结果','','```json',json.dumps(load_json(self.root/'validation.json'),ensure_ascii=False,indent=2),'```']
        if 'generate' in self.state.get('stages',{}) and (self.root/'cases.json').exists():
            lines+=['','## 测试点','','| 点 | 规模 | 构造 | 输入字节 | 本机耗时(s) |','|---|---|---|---:|---:|']
            for row in load_json(self.root/'cases.json'):
                clean=lambda s:str(s).replace('|','\\|').replace('\n',' ')
                lines.append(f'| {row["id"]} | {clean(json.dumps(row["scale"],ensure_ascii=False))} | {clean(row["method"])} | {row["input_bytes"]} | {row["seconds"]} |')
        if 'package' in self.state.get('stages',{}) and (self.root/'testcases.zip').exists():
            lines+=['',f'ZIP SHA256：`{file_hash(self.root/"testcases.zip")}`']
        atomic_write(self.root/'report.md','\n'.join(lines)+'\n')

    def execute(self):
        with locked(self.root):
            self.statement=self.problem.read_text(encoding='utf-8-sig')
            code_hash=digest(b''.join(p.read_bytes() for p in sorted(Path(__file__).parent.glob('*.py'))))
            fingerprint={'statement':digest(self.statement.encode()),'points':self.args.points,'seed':self.args.seed,
                         'stress':self.args.stress,'provider':self.args.provider,'base_url':self.args.base_url,
                         'model':self.args.model,'review_model':getattr(self.args,'review_model',None),'cxx':self.args.cxx,'policy':self.policy,'pipeline':code_hash}
            state_path=self.root/'state.json'
            if state_path.exists():
                self.state=load_json(state_path)
                if not self.args.resume:
                    raise PipelineError('输出目录已存在；使用 --resume 恢复，或使用新 --output 目录')
                if self.state.get('fingerprint')!=fingerprint:
                    raise PipelineError('题面、配置或流水线代码已变化；请使用新的输出目录')
            else:
                if any(p.name!='.lock' for p in self.root.iterdir()):
                    raise PipelineError('拒绝覆盖非空且非本工具管理的输出目录')
                self.state={'version':1,'fingerprint':fingerprint,'stages':{},'status':'in_progress'}
                save_json(state_path,self.state)
            try:
                if self.state.get('invalidated'):
                    raise PipelineError('之前的产物完整性检查失败；必须使用新输出目录，不能重新认可被修改的文件')
                self.check_artifacts()
                for stage in STAGES:
                    if stage not in self.state['stages']:
                        print('阶段：'+stage,flush=True)
                        getattr(self,stage)()
                    if stage==self.args.until: break
                self.state['status']='passed' if 'package' in self.state['stages'] else 'partial'
                self.state.pop('error',None)
                save_json(state_path,self.state)
                self.report()
                return self.root
            except (Exception,KeyboardInterrupt) as exc:
                (self.root/'testcases.zip').unlink(missing_ok=True)
                self.state['stages'].pop('package',None)
                self.state['status']='failed'
                self.state['error']=str(exc) or '用户中断'
                save_json(state_path,self.state)
                self.report()
                raise
