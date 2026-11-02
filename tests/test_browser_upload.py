import asyncio
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import importlib.util
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
import zipfile

from luogu_data import builtin
from luogu_data.common import PipelineError,load_json,save_json
from luogu_data.packaging import config_text, expected_config, scoring_scripts
from luogu_data.uploader import submit_and_verify

ADAPTER={'file_input':'input[type=file]','success_indicator':'#done','rows':'tbody tr','filename':'.filename',
         'timeLimit':'input[name=timeLimit]','memoryLimit':'input[name=memoryLimit]','score':'input[name=score]',
         'download_button':'#download','verification_timeout':0.2}


@unittest.skipUnless(importlib.util.find_spec('playwright'),'optional Playwright not installed')
class BrowserUpload(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from playwright.async_api import async_playwright
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.playwright=await async_playwright().start()
        executable=shutil.which('chromium') or self.playwright.chromium.executable_path
        if not Path(executable).exists():
            await self.playwright.stop(); self.temp.cleanup(); self.skipTest('Chromium not installed')
        self.browser=await self.playwright.chromium.launch(headless=True,executable_path=executable)
        spec=builtin.bundle('',2)['spec']
        save_json(self.root/'spec.json',spec)
        save_json(self.root/'state.json',{'fingerprint':{'points':2}})
        save_json(self.root/'scoring.json',{})
        with zipfile.ZipFile(self.root/'testcases.zip','w') as z:
            z.writestr('config.yml',config_text(spec,2))
            for i in (1,2):
                z.writestr(f'{i}.in','1\n1\n'); z.writestr(f'{i}.out','1\n')
        self.uploads=[]; self.bad_score=False; self.bad_download=False; self.scripts={}
        test=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                raw=self.rfile.read(int(self.headers['Content-Length']))
                if self.path=='/scripts':
                    import json
                    test.scripts=json.loads(raw)
                else: test.uploads.append(raw)
                self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
            def do_GET(self):
                if self.path=='/download':
                    raw=test.uploads[-1]
                    if test.bad_download:
                        import io
                        buf=io.BytesIO()
                        with zipfile.ZipFile(buf,'w') as z:
                            z.writestr('unexpected.in','0\n')
                        raw=buf.getvalue()
                    self.send_response(200);self.send_header('Content-Type','application/zip')
                    self.send_header('Content-Disposition','attachment; filename=data.zip')
                    self.end_headers();self.wfile.write(raw);return
                rows=''
                if test.uploads:
                    configs=expected_config(load_json(test.root/'spec.json'),2)
                    for i in (1,2):
                        values=configs[f'{i}.in']
                        rows+=f'<tr><td class="filename">{i}.in</td>'
                        for key,value in values.items():
                            if test.bad_score and key=='score': value=49
                            rows+=f'<td><input name="{key}" value="{value}"></td>'
                        rows+='</tr>'
                import html
                scripts=''.join(f'<textarea id="script-{key}" data-script="{key}">{html.escape(test.scripts.get(key,""))}</textarea>' for key in load_json(test.root/'scoring.json'))
                scripts+='''<button id="save-scripts" onclick="fetch('/scripts',{method:'POST',body:JSON.stringify(Object.fromEntries(Array.from(document.querySelectorAll('[data-script]')).map(x=>[x.dataset.script,x.value])))}).then(()=>document.querySelector('#script-done').hidden=false)">save</button><div id="script-done" hidden>saved</div>'''
                html='''<html><body><h1>U1 fixture</h1>
<input type="file" onchange="fetch('/upload',{method:'POST',body:this.files[0]}).then(()=>document.querySelector('#done').hidden=false)">
<div id="done" hidden>success</div><table><tbody>'''+rows+'''</tbody></table><a id="download" href="/download">download</a>'''+scripts+'''</body></html>'''
                raw=html.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.page=await self.browser.new_page(accept_downloads=True)
        self.url=f'http://127.0.0.1:{self.server.server_port}/problem/U1'
        await self.page.goto(self.url)

    async def asyncTearDown(self):
        if hasattr(self,'server'):
            await asyncio.to_thread(self.server.shutdown);self.server.server_close();self.thread.join()
        if hasattr(self,'browser'): await self.browser.close()
        await self.playwright.stop();self.temp.cleanup()

    async def guard(self):
        if self.page.url!=self.url: raise PipelineError('测试目标页面改变')

    async def test_decline_never_selects_file_or_sends_request(self):
        async def decline(sha): return False
        with self.assertRaisesRegex(PipelineError,'未确认'):
            await submit_and_verify(self.page,self.root,ADAPTER,decline,target_guard=self.guard)
        self.assertEqual(self.uploads,[])

    async def test_upload_refresh_config_and_download_bytes(self):
        async def approve(sha):
            self.assertEqual(self.uploads,[])
            return True
        result=await submit_and_verify(self.page,self.root,ADAPTER,approve,target_guard=self.guard)
        self.assertEqual(len(self.uploads),1)
        self.assertEqual(result['status'],'verified')
        self.assertTrue(result['data_bytes_verified'])

    async def test_wrong_persisted_config_is_not_success(self):
        self.bad_score=True
        async def approve(sha): return True
        with self.assertRaisesRegex(PipelineError,'验收未通过'):
            await submit_and_verify(self.page,self.root,ADAPTER,approve,target_guard=self.guard)
        self.assertEqual(load_json(self.root/'upload-receipt.json')['status'],'uploaded_unverified')
        self.assertEqual(len(self.uploads),1)

    async def test_wrong_download_is_not_success(self):
        self.bad_download=True
        async def approve(sha): return True
        with self.assertRaisesRegex(PipelineError,'验收未通过'):
            await submit_and_verify(self.page,self.root,ADAPTER,approve,target_guard=self.guard)
        self.assertEqual(load_json(self.root/'upload-receipt.json')['status'],'uploaded_unverified')

    async def test_subtask_scripts_persist_and_are_checked_after_reload(self):
        spec=load_json(self.root/'spec.json')
        spec['subtasks']=[{'id':1,'score':30,'cases':[1],'depends':[]},
                          {'id':2,'score':70,'cases':[2],'depends':[1]}]
        save_json(self.root/'spec.json',spec)
        scripts=scoring_scripts(spec);save_json(self.root/'scoring.json',scripts)
        await self.page.reload()
        adapter=dict(ADAPTER,subtaskId='input[name=subtaskId]',script_fields={k:'#script-'+k for k in scripts},
                     script_save='#save-scripts',script_success='#script-done')
        async def approve(sha): return True
        result=await submit_and_verify(self.page,self.root,adapter,approve,target_guard=self.guard)
        self.assertEqual(self.scripts,scripts)
        self.assertEqual(result['status'],'verified')

    async def test_changed_file_after_confirmation_blocks_upload(self):
        async def approve(sha):
            (self.root/'testcases.zip').write_bytes(b'changed')
            return True
        with self.assertRaisesRegex(PipelineError,'已改变'):
            await submit_and_verify(self.page,self.root,ADAPTER,approve,target_guard=self.guard)
        self.assertEqual(self.uploads,[])
