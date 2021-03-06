#!/usr/bin/env python
import vertica_python
import ConfigParser
import datetime
import logging
import os
import sys
import ast
import redis
import requests
import json
import time
from jinja2 import Template
from pyTableFormat import TableFormat
from pyTableFormat import Table_Formattable_Object

sversion = '0.1'
scriptfilename = os.path.basename(sys.argv[0])
defaultlogfilename = scriptfilename + '.log'

BEGINNINGOFTIME = datetime.datetime.fromtimestamp(0)


jtpl_conn_info = """
{'host':'{{ vert_host }}',
 'port': {{ vert_port }},
 'user': '{{ vert_user }}',
 'password': '{{ vert_pass }}',
 'database': '{{ vert_db }}',
 'read_timeout': {{ vert_read_timeout }},
 'unicode_error': '{{ vert_unicode_error}}',
 'ssl': {{ vert_ssl }}
}
"""

jtpl_vertica_query_non_bucketized = """
select /*+direct*/
          timestamp_trunc(ServedDT,'hh24') as ServedDate_Hour,
          InstitutionID,
          SourceChannelID,  ChannelID,
          SourceLocationID, LocationID,
          SourceDisplayID, DisplayID,
          case when TransactionID > 1 then 1 else 0 end as Trxn_Placement_Flag, case when SourceTransactionID > 1 then 1 else 0 end as SourceTrxn_Placement_Flag,
          MarkServedMethodID, SourceMarkServedMethodID,
          count(*) as counts
 from {{ db }}.{{ table }}
 WHERE ServedDT >= '{{ from_time }}'  AND ServedDT < '{{ to_time }}'
 group by 1,2,3,4,5,6,7,8,9,10,11,12
 limit {{ limit }};
"""

jtpl_vertica_query_build_temp = """
-- build shell table
drop table if exists temp.ew_anodotUpload cascade;
create table temp.ew_anodotUpload as /*+direct*/ (
        with dates as (
                select  timestamp_trunc(ServedDT, 'hh24') as ServedDate_hour
                from    {{ db }}.{{ table }}
                where   1=1
                        and ServedDate between '{{ from_time }}' and '{{ to_time }}' /*UPDATE DATES*/
                group by 1 order by 1
                ),
        indicators as (
                select  InstitutionID, ChannelID, LocationID, DisplayID, MarkServedMethodID
                from    {{ db }}.{{ table }}
                where   year(ServedDate) > 2014
                group by InstitutionID, ChannelID, LocationID, DisplayID, MarkServedMethodID
                )

        select  ServedDate_hour,
                InstitutionID, ChannelID, LocationID, DisplayID, MarkServedMethodID,
                0 as srv_count
        from    dates cross join indicators
        group by ServedDate_hour, InstitutionID, ChannelID, LocationID, DisplayID, MarkServedMethodID
        order by ServedDate_hour,  InstitutionID, ChannelID, LocationID, DisplayID, MarkServedMethodID
        );
        
        
--- pull serve data
drop table if exists srvData;
create local temporary table srvData on commit preserve rows as /*+direct*/ (  
        select  timestamp_trunc(ServedDT, 'hh24') as ServedDate_hour,
                InstitutionID, 
                ChannelID, 
                LocationID, 
                DisplayID,
                MarkServedMethodID,
                count(OfferServedID) as counts
        from    {{ db }}.{{ table }}
        where   ServedDate between '{{ from_time }}' and '{{ to_time }}' /*UPDATE DATES*/
        group by 1,2,3,4,5,6
        order by 1,2,3,4,5,6
        ); -- 23 seconds
        
        

--- update upload table
update  temp.ew_anodotUpload a
set     srv_count = counts
from    srvData b
where   1=1
        and(a.ServedDate_hour||'+'||a.InstitutionID||'+'||a.ChannelID||'+'||a.LocationID||'+'||a.DisplayID||'+'||a.MarkServedMethodID) :: varchar = (b.ServedDate_hour||'+'||b.InstitutionID||'+'||b.ChannelID||'+'||b.LocationID||'+'||b.DisplayID||'+'||b.MarkServedMethodID) :: varchar
        ;

"""

