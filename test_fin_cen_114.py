"""Tests for fin-cen-114.py: filter_subaccounts, build_reportable, iter_year, find_daily_max."""
import collections
import datetime
import functools
import importlib.util
import os
from decimal import Decimal

import beancount.core.account_types
import beancount.core.data as data
import beancount.core.amount
import beancount.core.getters
import beancount.core.inventory
import beancount.core.prices
import beancount.core.realization
import beancount.loader
import beancount.parser.options

# Load module under test (filename contains hyphens, so normal import fails)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("fincen", "fin-cen-114.py")
fincen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fincen)

filter_subaccounts = fincen.filter_subaccounts
build_reportable = fincen.build_reportable

ACCT = "Assets:CA:Test:Checking"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_open(account, date=datetime.date(2015, 1, 1), meta=None):
    return data.Open({"filename": "", "lineno": 0}, date, account, None, meta or {})


def make_close(account, date):
    return data.Close({"filename": "", "lineno": 0}, date, account)


def make_txn_posting(account, number, currency, date):
    units = beancount.core.amount.Amount(Decimal(str(number)), currency)
    posting = data.Posting(account, units, None, None, None, {})
    meta = {"filename": "", "lineno": 0}
    txn = data.Transaction(meta, date, "*", None, "desc", set(), set(), [posting])
    return data.TxnPosting(txn, posting)


def make_price_map(currency, usd_rate, date):
    bean_str = (
        'option "operating_currency" "USD"\n'
        f'{date} price {currency} {usd_rate} USD\n'
    )
    entries, _, _ = beancount.loader.load_string(bean_str)
    return beancount.core.prices.build_price_map(entries)


def empty_price_map():
    return beancount.core.prices.build_price_map([])


# ---------------------------------------------------------------------------
# filter_subaccounts() tests
# ---------------------------------------------------------------------------

class TestFilterSubaccounts:

    def test_filter_subaccounts_basic(self):
        """Direct children of the registered parent are grouped; parent removed from standalone."""
        parent = "Assets:CA:Tangerine:Saving"
        child1 = "Assets:CA:Tangerine:Saving:Main"
        child2 = "Assets:CA:Tangerine:Saving:Travel"

        accounts_sorted = [
            (parent, (make_open(parent), None)),
            (child1, (make_open(child1), None)),
            (child2, (make_open(child2), None)),
        ]

        subaccounts_sorted, standalone = filter_subaccounts([parent], accounts_sorted)

        assert parent in subaccounts_sorted
        grouped_names = [a for a, _ in subaccounts_sorted[parent]]
        assert child1 in grouped_names
        assert child2 in grouped_names

        standalone_names = [a for a, _ in standalone]
        assert parent not in standalone_names
        assert child1 not in standalone_names
        assert child2 not in standalone_names

    def test_filter_subaccounts_no_match(self):
        """A registered parent with no matching children yields empty subaccounts_sorted."""
        parent = "Assets:CA:Foo"
        other = "Assets:CA:Wise:Main"

        accounts_sorted = [(other, (make_open(other), None))]

        subaccounts_sorted, standalone = filter_subaccounts([parent], accounts_sorted)

        assert subaccounts_sorted == {}
        assert other in [a for a, _ in standalone]

    def test_filter_subaccounts_grandchildren_not_grouped(self):
        """Grandchildren (two levels deep) are not grouped under the registered parent."""
        parent = "Assets:CA:Tangerine:Saving"
        child = "Assets:CA:Tangerine:Saving:Main"
        grandchild = "Assets:CA:Tangerine:Saving:Main:Sub"

        accounts_sorted = [
            (parent, (make_open(parent), None)),
            (child, (make_open(child), None)),
            (grandchild, (make_open(grandchild), None)),
        ]

        subaccounts_sorted, standalone = filter_subaccounts([parent], accounts_sorted)

        grouped_names = [a for a, _ in subaccounts_sorted[parent]]
        assert child in grouped_names

        standalone_names = [a for a, _ in standalone]
        assert grandchild in standalone_names
        assert parent not in standalone_names

    def test_filter_subaccounts_unrelated_accounts_pass_through(self):
        """Accounts unrelated to any registered parent pass through to standalone unchanged."""
        parent = "Assets:CA:Tangerine:Saving"
        unrelated = "Assets:CA:Wise:Main"

        accounts_sorted = [
            (parent, (make_open(parent), None)),
            (unrelated, (make_open(unrelated), None)),
        ]

        subaccounts_sorted, standalone = filter_subaccounts([parent], accounts_sorted)

        assert subaccounts_sorted == {}
        standalone_names = [a for a, _ in standalone]
        assert unrelated in standalone_names
        # Parent had no children so it stays in standalone too.
        assert parent in standalone_names


