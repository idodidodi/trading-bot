"""Apply only the portfolio hook to an existing runtime routing module."""
import argparse
from pathlib import Path


def patch_routing(text, source):
    if 'from telegram_portfolio import deliver_portfolio' in text:
        raise ValueError('Portfolio hook already installed; inspect before replacing')
    marker = 'def deliver_routed(store, token, general, text, event, send, silent=False, payload=None, notification_key=None):\n'
    if text.count(marker) != 1:
        raise ValueError('Unexpected routing signature')
    begin = source.index('    from telegram_portfolio import deliver_portfolio')
    end = source.index('    # Scanning and subscriber', begin)
    hook = source[begin:end]
    text = text.replace(marker, marker + hook)
    position = text.rindex('    return outcome')
    text = text[:position] + "    if portfolio_pending:\n        raise DeliveryPending('Portfolio delivery needs retry') from portfolio_pending\n" + text[position:]
    compile(text, 'telegram_routing.py', 'exec')
    return text


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent.parent / 'telegram_routing.py'
    args.output.write_text(patch_routing(args.input.read_text(), source.read_text()))
