"""Allocate one build version and verify both public packages before publication.

Versions are written only inside Actions checkouts, never pushed back to main.
This avoids recursive release commits and lets existing numeric-version updaters
install new builds without a bootstrap updater change.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def parts(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise ValueError('Expected a stable vX.Y.Z version')
    return tuple(map(int, value.lstrip('v').split('.')))


def allocate(base, tags):
    current = parts(base)
    published = []
    for tag in tags:
        try:
            published.append(parts(tag))
        except ValueError:
            pass
    latest = max(published, default=None)
    if latest is None or current > latest:
        return '.'.join(map(str, current))
    major, minor, patch = latest
    return f'{major}.{minor}.{patch+1}'


def base_version(root):
    tree = ast.parse((root/'app_version.py').read_text(encoding='utf-8'))
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'VERSION' for t in n.targets))


def prepare(root, env):
    base = base_version(root)
    publish = env['GITHUB_REF'] == 'refs/heads/main' or env['GITHUB_REF'].startswith('refs/tags/v')
    if env['GITHUB_REF'].startswith('refs/tags/'):
        version = env['GITHUB_REF'].removeprefix('refs/tags/').lstrip('v')
        if parts(version) < parts(base):
            raise ValueError('Release tag is older than the source version')
    elif publish:
        tags = subprocess.check_output(['git','tag','--list','v*'],cwd=root,text=True).splitlines()
        version = allocate(base, tags)
    else:
        version = base
    with Path(env['GITHUB_OUTPUT']).open('a', encoding='utf-8') as output:
        output.write(f'version={version}\npublish={str(publish).lower()}\n')
    return version


def apply(root, version):
    parts(version)
    if parts(version) < parts(base_version(root)):
        raise ValueError('Build version cannot go backwards')
    (root/'app_version.py').write_text(f"VERSION = '{version.lstrip('v')}'\n", encoding='utf-8')
    # This label is bundled with each executable, so it must match that build.
    guide = root/'web/update-notes.html'
    if guide.exists():
        text = guide.read_text(encoding='utf-8').replace('Pending executable release', 'Executable version '+version.lstrip('v'))
        guide.write_text(text, encoding='utf-8')
    changelog = root/'CHANGELOG.md'
    if changelog.exists():
        changelog.write_text(changelog.read_text(encoding='utf-8').replace('## Unreleased', '## '+version.lstrip('v'), 1), encoding='utf-8')


def verify(root, version):
    parts(version)
    for manifest, binary, platform in [('windows-update.json','FamilyTradingBot.exe',None),
                                       ('linux-x86_64-update.json','FamilyTradingBot-linux-x86_64','linux-x86_64')]:
        metadata = json.loads((root/'dist'/manifest).read_text(encoding='utf-8'))
        raw = (root/'dist'/binary).read_bytes()
        if metadata['version'] != version or metadata['size'] != len(raw) or metadata['sha256'] != hashlib.sha256(raw).hexdigest():
            raise ValueError('Release packages must share the allocated version and valid checksums')
        if platform and metadata.get('platform') != platform:
            raise ValueError('Incorrect release platform')
        if not raw.startswith(b'MZ' if platform is None else b'\x7fELF'):
            raise ValueError('Incorrect executable format')


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','apply','verify'])
    action=parser.parse_args().action
    if action=='prepare':
        print('Allocated build version:',prepare(ROOT,os.environ))
    elif action=='apply':
        apply(ROOT,os.environ['RELEASE_VERSION'])
    else:
        verify(ROOT,os.environ['RELEASE_VERSION'])