# ---------------------------------------------------------------------------
# build_reportable() tests
# ---------------------------------------------------------------------------

class TestBuildReportable:

    def test_build_reportable_standalone(self):
        """With no subaccounts, both active accounts are returned."""
        acct1 = "Assets:CA:Wise:Main"
        acct2 = "Assets:CA:Tangerine:Checking"

        accounts_sorted = [
            (acct1, (make_open(acct1), None)),
            (acct2, (make_open(acct2), None)),
        ]
        realized_accounts = collections.defaultdict(list)

        result = build_reportable(accounts_sorted, None, realized_accounts, year=2024)

        display_names = [r[0] for r in result]
        assert acct1 in display_names
        assert acct2 in display_names
        assert len(result) == 2

    def test_build_reportable_excludes_inactive(self):
        """An account closed before the reporting year is excluded."""
        active_acct = "Assets:CA:Wise:Main"
        closed_acct = "Assets:CA:OldBank:Checking"

        accounts_sorted = [
            (active_acct, (make_open(active_acct), None)),
            (closed_acct, (make_open(closed_acct, date=datetime.date(2010, 1, 1)),
                           make_close(closed_acct, datetime.date(2020, 6, 1)))),
        ]
        realized_accounts = collections.defaultdict(list)

        result = build_reportable(accounts_sorted, None, realized_accounts, year=2024)

        display_names = [r[0] for r in result]
        assert active_acct in display_names
        assert closed_acct not in display_names

    def test_build_reportable_subaccounts_merged(self):
        """Children of a registered parent are merged into a single entry with postings sorted by date."""
        parent = "Assets:CA:Tangerine:Saving"
        child1 = "Assets:CA:Tangerine:Saving:Main"
        child2 = "Assets:CA:Tangerine:Saving:Travel"

        accounts_sorted = [
            (parent, (make_open(parent), None)),
            (child1, (make_open(child1), None)),
            (child2, (make_open(child2), None)),
        ]

        p1 = make_txn_posting(child1, 100, "CAD", datetime.date(2024, 3, 15))
        p2 = make_txn_posting(child2, 200, "CAD", datetime.date(2024, 1, 10))

        realized_accounts = collections.defaultdict(list)
        realized_accounts[child1] = [p1]
        realized_accounts[child2] = [p2]

        result = build_reportable(accounts_sorted, [parent], realized_accounts, year=2024)

        display_names = [r[0] for r in result]
        assert parent in display_names
        assert child1 not in display_names
        assert child2 not in display_names

        merged = next(r for r in result if r[0] == parent)
        merged_postings = merged[2]
        assert len(merged_postings) == 2
        dates = [fincen.get_date(p) for p in merged_postings]
        assert dates == sorted(dates)

    def test_build_reportable_only_account_filter(self):
        """only_account restricts results to the specified account(s)."""
        acct1 = "Assets:CA:Wise:Main"
        acct2 = "Assets:CA:Tangerine:Checking"

        accounts_sorted = [
            (acct1, (make_open(acct1), None)),
            (acct2, (make_open(acct2), None)),
        ]
        realized_accounts = collections.defaultdict(list)

        result = build_reportable(
            accounts_sorted, None, realized_accounts,
            year=2024, only_account=[acct1],
        )

        display_names = [r[0] for r in result]
        assert acct1 in display_names
        assert acct2 not in display_names
        assert len(result) == 1

    def test_build_reportable_no_subaccount_children_active(self):
        """When all children of a registered parent are closed before the year, the parent is omitted."""
        parent = "Assets:CA:Tangerine:Saving"
        child1 = "Assets:CA:Tangerine:Saving:Main"
        child2 = "Assets:CA:Tangerine:Saving:Travel"

        accounts_sorted = [
            (parent, (make_open(parent), None)),
            (child1, (make_open(child1, date=datetime.date(2010, 1, 1)),
                      make_close(child1, datetime.date(2019, 12, 31)))),
            (child2, (make_open(child2, date=datetime.date(2010, 1, 1)),
                      make_close(child2, datetime.date(2018, 6, 1)))),
        ]

        realized_accounts = collections.defaultdict(list)
        realized_accounts[child1] = [make_txn_posting(child1, 100, "CAD", datetime.date(2018, 5, 1))]
        realized_accounts[child2] = [make_txn_posting(child2, 200, "CAD", datetime.date(2017, 3, 1))]

        result = build_reportable(accounts_sorted, [parent], realized_accounts, year=2024)

        assert parent not in [r[0] for r in result]


