import asyncio
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from luogu_data import builtin
from luogu_data.cli import parser
from luogu_data.common import PipelineError, check_input_format, judge_equal, load_json, save_json
from luogu_data.pipeline import Pipeline
from luogu_data.provider import obtain
from luogu_data.settings import read_settings
from luogu_data.watch import watch

ROOT=Path(__file__).resolve().parents[1]
EXAMPLE=ROOT/'examples/problem.md'
STATEMENT=EXAMPLE.read_text(encoding='utf-8')


class StrictFormats(unittest.TestCase):
    def test_directory_watch_waits_for_stability_and_processes_each_version_once(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'inbox';folder.mkdir()
            problem=folder/'problem.md';problem.write_text('first',encoding='utf-8')
            args=parser().parse_args([str(folder),'--watch','--watch-interval','0.2'])
            iterations=0
            def tick(_):
                nonlocal iterations
                iterations+=1
                if iterations==3: problem.write_text('second',encoding='utf-8')
                if iterations==6: raise KeyboardInterrupt
            with patch('luogu_data.watch.time.sleep',tick),patch('luogu_data.watch.Pipeline') as pipeline,redirect_stdout(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt): watch(folder,Path(directory)/'out',args)
            self.assertEqual(pipeline.call_count,2)
            paths=[call.args[1] for call in pipeline.call_args_list]
            self.assertNotEqual(paths[0],paths[1])

    def test_whole_text_comparison_preserves_structure(self):
        self.assertTrue(judge_equal('1 2  \n3\n\n','1 2\n3'))
        self.assertFalse(judge_equal('1 2\n','1\n2\n'))
        self.assertFalse(judge_equal('1  2\n','1 2\n'))

    def test_official_ascii_space_and_newline_checks(self):
        check_input_format('2\n1 2\n')
        for text in ['','1','1\r\n','1\n\n',' 1\n','1  \n','1  2\n','1\t2\n','中文\n']:
            with self.assertRaises(PipelineError): check_input_format(text)
        check_input_format('1\n\n',{'blank_lines':'题目允许空行'})

    def test_settings_rejects_secret_and_wrong_types(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.toml'
            path.write_text('[model]\napi_key="not-a-real-secret"\n')
            with self.assertRaises(PipelineError): read_settings(path)
            path.write_text('[generation]\npoints="20"\n')
            with self.assertRaises(PipelineError): read_settings(path)


class APIProtocol(unittest.TestCase):
    def test_real_http_staged_generation_independent_brute_and_cache(self):
        requests=[]
        bundle=builtin.bundle(STATEMENT,2)
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                value=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append(value)
                user=json.loads(value['messages'][1]['content'])
                if 'requested_file' in user:
                    answer={'source':bundle['sources'][user['requested_file']]}
                    if user['requested_file'] in ('brute.cpp','validator.py'):
                        if 'reference' in user: raise AssertionError('standard leaked to independent generator')
                elif 'spec' in user or 'candidate' in user:
                    answer={'approved':True,'issues':[],'method':'模拟服务协议测试'}
                else: answer={'spec':bundle['spec']}
                body=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(answer)}}]}).encode()
                self.send_response(200); self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory,redirect_stdout(io.StringIO()):
                args=(STATEMENT,2,'api',f'http://127.0.0.1:{server.server_port}/v1','mock')
                first=obtain(*args,cache=Path(directory))
                count=len(requests)
                self.assertEqual(count,9)
                second=obtain(*args,cache=Path(directory))
                self.assertEqual(len(requests),count)
                self.assertEqual(first,second)
                cache=Path(directory)/'01-spec.json'
                value=load_json(cache); value['response']['spec']['title']='tampered'; save_json(cache,value)
                with self.assertRaises(PipelineError): obtain(*args,cache=Path(directory))
        finally: server.shutdown();server.server_close();thread.join()


@unittest.skipUnless(shutil.which('g++'),'g++ required')
class RecoveryAndStrength(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.output=Path(self.temp.name)/'out'
        self.args=parser().parse_args([str(EXAMPLE),'--points','2','--stress','20','--output',str(self.output)])

    def test_resume_individual_cases_and_stress_checks(self):
        args=self.args; args.until='generate'
        original=Pipeline.solve
        calls=0
        def interrupted(instance,text,brute=False):
            nonlocal calls
            calls+=1
            if calls==2: raise PipelineError('模拟中断')
            return original(instance,text,brute)
        with redirect_stdout(io.StringIO()),patch.object(Pipeline,'solve',interrupted):
            with self.assertRaisesRegex(PipelineError,'模拟中断'): Pipeline(EXAMPLE,self.output,args).execute()
        original_time=(self.output/'cases/1.in').stat().st_mtime_ns
        args.resume=True
        with redirect_stdout(io.StringIO()): Pipeline(EXAMPLE,self.output,args).execute()
        self.assertEqual(original_time,(self.output/'cases/1.in').stat().st_mtime_ns)
        args.until='verify'; compare=Pipeline.compare; calls=0
        def stop_stress(instance,text,expected=None):
            nonlocal calls
            calls+=1
            if calls==3: raise PipelineError('对拍中断')
            return compare(instance,text,expected)
        with redirect_stdout(io.StringIO()),patch.object(Pipeline,'compare',stop_stress):
            with self.assertRaisesRegex(PipelineError,'对拍中断'): Pipeline(EXAMPLE,self.output,args).execute()
        self.assertEqual(load_json(self.output/'state.json')['stress_completed'],2)
        with redirect_stdout(io.StringIO()): Pipeline(EXAMPLE,self.output,args).execute()
        result=load_json(self.output/'validation.json')
        self.assertEqual(result['differential_cases'],20)
        self.assertEqual(len(result['wrong_solutions']),2)

    def test_unkilled_wrong_solution_rejects_data(self):
        value=builtin.bundle(STATEMENT,2)
        value['sources']['wrong_2.cpp']=value['sources']['standard.cpp']
        with redirect_stdout(io.StringIO()),patch('luogu_data.pipeline.obtain',return_value=value):
            with self.assertRaisesRegex(PipelineError,'错误解未被正式数据击败'):
                Pipeline(EXAMPLE,self.output,self.args).execute()
        self.assertFalse((self.output/'testcases.zip').exists())
