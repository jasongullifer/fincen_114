#!/usr/bin/env python
import argparse
import logging
import sys
import functools
import math
import collections
import datetime
import heapq
import beancount.loader
import beancount.utils
import beancount.core
import beancount.core.realization
import beancount.core.data
import beancount.parser
from beancount.utils.date_utils import iter_dates
from beancount.core.number import D
from beancount.core.amount import Amount, add
import pandas as pd

def get_date(p):
    """Return the date of a Posting or TxnPosting."""
    if isinstance(p, beancount.core.data.Posting):
        return p.date
    elif isinstance(p, beancount.core.data.TxnPosting):
        return p.txn.date
    else:
        raise Exception("Not a Posting or TxnPosting", p)

def only_postings(p):
    """Return True if p is a Posting or TxnPosting (filters out balance/pad entries)."""
    return isinstance(p, (beancount.core.data.Posting, beancount.core.data.TxnPosting))
        
def this_year(year, p):
    """Return True if the posting's date falls within the given year."""
    return get_date(p).year == year

def add_position(p, inventory):
    """Add a Posting or TxnPosting to the given Inventory."""
    if isinstance(p, beancount.core.data.Posting):
        inventory.add_position(p)
    elif isinstance(p, beancount.core.data.TxnPosting):
        inventory.add_position(p.posting)
    else:
        raise Exception("Not a Posting or TxnPosting", p)

def start_of_year_inventory(year, postings):
    """Build an Inventory from all postings strictly before the given year."""
    balance = beancount.core.inventory.Inventory()
    for p in filter(only_postings, postings):
        if get_date(p).year < year:
            add_position(p, balance)
    return balance

def iter_year(year, account_postings, inventory, price_map):
    """Iterate over every date in the year, yielding (date, usd_balance, cad_balance).

    Applies postings in chronological order, converting the running inventory to
    USD and CAD at each date using price_map. inventory should be pre-seeded with
    the carry-in balance from before the year starts.
    """
    assert all(
        get_date(a) <= get_date(b)
        for a, b in zip(account_postings, account_postings[1:])
    ), "account_postings must be sorted by date"
    start_of_year = datetime.date(year, 1, 1)
    end_of_year = datetime.date(year+1, 1, 1)

    filter_year = functools.partial(this_year, year)

    postings = account_postings
    postings = filter(filter_year, iter(postings))
    txn = next(postings, None)
    for date in iter_dates(start_of_year, end_of_year):
        while txn and get_date(txn) <= date:
            add_position(txn, inventory)
            txn = next(postings, None)
        yield date, inventory.reduce(beancount.core.convert.convert_position, 'USD', price_map, date), inventory.reduce(beancount.core.convert.convert_position, 'CAD', price_map, date)

def get_account_number(account, keys):
    """Return the first metadata value found for any of the given keys, or '' if none match."""
    for k in keys:
        if k in account.meta:
            return account.meta[k]
    return ''

def account_active_in(open_directive, close_directive, year):
    """Return True if the account was open at any point during the given year."""
    open_year = open_directive.date.year if open_directive else -math.inf
    close_year = close_directive.date.year if close_directive else math.inf
    return open_year <= year <= close_year

def get_parent(account):
    """Return the immediate parent account name, or None if account has no parent."""
    if ":" in account:
        return account.rsplit(":", 1)[0]             
    return None

def filter_subaccounts(subaccts, accounts_sorted):
    """Separate direct children of subacct parents from the standalone account list.

    Returns (subaccounts_sorted, standalone_accounts) where:
      - subaccounts_sorted: dict mapping each parent in subaccts to its direct children
      - standalone_accounts: accounts that are not direct children of any subacct parent,
        with parent accounts that have children removed
    """
    subaccts = set(subaccts)              # O(1) lookups
    subaccounts_sorted = {m: [] for m in subaccts}
    accounts_filtered = []
    majors_with_children = set()

    for account, (open_directive, close_directive) in accounts_sorted:
        # Find nearest parent
        parent = get_parent(account)

        if parent and parent in subaccts:
            subaccounts_sorted[parent].append((account, (open_directive, close_directive)))
            majors_with_children.add(parent)
        else:
            accounts_filtered.append((account, (open_directive, close_directive)))

    # Remove empty major buckets
    subaccounts_sorted = {m: children for m, children in subaccounts_sorted.items() if children}

    # Remove parent accounts from standalone list
    accounts_filtered = [a for a in accounts_filtered if a[0] not in majors_with_children]

    return subaccounts_sorted, accounts_filtered