jtpl_vertica_query = """
select * from temp.ew_anodotUpload;
"""

jtpl_vertica_query_basic = """
SELECT * FROM {{ db }}.{{ table }}
WHERE VerticaUpdateDate >= '{{ from_time }}'  AND VerticaUpdateDate < '{{ to_time }}';
"""

# what we're sending to Anodot
#   ServedDate_Hour,InstitutionID,SourceChannelID,ChannelID,SourceLocationID,
#   LocationID,SourceDisplayID,DisplayID,Trxn_Placement_Flag,SourceTrxn_Placement_Flag,
#   MarkServedMethodID,SourceMarkServedMethodID,counts
'''
body:
[{"name":"company=anodot.device=test.what=hello_world_count",
"timestamp":`date +%s`,"value":100,"tags":{"target_type":"counter"}}]
'''
jtpl_anodot_body = """
{"name":"InstitutionID={{ institution_id }}.
LocationID={{ location_id }}.DisplayID={{ display_id }}.
MarkServedMethodID={{ mark_served_method_id }}",
"timestamp": {{ anodot_timestamp }},"value": {{ value }},"tags":{}}
"""

def setuplogging(loglev, printtostdout, logfile):
    """
    pretty self explanatory. Takes options and sets up logging.
    :param loglev:
    :param printtostdout:
    :param logfile:
    :return: None
    """
    logging.basicConfig(filename=logfile,
                        filemode='w', level=loglev,
                        format='%(asctime)s:%(levelname)s:%(message)s')
    if printtostdout:
        soh = logging.StreamHandler(sys.stdout)
        soh.setLevel(loglev)
        logger = logging.getLogger()
        logger.addHandler(soh)


def vert_query(settingsobj,from_time,to_time,limit):
    settings_dict = settingsobj.make_vert_conn_dict()
    conn_info_tpl = Template(jtpl_conn_info)
    conn_info = conn_info_tpl.render(settings_dict)
    # convert from unicode string dict to actual dict
    conn_info = ast.literal_eval(conn_info)
    logging.info("Connection info dict is like this: " + str(conn_info))
    connection = vertica_python.connect(**conn_info)
    cur = connection.cursor()
    # first build the bucket tables
    q_buildTemp = Template(jtpl_vertica_query_build_temp)
    qstring = q_buildTemp.render(db=settingsobj.vert_db,table="cdw.vOfferServed",from_time=from_time,to_time=to_time,limit=limit)
    logging.info("BUILT QUERY FOR GENERATING TEMP TABLES IS:")
    logging.info("--------------------------------------")
    logging.info(qstring)
    logging.info("--------------------------------------")
    cur.execute(qstring)
    
    # now query the temp table for actual results
    q_readTemp = Template(jtpl_vertica_query)
    qstring = q_readTemp.render()
    logging.info("done build temp tables")
    logging.info(" ")
    logging.info("BUILT QUERY FOR QUERYING TEMP TABLE IS:")
    logging.info("--------------------------------------")
    logging.info(qstring)
    logging.info("--------------------------------------")
    cur.execute(qstring)
    results = cur.fetchall()
    cur.close()

    ''' Sample result line
    [2002, 43913, datetime.date(2013, 2, 23), datetime.datetime(2013, 2, 23, 4, 4, 25), 20020295736907L, 5, 5, 2, 2612822934L, 3851110869L, 0, 5, 5, 2, 3851110869L, 0, 18006, datetime.date(2013, 2, 27), None, datetime.datetime(2013, 5, 22, 17, 4, 54, 674620)]
    '''
    return results

