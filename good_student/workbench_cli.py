"""Agent-only bridge for local UI tasks and complete, precomputed study packs."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout
import json
from pathlib import Path
import sys
import threading
import time

from .service import StudentService
from .storage import DataError, atomic_json, now


def parser():
    p = argparse.ArgumentParser(description='好学生工作台内部入口')
    p.add_argument('--data-dir', required=True, type=Path)
    commands = p.add_subparsers(dest='command', required=True)
    open_ui = commands.add_parser('open')
    open_ui.add_argument('--course')
    open_ui.add_argument('--materials')
    open_ui.add_argument('--recording', action='append', default=[])
    open_ui.add_argument('--no-browser', action='store_true')
    serving = commands.add_parser('serve')
    serving.add_argument('--port', type=int, default=0)
    for name in ('jobs', 'wait'):
        cmd = commands.add_parser(name)
        cmd.add_argument('--course')
        if name == 'wait':
            cmd.add_argument('--timeout', type=float, default=40)
    create = commands.add_parser('job-create')
    create.add_argument('--course', required=True)
    create.add_argument('--file', action='append', default=[])
    create.add_argument('--source', action='append')
    create.add_argument('--request', default='')
    create.add_argument('--kind', default='build', choices=('build', 'question', 'review', 'challenge'))
    create.add_argument('--settings', type=Path)
    for name in ('claim', 'resume', 'heartbeat', 'release', 'context', 'chapter-context', 'prepare-source', 'checkpoint',
                 'package-create', 'chapter-save', 'overview-save', 'publish', 'respond', 'challenge-resolve'):
        cmd = commands.add_parser(name)
        cmd.add_argument('--course', required=True)
        cmd.add_argument('--job', required=True)
        if name == 'claim':
            cmd.add_argument('--worker', required=True)
        elif name not in ('context', 'chapter-context', 'resume'):
            cmd.add_argument('--lease', required=True)
        if name in ('package-create', 'chapter-save', 'overview-save', 'respond', 'challenge-resolve'):
            cmd.add_argument('--file', required=True, type=Path)
        if name in ('chapter-save', 'chapter-context'):
            cmd.add_argument('--chapter', required=True)
        if name in ('context', 'prepare-source'):
            cmd.add_argument('--source')
        if name == 'context':
            cmd.add_argument('--start', type=int, default=0)
            cmd.add_argument('--limit', type=int, default=40)
        if name == 'checkpoint':
            cmd.add_argument('--stage', required=True)
            cmd.add_argument('--progress', type=Path)
        if name == 'release':
            cmd.add_argument('--error')
        if name == 'publish':
            cmd.add_argument('--partial', action='store_true')
    return p


@contextmanager
def live_step(jobs, cid, jid, token):
    jobs.heartbeat(cid, jid, token)
    stop = threading.Event()
    def pulse():
        while not stop.wait(30):
            try:
                jobs.heartbeat(cid, jid, token)
            except (ValueError, OSError):
                break
    thread = threading.Thread(target=pulse, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)


def _json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def dispatch(args):
    from .workbench import launch, serve, create_job
    if args.command == 'open':
        return launch(args.data_dir, defaults={'course_id': args.course,
                      'material_directory': args.materials, 'recording_paths': args.recording},
                      browser=not args.no_browser)
    if args.command == 'serve':
        token = _json(args.data_dir.resolve() / '.workbench/launch.json')['token']
        serve(args.data_dir, token, args.port)
        return {'stopped': True}
    from .jobs import JobManager
    from .packages import PackageManager
    service = StudentService(args.data_dir)
    jobs, packages = JobManager(service), PackageManager(service)
    if args.command in ('jobs', 'wait'):
        timeout = args.timeout if args.command == 'wait' else 0
        if not 0 <= timeout <= 50:
            raise DataError('每次等待应在0到50秒之间')
        end = time.monotonic() + timeout
        while True:
            courses = [args.course] if args.course else [c['id'] for c in service.store.list_courses() if not c.get('error')]
            result = [j for cid in courses for j in jobs.list(cid)]
            pending = [j for j in result if j['status'] in ('queued', 'waiting_agent')]
            if pending or time.monotonic() >= end:
                return {'pending': pending, 'jobs': result}
            time.sleep(min(1, max(0, end - time.monotonic())))
    if args.command == 'job-create':
        return create_job(service, jobs, {'course_id': args.course, 'files': args.file,
                          'source_ids': args.source, 'request': args.request, 'kind': args.kind,
                          'settings': _json(args.settings) if args.settings else {}})
    cid, jid = args.course, args.job
    if args.command == 'claim':
        return jobs.claim(cid, jid, args.worker)
    if args.command == 'resume':
        return jobs.resume(cid, jid)
    job = jobs.read(cid, jid)
    if args.command == 'chapter-context':
        if not job.get('package_id'):
            raise DataError('任务尚无章节内容')
        package = packages.read(cid, job['package_id'], preview=True)
        chapter = next((ch for ch in package['chapters'] if ch['id'] == args.chapter), None)
        if not chapter:
            raise DataError('该章尚未完成或不在本任务中')
        return chapter
    if args.command == 'context':
        state = service.store.read(cid)
        if args.source:
            if args.source not in job['source_ids']:
                raise DataError('资料不在本次任务范围内')
            return service.source_slice(cid, args.source, args.start, args.limit)
        package = packages.read(cid, job['package_id'], preview=True) if job.get('package_id') else None
        review_attempt = next((a for a in state['attempts'] if a['id'] == job['settings'].get('attempt_id')), None)
        review_question = state['questions'].get(job['settings'].get('question_id'))
        reference_package = (review_attempt or {}).get('reference_package_id') or job['settings'].get('package_id')
        related_chapter = None
        if reference_package:
            reference = packages.read(cid, reference_package)
            if reference:
                review_question = next((q for q in reference['practice'] if q['id'] == job['settings'].get('question_id')), review_question)
                related_chapter = next((ch for ch in reference['chapters'] if ch['id'] == job['settings'].get('chapter_id')), None)
        if job['kind'] != 'build':
            return {'job': job, 'review_context': review_attempt, 'review_question': review_question,
                    'related_chapter': related_chapter, 'challenge_reason': job['settings'].get('reason'),
                    'sources': [service._source_metadata(state['sources'][sid]) for sid in
                                ((related_chapter or {}).get('source_ids') or job['source_ids'])]}
        compact = None if package is None else {
            'manifest': package['manifest'],
            'chapters': [{'id': ch['id'], 'title': ch['title'], 'summary': ch['summary']} for ch in package['chapters']]}
        return {'job': job, 'sources': [service._source_metadata(state['sources'][sid]) for sid in job['source_ids']],
                'topics': [{k: v for k, v in topic.items() if k != 'text'} for topic in state['topics'].values()
                           if any(ref.get('source_id') in job['source_ids'] for ref in topic.get('source_refs', []))],
                'focus': list(state['focus'].values()),
                'questions': [{k: v for k, v in q.items() if k not in ('answer', 'explanation')}
                              for q in state['questions'].values() if set(q.get('source_ids', [])).intersection(job['source_ids'])],
                'package': compact,
                'review_context': review_attempt, 'review_question': review_question, 'related_chapter': related_chapter}
    if args.command == 'heartbeat':
        return jobs.heartbeat(cid, jid, args.lease)
    if args.command == 'release':
        return jobs.release(cid, jid, args.lease, args.error)
    if args.command == 'prepare-source':
        with live_step(jobs, cid, jid, args.lease):
            if args.source not in job['source_ids']:
                raise DataError('资料不在本次任务范围内')
            source = service.store.read(cid)['sources'][args.source]
            def report_media_progress(progress):
                jobs.update(cid, jid, args.lease, stage='正在识别：' + source['filename'],
                            progress={**progress, 'source_id': args.source, 'filename': source['filename']})
            return service.process_source(cid, args.source, on_progress=report_media_progress)
    with jobs.owned_commit(cid, jid, args.lease) as owned:
        owned['error'] = None
        owned['error_details'] = None
        if args.command == 'challenge-resolve':
            from .challenges import AnswerReviewManager
            if owned['kind'] != 'challenge':
                raise DataError('该任务不是答案复核任务')
            record = AnswerReviewManager(service).resolve(cid, owned, _json(args.file))
            owned.update(status='completed', stage='答案已重新核对并保存')
            return record
        if args.command == 'checkpoint':
            owned['stage'] = args.stage
            if args.progress:
                progress = _json(args.progress)
                if not isinstance(progress, dict):
                    raise DataError('任务进度须为对象')
                owned['progress'] = progress
            return {'saved': True, 'stage': args.stage}
        if args.command == 'package-create':
            if owned.get('package_id'):
                return packages.read(cid, owned['package_id'], preview=True)['manifest']
            plan = _json(args.file)
            manifest = packages.create(cid, owned, plan['chapters'] if isinstance(plan, dict) else plan)
            owned.update(package_id=manifest['id'], stage='正在整理章节')
            return manifest
        if args.command in ('chapter-save', 'overview-save', 'publish'):
            pid = owned.get('package_id')
            if not pid:
                raise DataError('请先建立复习包章节计划')
            if args.command == 'chapter-save':
                result = packages.write_chapter(cid, pid, args.chapter, _json(args.file))
                manifest = packages.read(cid, pid, preview=True)['manifest']
                done = sum(c['status'] == 'complete' for c in manifest['chapters'])
                owned.update(stage='正在整理章节', progress={'completed': done, 'total': len(manifest['chapters'])})
                return {'chapter_id': result['id'], 'title': result['title'], 'saved': True}
            if args.command == 'overview-save':
                item = _json(args.file)
                return packages.write_overview(cid, pid, item['overview'], item['mindmap'])
            manifest = packages.publish(cid, pid, partial=args.partial)
            owned.update(status='completed' if manifest['status'] == 'complete' else 'partial',
                         stage='复习资料已完成' if manifest['status'] == 'complete' else '部分完成，请查看资料说明')
            return manifest
        if args.command == 'respond':
            if job['kind'] == 'build':
                raise DataError('资料生成任务须通过复习包发布')
            text = args.file.read_text(encoding='utf-8-sig').strip()
            if not text:
                raise DataError('答疑内容不能为空')
            response = {'job_id': jid, 'kind': job['kind'], 'request': job['request'],
                        'text': text, 'at': now(), 'chapter_id': job['settings'].get('chapter_id'),
                        'package_id': job['settings'].get('package_id')}
            atomic_json(service.store.contained(cid, f'jobs/{jid}/response.json'), response)
            owned.update(status='completed', stage='答疑已保存')
            return response
    raise DataError('操作不支持')


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        with redirect_stdout(sys.stderr):
            result = dispatch(args)
        print(json.dumps({'ok': True, 'data': result}, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (DataError, ValueError, OSError, KeyError, TypeError, ImportError) as exc:
        details = {'stage': args.command, 'message': str(exc)}
        if getattr(args, 'job', None) and getattr(args, 'lease', None):
            try:
                from .jobs import JobManager
                service = StudentService(args.data_dir)
                if getattr(args, 'source', None):
                    source = service.store.read(args.course)['sources'].get(args.source, {})
                    details.update(source_id=args.source, filename=source.get('filename'))
                elif getattr(args, 'file', None):
                    details['filename'] = args.file.name
                JobManager(service).update(args.course, args.job, args.lease, error=str(exc), error_details=details)
            except (DataError, OSError, ValueError, KeyError):
                pass
        print(json.dumps({'ok': False, 'error': str(exc), 'error_details': details}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
