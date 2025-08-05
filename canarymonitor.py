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
import os
import pathlib
import signal
import hashlib
import tempfile
from datetime import datetime, timezone
import random
import platform
import shutil
from urllib.parse import urljoin
from queue import Queue
import utils
import dash


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
            endpointconfig = {}
            identifier = (splitline[0].strip().lower(), splitline[1].strip().lower(), splitline[2].strip(), splitline[3].strip(), splitline[4].strip().lower()) # endpoint type, technology, workload name, endpoint name, origin name
            # Get endpoint configuration
            endpointconfig.update(defaultendpointconfig)
            if splitline[5].strip() not in mainconfig['hashtable']['config'].keys():
              gethash('config', splitline[5].strip(), True)
            try:
              if pathlib.Path(splitline[5].strip()).is_file():
                with open(splitline[5].strip(), 'r') as file:
                  endpointconfig.update(json.load(file))
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


# Update status report
def savereport(logger, monitorinfo, final:bool):
  try:
    report = {
      'adbreaks': monitorinfo['manifest']['primary']['adbreaks']
    }
    if monitorinfo['config']['technology'] == 'dash':
      report.update({
        'periods': monitorinfo['manifest']['primary']['periods']
      })
    reportfilepath = pathlib.Path('archive', monitorinfo['config']['type'], monitorinfo['config']['workload'], monitorinfo['config']['origin'], monitorinfo['config']['endpoint'], monitorinfo['config']['technology'], 'reports', f"{monitorinfo['state']['startdatetime'].strftime('%Y_%m_%d_%H_%M_%S_%f')}.json")
    if final:
      reportfilepathfinal = pathlib.Path('archive', monitorinfo['config']['type'], monitorinfo['config']['workload'], monitorinfo['config']['origin'], monitorinfo['config']['endpoint'], monitorinfo['config']['technology'], 'reports', f"{monitorinfo['state']['startdatetime'].strftime('%Y_%m_%d_%H_%M_%S_%f')}_to_{datetime.now(timezone.utc).strftime('%Y_%m_%d_%H_%M_%S_%f')}.json")
      if reportfilepath.exists():
        shutil.move(reportfilepath, reportfilepathfinal)
      reportfilepath = reportfilepathfinal
    reportfilepath.parent.mkdir(parents=True, exist_ok=True)
    with reportfilepath.open('w') as file:
      file.write(json.dumps(report, indent=2))
      logger.debug(f"Saved report to {reportfilepath}")
  except Exception as e:
    logger.error(f"Error updating worker endpoint configuration. Exception: {str(e)} Traceback: {traceback.format_exc()}")


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


# Get HLS rendition info from multivariant manifest
def getrenditions(logger, responsedata: str, monitorinfo: dict):
  renditions = {'v': [], 'a': [], 's': []}
  vuris = []
  auris = []
  suris = []
  try:
    # Get all renditions for manifest
    multiplaylist = m3u8.loads(responsedata)
    for playlist in multiplaylist.playlists:
      if playlist.uri and playlist.uri not in vuris:
        vuris.append(playlist.uri)
        renditions['v'].append({'id': f"v{len(vuris)}", 'monitor': False, 'bandwidth': playlist.stream_info.bandwidth, 'url': urljoin(monitorinfo['manifest']['url'], playlist.uri)})
    for media in multiplaylist.media:
      if media.type == 'AUDIO':
        if media.uri and media.uri not in auris:
          auris.append(media.uri)
          renditions['a'].append({'id': f"a{len(auris)}", 'monitor': False, 'groupid': media.group_id, 'url': urljoin(monitorinfo['manifest']['url'], media.uri)})
      elif media.type == 'SUBTITLES':
        if media.uri and media.uri not in suris:
          suris.append(media.uri)
          renditions['s'].append({'id': f"s{len(suris)}", 'monitor': False, 'groupid': media.group_id, 'url': urljoin(monitorinfo['manifest']['url'], media.uri)})
    # Mark renditions for monitoring
    for renditionstring in args.renditions:
      foundrendition = False
      match = re.search(r'^([vas])(\d+|\*)$', renditionstring)
      if match:
        for rendition in renditions[match.group(1)]:
          if renditionstring == rendition['id'] or match.group(2) == '*':
            rendition['monitor'] = True
            foundrendition = True
      if not foundrendition:
        logger.warning(f"Failed finding user selected rendition {renditionstring}")
  except Exception as e:
    logger.error(f"Error parsing manifest. Exception: {str(e)}")
  logger.debug(f"Found these renditions: {renditions}")
  return renditions


