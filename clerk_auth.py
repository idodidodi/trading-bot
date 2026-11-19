"""Clerk session verification for the forthcoming authenticated Linux API.

Not wired into the production server until the API/identity migration is ready.
Only public JWKS keys are needed. Membership and endpoint permissions must still
be checked by the API using the returned application UUID on every request.
"""
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import jwt
import ssl
import certifi


class AuthenticationError(Exception):
    """Safe message for an HTTP 401 response; never includes token contents."""


class MembershipError(Exception):
    """Verified identity has no administrator-approved application mapping."""


@dataclass(frozen=True)
class Identity:
    subject: str
    session_id: str
    user_id: UUID


def _origin(value, *, allow_local=False):
    parsed = urlsplit(value)
    local = allow_local and parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1')
    if (not parsed.hostname or not (parsed.scheme == 'https' or local)
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('Expected an explicit HTTPS origin (HTTP loopback allowed for development)')
    return value


class SessionVerifier:
    def __init__(self, issuer, authorized_parties, resolve_user):
        self.issuer = _origin(issuer)
        self.parties = frozenset(_origin(party, allow_local=True) for party in authorized_parties)
        if not self.parties:
            raise ValueError('At least one authorized browser origin is required')
        self.resolve_user = resolve_user
        # Never derive this URL from untrusted token headers/claims. The bounded
        # JWKS cache refreshes on unknown key IDs; no indefinite per-key cache.
        self.keys = jwt.PyJWKClient(issuer + '/.well-known/jwks.json',
                                   cache_keys=False, lifespan=300, timeout=5,
                                   ssl_context=ssl.create_default_context(cafile=certifi.where()))

    def authenticate(self, authorization, origin):
        if origin not in self.parties:
            raise AuthenticationError('Unauthorized browser origin')
        if not isinstance(authorization, str) or len(authorization) > 16384:
            raise AuthenticationError('Invalid session')
        scheme, separator, token = authorization.partition(' ')
        if scheme.lower() != 'bearer' or not separator or not token or any(c.isspace() for c in token):
            raise AuthenticationError('Invalid session')
        try:
            header = jwt.get_unverified_header(token)
            if header.get('alg') != 'RS256' or not isinstance(header.get('kid'), str) or not header['kid']:
                raise AuthenticationError('Invalid session')
            key = self.keys.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=['RS256'], issuer=self.issuer,
                                options={'require': ['iss', 'sub', 'sid', 'iat', 'nbf', 'exp', 'azp']})
            if (claims['azp'] != origin or claims.get('sts') == 'pending'
                    or not isinstance(claims['sub'], str) or not claims['sub'].startswith('user_')
                    or not isinstance(claims['sid'], str) or not claims['sid'].startswith('sess_')):
                raise AuthenticationError('Invalid session')
        except jwt.PyJWTError:
            raise AuthenticationError('Invalid or unverifiable session') from None
        # Resolve on every request so revoked mappings take effect immediately.
        # Never trust email, public metadata, or an arbitrary UUID in the JWT.
        mapped = self.resolve_user(claims['sub'])
        if mapped is None:
            raise MembershipError('This account does not have dashboard access')
        try:
            user_id = UUID(str(mapped))
        except (ValueError, TypeError, AttributeError):
            raise MembershipError('Invalid application identity mapping') from None
        return Identity(claims['sub'], claims['sid'], user_id)
