import unittest,json
from bid_agent.skill_tools import compact_inspection
class CompactInspectionTest(unittest.TestCase):
 def test_structure_preserved_without_raw_xml_or_body(self):
  xml='<w:sectPr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:left="1531"/></w:sectPr>'
  data={'sha256':'bound','body_text':'x'*30000,'sections':[xml]*40,'tables':[{'index':0,'rows':132,'columns':12}],'media':{'image':'hash'},'image_occurrences':['hash']}
  out=compact_inspection(data)
  self.assertEqual(out['tables'],data['tables']);self.assertEqual(out['media'],data['media'])
  self.assertEqual(out['section_count'],40);self.assertEqual(out['sections'][0]['pgSz']['w'],'11906')
  self.assertEqual(out['image_occurrence_count'],1);self.assertEqual(out['body_text_characters'],30000)
  self.assertNotIn('body_text',out);self.assertNotIn('<w:',json.dumps(out))
  self.assertEqual(data['body_text'],'x'*30000)
