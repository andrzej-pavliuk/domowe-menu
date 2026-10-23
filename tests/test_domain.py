import datetime
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import calculate, calorie_balance, duration_label, recipe_jsonld, validate_public_url


class NutritionTests(unittest.TestCase):
    def test_2512_is_accepted_for_2500_with_five_percent_tolerance(self):
        profile = {'id': 1, 'calories': 2500, 'tolerance_type': 'percent', 'tolerance_value': 5}
        result = calorie_balance(profile, [{'profile_id': 1, 'calories': 2512, 'status': 'eaten'}])
        self.assertEqual((result['range_low'], result['range_high']), (2375, 2625))
        self.assertEqual(result['deviation'], 12)
        self.assertTrue(result['accepted'])

    def test_skipped_food_does_not_count_and_fixed_tolerance_works(self):
        profile = {'id': 2, 'calories': 2500, 'tolerance_type': 'fixed', 'tolerance_value': 50}
        entries = [{'profile_id': 2, 'calories': 2530, 'status': 'planned'}, {'profile_id': 2, 'calories': 300, 'status': 'skipped'}]
        result = calorie_balance(profile, entries)
        self.assertEqual((result['planned'], result['range_low'], result['range_high']), (2530, 2450, 2550))
        self.assertTrue(result['accepted'])

    def test_mifflin_and_tdee_for_adult(self):
        result = calculate({'birth_date': '1990-01-01', 'sex': 'male', 'height': 180, 'weight': 80, 'activity': 'light', 'goal': 'maintain'})
        self.assertEqual(result['bmi'], 24.7)
        self.assertEqual(result['bmr'], 1750)
        self.assertEqual(result['tdee'], 2406)

    def test_estimate_is_disabled_for_minor_and_special_cases(self):
        minor = calculate({'birth_date': (datetime.date.today()-datetime.timedelta(days=17*365)).isoformat(), 'sex':'female','height':160,'weight':55})
        pregnancy = calculate({'birth_date':'1990-01-01','sex':'female','height':160,'weight':55,'pregnant':1})
        self.assertIsNone(minor['bmr']); self.assertIsNone(minor['suggested'])
        self.assertIsNone(pregnancy['bmr']); self.assertIsNone(pregnancy['suggested'])

    def test_very_old_age_does_not_receive_automatic_calorie_estimate(self):
        result = calculate({'age_override': 81, 'sex': 'male', 'height': 175, 'weight': 80})
        self.assertIsNone(result['bmr'])
        self.assertIsNone(result['suggested'])
        self.assertIn('80 lat', result['warning'])

    def test_recipe_jsonld_and_duration_parsing(self):
        page = '''<script type="application/ld+json">{"@context":"https://schema.org","@type":"Recipe","name":"Zupa","recipeIngredient":["1 marchewka"],"recipeInstructions":[{"@type":"HowToStep","text":"Pokrój warzywa"}]}</script>'''
        recipe = recipe_jsonld(page)
        self.assertEqual(recipe['name'], 'Zupa')
        self.assertEqual(duration_label('PT1H25M'), '1 godz. 25 min')
        self.assertEqual(duration_label('PT20M'), '20 min')

    def test_url_import_rejects_local_destinations(self):
        with self.assertRaises(ValueError):
            validate_public_url('http://127.0.0.1:8080/admin')


if __name__ == '__main__':
    unittest.main()
