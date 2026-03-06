# The Quick Version

```
>> python fin-cen-114.py --year 2024 example/example.bean
Account name                                      CAD             USD    Acct. number
Assets:CA:Tangerine:Checking             $     10,000    $      7,500      CA-CHK-001
Assets:CA:Wise:Main                      $      8,000    $      6,000         WISE-01
```

Using this script means

1. You don't accidentally forget any accounts.
2. You don't have to remember/lookup/calculate the maximum account value in $USD.
3. You don't have to lookup the account numbers of all your foreign accounts.

# FinCEN 114

FinCEN Report 114[1], or Report of Foreign Bank and Financial Accounts
(FBAR), is required to be filed every year by US persons (basically, US
citizen and residents) who hold a bank account overseas and whose aggregate
value of all foreign financial accounts exceeds $10,000 in any year.  You
have to list every foreign bank account, **along with the maximum account
value during the year**, the account number, the name of the institution,
and some other similar details.

For many US persons, this is not necessarily challenging to gather. If you
have a lot of accounts and the value of those accounts has varied substantially
then it becomes a little bit harder.

Of course, if you're using beancount[2] then it **already** has all the
information needed for filing FinCEN 114. This script does the work of
gathering it all together for you.

# Account numbers

If you add metadata on the account opening directive then this script will
remind you of what that is. This makes filling in the FinCEN 114 easier,
since you don't have to hunt down all the account numbers yourself. You can
specify the metadata like so:

```
2014-04-08 open Assets:AUS:Citibank AUD
        account-number: 431411339
```

If you prefer to use a different metadata keyword, you can specify that with
the ```--meta-account-number``` option.

# Price map warning

The results you get will only be as good as your price data. If you try to
run this for the year 2010 but haven't given beancount any price directives
for the year 2010, then the results will be nonsensical. (You will usually get
a value of $0 for the account, regardless of what is in it.)

# Option summary

* ```--year``` the calendar year to use
* ```--only-account``` Instead of listing all accounts, you can specify which accounts you care about.
* ```--subaccount``` Specify the the top level name for an account that has subaccounts that will be summed and reported together. E.g., Supplying `Assets:Stocks` would aggregate subaccounts related to ticker symbols like `Assets:Stocks:Symbol1` `Assets:Stocks:Symbol2` `Assets:Stocks:Symbol3`. Can also be used if you divide a real bank account into theoretical categories.
* ```--meta-account-number``` This is the metadata on the account opening directive that includes the account number. By default it will look for *account-number*

# Subaccount example

The file example_subaccounts.bean demonstrates the --subaccount option.

In this example, the account Assets:CA:Wise:Main represents a single real-world bank account, but it is divided into two subaccounts in the ledger:

Assets:CA:Wise:Main:Savings

Assets:CA:Wise:Main:Travel

This is a common bookkeeping pattern used to track how money in one account is allocated to different purposes. However, for FBAR reporting, these balances must be reported as one account, since they all belong to the same underlying bank account.

If the script is run normally, each subaccount is reported separately:

```
>> python fin-cen-114.py --year 2024 example/example_subaccounts.bean 
Account name                                      CAD             USD    Acct. number
Assets:CA:Tangerine:Checking             $     10,000    $      7,500      CA-CHK-001
Assets:CA:Wise:Main                      $          0    $          0         WISE-01
Assets:CA:Wise:Main:Savings              $      5,000    $      3,750         WISE-01
Assets:CA:Wise:Main:Travel               $      3,000    $      2,250         WISE-01
```

Using the --subaccount option tells the script to treat the specified account and all of its subaccounts as a single account, aggregating their balances.

```
>> python fin-cen-114.py --year 2024 --subaccount Assets:CA:Wise:Main example/example_subaccounts.bean 
Found subaccount: Assets:CA:Wise:Main                               
Found subaccount: Assets:CA:Wise:Main:Savings                       
Found subaccount: Assets:CA:Wise:Main:Travel                        

Account name                                      CAD             USD    Acct. number
Assets:CA:Tangerine:Checking             $     10,000    $      7,500      CA-CHK-001
Assets:CA:Wise:Main                      $      8,000    $      6,000         WISE-01
```

In this case, the balances of the Savings and Travel subaccounts are combined, producing a total balance of 8,000 CAD (6,000 USD) for the underlying Wise account.

[1]: https://bsaefiling.fincen.treas.gov/NoRegFBARFiler.html
[2]: https://bitbucket.org/blais/beancount/overview