# ---------------------------------------------------------------------------
# iter_year() tests
# ---------------------------------------------------------------------------

class TestIterYear:

    def test_iter_year_basic(self):
        """Posting mid-year: USD balance is 0 before posting date, non-zero after."""
        price_map = make_price_map("CAD", "1.0", "2024-01-01")
        p = make_txn_posting(ACCT, 1000, "CAD", datetime.date(2024, 6, 1))
        inventory = beancount.core.inventory.Inventory()

        results = list(fincen.iter_year(2024, [p], inventory, price_map))
        by_date = {d: usd for d, usd, _ in results}

        assert by_date[datetime.date(2024, 5, 31)].get_currency_units("USD").number == 0
        assert by_date[datetime.date(2024, 6, 1)].get_currency_units("USD").number == Decimal("1000")

    def test_iter_year_carry_in(self):
        """Pre-seeded inventory is visible from Jan 1 even with no current-year postings."""
        price_map = make_price_map("CAD", "1.0", "2024-01-01")
        inventory = beancount.core.inventory.Inventory()
        fincen.add_position(make_txn_posting(ACCT, 500, "CAD", datetime.date(2023, 6, 1)), inventory)

        results = list(fincen.iter_year(2024, [], inventory, price_map))
        assert results[0][1].get_currency_units("USD").number == Decimal("500")

    def test_iter_year_no_postings(self):
        """Empty postings list and empty inventory yields 0 USD for all days."""
        results = list(fincen.iter_year(2024, [], beancount.core.inventory.Inventory(), empty_price_map()))
        assert all(usd.get_currency_units("USD").number == 0 for _, usd, _ in results)

    def test_iter_year_jan1_posting(self):
        """A posting dated Jan 1 is visible on Jan 1."""
        price_map = make_price_map("CAD", "1.0", "2024-01-01")
        p = make_txn_posting(ACCT, 100, "CAD", datetime.date(2024, 1, 1))
        inventory = beancount.core.inventory.Inventory()

        results = list(fincen.iter_year(2024, [p], inventory, price_map))
        assert results[0][1].get_currency_units("USD").number == Decimal("100")

    def test_iter_year_dec31_posting(self):
        """A posting dated Dec 31 is visible on Dec 31."""
        price_map = make_price_map("CAD", "1.0", "2024-01-01")
        p = make_txn_posting(ACCT, 200, "CAD", datetime.date(2024, 12, 31))
        inventory = beancount.core.inventory.Inventory()

        results = list(fincen.iter_year(2024, [p], inventory, price_map))
        assert results[-1][1].get_currency_units("USD").number == Decimal("200")

    def test_iter_year_multiple_postings(self):
        """Multiple postings accumulate correctly as running balance."""
        price_map = make_price_map("CAD", "1.0", "2024-01-01")
        p1 = make_txn_posting(ACCT, 100, "CAD", datetime.date(2024, 2, 1))
        p2 = make_txn_posting(ACCT, 200, "CAD", datetime.date(2024, 5, 1))
        p3 = make_txn_posting(ACCT, 300, "CAD", datetime.date(2024, 9, 1))
        inventory = beancount.core.inventory.Inventory()

        results = list(fincen.iter_year(2024, [p1, p2, p3], inventory, price_map))
        by_date = {d: usd.get_currency_units("USD").number for d, usd, _ in results}

        assert by_date[datetime.date(2024, 2, 1)] == Decimal("100")
        assert by_date[datetime.date(2024, 5, 1)] == Decimal("300")
        assert by_date[datetime.date(2024, 9, 1)] == Decimal("600")

    def test_iter_year_365_or_366_dates(self):
        """2024 (leap year) yields 366 dates; 2023 yields 365."""
        pm = empty_price_map()
        assert len(list(fincen.iter_year(2024, [], beancount.core.inventory.Inventory(), pm))) == 366
        assert len(list(fincen.iter_year(2023, [], beancount.core.inventory.Inventory(), pm))) == 365


# ---------------------------------------------------------------------------
# find_daily_max() tests
# ---------------------------------------------------------------------------

