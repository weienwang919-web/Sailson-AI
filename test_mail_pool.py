import unittest
from unittest.mock import patch

import mail_access as access
import mail_blaster_service as mb


class MaterialPoolTests(unittest.TestCase):
    def setUp(self):
        self.base = dict(enabled=True, status='ready', auth_mode='password', has_password=True)
        self.accounts = [
            dict(self.base, id=1, email='one@hotmail.com', provider='outlook'),
            dict(self.base, id=2, email='two@163.com', provider='163'),
            dict(self.base, id=3, email='old@outlook.com', provider='outlook'),
            dict(self.base, id=4, email='old@sailson.com', provider='aliyun_qiye'),
            dict(self.base, id=5, email='hidden@hotmail.com', provider='outlook', hidden=True),
            dict(self.base, id=6, email='draft@hotmail.com', provider='outlook', status='draft'),
        ]

    def test_default_pool_only_uses_ready_hotmail(self):
        with patch.object(mb, 'list_accounts', return_value=self.accounts):
            self.assertEqual([a['id'] for a in mb.material_sender_pool(False)], [1])

    def test_replacement_pool_only_uses_163(self):
        with patch.object(mb, 'list_accounts', return_value=self.accounts):
            self.assertEqual([a['id'] for a in mb.material_sender_pool(True)], [2])

    def test_hidden_accounts_are_not_usable(self):
        self.assertFalse(mb.usable_account(self.accounts[4]))

    def test_mixed_case_hotmail_is_supported(self):
        self.assertTrue(mb.material_account_matches({'email': 'One@Hotmail.COM'}, False))

    def test_list_hides_archived_accounts_unless_requested(self):
        with patch.object(mb.db, 'query_all', return_value=[]) as query:
            mb.list_accounts()
            self.assertIn('hidden = FALSE', query.call_args.args[0])
            mb.list_accounts(include_hidden=True)
            self.assertNotIn('hidden = FALSE', query.call_args.args[0])
            mb.list_accounts(include_hidden=True, only_sendable=True)
            self.assertIn('hidden = FALSE', query.call_args.args[0])

    def test_material_list_is_shared_and_excludes_sailson(self):
        with patch.object(mb.mail_access, 'account_scope') as account_scope, \
                patch.object(mb.db, 'query_all', return_value=[]) as query:
            mb.list_accounts(purpose='material')
        account_scope.assert_not_called()
        sql, args = query.call_args.args
        self.assertIn('email NOT ILIKE %s', sql)
        self.assertNotIn('purpose IN', sql)
        self.assertEqual(args, ('%@sailson.com',))

    def test_outreach_list_remains_member_scoped(self):
        with patch.object(mb.mail_access, 'account_scope', return_value=('member_scope', [9])), \
                patch.object(mb.db, 'query_all', return_value=[]) as query:
            mb.list_accounts(purpose='outreach')
        sql, args = query.call_args.args
        self.assertIn('WHERE member_scope', sql)
        self.assertIn("purpose IN (%s, 'both')", sql)
        self.assertEqual(args, (9, 'outreach'))

    def test_material_account_validation_ignores_membership_and_purpose(self):
        row = {'id': 7, 'email': 'shared@hotmail.com', 'purpose': 'outreach'}
        with patch.object(access, 'account_scope') as account_scope, \
                patch.object(access.db, 'query_one', return_value=row):
            access.require_account(7, 'material', access.Actor(99))
        account_scope.assert_not_called()

    def test_material_account_validation_rejects_sailson(self):
        row = {'id': 7, 'email': 'Internal@Sailson.com', 'purpose': 'both'}
        with patch.object(access.db, 'query_one', return_value=row):
            with self.assertRaisesRegex(access.AccessDenied, 'Sailson'):
                access.require_account(7, 'material', access.Actor(99))

    def test_legacy_templates_remain_visible_to_users(self):
        rows = [{'id': 1, 'user_id': None}, {'id': 2, 'user_id': 9}]
        token = access.current_actor.set(access.Actor(9))
        try:
            with patch.object(mb.db, 'query_all', return_value=rows) as query:
                templates = mb.list_templates('material')
        finally:
            access.current_actor.reset(token)
        sql, args = query.call_args.args
        self.assertIn('(user_id = %s OR user_id IS NULL)', sql)
        self.assertEqual(args, ['material', 9])
        self.assertEqual([(t['is_shared'], t['can_delete']) for t in templates],
                         [(True, False), (False, True)])

    def test_admin_still_sees_and_can_delete_all_templates(self):
        rows = [{'id': 1, 'user_id': None}]
        token = access.current_actor.set(access.Actor(1, admin=True))
        try:
            with patch.object(mb.db, 'query_all', return_value=rows) as query:
                templates = mb.list_templates('material')
        finally:
            access.current_actor.reset(token)
        sql, args = query.call_args.args
        self.assertIn('AND TRUE', sql)
        self.assertEqual(args, ['material'])
        self.assertTrue(templates[0]['can_delete'])

    def test_hidden_migration_is_in_schema_check(self):
        self.assertIn(('mb_sender_accounts', 'hidden'), mb._LATEST_COLUMNS)

    def test_no_matching_pool_fails_before_excel_parse(self):
        with patch.object(mb, 'list_accounts', return_value=[self.accounts[2]]), \
                patch.object(mb, 'parse_material_xlsx') as parse:
            with self.assertRaisesRegex(ValueError, 'Hotmail'):
                mb.create_job_from_excel(file_bytes=b'invalid')
        parse.assert_not_called()

    def test_hiding_keeps_credentials_and_enabled_state(self):
        row = dict(id=10, email='old@outlook.com', provider='outlook', enabled=True,
                   auth_mode='xoauth2', encrypted_client_id='encrypted',
                   encrypted_refresh_token='encrypted', status='ready')
        with patch.object(mb, 'get_account', return_value=row), \
                patch.object(mb.db, 'execute') as execute, \
                patch.object(mb, 'serialize_account', side_effect=lambda x: x):
            mb.update_account(10, {'hidden': True})
        sql, values = execute.call_args.args
        self.assertTrue(values['hidden'])
        self.assertTrue(values['enabled'])
        self.assertNotIn('encrypted_refresh_token', sql)
        self.assertNotIn("status = 'draft'", sql)


if __name__ == '__main__':
    unittest.main()
