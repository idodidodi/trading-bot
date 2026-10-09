"""Render TradingView's Pine script with the parameters from the Markdown skill."""
import json
from platform_app import ROOT, load_rules


def render():
    rules = load_rules()
    template = (ROOT / 'tradingview_template.pine').read_text(encoding='utf-8')
    for key, value in rules.items():
        template = template.replace('__' + key.upper() + '__', str(value))
    template = template.replace('__RULES_JSON__', json.dumps(rules, separators=(',', ':')))
    if '__' in template:
        raise ValueError('Unresolved Pine placeholder')
    return template


if __name__ == '__main__':
    path = ROOT / 'data' / 'tradingview.pine'
    path.parent.mkdir(exist_ok=True)
    path.write_text(render(),encoding='utf-8')
    print(f'Generated {path}. Paste into TradingView Pine Editor.')