class VOSlist:
    def __init__(self):
        self.vosrows = []
        # holder list for lists of len 1000
        self.kbodies = []
    def sort_by_date(self):
        self.vosrows.sort(key=lambda x: x.anodot_timestamp_epoch_str, reverse=False)
    def dumpself(self):
        msg = self.vosrows[1].tableformat_header()
        for vosrow in self.vosrows:
            msg += vosrow.dumpself_tableformat()
        return msg
    def dump_csv(self,filename):
        with open(filename,'wb') as f:
            f.write(self.vosrows[1].dumpself_csv_header())
            for i,v in enumerate(self.vosrows):
                f.write(v.dumpself_csv())
    def make_kbodies(self):
        total = len(self.vosrows)
        num_of_chunks = total / 1000
        remainder = total % 1000
        for i in range(num_of_chunks):
            klist = []
            for j in range(1000):
                body = self.vosrows.pop().build_anodot_body()
                klist.append(body)
            self.kbodies.append(klist)
        # now handle remainder
        klist = []
        for k in range(remainder):
            body = self.vosrows.pop().build_anodot_body()
            klist.append(body)
        self.kbodies.append(klist)
        logging.info("After chunking kbodies we have '%s' chunks of 1000 or less" % str(len(self.kbodies)))


class VOSrow_batched(Table_Formattable_Object):
    def __init__(self,timestamp_format):
        # self.BEGINNINGOFTIME = datetime.datetime.fromtimestamp(0)
        self.timestamp_format = timestamp_format
        self.anodot_timestamp = None
        self.anodot_timestamp_obj = None
        self.anodot_timestamp_epoch_str = None
        self.served_date_hour = None
        self.institution_id = None
        self.channel_id = None
        self.location_id = None
        self.display_id = None
        self.counts = None
        self.mark_served_method_id = None
        self.what = None
        self.tags = None
        self.version = None

    def build_self_from_row_list(self,rowlist):
        self.served_date_hour = rowlist[0]
        self.institution_id = rowlist[1]
        self.channel_id = rowlist[2]
        self.location_id = rowlist[3]
        self.display_id = rowlist[4]
        self.mark_served_method_id = rowlist[5]
        # for the batched we'll use the pulled row count
        self.counts = int(rowlist[6])
        # for the batched we'll use the served date hour
        self.anodot_timestamp = self.served_date_hour
        self.make_anodot_timestamp()

    def build_anodot_body(self):
        ##t0 = time.time()
        tpl = '{{"name":"ver={0}.InstitutionID={1}.ChannelID={2}.LocationID={3}.DisplayID={4}.MarkServedMethodID={5}.what={6}","timestamp": {7},"value": {8},"tags": {9} }}'
        anodot_body_vals = []
        anodot_body_vals.append(self.version)
        anodot_body_vals.append(self.institution_id)
        anodot_body_vals.append(self.channel_id)
        anodot_body_vals.append(self.location_id)
        anodot_body_vals.append(self.display_id)
        anodot_body_vals.append(self.mark_served_method_id)
        anodot_body_vals.append(self.what)
        anodot_body_vals.append(int(self.anodot_timestamp_epoch_str))
        anodot_body_vals.append(self.counts)
        anodot_body_vals.append(self.tags)

        result = tpl.format(*anodot_body_vals)
        #t = Template(jtpl_anodot_body)
        ## render and remove newlines
        #result = t.render(anodot_body_vals).replace('\n','')
        ##t1 = time.time()
        ##timing = t1 - t0
        return result
    def make_anodot_timestamp(self):
        # takes the anodot_timestamp set from wherever and turns it into a
        #  unix epoch time which can then be sorted
        try:
            if 'datetime' in str(type(self.anodot_timestamp)):
                # means self.anodot_timestamp is already an obj so we just convert it to unix epoch
                self.anodot_timestamp_obj = self.anodot_timestamp
            else:
                # means we need to convert datetime obj to string
                self.anodot_timestamp_obj = datetime.datetime.strptime(self.anodot_timestamp,self.timestamp_format)
            # either way we need the delta to make it a unix epoch timestamp
            self.anodot_timestamp_epoch_str = (self.anodot_timestamp_obj - BEGINNINGOFTIME).total_seconds()
        except Exception as whar:
            logging.debug("Exception: " + str(whar))
            logging.debug("self.anodot_timestamp = " + str(self.anodot_timestamp))
            logging.debug("type(self.anodot_timestamp) = " + str(type(self.anodot_timestamp)))
            logging.debug("self.timestamp_format = " + str(self.timestamp_format))


