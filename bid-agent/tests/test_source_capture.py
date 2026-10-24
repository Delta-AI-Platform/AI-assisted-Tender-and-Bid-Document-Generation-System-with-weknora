import unittest
from bid_agent.source_capture import locate_anchor


class SourceCaptureTests(unittest.TestCase):
    def test_pdf_line_wrap_matches_without_changing_original(self):
        text='前文。\n3.对应\n金额的当日银行交易流水。\n后文'
        start,end=locate_anchor(text,'3.对应金额的当日银行交易流水。')
        self.assertEqual(text[start:end],'3.对应\n金额的当日银行交易流水。')
    def test_changed_words_numbers_and_ambiguous_anchors_are_rejected(self):
        for source,anchor in [('金额6000元','金额600元'),('不得缺项报价','可缺项报价'),('金额\n6000元 金额6000元','金额6000元'),('aaaa','aaa')]:
            with self.assertRaises(ValueError):locate_anchor(source,anchor)
