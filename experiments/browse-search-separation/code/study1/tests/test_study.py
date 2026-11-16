import copy
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
from study import CONFIG,simulate,select_rows,policy_scores,evaluate,ranking_metrics,formula_scores
from isp.services.domain.popularity_model import doc_features,position_weight,fit_scores,make_estimator
import legacy_features

class ModelParityTests(unittest.TestCase):
    def test_features_match_previous_production(self):
        docs=[{'id':'a','metadata':'{"brand":"Acme","price":42,"averageStarRating":4,"ratingsCount":10}',
               'is_featured':True,'indexed_at':90000},
              {'id':'b','metadata':{'brand':['Acme','B'],'price':'7'}},
              {'id':'c','metadata':'broken'}, {'id':'d','brand':' ABC '}]
        dims=[{'field':'brand'},{'field':'price_tier','derived_from':'price'},
              {'field':'disabled','enabled':False}]
        with patch('time.time',return_value=100000):
            for d in docs:self.assertEqual(doc_features(d,dims),legacy_features._doc_features(d,dims))
    def test_weights_match_previous_production(self):
        for rank in range(100):self.assertEqual(position_weight(rank),legacy_features._position_weight(rank))
    def test_estimator_configuration(self):
        from sklearn.linear_model import LogisticRegression
        self.assertEqual(make_estimator().get_params(),LogisticRegression(max_iter=1000).get_params())
    def test_offline_fit_matches_previous_recipe(self):
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.linear_model import LogisticRegression
        docs={f'p{i}':{'id':f'p{i}','is_featured':i%2==0,'metadata':{'brand':str(i%3)}} for i in range(8)}
        rows=[{'doc_id':f'p{i%8}','rank':i%10,'clicked':(i%7)<3} for i in range(80)]
        dims=[{'field':'brand'}];feat={k:legacy_features._doc_features(d,dims) for k,d in docs.items()}
        v=DictVectorizer();X=v.fit_transform([feat[r['doc_id']] for r in rows])
        model=LogisticRegression(max_iter=1000).fit(X,[int(r['clicked']) for r in rows],
                    sample_weight=[legacy_features._position_weight(r['rank']) for r in rows])
        expected=model.predict_proba(v.transform(list(feat.values())))[:,1]
        np.testing.assert_allclose(list(fit_scores(docs,rows,dims).values()),expected,rtol=0,atol=1e-12)
    def test_no_features(self):
        rows=[{'doc_id':'a','rank':0,'clicked':i%2==0} for i in range(8)]
        self.assertIsNone(fit_scores({'a':{'id':'a'}},rows,[]))
    def test_single_class(self):
        rows=[{'doc_id':'a','rank':0,'clicked':True} for i in range(8)]
        self.assertIsNone(fit_scores({'a':{'is_featured':True}},rows,[]))
    def test_missing_docs_postjoin_threshold(self):
        rows=[{'doc_id':str(i),'rank':0,'clicked':i%2==0} for i in range(8)]
        self.assertIsNone(fit_scores({'0':{'is_featured':True}},rows,[]))

class SimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg=copy.deepcopy(CONFIG);cls.cfg['train_sessions']=5;cls.cfg['evaluation_sessions']=2
        cls.world,cls.rows,cls.sessions=simulate(17,20,'shifted',cls.cfg)
    def test_determinism(self):
        _,rows,sessions=simulate(17,20,'shifted',self.cfg)
        self.assertEqual(rows,self.rows);self.assertEqual(sessions,self.sessions)
    def test_no_unseen_customers_in_training(self):
        self.assertEqual(set(r['user'] for r in self.rows),set(range(16)))
    def test_time_boundary(self):
        selected=select_rows(self.rows,2,'mixed',17)
        self.assertTrue(all(r['step']<2 for r in selected))
    def test_browse_filter(self):
        selected=select_rows(self.rows,5,'browse',17)
        self.assertTrue(selected);self.assertTrue(all(r['query']=='' for r in selected))
    def test_matched_size(self):
        b=select_rows(self.rows,5,'browse',17);m=select_rows(self.rows,5,'mixed_matched',17)
        self.assertEqual(len(b),len(m));self.assertTrue(any(r['query'] for r in m))
    def test_click_has_separate_impression(self):
        impressions={(r['session'],r['doc_id'],r['rank']) for r in self.rows if not r['clicked']}
        self.assertTrue(all((r['session'],r['doc_id'],r['rank']) in impressions for r in self.rows if r['clicked']))
        self.assertEqual(len(impressions),16*5*10)
    def test_formula_matches_existing_event_weights(self):
        result=formula_scores({'a':{}},[{'doc_id':'a','rank':0,'clicked':False},{'doc_id':'a','rank':0,'clicked':True}])
        self.assertAlmostEqual(result['a'],1.1)
    def test_oracle_ndcg(self):
        p=np.linspace(.1,.9,30);order=np.argsort(-p)
        ndcg,utility=ranking_metrics(p,order)
        self.assertAlmostEqual(ndcg,1);self.assertGreater(utility,0)
        self.assertLess(ranking_metrics(p,order[::-1])[0],ndcg)
    def test_equal_policies_equal_metrics(self):
        w,_,_=simulate(17,20,'shifted',self.cfg)
        scores={k:float(i) for i,k in enumerate(w['docs'])}
        result=evaluate(w,{'first':scores,'second':scores},'shifted',self.cfg)
        for cohort in ['seen','unseen']:
            np.testing.assert_allclose(result[cohort,'first'],result[cohort,'second'],rtol=0,atol=1e-14)
    def test_fit_all_methods_without_mutation(self):
        original=copy.deepcopy(self.rows);outputs,counts,fallback=policy_scores(self.world,self.rows,5,17)
        self.assertEqual(self.rows,original);self.assertEqual(set(outputs),set(CONFIG['methods']))
        self.assertEqual(counts['browse'],counts['mixed_matched']);self.assertFalse(any(fallback.values()))
    def test_scenario_drift_does_not_change_training(self):
        _,rows,_=simulate(17,20,'preference_drift',self.cfg)
        self.assertEqual(rows,self.rows)

if __name__=='__main__':unittest.main()