class Settings:
    def __init__(self):
        self.timestamp_format = None
        self.query_limit = None
        self.redis_address = None
        self.redis_port = None
        self.vert_host = None
        self.vert_port = None
        self.vert_user = None
        self.vert_pass = None
        self.vert_db = None
        self.vert_read_timeout = None
        self.vert_unicode_error = None
        self.vert_ssl = None
        self.vert_conn_dict = {}
        self.anodot_url_with_api_key = None
        self.query_lag_hours = None
        self.lagged_now = None
        self.lagged_now_str = None
        self.anodot_what = None
        self.anodot_tags = None
        self.simulate = False
        self.total_runtime = float(0)
    def make_vert_conn_dict(self):
        self.vert_conn_dict = {'vert_host': self.vert_host,
                               'vert_port': self.vert_port,
                               'vert_user': self.vert_user,
                               'vert_pass': self.vert_pass,
                               'vert_db': self.vert_db,
                               'vert_read_timeout': self.vert_read_timeout,
                               'vert_unicode_error': self.vert_unicode_error,
                               'vert_ssl': self.vert_ssl,
                              }
        return self.vert_conn_dict

def process_config(filename):
    """
    Processes the config INI file and returns a Settings
    object.
    :param filename:
    :return: settings
    """
    logging.info('------- ENTERING FUNCTION: process_config() -------')
    settings = Settings()

    try:
        cfg = ConfigParser.ConfigParser()
        cfg.read(filename)
    except Exception as arr:
        logging.critical("Exception reading config file. " + str(arr))
        sys.exit(1)
    try:
        settings.timestamp_format = cfg.get('general','timestamp_format')
        settings.redis_address = cfg.get('redis', 'redis_address')
        settings.redis_port = cfg.get('redis', 'redis_port')
        settings.vert_host = cfg.get('vertica', 'host')
        settings.vert_port = cfg.get('vertica', 'port')
        settings.vert_user = cfg.get('vertica', 'user')
        settings.vert_pass = cfg.get('vertica', 'password')
        settings.vert_db = cfg.get('vertica', 'database')
        settings.vert_read_timeout = cfg.get('vertica', 'read_timeout')
        settings.vert_unicode_error = cfg.get('vertica', 'unicode_error')
        settings.vert_ssl = cfg.get('vertica', 'ssl')
        settings.anodot_url_with_api_key = cfg.get('anodot','url_with_api_key')
        settings.anodot_what = cfg.get('anodot','what')
        settings.anodot_tags = cfg.get('anodot','tags')
        settings.anodot_ver = cfg.get('anodot','ver')
    except Exception as orr:
        logging.critical("Exception processing config options: " + str(orr))
        sys.exit(1)

    try:
        settings.query_lag_hours = int(cfg.get('general','query_lag_hours'))
    except Exception as orrp:
        logging.info("Couldn't process query_lag_hours in settings file. Ignoring. : " + str(orrp))

    '''
    # sample of how to loop through sections unknown
    for section in cfg.sections():
        if section != 'global':
            job = Job()
            job.name = cfg.get(section,'name')
            job.input_file = cfg.get(section,'input_file')
    '''
    return settings

