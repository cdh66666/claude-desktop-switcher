"""Regression tests for live-usage credential selection and feedback."""

import base64
import json
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import claude_switcher as switcher  # noqa: E402


ACCOUNT_A = '11111111-1111-1111-1111-111111111111'
ACCOUNT_B = '22222222-2222-2222-2222-222222222222'
ORG_A = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
ORG_B = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'


def key(account=ACCOUNT_A, org=ORG_A, scope='user:inference user:profile'):
    return f'acct:{account}|client:{org}:https://api.anthropic.com:{scope}'


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        state = {'os_crypt': {'encrypted_key': base64.b64encode(b'DPAPIkey').decode()}}
        (self.root / 'Local State').write_text(json.dumps(state), encoding='utf-8')
        self.cfg = self.root / 'config.json'
        self.patch_dir = mock.patch.object(switcher, 'CLAUDE_DIR', self.root)
        self.patch_dir.start()
        self.addCleanup(self.patch_dir.stop)
        self.dpapi = mock.patch.object(switcher.UsageFetcher, '_dpapi_decrypt', return_value=b'key')
        self.dpapi.start()
        self.addCleanup(self.dpapi.stop)

    def config(self, **values):
        self.cfg.write_text(json.dumps(values), encoding='utf-8')

    @staticmethod
    def blob():
        return base64.b64encode(b'v10' + b'n' * 12 + b'x' * 16).decode()

    def get(self, decoded):
        with mock.patch.object(switcher.UsageFetcher, '_aes_gcm_decrypt',
                               return_value=json.dumps(decoded).encode()):
            return switcher.UsageFetcher.get_account_info(self.cfg)

    def test_v2_selects_inference_scope_for_current_account(self):
        self.config(**{'oauth:tokenCacheV2': self.blob(), 'lastKnownAccountUuid': ACCOUNT_A})
        expiry = (time.time() + 3600) * 1000
        entries = {
            key(scope='user:profile'): {'token': 'profile-only', 'expiresAt': expiry},
            key(account=ACCOUNT_B): {'token': 'other-account', 'expiresAt': expiry},
            key(): {'token': 'correct', 'expiresAt': expiry, 'subscriptionType': 'pro'},
        }
        result = self.get(entries)
        self.assertEqual(result['token'], 'correct')
        self.assertEqual(result['plan'], 'Pro')

    def test_v1_remains_supported(self):
        self.config(**{'oauth:tokenCache': self.blob()})
        result = self.get({key(): {'token': 'old-format', 'expiresAt': (time.time()+3600)*1000}})
        self.assertEqual(result['token'], 'old-format')

    def test_falls_back_to_v1_if_v2_has_no_inference_permission(self):
        self.config(**{'oauth:tokenCacheV2': self.blob(), 'oauth:tokenCache': self.blob()})
        expiry = (time.time() + 3600) * 1000
        payloads = {'oauth:tokenCacheV2': {key(scope='user:profile'): {'token': 'v2', 'expiresAt': expiry}},
                    'oauth:tokenCache': {key(): {'token': 'v1', 'expiresAt': expiry}}}
        with mock.patch.object(switcher.UsageFetcher, 'decrypt_oauth_cache',
                               side_effect=lambda _path, field: payloads[field]):
            result = switcher.UsageFetcher.get_account_info(self.cfg)
        self.assertEqual(result['token'], 'v1')

    def test_missing_expired_or_ambiguous_credentials_do_not_get_sent(self):
        self.config()
        self.assertIsNone(switcher.UsageFetcher.get_account_info(self.cfg)['token'])
        self.config(**{'oauth:tokenCacheV2': self.blob()})
        expired = self.get({key(): {'token': 'expired', 'expiresAt': (time.time()-60)*1000}})
        self.assertIsNone(expired['token'])
        self.assertIn('过期', expired['error'])
        no_expiry = self.get({key(): {'token': 'unknown', 'expiresAt': 0}})
        self.assertIsNone(no_expiry['token'])
        expiry = (time.time()+3600)*1000
        ambiguous = self.get({key(): {'token': 'a', 'expiresAt': expiry},
                              key(org=ORG_B): {'token': 'b', 'expiresAt': expiry}})
        self.assertIsNone(ambiguous['token'])
        self.assertIn('多个', ambiguous['error'])


