"""Telegram presentation only: retain all words, punctuation and original lines."""


def utf16_length(text):
    # Telegram entity offsets count UTF-16 code units, including emoji pairs.
    return len(text.encode('utf-16-le')) // 2


def presentation(text):
    """Return spaced text and native entities, without parsing user content.

    Quote timestamps stay attached to prices. Footnotes form a quiet final
    section. Formatting happens after delivery claims, preserving queue/event IDs.
    """
    lines = text.split('\n')
    spaced = []
    previous_note = False
    for line in lines:
        note = line.startswith(('Source:', 'All quote times ', 'Gold/oil use ',
                                'Signal only ', 'One alert per '))
        if (line and spaced and spaced[-1] and not line[:1].isspace()
                and not (note and previous_note)):
            spaced.append('')
        spaced.append(line)
        previous_note = note
    display = '\n'.join(spaced)
    # Never make an otherwise deliverable message too long just for spacing.
    if utf16_length(display) > 4096:
        display = text

    entities = []
    offset = 0
    first = True
    for line in display.split('\n'):
        content = line.lstrip()
        kind = None
        if content:
            if first:
                kind = 'bold'
                first = False
            elif content.startswith(('As of ', 'Source:', 'Confirmed:',
                                     'Second pivot closed:', 'All quote times ',
                                     'Gold/oil use ', 'Signal only ', 'One alert per ')):
                kind = 'italic'
            elif not content.startswith(('Reference candle ', 'Signal candle ')) and (':' in content or ' · ' in content):
                kind = 'bold'
        if kind and len(entities) < 100:
            entities.append(dict(type=kind,
                                 offset=offset + utf16_length(line[:len(line) - len(content)]),
                                 length=utf16_length(content)))
        offset += utf16_length(line) + 1
    return display, entities
