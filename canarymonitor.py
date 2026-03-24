#!/usr/bin/env python3
import logging
import logging.config
import multiprocessing
import traceback
import threading
import time
import re
import sys
import json
import yaml
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
import socket
import urllib3


# Allow logging extra values
from loggeradapter import getloggeradapterclass


# Update default endpointconfig with user provided settings
def endpointconfigupdate(endpointconfig, userconfig):
  for k, v in userconfig.items():
    if k in endpointconfig and isinstance(endpointconfig[k], dict) and isinstance(v, dict):
      endpointconfigupdate(endpointconfig[k], v)
    else:
      endpointconfig[k] = v


# Read endpoint information from CSV file content into endpoints dictionary
def readcsvfile(filename, content, endpoints:dict):
  mainlogger.info(f"Ingesting endpoints from CSV file {filename}")
  try:
    lines = content.splitlines()
    for index, line in enumerate(lines, 1):
      if line.strip() and not line.startswith('#'):
        splitline = re.split(f',', line)
        if len(splitline) >= 8:
          validentry = True
          for value in splitline:
            if not value.strip():
              mainlogger.warning(f"Found empty value in {filename} file on line {index}: {line.strip()}")
              validentry = False
              break
          if validentry:
            # Get identifier
            identifier = (splitline[0].strip().lower(), splitline[1].strip().lower(), splitline[2].strip(), splitline[3].strip(), splitline[4].strip(), splitline[5].strip().lower() == 'true') # endpoint type, technology, workload name, endpoint name, origin name, is dai
            # Get endpoint configuration
            endpointconfig = copy.deepcopy(defaultendpointconfig)
            configname = splitline[6].strip()
            configpath = f"configs/{configname}"
            try:
              if configpath in mainconfig['configcache']:
                endpointconfigupdate(endpointconfig, mainconfig['configcache'][configpath])
              else:
                if settings['application']['input_location'] == 's3':
                  if configpath not in mainconfig['hashtable']['config'].keys():
                    response = s3.head_object(Bucket=settings['aws']['bucket'], Key=configpath)
                    mainconfig['hashtable']['config'][configpath] = response['ETag'].strip('"')
                  configdata = s3.get_object(Bucket=settings['aws']['bucket'], Key=configpath)['Body'].read().decode('utf-8')
                  mainconfig['configcache'][configpath] = json.loads(configdata)
                else:
                  if configpath not in mainconfig['hashtable']['config'].keys():
                    gethash('config', configpath, True)
                  with open(configpath, 'r') as file:
                    mainconfig['configcache'][configpath] = json.load(file)
                endpointconfigupdate(endpointconfig, mainconfig['configcache'][configpath])
            except json.decoder.JSONDecodeError as e:
              mainlogger.warning(f"Failed parsing config file {configpath}. Will use default settings. Exception: {e} Traceback: {traceback.format_exc()}")
            except Exception as e:
              mainlogger.warning(f"Failed reading config file {configpath}. Will use default settings. Exception: {e} Traceback: {traceback.format_exc()}")
            # Get manifest url
            endpointconfig['manifesturl'] = splitline[7].strip()
            # Get tracking url
            if len(splitline) >= 9:
              endpointconfig['trackingurl'] = splitline[8].strip()
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
    if settings['application']['input_location'] == 's3':
      response = s3.list_objects_v2(Bucket=settings['aws']['bucket'], Prefix='origins/')
      if 'Contents' in response:
        for obj in response['Contents']:
          if obj['Key'].endswith('.csv'):
            mainconfig['hashtable']['input'][obj['Key']] = obj['ETag'].strip('"')
            csvdata = s3.get_object(Bucket=settings['aws']['bucket'], Key=obj['Key'])['Body'].read().decode('utf-8')
            readcsvfile(obj['Key'], csvdata, endpoints)
    elif settings['application']['input_location'] == 'local':
      for csvfile in localinputsfolderpath.rglob('*.csv'):
        gethash('input', str(csvfile), True)
        with open(csvfile, 'r') as file:
          readcsvfile(str(csvfile), file.read(), endpoints)
    else:
      mainlogger.warning(f"Unsupported input location: {settings['application']['input_location']}")
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
  try:
    if settings['application']['input_location'] == 's3':
      # New or updated inputs from S3
      response = s3.list_objects_v2(Bucket=settings['aws']['bucket'], Prefix='origins/')
      current_s3_files = {}
      if 'Contents' in response:
        for obj in response['Contents']:
          if obj['Key'].endswith('.csv'):
            current_etag = obj['ETag'].strip('"')
            current_s3_files[obj['Key']] = current_etag
            if obj['Key'] not in mainconfig['hashtable']['input']:
              changes.append({'change': 'new', 'category': 'input', 'filename': obj['Key']})
            elif mainconfig['hashtable']['input'][obj['Key']] != current_etag:
              changes.append({'change': 'updated', 'category': 'input', 'filename': obj['Key']})
      # Deleted inputs from S3
      for inputfile in list(mainconfig['hashtable']['input'].keys()):
        if inputfile not in current_s3_files:
          changes.append({'change': 'deleted', 'category': 'input', 'filename': inputfile})
    elif settings['application']['input_location'] == 'local':
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
    for configpath in mainconfig['hashtable']['config'].keys():
      if settings['application']['input_location'] == 's3':
        response = s3.head_object(Bucket=settings['aws']['bucket'], Key=configpath)
        current_etag = response['ETag'].strip('"')
        if mainconfig['hashtable']['config'][configpath] != current_etag:
          changes.append({'change': 'updated', 'category': 'config', 'filename': configpath})
      elif pathlib.Path(configpath).is_file():
        if mainconfig['hashtable']['config'][configpath] != gethash('config', configpath, False):
          changes.append({'change': 'updated', 'category': 'config', 'filename': configpath})
  except Exception as e:
    mainlogger.error(f"Error checking for changes. Hashtable: {mainconfig['hashtable']} Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})
  return changes


# Save report
def savereport(logger, monitorinfo, final:bool):
  try:
    monitorinfo['reporting']['filepath'].parent.mkdir(parents=True, exist_ok=True)
    starttimeepochstr = f"{monitorinfo['state']['starttimeepoch']}"
    monitorinfo['reporting']['report'][starttimeepochstr] = {
      'manifest_url': f"{monitorinfo['config']['endpointconfig']['manifesturl']}",
      'start_time': f"{monitorinfo['state']['startdatetime']}",
      'end_time': f"{datetime.now(timezone.utc)}" if final else None
    }
    if monitorinfo['config']['technology'] == 'dash':
      monitorinfo['reporting']['report'][starttimeepochstr]['periods'] = monitorinfo['manifest']['primary']['periods'],
    elif monitorinfo['config']['technology'] == 'hls':
      monitorinfo['reporting']['report'][starttimeepochstr]['renditions'] = monitorinfo['manifest']['multi']['renditions']
      monitorinfo['reporting']['report'][starttimeepochstr]['ad_breaks'] = monitorinfo['adbreaks']
    with open(monitorinfo['reporting']['filepath'], 'w') as file:
      json.dump(monitorinfo['reporting']['report'], file, indent=2)
      logger.debug(f"Saved report to {monitorinfo['reporting']['filepath']}")
    if monitorinfo['config']['endpointconfig']['reports']['save']['s3']:
      bucket = monitorinfo['settings']['aws']['bucket']
      key = str(monitorinfo['reporting']['filepath'])
      body = json.dumps(monitorinfo['reporting']['report'], indent=2)
      monitorinfo['s3_queue'].put((key, body))
      logger.debug(f"Queued S3 report upload: s3://{bucket}/{key}")
  except Exception as e:
    logger.error(f"Error saving report. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


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
    logger.error(f"Error publishing metrics. Exception: {str(e)}", extra={'event': 'INTERNAL_ERROR'})


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
    logger.error(f"Error updating worker endpoint configuration. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


# Monitor endpoint
def monitor(endpointidentifier:tuple, endpointconfig:dict, stopflag, changeflag, endpointinfofile, sharedwithmain, loggingconfig:dict, settings, s3_queue):
  monitorinfo = {
    'settings': settings,
    's3_queue': s3_queue,
    'config': {
      'type': endpointidentifier[0],
      'technology': endpointidentifier[1],
      'workload': endpointidentifier[2],
      'endpoint': endpointidentifier[3],
      'origin': endpointidentifier[4],
      'isdai': endpointidentifier[5],
      'endpointconfig': endpointconfig,
      'logging': loggingconfig,
      'sharedwithmain': sharedwithmain
    },
    'state': {
      'starttimeperf': time.perf_counter(),
      'startdatetime': datetime.now(timezone.utc),
      'starttimeepoch': int(time.time()),
      'threads': {},
      'lock': threading.Lock(),
      'stop': threading.Event(),
      'status': 'init'
    },
    'metrics': {
      'lastpublishtime': time.perf_counter() - random.uniform(0,15),
      'publishinterval': 15,
      'queue': Queue(),
    },
    'reporting': {
      'filepath': pathlib.Path('archive', endpointidentifier[0], endpointidentifier[2], endpointidentifier[4], endpointidentifier[3], endpointidentifier[1], 'report.json'),
      'lastsavetime': 0.0,
      'report': {}
    },
    'adbreaks': {}
  }
  monitorinfo['reporting']['starttime'] = f"{monitorinfo['state']['startdatetime']}"
  if monitorinfo['config']['technology'] == 'dash':
    utils.initializemonitor(monitorinfo, 'dash')
  elif monitorinfo['config']['technology'] == 'hls':
    monitorinfo['manifest'] = {
      'multi': {
        'lasthash': '',
        'renditions': {}
      }
    }
  # Configure logging
  logging.config.dictConfig(loggingconfig)
  monitorlogger = logging.getLogger('monitor')
  logger = getloggeradapterclass(settings['application']['json_logger'])(monitorlogger, {'type': monitorinfo['config']['type'], 'origin': monitorinfo['config']['origin'], 'workload': monitorinfo['config']['workload'], 'endpoint': monitorinfo['config']['endpoint'], 'technology': monitorinfo['config']['technology'], 'rendition': 'multi'})
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
    if endpointconfig.get('trackingurl'):
      monitorinfo['state']['threads']['tracking'] = threading.Thread(target=utils.tracking, args=(logger, monitorinfo, endpointconfig))
      monitorinfo['state']['threads']['tracking'].start()
  except Exception as e:
    logger.error(f"Failed to start tracking. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})
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
      if endpointconfig['manifests']['save']['local'] or endpointconfig['manifests']['save']['s3']:
        utils.saveresponse(logger, response, monitorinfo, 'manifests', "", False, 'multi')
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
            manifestchanged = False
            # Check for manifest content change
            manifesthash = utils.getmultivariantfingerprint(logger, utils.decoderesponse(response, True))
            if monitorinfo['manifest']['multi']['lasthash'] and manifesthash != monitorinfo['manifest']['multi']['lasthash']:
              logger.warning(f"Multivariant manifest has changed", extra={'event': 'MULTIVARIANT_MANIFEST_CHANGED'})
              manifestchanged = True
            # Initialize rendition manifests monitoring
            if monitorinfo['state']['status'] == 'init':
              hls.startthreads(logger, monitorinfo, utils.decoderesponse(response, True), {})
              monitorinfo['state']['status'] = 'running'
            elif manifestchanged:
              hls.startthreads(logger, monitorinfo, utils.decoderesponse(response, True), {'reason': 'MULTIVARIANT_MANIFEST_CHANGED'})
            # Update manifest hash
            monitorinfo['manifest']['multi']['lasthash'] = manifesthash
      # Publish metrics to CW
      if endpointconfig['cwmetrics'] and monitorinfo['settings']['aws']['metrics'] and cloudwatch:
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
    logger.error(f"Encountered error while monitoring. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})
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
  forbiddenpaths = {"root['manifests']['hls_renditions']", "root['manifesturl']", "root['trackingurl']"}
  diff = DeepDiff(old, new)
  # No changes
  if not diff:
    return False
  # Check all change types
  for change_type in ['values_changed', 'iterable_item_added', 'iterable_item_removed', 'type_changes']:
    if change_type in diff:
      for path in diff[change_type]:
        # Check if path starts with any forbidden path
        if any(path.startswith(forbidden) for forbidden in forbiddenpaths):
          return True
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
  if settings['application']['threads']:
    mainconfig['stopflags'][identifier] = threading.Event()
    mainconfig['changeflags'][identifier] = threading.Event()
    mainconfig['workers'][identifier] = threading.Thread(target=monitor, args=(identifier, endpointconfig, mainconfig['stopflags'][identifier], mainconfig['changeflags'][identifier], endpointinfofile.name, sharedwithmain, loggingconfig, settings))
  else:
    mainconfig['stopflags'][identifier] = multiprocessing.Event()
    mainconfig['changeflags'][identifier] = multiprocessing.Event()
    mainconfig['workers'][identifier] = multiprocessing.Process(target=monitor, args=(identifier, endpointconfig, mainconfig['stopflags'][identifier], mainconfig['changeflags'][identifier], endpointinfofile.name, sharedwithmain, loggingconfig, settings, mainconfig['s3_queue']))
  mainconfig['workers'][identifier].start()
  # Wait 50 milliseconds to avoid spike in new processes
  time.sleep(0.05)


# Update x,y positions for each widget
def recalculate_positions(renderjson):
  try:
    current_x = 0
    current_y = 0
    max_height_in_row = 0
    for widget in renderjson.get('widgets', []):
      width = widget.get('width', 4)
      height = widget.get('height', 4)
      if current_x + width > 24:
        current_x = 0
        current_y += max_height_in_row
        max_height_in_row = 0
      widget['x'] = current_x
      widget['y'] = current_y
      current_x += width
      max_height_in_row = max(max_height_in_row, height)
    return json.dumps(renderjson)
  except Exception as e:
    mainlogger.error(f"Error recalculating widget positions. Exception: {e} Traceback: {traceback.format_exc()}")


# Render and save CW dashboards
def renderandsavedashboard(renderinfo:dict):
  render = ''
  try:
    if renderinfo['type'] in ['live', 'vod']:
      template = env.get_template(renderinfo['type'])
      render = template.render(renderinfo=renderinfo, dashboardconfig=dashboardconfig)
      render = recalculate_positions(json.loads(render))
      # Save dashboard to CloudWatch
      try:
        dashboardname = f"{renderinfo['workload']}_{renderinfo['origin']}_canary-monitor"
        response = cloudwatch.put_dashboard(DashboardName=dashboardname, DashboardBody=render)
        if response:
          mainlogger.info(f"Saved dashboard '{dashboardname}' to CloudWatch")
          if 'DashboardValidationMessages' in response.keys() and len(response['DashboardValidationMessages']) > 0:
            mainlogger.warning(f"Dashboard validation warnings: {response['DashboardValidationMessages']}")
      except Exception as e:
        mainlogger.error(f"Faled to save dashboard to CloudWatch. Exception: {e} Traceback: {traceback.format_exc()}")
        raise
  except Exception as e:
    mainlogger.error(f"Error creating dashboard for {renderinfo['workload']} workload, {renderinfo['origin']} origin. Exception: {e} Traceback: {traceback.format_exc()}")
  finally:
    # Save dashboard
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
      f.write(render)
      mainlogger.info(f"Saved dashboard to {f.name}")


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
            'includes_dash': False,
            'includes_hls': False,
            'endpoints': []
          }
        endpointinfo = {
          'technology': endpoint[1],
          'endpoint': endpoint[3],
          'isdai': endpoint[5],
          'segmentrequests': False,
          'trackingrequests': False,
          'config': config
        }
        # Include renditions
        if endpointinfo['technology'] == 'hls':
          organizedendpoints[(endpoint[0], endpoint[2], endpoint[4])]['includes_hls'] = True
          endpointinfo['renditions'] = sharedwithmain.get(endpoint, {}).get('hls_renditions', [])
          if not endpointinfo['renditions']:
            mainlogger.warning(f"Failed to collect rendition information for HLS endpoint {endpoint}")
        elif endpointinfo['technology'] == 'dash':
          organizedendpoints[(endpoint[0], endpoint[2], endpoint[4])]['includes_dash'] = True
        # if config['segments']['get'] or config['segments']['head']:
        #   organizedendpoints[(endpoint[0], endpoint[2], endpoint[4])]['segmentrequests'] = True
        #   endpointinfo['segmentrequests'] = True
        if config['tracking']['get'] and endpointinfo['isdai']:
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


# Get hostname / instance id for service metric dimension
def gethostname():
  try:
    http = urllib3.PoolManager()
    # Get session token for IMDSv2
    token_response = http.request('PUT','http://169.254.169.254/latest/api/token', headers={'X-aws-ec2-metadata-token-ttl-seconds': '21600'}, timeout=1.0)
    token = token_response.data.decode('utf-8')
    # Get instance ID using token
    instance_response = http.request('GET','http://169.254.169.254/latest/meta-data/instance-id', headers={'X-aws-ec2-metadata-token': token}, timeout=1.0)
    mainconfig['hostname'] = instance_response.data.decode('utf-8')
    mainlogger.info(f"Got hostname {mainconfig['hostname']}")
  except Exception as e:
    mainconfig['hostname'] = socket.gethostname()
  finally:
    mainlogger.info(f"Got hostname '{mainconfig['hostname']}'")


def publishservicemetrics():
  if settings['aws']['metrics']:
    try:
      # Count endpoints by technology
      tech_counts = {'hls': 0, 'dash': 0}
      for endpoint_id in mainconfig['endpoints'].keys():
        technology = endpoint_id[1].lower()
        if technology in tech_counts:
          tech_counts[technology] += 1
      
      metric_data = []
      # Publish metric for each technology
      for technology, count in tech_counts.items():
        metric_data.append({
          'MetricName': 'Endpoints',
          'Value': count,
          'Unit': 'Count',
          'Timestamp': datetime.now(timezone.utc),
          'Dimensions': [
            {'Name': 'Hostname', 'Value': mainconfig['hostname']},
            {'Name': 'Technology', 'Value': technology}
          ]
        })
      cloudwatch.put_metric_data(
        Namespace='CanaryMonitor',
        MetricData=metric_data
      )
    except Exception as e:
      mainlogger.error(f"Error publishing metrics: {e}")


# Write files to S3
def start_s3_upload_threads(queue, settings, logger, num_threads=10):
  import threading
  def upload_worker():
    while True:
      try:
        item = queue.get()
        if item is None:
          break
        # Unpack upload request
        key, body = item
        # Upload to S3
        s3.put_object(Bucket=settings['aws']['bucket'], Key=key, Body=body)
      except Exception as e:
        logger.error(f"S3 upload error: {e}")
  # Start upload threads
  threads = []
  for _ in range(num_threads):
    t = threading.Thread(target=upload_worker, daemon=True)
    t.start()
    threads.append(t)
  logger.info(f"Started {num_threads} S3 upload threads")
  return threads


def stop_s3_threads(queue, threads, logger):
  # Send poison pills
  for _ in range(len(threads)):
    queue.put(None)
  # Wait for threads to finish
  for t in threads:
    t.join(timeout=5)


# Handle signals
def signalhandler(signal, frame):
  raise KeyboardInterrupt()


# Main
if __name__ == '__main__':
  # Read settings from YAML file
  gotsettingsfromfile = False
  settings_path = pathlib.Path('settings.yaml')
  if settings_path.is_file():
    with open(settings_path, 'r') as f:
      settings = yaml.safe_load(f)
      gotsettingsfromfile = True
  else:
    settings = {
      'application': {
        'threads': False,
        'json_logger': False,
        'input_location': 'local'
      },
      'aws': {
        'region': None,
        'metrics': False,
        'dashboards': False,
        'bucket': None,
        'lambda': {
          'report': None,
          'logs': None
        }
      }
    }

  # Configure logging
  locallogsfolderpath = pathlib.Path('logs')
  locallogsfolderpath.mkdir(exist_ok=True)
  
  # Use JSON logger only if requested and library is available
  loggingconfigpath = pathlib.Path(os.path.dirname(os.path.realpath(__file__)), 'loggingconfig.json')
  with loggingconfigpath.open() as loggingconfigfile:
    loggingconfig = json.load(loggingconfigfile)
  
  # Check if JSON logging is requested and available
  use_json = settings['application']['json_logger']
  if use_json:
    try:
      import pythonjsonlogger
    except ImportError:
      use_json = False
  
  # Modify formatters based on availability
  if not use_json:
    loggingconfig['handlers']['filemonitor']['formatter'] = 'monitor'
    loggingconfig['handlers']['fileservice']['formatter'] = 'service'

  # Configure main logger
  logging.config.dictConfig(loggingconfig)
  mainlogger = logging.getLogger('service')
  if settings['application']['json_logger'] and not use_json:
    mainlogger.warning(f"Missing 'python-json-logger' package, will use default logging")

  # Check if settings were loaded from file
  if gotsettingsfromfile:
    mainlogger.info(f"Loaded settings from {settings_path}")
  else:
    mainlogger.warning(f"Failed to load settings from {settings_path}")

  # Start
  mainlogger.info(f"Started")

  # Enable threading if platform is Windows
  if platform.system() == 'Windows':
    mainlogger.info(f"Using threads because system is Windows")
    settings['application']['threads'] = True

  # Import external libraries
  try:
    from deepdiff import DeepDiff
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    from lxml import etree as et
  except Exception as e:
    mainlogger.error(f"Exception: {e} Trackeback: {traceback.format_exc()}")
    sys.exit(1)

  # Set worker type and prepare data sharing
  if settings['application']['threads']:
    sharedwithmain = {}
  else:
    multiprocessing.set_start_method('fork')
    manager = multiprocessing.Manager()
    sharedwithmain = manager.dict()


  # Data
  max_s3_upload_queue_size = 50
  mainconfig = {
    'stopflags': {},
    'changeflags': {},
    'workers': {},
    'hashtable': {
      'input': {},
      'config': {}
    },
    'configcache': {},
    'changedworkloads': [],
    'hostname': '',
    's3_queue': multiprocessing.Queue(maxsize=max_s3_upload_queue_size),
    's3_threads': []  # S3 upload threads
  }

  # Configure AWS resources
  s3 = None
  cloudwatch = None
  if settings['aws']['metrics'] or settings['aws']['dashboards'] or settings['application']['input_location'] == 's3' or settings['aws']['bucket']:
    try:
      import boto3
      from botocore.config import Config
      from botocore.exceptions import BotoCoreError, ClientError
      config = Config(
        region_name=settings['aws']['region'],
        read_timeout=3,
        connect_timeout=3,
        retries={
          'max_attempts': 1
        }
      )
      # Cloudwatch
      if settings['aws']['metrics'] or settings['aws']['dashboards']:
        cloudwatch = boto3.client('cloudwatch', config=config)
        mainlogger.info(f"Configured CloudWatch client in {settings['aws']['region']}")
      # S3
      if settings['application']['input_location'] == 's3' or settings['aws']['bucket']:
        s3 = boto3.client('s3', config=config)
        mainlogger.info(f"Configured S3 client in {settings['aws']['region']}")
        try:
          s3.head_bucket(Bucket=settings['aws']['bucket'])
          mainlogger.info(f"Found bucket {settings['aws']['bucket']}")
          # Start S3 upload threads
          if settings['aws']['bucket']:
            mainconfig['s3_threads'] = start_s3_upload_threads(mainconfig['s3_queue'], settings, mainlogger)
        except ClientError as e:
          if e.response['Error']['Code'] == '404':
            mainlogger.error(f"Error finding S3 bucket. Exception: {e}")
          elif e.response['Error']['Code'] == '403':
            mainlogger.error(f"Error accessing S3 bucket. Exception: {e}")
          sys.exit(1)
    except Exception as e:
      mainlogger.error(f"Error initializing AWS resources. Exception: {e} Trackeback: {traceback.format_exc()}")
      sys.exit(1)

  # Prepare local storage
  localinputsfolderpath = pathlib.Path('origins')
  localinputsfolderpath.mkdir(exist_ok=True)
  localoutputsfolderpath = pathlib.Path('archive')
  localoutputsfolderpath.mkdir(exist_ok=True)

  # Handle signals
  signal.signal(signal.SIGINT, signalhandler)  # 2
  signal.signal(signal.SIGTERM, signalhandler)  # 15

  dashboardconfig = {
    'maxwidth': 24,
    'header': {
      'height': 1,
      'width': 24,
    },
    'readme': {
      'height': 4,
      'width': 24,
    },
    'metric': {
      'height': 4,
      'width': 4
    },
    'report': {
      'height': 12,
      'width': 20,
      'bucket': settings['aws']['bucket'] or None,
      'lambda': settings['aws']['lambda']['report'] or None
    },
    'logs': {
      'height': 12,
      'width': 4,
      'lambda': settings['aws']['lambda']['logs'] or None
    },
    'jsonlogformat': settings['application']['json_logger'],
    'region': settings['aws']['region'] or None
  }

  # Load default endpoint config
  if settings['application']['input_location'] == 's3':
    try:
      configdata = s3.get_object(Bucket=settings['aws']['bucket'], Key='configs/default.json')['Body'].read().decode('utf-8')
      defaultendpointconfig = json.loads(configdata)
    except Exception as e:
      mainlogger.warning(f"Did not find default endpoint config file in S3. Exception: {e}")
      sys.exit(1)
  elif pathlib.Path('configs', 'default.json').is_file():
    with open(pathlib.Path('configs', 'default.json'), 'r') as file:
      defaultendpointconfig = json.load(file)
  else:
    mainlogger.warning(f"Did not find default endpoint config file")
    sys.exit(1)

  # Temp file for storing endpoint information
  endpointinfofile = tempfile.NamedTemporaryFile(mode='w+', delete=False)
  endpointinfofile.close()

  # Prepare dasbhoard templates
  env = Environment(loader=FileSystemLoader('templates'), autoescape=select_autoescape(), trim_blocks=True, lstrip_blocks=True)

  # Get hostname / instance id
  gethostname()

  # Main loop
  try:
    # Collect endpoint information
    mainconfig['endpoints'] = getendpointsinfo()
    saveendpointinfotofile(mainconfig['endpoints'])

    # Start monitor workers
    for key, value in mainconfig['endpoints'].items():
      startmonitorworker(key, value)
    mainlogger.info(f"Now monitoring {len(mainconfig['workers'])} endpoints")

    while True:
      # Check for input and config changes
      inputorconfigchanges = checkforinputorconfigchanges()
      if inputorconfigchanges:
        for item in inputorconfigchanges:
          mainlogger.info(f"Input or config has changed, {item['change']}: {item['filename']}")
          # Clear config cache
          mainconfig['configcache'].clear()
        mainconfig['endpoints'] = updateworkers()
        mainlogger.info(f"Now monitoring {len(mainconfig['workers'])} endpoints")
        if len(mainconfig['changedworkloads']) > 0 and settings['aws']['dashboards']:
          createdashboards()
        mainconfig['changedworkloads'].clear()
      # Check S3 upload queue
      s3_queue_size = mainconfig['s3_queue'].qsize()
      if s3_queue_size > max_s3_upload_queue_size * 0.5:
        mainlogger.warning(f"S3 upload is backed up ({(s3_queue_size/max_s3_upload_queue_size) * 100}% full)")
      # Publish metrics
      publishservicemetrics()
      time.sleep(5)
  except KeyboardInterrupt:
    mainlogger.info(f"Received signal to stop, waiting for all workers to stop")
  except Exception as e:
    mainlogger.error(f"Error. Exception: {e} Traceback: {traceback.format_exc()}")
  finally:
    mainlogger.info(f"Stopping workers")
    for flag in mainconfig['stopflags'].values():
      flag.set() # noqa
    for worker in mainconfig['workers'].values():
      worker.join() # noqa
    mainlogger.info(f"Stopping S3 upload threads")
    if mainconfig['s3_threads']:
      stop_s3_threads(mainconfig['s3_queue'], mainconfig['s3_threads'], mainlogger)


