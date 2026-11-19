"""Allowlisted public Clerk configuration; never serialize environment mappings."""
import base64
import re
from urllib.parse import urlsplit
from uuid import UUID


def clerk_config(value):
    key = value.get('clerkPublishableKey', '')
    if not re.fullmatch(r'pk_(test|live)_[A-Za-z0-9=]+', key):
        raise ValueError('A Clerk publishable key is required')
    try:
        domain = base64.b64decode(key.split('_')[2] + '=' * (-len(key.split('_')[2]) % 4), validate=True).decode('ascii')
    except (ValueError, UnicodeError):
        raise ValueError('Invalid Clerk publishable key') from None
    if not re.fullmatch(r'(?:[a-zA-Z0-9-]+\.)+[a-zA-Z0-9-]+\$', domain):
        raise ValueError('Invalid Clerk frontend domain')
    api = value.get('apiOrigin', '')
    parsed = urlsplit(api)
    loopback = parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1') and key.startswith('pk_test_')
    if (not parsed.hostname or not (parsed.scheme == 'https' or loopback)
            or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('Configure an HTTPS API origin (development permits loopback)')
    return dict(authProvider='clerk', clerkPublishableKey=key, apiOrigin=api.rstrip('/'),
                installationId=str(UUID(value.get('installationId', ''))))
