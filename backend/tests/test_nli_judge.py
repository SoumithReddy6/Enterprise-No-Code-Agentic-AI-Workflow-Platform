"""Offline NLI safety contract; no downloads or inference required."""
import importlib.util
import unittest

class NLIContractTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('scripts.eval_nli'), 'offline NLI evaluator must exist')
        from scripts import eval_nli
        return eval_nli

    def test_question_context_and_citation_filter(self):
        pairs = self.api().prepare_pairs('What rate?', '70 cents [S1]', [{'citation':'S1','text':'Rate is 70 cents.'},{'citation':'S2','text':'unrelated'}])
        self.assertEqual(len(pairs),1)
        self.assertIn('What rate?',pairs[0]['hypothesis'])
        self.assertIn('70 cents',pairs[0]['hypothesis'])
        self.assertEqual(pairs[0]['premise'],'Rate is 70 cents.')

    def test_missing_unknown_duplicate_and_empty_inputs_rejected(self):
        api=self.api()
        for answer, passages in [('Claim',[]),('Claim [S2]',[{'citation':'S1','text':'x'}]),('Claim [S1]',[{'citation':'S1','text':'x'},{'citation':'S1','text':'y'}]),('[S1]',[{'citation':'S1','text':'x'}])]:
            with self.assertRaises(ValueError): api.prepare_pairs('Question?',answer,passages)

    def test_nan_wrong_shape_and_infinite_logits_rejected(self):
        api=self.api()
        for logits in [[0,1], [0,float('nan'),1], [0,1,float('inf')]]:
            with self.assertRaises(ValueError):api.classify_logits(logits)
        self.assertEqual(api.classify_logits([0,10,0])['verdict'],'supported')
        self.assertEqual(api.classify_logits([10,0,0])['verdict'],'contradicted')
        self.assertEqual(api.classify_logits([0,0,10])['verdict'],'insufficient_evidence')

    def test_errors_never_count_as_passes(self):
        api=self.api()
        rows=[{'expected':'supported','judgment':{'status':'error'}},{'expected':'contradicted','judgment':{'status':'judged','verdict':'supported'}}]
        result=api.summarize(rows)
        self.assertEqual(result['valid_judgments'],1)
        self.assertEqual(result['false_acceptances'],1)
        self.assertFalse(result['validity_threshold_met'])
        self.assertFalse(result['runtime_influence_allowed'])

    def test_manifest_cannot_substitute_unpinned_weights(self):
        import tempfile,json,hashlib
        from pathlib import Path
        api=self.api()
        from scripts.install_nli import MODEL,REVISION,FILES,LABELS
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in FILES:(root/name).write_text('substituted')
            (root/'manifest.json').write_text(json.dumps({'model':MODEL,'revision':REVISION,'files':FILES,'id2label':LABELS,'sha256':{name:hashlib.sha256(b'substituted').hexdigest() for name in FILES}}))
            try:
                api.NLIJudge(root)
            except Exception as exc:
                self.assertIsInstance(exc,ValueError)
                self.assertIn('pinned checksums',str(exc))
            else:self.fail('Unpinned artifacts accepted')

    def test_overlength_pair_errors_before_inference_without_truncation(self):
        from tokenizers import Tokenizer,models,pre_tokenizers
        judge=self.api().NLIJudge.__new__(self.api().NLIJudge)
        judge.tokenizer=Tokenizer(models.WordLevel({'[UNK]':0},unk_token='[UNK]'))
        judge.tokenizer.pre_tokenizer=pre_tokenizers.Whitespace()
        result=judge.judge('What happened?','A claim [S1]',[{'citation':'S1','text':'word '*513}])
        self.assertEqual(result['status'],'error')
        self.assertIsNone(result['verdict'])
        self.assertIn('exceeds 512',result['error'])
        self.assertEqual(result['pairs'],[])