class TestFindDailyMax:

    def test_find_daily_max_basic(self):
        """Single CAD posting: max USD equals CAD * rate, truncated to int."""
        price_map = make_price_map("CAD", "0.75", "2024-01-01")
        p = make_txn_posting(ACCT, 1000, "CAD", datetime.date(2024, 3, 1))

        max_usd, max_cad, max_date = fincen.find_daily_max(2024, [p], price_map)

        assert max_usd == 750
        assert max_cad == 1000
        assert max_date is not None

    def test_find_daily_max_no_postings(self):
        """No postings returns (0, 0, None)."""
        max_usd, max_cad, max_date = fincen.find_daily_max(2024, [], empty_price_map())
        assert (max_usd, max_cad, max_date) == (0, 0, None)

    def test_find_daily_max_carry_in(self):
        """Balance from a prior-year posting is present all year and is found as the max."""
        price_map = make_price_map("CAD", "0.75", "2024-01-01")
        p = make_txn_posting(ACCT, 800, "CAD", datetime.date(2023, 6, 1))

        max_usd, max_cad, max_date = fincen.find_daily_max(2024, [p], price_map)

        assert max_usd == 600   # int(800 * 0.75)
        assert max_cad == 800
        assert max_date is not None

    def test_find_daily_max_peak_then_withdrawal(self):
        """Peak is captured at deposit; withdrawal afterwards doesn't lower the recorded max."""
        price_map = make_price_map("CAD", "1.0", "2024-01-01")
        deposit = make_txn_posting(ACCT, 1000, "CAD", datetime.date(2024, 6, 1))
        withdrawal = make_txn_posting(ACCT, -800, "CAD", datetime.date(2024, 9, 1))

        max_usd, max_cad, max_date = fincen.find_daily_max(2024, [deposit, withdrawal], price_map)

        assert max_usd == 1000
        assert max_cad == 1000
        assert max_date < datetime.date(2024, 9, 1)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

def _load_and_build(bean_file, year, subaccounts=None, only_account=None):
    """Helper: load a .bean file and return (reportable, price_map)."""
    entries, errors, options = beancount.loader.load_file(bean_file)
    account_types = beancount.parser.options.get_account_types(options)
    open_close = beancount.core.getters.get_account_open_close(entries)

    filterby_assets = functools.partial(
        beancount.core.account_types.is_account_type, account_types.assets
    )
    sortby_name = functools.partial(
        beancount.core.account_types.get_account_sort_key, account_types
    )
    accounts_sorted = sorted(
        filter(lambda e: filterby_assets(e[0]), open_close.items()),
        key=lambda e: sortby_name(e[0]),
    )

    realized_accounts = beancount.core.realization.postings_by_account(entries)
    price_map = beancount.core.prices.build_price_map(entries)

    reportable = fincen.build_reportable(
        accounts_sorted, subaccounts, realized_accounts, year, only_account
    )
    return reportable, price_map


class TestIntegration:

    def test_integration_example_bean(self):
        """example.bean: Tangerine:Checking peaks at 10000 CAD, Wise:Main at 8000 CAD."""
        reportable, price_map = _load_and_build("example/example.bean", 2024)
        by_name = {name: postings for name, _, postings in reportable}

        assert "Assets:CA:Tangerine:Checking" in by_name
        assert "Assets:CA:Wise:Main" in by_name

        max_usd_t, max_cad_t, _ = fincen.find_daily_max(2024, by_name["Assets:CA:Tangerine:Checking"], price_map)
        assert max_cad_t == 10000
        assert 7000 <= max_usd_t <= 8000

        max_usd_w, max_cad_w, _ = fincen.find_daily_max(2024, by_name["Assets:CA:Wise:Main"], price_map)
        assert max_cad_w == 8000
        assert 5500 <= max_usd_w <= 6500

    def test_integration_subaccounts_bean(self):
        """example_subaccounts.bean: Tangerine:Saving children merge to 9000 CAD peak."""
        parent = "Assets:CA:Tangerine:Saving"
        reportable, price_map = _load_and_build(
            "example/example_subaccounts.bean", 2024, subaccounts=[parent]
        )

        merged = next((r for r in reportable if r[0] == parent), None)
        assert merged is not None, f"{parent} not found in reportable"

        max_usd, max_cad, _ = fincen.find_daily_max(2024, merged[2], price_map)
        assert max_cad == 9000
        assert 6000 <= max_usd <= 7000
