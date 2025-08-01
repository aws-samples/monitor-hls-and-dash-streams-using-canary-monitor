from datetime import datetime, timezone
import gzip
import pathlib
import traceback
import time
import urllib3
import logging
import threefive

# Custom exceptions
class HTTPNon200Error(Exception):
  pass

# Log levels
loglevels = {
  'debug': logging.DEBUG,
  'info': logging.INFO,
  'warning': logging.WARNING,
  'error': logging.ERROR,
  'critical': logging.CRITICAL
}

# Configure HTTP requests
http = urllib3.PoolManager(timeout=3, retries=urllib3.Retry(total=0, redirect=True))

# SCTE messages
segmentationmessagemap = {
  '16': 'Program Start',
  '17': 'Program End',
  '32': 'Chapter Start',
  '33': 'Chapter End',
  '34': 'Break Start',
  '35': 'Break End',
  '48': 'Provider Advertisement Start',
  '49': 'Provider Advertisement End',
  '50': 'Distributor Advertisement Start',
  '51': 'Distributor Advertisement End',
  '52': 'Provider Placement Opportunity Start',
  '53': 'Provider Placement Opportunity End',
  '54': 'Distributor Placement Opportunity Start',
  '55': 'Distributor Placement Opportunity End',
  '56': 'Provider Overlay Placement Opportunity Start',
  '57': 'Provider Overlay Placement Opportunity End'
}


# Decode base64 or hex SCTE string and return a decoded message
def decodesctestring(logger, scte:str):
  logger.debug(f"Decoding SCTE message '{scte}'")
  sctemessage = {}
  try:
    cue = threefive.Cue(bytes.fromhex(scte[2:])) if scte.startswith('0x') else threefive.Cue(scte)
    cue.decode()
    # Splice insert
    if cue.command.command_type == 5:
      sctemessage['type'] = 'spliceinsert'
      if cue.command.out_of_network_indicator:
        sctemessage['outofnetwork'] = True
    elif cue.command.command_type == 6:
      sctemessage['type'] = 'timesignal'
    for descriptor in cue.descriptors:
      if descriptor.tag == 2:
        segmentationdescriptor = {
          'segmentationtype': round(descriptor.segmentation_type_id, 3),
          'segmentationmessage': segmentationmessagemap.get(str(descriptor.segmentation_type_id), 'Unknown')
        }
        if descriptor.segmentation_duration is not None:
          segmentationdescriptor['duration'] = descriptor.segmentation_duration
        sctemessage.setdefault('descriptors', []).append(segmentationdescriptor)
  except Exception as e:
    logger.error(f"Error decoding SCTE message '{scte}'. Exception: {str(e)} Traceback: {traceback.format_exc()}")
  return sctemessage


# Send HTTP request
def request(logger, method:str, url:str, requesttype:str, rendition:str, monitorinfo):
  response = None
  headers = {'User-Agent': 'CanaryMonitor (v24.11.a)'}
  if requesttype in ['manifest', 'tracking']:
    headers.update({'Accept-Encoding': 'gzip'})
  dimensions = [{'Name': 'RequestType', 'Value': requesttype}]
  if rendition:
    dimensions.append({'Name': 'Rendition', 'Value': rendition})
  starttime = time.perf_counter()
  try:
    response = http.request(method, url, headers=headers, decode_content=False)
    if response.status >= 300:
      raise HTTPNon200Error()
  except HTTPNon200Error:
    responsedata = response.data.decode('utf-8').replace('\n', '')
    logger.error(f"HTTP response {response.status} ({response.reason}), url: {url}, response headers: {dict(response.headers.items())}, response data: {responsedata}")
    addmetric(logger, monitorinfo, 'Request', 1, 'Count', dimensions + [{'Name': 'Status', 'Value': f"{response.status // 100}xx"}])
    return None
  except Exception as e:
    logger.error(f"HTTP failure, url: {url} Exception: {str(e)}")
    addmetric(logger, monitorinfo, 'Request', 1, 'Count', dimensions + [{'Name': 'Status', 'Value': 'failure'}])
    return None
  else:
    logger.debug(f"HTTP response {response.status} ({response.reason}), url: {url}, response headers: {dict(response.headers.items())}")
    return response
  finally:
    addmetric(logger, monitorinfo, 'Latency', int((time.perf_counter() - starttime) * 1000), 'Milliseconds', dimensions)


