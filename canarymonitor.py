#!/usr/bin/env python3
import argparse
import logging
import logging.config
import multiprocessing
import traceback
import threading
import time
import re
import sys
import json
import copy
import os
import pathlib
import signal
import hashlib
import tempfile
from datetime import datetime, timezone
import random
import platform
from queue import Queue
import utils
import dash
import hls


# Initialize widget positions when rendering dashboard
def initpositions():
  dashboardconfig['x'] = 0
  dashboardconfig['y'] = 0
  dashboardconfig['ymax'] = 0
  return [0, 0]


# Calculate new widget positions when rendering dashboard
def getpositions(widgettype:str):
  x = 0
  y = dashboardconfig['ymax']
  if widgettype == 'header':
    dashboardconfig['x'] = dashboardconfig['maxwidth']
    dashboardconfig['y'] = dashboardconfig['ymax']
    dashboardconfig['ymax'] = dashboardconfig['ymax'] + dashboardconfig['header']['height']
  elif widgettype == 'metric':
    # New row
    if dashboardconfig['x'] + dashboardconfig['metric']['width'] > dashboardconfig['maxwidth']:
      dashboardconfig['x'] = dashboardconfig['metric']['width']
      dashboardconfig['y'] = dashboardconfig['ymax']
      dashboardconfig['ymax'] = dashboardconfig['ymax'] + dashboardconfig['metric']['height']
    # Same row
    else:
      x = dashboardconfig['x']
      y = dashboardconfig['y']
      dashboardconfig['x'] = dashboardconfig['x'] + dashboardconfig['metric']['width']
  elif widgettype == 'loginsights':
    dashboardconfig['x'] = dashboardconfig['maxwidth']
    dashboardconfig['y'] = dashboardconfig['ymax']
    dashboardconfig['ymax'] = dashboardconfig['ymax'] + dashboardconfig['loginsights']['height']
  return [x, y]


# Update default endpointconfig with user provided settings
def endpointconfigupdate(endpointconfig, userconfig):
  for k, v in userconfig.items():
    if k in endpointconfig and isinstance(endpointconfig[k], dict) and isinstance(v, dict):
      endpointconfigupdate(endpointconfig[k], v)
    else:
      endpointconfig[k] = v


# Read endpoint information from CSV file content into endpoints dictionary
def readcsvfile(filename, content, endpoints:dict):
  try:
    lines = content.splitlines()
    for index, line in enumerate(lines, 1):
      if line.strip() and not line.startswith('#'):
        splitline = re.split(f',', line)
        if len(splitline) >= 7:
          validentry = True
          for value in splitline:
            if not value.strip():
              mainlogger.warning(f"Invalid empty value in CSV file {filename} on line {index}: {line.strip()}")
              validentry = False
              break
          if validentry:
            endpointconfig = copy.deepcopy(defaultendpointconfig)
            identifier = (splitline[0].strip().lower(), splitline[1].strip().lower(), splitline[2].strip(), splitline[3].strip(), splitline[4].strip().lower()) # endpoint type, technology, workload name, endpoint name, origin name
            # Get endpoint configuration
            if splitline[5].strip() not in mainconfig['hashtable']['config'].keys():
              gethash('config', splitline[5].strip(), True)
            try:
              if pathlib.Path(splitline[5].strip()).is_file():
                with open(splitline[5].strip(), 'r') as file:
                  endpointconfigupdate(endpointconfig, json.load(file))
                  # endpointconfig.update(json.load(file))
              else:
                mainlogger.warning(f"Failed inputting endpoint on line {index} from {filename} because config file {splitline[5].strip()} does not exist")
                continue
            except json.decoder.JSONDecodeError as e:
              mainlogger.error(f"Error parsing config file {splitline[5].strip()}. Will use default settings. Exception: {e} Traceback: {traceback.format_exc()}")
            # Get manifest url
            endpointconfig['manifesturl'] = splitline[6].strip()
            # Get tracking url
            if len(splitline) >= 8:
              endpointconfig['trackingurl'] = splitline[7].strip()
            # Update endpoints with origin endpoint information
            if identifier in endpoints.keys():
              mainlogger.warning(f"Duplicate origin endpoint in CSV file {filename} on line {index}: {line.strip()}")
              continue
            else:
              endpoints[identifier] = endpointconfig.copy()
        else:
          mainlogger.warning(f"CSV file {filename} has invalid syntax on line {index}: {line.strip()}")
          continue
  except Exception as e:
    mainlogger.error(f"Error reading CSV content. Exception: {e} Traceback: {traceback.format_exc()}")


