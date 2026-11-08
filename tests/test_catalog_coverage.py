"""Offline title-identification regression set. Test-only fixture facts, not app seed data."""
import unittest

from app.catalog import normalize_title, resolve_catalog_title
from app.models import CatalogMovie


FILMS = [
    ("RRR", 2022, "te", ["Roudram Ranam Rudhiram"]),
    ("Baahubali: The Beginning", 2015, "te", ["Bahubali: The Beginning"]),
    ("Baahubali 2: The Conclusion", 2017, "te", ["Baahubali 2"]),
    ("Mahanati", 2018, "te", []), ("Eega", 2012, "te", ["Makkhi"]),
    ("Magadheera", 2009, "te", []), ("Arjun Reddy", 2017, "te", []),
    ("Jersey", 2019, "te", []), ("Rangasthalam", 2018, "te", []),
    ("Pushpa: The Rise", 2021, "te", ["Pushpa"]), ("Srimanthudu", 2015, "te", []),
    ("Athadu", 2005, "te", []), ("Nuvvostanante Nenoddantana", 2005, "te", []),
    ("Sita Ramam", 2022, "te", []), ("Pokiri", 2006, "te", []),
    ("3 Idiots", 2009, "hi", ["Three Idiots"]), ("Lagaan", 2001, "hi", []),
    ("Queen", 2014, "hi", []), ("Pink", 2016, "hi", []), ("Dangal", 2016, "hi", []),
    ("Gully Boy", 2019, "hi", []), ("Kahaani", 2012, "hi", ["Kahani"]),
    ("Piku", 2015, "hi", []), ("Swades", 2004, "hi", []), ("Sholay", 1975, "hi", []),
    ("Mughal-e-Azam", 1960, "hi", ["Mughal E Azam"]), ("Andhadhun", 2018, "hi", []),
    ("Drishyam 2", 2022, "hi", []), ("Article 15", 2019, "hi", []), ("Stree", 2018, "hi", []),
    ("The Matrix", 1999, "en", ["Matrix"]), ("The Godfather", 1972, "en", []),
    ("The Dark Knight", 2008, "en", []), ("Inception", 2010, "en", []),
    ("Arrival", 2016, "en", []), ("Jaws", 1975, "en", []), ("Alien", 1979, "en", []),
    ("Blade Runner", 1982, "en", []), ("Dune", 2021, "en", []), ("Dune", 1984, "en", []),
    ("Titanic", 1997, "en", []), ("Avatar", 2009, "en", []), ("Get Out", 2017, "en", []),
    ("Casablanca", 1942, "en", []), ("The Lion King", 1994, "en", []),
]


class LocalTitleCoverageTests(unittest.TestCase):
    def setUp(self):
        self.records = [CatalogMovie(id=i + 1, source="fixture", external_id=str(i + 1), title=title,
                                     year=year, language=language, aliases_json=aliases)
                        for i, (title, year, language, aliases) in enumerate(FILMS)]

    def test_45_titles_identify_exact_year_and_language_offline(self):
        self.assertEqual(len(FILMS), 45)
        correct_title = correct_year = correct_language = 0
        for title, year, language, aliases in FILMS:
            query = aliases[0] if aliases else title
            status, result = resolve_catalog_title(self.records, query, year)
            self.assertEqual(status, "matched", (title, year, query))
            correct_title += normalize_title(result.title) == normalize_title(title)
            correct_year += result.year == year
            correct_language += result.language == language
        self.assertEqual((correct_title, correct_year, correct_language), (45, 45, 45))

    def test_similar_titles_need_a_year_to_avoid_ambiguous_import(self):
        status, result = resolve_catalog_title(self.records, "Dune")
        self.assertEqual((status, result), ("ambiguous", None))
        status, result = resolve_catalog_title(self.records, "Dune", 1984)
        self.assertEqual((status, result.year), ("matched", 1984))

    def test_field_completeness_is_reported_as_missing_not_fabricated(self):
        # The resolver fixtures contain identity facts only. No fabricated plot, cast, art or rating.
        self.assertEqual(sum(bool(movie.overview) for movie in self.records), 0)
        self.assertEqual(sum(bool(movie.cast_json) for movie in self.records), 0)
        self.assertEqual(sum(bool(movie.poster_url) for movie in self.records), 0)
        self.assertEqual(sum(bool(movie.ratings_json) for movie in self.records), 0)


if __name__ == "__main__":
    unittest.main()
