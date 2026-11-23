import copy
import json
import unittest
from pathlib import Path
from verifier import verify, time, amount

DATA=Path(__file__).resolve().parents[2]/'docs/research-papers/telco-ecra-operational/data'
class ContractTests(unittest.TestCase):
    def setUp(self):
        # Only the first development case is used as a fixture; expected changes are literal assertions.
        d=json.loads((DATA/'benchmark.json').read_text());self.c=d['cases'][0]
        self.rows=[r for r in d['records'] if r['case_id']==self.c['case_id']]
        self.ctx=next(r for r in self.rows if r['kind']=='account')
        self.offer=next(r for r in self.rows if r['kind']=='promotion')
        self.bill=[r for r in self.rows if r['kind']=='billing']
    def answer(self,**kw):return verify(self.c,self.rows,**kw)['answer']
    def test_true_eligibility(self):self.assertIs(self.answer()['promotion_eligible'],True)
    def test_mandatory_exclusion(self):self.ctx['segment']='employee';self.assertIs(self.answer()['promotion_eligible'],False)
    def test_wrong_sku(self):self.ctx['sku']='not-listed';self.assertIs(self.answer()['promotion_eligible'],False)
    def test_wrong_plan(self):self.ctx['plan_id']='not-listed';self.assertIs(self.answer()['promotion_eligible'],False)
    def test_start_inclusive(self):self.ctx['activation_time']=self.offer['valid_from'];self.assertIs(self.answer()['promotion_eligible'],True)
    def test_end_exclusive(self):self.ctx['activation_time']=self.offer['valid_to'];self.assertIs(self.answer()['promotion_eligible'],False)
    def test_activation_missing(self):self.ctx.pop('activation_time');self.assertIsNone(self.answer()['promotion_eligible'])
    def test_future_version_ignored(self):
        newer={**self.offer,'id':'future','version':2,'recorded_at':'2026-07-01T00:00:00+00:00','eligible_skus':[]};self.rows.append(newer)
        self.assertIs(self.answer()['promotion_eligible'],True)
    def test_visible_version_used(self):
        self.rows.append({**self.offer,'id':'new','version':2,'eligible_skus':[]});self.assertIs(self.answer()['promotion_eligible'],False)
    def test_conflict_unknown(self):self.rows.append({**self.offer,'id':'conflict','eligible_skus':[]});self.assertIsNone(self.answer()['promotion_eligible'])
    def test_decimal_billing(self):
        self.bill[0]['amount']='0.1';self.bill[1]['amount']='0.2';self.assertEqual(self.answer()['net_billed'],'0.3')
    def test_late_posting(self):
        self.bill[0]['amount']='10';self.bill[1]['amount']='-3';self.bill[1]['posted_at']='2026-07-01T00:00:00+00:00';self.assertEqual(self.answer()['net_billed'],'10')
    def test_future_event(self):
        self.bill[0]['amount']='10';self.bill[1]['amount']='-3';self.bill[1]['event_time']='2026-07-01T00:00:00+00:00';self.assertEqual(self.answer()['net_billed'],'10')
    def test_currency_missing(self):self.bill[0].pop('currency');self.assertIsNone(self.answer()['net_billed'])
    def test_currency_mixed(self):self.bill[0]['currency']='EUR';self.assertIsNone(self.answer()['net_billed'])
    def test_duplicate_deduped(self):
        old=self.answer()['net_billed'];self.rows.append({**self.bill[0],'id':'dup'});self.assertEqual(self.answer()['net_billed'],old)
    def test_conflicting_duplicate(self):self.rows.append({**self.bill[0],'id':'conflict','amount':'9999'});self.assertIsNone(self.answer()['net_billed'])
    def test_missing_completeness(self):self.ctx.pop('ledger_complete_through');self.assertIsNone(self.answer()['net_billed'])
    def test_support_not_authoritative(self):
        old=self.answer()['net_billed'];next(r for r in self.rows if r['kind']=='support')['asserted_net']='9999'
        self.assertEqual(self.answer()['net_billed'],old);self.assertIs(self.answer()['support_correct'],False)
    def test_cross_account_isolation(self):self.rows.append({**self.bill[0],'id':'other','account_id':'OTHER','amount':'9999'});self.assertIsNotNone(self.answer()['net_billed'])
    def test_plan_unlisted(self):self.ctx['lines']=999;self.assertIsNone(self.answer()['plan_price'])
    def test_no_mutation(self):before=copy.deepcopy(self.rows);self.answer();self.assertEqual(before,self.rows)
    def test_nonfinite(self):self.assertIsNone(amount('NaN'))
    def test_timezone_required(self):self.assertIsNone(time('2026-06-01T00:00:00'))
    def test_development_oracle_agreement(self):
        d=json.loads((DATA/'benchmark.json').read_text());labels={r['case_id']:r for r in json.loads((DATA/'oracle.json').read_text()) if r['split']=='development'}
        for c in d['cases']:
            if c['split']=='development':self.assertEqual(verify(c,d['records'])['answer'],labels[c['case_id']]['expected'],c['case_id'])

if __name__=='__main__':unittest.main()