# Get endpoint information from CSV files in input folder
def getendpointsinfo():
  endpoints = {}
  # Clear hash tables because will build new ones
  mainconfig['hashtable']['config'].clear()
  mainconfig['hashtable']['input'].clear()
  # Collect origin endpoints information
  mainlogger.info(f"Collecting origin endpoint information")
  try:
    for csvfile in localinputsfolderpath.rglob('*.csv'):
      gethash('input', str(csvfile), True)
      with open(csvfile, 'r') as file:
        readcsvfile(str(csvfile), file.read(), endpoints)
  except Exception as e:
    mainlogger.error(f"Failed to get origin endpoints information. Exception: {e} Traceback: {traceback.format_exc()}")
  return endpoints


# Get file hash
def gethash(category:str, filename:str, update:bool):
  if pathlib.Path(filename).is_file():
    with open(filename, 'rb') as file:
      hashstring = hashlib.md5(file.read()).hexdigest()
      if update:
        mainconfig['hashtable'][category][filename] = hashstring
      return hashstring
  else:
    return ''


# Find new, deleted, updated input files and modified config files
def checkforinputorconfigchanges():
  changes = []
  # New inputs
  for csvfile in localinputsfolderpath.rglob('*.csv'):
    if str(csvfile) not in mainconfig['hashtable']['input'].keys():
      changes.append({'change': 'new', 'category': 'input', 'filename': str(csvfile)})
  # Deleted or updated input
  for inputfile in mainconfig['hashtable']['input'].keys():
    if pathlib.Path(inputfile).is_file():
      if mainconfig['hashtable']['input'][inputfile] != gethash('input', inputfile, False):
        changes.append({'change': 'updated', 'category': 'input', 'filename': inputfile})
    else:
      changes.append({'change': 'deleted', 'category': 'input', 'filename': inputfile})
  # Modified config
  for configfile in mainconfig['hashtable']['config'].keys():
    if pathlib.Path(configfile).is_file():
      if mainconfig['hashtable']['config'][configfile] != gethash('config', configfile, False):
        changes.append({'change': 'updated', 'category': 'config', 'filename': configfile})
  return changes


# Save report
def savereport(logger, monitorinfo, final:bool):
  try:
    monitorinfo['reporting']['filepath'].parent.mkdir(parents=True, exist_ok=True)
    monitorinfo['reporting']['report'][f"{monitorinfo['state']['startdatetime']}"] = {
      'adbreaks': monitorinfo['reporting']['adbreaks'],
      'endtime': f"{datetime.now(timezone.utc)}" if final else None
    }
    with open(monitorinfo['reporting']['filepath'], 'w') as file:
      json.dump(monitorinfo['reporting']['report'], file, indent=2)
      logger.debug(f"Saved report to {monitorinfo['reporting']['filepath']}")
    if monitorinfo['config']['endpointconfig']['reports']['save']['s3'] and args.bucket:
      s3.put_object(Bucket=args.bucket, Key=str(monitorinfo['reporting']['filepath']), Body=json.dumps(monitorinfo['reporting']['report'], indent=2), ContentType='application/json')
      logger.debug(f"Saved report to s3://{args.bucket}/{str(monitorinfo['reporting']['filepath'])}")
  except Exception as e:
    logger.error(f"Error saving report. Exception: {str(e)} Traceback: {traceback.format_exc()}")