def timedelta_from_strings(settings,start,end):
    # takes two timestamp strings and converts them to datetime objs
    # then takes a delta and returns the difference in seconds.
    t1 = datetime.datetime.strptime(start,settings.timestamp_format)
    t2 = datetime.datetime.strptime(end,settings.timestamp_format)
    logging.info("Timedelta: %s" % str(t2 - t1))
    td = t2 - t1
    return(td.total_seconds())



def log_run(settings,counter,last_run,current_run,resultscount_rows,resultscount_counts):
    r = redis.StrictRedis(host=settings.redis_address, port=settings.redis_port, db=0)
    keystring = 'run-%07d' % int(counter)
    r.hset(keystring,'from',last_run)
    r.hset(keystring,'to',current_run)
    r.hset(keystring,'resultscount_rows',resultscount_rows)
    r.hset(keystring,'resultscount_counts',resultscount_counts)
    r.hset(keystring,'simulation',str(settings.simulate))
    r.hset(keystring,'total_runtime',settings.total_runtime)
    delta = str(timedelta_from_strings(settings,last_run,current_run))
    logging.info("Delta coming back?: %s" % delta)
    r.hset(keystring,'timedelta_seconds',delta)


def store_pointer(settings):
    r = redis.StrictRedis(host=settings.redis_address, port=settings.redis_port, db=0)
    # store the current lagged now so we know where to start query from next time.
    timestamp = settings.lagged_now
    timestamp_str = timestamp.strftime(settings.timestamp_format)
    logging.info("Setting redis 'pointer' key with value '%s' " % timestamp_str)
    r.set('pointer',timestamp_str)

def retrieve_and_increment_counter(settings):
    r = redis.StrictRedis(host=settings.redis_address, port=settings.redis_port, db=0)
    try:
        r.incr('counter')
    except Exception as rrr:
        logging.critical("Exception with incr: %s" % rrr)
    try:
        rval = r.get('counter')
    except:
        rval = 0
    return rval

def retrieve_pointer(settings):
    r = redis.StrictRedis(host=settings.redis_address, port=settings.redis_port, db=0)
    timestamp_str = r.get('pointer')
    if timestamp_str is None:
        # default back to lagged_now minus 5 days
        backtime = settings.lagged_now - datetime.timedelta(days=5)
        timestamp_str = backtime.strftime(settings.timestamp_format)
    return timestamp_str

def build_anodot_queue(settings, voslist):
    logging.info("Building Anodot Redis Queue...")
    r = redis.StrictRedis(host=settings.redis_address, port=settings.redis_port, db=0)
    logging.info("Length of voslist.vosrows = %s" % str(len(voslist.vosrows)))
    local_id = r.incr("queue_space")
    queue_id = "queue:%s" %(local_id)
    batchsize = 100
    pipe = r.pipeline()
    #timings = float(0)
    #timings_count = float(0)
    for i,v in enumerate(voslist.vosrows):
        value = v.build_anodot_body()
        #timings += timing
        #timings_count += float(1)
        pipe.lpush(queue_id, value)
        if i % batchsize == 0:
            logging.info("PROGRESS: Working on %s of %s" % (str(i),str(len(voslist.vosrows))))
            pipe.execute()
            #timing_avg = float(timings / timings_count)
            #logging.info("Average timing for build_anodot_body() = %s" % timing_avg)
            #timings = float(0)
            #timings_count = float(0)
        elif ((len(voslist.vosrows) - i) < batchsize):
            logging.info("Remaining smaller than batchsize so pipe.execute() individual.")
            pipe.execute()
    return(queue_id)

