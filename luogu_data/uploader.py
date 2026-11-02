"""Opt-in browser upload with explicit confirmation and persisted-data verification."""
from __future__ import annotations
import asyncio
import json
from pathlib import Path
import re
import tempfile
import time
import zipfile
from .common import PipelineError, file_hash, load_json, save_json
from .packaging import expected_config


def problem_id(url):
    match=re.fullmatch(r'https://www\.luogu\.com\.cn/problem/([PTU][0-9]+)',url)
    if not match: raise PipelineError('必须使用明确的洛谷题目 URL：https://www.luogu.com.cn/problem/U123456')
    return match[1]


def preview(root: Path, problem_url: str):
    problem_id(problem_url)
    state=load_json(root/'state.json')
    if state.get('status')!='passed' or 'package' not in state.get('stages',{}):
        raise PipelineError('只有完整验证通过的产物可进入上传流程')
    for stage in state['stages'].values():
        for name,expected in stage.items():
            path=root/name
            if not path.resolve().is_relative_to(root.resolve()) or not path.is_file() or path.is_symlink() or file_hash(path)!=expected:
                raise PipelineError('产物完整性检查失败，拒绝上传：'+name)
    archive=root/'testcases.zip'
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None: raise PipelineError('ZIP 校验失败')
        files=[{'name':i.filename,'bytes':i.file_size} for i in z.infolist()]
    return {'target':problem_url,'title':load_json(root/'spec.json')['title'],
            'zip':str(archive.resolve()),'sha256':file_hash(archive),'bytes':archive.stat().st_size,
            'files':files,'config':(root/'config.yml').read_text(encoding='utf-8'),
            'scoring':load_json(root/'scoring.json'),'upload_enabled':False,
            'message':'预览不上传；实际提交需本地浏览器登录和本次明确确认。'}


def validate_adapter(adapter,scoring):
    for key in ('file_input','rows','filename','timeLimit','memoryLimit','score','download_button','success_indicator'):
        if not isinstance(adapter.get(key),str) or not adapter[key].strip():
            raise PipelineError('上传界面适配器缺少选择器：'+key)
    if adapter.get('memory_unit','KB') not in ('KB','MB'): raise PipelineError('memory_unit 必须为 KB/MB')
    if scoring and (not adapter.get('subtaskId') or set(adapter.get('script_fields',{}))!=set(scoring) or not adapter.get('script_save') or not adapter.get('script_success')):
        raise PipelineError('子任务上传必须提供 subtaskId、全部计分脚本编辑框及保存按钮选择器')


async def text_value(locator):
    if await locator.count()!=1: raise PipelineError('界面元素不唯一，拒绝猜测字段')
    return await locator.evaluate('(el) => /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) ? el.value : el.textContent')


async def displayed_rows(page,adapter):
    result={}
    rows=page.locator(adapter['rows'])
    for index in range(await rows.count()):
        row=rows.nth(index)
        label=await text_value(row.locator(adapter['filename']))
        names=re.findall(r'(?<![\w.])([1-9][0-9]*\.in)(?![\w.])',label)
        if len(names)!=1 or names[0] in result: raise PipelineError('页面测试点文件名不明确或重复')
        values={}
        for key in ('timeLimit','memoryLimit','score','subtaskId'):
            if not adapter.get(key): continue
            text=(await text_value(row.locator(adapter[key]))).strip()
            if key=='subtaskId' and text in ('','0','无','-'): continue
            if not re.fullmatch(r'\d+(?:\s*(?:ms|KB|MB))?',text,re.I):
                raise PipelineError('页面数值格式无法核验：'+key)
            value=int(re.match(r'\d+',text).group())
            if key=='memoryLimit' and adapter.get('memory_unit','KB')=='MB': value*=1024
            values[key]=value
        result[names[0]]=values
    return result


def verify_download(local_zip,downloaded):
    with zipfile.ZipFile(local_zip) as expected,zipfile.ZipFile(downloaded) as actual:
        names=set(expected.namelist())-{'config.yml'}
        if names!=set(actual.namelist())-{'config.yml'} or len(actual.namelist())!=len(set(actual.namelist())):
            raise PipelineError('上传后下载的数据文件清单不一致')
        # Check declared sizes BEFORE decompressing to bound archive reads.
        for name in names:
            if actual.getinfo(name).file_size!=expected.getinfo(name).file_size:
                raise PipelineError('上传后下载的数据文件大小不一致：'+name)
            if actual.read(name)!=expected.read(name):
                raise PipelineError('上传后下载的数据内容不一致：'+name)