def build_reportable(accounts_sorted, subaccounts, realized_accounts, year, only_account=None):
    """Build the list of (display_name, open_directive, postings) tuples to report.

    Standalone accounts are included as-is. Accounts whose parent is listed in
    subaccounts have their postings merged under the parent name. Accounts not
    active in year (per account_active_in) are excluded.
    """
    if subaccounts:
        subaccounts_sorted, standalone_accounts = filter_subaccounts(subaccounts, accounts_sorted)
    else:
        standalone_accounts = accounts_sorted

    reportable = []
    for account, (open_directive, close_directive) in standalone_accounts:
        if only_account and account not in only_account:
            continue
        if account_active_in(open_directive, close_directive, year):
            reportable.append((account, open_directive, [p for p in realized_accounts[account] if only_postings(p)]))

    if subaccounts:        
        for major_account, minor_accounts in subaccounts_sorted.items():
            postings = []
            streams = []
            last_open = None
            for account, (open_directive, close_directive) in minor_accounts:
                if only_account and account not in only_account:
                    continue
                if account_active_in(open_directive, close_directive, year):
                    streams.append([p for p in realized_accounts[account] if only_postings(p)])
                    last_open = open_directive
            postings = list(heapq.merge(*streams, key=get_date))
            if postings:
                reportable.append((major_account, last_open, postings))

    return reportable

def find_daily_max(year, postings, price_map):
    """Return (max_usd, max_cad, date) for the peak USD balance during the year."""
    inventory = start_of_year_inventory(year, postings)
    max_value_date = None
    max_value = 0
    max_value_cad = 0
    for date, balance_usd, balance_cad in iter_year(year, postings, inventory, price_map):
        usd_value = balance_usd.get_currency_units('USD')
        cad_value = balance_cad.get_currency_units('CAD')
        if usd_value.number > max_value:
            max_value = usd_value.number
            max_value_cad = cad_value.number
            max_value_date = date
    return int(max_value), int(max_value_cad), max_value_date

def get_cli_args():
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Summarise account information for FinCEN 114 filing."
    )
    parser.add_argument('bean', help='Path to the beancount file.')
    parser.add_argument('--year', type=int, help='Which year to summarise.', required=True)
    parser.add_argument('--subaccounts', action='append', help='Group sub-accounts under this parent account (repeatable).')
    parser.add_argument('--only-account', action='append', help='Only calculate for specified account(s).')
    parser.add_argument('--meta-account-number', default=['account-number'], action='append', help='Metadata key(s) containing account numbers.')
    args = parser.parse_args()
    return args

if __name__ == '__main__':
    args = get_cli_args()
    entries, errors, options = beancount.loader.load_file(args.bean, logging.info, log_errors=sys.stderr)

    account_types = beancount.parser.options.get_account_types(options)
    open_close = beancount.core.getters.get_account_open_close(entries)

    # We only want Asset accounts and we want them sorted by account name
    filterby_assets = functools.partial(beancount.core.account_types.is_account_type, account_types.assets)
    sortby_name = functools.partial(beancount.core.account_types.get_account_sort_key, account_types)

    items = open_close.items()
    accounts_filtered = filter(lambda entry: filterby_assets(entry[0]), items)
    accounts_sorted = sorted(accounts_filtered, key=lambda entry: sortby_name(entry[0]))

    realized_accounts = beancount.core.realization.postings_by_account(entries)
    price_map = beancount.core.prices.build_price_map(entries)

    reportable = build_reportable(accounts_sorted, args.subaccounts, realized_accounts, args.year, args.only_account)

    rows = []
    print(f"{'Account name':50s}\t{'CAD':>12s}\t{'USD':>12s}\t{'Acct. number':>12s}")
    for display_name, open_directive, postings in reportable:
        max_value, max_value_cad, max_value_date = find_daily_max(args.year, postings, price_map)
        account_number = get_account_number(open_directive, args.meta_account_number) if open_directive else ''
        rows.append({
            "account": display_name,
            "account_number": account_number,
            "date": max_value_date,
            "cad": max_value_cad,
            "usd": max_value
        })
        print(f"{display_name:50s}\t${(max_value_cad):11,.0f}\t${(max_value):11,.0f}\t{account_number:>12}")

    df = pd.DataFrame(rows)
    df.to_csv("summary.csv", index=False)