"""Mail resource authorization shared by HTTP handlers and workers."""

from contextvars import ContextVar
from dataclasses import dataclass

import database as db


class AccessDenied(ValueError):
    pass


@dataclass(frozen=True)
class Actor:
    user_id: int
    admin: bool = False

    @classmethod
    def load(cls, user_id):
        row = db.query_one("SELECT id, role, permissions FROM users WHERE id = %s", (user_id,))
        if not row or (row['role'] != 'admin' and
                       'mail_blaster' not in (row['permissions'] or '').split(',')):
            raise AccessDenied('没有邮件功能权限')
        return cls(row['id'], row['role'] == 'admin')


current_actor = ContextVar('mail_actor', default=None)


def owner_scope(column, actor=None):
    actor = actor or current_actor.get()
    return ('TRUE', []) if actor is None or actor.admin else (f'{column} = %s', [actor.user_id])


def account_scope(column='mb_sender_accounts.id', actor=None):
    actor = actor or current_actor.get()
    if actor is None or actor.admin:
        return 'TRUE', []
    return (f'EXISTS (SELECT 1 FROM mb_account_members am '
            f'WHERE am.account_id = {column} AND am.user_id = %s)', [actor.user_id])


def mail_task_scope(user_id):
    unrelated = "COALESCE(function_type, '') NOT LIKE 'mail_blaster%%'"
    try:
        actor = Actor.load(user_id)
    except AccessDenied:
        return unrelated, []
    if actor.admin:
        return 'TRUE', []
    return f'({unrelated} OR user_id = %s)', [actor.user_id]


def thread_scope(alias='t'):
    actor = current_actor.get()
    if actor is None or actor.admin:
        return 'TRUE', []
    return (f'NOT {alias}.legacy_locked AND EXISTS (SELECT 1 FROM mb_jobs aj '
            f'WHERE aj.id = {alias}.job_id AND aj.user_id = %s)', [actor.user_id])


def require_job(job_id, actor=None):
    scope, args = owner_scope('user_id', actor)
    row = db.query_one(f'SELECT * FROM mb_jobs WHERE id = %s AND {scope}', [job_id] + args)
    if not row:
        raise AccessDenied('活动不存在或无权访问')
    return row


def require_account(account_id, mode='', actor=None):
    scope, args = account_scope(actor=actor)
    row = db.query_one(f'SELECT id, purpose FROM mb_sender_accounts WHERE id = %s AND {scope}',
                       [account_id] + args)
    if not row or (mode and row['purpose'] not in (mode, 'both')):
        raise AccessDenied('发件邮箱未授权或用途不匹配，请联系管理员')


def require_attachment(attachment_id):
    actor = current_actor.get()
    if actor is None or actor.admin:
        return
    row = db.query_one("""
        SELECT 1 FROM mb_attachment_members WHERE attachment_id = %s AND user_id = %s
        UNION ALL
        SELECT 1 FROM mb_job_attachments a JOIN mb_jobs j ON j.id = a.job_id
          WHERE a.attachment_id = %s AND j.user_id = %s
        UNION ALL
        SELECT 1 FROM mb_item_attachments a JOIN mb_items i ON i.id = a.item_id
          JOIN mb_jobs j ON j.id = i.job_id WHERE a.attachment_id = %s AND j.user_id = %s
        LIMIT 1
    """, (attachment_id, actor.user_id) * 3)
    if not row:
        raise AccessDenied('附件不存在或无权访问')


def validate_payload(data, job=None):
    mode = (job or {}).get('mode', 'outreach')
    if data.get('sender_account_id'):
        require_account(data['sender_account_id'], mode)
    for spec in data.get('attachments') or []:
        require_attachment(spec.get('id'))
    for item in data.get('items') or []:
        if item.get('sender_account_id'):
            require_account(item['sender_account_id'], mode)
        for spec in item.get('attachments') or []:
            require_attachment(spec.get('id'))


