"""Compact landscape PDF reports, grouped by shared signal candle times."""
from collections import defaultdict
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

LABELS = {'rsi':'RSI divergence', 'pin-bars':'Pin Bars', 'bb-pin-bars':'BB Pin Bars', 'rsi-pb':'RSI + PB'}


def stamp(ms):
    return datetime.fromtimestamp(ms/1000, ZoneInfo('Asia/Jerusalem')).strftime('%d %b %Y %H:%M (UTC%z)')


def market_cap(value):
    import math
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return 'Unavailable'
    for unit, divisor in (('T', 1e12), ('B', 1e9), ('M', 1e6)):
        if value >= divisor:
            return f'${value/divisor:,.2f}{unit}'
    return f'${value:,.0f}'


def render(run, strategy=None, direction=None, rows=(), empty=(), issues=()):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    out = BytesIO()
    navy = colors.HexColor('#142D40')
    small = ParagraphStyle('small', fontName='Helvetica', fontSize=8, leading=11, textColor=navy)
    heading = ParagraphStyle('heading', parent=small, fontName='Helvetica-Bold', fontSize=23, leading=28, spaceAfter=8)
    label = ParagraphStyle('label', parent=small, fontName='Helvetica-Bold', fontSize=10, leading=14, spaceAfter=6, keepWithNext=True)
    title = f'{LABELS[strategy]} / {direction.upper()}' if strategy else 'Coverage notes' if issues else 'No matches'
    period = run['period']
    preclose = run.get('mode') == 'preclose'
    scheduled = stamp(run['scheduled_at']) if run.get('scheduled_at') else run['day']+' 08:00 Israel time'
    story = [Paragraph(f'S&amp;P 500 / {run["frame"].upper()} STOCK REPORT', label),
             Paragraph(escape(title), heading),
             Paragraph(f'Period: {escape(period["label"])} | Scheduled: {escape(scheduled)}', small),
             Paragraph('Candle prices: USD, split-adjusted. Source: Alpaca '+escape(run['feed'].upper())+'. Times: Asia/Jerusalem.', small)]
    if preclose:
        story.append(Paragraph('PROVISIONAL - Current candle is still forming; signals may change before market close. Snapshot: '+stamp(run['cutoff'])+'.', label))
    elif strategy == 'rsi':
        story.append(Paragraph('RSI uses the existing pivot-close stage on completed candles; later right-side pivot confirmation is not required.', small))
    if rows:
        caps = run.get('market_caps', {})
        retrieved = caps.get('retrieved_at')
        values = caps.get('values', {})
        dates = sorted({v['as_of'] for v in values.values() if v.get('as_of')})
        note = 'Market cap: USD; latest available company value, not historical candle value. Source: '+caps.get('source','unavailable')+'.'
        if retrieved:
            note += ' Retrieved: '+retrieved+'.'
        if dates:
            note += ' Provider date(s): '+', '.join(dates)+'.'
        story.append(Paragraph(escape(note), small))
    if run['membership'].get('fallback'):
        story.append(Paragraph('Membership refresh unavailable; using saved membership dated '+escape(run['membership']['as_of'])+'.', small))
    coverage = run.get('coverage', [])
    if any(item.get('reason') or item.get('limited_history') for item in coverage):
        story.append(Paragraph('Coverage limitations apply. See the separate coverage report; unavailable data is not a no-match result.', small))
    if run.get('example'):
        story.append(Paragraph('EXAMPLE ONLY - Illustrative stocks and prices; no verified signals.', label))
    story.append(Spacer(1, 15))

    def table(data, widths, numeric=(), candle=None):
        cells = [[Paragraph(escape(str(v)), small) for v in row] for row in data]
        header = 0
        if candle is not None:
            cells.insert(0, [candle]+['']*(len(widths)-1))
            header = 1
        t = Table(cells, colWidths=widths, repeatRows=header+1, hAlign='LEFT')
        commands = [('BACKGROUND',(0,header),(-1,header),colors.HexColor('#DCE5EB')),
                    ('ROWBACKGROUNDS',(0,header+1),(-1,-1),[colors.HexColor('#F1F5F8'),colors.white]),
                    ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9)]
        if candle is not None:
            commands.append(('SPAN',(0,0),(-1,0)))
        # Numeric paragraph alignment is set explicitly, including wrapped cells.
        for col in numeric:
            for row in cells[header:]:
                row[col].style = ParagraphStyle('number', parent=small, alignment=2)
        t.setStyle(TableStyle(commands))
        return t

    groups = defaultdict(list)
    for row in rows:
        groups[(row['start'],row['end'])].append(row)
    for (start,end), items in sorted(groups.items()):
        end_label = 'Snapshot' if preclose else 'Close'
        candle = Paragraph(f'CANDLE PERIOD - applies to the stocks below<br/>Open: {stamp(start)} &nbsp;&nbsp; {end_label}: {stamp(end)}', label)
        data = [['Ticker','Stock name','Open (USD)','Latest price (USD)' if preclose else 'Close (USD)','Sector','Market cap (USD)']]
        caps = run.get('market_caps', {}).get('values', {})
        data += [[r['ticker'],r['name'],f'{r["open"]:,.2f}',f'{r["close"]:,.2f}',r['sector'],
                  market_cap(caps.get(r['ticker'], {}).get('usd'))] for r in sorted(items,key=lambda r:r['ticker'])]
        # A keepWithNext caption before a long table can create a near-empty
        # first page. Make it a repeating spanned row for split tables instead.
        t = table(data, [55,235,85,95,197,110], (2,3,5), candle if len(data)>10 else None)
        story.extend([KeepTogether([candle,t])] if len(data)<=10 else [t])
        story.append(Spacer(1,14))
    if empty:
        story.append(Paragraph('No qualifying signals among successfully evaluated stocks for:',label))
        story.append(table([['Strategy','Direction']]+[[LABELS[s],d.title()] for s,d in empty],[440,337]))
    if issues:
        story.append(table([['Ticker','Strategy','Coverage note']]+[[i['ticker'],LABELS.get(i.get('strategy'),i.get('strategy','All')),i['reason']] for i in issues],[65,140,572]))
    def footer(canvas, doc):
        canvas.setStrokeColor(colors.HexColor('#DDE4E9'));canvas.line(32,35,810,35)
        canvas.setFillColor(navy);canvas.setFont('Helvetica',8)
        canvas.drawString(32,23,'S&P 500 | '+run['frame'].title()+' | '+title)
        canvas.drawRightString(810,23,str(doc.page))
    SimpleDocTemplate(out,pagesize=landscape(A4),leftMargin=32,rightMargin=32,topMargin=30,bottomMargin=49,
                      title='S&P 500 '+run['frame']+' - '+title).build(story,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
