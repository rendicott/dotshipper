# dotshipper

Runs a Vertica query for a given time window and then prepares the data for shipping to Anodot.

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
