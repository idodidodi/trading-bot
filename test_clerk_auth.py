import time
import unittest
from unittest.mock import Mock
from uuid import UUID

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from clerk_auth import AuthenticationError, MembershipError, SessionVerifier


class ClerkAuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.origin = 'https://dashboard.example.com'
        self.issuer = 'https://clerk.example.com'
        self.user_id = UUID('eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee')
        self.resolve = Mock(return_value=self.user_id)
        self.verifier = SessionVerifier(self.issuer, [self.origin], self.resolve)
        self.verifier.keys.fetch_data = Mock(return_value=self.jwks(self.private, 'first'))
        now = int(time.time())
        self.claims = dict(iss=self.issuer, sub='user_example', sid='sess_example',
                           azp=self.origin, iat=now, nbf=now-1, exp=now+60)

    def jwks(self, private, kid):
        key = jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key(), as_dict=True)
        return {'keys': [dict(key, kid=kid, use='sig', alg='RS256')]}

    def token(self, claims=None, key=None, kid='first'):
        return 'Bearer ' + jwt.encode(self.claims if claims is None else claims,
                                      key or self.private, algorithm='RS256', headers={'kid':kid})

    def test_valid_session_uses_only_server_mapping(self):
        self.claims.update(email='owner@example.com', user_id='attacker', public_metadata={'role':'owner'})
        identity = self.verifier.authenticate(self.token(), self.origin)
        self.assertEqual(identity.user_id, self.user_id)
        self.resolve.assert_called_once_with('user_example')

    def test_rejects_expired_future_wrong_issuer_origin_pending_and_non_sessions(self):
        for field, value in [('exp',0), ('nbf',time.time()+600), ('iat',time.time()+600),
                             ('iss','https://attacker.example'), ('azp','https://attacker.example'),
                             ('sts','pending'), ('sub','machine_example'), ('sid','')]:
            with self.subTest(field=field):
                with self.assertRaises(AuthenticationError):
                    self.verifier.authenticate(self.token(dict(self.claims, **{field:value})), self.origin)
        self.resolve.assert_not_called()

    def test_requires_all_session_claims(self):
        for field in self.claims:
            with self.subTest(field=field):
                claims = dict(self.claims)
                del claims[field]
                with self.assertRaises(AuthenticationError):
                    self.verifier.authenticate(self.token(claims), self.origin)

    def test_forged_signature_never_resolves_user(self):
        with self.assertRaises(AuthenticationError):
            self.verifier.authenticate(self.token(key=self.other), self.origin)
        self.resolve.assert_not_called()

    def test_rejects_unsigned_token_before_jwks_access(self):
        token = jwt.encode(self.claims, '', algorithm='none', headers={'kid':'first'})
        with self.assertRaises(AuthenticationError):
            self.verifier.authenticate('Bearer '+token, self.origin)
        self.verifier.keys.fetch_data.assert_not_called()

    def test_unknown_rotated_key_refreshes_jwks(self):
        self.verifier.keys.fetch_data.side_effect = [self.jwks(self.private,'first'), self.jwks(self.other,'rotated')]
        self.assertEqual(self.verifier.authenticate(self.token(key=self.other,kid='rotated'),self.origin).user_id,self.user_id)
        self.assertEqual(self.verifier.keys.fetch_data.call_count,2)

    def test_jwks_failure_is_not_authentication_success(self):
        self.verifier.keys.fetch_data.side_effect = jwt.PyJWKClientConnectionError('Unavailable')
        with self.assertRaises(AuthenticationError):
            self.verifier.authenticate(self.token(),self.origin)
        self.resolve.assert_not_called()

    def test_revoked_and_unmapped_users_denied(self):
        self.verifier.authenticate(self.token(),self.origin)
        self.resolve.return_value = None
        with self.assertRaises(MembershipError):
            self.verifier.authenticate(self.token(),self.origin)
        self.resolve.return_value = 'not-a-uuid'
        with self.assertRaises(MembershipError):
            self.verifier.authenticate(self.token(),self.origin)

    def test_rejects_missing_foreign_origin_and_malformed_headers(self):
        for origin in [None, 'null', 'https://attacker.example']:
            with self.assertRaises(AuthenticationError):
                self.verifier.authenticate(self.token(),origin)
        for header in [None, '', 'Basic test', 'Bearer ', 'Bearer x y', 'Bearer '+('x'*17000)]:
            with self.assertRaises(AuthenticationError):
                self.verifier.authenticate(header,self.origin)
        self.resolve.assert_not_called()

    def test_configuration_cannot_supply_urls_with_credentials_or_paths(self):
        for issuer in ['http://clerk.example.com','https://user:pass@example.com','https://example.com/path']:
            with self.assertRaises(ValueError):
                SessionVerifier(issuer,[self.origin],self.resolve)
        with self.assertRaises(ValueError):
            SessionVerifier(self.issuer,[],self.resolve)


if __name__ == '__main__':
    unittest.main()
