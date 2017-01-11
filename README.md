# dotshipper

Runs a Vertica query for a given time window and then prepares the data for shipping to Anodot. It stores the from-to timestamp window in a Redis instance so the next time it runs it only queries latest data from the last run. 

![Sample Anodot Email Alert](../images/anodot-alert.PNG)

## Pre-reqs

- Requires pyTableFormat for debugging (https://github.com/rendicott/pyTableFormat)
- Requires a Redis instance (on localhost is fine)


Additionaly requires the following standard packages:
```
pip install vertica_python
pip install redis
pip install requests
pip install jinja2
```

## Usage

Here's the in-program help output:
```
C:\source\rendicott-github\dotshipper>python dotshipper.py --help
Usage: dotshipper.py [--debug] [--printtostdout] [--logfile] [--version] [--help] [--samplefileoption]

Options:
  --version             show program's version number and exit
  -h, --help            show this help message and exit
  -c FILE, --configfile=FILE
                        REQUIRED: config ini file. See sample. (default =
                        'runningconfig.ini'
  --query_lag_hours=QUERY_LAG_HOURS
                        Number of hours back to adjust the query time window.
                        (Default=96)
  --query_limit=QUERY_LIMIT
                        Number of lines to limit results in Vertica query.
                        (Default='ALL')
  -s, --simulate        Boolean flag. If this option is present then no REST
                        calls will be made, only testing. (Default=False)

  Debug Options:
    -d DEBUG, --debug=DEBUG
                        Available levels are CRITICAL (3), ERROR (2), WARNING
                        (1), INFO (0), DEBUG (-1)
    -p, --printtostdout
                        Print all log messages to stdout
    -l FILE, --logfile=FILE
                        Desired filename of log file output. Default is
                        "dotshipper.py.log"

```


Example command:
```
python dotshipper.py --configfile dotshipper.ini -d 0 --query_lag_hours=145 --simulate
```


## Configuration

I'll explain each section of the config file

```
[general]
# as outlined here https://docs.python.org/2/library/datetime.html#strftime-and-strptime-behavior
#timestamp_format: %Y-%m-%d %H:%M:%S.%f
timestamp_format: %Y-%m-%d %H:%M:%S
# shifts the 'now' timestamp to now minus lag to combat data lag
query_lag_hours: 96
```
The timestamp format is however the timestamp is stored coming back from the Vertica query.
The query_lag_hours is there because the data from Vertica has a lag of at least 4 days before the majority of the data is in. Therefore we'll redefine 'now' as 'now - 4 days'. 


```
[anodot]
url_with_api_key: https://api.anodot.com/api/v1/metrics?token=asdfeasdfgab48c51
# what to put after the what= for these metrics
what: offerserved_counts_per_hour
# what tags to add to this data
tags: {"target_type":"counter"}
# the version dimension for this data
ver: 1
```
For the Anodot URL put your custom auth token in the URL. 
For the 'what' line put what you want your metrics to be called. 
For the 'tags' line put the type of data tag for Anodot.

More info can be found here: https://support.anodot.com/hc/en-us/articles/208694449-Post-Time-Series-Data-Points

Sample output body from real working REST call:
```
{"name":"ver=1.InstitutionID=5555.OfferID=110171.SourceChannelID=5.LocationID=7.SourceDisplayID=7.DisplayID=2.MarkServedMethodID=0.SourceMarkServedMethodID=13.what=offerserved_counts_per_hour","timestamp": 1471615200,"value": 13,"tags": {"target_type":"counter"} }
```

```
[redis]
redis_address: 127.0.0.1
redis_port: 6379
```
Pretty straight forward. Put Redis connection details here. It will default to db=0 (sorry)

```
[vertica]
host: 10.99.89.12
port: 5433
user: userdude
password: hahanotmypaswword
database: BIGDOTA
read_timeout: 600
unicode_error: strict
ssl: False
```
Same straightforward here. Put your Vertica connection info here. 