# HLS rendition monitor
def hls(monitorinfo: dict, rendition: dict, loggingconfig: dict):
  logging.config.dictConfig(loggingconfig)
  monitorlogger = logging.getLogger('monitor')
  logger = logging.LoggerAdapter(monitorlogger, {'type': monitorinfo['type'], 'origin': monitorinfo['origin'], 'endpoint': monitorinfo['endpoint'], 'technology': monitorinfo['technology'], 'rendition': rendition['id']})
  rendition.update({'status': 'initializing', 'lastmanifesthash': ''})
  metricstopublish = {}
  logger.info(f"Started monitoring")
  try:
    while not monitorinfo['stop'].is_set():
      starttime = time.perf_counter()
      logger.debug(f"Requesting manifest")
      # response, responsehash = request(logger, 'GET', rendition['url'], 'manifest', metricstopublish)
      utils.wait(logger, starttime, args.liveinterval)
  except KeyboardInterrupt:
    pass
  finally:
    logger.info(f"Stopped monitoring")


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


# Get manifest last updated header
def getmanifestlastupdated(response):
  manifestlastupdated = 0
  if 'X-MediaPackage-Manifest-Last-Updated' in response.headers:
    manifestlastupdated = int(response.headers['X-MediaPackage-Manifest-Last-Updated'])
  return manifestlastupdated


def clearup(monitorinfo:dict):
  monitorinfo['manifest']['primary']['foundlastsegment'] = False
  monitorinfo['manifest']['primary']['new']['segments'].clear()
  monitorinfo['manifest']['primary']['new']['duration'] = 0.0


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
      'endpointconfig': endpointconfig
    },
    'state': {
      'starttimeperf': time.perf_counter(),
      'startdatetime': datetime.now(timezone.utc),
      'threads': {},
      'lock': threading.Lock(),
      'stop': threading.Event()
    },
    'metrics': {
      'lastpublishtime': time.perf_counter() - random.uniform(0,20),
      'publishinterval': 20,
      'queue': Queue(),
    },
    'reporting': {
      'lastsavetime': time.perf_counter() - random.uniform(0,20),
      'saveinterval': 20
    },
    'manifest': {
      'primary': {
        'foundlastsegment': False,
        'adbreaks': {},
        'periods': {},
        'headers': {
          'manifestlastupdated': 0
        },
        'new': {
          'segments': {},
          'duration': 0
        },
        'last': {
          'segment': {},
          'period': ''
        },
        'buffer': {
          'window': {},
          'size': 20.0
        }
      }
    }
  }
  # Configure logging
  logging.config.dictConfig(loggingconfig)
  monitorlogger = logging.getLogger('monitor')
  logger = logging.LoggerAdapter(monitorlogger, {'type': monitorinfo['config']['type'], 'origin': monitorinfo['config']['origin'], 'workload': monitorinfo['config']['workload'], 'endpoint': monitorinfo['config']['endpoint'], 'technology': monitorinfo['config']['technology'], 'rendition': 'multi'})
  if endpointconfig['loglevel'] in utils.loglevels.keys():
    logger.setLevel(utils.loglevels[endpointconfig['loglevel']])
  logger.info(f"Started monitoring")
  # Start tracking
  try:
    if 'trackingurl' in endpointconfig.keys():
      trackinglogger = logging.LoggerAdapter(monitorlogger, {'type': monitorinfo['config']['type'], 'origin': monitorinfo['config']['origin'], 'workload': monitorinfo['config']['workload'], 'endpoint': monitorinfo['config']['endpoint'], 'technology': monitorinfo['config']['technology'], 'rendition': 'tracking'})
      monitorinfo['state']['threads']['tracking'] = threading.Thread(target=utils.tracking, args=(trackinglogger, monitorinfo, endpointconfig))
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
      logger.debug(f"Requesting manifest")
      response = utils.request(logger, 'GET', endpointconfig['manifesturl'], 'manifest', 'multi', monitorinfo)
      # Save manifest response
      if endpointconfig['manifests']['save']['local']:
        utils.saveresponse(logger, response, monitorinfo, 'manifests', f"{datetime.now(timezone.utc).strftime('%Y_%m_%d_%H_%M_%S_%f')}", False)
      # Process manifest response if configured to validate manifests
      if endpointconfig['validations']['perform']:
        if monitorinfo['config']['type'] == 'live':
          if response:
            clearup(monitorinfo)
            if monitorinfo['config']['technology'] == 'hls':
              pass
            elif monitorinfo['config']['technology'] == 'dash':
              manifestlastupdated = getmanifestlastupdated(response)
              if manifestlastupdated != monitorinfo['manifest']['primary']['headers']['manifestlastupdated'] or manifestlastupdated == 0:
                dash.monitor(logger, monitorinfo, utils.decoderesponse(response, False))
              monitorinfo['manifest']['primary']['headers']['manifestlastupdated'] = manifestlastupdated
          # Check for staleness
          monitorinfo['manifest']['primary']['buffer']['window'][requesttime] = monitorinfo['manifest']['primary']['new']['duration']
          if requesttime - monitorinfo['state']['starttimeperf'] > max(monitorinfo['manifest']['primary']['buffer']['size'], monitorinfo['config']['endpointconfig']['manifests']['frequency']):
            utils.checkforstaleness(logger, monitorinfo, requesttime)
      # Publish metrics to CW
      if endpointconfig['cwmetrics'] and not monitorinfo['args'].no_aws:
        if time.perf_counter() - monitorinfo['metrics']['lastpublishtime'] > monitorinfo['metrics']['publishinterval']:
          publishmetrics(logger, monitorinfo)
          monitorinfo['metrics']['lastpublishtime'] = time.perf_counter()
      # Save report
      if time.perf_counter() - monitorinfo['reporting']['lastsavetime'] > monitorinfo['reporting']['saveinterval']:
        savereport(logger, monitorinfo, False)
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
    monitorinfo['state']['endtime'] = time.time()
    # Save report
    savereport(logger, monitorinfo, True)
    logger.info(f"Stopped monitoring")