# Add metric to queue
def addmetric(logger, monitorinfo, metricname:str, metricvalue, metricunit:str, metricdimensions:list):
  if monitorinfo['config']['endpointconfig']['cwmetrics']:
    metricdata = {
      'MetricName': metricname,
      'Value': metricvalue,
      'Dimensions': metricdimensions,
      'Timestamp': datetime.now(timezone.utc)
    }
    if metricunit:
      metricdata['Unit'] = metricunit
    monitorinfo['metrics']['queue'].put(metricdata)


# Save response to disk or to S3
def saveresponse(logger, response, monitorinfo:dict, filetypegroup:str, timestamp:str, binary:bool):
  isgzip = False
  extension = ''
  try:
    if response:
      # Check technology for extension
      if filetypegroup == 'manifests':
        if monitorinfo['config']['technology'] == 'dash':
          extension = '.mpd'
        else:
          extension = '.m3u8'
      # Check if response is gzip
      if 'Content-Encoding' in response.headers:
        if response.headers['Content-Encoding'] == 'gzip':
          isgzip = True
      # If local
      if monitorinfo['config']['endpointconfig'][filetypegroup]['save']['local']:
        folderpath = pathlib.Path('archive', monitorinfo['config']['type'], monitorinfo['config']['workload'], monitorinfo['config']['origin'], monitorinfo['config']['endpoint'], monitorinfo['config']['technology'], filetypegroup)
        folderpath.mkdir(parents=True, exist_ok=True)
        if binary:
          pass
        else:
          filepath = folderpath / f"{timestamp}{extension}.gz"
          if isgzip:
            with open(filepath, 'wb') as f:
              f.write(response.data)
          else:
            with gzip.open(filepath, 'wb') as f:
              f.write(response.data)
          logger.debug(f"Saved response to {filepath}")
  except Exception as e:
    logger.error(f"Error saving response. Exception: {str(e)} Traceback: {traceback.format_exc()}")


# Wait for a certain time
def wait(logger, starttime:float, duration:float):
  waittime = starttime - time.perf_counter() + duration
  if waittime > 0:
    time.sleep(waittime)
  else:
    logger.warning(f"Negative wait time between manifest requests")


# Tracking
def tracking(logger, monitorinfo:dict, endpointconfig:dict):
  try:
    while not monitorinfo['state']['stop'].is_set():
      ready = True
      starttime = time.perf_counter()
      if endpointconfig['tracking']['get']:
        logger.debug(f"Requesting tracking")
        trackingurl = endpointconfig['trackingurl']
        if monitorinfo['config']['endpointconfig']['tracking']['playhead']:
          if 'playhead' in monitorinfo['manifest'].keys():
            trackingurl = f"{trackingurl}?aws.playheadPositionInSeconds={str(monitorinfo['manifest']['playhead'])}"
          else:
            ready = False
            logger.debug(f"Waiting for playhead to be ready")
        if ready:
          response = request(logger, 'GET', trackingurl, 'tracking', '', monitorinfo)
          if endpointconfig['tracking']['save']['local']:
            saveresponse(logger, response, monitorinfo, 'tracking', f"{datetime.now(timezone.utc).strftime('%Y_%m_%d_%H_%M_%S_%f')}", False)
      wait(logger, starttime, endpointconfig['tracking']['frequency'])
  except Exception as e:
    logger.error(f"Encountered error in tracking thread. Exception: {str(e)} Traceback: {traceback.format_exc()}")
  finally:
    logger.info(f"Stopped tracking thread")


def checkforstaleness(logger, monitorinfo:dict, requesttime):
  durationsum = 0
  todelete = []
  try:
    bufferlength = len(monitorinfo['manifest']['primary']['buffer']['window'])
    for timestamp, duration in monitorinfo['manifest']['primary']['buffer']['window'].items():
      if timestamp > requesttime - monitorinfo['manifest']['primary']['buffer']['size'] or bufferlength == 1:
        durationsum = durationsum + duration
      else:
        todelete.append(timestamp)
    for timestamp in todelete:
      del monitorinfo['manifest']['primary']['buffer']['window'][timestamp]
    addmetric(logger, monitorinfo, 'BufferFillDuration', durationsum, 'Seconds', [])
    if durationsum == 0:
      logger.warning(f"Stale manifest")
  except Exception as e:
    logger.error(f"Error while checking for staleness. Exception: {str(e)} Traceback: {traceback.format_exc()}")