def install(app):
    from flask import g, jsonify, request, session

    @app.before_request
    def authorize_mail_request():
        endpoint = request.endpoint or ''
        task_id = (request.view_args or {}).get('task_id')
        if task_id:
            task = db.query_one('SELECT user_id, function_type FROM task_queue WHERE task_id = %s',
                                (str(task_id),))
            if task and (task.get('function_type') or '').startswith('mail_blaster'):
                try:
                    actor = Actor.load(session.get('user_id'))
                    if not actor.admin and actor.user_id != task['user_id']:
                        raise AccessDenied('任务不存在或无权访问')
                except AccessDenied as exc:
                    return jsonify(status='error', message=str(exc)), 404
        if not (endpoint.startswith('api_mb_') or endpoint in
                ('mail_blaster_page', 'kol_outreach_page', 'kol_inbox_page')):
            return
        try:
            actor = Actor.load(session.get('user_id'))
            g.mail_actor_token = current_actor.set(actor)
            # Credential management and unassigned mail triage are admin-only in v1.
            admin_only = endpoint in {
                'api_mb_create_account', 'api_mb_update_account', 'api_mb_delete_account',
                'api_mb_test_account', 'api_mb_test_imap', 'api_mb_bulk_import',
                'api_mb_account_members', 'api_mb_mail_members', 'api_mb_claim_message', 'api_mb_claim_candidates',
                'api_mb_poll_now', 'api_mb_list_suppression', 'api_mb_add_suppression',
                'api_mb_remove_suppression', 'api_mb_phones',
            }
            if admin_only and not actor.admin:
                return jsonify(status='error', message='此操作需要管理员权限'), 403
            ids = request.view_args or {}
            job = None
            if 'job_id' in ids:
                job = require_job(ids['job_id'])
            if 'item_id' in ids:
                row = db.query_one('SELECT job_id FROM mb_items WHERE id = %s', (ids['item_id'],))
                job = require_job(row['job_id'] if row else None)
            if 'thread_id' in ids:
                scope, args = thread_scope()
                if not db.query_one(f'SELECT t.id FROM mb_threads t WHERE t.id = %s AND {scope}',
                                    [ids['thread_id']] + args):
                    raise AccessDenied('会话不存在或无权访问')
            if 'inbox_id' in ids and not actor.admin:
                scope, args = thread_scope()
                if not db.query_one('SELECT m.id FROM mb_inbox_messages m JOIN mb_threads t '
                                    f'ON t.id = m.thread_id WHERE m.id = %s AND {scope}',
                                    [ids['inbox_id']] + args):
                    raise AccessDenied('邮件不存在或无权访问')
            if 'attachment_id' in ids:
                require_attachment(ids['attachment_id'])
            if 'image_id' in ids and not actor.admin:
                if not db.query_one('SELECT i.id FROM mb_items i JOIN mb_jobs j ON j.id = i.job_id '
                                    'WHERE i.image_id = %s AND j.user_id = %s LIMIT 1',
                                    (ids['image_id'], actor.user_id)):
                    raise AccessDenied('图片不存在或无权访问')
            if endpoint in ('api_mb_preview', 'api_mb_send', 'api_mb_resend',
                            'api_mb_create_outreach_job', 'api_mb_save_job'):
                data = request.get_json(silent=True) or {}
                if not isinstance(data, dict):
                    return jsonify(status='error', message='请求格式错误'), 400
                validate_payload(data, job)
        except AccessDenied as exc:
            return jsonify(status='error', message=str(exc)), 404

    @app.teardown_request
    def clear_mail_actor(error=None):
        token = g.pop('mail_actor_token', None)
        if token is not None:
            current_actor.reset(token)


def ensure_schema():
    """Versioned, transactional migration. Existing mixed threads stay quarantined."""
    db.execute('CREATE TABLE IF NOT EXISTS mb_schema_versions (version INTEGER PRIMARY KEY)')
    if db.query_one('SELECT version FROM mb_schema_versions WHERE version = 1'):
        return
    with db.get_db_cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(6700, 1)')
        cur.execute('SELECT version FROM mb_schema_versions WHERE version = 1')
        if cur.fetchone():
            return
        for sql in (
            '''CREATE TABLE mb_account_members (
                account_id INTEGER REFERENCES mb_sender_accounts(id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                PRIMARY KEY(account_id, user_id))''',
            '''CREATE TABLE mb_attachment_members (
                attachment_id INTEGER REFERENCES mb_attachments(id) ON DELETE CASCADE,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                PRIMARY KEY(attachment_id, user_id))''',
            'ALTER TABLE mb_threads ADD COLUMN legacy_locked BOOLEAN NOT NULL DEFAULT TRUE',
            'ALTER TABLE mb_threads ALTER COLUMN legacy_locked SET DEFAULT FALSE',
            'ALTER TABLE mb_threads DROP CONSTRAINT IF EXISTS mb_threads_kol_email_key',
            'CREATE UNIQUE INDEX uq_mb_thread_item ON mb_threads(item_id) WHERE NOT legacy_locked',
            'ALTER TABLE mb_templates ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE',
            'DROP INDEX IF EXISTS uq_mb_templates_mode_name',
            'CREATE UNIQUE INDEX uq_mb_templates_owner ON mb_templates(user_id, mode, name)',
            "ALTER TABLE mb_jobs ADD COLUMN name VARCHAR(200) NOT NULL DEFAULT ''",
            'CREATE INDEX idx_mb_jobs_owner ON mb_jobs(user_id, created_at DESC)',
            "UPDATE mb_items SET status = 'unknown' WHERE status = 'failed' "
            "AND error LIKE '进程在发送途中退出%';",
            '''UPDATE mb_inbox_messages SET dedupe_key = 'a:' || COALESCE(account_id, 0) ||
                CASE WHEN COALESCE(message_id, '') <> '' THEN ':m:' || md5(message_id)
                ELSE ':u:' || folder || ':0:' || COALESCE(uid, 0) END''',
        ):
            cur.execute(sql)
        cur.execute('INSERT INTO mb_schema_versions(version) VALUES (1)')
