import unittest
from types import SimpleNamespace

from backend.base.definitions import SpecialVersion, VolumeData
from backend.base.file_extraction import extract_filename_data
from backend.implementations.matching import file_importing_filter


def _volume(title, year, special_version=SpecialVersion.NORMAL):
    return VolumeData(
        id=1, comicvine_id=1, title=title, alt_title=None, year=year,
        volume_number=1, description='', site_url='', publisher='DC',
        monitored=True, monitor_new_issues=True, root_folder=1,
        folder=f'/library/{title}', custom_folder=False,
        special_version=special_version, special_version_locked=False,
        last_cv_fetch=0
    )


def _run(first_year, count, per_year=12, start=1):
    """A publication run: `count` issues from `first_year`, monthly."""
    issues = [
        SimpleNamespace(
            calculated_issue_number=float(n),
            date=f'{first_year + (n - start) // per_year}-01-01'
        )
        for n in range(start, start + count)
    ]
    return issues, {
        i.calculated_issue_number: int(i.date[:4]) for i in issues
    }


def _imports(filename, volume, run):
    issues, number_to_year = run
    return file_importing_filter(
        extract_filename_data(filename), volume, issues, number_to_year
    )


class the_year_on_a_file_decides_which_volume_it_is(unittest.TestCase):
    """Silas's 2026-09-03 log: 997 downloaded issues sitting in the download
    folder unimportable, 325 reported in a single pass, because each matched
    two volumes and an ambiguous file is left alone.

    Not duplicate volumes. Genuinely different runs of one series, decades
    apart -- Wonder Woman (1942) and Wonder Woman (2023), Green Lantern
    (1960) and Green Lantern (2023), Detective Comics (1937) and Detective
    Comics (2017). The library was right and the matcher could not tell them
    apart.

    `extract_filename_data` defaults `volume_number` to 1 when the name does
    not say -- which is almost every file -- and almost every volume in a
    comics library is volume 1. So `matching_volume_number` was true for all
    of them, and the filter's `volume_number or year` meant the year could
    never rule anything out.

    The pairs below are the real ones from that log.
    """

    PAIRS = (
        ('Wonder Woman 033 (2026).cbz',
         ('Wonder Woman', 2023, (2023, 40)), ('Wonder Woman', 1942, (1942, 329))),
        ('Green Lantern 012 (2024).cbz',
         ('Green Lantern', 2023, (2023, 30)), ('Green Lantern', 1960, (1960, 224))),
        ('Swamp Thing 046 (1986).cbz',
         ('Swamp Thing', 1986, (1986, 100)), ('Swamp Thing', 1972, (1972, 24))),
        ('Supergirl 015 (2026).cbz',
         ('Supergirl', 2025, (2025, 20)), ('Supergirl', 1972, (1972, 10))),
        ('Moon Knight 022 (2023).cbz',
         ('Moon Knight', 2022, (2022, 30)), ('Moon Knight', 1980, (1980, 38))),
        ('The Incal 003 (2011).cbz',
         ('The Incal', 2011, (2011, 6)), ('The Incal', 2001, (2001, 6))),
        ('Vampirella 004 (2026).cbz',
         ('Vampirella', 2026, (2026, 12)), ('Vampirella', 2001, (2001, 112))),
        ('Green Arrow 006 (2016).cbz',
         ('Green Arrow', 2016, (2016, 50)), ('Green Arrow', 2011, (2011, 52))),
        ('Tales of the Unexpected 003 (2006).cbz',
         ('Tales of the Unexpected', 2006, (2006, 8)),
         ('Tales of the Unexpected', 1956, (1956, 104))),
    )

    def test_the_right_volume_still_takes_its_file(self):
        for filename, right, _ in self.PAIRS:
            title, year, run = right
            with self.subTest(filename=filename, volume=year):
                self.assertTrue(
                    _imports(filename, _volume(title, year), _run(*run))
                )

    def test_the_other_run_of_the_same_series_does_not(self):
        for filename, _, wrong in self.PAIRS:
            title, year, run = wrong
            with self.subTest(filename=filename, volume=year):
                self.assertFalse(
                    _imports(filename, _volume(title, year), _run(*run))
                )

    def test_a_renumbered_continuation_is_told_from_its_original(self):
        """Detective Comics runs continuously across two catalogue entries,
        so the issue number cannot separate them and only the year can.
        """
        name = 'Detective Comics 1090 (2026).cbz'

        self.assertTrue(_imports(
            name, _volume('Detective Comics', 2017),
            _run(2017, 200, start=934)
        ))
        self.assertFalse(_imports(
            name, _volume('Detective Comics', 1937), _run(1937, 881)
        ))


class a_year_only_refuses_what_it_can_be_sure_of(unittest.TestCase):
    """The year is evidence, not a veto. Where a volume cannot be checked
    against it, the old behaviour has to stand -- refusing a new release
    because ComicVine has not listed it yet would break the case that
    matters most.
    """

    def test_a_new_issue_the_catalogue_has_not_got_yet_is_accepted(self):
        volume = _volume('Wonder Woman', 2023)

        self.assertTrue(_imports(
            'Wonder Woman 041 (2027).cbz', volume, _run(2023, 40)
        ))

    def test_but_the_old_run_still_does_not_claim_it(self):
        self.assertFalse(_imports(
            'Wonder Woman 041 (2027).cbz', _volume('Wonder Woman', 1942),
            _run(1942, 329)
        ))

    def test_a_file_with_no_year_is_judged_as_before(self):
        "Nothing to judge on, so nothing is refused for it."
        self.assertTrue(_imports(
            'Wonder Woman 033.cbz', _volume('Wonder Woman', 2023),
            _run(2023, 40)
        ))

    def test_a_file_a_year_out_is_still_taken(self):
        "Cover dates run ahead of publication, which is what the wiggle is."
        self.assertTrue(_imports(
            'Moon Knight 002 (2023).cbz', _volume('Moon Knight', 2022),
            _run(2022, 30)
        ))

    def test_a_guess_about_the_catalogue_does_not_get_a_veto(self):
        """Witch Hammer's entry says one issue from 2018; the series ran to
        at least #3 in 2024. A classification the app inferred must not
        outrank the files it was guessing about -- and neither may a year
        read off that same thin entry.
        """
        volume = _volume('Witch Hammer', 2018, SpecialVersion.TPB)

        for name in ('Witch Hammer 02 (2023).cbz', 'Witch Hammer 03 (2024).cbz'):
            with self.subTest(name=name):
                self.assertTrue(_imports(name, volume, _run(2018, 1)))