# Find what endpoint configuration changes were made to know if worked needs to be restarted
def needtorestartworker(old:dict, new:dict):
  allowedpaths = {"root['cwmetrics']", "root['manifests']['frequency']", "root['manifests']['save']['local']", "root['tracking']['frequency']", "root['tracking']['get']", "root['tracking']['save']['local']", "root['tracking']['playhead']", "root['loglevel']"}
  diff = DeepDiff(old, new)
  if 'values_changed' not in diff:
    return True
  else:
    if len(diff.keys()) == 1 and all(path in allowedpaths for path in diff['values_changed']):
      return False
    return True


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
  # Start workers for new endpoints
  for identifier, endpointconfig in newendpoints.items():
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
      # dashboardfilepath = pathlib.Path('archive', renderinfo['type'], renderinfo['workload'], renderinfo['origin'], 'dashboards', f"{datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f')}.json")
      # try:
      #   dashboardfilepath.parent.mkdir(parents=True, exist_ok=True)
      #   with dashboardfilepath.open('w') as file:
      #     file.write(render)
      # except Exception as e:
      #   mainlogger.error(f"Failed to archive dashboard. Exception: {e} Traceback: {traceback.format_exc()}")
      renderjson = json.loads(render)
      # Save dashboard to CloudWatch if it is a valid JSON
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
    mainlogger.error(f"Failed to render dashboard for {renderinfo['workload']} workload, {renderinfo['origin']} origin. Exception: {e} Traceback: {traceback.format_exc()}")


# Create CW dashboards
def createdashboards():
  try:
    organizedendpoints = {}
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
        # if config['segments']['get'] or config['segments']['head']:
        #   organizedendpoints[(endpoint[0], endpoint[2], endpoint[4])]['segmentrequests'] = True
        #   endpointinfo['segmentrequests'] = True
        if config['tracking']['get']:
          organizedendpoints[(endpoint[0], endpoint[2], endpoint[4])]['trackingrequests'] = True
          endpointinfo['trackingrequests'] = True
        # Append endpointinfo to list of endpoints
        organizedendpoints[(endpoint[0], endpoint[2], endpoint[4])]['endpoints'].append(endpointinfo.copy())
    for item in organizedendpoints.keys():
      if item in mainconfig['changedworkloads']:
        renderandsavedashboard(organizedendpoints[item])
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
    import m3u8
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
      awsaccountid = boto3.client('sts').get_caller_identity().get('Account')
      # CloudWatch
      cloudwatch = boto3.client('cloudwatch', config=config)
      mainlogger.info(f"Configured CloudWatch client in {args.region}")
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



