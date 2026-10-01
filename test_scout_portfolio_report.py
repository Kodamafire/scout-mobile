import unittest
from scout_portfolio_report import portfolio_html

class PortfolioReportTests(unittest.TestCase):
    def test_report_separates_reused_result_and_fresh_recording(self):
        html=portfolio_html({'status':'RECORDED'})
        self.assertIn('Active rules are unchanged',html)
        self.assertIn('RECORDED',html)
        self.assertIn('tied the later period',html)
        self.assertIn('not fresh validation',html)
        self.assertEqual(html.count('<li>'),5)
    def test_missing_report_does_not_break_dashboard(self):
        self.assertIn('unavailable',portfolio_html(path='/tmp/not-a-scout-report.json'))

if __name__=='__main__':unittest.main()
