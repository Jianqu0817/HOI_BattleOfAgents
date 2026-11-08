"""NPC shared-war projection does not expose foreign state to selected defenders."""
import json
from pathlib import Path
import tempfile
import unittest

from diplomacy_actions import diplomacy_observation, public_war_graph
from parse_save import load_save


class PublicWarTests(unittest.TestCase):
    def load(self, body, wanted=("FRA",)):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.hoi4"
            path.write_text("HOI4txt\ndate=1936.1.1.12\ncountries={" + body + "}\nfaction={}\nstates={}\n", encoding="utf-8")
            return load_save(path, set(wanted))

    def test_unselected_attacker_projects_only_public_war_fields(self):
        state = self.load('''GER={units={secret_enemy_division=901} manpower=999
          diplomacy={wargoals={private_goal=hidden} active_relations={
            FRA={private_note=secret war_relation={first=GER second=FRA start_date=1936.1.1.12
              first_casualties=4 second_casualties=9 private_history=hidden}}
            ITA={war_relation={first=GER second=ITA start_date=1935.1.1.12}}
          }}} FRA={capital=1 diplomacy={active_relations={GER={attitude=hostile}}}}''')
        self.assertEqual(set(state["countries"]), {"FRA"})
        self.assertEqual(state["public_wars"], [{"first": "GER", "second": "FRA", "start_date": "1936.1.1.12",
                                                "first_casualties": 4, "second_casualties": 9}])
        self.assertEqual(diplomacy_observation(state, "FRA")["wars"], [{"enemy": "GER", "since": "1936.1.1.12"}])
        serialized = json.dumps(state)
        for private in ("secret_enemy_division", "private_goal", "private_note", "private_history", "hidden", "999"):
            self.assertNotIn(private, serialized)

    def test_selected_and_unselected_records_deduplicate_and_missing_fields_are_none(self):
        state = self.load('''GER={diplomacy={active_relations={FRA={war_relation={first=GER second=FRA}}}}}
          FRA={diplomacy={active_relations={GER={war_relation={first=GER second=FRA}}}}}''')
        self.assertEqual(len(state["public_wars"]), 1)
        self.assertIsNone(state["public_wars"][0]["start_date"])
        self.assertIsNone(public_war_graph(state)[("FRA", "GER")]["first_casualties"])

    def test_conflicting_copies_or_wrong_endpoints_are_rejected(self):
        with self.assertRaises(ValueError):
            self.load('''GER={diplomacy={active_relations={FRA={war_relation={first=GER second=FRA start_date=1936.1.1.12}}}}}
              FRA={diplomacy={active_relations={GER={war_relation={first=GER second=FRA start_date=1936.2.1.12}}}}}''')
        with self.assertRaises(ValueError):
            self.load('''GER={diplomacy={active_relations={FRA={war_relation={first=GER second=ITA}}}}} FRA={}''')


if __name__ == "__main__":
    unittest.main()