class UsageTests(unittest.TestCase):
    def test_live_response_supplies_real_utilization_and_reset_times(self):
        response = types.SimpleNamespace(status_code=200, headers={
            'anthropic-ratelimit-unified-5h-utilization': '0.23',
            'anthropic-ratelimit-unified-5h-reset': '1800000000',
            'anthropic-ratelimit-unified-7d-utilization': '0.08',
            'anthropic-ratelimit-unified-7d-reset': '1800100000',
        })
        with mock.patch('requests.post', return_value=response):
            result = switcher.UsageFetcher.fetch_usage('test-token')
        self.assertTrue(result['ok'])
        self.assertEqual((result['session_pct'], result['weekly_pct']), (23, 8))
        self.assertEqual((result['session_reset_epoch'], result['weekly_reset_epoch']),
                         (1800000000, 1800100000))

    def test_http_success_without_usage_is_not_a_success(self):
        response = types.SimpleNamespace(status_code=200, headers={})
        with mock.patch('requests.post', return_value=response):
            result = switcher.UsageFetcher.fetch_usage('test-token')
        self.assertFalse(result.get('ok', False))
        self.assertIn('未返回', result['error'])

    def test_429_does_not_invent_a_full_five_hour_window(self):
        response = types.SimpleNamespace(
            status_code=429,
            headers={'anthropic-ratelimit-unified-5h-utilization': '0.25',
                     'anthropic-ratelimit-unified-7d-utilization': '1.0'},
            json=lambda: {'error': {'type': 'rate_limit_error', 'message': 'limited'}},
        )
        with mock.patch('requests.post', return_value=response):
            result = switcher.UsageFetcher.fetch_usage('test-token')
        self.assertEqual(result['session_pct'], 25)
        self.assertEqual(result['weekly_pct'], 100)

    def test_all_refresh_reports_missing_credentials_instead_of_zero_of_zero(self):
        statuses = []
        fake = types.SimpleNamespace(
            _last_usage_fetch=0,
            pm=types.SimpleNamespace(get_profiles=lambda: ['1', '2'], get_current=lambda: '2'),
            refresh_btn=types.SimpleNamespace(configure=lambda **_: None),
            set_status=statuses.append,
            _refresh_profiles=lambda fetch_usage=False: None,
        )
        fake._on_all_usage_done = lambda result: switcher.App._on_all_usage_done(fake, result)
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / '1'
            profile.mkdir()
            (profile / 'config.json').write_text('{}', encoding='utf-8')
            with mock.patch.object(switcher, 'INSTANCE_DIR', Path(tmp)), \
                 mock.patch.object(switcher.UsageFetcher, 'get_account_info',
                                   return_value={'token': None, 'error': '账号配置中没有 OAuth 登录令牌'}):
                switcher.App.on_refresh_usage(fake)
        self.assertIn('0/2', statuses[-1])
        self.assertIn('没有 OAuth 登录令牌', statuses[-1])

    def test_switch_reports_launch_failure_after_loading_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / '1'
            target.mkdir()
            (target / 'config.json').write_text('{}', encoding='utf-8')
            pm = switcher.ProfileManager.__new__(switcher.ProfileManager)
            pm.get_current = mock.Mock(return_value='2')
            pm.session_storage_error = mock.Mock(return_value=None)
            pm.stop_claude = mock.Mock(return_value=True)
            pm.save_session = mock.Mock()
            pm.load_session = mock.Mock()
            pm.set_current = mock.Mock()
            pm.start_claude = mock.Mock(return_value=False)
            with mock.patch.object(switcher, 'INSTANCE_DIR', root):
                ok, message = pm.switch_profile('1')
        self.assertFalse(ok)
        self.assertIn('未能启动', message)
        pm.load_session.assert_called_once_with('1')

    def test_redirected_session_file_blocks_switch_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            live = base / 'Claude'
            live.mkdir()
            config = live / 'config.json'
            config.write_text('{}', encoding='utf-8')
            elsewhere = base / 'redirected-config.json'
            elsewhere.write_text('{}', encoding='utf-8')
            profiles = base / 'profiles'
            target = profiles / '1'
            target.mkdir(parents=True)
            (target / 'config.json').write_text('{}', encoding='utf-8')
            original_resolve = Path.resolve

            def resolve(path, strict=False):
                if path == config:
                    return original_resolve(elsewhere, strict=strict)
                return original_resolve(path, strict=strict)

            pm = switcher.ProfileManager.__new__(switcher.ProfileManager)
            pm.get_current = mock.Mock(return_value='2')
            pm.stop_claude = mock.Mock(return_value=True)
            pm.save_session = mock.Mock()
            pm.load_session = mock.Mock()
            with mock.patch.object(switcher, 'CLAUDE_DIR', live), \
                 mock.patch.object(switcher, 'INSTANCE_DIR', profiles), \
                 mock.patch.object(Path, 'resolve', autospec=True, side_effect=resolve):
                ok, message = pm.switch_profile('1')
        self.assertFalse(ok)
        self.assertIn('重定向', message)
        pm.stop_claude.assert_not_called()
        pm.save_session.assert_not_called()
        pm.load_session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
