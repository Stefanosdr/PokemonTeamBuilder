import sqlite3
import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

# Add current directory to path so we can import ui_app
sys.path.append(str(Path(__file__).parent))

import ui_app

class TestTierLogic(unittest.TestCase):
    def setUp(self):
        # Create a temporary in-memory database
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        
        # Setup schema
        self.cursor.execute("CREATE TABLE pokemon_builds (id INTEGER PRIMARY KEY, pokemon_name TEXT, tier TEXT)")
        self.cursor.execute("CREATE TABLE build_items (build_id INTEGER, item_name TEXT)")
        self.cursor.execute("CREATE TABLE build_abilities (build_id INTEGER, ability_name TEXT)")
        self.cursor.execute("CREATE TABLE build_natures (build_id INTEGER, nature_name TEXT)")
        self.cursor.execute("CREATE TABLE build_evs (id INTEGER PRIMARY KEY, build_id INTEGER, ev_string TEXT)")
        self.cursor.execute("CREATE TABLE build_tera_types (build_id INTEGER, tera_type TEXT)")
        self.cursor.execute("CREATE TABLE build_moves (build_id INTEGER, move_slot TEXT, move_name TEXT)")
        
        # Insert test data
        # OU Pokemon
        self.cursor.execute("INSERT INTO pokemon_builds (pokemon_name, tier) VALUES ('Garchomp', 'OU')")
        # UU Pokemon
        self.cursor.execute("INSERT INTO pokemon_builds (pokemon_name, tier) VALUES ('Latias', 'UU')")
        # RU Pokemon
        self.cursor.execute("INSERT INTO pokemon_builds (pokemon_name, tier) VALUES ('Mimikyu', 'RU')")
        
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    @patch('ui_app._get_db_connection')
    def test_include_lower_tiers_true(self, mock_get_db):
        # Create a mock connection and cursor
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn
        
        # Mock fetchall to return some dummy data so the function proceeds
        mock_cursor.fetchall.side_effect = [
            [{'pokemon_name': 'Garchomp'}, {'pokemon_name': 'Latias'}], # 1. Get all pokemon names
            [{'tier': 'OU'}, {'tier': 'UU'}], # 2. Get available tiers for 'Garchomp'
            [{'tier': 'UU'}] # 3. Get available tiers for 'Latias'
        ]
        
        # We need to mock _get_random_build_from_db or let it run with mocks. 
        # Let's mock it to keep it simple and focus on the tier logic.
        with patch('ui_app._get_random_build_from_db', return_value={'moves': []}):
             ui_app._build_random_team_for_tier('OU', num_pokemon=2, include_lower_tiers=True)
             
             # Verify the query for pokemon names
             # We expect a call that selects distinct pokemon names with IN clause
             found_query = False
             for call in mock_cursor.execute.call_args_list:
                 args = call[0]
                 query = args[0]
                 if "SELECT DISTINCT pokemon_name" in query:
                     found_query = True
                     params = args[1]
                     # Verify params include OU and lower tiers
                     self.assertIn('OU', params)
                     self.assertIn('UU', params)
                     self.assertIn('RU', params)
                     break
             self.assertTrue(found_query, "Did not find the expected selection query for multiple tiers")

    @patch('ui_app._get_db_connection')
    def test_include_lower_tiers_false(self, mock_get_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_db.return_value = mock_conn
        
        mock_cursor.fetchall.side_effect = [
            [{'pokemon_name': 'Garchomp'}], 
            [{'tier': 'OU'}],
            [{'item_name': 'Leftovers'}]
        ]
        
        with patch('ui_app._get_random_build_from_db', return_value={'moves': []}):
             ui_app._build_random_team_for_tier('OU', num_pokemon=1, include_lower_tiers=False)
             
             found_query = False
             for call in mock_cursor.execute.call_args_list:
                 args = call[0]
                 query = args[0]
                 if "SELECT DISTINCT pokemon_name" in query:
                     found_query = True
                     params = args[1]
                     # Verify params include ONLY OU
                     self.assertEqual(len(params), 1)
                     self.assertIn('OU', params)
                     break
             self.assertTrue(found_query, "Did not find the expected selection query for single tier")

if __name__ == '__main__':
    unittest.main()
