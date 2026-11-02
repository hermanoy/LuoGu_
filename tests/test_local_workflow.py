from contextlib import redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from luogu_data.cli import parser, main
from luogu_data.common import load_json
from luogu_data.diagnostics import diagnose, safe_endpoint
from luogu_data.watch import watch

ROOT=Path(__file__).resolve().parents[1]


class LocalWorkflow(unittest.TestCase):
    def test_diagnostic_url_never_exposes_credentials(self):
        url='https://user:private@example.com/v1?api_key=private#private'
        safe=safe_endpoint(url)
        self.assertEqual(safe,'https://example.com/v1')
        self.assertNotIn('private',safe)

    @unittest.skipUnless(shutil.which('g++'),'g++ required')
    def test_doctor_compiles_and_executes_without_model_call(self):
        args=parser().parse_args(['--doctor'])
        with patch('luogu_data.diagnostics.Client') as client:
            result=diagnose(args)
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['checks'][0]['name'],'cpp17_compile_run_unicode_path')
        client.assert_not_called()

    def test_missing_compiler_diagnostic_returns_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'diagnostic.json'
            with redirect_stdout(io.StringIO()):
                result=main(['--doctor','--cxx','definitely-missing-cxx','--diagnostic-report',str(path)])
            self.assertEqual(result,1)
            self.assertEqual(load_json(path)['checks'][0]['status'],'failed')

    @unittest.skipUnless(shutil.which('g++'),'g++ required')
    def test_explicit_api_check_checks_both_models_and_rejects_bad_json(self):
        args=parser().parse_args(['--test-api','--model','generator','--review-model','reviewer'])
        with patch('luogu_data.diagnostics.Client') as client:
            client.return_value.ask.side_effect=[{'ok':True},{'ok':False}]
            result=diagnose(args,test_api=True)
        self.assertEqual(result['status'],'failed')
        self.assertEqual([c.args[1] for c in client.call_args_list],['generator','reviewer'])
        self.assertEqual([c['status'] for c in result['checks']],['passed','passed','failed'])

    def test_watch_configuration_change_selects_new_output(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'inbox';folder.mkdir()
            (folder/'problem.md').write_text('same statement',encoding='utf-8')
            destinations=[]
            for points in (20,30,20):
                args=parser().parse_args([str(folder),'--points',str(points)])
                count=0
                def tick(_):
                    nonlocal count
                    count+=1
                    if count==2: raise KeyboardInterrupt
                with patch('luogu_data.watch.time.sleep',tick),patch('luogu_data.watch.Pipeline') as pipeline,redirect_stdout(io.StringIO()):
                    with self.assertRaises(KeyboardInterrupt): watch(folder,Path(directory)/'out',args)
                    destinations.append(pipeline.call_args.args[1])
            self.assertNotEqual(destinations[0],destinations[1])
            self.assertEqual(destinations[0],destinations[2])

    def test_launcher_prompt_key_is_removed_after_run(self):
        spec=importlib.util.spec_from_file_location('launch_under_test',ROOT/'scripts/launch.py')
        launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
        original=Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as directory,patch.object(launcher,'ROOT',Path(directory)),patch.dict(os.environ,{},clear=True),patch('sys.stdin.isatty',return_value=True),patch.object(launcher.getpass,'getpass',return_value='test-only-key') as prompt,patch.object(launcher,'main',side_effect=[0,0]) as command:
                self.assertEqual(launcher.launch(),0)
                self.assertNotIn('LLM_API_KEY',os.environ)
                self.assertEqual(command.call_count,2)
                self.assertEqual(list(Path(directory).iterdir()),[])
                prompt.assert_called_once()
        finally: os.chdir(original)

    def test_launcher_reuses_existing_key_without_prompt(self):
        spec=importlib.util.spec_from_file_location('launch_existing',ROOT/'scripts/launch.py')
        launcher=importlib.util.module_from_spec(spec);spec.loader.exec_module(launcher)
        original=Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as directory,patch.object(launcher,'ROOT',Path(directory)),patch.dict(os.environ,{'LLM_API_KEY':'existing-test-key'},clear=True),patch.object(launcher.getpass,'getpass') as prompt,patch.object(launcher,'main',side_effect=[0,0]):
                self.assertEqual(launcher.launch(),0)
                self.assertEqual(os.environ['LLM_API_KEY'],'existing-test-key')
                prompt.assert_not_called()
        finally: os.chdir(original)
