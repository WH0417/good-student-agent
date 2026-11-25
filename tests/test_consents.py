import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient
from good_student.consents import ConsentManager
from good_student.jobs import JobManager
from good_student.service import StudentService
from good_student.storage import DataError
from good_student.workbench import create_app


class ConsentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = StudentService(self.root / 'data')
        self.cid = self.service.store.create('课程授权测试', 'pass')['course']['id']
        self.source = self.root / '课程知识.md'
        self.source.write_text('# 栈\n栈采用后进先出原则。', encoding='utf-8')
        self.sid = self.service.import_material(self.cid, self.source, 'material')['source']['id']
        self.jobs = JobManager(self.service)
        self.consents = ConsentManager(self.service)

    def tearDown(self):
        self.temp.cleanup()

    def test_scope_is_remembered_by_content_version_but_never_across_courses(self):
        job = self.jobs.create(self.cid, [self.sid], kind='question')
        self.assertFalse(self.consents.check(job))
        self.consents.grant(self.cid, job['id'])
        self.assertTrue(self.consents.check(job))
        tagged = self.service.import_material(self.cid, self.source, 'course_focus')['source']['id']
        self.assertTrue(self.consents.check(self.jobs.create(self.cid, [tagged], kind='question')))
        other = self.service.store.create('另一门课', 'pass')['course']['id']
        other_sid = self.service.import_material(other, self.source, 'material')['source']['id']
        self.assertFalse(self.consents.check(self.jobs.create(other, [other_sid], kind='question')))
        self.source.write_text('# 队列\n这是未经确认的新版本课程内容。', encoding='utf-8')
        new_sid = self.service.import_material(self.cid, self.source, 'material')['source']['id']
        self.assertFalse(self.consents.check(self.jobs.create(self.cid, [new_sid], kind='question')))

    def test_revoke_stops_authorized_work_and_corrupt_records_fail_closed(self):
        job = self.jobs.create(self.cid, [self.sid], kind='question')
        self.consents.grant(self.cid, job['id'])
        self.consents.revoke(self.cid)
        self.assertFalse(self.consents.check(job))
        self.consents.path.write_text('broken', encoding='utf-8')
        with self.assertRaises(DataError):
            self.consents.check(job)

    def test_interface_requires_explicit_confirmation_and_reuses_only_selected_scope(self):
        web = self.root / 'web'
        web.mkdir()
        (web / 'index.html').write_text('<!doctype html><title>test</title>', encoding='utf-8')
        app = create_app(self.service.store.root, 'token', web_dir=web, test_mode=True)
        with TestClient(app, base_url='http://127.0.0.1:8765', headers={'x-session-token': 'token'}) as client:
            job = client.post('/api/jobs', json={'course_id': self.cid, 'source_ids': [self.sid]}).json()['data']
            self.assertTrue(job['authorization_required'])
            self.assertEqual(job['authorization']['source_names'], ['课程知识.md'])
            self.assertFalse(self.consents.check(job))
            bad = client.post('/api/consent', json={'course_id': self.cid, 'job_id': job['id'], 'allow': 'true'})
            self.assertEqual(bad.status_code, 400)
            grant = client.post('/api/consent', json={'course_id': self.cid, 'job_id': job['id'], 'allow': True})
            self.assertTrue(grant.json()['data']['allowed'])
            self.assertTrue(self.consents.check(job))
            listed = client.get('/api/jobs', params={'course_id': self.cid}).json()['data']
            self.assertFalse(listed[0]['authorization_required'])
            owned = self.jobs.claim(self.cid, job['id'], 'test-worker')
            self.jobs.update(self.cid, job['id'], owned['lease_token'], status='failed', error='可恢复的测试中断')
            revoked = client.post('/api/consent', json={'course_id': self.cid, 'allow': False})
            self.assertFalse(revoked.json()['data']['allowed'])
            self.assertFalse(self.consents.check(job))
            again = client.post('/api/consent', json={'course_id': self.cid, 'job_id': job['id'], 'allow': True})
            self.assertTrue(again.json()['data']['allowed'])
            self.assertEqual(self.jobs.read(self.cid, job['id'])['status'], 'waiting_agent')


if __name__ == '__main__':
    unittest.main()
