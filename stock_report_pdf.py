"""Compact landscape PDF reports, grouped by shared signal candle times."""
from collections import defaultdict
from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

LABELS = {'rsi':'RSI divergence', 'pin-bars':'Pin Bars', 'bb-pin-bars':'BB Pin Bars', 'rsi-pb':'RSI + PB'}


def stamp(ms):
    return datetime.fromtimestamp(ms/1000, ZoneInfo('Asia/Jerusalem')).strftime('%d %b %Y %H:%M (UTC%z)')


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
    story = [Paragraph(f'S&amp;P 500 / {run["frame"].upper()} STOCK REPORT', label),
             Paragraph(escape(title), heading),
             Paragraph(f'Period: {escape(period["label"])} | Scheduled: {escape(run["day"])} 08:00 Israel time', small),
             Paragraph('Candle prices: USD, split-adjusted. Source: Alpaca '+escape(run['feed'].upper())+'. Times: Asia/Jerusalem.', small)]
    if run['membership'].get('fallback'):
        story.append(Paragraph('Membership refresh unavailable; using saved membership dated '+escape(run['membership']['as_of'])+'.', small))
    coverage = run.get('coverage', [])
    if any(item.get('reason') or item.get('limited_history') for item in coverage):
        story.append(Paragraph('Coverage limitations apply. See the separate coverage report; unavailable data is not a no-match result.', small))
    if run.get('example'):
        story.append(Paragraph('EXAMPLE ONLY - Illustrative stocks and prices; no verified signals.', label))
    story.append(Spacer(1, 15))

    def table(data, widths, numeric=()):
        cells = [[Paragraph(escape(str(v)), small) for v in row] for row in data]
        t = Table(cells, colWidths=widths, repeatRows=1, hAlign='LEFT')
        commands = [('BACKGROUND',(0,0),(-1,0),colors.HexColor('#DCE5EB')),
                    ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#F1F5F8'),colors.white]),
                    ('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9)]
        # Numeric paragraph alignment is set explicitly, including wrapped cells.
        for col in numeric:
            for row in cells:
                row[col].style = ParagraphStyle('number', parent=small, alignment=2)
        t.setStyle(TableStyle(commands))
        return t

    groups = defaultdict(list)
    for row in rows:
        groups[(row['start'],row['end'])].append(row)
    for (start,end), items in sorted(groups.items()):
        candle = Paragraph(f'CANDLE PERIOD - applies to the stocks below<br/>Open: {stamp(start)} &nbsp;&nbsp; Close: {stamp(end)}', label)
        data = [['Ticker','Stock name','Open (USD)','Close (USD)','Sector']]
        data += [[r['ticker'],r['name'],f'{r["open"]:,.2f}',f'{r["close"]:,.2f}',r['sector']] for r in sorted(items,key=lambda r:r['ticker'])]
        t = table(data, [60,225,90,90,212], (2,3))
        story.extend([KeepTogether([candle,t])] if len(data)<=10 else [candle,t])
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
