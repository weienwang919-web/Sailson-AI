import imaplib
import json
import base64
import smtplib
import time
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs

import mail_blaster_service as mb


class MailOAuthTests(unittest.TestCase):
    def setUp(self):
        mb._token_cache.clear()
        self.account = dict(id=1, email='test@hotmail.com', provider='outlook',
                            auth_mode='xoauth2', encrypted_client_id='client',
                            encrypted_refresh_token='refresh', encrypted_password='password',
                            imap_host='outlook.office365.com', imap_port=993, imap_ssl=True,
                            smtp_host='smtp-mail.outlook.com', smtp_port=587,
                            use_ssl=False, use_tls=True, enabled=True)
        self.decrypt = patch.object(mb.crypto_util, 'decrypt', side_effect=lambda x: x)
        self.decrypt.start()
        self.addCleanup(self.decrypt.stop)

    def test_four_field_import_selects_oauth(self):
        payload = mb.parse_import_line('test@hotmail.com----password----client----refresh')
        self.assertEqual(mb._normalize_account(payload)['auth_mode'], 'xoauth2')

    def test_scopes_are_requested_separately(self):
        for protocol, scope in [('smtp', mb.MICROSOFT_SCOPE), ('imap', mb.MICROSOFT_IMAP_SCOPE)]:
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(
                {'access_token': 'token', 'refresh_token': 'new'}).encode()
            with patch.object(mb.urllib.request, 'urlopen', return_value=response) as request:
                mb._request_access_token('microsoft', 'client', 'refresh', protocol)
            self.assertEqual(parse_qs(request.call_args.args[0].data.decode())['scope'], [scope])

    def test_token_cache_isolates_protocol_and_rotated_token(self):
        with patch.object(mb, '_request_access_token', side_effect=[
                ('smtp-token', time.time() + 3600, 'new'),
                ('imap-token', time.time() + 3600, 'new')]) as request, \
                patch.object(mb, '_rotate_refresh_token'):
            args = dict(provider='outlook', client_id='client', refresh_token='refresh')
            self.assertEqual(mb.get_access_token(**args), 'smtp-token')
            self.assertEqual(mb.get_access_token(**args, protocol='imap'), 'imap-token')
            args['refresh_token'] = 'new'
            self.assertEqual(mb.get_access_token(**args), 'smtp-token')
            self.assertEqual(request.call_count, 2)
            mb.invalidate_token('client', 'refresh')
            self.assertEqual(mb._token_cache, {})

    def test_imap_oauth_responds_once_and_never_uses_password(self):
        client = MagicMock()
        def authenticate(mechanism, callback):
            self.assertEqual(mechanism, 'XOAUTH2')
            self.assertEqual(callback(b''), b'user=test@hotmail.com\x01auth=Bearer token\x01\x01')
            self.assertEqual(callback(b'error challenge'), b'')
        client.authenticate.side_effect = authenticate
        with patch.object(imaplib, 'IMAP4_SSL', return_value=client), \
                patch.object(mb, 'get_access_token', return_value='token') as token:
            self.assertIs(mb.open_imap(self.account), client)
        self.assertEqual(token.call_args.kwargs['protocol'], 'imap')
        client.login.assert_not_called()

    def test_imap_rejection_closes_connection_and_invalidates(self):
        client = MagicMock()
        client.authenticate.side_effect = imaplib.IMAP4.error('AUTHENTICATE failed')
        with patch.object(imaplib, 'IMAP4_SSL', return_value=client), \
                patch.object(mb, 'get_access_token', return_value='token'), \
                patch.object(mb, 'invalidate_token') as invalidate:
            with self.assertRaises(imaplib.IMAP4.error):
                mb.open_imap(self.account)
        client.logout.assert_called_once()
        invalidate.assert_called_once_with('client', 'refresh')

    def test_password_imap_still_works(self):
        client = MagicMock()
        with patch.object(imaplib, 'IMAP4_SSL', return_value=client):
            mb.open_imap({**self.account, 'auth_mode': 'password'})
        client.login.assert_called_once_with('test@hotmail.com', 'password')
        client.authenticate.assert_not_called()

    def test_oauth_accounts_are_receivable_with_credentials(self):
        self.assertTrue(mb.serialize_account(self.account)['can_receive'])
        self.assertFalse(mb.serialize_account({**self.account, 'encrypted_refresh_token': None})['can_receive'])

    def test_bulk_import_upgrades_password_account(self):
        with patch.object(mb.db, 'query_one', return_value={**self.account, 'auth_mode': 'password'}), \
                patch.object(mb, 'update_account', return_value={'id': 1}) as update, \
                patch.object(mb, 'create_account') as create:
            result = mb.bulk_import('test@hotmail.com----password----client----refresh')
        self.assertEqual(result['updated'], [{'id': 1}])
        self.assertEqual(update.call_args.args[1]['auth_mode'], 'xoauth2')
        create.assert_not_called()

    def test_bulk_import_preserves_rotated_oauth_credentials(self):
        with patch.object(mb.db, 'query_one', return_value=self.account), \
                patch.object(mb, 'update_account') as update, \
                patch.object(mb, 'create_account', side_effect=ValueError('账号已存在')):
            result = mb.bulk_import('test@hotmail.com----password----client----refresh')
        update.assert_not_called()
        self.assertEqual(result['skipped'], ['test@hotmail.com'])

    def test_graph_fallback_only_for_disabled_smtp_auth(self):
        disabled = smtplib.SMTPAuthenticationError(535, b'5.7.139 SMTP AUTH disabled')
        self.assertTrue(mb._can_use_graph_fallback(self.account, disabled))
        self.assertFalse(mb._can_use_graph_fallback(self.account, smtplib.SMTPDataError(554, b'rejected')))
        self.assertFalse(mb._can_use_graph_fallback({**self.account, 'auth_mode': 'password'}, disabled))

    def test_recording_smtp_falls_back_after_closing_failed_connection(self):
        client = MagicMock()
        with patch.object(mb, '_SMTP', return_value=client), \
                patch.object(mb, '_do_login', side_effect=smtplib.SMTPAuthenticationError(535, b'5.7.139')), \
                patch.object(mb, '_GraphMailClient', return_value='graph'):
            self.assertEqual(mb._open_smtp_recording(self.account), 'graph')
        client.close.assert_called_once()

    def test_graph_preserves_mime_and_requires_accepted_response(self):
        with patch.object(mb, 'get_account', return_value=self.account), \
                patch.object(mb, 'get_access_token', return_value='token'), \
                patch.object(mb._GraphMailClient, '_request', return_value=(200, b'{}')):
            client = mb._GraphMailClient(self.account)
        mime = 'From: test@hotmail.com\r\nTo: test@hotmail.com\r\n\r\nTest'
        with patch.object(client, '_request', return_value=(202, b'')) as request:
            client.sendmail(self.account['email'], [self.account['email']], mime)
        self.assertEqual(base64.b64decode(request.call_args.args[1]), mime.encode())
        self.assertEqual(client.last_data_response[0], 202)
        with patch.object(client, '_request', return_value=(200, b'{}')):
            with self.assertRaises(mb.OAuthError):
                client.sendmail(self.account['email'], [self.account['email']], mime)

    def test_graph_rejects_large_payload_before_sending(self):
        client = object.__new__(mb._GraphMailClient)
        client.email = self.account['email']
        with patch.object(client, '_request') as request:
            with self.assertRaises(ValueError):
                client.sendmail(client.email, [client.email], b'x' * (3 * 1024 * 1024))
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
