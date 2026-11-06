import statistics
import json

def ranks(values):
    """Average ranks for tied values, for descriptive Spearman correlations."""
    order=sorted(range(len(values)),key=lambda i:values[i]);result=[0.]*len(values);i=0
    while i<len(order):
        j=i+1
        while j<len(order) and values[order[j]]==values[order[i]]:j+=1
        for k in order[i:j]:result[k]=(i+j+1)/2
        i=j
    return result


def descriptive_research(out,report,details):
    config=report.get('config',{})
    lines=['# Exploratory return and volume comparison','',
           f"These are separate hypothetical entries, not a portfolio or realized trades. Success below means positive net return at the fixed original {config.get('horizon_4h_bars',3)*4}-hour horizon, with {config.get('roundtrip_cost_bps',10)} bps total assumed costs. It is different from touching a favorable price or avoiding an adverse threshold.",'',
           '| Policy | Complete entries | No entry | Incomplete | Net-positive entries | Mean net return per entry | Mean return per original, unentered cash = 0 |',
           '|---|---:|---:|---:|---:|---:|---:|']
    fmt=lambda v:'unavailable' if v is None else f'{v:.3f}%'
    for policy in ('original','first_chase'):
        v=report.get('summary',{}).get(policy)
        if not v:continue
        lines.append(f"| {policy} | {v['complete']} | {v['no_entry']} | {v['incomplete']} | {fmt(v['net_win_rate']*100 if v['net_win_rate'] is not None else None)} | {fmt(v['mean_net_return_pct'])} | {fmt(v['per_original_root_mean_return_pct_with_unentered_cash_zero'])} |")
    paired=report.get('paired',{})
    lines.extend(['',f"Matched original/chase pairs: {paired.get('count',0)}. Mean original net return: {fmt(paired.get('mean_original_net_return_pct'))}; mean chase net return: {fmt(paired.get('mean_chase_net_return_pct'))}; mean reduction in adverse excursion: {fmt(paired.get('mean_mae_reduction_pct'))} (percentage points).",'',
        'A higher conditional chase mean can reflect selecting a smaller set of roots. Use the paired comparison and the all-root denominator together. Censored observations remain incomplete.','',
        '| Original candle feature vs subsequent original-entry net return | Complete pairs | Spearman rank correlation |',
        '|---|---:|---:|'])
    correlations={}
    for feature in ('volume','rvol20'):
        pairs=[(v[feature],v['original']['net_return_pct']) for v in report.get('volume_observations',[])
               if v.get(feature) is not None and v['original'].get('status')=='complete']
        x=ranks([p[0] for p in pairs]);y=ranks([p[1] for p in pairs])
        rho=statistics.correlation(x,y) if len(pairs)>2 and len(set(x))>1 and len(set(y))>1 else None
        correlations[feature]=dict(n=len(pairs),spearman=rho)
        lines.append(f"| {feature} | {len(pairs)} | {rho:.3f} |" if rho is not None else f'| {feature} | {len(pairs)} | unavailable |')
    lines.extend(['','Raw closing-candle volume and relative volume (versus its preceding 20 traded candles) are recorded separately. Correlation describes this sample and does not establish that a volume filter improves success. Signals may overlap and share a price path, so ordinary independent-observation confidence claims would be misleading.','',
        'No strategy parameters were optimized on these outcomes. Any proposed volume, stop, timing or timeframe change needs a frozen rule and unseen-period/other-asset evaluation before claiming an improvement. Maximum favorable excursion assumes hindsight and is not a realizable exit rule.'])
    bins={}
    lines.extend(['','| Original relative-volume group | Complete entries | Net-positive share | Mean net return |',
                  '|---|---:|---:|---:|'])
    for label,predicate in [('Below preceding-20 average',lambda x:x<1),('At/above preceding-20 average',lambda x:x>=1)]:
        values=[v['original']['net_return_pct'] for v in report.get('volume_observations',[])
                if v.get('rvol20') is not None and predicate(v['rvol20']) and v['original'].get('status')=='complete']
        win=sum(v>0 for v in values)/len(values)*100 if values else None
        mean=statistics.mean(values) if values else None
        bins[label]=dict(n=len(values),net_positive_pct=win,mean_net_return_pct=mean)
        lines.append(f'| {label} | {len(values)} | {fmt(win)} | {fmt(mean)} |')
    lines.extend(['','The relative-volume split uses 1.0 (the preceding average), not a threshold fitted to maximize these outcomes. These descriptive subgroup percentages are not forecasts or a validated success-rate improvement.'])
    correlations['relative_volume_groups']=bins
    (out/'research.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    (out/'volume_summary.json').write_text(json.dumps(correlations,indent=2)+'\n',encoding='utf-8')