# Publish metrics to CW
def publishmetrics(logger, monitorinfo):
  try:
    metricstopublish = []
    while not monitorinfo['metrics']['queue'].empty():
      metric = monitorinfo['metrics']['queue'].get_nowait()
      metric['Dimensions'].extend([{'Name': 'Type', 'Value': monitorinfo['config']['type']}, {'Name': 'Technology', 'Value': monitorinfo['config']['technology']}, {'Name': 'Workload', 'Value': monitorinfo['config']['workload']}, {'Name': 'Endpoint', 'Value': monitorinfo['config']['endpoint']}, {'Name': 'Origin', 'Value': monitorinfo['config']['origin']}])
      metricstopublish.append(metric)
    if metricstopublish:
      cloudwatch.put_metric_data(Namespace='CanaryMonitor', MetricData=metricstopublish)
      logger.debug(f"Published {len(metricstopublish)} metrics to CloudWatch")
  except Exception as e:
    logger.error(f"Error publishing metrics. Exception: {str(e)}")


# Update worker settings
def updateendpointconfig(logger, endpointinfofile:str, endpointidentifier:tuple, endpointconfig:dict):
  try:
    with open(endpointinfofile, 'r') as f:
      jsonload = json.load(f)
      if str(endpointidentifier) in jsonload.keys():
        logger.info(f"Loaded new endpoint configuration")
        endpointconfig.update(jsonload[str(endpointidentifier)])
    if endpointconfig['loglevel'] in utils.loglevels.keys():
      logger.setLevel(utils.loglevels[endpointconfig['loglevel']])
  except Exception as e:
    logger.error(f"Error updating worker endpoint configuration. Exception: {str(e)} Traceback: {traceback.format_exc()}")


