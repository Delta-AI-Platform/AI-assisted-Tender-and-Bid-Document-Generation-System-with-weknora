import tempfile
import unittest
from pathlib import Path
from bid_agent.skills import mount_skills
from bid_agent.store import Store
from bid_agent.tools import ProjectTools

class SkillTests(unittest.TestCase):
    def test_changed_entry_cannot_silently_replace_pinned_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);skill=root/'skill';skill.mkdir();entry=skill/'SKILL.md';entry.write_text('version one')
            mounts,pins=mount_skills({'test':str(skill)})
            store=Store(root/'state.sqlite');run=store.create('test',{'skills':mounts,'skill_pins':pins})
            tools=ProjectTools(root,store,run,mounts)
            self.assertEqual(tools.read_skill('test','SKILL.md')['content'],'version one')
            entry.write_text('version two')
            with self.assertRaises(ValueError): tools.read_skill('test','SKILL.md')