async def submit_and_verify(page,root,adapter,approve,*,target_guard):
    """File selection can auto-submit, so approval is mandatory BEFORE selection."""
    spec=load_json(root/'spec.json')
    points=load_json(root/'state.json')['fingerprint']['points']
    scoring=load_json(root/'scoring.json')
    validate_adapter(adapter,scoring)
    archive=root/'testcases.zip'
    sha=file_hash(archive)
    field=page.locator(adapter['file_input'])
    if await field.count()!=1: raise PipelineError('找不到唯一的 ZIP 上传控件；请更新页面适配器')
    if await page.locator(adapter['success_indicator']).is_visible():
        raise PipelineError('上传成功提示在上传前已可见，无法辨别本次请求；请清除旧提示或修正选择器')
    await target_guard()
    if not await approve(sha): raise PipelineError('用户未确认，没有上传')
    await target_guard()
    if file_hash(archive)!=sha: raise PipelineError('确认后 ZIP 已改变，禁止上传')
    receipt={'status':'upload_started','zip_sha256':sha}
    save_json(root/'upload-receipt.json',receipt)
    try:
        await field.set_input_files(str(archive.resolve()))
        if adapter.get('submit_button'): await page.locator(adapter['submit_button']).click()
        await page.locator(adapter['success_indicator']).wait_for(state='visible',timeout=120000)
        for ident,script in scoring.items():
            await page.locator(adapter['script_fields'][ident]).fill(script)
        if scoring:
            if await page.locator(adapter['script_success']).is_visible():
                raise PipelineError('脚本保存成功提示在保存前已可见，不能确认本次保存')
            await page.locator(adapter['script_save']).click()
            if not adapter.get('script_success'): raise PipelineError('缺少计分脚本保存完成指示器')
            await page.locator(adapter['script_success']).wait_for(state='visible',timeout=30000)
        await page.reload(wait_until='domcontentloaded')
        await target_guard()
        expected=expected_config(spec,points)
        deadline=time.monotonic()+float(adapter.get('verification_timeout',30))
        while True:
            try:
                observed=await displayed_rows(page,adapter)
                if observed!=expected: raise PipelineError('刷新后的页面配置与本地配置不一致')
                for ident,script in scoring.items():
                    if (await text_value(page.locator(adapter['script_fields'][ident]))).replace('\r\n','\n')!=script:
                        raise PipelineError('刷新后计分脚本未正确保存：'+ident)
                break
            except PipelineError:
                if time.monotonic()>=deadline: raise
                await asyncio.sleep(.25)
        async with page.expect_download(timeout=60000) as event:
            await page.locator(adapter['download_button']).click()
        download=await event.value
        with tempfile.TemporaryDirectory(prefix='luogu-verify-') as folder:
            path=Path(folder)/'download.zip'
            await download.save_as(str(path))
            verify_download(archive,path)
        receipt.update(status='verified',displayed_config=observed,data_bytes_verified=True,scoring_verified=True)
        save_json(root/'upload-receipt.json',receipt)
        return receipt
    except Exception as exc:
        receipt.update(status='uploaded_unverified',error=str(exc))
        save_json(root/'upload-receipt.json',receipt)
        raise PipelineError('上传可能已经发生，但验收未通过；不会自动重传。请查看 upload-receipt.json。'+str(exc)) from exc


async def upload(root,url,adapter_path,*,prompt=input):
    info=preview(root,url)
    adapter=load_json(adapter_path)
    validate_adapter(adapter,info['scoring'])
    from playwright.async_api import async_playwright
    pid=problem_id(url)
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=False,channel=adapter.get('browser_channel') or None)
        try:
            context=await browser.new_context(accept_downloads=True)
            page=await context.new_page()
            await page.goto(url,wait_until='domcontentloaded')
            await asyncio.to_thread(prompt,'请在浏览器中自行登录，回到该题题面页面后按 Enter：')
            if page.url.rstrip('/')!=url: raise PipelineError('当前浏览器页面不是目标题目')
            remote_title=await text_value(page.locator(adapter.get('problem_title','h1')))
            remote_statement=await text_value(page.locator(adapter.get('problem_statement','main')))
            if not remote_statement.strip(): raise PipelineError('无法读取目标题面，禁止上传')
            print(json.dumps(info,ensure_ascii=False,indent=2))
            print('\n远端题目：'+remote_title+'\n'+remote_statement+'\n\n本地题面：\n'+load_json(root/'parsed.json')['markdown'])
            await asyncio.to_thread(prompt,'请在同一标签页打开此题的测试数据管理页，再按 Enter（尚未上传）：')
            async def guard():
                from urllib.parse import urlsplit
                parsed=urlsplit(page.url)
                if parsed.scheme!='https' or parsed.hostname!='www.luogu.com.cn' or not re.search(r'(?:^|/)'+re.escape(pid)+r'(?:/|$)',parsed.path):
                    raise PipelineError('浏览器已离开目标题目的管理页面')
            async def approve(sha):
                preview(root,url)
                phrase=f'UPLOAD {pid} {sha[:12]}'
                answer=await asyncio.to_thread(prompt,f'即将上传，可能覆盖现有数据；确认题面、文件及配置后输入 {phrase}：')
                return answer.strip()==phrase
            return await submit_and_verify(page,root,adapter,approve,target_guard=guard)
        finally:
            await browser.close()