# Monitor endpoint
def monitor(endpointidentifier:tuple, endpointconfig:dict, stopflag, changeflag, endpointinfofile, sharedwithmain, loggingconfig:dict, args):
  monitorinfo = {
    'args': args,
    'config': {
      'type': endpointidentifier[0],
      'technology': endpointidentifier[1],
      'workload': endpointidentifier[2],
      'endpoint': endpointidentifier[3],
      'origin': endpointidentifier[4],
      'endpointconfig': endpointconfig,
      'logging': loggingconfig,
      'sharedwithmain': sharedwithmain
    },
    'state': {
      'starttimeperf': time.perf_counter(),
      'startdatetime': datetime.now(timezone.utc),
      'threads': {},
      'lock': threading.Lock(),
      'stop': threading.Event(),
      'restart': (False, '')
    },
    'metrics': {
      'lastpublishtime': time.perf_counter() - random.uniform(0,15),
      'publishinterval': 15,
      'queue': Queue(),
    },
    'reporting': {
      'filepath': pathlib.Path('archive', endpointidentifier[0], endpointidentifier[2], endpointidentifier[4], endpointidentifier[3], endpointidentifier[1], 'report.json'),
      'report': {},
      'lastsavetime': time.perf_counter() - random.uniform(0,15),
      'adbreaks': {},
      'periods': {},
      'validations': {
        'failures': set()
      }
    }
  }
  if monitorinfo['config']['technology'] == 'dash':
    utils.initializemonitor(monitorinfo, 'dash')
  elif monitorinfo['config']['technology'] == 'hls':
    monitorinfo['manifest'] = {
      'multi': {
        'lasthash': ''
      }
    }
  # Configure logging
  logging.config.dictConfig(loggingconfig)
  monitorlogger = logging.getLogger('monitor')
  logger = logging.LoggerAdapter(monitorlogger, {'type': monitorinfo['config']['type'], 'origin': monitorinfo['config']['origin'], 'workload': monitorinfo['config']['workload'], 'endpoint': monitorinfo['config']['endpoint'], 'technology': monitorinfo['config']['technology'], 'rendition': 'multi'})
  if endpointconfig['loglevel'] in utils.loglevels.keys():
    logger.setLevel(utils.loglevels[endpointconfig['loglevel']])
  logger.info(f"Started monitoring origin endpoint {endpointconfig['manifesturl']}")
  # Initialize reporting
  if monitorinfo['reporting']['filepath'].exists():
    with open(monitorinfo['reporting']['filepath'], 'r') as file:
      logger.debug(f"Loaded report from {monitorinfo['reporting']['filepath']}")
      monitorinfo['reporting']['report'] = json.load(file)
  # Start tracking
  try:
    if 'trackingurl' in endpointconfig.keys():
      monitorinfo['state']['threads']['tracking'] = threading.Thread(target=utils.tracking, args=(logger, monitorinfo, endpointconfig))
      monitorinfo['state']['threads']['tracking'].start()
  except Exception as e:
    logger.error(f"Failed to start tracking. Exception: {str(e)} Traceback: {traceback.format_exc()}")
  # Main loop
  try:
    while not stopflag.is_set():
      requesttime = time.perf_counter()
      # Check for endpoint config changes
      if changeflag.is_set():
        updateendpointconfig(logger, endpointinfofile, endpointidentifier, endpointconfig)
        changeflag.clear()
      # Clear state
      if monitorinfo['config']['technology'] == 'dash':
        monitorinfo['manifest']['primary']['foundlastsegment'] = False
        monitorinfo['manifest']['primary']['new']['segments'].clear()
        monitorinfo['manifest']['primary']['new']['duration'] = 0
        monitorinfo['manifest']['primary']['consistency']['current']['periods'].clear()
      # Request manifest
      logger.debug(f"Requesting manifest")
      response = utils.request(logger, 'GET', endpointconfig['manifesturl'], 'manifest', 'multi', monitorinfo)
      # Save manifest response
      if endpointconfig['manifests']['save']['local']:
        utils.saveresponse(logger, response, monitorinfo, 'manifests', "", False)
      if monitorinfo['config']['type'] == 'live':
        # If DASH
        if monitorinfo['config']['technology'] == 'dash':
          monitorinfo['manifest']['primary']['manifestrequesttime'] = datetime.now(timezone.utc)
          if endpointconfig['validations']['perform']:
            if response:
              # Perform validations
              utils.checkresponseheaders(logger, monitorinfo, response)
              manifestlastupdated = utils.getmanifestlastupdated(response)
              if manifestlastupdated != monitorinfo['manifest']['primary']['headers']['manifestlastupdated'] or manifestlastupdated == 0:
                dash.monitor(logger, monitorinfo, utils.decoderesponse(response, False))
              monitorinfo['manifest']['primary']['headers']['manifestlastupdated'] = manifestlastupdated
            # Update new duration
            monitorinfo['manifest']['primary']['buffer']['window'][requesttime] = monitorinfo['manifest']['primary']['new']['duration']
            # Check for staleness
            if requesttime - monitorinfo['state']['starttimeperf'] > max(monitorinfo['manifest']['primary']['buffer']['size'], monitorinfo['config']['endpointconfig']['manifests']['frequency']):
              utils.checkforstaleness(logger, monitorinfo, requesttime, 'primary', 'multi')
        # If HLS
        elif monitorinfo['config']['technology'] == 'hls':
          if response:
            # Start threads at first, then restart threads if multivariant manifest has changed
            manifesthash = hashlib.md5(utils.decoderesponse(response, False)).hexdigest()
            if manifesthash != monitorinfo['manifest']['multi']['lasthash']:
              monitorinfo['state']['restart'] = (True, 'Multivariant manifest has changed') if monitorinfo['manifest']['multi']['lasthash'] else (True, '')
            monitorinfo['manifest']['multi']['lasthash'] = manifesthash
            # Check if need to restart monitoring
            if monitorinfo['state']['restart'][0]:
              hls.restartthreads(logger, monitorinfo, utils.decoderesponse(response, True))
      # Publish metrics to CW
      if endpointconfig['cwmetrics'] and not monitorinfo['args'].no_aws:
        if requesttime - monitorinfo['metrics']['lastpublishtime'] > monitorinfo['metrics']['publishinterval']:
          publishmetrics(logger, monitorinfo)
          monitorinfo['metrics']['lastpublishtime'] = time.perf_counter()
      # Save report
      if endpointconfig['validations']['perform']:
        if requesttime - monitorinfo['reporting']['lastsavetime'] > monitorinfo['config']['endpointconfig']['reports']['frequency']:
          savereport(logger, monitorinfo, False)
          monitorinfo['reporting']['lastsavetime'] = requesttime
      # Wait
      utils.wait(logger, requesttime, endpointconfig['manifests']['frequency'])
  except KeyboardInterrupt:
    logger.info(f"Received signal to stop, waiting for all workers to stop")
  except Exception as e:
    logger.error(f"Encountered error while monitoring. Exception: {str(e)} Traceback: {traceback.format_exc()}")
  finally:
    # Stop all threads
    monitorinfo['state']['stop'].set()
    for thread in monitorinfo['state']['threads'].keys():
      monitorinfo['state']['threads'][thread].join()
    # Save report
    savereport(logger, monitorinfo, True)
    logger.info(f"Stopped monitoring")


