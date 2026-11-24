"""Load settings in fresh interpreters without changing this test process."""
import json
import os
import secrets
import subprocess
import sys

from django.test import SimpleTestCase


class EnvironmentSettingsTests(SimpleTestCase):
    names = (
        'SECRET_KEY', 'DEBUG', 'ALLOWED_HOSTS', 'CSRF_TRUSTED_ORIGINS',
        'HTTPS_ENABLED', 'SECURE_HSTS_SECONDS', 'SECURE_HSTS_INCLUDE_SUBDOMAINS',
        'SECURE_HSTS_PRELOAD', 'DJANGO_SETTINGS_MODULE',
    )
    keys = (
        'DEBUG', 'ALLOWED_HOSTS', 'CSRF_TRUSTED_ORIGINS', 'SECURE_SSL_REDIRECT',
        'SESSION_COOKIE_SECURE', 'CSRF_COOKIE_SECURE', 'SECURE_HSTS_SECONDS',
        'SECURE_HSTS_INCLUDE_SUBDOMAINS', 'SECURE_HSTS_PRELOAD',
    )

    def load(self, values=None, secret=True):
        env = os.environ.copy()
        for name in self.names:
            env.pop(name, None)
        if secret:
            env['SECRET_KEY'] = secrets.token_urlsafe(64)
        env.update(values or {})
        program = ('import json; from smartkitap import settings as s; '
                   f'print(json.dumps({{k: getattr(s, k) for k in {self.keys!r}}}))')
        return subprocess.run([sys.executable, '-c', program], env=env, text=True,
                              capture_output=True, check=False)

    def settings(self, values=None):
        result = self.load(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_missing_secret_fails_clearly(self):
        result = self.load(secret=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Set the SECRET_KEY environment variable', result.stderr)

    def test_boolean_values_and_invalid_input(self):
        for value, expected in [('1', True), (' TRUE ', True), ('yes', True), ('on', True),
                                ('0', False), (' FALSE ', False), ('no', False), ('off', False)]:
            with self.subTest(value=value):
                settings = self.settings({'DEBUG': value, 'HTTPS_ENABLED': value,
                                          'SECURE_HSTS_INCLUDE_SUBDOMAINS': value,
                                          'SECURE_HSTS_PRELOAD': value})
                self.assertEqual(settings['DEBUG'], expected)
                self.assertEqual(settings['SECURE_SSL_REDIRECT'], expected)
                self.assertEqual(settings['SECURE_HSTS_INCLUDE_SUBDOMAINS'], expected)
                self.assertEqual(settings['SECURE_HSTS_PRELOAD'], expected)
        for name in ('DEBUG', 'HTTPS_ENABLED', 'SECURE_HSTS_INCLUDE_SUBDOMAINS', 'SECURE_HSTS_PRELOAD'):
            with self.subTest(name=name):
                result = self.load({name: 'invalid'})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f'{name} must be true or false', result.stderr)

    def test_host_and_origin_lists(self):
        settings = self.settings({'ALLOWED_HOSTS': ' localhost,example.test, , [::1] ',
                                  'CSRF_TRUSTED_ORIGINS': ' https://example.test, ,http://localhost:8000 '})
        self.assertEqual(settings['ALLOWED_HOSTS'], ['localhost', 'example.test', '[::1]'])
        self.assertEqual(settings['CSRF_TRUSTED_ORIGINS'], ['https://example.test', 'http://localhost:8000'])
        empty = self.settings({'ALLOWED_HOSTS': '', 'CSRF_TRUSTED_ORIGINS': ''})
        self.assertEqual(empty['ALLOWED_HOSTS'], [])
        self.assertEqual(empty['CSRF_TRUSTED_ORIGINS'], [])

    def test_local_http_and_https_without_hsts(self):
        local = self.settings()
        self.assertTrue(local['DEBUG'])
        for key in ('SECURE_SSL_REDIRECT', 'SESSION_COOKIE_SECURE', 'CSRF_COOKIE_SECURE',
                    'SECURE_HSTS_INCLUDE_SUBDOMAINS', 'SECURE_HSTS_PRELOAD'):
            self.assertFalse(local[key], key)
        self.assertEqual(local['SECURE_HSTS_SECONDS'], 0)
        https = self.settings({'HTTPS_ENABLED': 'true'})
        for key in ('SECURE_SSL_REDIRECT', 'SESSION_COOKIE_SECURE', 'CSRF_COOKIE_SECURE'):
            self.assertTrue(https[key], key)
        self.assertEqual(https['SECURE_HSTS_SECONDS'], 0)
        self.assertFalse(https['SECURE_HSTS_INCLUDE_SUBDOMAINS'])
        self.assertFalse(https['SECURE_HSTS_PRELOAD'])

    def test_hsts_is_independent_and_validated(self):
        hsts = self.settings({'SECURE_HSTS_SECONDS': '3600', 'SECURE_HSTS_INCLUDE_SUBDOMAINS': 'yes',
                              'SECURE_HSTS_PRELOAD': 'true'})
        self.assertEqual(hsts['SECURE_HSTS_SECONDS'], 3600)
        self.assertTrue(hsts['SECURE_HSTS_INCLUDE_SUBDOMAINS'])
        self.assertTrue(hsts['SECURE_HSTS_PRELOAD'])
        self.assertFalse(hsts['SECURE_SSL_REDIRECT'])
        for value in ('-1', 'abc', '1.5'):
            with self.subTest(value=value):
                result = self.load({'SECURE_HSTS_SECONDS': value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('SECURE_HSTS_SECONDS must be a non-negative integer', result.stderr)
