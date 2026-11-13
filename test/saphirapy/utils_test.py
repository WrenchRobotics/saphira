from brom_drake.productions.all import ChemLab1, ChemLab2, CornellTech1, ShelfPlanning1
from saphirapy.utils import find_all_alternative_productions
import unittest

class TestUtils(unittest.TestCase):
    def test_find_all_alternative_productions1(self):
        # Test the alternatives for one of the productions in the RELATED_PRODUCTIONS mapping
        alternatives = find_all_alternative_productions(ChemLab1)
        self.assertIn(ShelfPlanning1, alternatives)
        self.assertEqual(len(alternatives), 1)
    
    def test_find_all_alternative_productions_dynamic(self):
        # ChemLab2 and CornellTech1 (added in brom_drake 1.0.3) are alternatives to each other
        self.assertEqual(find_all_alternative_productions(ChemLab2), [CornellTech1])
        self.assertEqual(find_all_alternative_productions(CornellTech1), [ChemLab2])

    def test_find_all_alternative_productions2(self):
        # Test that the method properly raises an error when given a production class that does not belong to any base class in the RELATED_PRODUCTIONS mapping
        with self.assertRaises(ValueError):
            find_all_alternative_productions(str)

if __name__ == "__main__":
    unittest.main()