# Find what endpoint configuration changes were made to know if worked needs to be restarted
def needtorestartworker(old:dict, new:dict):
  forbiddenpaths = {"root['manifests']['hlsrenditions']", "root['manifesturl']", "root['trackingurl']"}
  diff = DeepDiff(old, new)
  if 'values_changed' not in diff:
    return True
  else:
    if any(path in forbiddenpaths for path in diff['values_changed']):
      return True
    else:
      return False


# Stop or start new monitor workers after any input or config change
def updateworkers():
  newendpoints = getendpointsinfo()
  saveendpointinfotofile(newendpoints)
  stoppedworkers = []
  workerstostart = []
  # Stop workers for removed origin endpoints
  for identifier in mainconfig['workers'].keys():
    if identifier not in newendpoints.keys():
      mainconfig['stopflags'][identifier].set()
      if (identifier[0], identifier[2], identifier[4]) not in mainconfig['changedworkloads']:
        mainconfig['changedworkloads'].append((identifier[0], identifier[2], identifier[4]))
      stoppedworkers.append(identifier)
  for identifier, endpointconfig in newendpoints.items():
    # Start workers for new endpoints
    if identifier not in mainconfig['workers']:
      startmonitorworker(identifier, endpointconfig)
      if (identifier[0], identifier[2], identifier[4]) not in mainconfig['changedworkloads']:
        mainconfig['changedworkloads'].append((identifier[0], identifier[2], identifier[4]))
    # Restart workers for modified endpoints
    elif endpointconfig != mainconfig['endpoints'].get(identifier):
      if needtorestartworker(endpointconfig, mainconfig['endpoints'].get(identifier)):
        mainconfig['stopflags'][identifier].set()
        stoppedworkers.append(identifier)
        workerstostart.append(identifier)
      else:
        mainconfig['changeflags'][identifier].set()
      if (identifier[0], identifier[2], identifier[4]) not in mainconfig['changedworkloads']:
        mainconfig['changedworkloads'].append((identifier[0], identifier[2], identifier[4]))
  # Clean up stopped workers
  for identifier in stoppedworkers:
    if mainconfig['workers'][identifier].is_alive():
      mainconfig['workers'][identifier].join()
    del mainconfig['workers'][identifier]
    del mainconfig['stopflags'][identifier]
  # Start workers of modified origin endpoints
  for identifier in workerstostart:
    startmonitorworker(identifier, newendpoints[identifier])
  return newendpoints


