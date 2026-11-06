import unittest
import tempfile
from pathlib import Path
import json
from return_research import ranks,descriptive_research

class ReturnTests(unittest.TestCase):
    def test_correlation_ranks_average_tied_volume_values(self):
        self.assertEqual(ranks([30,10,10,20]),[4,1.5,1.5,3])

    def test_volume_groups_exclude_censored_outcomes(self):
        rows=[dict(volume=10,rvol20=.5,original=dict(status='complete',net_return_pct=-1)),
              dict(volume=20,rvol20=1,original=dict(status='complete',net_return_pct=2)),
              dict(volume=30,rvol20=2,original=dict(status='incomplete'))]
        with tempfile.TemporaryDirectory() as d:
            descriptive_research(Path(d),dict(volume_observations=rows),[])
            bins=json.loads((Path(d)/'volume_summary.json').read_text())['relative_volume_groups']
        self.assertEqual(bins['Below preceding-20 average']['net_positive_pct'],0)
        self.assertEqual(bins['At/above preceding-20 average']['n'],1)
        self.assertEqual(bins['At/above preceding-20 average']['net_positive_pct'],100)
