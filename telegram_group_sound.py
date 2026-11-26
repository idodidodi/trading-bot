"""Sound preferences for dedicated groups; never changes signal eligibility."""
def silent_for_group(store, group, timeframe):
    with store.connect() as db:
        row = db.execute('SELECT p.silent FROM telegram_group_sound p '
                         'JOIN runtime_control c ON c.installation=p.installation WHERE c.id=1').fetchone()
    return bool(row and row[0].get(group, {}).get(timeframe, False))


def silent_for_event(store, event):
    # Lower/ready and lower invalidations use the lower candle; parent-only
    # notifications use the daily/weekly parent candle. Never parse display text.
    return silent_for_group(store, event['kind'], event.get('lower') or event.get('higher'))