# Start monitor process
def startmonitorworker(identifier:tuple, endpointconfig:dict):
  if args.threads:
    mainconfig['stopflags'][identifier] = threading.Event()
    mainconfig['changeflags'][identifier] = threading.Event()
    mainconfig['workers'][identifier] = threading.Thread(target=monitor, args=(identifier, endpointconfig, mainconfig['stopflags'][identifier], mainconfig['changeflags'][identifier], endpointinfofile.name, sharedwithmain, loggingconfig, args))
  else:
    mainconfig['stopflags'][identifier] = multiprocessing.Event()
    mainconfig['changeflags'][identifier] = multiprocessing.Event()
    mainconfig['workers'][identifier] = multiprocessing.Process(target=monitor, args=(identifier, endpointconfig, mainconfig['stopflags'][identifier], mainconfig['changeflags'][identifier], endpointinfofile.name, sharedwithmain, loggingconfig, args))
  mainconfig['workers'][identifier].start()
  # Wait 50 milliseconds to avoid spike in new processes
  time.sleep(0.05)


# Render and save CW dashboards
def renderandsavedashboard(renderinfo:dict):
  try:
    if renderinfo['type'] in ['live', 'vod']:
      template = env.get_template(renderinfo['type'])
      render = template.render(renderinfo=renderinfo, dashboardconfig=dashboardconfig)
      # Save render to file
      with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write(render)
        mainlogger.info(f"Saved dashboard to {f.name}")
      renderjson = json.loads(render)
      # Save dashboard to CloudWatch
      try:
        dashboardname = f"{renderinfo['workload'].upper()}-{renderinfo['origin'].upper() if renderinfo['origin'] in ['emp', 'emt'] else renderinfo['origin'].capitalize()}-Canary-Monitor"
        response = cloudwatch.put_dashboard(DashboardName=dashboardname, DashboardBody=render)
        if response:
          mainlogger.info(f"Saved dashboard '{dashboardname}' to CloudWatch")
          if 'DashboardValidationMessages' in response.keys() and len(response['DashboardValidationMessages']) > 0:
            mainlogger.warning(f"Dashboard validation warnings: {response['DashboardValidationMessages']}")
      except Exception as e:
        mainlogger.error(f"Faled to save dashboard to CloudWatch. Exception: {e} Traceback: {traceback.format_exc()}")
        raise
  except Exception as e:
    mainlogger.error(f"Error saving dashboard for {renderinfo['workload']} workload, {renderinfo['origin']} origin. Exception: {e} Traceback: {traceback.format_exc()}")


# Create CW dashboards
def createdashboards():
  organizedendpoints = {}
  try:
    # Give HLS monitor time to collect information about renditions
    if any(endpoint[1] == 'hls' for endpoint in mainconfig['endpoints']):
      mainlogger.info(f"Waiting to collect rendition information about new HLS endpoints")
      time.sleep(10)
    # Prepare organized dictionary of endpoints for render
    for endpoint, config in mainconfig['endpoints'].items():
      if config['cwmetrics']:
        if (endpoint[0], endpoint[2], endpoint[4]) not in organizedendpoints.keys():
          organizedendpoints[(endpoint[0], endpoint[2], endpoint[4])] = {
            'type': endpoint[0],
            'workload': endpoint[2],
            'origin': endpoint[4],
            'segmentrequests': False,
            'trackingrequests': False,
            'endpoints': []
          }
        endpointinfo = {
          'technology': endpoint[1],
          'endpoint': endpoint[3],
          'segmentrequests': False,
          'trackingrequests': False,
          'config': config
        }
        # Include renditions
        if endpointinfo['technology'] == 'hls':
          endpointinfo['renditions'] = sharedwithmain.get(endpoint, {}).get('hlsrenditions', [])
          if not endpointinfo['renditions']:
            mainlogger.warning(f"Failed to collect rendition information for HLS endpoint {endpoint}")
        # if config['segments']['get'] or config['segments']['head']:
        #   organizedendpoints[(endpoint[0], endpoint[2], endpoint[4])]['segmentrequests'] = True
        #   endpointinfo['segmentrequests'] = True
        if config['tracking']['get']:
          organizedendpoints[(endpoint[0], endpoint[2], endpoint[4])]['trackingrequests'] = True
          endpointinfo['trackingrequests'] = True
        # Append endpointinfo to list of endpoints
        organizedendpoints[(endpoint[0], endpoint[2], endpoint[4])]['endpoints'].append(endpointinfo.copy())
    for item, value in organizedendpoints.items():
      if item in mainconfig['changedworkloads']:
        renderandsavedashboard(value)
  except Exception as e:
    mainlogger.error(f"Failed to create dashboards. Exception: {e} Traceback: {traceback.format_exc()}")


