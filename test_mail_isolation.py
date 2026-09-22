"""Real PostgreSQL regression tests. Requires a disposable local database URL."""
import datetime
import io
import json
import logging
import os
from pathlib import Path
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch
from urllib.parse import urlparse

from flask import Flask, jsonify, request, session, render_template, send_file, redirect

import database as db
import mail_access as access
import mail_blaster_service as mb
import mail_inbox_service as inbox


def test_app():
    """Load actual mail routes without the monolith's scheduler or external startup."""
    app = Flask(__name__, root_path=str(Path(__file__).parent))
    app.secret_key = 'isolated-mail-test-only'
    source = Path('app.py').read_text()
    start = source.index('import mail_access\nmail_access.install(app)')
    end = source.index("if __name__ == '__main__':", start)
    ns = dict(app=app, feature_required=lambda feature: lambda fn: fn,
              MAIL_BLASTER_AVAILABLE=True, MAIL_INBOX_AVAILABLE=True,
              mail_blaster_service=mb, mail_inbox_service=inbox, db=db,
              jsonify=jsonify, request=request, session=session, render_template=render_template,
              send_file=send_file, redirect=redirect, os=os, io=io,
              logger=logging.getLogger(__name__),
              _json_safe=lambda v: json.loads(json.dumps(v, default=str)),
              _json_safe_rows=lambda v: json.loads(json.dumps(v, default=str)))
    exec(compile(source[start:end], 'app.py:mail-routes', 'exec'), ns)
    start = source.index("@app.route('/api/tasks/summary')")
    end = source.index("@app.route('/api/tasks')", start)
    ns.update(login_required=lambda fn: fn, _serialize_task_row=lambda row: dict(row))
    exec(compile(source[start:end], 'app.py:task-summary', 'exec'), ns)
    return app


@contextmanager
def as_user(uid):
    token = access.current_actor.set(access.Actor.load(uid))
    try:
        yield
    finally:
        access.current_actor.reset(token)


def initialize_test_database():
    url = os.environ.get('MAIL_TEST_DATABASE_URL', '')
    parsed = urlparse(url)
    if parsed.hostname not in ('127.0.0.1', 'localhost') or parsed.port != 55449:
        raise RuntimeError('MAIL_TEST_DATABASE_URL must point to the disposable localhost:55449 instance')
    db.DATABASE_URL = url
    db.execute('''CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY, username TEXT, real_name TEXT, role TEXT, permissions TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS task_queue (
        task_id TEXT PRIMARY KEY, user_id INTEGER, session_id TEXT, function_type TEXT,
        lane TEXT, status TEXT, progress TEXT, task_params TEXT, updated_at TIMESTAMP DEFAULT NOW())''')
    db.execute('''ALTER TABLE task_queue ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW(),
        ADD COLUMN IF NOT EXISTS started_at TIMESTAMP, ADD COLUMN IF NOT EXISTS worker_id TEXT''')
    mb.ensure_schema()


