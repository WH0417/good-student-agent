"""Explicit, course-scoped consent for sending selected material to Codex."""
from __future__ import annotations

from copy import deepcopy
import json

from .storage import DataError, atomic_json, file_lock, now


class ConsentManager:
    destination = '已登录的 Codex（OpenAI）后台执行器'

    def __init__(self, service):
        self.service = service
        self.path = service.store.root / '.workbench/consents.json'

    def _read(self):
        if not self.path.exists():
            return {}
        try:
            result = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise DataError('授权记录无法读取，未自动允许资料发送。') from exc
        if not isinstance(result, dict):
            raise DataError('授权记录格式异常，未自动允许资料发送。')
        return result

    def check(self, job):
        grant = self._read().get(job['course_id'], {})
        allowed = set(grant.get('source_sha256', []))
        return bool(grant.get('allowed') and all(s.get('sha256') and s['sha256'] in allowed
                                               for s in job.get('input_sources', [])))

    def describe(self, course_id):
        self.service.store.read(course_id)
        grant = self._read().get(course_id, {})
        return {'allowed': bool(grant.get('allowed')), 'source_count': len(set(grant.get('source_sha256', []))),
                'destination': self.destination, 'granted_at': grant.get('granted_at')}

    def annotate(self, job):
        result = deepcopy(job)
        required = job['status'] not in ('completed', 'cancelled') and not self.check(job)
        result['authorization_required'] = required
        result['authorization'] = {'required': required, 'course_id': job['course_id'],
            'course_name': self.service.store.read(job['course_id'])['course']['name'],
            'source_count': len(job.get('input_sources', [])),
            'source_names': [s.get('filename') or '课程资料' for s in job.get('input_sources', [])],
            'destination': self.destination,
            'message': '仅用于本课程复习生成、答疑与答案复核；新增资料或换课程需再次确认。'}
        return result

    def grant(self, course_id, job_id):
        from .jobs import JobManager
        job = JobManager(self.service).read(course_id, job_id)
        hashes = [s.get('sha256') for s in job.get('input_sources', [])]
        if any(not isinstance(value, str) or not value for value in hashes):
            raise DataError('资料版本缺失，不能授予不明确的访问范围。')
        with file_lock(self.path.with_suffix('.lock')):
            data = self._read()
            previous = data.get(course_id, {})
            old_hashes = previous.get('source_sha256', []) if previous.get('allowed') else []
            data[course_id] = {'allowed': True, 'destination': self.destination,
                'source_sha256': sorted(set(old_hashes) | set(hashes)),
                'source_ids': sorted(set(previous.get('source_ids', [])) | set(job['source_ids'])),
                'granted_at': now(), 'scope_job_id': job_id,
                'user_statement': '用户在工作台确认允许Codex处理本课程选中资料及必要学习记录。'}
            atomic_json(self.path, data)
        return self.describe(course_id)

    def revoke(self, course_id):
        self.service.store.read(course_id)
        with file_lock(self.path.with_suffix('.lock')):
            data = self._read()
            grant = data.get(course_id, {})
            data[course_id] = {**grant, 'allowed': False, 'revoked_at': now()}
            atomic_json(self.path, data)
        return self.describe(course_id)