# Save endpoint info to a temp file for workers to pick up changes
def saveendpointinfotofile(endpointinfo:dict):
  tupletostring = {}
  try:
    # Replace tuples with strings so that can save as JSON
    for key, value in endpointinfo.items():
      tupletostring[str(key)] = value
    with open(endpointinfofile.name, 'w') as f:
      json.dump(tupletostring, f)
      f.flush()
  except Exception as e:
    mainlogger.error(f"Failed to save endpoint info to file. Exception: {e} Traceback: {traceback.format_exc()}")
  else:
    mainlogger.info(f"Saved endpoint information to {endpointinfofile.name}")


# Handle signals
def signalhandler(signal, frame):
  raise KeyboardInterrupt()


# Main
if __name__ == '__main__':
  # Read arguments
  parser = argparse.ArgumentParser()
  parser.add_argument('-t', '--threads', action='store_true', help='use threads instead of processes')
  parser.add_argument('-na', '--no-aws', action='store_true', help='do not use AWS')
  parser.add_argument('-r', '--region', type=str, default='us-west-2', help='AWS region to use, default: us-west-2')
  parser.add_argument('-b', '--bucket', type=str, help='AWS S3 bucket name for archive')
  parser.add_argument('-l', '--lambda-function', type=str, help='AWS Lambda arn for AWS CloudWatch dashboard reporting')
  args = parser.parse_args()

  # Configure logging
  locallogsfolderpath = pathlib.Path('logs')
  locallogsfolderpath.mkdir(exist_ok=True)
  loggingconfigpath = pathlib.Path(os.path.dirname(os.path.realpath(__file__)), 'loggingconfig.json')
  with loggingconfigpath.open() as loggingconfigfile:
    loggingconfig = json.load(loggingconfigfile)
  logging.config.dictConfig(loggingconfig)
  mainlogger = logging.getLogger('service')
  mainlogger.info(f"Started")

  # Enable threading if platform is Windows
  if platform.system() == 'Windows':
    mainlogger.info(f"Will use threads because system is Windows")
    args.threads = True

  # Import external libraries
  try:
    from deepdiff import DeepDiff
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from lxml import etree as et
  except Exception as e:
    mainlogger.error(f"Exception: {e} Trackeback: {traceback.format_exc()}")
    sys.exit(1)

  # Configure AWS resources
  if not args.no_aws:
    try:
      import boto3
      from botocore.config import Config
      from botocore.exceptions import BotoCoreError, ClientError
      config = Config(
        region_name=args.region,
        read_timeout=3,
        connect_timeout=3,
        retries={
          'max_attempts': 1
        }
      )
      # Get account id
      # awsaccountid = boto3.client('sts').get_caller_identity().get('Account')
      # CloudWatch
      cloudwatch = boto3.client('cloudwatch', config=config)
      mainlogger.info(f"Configured CloudWatch client in {args.region}")
      # S3
      if args.bucket:
        s3 = boto3.client('s3', config=config)
        mainlogger.info(f"Configured S3 client in {args.region}")
        try:
          s3.head_bucket(Bucket=args.bucket)
        except ClientError as e:
          if e.response['Error']['Code'] == '404':
            mainlogger.error(f"Error finding S3 bucket. Exception: {e}")
          elif e.response['Error']['Code'] == '403':
            mainlogger.error(f"Error accessing S3 bucket. Exception: {e}")
          args.bucket = None
    except Exception as e:
      mainlogger.error(f"Error initializing AWS resources. Exception: {e} Trackeback: {traceback.format_exc()}")
      args.no_aws = True


  # Prepare local storage
  localinputsfolderpath = pathlib.Path('origins')
  localinputsfolderpath.mkdir(exist_ok=True)
  localoutputsfolderpath = pathlib.Path('archive')
  localoutputsfolderpath.mkdir(exist_ok=True)

  # Handle signals
  signal.signal(signal.SIGINT, signalhandler)  # 2
  signal.signal(signal.SIGTERM, signalhandler)  # 15

  # Set worker type and prepare data sharing
  if args.threads:
    sharedwithmain = {}
  else:
    multiprocessing.set_start_method('fork')
    manager = multiprocessing.Manager()
    sharedwithmain = manager.dict()

  # Data
  mainconfig = {
    'stopflags': {},
    'changeflags': {},
    'workers': {},
    'hashtable': {
      'input': {},
      'config': {}
    },
    'changedworkloads': []
  }
  dashboardconfig = {
    'maxwidth': 24,
    'header': {
      'height': 1
    },
    'metric': {
      'height': 4,
      'width': 4
    },
    'loginsights': {
      'height': 8
    },
    'report': {
      'height': 8,
      'lambda': args.lambda_function if args.lambda_function else None
    },
    'region': args.region
  }

  # Load default endpoint config
  if pathlib.Path('configs', 'default.json').is_file():
    with open(pathlib.Path('configs', 'default.json'), 'r') as file:
      defaultendpointconfig = json.load(file)
  else:
    mainlogger.warning(f"Did not find default endpoint config file")
    sys.exit(1)

  # Temp file for storing endpoint information
  endpointinfofile = tempfile.NamedTemporaryFile(mode='w+', delete=False)
  endpointinfofile.close()

  # Collect information about endpoints
  mainconfig['endpoints'] = getendpointsinfo()
  saveendpointinfotofile(mainconfig['endpoints'])

  # Prepare dasbhoard templates
  env = Environment(loader=FileSystemLoader('templates'), autoescape=select_autoescape(), trim_blocks=True, lstrip_blocks=True)
  env.globals['getpositions'] = getpositions
  env.globals['initpositions'] = initpositions

  # Start monitor workers
  for key, value in mainconfig['endpoints'].items():
    startmonitorworker(key, value)

  # Main loop
  try:
    while True:
      # Check for input and config changes
      inputorconfigchanges = checkforinputorconfigchanges()
      if inputorconfigchanges:
        for item in inputorconfigchanges:
          mainlogger.info(f"Input or config has changed, {item['change']}: {item['filename']}")
        mainconfig['endpoints'] = updateworkers()
        mainlogger.info(f"Now monitoring {len(mainconfig['workers'])} endpoints")
        if len(mainconfig['changedworkloads']) > 0 and not args.no_aws:
          createdashboards()
        mainconfig['changedworkloads'].clear()
      time.sleep(5)
  except KeyboardInterrupt:
    mainlogger.info(f"Received signal to stop, waiting for all workers to stop")
  except Exception as e:
    mainlogger.error(f"Error. Exception: {e} Traceback: {traceback.format_exc()}")
  finally:
    for flag in mainconfig['stopflags'].values():
      flag.set() # noqa
    for worker in mainconfig['workers'].values():
      worker.join() # noqa