@unittest.skipUnless(os.environ.get('MAIL_TEST_DATABASE_URL'), 'requires disposable PostgreSQL')
class MailIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialize_test_database()
        cls.app = test_app()

    def setUp(self):
        db.execute('TRUNCATE mb_jobs, mb_sender_accounts, mb_templates, mb_history, mb_attachments, '
                   'mb_images, mb_threads, mb_inbox_messages, users, task_queue RESTART IDENTITY CASCADE')
        for name, role in [('Alice', 'user'), ('Bob', 'user'), ('Admin', 'admin')]:
            db.execute("INSERT INTO users(username, real_name, role, permissions) VALUES (%s,%s,%s,'mail_blaster')",
                       (name, name, role))
        for email in ['shared@example.com', 'private@example.com']:
            db.execute("INSERT INTO mb_sender_accounts(email,smtp_host,smtp_port,encrypted_password,status,purpose) "
                       "VALUES (%s,'localhost',1025,'test-only','ready','outreach')", (email,))
        db.execute('INSERT INTO mb_account_members VALUES (1,1),(1,2),(2,2)')
        self.client = self.app.test_client()
        self.login(1)
        window = patch.object(mb, 'outside_send_window', return_value='')
        window.start()
        self.addCleanup(window.stop)

    def login(self, uid):
        with self.client.session_transaction() as s:
            s['user_id'] = uid
            s['role'] = 'admin' if uid == 3 else 'user'

    def job(self, uid=1, account=1):
        with as_user(uid):
            return mb.create_outreach_job(sender_account_id=account,
                                         rows=[{'email':'kol@example.com', 'vars':{'name':'KOL'}}], user_id=uid)

    def sent(self, job, mid):
        iid = job['items'][0]['id']
        db.execute("UPDATE mb_items SET status = 'sent', message_id = %s, sent_at = NOW() WHERE id = %s",
                   (mid, iid))
        return iid

    def message(self, account, mid, reply_to='', uid=1):
        parsed = dict(message_id=mid, in_reply_to=reply_to, refs='', from_email='kol@example.com',
                      from_name='KOL', to_email='shared@example.com', subject='Re: offer',
                      body_text='Interested', body_html='', received_at=datetime.datetime.now(),
                      auto_submitted='', precedence='', return_path='')
        return inbox._store_message(account, 'INBOX', uid, parsed, uidvalidity=10)

    def test_scoped_job_routes_and_payloads(self):
        own, other = self.job(), self.job(2)
        jid = other['job']['id']
        for path, method in [(f'/jobs/{jid}/status','get'), (f'/jobs/{jid}/preview','post'),
                             (f'/jobs/{jid}/send','post'), (f'/jobs/{jid}','put'),
                             (f'/items/{other["items"][0]["id"]}/resend','post')]:
            response = getattr(self.client, method)('/api/mail-blaster' + path)
            self.assertEqual(response.status_code, 404, path)
        response = self.client.get('/api/mail-blaster/outreach/jobs').get_json()
        self.assertEqual([j['id'] for j in response['jobs']], [own['job']['id']])
        response = self.client.put(f'/api/mail-blaster/jobs/{own["job"]["id"]}',
                                   json={'sender_account_id':2})
        self.assertEqual(response.status_code, 404)
        self.login(3)
        self.assertEqual(self.client.get(f'/api/mail-blaster/jobs/{jid}/status').status_code, 200)

    def test_account_members_and_templates(self):
        self.assertEqual([a['id'] for a in self.client.get('/api/mail-blaster/accounts').json['accounts']], [1])
        self.assertEqual(self.client.put('/api/mail-blaster/accounts/1',json={}).status_code,403)
        with as_user(1):
            mb.save_template('default','Alice','private body','')
        with as_user(2):
            mb.save_template('default','Bob','private body','')
        self.assertEqual(self.client.get('/api/mail-blaster/templates').json['templates'][0]['subject'],'Alice')
        self.login(3)
        self.assertEqual(self.client.put('/api/mail-blaster/accounts/2/members',json={'user_ids':[1]}).status_code,200)
        self.login(1)
        self.assertEqual(len(self.client.get('/api/mail-blaster/accounts').json['accounts']),2)

    def test_attachments_owner_and_hash_dedup(self):
        with as_user(2):
            att = mb.store_attachment(b'private', 'brief.txt')
        url = f'/api/mail-blaster/attachments/{att["id"]}'
        self.assertEqual(self.client.get(url).status_code,404)
        j = self.job()
        response = self.client.put(f'/api/mail-blaster/jobs/{j["job"]["id"]}',json={'attachments':[att]})
        self.assertEqual(response.status_code,404)
        with as_user(1):
            duplicate = mb.store_attachment(b'private','own.txt')
        self.assertEqual(duplicate['id'],att['id'])
        self.assertEqual(self.client.get(url).status_code,200)

    def test_shared_mailbox_threads_remain_private(self):
        a, b = self.job(), self.job(2)
        ai, bi = self.sent(a,'<alice@example.com>'), self.sent(b,'<bob@example.com>')
        am = self.message(1,'<reply-a@example.com>','<alice@example.com>')
        bm = self.message(1,'<reply-b@example.com>','<bob@example.com>',2)
        bt = db.query_one('SELECT thread_id FROM mb_inbox_messages WHERE id=%s',(bm,))['thread_id']
        response = self.client.get('/api/mail-blaster/inbox/threads').json
        self.assertEqual([t['item_id'] for t in response['threads']],[ai])
        self.assertEqual(response['stats']['pending'],1)
        self.assertEqual(self.client.get(f'/api/mail-blaster/inbox/threads/{bt}').status_code,404)
        self.assertEqual(self.client.post(f'/api/mail-blaster/inbox/messages/{bm}/handled',json={}).status_code,404)
        parsed = dict(in_reply_to='',refs='',from_email='kol@example.com')
        self.assertEqual(inbox.match_to_item(parsed,1),(None,'ambiguous',0.0))
        parsed['in_reply_to'] = '<alice@example.com>'
        self.assertEqual(inbox.match_to_item(parsed,2)[0],None)

    def test_mailbox_dedupe_and_legacy_quarantine(self):
        self.message(1,'<same@example.com>')
        self.message(2,'<same@example.com>')
        self.message(1,'<same@example.com>')
        self.assertEqual(db.query_one('SELECT COUNT(*) c FROM mb_inbox_messages')['c'],2)
        a = self.job()
        iid = self.sent(a,'<orig@example.com>')
        tid = inbox._thread_for('kol@example.com',iid)
        db.execute('UPDATE mb_threads SET legacy_locked=TRUE WHERE id=%s',(tid,))
        self.assertEqual(self.client.get('/api/mail-blaster/inbox/threads').json['threads'],[])
        self.assertNotEqual(inbox._thread_for('kol@example.com',iid),tid)

    def test_atomic_enqueue_and_mailbox_reservation(self):
        a,b = self.job(), self.job(2)
        jid = a['job']['id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _:mb.enqueue_job(jid,1),range(2)))
        self.assertEqual(ids[0],ids[1])
        self.assertEqual(db.query_one('SELECT COUNT(*) c FROM task_queue')['c'],1)
        with self.assertRaisesRegex(ValueError,'还有活动'):
            mb.enqueue_job(b['job']['id'],2)
        with self.assertRaisesRegex(ValueError,'不能编辑'):
            mb.sync_job(jid,{'subject_tpl':'changed'})

    def test_unknown_delivery_is_not_retried(self):
        a = self.job()
        iid, jid = a['items'][0]['id'], a['job']['id']
        db.execute("UPDATE mb_items SET status='unknown' WHERE id=%s",(iid,))
        with self.assertRaisesRegex(ValueError,'待核实'):
            mb.enqueue_job(jid,1)
        with patch.object(mb,'send_one_email') as smtp:
            self.assertEqual(mb.send_item(jid,iid),'unknown')
            smtp.assert_not_called()

    def test_draft_does_not_record_negotiation_round(self):
        a = self.job()
        iid = self.sent(a, '<quote@example.com>')
        tid = inbox._thread_for('kol@example.com', iid)
        inbox.add_quote(tid, amount=900)
        for _ in range(2):
            inbox.suggest_reply(tid, target=500, ceiling=800,
                                ai_call=lambda *args, **kwargs: ('{"reply":"Our offer is ..."}', 0))
        self.assertEqual(inbox.negotiation_round(tid), 0)
        self.assertEqual(len(inbox.quotes_of(tid)), 1)

    def test_manual_claim_cannot_cross_mailboxes_or_overwrite(self):
        a = self.job(2, 2)
        iid = self.sent(a, '<private@example.com>')
        mid = self.message(1, '<unmatched@example.com>')
        with self.assertRaisesRegex(ValueError, '收件邮箱'):
            inbox.claim_message(mid, item_id=iid)
        own = self.job()
        own_item = self.sent(own, '<shared@example.com>')
        inbox.claim_message(mid, item_id=own_item)
        with self.assertRaisesRegex(ValueError, '已归属'):
            inbox.claim_message(mid, item_id=own_item)

    def test_uidvalidity_and_reference_order(self):
        a,b = self.job(), self.job()
        ai,bi = self.sent(a,'<older@example.com>'),self.sent(b,'<newer@example.com>')
        parsed = inbox.parse_message(b'From: kol@example.com\r\nSubject: Hello\r\n\r\nHi')
        first = inbox._store_message(1,'INBOX',1,parsed,uidvalidity=10)
        again = inbox._store_message(1,'INBOX',1,parsed,uidvalidity=10)
        changed = inbox._store_message(1,'INBOX',1,parsed,uidvalidity=11)
        self.assertIsNone(again)
        self.assertNotEqual(first,changed)
        parsed.update(in_reply_to='<older@example.com>', refs='<newer@example.com>')
        self.assertEqual(inbox.match_to_item(parsed,1)[0],ai)

    def test_material_attachment_edit_still_works(self):
        a = self.job()
        jid, iid = a['job']['id'], a['items'][0]['id']
        db.execute("UPDATE mb_jobs SET mode='material',status='done' WHERE id=%s",(jid,))
        db.execute("UPDATE mb_sender_accounts SET purpose='both' WHERE id=1")
        with as_user(1):
            att = mb.store_attachment(b'material','asset.txt')
        response = self.client.post(f'/api/mail-blaster/jobs/{jid}/preview',json={
            'items':[{'id':iid,'sender_account_id':1,'attachments':[att]}]})
        self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(mb.load_job(jid)['items'][0]['attachments'][0]['id'],att['id'])

    def test_send_claim_and_revocation(self):
        a = self.job()
        iid, jid = a['items'][0]['id'], a['job']['id']
        accepted = dict(subject='offer',body_html='body',message_id='<sent@example.com>',smtp_response='250 OK')
        with patch.object(mb,'send_one_email',return_value=accepted) as smtp:
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda _:mb.send_item(jid,iid),range(2)))
            self.assertEqual(smtp.call_count,1)
        b = self.job()
        db.execute('DELETE FROM mb_account_members WHERE user_id=1')
        with patch.object(mb,'send_one_email') as smtp:
            self.assertEqual(mb.send_item(b['job']['id'],b['items'][0]['id']),'failed')
            smtp.assert_not_called()

    def test_invalid_attachment_change_rolls_back(self):
        a = self.job()
        with as_user(1):
            att = mb.store_attachment(b'brief','brief.txt')
        mb.set_job_attachments(a['job']['id'],[att])
        with self.assertRaises(ValueError):
            mb.sync_job(a['job']['id'],{'subject_tpl':'mutated','attachments':[{'id':99999}]})
        state = mb.load_job(a['job']['id'])
        self.assertEqual(state['job']['subject_tpl'],a['job']['subject_tpl'])
        self.assertEqual(state['attachments'][0]['id'],att['id'])

    def test_single_retry_respects_window_and_quota(self):
        a = self.job()
        jid, iid = a['job']['id'], a['items'][0]['id']
        with patch.object(mb, 'send_one_email') as smtp:
            with patch.object(mb, 'outside_send_window', return_value='不在发送窗口内'):
                self.assertEqual(mb.send_item(jid, iid), 'skipped')
            db.execute('UPDATE mb_sender_accounts SET daily_limit=0 WHERE id=1')
            self.assertEqual(mb.send_item(jid, iid), 'skipped')
            smtp.assert_not_called()
        self.assertIn('配额', mb.load_job(jid)['job']['paused_reason'])

    def test_http_repeat_send_and_generic_task_scope(self):
        a = self.job()
        path = f'/api/mail-blaster/jobs/{a["job"]["id"]}/send'
        first = self.client.post(path, json={})
        second = self.client.post(path, json={})
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(first.json['task_id'], second.json['task_id'])
        task_id = first.json['task_id']
        local_app = test_app()
        local_app.add_url_rule('/task/<task_id>', 'test_task', lambda task_id: jsonify(ok=True))
        client = local_app.test_client()
        with client.session_transaction() as s:
            s['user_id'] = 2
        self.assertEqual(client.get(f'/task/{task_id}').status_code, 404)
        with client.session_transaction() as s:
            s['user_id'] = 1
        self.assertEqual(client.get(f'/task/{task_id}').status_code, 200)
        self.login(2)
        summary = self.client.get('/api/tasks/summary')
        self.assertEqual(summary.status_code, 200, summary.json)
        self.assertNotIn('mail_blaster_send', summary.json['by_module'])
        self.assertIsNone(summary.json['oldest_pending'])
        self.login(1)
        self.assertEqual(self.client.get('/api/tasks/summary').json['oldest_pending']['task_id'], task_id)

    def test_history_template_and_nested_attachment_scope(self):
        a, b = self.job(), self.job(2)
        for data in (a, b):
            mb.record_send(recipient='kol@example.com', material_id='', material_name='',
                           sender_account_id=1, sender_email='shared@example.com',
                           job_id=data['job']['id'], item_id=data['items'][0]['id'], mode='outreach')
        self.assertEqual(len(self.client.get('/api/mail-blaster/history').json['items']), 1)
        with as_user(2):
            template = mb.save_template('private', 'Bob', 'body', '')[0]
            attachment = mb.store_attachment(b'other private', 'secret.txt')
        self.client.delete(f'/api/mail-blaster/templates/{template["id"]}')
        self.assertIsNotNone(db.query_one('SELECT id FROM mb_templates WHERE id=%s', (template['id'],)))
        response = self.client.put(f'/api/mail-blaster/jobs/{a["job"]["id"]}', json={
            'items':[{'id':a['items'][0]['id'], 'attachments':[attachment]}]})
        self.assertEqual(response.status_code, 404)

    def test_migration_idempotent_and_page_render(self):
        mb.ensure_schema()
        for url in ['/kol-outreach','/mail-blaster']:
            self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(db.query_one('SELECT COUNT(*) c FROM mb_schema_versions')['c'],1)

    def test_recovery_releases_failed_queue_and_preserves_active_send(self):
        a = self.job()
        jid, iid = a['job']['id'], a['items'][0]['id']
        task = mb.enqueue_job(jid, 1)
        db.execute("UPDATE task_queue SET status='failed' WHERE task_id=%s", (task,))
        mb.reset_stuck_items()
        self.assertEqual(mb.load_job(jid)['job']['status'], 'done')
        task = mb.enqueue_job(jid, 1)
        db.execute("UPDATE task_queue SET status='processing' WHERE task_id=%s", (task,))
        db.execute("UPDATE mb_jobs SET status='sending' WHERE id=%s", (jid,))
        db.execute("UPDATE mb_items SET status='sending' WHERE id=%s", (iid,))
        mb.reset_stuck_items()
        self.assertEqual(mb.load_job(jid)['items'][0]['status'], 'sending')
        db.execute("UPDATE task_queue SET status='pending' WHERE task_id=%s", (task,))
        mb.reset_stuck_items()
        state = mb.load_job(jid)
        self.assertEqual(state['items'][0]['status'], 'unknown')
        self.assertEqual(state['job']['status'], 'queued')

    def test_upgrade_populated_legacy_tables(self):
        a = self.job()
        iid = self.sent(a, '<legacy-original@example.com>')
        tid = inbox._thread_for('kol@example.com', iid)
        self.message(1, '<legacy-reply@example.com>', '<legacy-original@example.com>')
        # Reconstruct the previous schema only in the validated disposable database.
        with db.get_db_cursor() as cur:
            for sql in (
                'DROP TABLE mb_account_members, mb_attachment_members',
                'ALTER TABLE mb_threads DROP COLUMN legacy_locked',
                'ALTER TABLE mb_threads ADD CONSTRAINT mb_threads_kol_email_key UNIQUE(kol_email)',
                'ALTER TABLE mb_templates DROP COLUMN user_id',
                'CREATE UNIQUE INDEX uq_mb_templates_mode_name ON mb_templates(mode,name)',
                'ALTER TABLE mb_jobs DROP COLUMN name',
                'DROP INDEX idx_mb_jobs_owner',
                'DELETE FROM mb_schema_versions',
                "UPDATE mb_inbox_messages SET dedupe_key=message_id",
                "UPDATE mb_items SET status='failed', error='进程在发送途中退出，未知'",
            ):
                cur.execute(sql)
        mb.ensure_schema()
        self.assertTrue(db.query_one('SELECT legacy_locked FROM mb_threads WHERE id=%s',(tid,))['legacy_locked'])
        self.assertEqual(mb.load_job(a['job']['id'])['items'][0]['status'], 'unknown')
        self.assertEqual(db.query_one('SELECT COUNT(*) c FROM mb_account_members')['c'], 0)
        self.assertIsNone(self.message(1, '<legacy-reply@example.com>'))
        self.assertEqual(db.query_one('SELECT COUNT(*) c FROM mb_inbox_messages')['c'], 1)


if __name__ == '__main__':
    unittest.main()
