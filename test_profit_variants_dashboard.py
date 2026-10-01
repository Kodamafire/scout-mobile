import tempfile
from pathlib import Path
import unittest
from profit_variants_dashboard import variants_html

class ResearchDisplayTests(unittest.TestCase):
    def test_both_periods_and_limitations_are_visible(self):
        html=variants_html()
        self.assertIn('Later-period mean',html)
        self.assertIn('No new version has earned',html)
        self.assertIn('not fresh validation',html)
        self.assertEqual(html.count('<li>'),5)
    def test_missing_report_does_not_break_dashboard(self):
        self.assertIn('unavailable',variants_html('/tmp/missing-scout-variant-report.json'))
    def test_corrupt_report_does_not_break_dashboard(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'bad.json';p.write_text('bad')
            self.assertIn('unavailable',variants_html(p))

if __name__=='__main__':unittest.main()