def drain_anodot_queue(settings, qname):
    url = settings.anodot_url_with_api_key
    headers = {'Content-Type': 'application/json'}
    r = redis.StrictRedis(host=settings.redis_address, port=settings.redis_port, db=0)
    qlength = r.llen(qname)
    iterations = qlength / 1000
    remainder = qlength % 1000

    for j in range(iterations + 1):
        logging.info("************************ITERATION %s of %s ***********************" % (str(j),str(iterations)))
        cache = []
        body = '['
        while r.llen(qname) > 0:
            cache.append(r.rpop(qname))
            if len(cache) > 999 or r.llen(qname) == 0:
                for i,item in enumerate(cache):
                    body += item
                    if i+1 < len(cache):
                        body += ',\n'
                    else:
                        # means last item in list
                        body += '\n'
                body += ']'
                logging.info("I'm about to REST POST the following body to %s " % url)
                logging.info("timestamp for one of these is: %s" % body[0:300])
                logging.info("--------------------")
                logging.info("body with len = %s" % str(len(body)))
                logging.info("--------------------------------------------")
                if not settings.simulate:
                    response = requests.post(url, headers=headers, data=body)
                    logging.info("Queue drain batch post response code = %s" % str(response.status_code))
                else:
                    mesg = "Not actually sending Data since 'simulate' flag is set."
                    print(mesg)
                    logging.info(mesg)
                break


def determine_lagged_now(settings):
    '''
    figures out what the delayed end time is for the query
    this used to be simple but is now more complicated because Anodot
    doesn't allow us to backfill an hour bucket once it's closed
    e.g., query from 0800-1030 hrs and then the next query 1030-1400 hrs,
    in the 1000 hour will only have 30 mins of data because the data from
    the 1000 in the 1030-1400 query is thrown out.

    Returns settings object.
    '''
    logging.debug("Entering determine_lagged_now()...")
    try:
        hoursback = int(settings.query_lag_hours)
    except Exception as orp:
        logging.critical("ERROR casting query lag hours as int: " + str(orp))
        sys.exit(1)

    lagged_now = datetime.datetime.now() - datetime.timedelta(hours=hoursback)

    # always round the lagged_now down the last hour's 59th minute
    if lagged_now.minute >= 59:
        logging.info("lagged_now.minute is >= 59 ('%s')so we're not doing any rounding" % str(lagged_now))
    else:
        lagged_now_rounded = lagged_now - datetime.timedelta(minutes=(lagged_now.minute + 1))
        logging.info("lagged_now is being rounded from %s to %s" % (str(lagged_now),str(lagged_now_rounded)))
        lagged_now = lagged_now_rounded

    settings.lagged_now = lagged_now
    # now convert to string
    settings.lagged_now_str = settings.lagged_now.strftime(settings.timestamp_format)

    return settings


def main(opts):
    t0 = time.time()
    settings = process_config(opts.configfile)

    # override settings from cmd line opts if applicable
    if opts.query_lag_hours:
        settings.query_lag_hours = opts.query_lag_hours

    if opts.simulate:
        settings.simulate = True
    if opts.query_limit:
        settings.query_limit = opts.query_limit

    # first thing we do is find the lagged 'now' which we should never exceed due to data lag
    settings = determine_lagged_now(settings)


    # build a class obj to hold the vosrows returned
    vs = VOSlist()

    #store_pointer(settings)
    pointer = retrieve_pointer(settings)
    logging.info("Pointer is %s" % pointer)
    counter = retrieve_and_increment_counter(settings)
    logging.info("Counter value = %s" % str(counter))
    logging.info("Type of val counter = %s" % str(type(counter)))

    ## get results from query
    results = vert_query(settings,from_time=pointer,to_time=settings.lagged_now_str,limit=settings.query_limit)

    # build the vosrows and parse
    for i,row in enumerate(results):
        v = VOSrow_batched(settings.timestamp_format)
        v.build_self_from_row_list(row)
        v.what = settings.anodot_what
        v.tags = settings.anodot_tags
        v.version = settings.anodot_ver
        vs.vosrows.append(v)
        if i % 1000 == 0:
            logging.info("index is %s and body is '%s'" % (str(i),str(v.build_anodot_body())))

    vs.sort_by_date()
    logging.info("Verify sort order is correct: ")
    logging.info("-------------------------------")
    try:
        logging.info("First entry in VOSrows: '%s'" % str(vs.vosrows[0].build_anodot_body()))
        logging.info("Last entry in VOSrows: '%s'" % str(vs.vosrows[-1].build_anodot_body()))
    except Exception as parr:
        logging.info("Problem getting first and last entry in vosrows, Maybe no results?: " + str(parr))
    logging.info("-------------------------------")
    # grab counts before we chunk
    resultscount_rows = str(len(vs.vosrows))
    resultscount_counts = str(sum([x.counts for x in vs.vosrows]))
    queue_name = build_anodot_queue(settings,vs)

    drain_anodot_queue(settings, queue_name)
    t1 = time.time()
    settings.total_runtime = float(t1) - float(t0)
    log_run(settings,counter,pointer,settings.lagged_now_str,resultscount_rows,resultscount_counts)
    # now finally update the new pointer with the 'to' date of the last query
    store_pointer(settings)


