"""Loopback-only workbench. Model work is explicitly claimed by the host Agent."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import uuid
from urllib.request import urlopen
import webbrowser
import zipfile

from .service import StudentService
from .storage import DataError, atomic_json, file_lock, now

SUPPORTED = {'.ppt', '.pptx', '.pdf', '.docx', '.txt', '.md', '.markdown',
             '.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff',
             '.mp3', '.m4a', '.wav', '.aac', '.flac', '.ogg', '.mp4'}
AUDIO = {'.mp3', '.m4a', '.wav', '.aac', '.flac', '.ogg', '.mp4'}


def material_kind(path):
    path = Path(path)
    if path.suffix.lower() in AUDIO:
        return 'audio'
    name = path.stem.lower()
    for words, kind in ((('逐字稿', 'transcript'), 'transcript'),
                        (('纪要',), 'summary'), (('答案', 'solution'), 'solution'),
                        (('真题', '往年', '试卷', 'exam'), 'exam'),
                        (('习题', '复习题', '自测', 'exercise'), 'exercise')):
        if any(word in name for word in words):
            return kind
    return 'material'


def scan_directory(directory):
    base = Path(directory).expanduser()
    if not base.is_absolute() or not base.is_dir():
        raise DataError('请选择存在的资料文件夹')
    base = base.resolve()
    result = []
    for current, directories, files in os.walk(base, followlinks=False):
        directories[:] = sorted(d for d in directories if not d.startswith('.') and d not in ('node_modules', '__pycache__'))
        for name in sorted(files):
            path = Path(current) / name
            if path.suffix.lower() not in SUPPORTED or path.is_symlink():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(base):
                continue
            result.append({'path': str(resolved), 'name': str(resolved.relative_to(base)),
                           'size': resolved.stat().st_size, 'kind': material_kind(resolved)})
            if len(result) > 5000:
                raise DataError('文件较多，请选择具体课程文件夹')
    return result


def create_job(service, jobs, payload):
    cid = str(payload.get('course_id', ''))
    service.store.read(cid)
    with file_lock(service.store.contained(cid, 'jobs/.intake.lock'), timeout=15):
        return _create_job(service, jobs, payload)


def _create_job(service, jobs, payload):
    cid = str(payload.get('course_id', ''))
    service.store.read(cid)
    kind = str(payload.get('kind', 'build'))
    if kind not in ('build', 'question', 'review', 'challenge'):
        raise DataError('任务类型无效')
    settings = payload.get('settings') or {}
    if not isinstance(settings, dict):
        raise DataError('任务设置无效')
    file_kinds = settings.get('file_kinds', {})
    if not isinstance(file_kinds, dict):
        raise DataError('资料类型设置无效')
    valid_kinds = {'auto', 'material', 'course_focus', 'exam', 'exercise', 'solution', 'transcript', 'summary', 'audio'}
    if any(kind not in valid_kinds for kind in file_kinds.values()):
        raise DataError('资料类型无效')
    if kind == 'build' and settings.get('resume_job_id'):
        return jobs.resume(cid, settings['resume_job_id'])
    # Do not import new files into an already frozen active build.
    if kind == 'build':
        existing = next((j for j in jobs.list(cid) if j['kind'] == 'build' and
                         j['status'] in ('queued', 'running', 'waiting_agent')), None)
        if existing:
            return {**existing, 'reused': True}
    paths = payload.get('files', [])
    if not isinstance(paths, list) or len(paths) > 5000:
        raise DataError('资料文件列表无效')
    validated = []
    for raw in paths:
        path = Path(raw).expanduser()
        if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in SUPPORTED:
            raise DataError(f'资料不存在或格式暂不支持：{path}')
        resolved = path.resolve()
        requested_kind = file_kinds.get(raw, file_kinds.get(str(resolved), 'auto'))
        validated.append((resolved, material_kind(resolved) if requested_kind == 'auto' else requested_kind))
    ids = payload.get('source_ids')
    if ids is not None and (not isinstance(ids, list) or any(not isinstance(s, str) for s in ids)):
        raise DataError('资料选择无效')
    selected = list(ids or [])
    for path, selected_kind in validated:
        record = service.import_material(cid, path, selected_kind, auto_process=False)
        selected.append(record['source']['id'])
    if not paths and ids is None:
        selected = list(service.store.read(cid)['sources'])
    if kind == 'build' and not selected:
        raise DataError('请先选择课程资料')
    return jobs.create(cid, list(dict.fromkeys(selected)), request=str(payload.get('request', '')),
                       kind=kind, settings=payload.get('settings') or {})


def create_app(data_dir, token, *, web_dir=None, defaults=None, test_mode=False):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, FileResponse
    from fastapi.staticfiles import StaticFiles
    from .jobs import JobManager
    from .packages import PackageManager
    from .runner import AutoRunner
    from .consents import ConsentManager
    from contextlib import asynccontextmanager

    root = Path(data_dir).resolve()
    service = StudentService(root)
    jobs, packages = JobManager(service), PackageManager(service)
    consents = ConsentManager(service)
    web = Path(web_dir or Path(__file__).resolve().parents[1] / 'web').resolve()
    runner = AutoRunner(service) if not test_mode else None
    @asynccontextmanager
    async def lifespan(app):
        if runner:
            runner.start()
        try:
            yield
        finally:
            if runner:
                runner.stop()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.service, app.state.jobs, app.state.packages = service, jobs, packages
    app.state.defaults = defaults or {}
    app.state.identity = sha256(str(root).encode()).hexdigest()[:24]
    app.state.runner = runner

    def ok(data):
        return {'ok': True, 'data': data}

    @app.middleware('http')
    async def local_guard(request: Request, call_next):
        hostname = request.url.hostname
        if hostname not in ('127.0.0.1', 'localhost') and not (test_mode and hostname == 'testserver'):
            return JSONResponse({'ok': False, 'error': '仅允许本机访问'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'ok': False, 'error': '请求来源无效'}, status_code=403)
        if request.url.path.startswith('/api/') and request.url.path != '/api/health':
            supplied = request.headers.get('x-session-token', '')
            if not supplied.isascii() or not secrets.compare_digest(supplied, token):
                return JSONResponse({'ok': False, 'error': '请从 Agent 提供的本地入口打开工作台'}, status_code=401)
            try:
                size = int(request.headers.get('content-length', '0'))
            except ValueError:
                size = 2_000_000
            limit = 200 * 1024 * 1024 if request.url.path == '/api/upload' else 1_000_000
            if size > limit:
                return JSONResponse({'ok': False, 'error': '请求内容过大'}, status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        return response

    @app.exception_handler(DataError)
    @app.exception_handler(ValueError)
    @app.exception_handler(TypeError)
    @app.exception_handler(KeyError)
    @app.exception_handler(OSError)
    async def input_error(request, exc):
        return JSONResponse({'ok': False, 'error': str(exc), 'error_details': getattr(exc, 'details', None)}, status_code=400)

    @app.get('/api/health')
    def health():
        return ok({'service': 'good-student-workbench', 'identity': app.state.identity})

    @app.get('/api/runner')
    def runner_status():
        return ok(runner.snapshot() if runner else {'available': False, 'status': 'manual', 'message': '由当前 Agent 接手任务'})

    @app.get('/api/defaults')
    def get_defaults():
        path = root / '.workbench/defaults.json'
        saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        return ok({**saved, **app.state.defaults})

    @app.get('/api/courses')
    def courses():
        return ok(service.store.list_courses())

    @app.post('/api/courses')
    def add_course(payload: dict):
        state = service.store.create(payload.get('name', ''), payload.get('mode', 'pass'),
                                     payload.get('score_max', 100), payload.get('passing_score', 60))
        return ok(state['course'])

    @app.post('/api/mode')
    def set_mode(payload: dict):
        return ok(service.set_mode(payload['course_id'], payload['mode'],
                                   payload.get('score_max'), payload.get('passing_score')))

    @app.get('/api/course')
    def course(course_id: str):
        state = service.store.read(course_id)
        response = {key: deepcopy(state.get(key, {})) for key in
                    ('course', 'progress', 'attempts', 'notes', 'focus', 'question_views', 'next_task', 'workbench')}
        response['sources'] = [service._source_metadata(s) for s in state['sources'].values()]
        response['topics'] = [{k: v for k, v in topic.items() if k != 'text'} for topic in state['topics'].values()]
        from .packages import _flag_visible_answer
        originals = []
        for item in state['questions'].values():
            if item.get('origin') == 'ai_generated':
                continue
            question = deepcopy(item)
            sid = question.get('answer_source_id') or question.get('source_id')
            position = question.get('answer_source') or question.get('source') or {}
            source = state['sources'].get(sid, {})
            locator = {k: v for k, v in position.items() if k in ('page', 'slide', 'paragraph', 'text_block', 'time_start', 'timestamp')}
            if 'page' in locator and source.get('location_type') in ('slide', 'text_block'):
                locator[source['location_type']] = locator.pop('page')
            question['answer_source_refs'] = ([{'source_id': sid, 'filename': source.get('filename', ''), 'locator': locator}]
                                              if question.get('answer') and sid else [])
            question['answer_basis'] = 'source_answer' if question.get('answer') else 'insufficient_material'
            question['answer_origin'] = 'source' if question.get('answer') else 'not_provided'
            if not question.get('answer'):
                question['answer_status'] = 'pending_generation'
            originals.append(_flag_visible_answer(question))
        response['original_questions'] = originals
        response['jobs'] = [consents.annotate(j) for j in jobs.list(course_id)]
        response['consent'] = consents.describe(course_id)
        response['runner'] = runner.snapshot() if runner else {'available': False, 'status': 'manual'}
        response['package'] = packages.read(course_id)
        response['preview'] = packages.read(course_id, preview=True)
        visible = response['package'] or response['preview']
        versions = {q['id']: q.get('reference_version') for q in (visible or {}).get('practice', [])}
        for topic_id, progress in response['progress'].items():
            qid = progress.get('question_id')
            if (progress.get('status') == 'practice_passed' and qid in versions
                    and progress.get('reference_version') != versions[qid]):
                progress.update(status='needs_review', basis='reference_changed')
        responses = []
        for job in response['jobs']:
            path = service.store.contained(course_id, f"jobs/{job['id']}/response.json")
            if path.exists():
                responses.append(json.loads(path.read_text(encoding='utf-8')))
        response['responses'] = responses
        return ok(response)

    @app.post('/api/scan')
    def scan(payload: dict):
        return ok(scan_directory(payload.get('path', '')))

    @app.post('/api/pick')
    def pick(payload: dict):
        kind = payload.get('kind', 'files')
        if kind not in ('directory', 'files', 'audio'):
            raise DataError('选择类型无效')
        from .filepicker import choose
        return ok({'paths': choose(kind)})

    @app.post('/api/upload')
    async def upload(request: Request, course_id: str, filename: str):
        service.store.read(course_id)
        safe_name = filename.replace('\\', '/').rsplit('/', 1)[-1]
        if not safe_name or Path(safe_name).suffix.lower() not in SUPPORTED or len(safe_name) > 220:
            raise DataError('文件名或资料格式暂不支持')
        folder = root / '.workbench' / 'imports' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        target = folder / safe_name
        count = 0
        try:
            with target.open('wb') as output:
                async for block in request.stream():
                    count += len(block)
                    if count > 200 * 1024 * 1024:
                        raise DataError('单文件超过200MB，请分段后添加')
                    output.write(block)
            if not count:
                raise DataError('不能添加空文件')
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return ok({'path': str(target), 'name': safe_name, 'size': count, 'kind': material_kind(safe_name)})

    @app.post('/api/jobs')
    def queue(payload: dict):
        return ok(consents.annotate(create_job(service, jobs, payload)))

    @app.post('/api/consent')
    def permission(payload: dict):
        if payload.get('allow') is True:
            granted = consents.grant(payload['course_id'], payload['job_id'])
            job = jobs.read(payload['course_id'], payload['job_id'])
            if job['status'] in ('partial', 'failed'):
                jobs.resume(payload['course_id'], payload['job_id'])
            return ok(granted)
        if payload.get('allow') is False:
            return ok(consents.revoke(payload['course_id']))
        raise DataError('请明确选择允许或撤销本课程授权')

    @app.get('/api/jobs')
    def get_jobs(course_id: str):
        return ok([consents.annotate(j) for j in jobs.list(course_id)])

    @app.get('/api/package')
    def get_package(course_id: str, package_id: str | None = None, preview: bool = False):
        return ok(packages.read(course_id, package_id, preview=preview))

    @app.get('/api/search')
    def search(course_id: str, q: str, package_id: str | None = None):
        return ok(packages.search(course_id, q, package_id))

    @app.post('/api/progress')
    def progress(payload: dict):
        cid, chapter_id, status = payload['course_id'], payload['chapter_id'], payload['status']
        if status not in ('read', 'self_assessed', 'needs_review'):
            raise DataError('学习标记无效')
        bundle = packages.read(cid, payload.get('package_id'), preview=True)
        if not bundle or not any(ch['id'] == chapter_id for ch in bundle['manifest']['chapters']):
            raise DataError('章节不属于当前课程复习包')
        with service.store.transaction(cid) as state:
            wb = state.setdefault('workbench', {})
            wb.setdefault('reading', {})[chapter_id] = {'status': status, 'at': now(), 'basis': 'user_report'}
            wb['last_chapter'] = chapter_id
        return ok(wb)

    @app.post('/api/focus')
    def add_focus(payload: dict):
        return ok(service.add_focus(payload['course_id'], payload['title'], payload.get('note') or payload['title']))

    @app.post('/api/reveal')
    def reveal(payload: dict):
        reference = package_reference(payload['course_id'], payload['question_id'], payload.get('package_id'))
        return ok(service.show_question(payload['course_id'], payload['question_id'], True, reference=reference))

    @app.post('/api/challenge')
    def challenge(payload: dict):
        from .challenges import AnswerReviewManager
        return ok(consents.annotate(AnswerReviewManager(service).request(payload['course_id'], payload['package_id'],
                  payload['question_id'], payload.get('reason') or '用户请求重新核对答案与解题过程。')))

    def package_reference(cid, qid, pid=None):
        bundle = packages.read(cid, pid)
        if not bundle and pid is None:
            bundle = packages.read(cid, preview=True)
        if not bundle:
            return None
        question = next((q for q in bundle['practice'] if q['id'] == qid), None)
        if question is None:
            if pid:
                raise DataError('题目不属于指定复习包')
            return None
        return {**question, 'reference_package_id': bundle['manifest']['id']}

    @app.post('/api/answer')
    def answer(payload: dict):
        cid, qid = payload['course_id'], payload['question_id']
        reference = package_reference(cid, qid, payload.get('package_id'))
        attempt = service.answer(cid, qid, payload.get('response', ''), payload.get('used_hint', False), reference=reference)
        if attempt['result'] == 'unsure':
            attempt['review_job'] = jobs.create(cid, request='请评阅这次开放题作答，反馈正确部分、缺漏及改进方向。',
                                                kind='review', settings={'question_id': qid, 'attempt_id': attempt['id'],
                                                                        'package_id': attempt.get('reference_package_id')})
            attempt['review_job'] = consents.annotate(attempt['review_job'])
        return ok(attempt)

    @app.post('/api/export')
    def export(payload: dict):
        cid = payload['course_id']
        result = packages.export(cid, payload.get('package_id'))
        directory = Path(result['directory'])
        archive = directory / '复习资料.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
            for path in directory.rglob('*'):
                if path.is_file() and path != archive:
                    zipped.write(path, path.relative_to(directory).as_posix())
        relative = archive.relative_to(service.store.course_dir(cid)).as_posix()
        result['download_url'] = f'/api/download?course_id={cid}&path={relative}'
        return ok(result)

    @app.get('/api/download')
    def download(course_id: str, path: str):
        target = service.store.contained(course_id, path)
        exports = service.store.contained(course_id, 'exports')
        if not target.is_relative_to(exports) or not target.is_file():
            raise DataError('导出文件不存在')
        return FileResponse(target, filename=target.name)

    app.mount('/', StaticFiles(directory=web, html=True), name='workbench')
    return app


def serve(data_dir, token, port=0, web_dir=None):
    import uvicorn
    root = Path(data_dir).resolve()
    statefile = root / '.workbench/server.json'
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', port))
    sock.listen(128)
    actual_port = sock.getsockname()[1]
    app = create_app(root, token, web_dir=web_dir)
    record = {'pid': os.getpid(), 'port': actual_port, 'token': token,
              'identity': app.state.identity, 'url': f'http://127.0.0.1:{actual_port}', 'started_at': now()}
    atomic_json(statefile, record)
    try:
        uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False)).run(sockets=[sock])
    finally:
        sock.close()
        if statefile.exists() and json.loads(statefile.read_text(encoding='utf-8')).get('pid') == os.getpid():
            statefile.unlink()


def launch(data_dir, *, defaults=None, browser=True):
    root = Path(data_dir).resolve()
    folder = root / '.workbench'
    folder.mkdir(parents=True, exist_ok=True)
    if defaults:
        path = folder / 'defaults.json'
        old = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        atomic_json(path, {**old, **{k: v for k, v in defaults.items() if v is not None}})
    identity = sha256(str(root).encode()).hexdigest()[:24]
    with file_lock(folder / 'launch.lock', timeout=15):
        statefile = folder / 'server.json'
        record = None
        if statefile.exists():
            try:
                saved = json.loads(statefile.read_text(encoding='utf-8'))
                if saved.get('identity') == identity and saved.get('url') == f"http://127.0.0.1:{int(saved['port'])}":
                    with urlopen(saved['url'] + '/api/health', timeout=2) as stream:
                        if json.load(stream).get('data', {}).get('identity') == identity:
                            record = saved
            except (ValueError, OSError, KeyError):
                pass
        if record is None:
            token = secrets.token_urlsafe(32)
            atomic_json(folder / 'launch.json', {'token': token})
            with (folder / 'server.log').open('ab') as logfile:
                child = subprocess.Popen([sys.executable, '-X', 'utf8', '-B', '-m', 'good_student.workbench_cli',
                                          '--data-dir', str(root), 'serve'],
                                         cwd=Path(__file__).resolve().parents[1], stdin=subprocess.DEVNULL,
                                         stdout=logfile, stderr=logfile,
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                         start_new_session=os.name != 'nt')
            for _ in range(100):
                if child.poll() is not None:
                    raise DataError('本地工作台未启动，请查看项目 data/.workbench/server.log')
                if statefile.exists():
                    candidate = json.loads(statefile.read_text(encoding='utf-8'))
                    if candidate.get('token') == token:
                        try:
                            with urlopen(candidate['url'] + '/api/health', timeout=1):
                                record = candidate
                            break
                        except OSError:
                            pass
                time.sleep(0.1)
            if record is None:
                raise DataError('本地工作台启动超时，已保留日志')
    url = record['url'] + '#token=' + record['token']
    opened = webbrowser.open(url, new=2) if browser else False
    return {'url': url, 'browser_opened': bool(opened), 'pid': record['pid'],
            'data_dir': str(root), 'message': '工作台已启动；生成任务由活跃 Agent 接管。'}
