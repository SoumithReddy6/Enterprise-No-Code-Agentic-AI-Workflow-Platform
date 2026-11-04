"""Span/null decision semantics independent of model downloads."""
import importlib.util
import math
import unittest

class ReaderTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('backend.app.kb.answerability'),'Reader must exist')
        from backend.app.kb import answerability
        return answerability

    def test_context_only_ordered_spans_and_max_length(self):
        # Question position1 must never win; reverse end2/start3 is forbidden.
        result=self.api().best_span([0,100,2,8,0],[0,100,9,1,4],[None,0,1,1,1],[(0,0),(0,8),(0,1),(2,3),(4,5)],'a b c',max_span_tokens=2)
        self.assertEqual(result['text'],'b c')
        self.assertEqual(result['margin'],12)
        self.assertEqual((result['start_token'],result['end_token']),(3,4))

    def test_null_wins_when_context_logits_are_lower(self):
        result=self.api().best_span([5,2],[5,3],[None,1],[(0,0),(0,3)],'cat')
        self.assertEqual(result['margin'],-5)
        self.assertFalse(result['answerable'])
        self.assertEqual(result['text'],'cat')

    def test_invalid_logits_offsets_and_missing_context_rejected(self):
        api=self.api()
        for starts,ends,seq,offsets in [([math.nan,1],[0,1],[None,1],[(0,0),(0,3)]),([0],[0,1],[None,1],[(0,0),(0,3)]),([0,1],[0,1],[None,1],[(0,0),(0,99)]),([0],[0],[None],[(0,0)])]:
            with self.assertRaises(ValueError):api.best_span(starts,ends,seq,offsets,'cat')

    def test_empty_evidence_abstains_and_invalid_question_errors(self):
        reader=self.api().AnswerabilityReader.__new__(self.api().AnswerabilityReader)
        result=reader.score('What?',[])
        self.assertEqual(result['status'],'scored')
        self.assertFalse(result['answerable'])
        self.assertIsNone(result['margin'])
        self.assertEqual(reader.score('',[])['status'],'error')

    def test_windows_cover_tail_and_overlong_question_is_not_silently_cut(self):
        from tokenizers import Tokenizer,models,pre_tokenizers,processors
        reader=self.api().AnswerabilityReader.__new__(self.api().AnswerabilityReader)
        reader.tokenizer=Tokenizer(models.WordLevel({'[UNK]':0,'[CLS]':1,'[SEP]':2},unk_token='[UNK]'))
        reader.tokenizer.pre_tokenizer=pre_tokenizers.Whitespace()
        reader.tokenizer.post_processor=processors.TemplateProcessing(single='[CLS] $A [SEP]',pair='[CLS] $A [SEP] $B:1 [SEP]:1',special_tokens=[('[CLS]',1),('[SEP]',2)])
        context='word '*1000+'tail'
        windows=reader.windows('What?',context)
        self.assertGreater(len(windows),1)
        offsets=[offset for window in windows for seq,offset in zip(window.sequence_ids,window.offsets) if seq==1]
        self.assertEqual(max(end for start,end in offsets),len(context))
        self.assertTrue(all(len(window.ids)<=384 for window in windows))
        with self.assertRaisesRegex(ValueError,'64 tokens'):reader.windows('question '*65,'context')
