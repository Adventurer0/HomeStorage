"""Require a reviewed, versioned handover describing models and implementation."""

import argparse
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
REQUIRED = ('采用的模型', '技术与方案', '验证结果', '升级与回档', '限制与待确认')


def check(version=None):
    source = (ROOT / 'app/version.py').read_text(encoding='utf-8')
    match = re.search(r'^VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"$', source, re.MULTILINE)
    if not match:
        raise ValueError('Source version is missing')
    version = version or match[1]
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version) or version != match[1]:
        raise ValueError('Handover version must match source version')
    current = (ROOT / '交接文档.md').read_text(encoding='utf-8')
    archived = (ROOT / 'docs/handover' / (version + '.md')).read_text(encoding='utf-8')
    if current != archived:
        raise ValueError('Current and versioned handover must match')
    if not current.startswith('# HomeStorage ' + version + ' 交接文档\n'):
        raise ValueError('Handover must identify this release version')
    for heading in REQUIRED:
        if not re.search(r'^## .*' + re.escape(heading), current, re.MULTILINE):
            raise ValueError('Handover section missing: ' + heading)
    if '<待填写>' in current or 'TODO: handover' in current:
        raise ValueError('Handover has unfilled template markers')
    return version


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--version')
    args = parser.parse_args()
    try:
        print('Validated versioned handover ' + check(args.version))
    except (OSError, ValueError) as error:
        raise SystemExit(str(error)) from None