if __name__ == '__main__':
    """
    This main section is mostly for parsing arguments to the
    script and setting up debugging
    """

    from optparse import OptionParser
    # set up an additional option group just for debugging parameters
    from optparse import OptionGroup

    usage = "%prog [--debug] [--printtostdout] [--logfile] [--version] [--help] [--samplefileoption]"
    # set up the parser object
    parser = OptionParser(usage, version='%prog ' + sversion)
    parser.add_option('-c', '--configfile',
                      type='string',
                      metavar='FILE',
                      help="REQUIRED: config ini file. See sample. (default = 'runningconfig.ini'",
                      default='runningconfig.ini')
    parser.add_option('--query_lag_hours',
                      type='string',
                      help="Number of hours back to adjust the query time window. (Default=96)",
                      default='96')
    parser.add_option('--query_limit',
                      type='string',
                      help="Number of lines to limit results in Vertica query. (Default='ALL')",
                      default='ALL')
    parser.add_option('-s','--simulate',
                      action='store_true',
                      help=("Boolean flag. If this option is present then no REST calls will be made" +
                            ", only testing. (Default=False)"),
                      default=False)
    parser_debug = OptionGroup(parser, 'Debug Options')
    parser_debug.add_option('-d', '--debug', type='string',
                            help=('Available levels are CRITICAL (3), ERROR (2), '
                                  'WARNING (1), INFO (0), DEBUG (-1)'),
                            default='CRITICAL')
    parser_debug.add_option('-p', '--printtostdout', action='store_true',
                            default=False, help='Print all log messages to stdout')
    parser_debug.add_option('-l', '--logfile', type='string', metavar='FILE',
                            help=('Desired filename of log file output. Default '
                                  'is "' + defaultlogfilename + '"'),
                            default=defaultlogfilename)
    # officially adds the debugging option group
    parser.add_option_group(parser_debug)
    options, args = parser.parse_args()  # here's where the options get parsed

    try:  # now try and get the debugging options
        loglevel = getattr(logging, options.debug)
    except AttributeError:  # set the log level
        loglevel = {3: logging.CRITICAL,
                    2: logging.ERROR,
                    1: logging.WARNING,
                    0: logging.INFO,
                    -1: logging.DEBUG,
                    }[int(options.debug)]

    try:
        open(options.logfile, 'w')  # try and open the default log file
    except:
        print("Unable to open log file '%s' for writing." % options.logfile)
        logging.critical(
            "Unable to open log file '%s' for writing." % options.logfile)

    setuplogging(loglevel, options.printtostdout, options.logfile)
    try:
        if options.configfile == 'runningconfig.ini':
            # try to get the real directory of the running script
            currdir = os.path.dirname(os.path.realpath(__file__))
            options.configfile = currdir + "/" + "runningconfig.ini"
    except Exception as arrr:
        msg = "Exception processing config file location: " + str(arrr)
        logging.error(msg)
        print(msg)
        sys.exit(1)

    main(options)
