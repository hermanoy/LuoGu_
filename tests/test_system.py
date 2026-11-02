from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from luogu_data import builtin
from luogu_data.cli import main, parser
from luogu_data.common import PipelineError, file_hash, load_json
from luogu_data.packaging import config_text, pack, scoring_scripts, expected_config
from luogu_data.pipeline import Pipeline
from luogu_data.provider import Client, obtain, validate_bundle
from luogu_data.runner import run
from luogu_data.statement import parse
from luogu_data.uploader import preview

ROOT=Path(__file__).resolve().parents[1]
EXAMPLE=ROOT/'examples/problem.md'
STATEMENT=EXAMPLE.read_text(encoding='utf-8')


class Contracts(unittest.TestCase):
    def test_markdown_math_and_samples(self):
        document=parse(STATEMENT)
        self.assertIn(r'\le',document['markdown'])
        self.assertEqual(document['samples'][0]['output'],'6\n')
        self.assertEqual(parse(STATEMENT.replace('```','~~~~'))['samples'],document['samples'])

    def test_unmatched_samples_and_fences_rejected(self):
        for text in ('# test', '```input\n1\n', '```output\n1\n```'):
            with self.assertRaises(PipelineError): parse(text)

    def test_missing_constraints_and_ambiguity_rejected(self):
        for change in ({'constraints':[]},{'ambiguities':['n 的范围缺失']},{'unique_output':False}):
            value=builtin.bundle(STATEMENT)
            value['spec'].update(change)
            with self.assertRaises(PipelineError): validate_bundle(value,STATEMENT,20)
        value=builtin.bundle(STATEMENT)
        value['spec']['constraints'][0]['evidence']='题面没有这个条件'
        with self.assertRaises(PipelineError): validate_bundle(value,STATEMENT,20)

    def test_builtin_does_not_guess(self):
        with self.assertRaises(PipelineError): obtain(STATEMENT+'\n范围不明确','20','builtin','','')

    def test_input_validator_rejects_illegal_data(self):
        module={}
        exec(builtin.VALIDATOR,module)
        for text in ('0\n\n','2\n1\n','1\n1000000001\n','1\n1.0\n','2\n1 2 3\n','1 3\n'):
            with self.assertRaises(ValueError): module['validate_input'](text)

    def test_official_config_and_dependency_scripts(self):
        spec=builtin.bundle(STATEMENT)['spec']
        config=config_text(spec,20)
        self.assertEqual(config.count('score: 5\n'),20)
        scores=[int(x.split(':')[1]) for x in config_text(spec,7).splitlines() if 'score:' in x]
        self.assertEqual(sum(scores),100)
        self.assertIn('1.in:\n  timeLimit: 1000\n  memoryLimit: 262144\n  score: 5',config)
        self.assertNotIn('subtaskId',config)
        spec['subtasks']=[{'id':1,'score':30,'cases':[1],'depends':[]},{'id':2,'score':70,'cases':[2],'depends':[1]}]
        self.assertEqual(expected_config(spec,2)['2.in']['subtaskId'],2)
        scripts=scoring_scripts(spec)
        self.assertIn('@status2 == AC and @status1 == AC',scripts['total'])
        self.assertIn('@total_score = 30;',scripts['1'])

    def test_api_json_contract_and_truncation(self):
        def response(reason='stop'):
            return io.BytesIO(json.dumps({'choices':[{'finish_reason':reason,'message':{'content':'{"ok":true}'}}]}).encode())
        with patch('urllib.request.OpenerDirector.open',return_value=response()):
            self.assertEqual(Client('http://localhost:1234/v1','test').ask('system','user'),{'ok':True})
        with patch('urllib.request.OpenerDirector.open',return_value=response('length')):
            with self.assertRaises(PipelineError): Client('http://localhost:1234/v1','test').ask('system','user')
        with self.assertRaises(PipelineError): Client('http://example.com','test')
        with self.assertRaises(PipelineError): Client('https://key@example.com','test')

    def test_review_can_reject_otherwise_valid_code(self):
        with patch.object(Client,'ask',side_effect=[builtin.bundle(STATEMENT),{'approved':False,'issues':['算法错误']} ]):
            with self.assertRaises(PipelineError): obtain(STATEMENT,20,'api','http://localhost:1234','mock')

    def test_subprocess_timeout(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(PipelineError,'超时'):
                run([sys.executable,'-c','while True: pass'],cwd=Path(tmp),timeout=0.1)


@unittest.skipUnless(shutil.which('g++'),'C++17 compiler required')
class Integration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output=Path(self.temp.name)/'result'
        self.args=parser().parse_args([str(EXAMPLE),'--points','2','--stress','20','--output',str(self.output)])

    def execute(self):
        with redirect_stdout(io.StringIO()):
            return Pipeline(EXAMPLE,self.output,self.args).execute()

    def test_stages_resume_zip_reproducibility_and_tamper(self):
        self.args.until='generate'
        self.execute()
        self.assertEqual(load_json(self.output/'state.json')['status'],'partial')
        self.assertFalse((self.output/'testcases.zip').exists())
        self.args.until='package'; self.args.resume=True
        self.execute()
        self.assertEqual(load_json(self.output/'validation.json')['differential_cases'],20)
        with zipfile.ZipFile(self.output/'testcases.zip') as z:
            self.assertEqual(set(z.namelist()),{'config.yml','1.in','1.out','2.in','2.out'})
            self.assertIsNone(z.testzip())
        first=file_hash(self.output/'testcases.zip')
        pack(self.output,2,load_json(ROOT/'luogu_data/policy.json'))
        self.assertEqual(first,file_hash(self.output/'testcases.zip'))
        self.execute()
        preview_result=preview(self.output,'https://www.luogu.com.cn/problem/P1000')
        self.assertFalse(preview_result['upload_enabled'])
        (self.output/'cases/1.out').write_text('0\n')
        with self.assertRaises(PipelineError): self.execute()
        self.assertEqual(load_json(self.output/'state.json')['status'],'failed')
        self.assertFalse((self.output/'testcases.zip').exists())
        with self.assertRaises(PipelineError): preview(self.output,'https://www.luogu.com.cn/problem/P1000')

    def test_wrong_solution_stops_packaging(self):
        bundle=builtin.bundle(STATEMENT,1)
        # Pass the provided sample but fail all-negative small boundary cases.
        bundle['sources']['standard.cpp']=builtin.STANDARD.replace('cout<<best', 'best=max(0LL,best); cout<<best')
        self.args.points=1
        with patch('luogu_data.pipeline.obtain',return_value=bundle):
            with self.assertRaisesRegex(PipelineError,'对拍失败'): self.execute()
        self.assertFalse((self.output/'testcases.zip').exists())
        self.assertTrue((self.output/'failure/counterexample.in').exists())
        self.assertIn('failed',(self.output/'report.md').read_text(encoding='utf-8'))

    def test_analysis_failure_produces_report(self):
        with patch('luogu_data.pipeline.obtain',side_effect=PipelineError('缺少关键约束')):
            with self.assertRaises(PipelineError): self.execute()
        self.assertIn('缺少关键约束',(self.output/'report.md').read_text(encoding='utf-8'))
        self.assertFalse((self.output/'testcases.zip').exists())

    def test_corrupt_metadata_invalidates_previous_verification(self):
        self.args.until='analyze'
        self.execute()
        (self.output/'spec.json').write_text('{broken')
        self.args.resume=True
        with self.assertRaisesRegex(PipelineError,'产物已修改'): self.execute()
        self.assertEqual(load_json(self.output/'state.json')['stages'],{})
        self.assertIn('failed',(self.output/'report.md').read_text(encoding='utf-8'))
        with self.assertRaisesRegex(PipelineError,'必须使用新输出目录'): self.execute()

    def test_batch_continues_after_failure(self):
        folder=Path(self.temp.name)/'problems'
        for name in ('a','b'): (folder/name).mkdir(parents=True)
        (folder/'a/problem.md').write_text('# 缺少输入输出约束',encoding='utf-8')
        (folder/'b/problem.md').write_text(STATEMENT,encoding='utf-8')
        with redirect_stdout(io.StringIO()), patch('sys.stderr',new_callable=io.StringIO):
            status=main([str(folder),'--output',str(self.output),'--until','analyze'])
        self.assertEqual(status,1)
        self.assertEqual(load_json(self.output/'a/problem/state.json')['status'],'failed')
        self.assertEqual(load_json(self.output/'b/problem/state.json')['status'],'partial')

    def test_preserve_unknown_files(self):
        self.output.mkdir()
        user=self.output/'notes.txt'; user.write_text('keep')
        with self.assertRaises(PipelineError): self.execute()
        self.assertEqual(user.read_text(encoding='utf-8'),'keep')


if __name__=='__main__': unittest.main()
