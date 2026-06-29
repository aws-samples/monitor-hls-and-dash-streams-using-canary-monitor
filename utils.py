from datetime import datetime, timezone
from loggeradapter import getloggeradapterclass
import logging.config
import gzip
import json
import pathlib
import traceback
import time
import urllib3
import logging
import isodate
import threefive
import random
import re
import hashlib

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
http = urllib3.PoolManager(num_pools=5, maxsize=5, timeout=3, retries=urllib3.Retry(total=None, connect=0, read=0, redirect=5, status=0, other=0))


# SCTE messages
segmentationmessagemap = {
    '0': 'Not Indicated',
    '1': 'Content Identification',
    '2': 'Private',
    '16': 'Program Start',
    '17': 'Program End',
    '18': 'Program Early Termination',
    '19': 'Program Breakaway',
    '20': 'Program Resumption',
    '21': 'Program Runover Planned',
    '22': 'Program Runover Unplanned',
    '23': 'Program Overlap Start',
    '24': 'Program Blackout Override',
    '25': 'Program Join',
    '26': 'Program Immediate Resumption',
    '32': 'Chapter Start',
    '33': 'Chapter End',
    '34': 'Break Start',
    '35': 'Break End',
    '36': 'Opening Credit Start (deprecated)',
    '37': 'Opening Credit End (deprecated)',
    '38': 'Closing Credit Start (deprecated)',
    '39': 'Closing Credit End (deprecated)',
    '48': 'Provider Advertisement Start',
    '49': 'Provider Advertisement End',
    '50': 'Distributor Advertisement Start',
    '51': 'Distributor Advertisement End',
    '52': 'Provider Placement Opportunity Start',
    '53': 'Provider Placement Opportunity End',
    '54': 'Distributor Placement Opportunity Start',
    '55': 'Distributor Placement Opportunity End',
    '56': 'Provider Overlay Placement Opportunity Start',
    '57': 'Provider Overlay Placement Opportunity End',
    '58': 'Distributor Overlay Placement Opportunity Start',
    '59': 'Distributor Overlay Placement Opportunity End',
    '60': 'Provider Promo Start',
    '61': 'Provider Promo End',
    '62': 'Distributor Promo Start',
    '63': 'Distributor Promo End',
    '64': 'Unscheduled Event Start',
    '65': 'Unscheduled Event End',
    '66': 'Alternate Content Opportunity Start',
    '67': 'Alternate Content Opportunity End',
    '68': 'Provider Ad Block Start',
    '69': 'Provider Ad Block End',
    '70': 'Distributor Ad Block Start',
    '71': 'Distributor Ad Block End',
    '80': 'Network Start',
    '81': 'Network End'
  }


# Recursively convert dictionary values for better logging
def printdictionary(logger, toprint:dict):
  try:
    if isinstance(toprint, dict):
      return {k: printdictionary(logger, v) for k, v in toprint.items()}
    elif isinstance(toprint, datetime):
      return toprint.isoformat()
    elif isinstance(toprint, list):
      return str(toprint)
    elif isinstance(toprint, float):
      return round(toprint, 3)
    else:
      return toprint
  except Exception as e:
    logger.error(f"Error during printing of dictionary. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


# Decode base64 or hex SCTE string and return a decoded message
def decodesctestring(logger, scte:str) -> dict:
  sctemessage = {}
  if scte:
    logger.debug(f"Decoding SCTE string '{scte}'")
    try:
      cue = threefive.Cue(bytes.fromhex(scte[2:])) if scte.startswith('0x') else threefive.Cue(scte)
      cue.decode()
      # Splice insert
      if cue.command.command_type == 5:
        sctemessage['type'] = 'splice_insert'
        if cue.command.out_of_network_indicator:
          # cue.show()
          sctemessage['out_of_network'] = True
          if cue.command.splice_event_id is not None:
            sctemessage['splice_event_id'] = int(cue.command.splice_event_id)
          if cue.command.splice_immediate_flag is not None:
            sctemessage['splice_immediate'] = bool(cue.command.splice_immediate_flag)
          if cue.command.break_auto_return is not None:
            sctemessage['auto_return'] = bool(cue.command.break_auto_return)
          if cue.command.break_duration is not None:
            sctemessage['duration'] = float(cue.command.break_duration)
          if cue.command.avail_num is not None:
            sctemessage['avail_num'] = int(cue.command.avail_num)
      elif cue.command.command_type == 6:
        sctemessage['type'] = 'time_signal'
      for descriptor in cue.descriptors:
        segmentationdescriptor = {
          'segmentation_type': None
        }
        if descriptor.tag is not None and descriptor.tag == 2:
          if descriptor.segmentation_type_id is not None:
            segmentationdescriptor = {
              'segmentation_type': descriptor.segmentation_type_id,
              'segmentation_message': segmentationmessagemap.get(str(descriptor.segmentation_type_id), 'Unknown')
            }
            if descriptor.segmentation_duration is not None:
              segmentationdescriptor['duration'] = descriptor.segmentation_duration
            if descriptor.segmentation_upid_type == 12:
              try:
                privatedata = descriptor.segmentation_upid['private_data']
                if privatedata.startswith('0x'):
                  privatedata = privatedata[2:]
                privatedatadecoded = bytes.fromhex(privatedata).decode('utf-8')
                segmentationdescriptor['upid_private_data'] = f"{privatedatadecoded}"
              except Exception as e:
                logger.error(f"Error decoding SCTE UPID private data. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})
        sctemessage.setdefault('descriptors', []).append(segmentationdescriptor)
    except Exception as e:
      logger.error(f"Error decoding SCTE message '{scte}'. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})
  return sctemessage


# Send HTTP request
def request(logger, method:str, url:str, requesttype:str, rendition:str, monitorinfo):
  response = None
  headers = {'User-Agent': 'CanaryMonitor (v2.0)'}
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
    responsedata = decoderesponse(response, True).replace('\n', '')
    logger.error(f"HTTP response {response.status} ({response.reason}), url: {url}, response headers: {dict(response.headers.items())}, response data: {responsedata}")
    addmetric(logger, monitorinfo, 'Request', 1, 'Count', dimensions + [{'Name': 'Status', 'Value': f"{response.status // 100}xx"}])
    return None
  except Exception as e:
    logger.error(f"HTTP timeout, url: {url} Exception: {str(e)}")
    addmetric(logger, monitorinfo, 'Request', 1, 'Count', dimensions + [{'Name': 'Status', 'Value': 'failure'}])
    return None
  else:
    logger.debug(f"HTTP response {response.status} ({response.reason}), url: {url}, response headers: {dict(response.headers.items())}", extra={'statusCode': response.status})
    return response
  finally:
    addmetric(logger, monitorinfo, 'Latency', int((time.perf_counter() - starttime) * 1000), 'Milliseconds', dimensions)


# Add metric to queue
def addmetric(logger, monitorinfo, metricname:str, metricvalue, metricunit:str, metricdimensions:list):
  if monitorinfo['config']['endpointconfig']['cwmetrics'] and monitorinfo['settings']['aws']['metrics']:
    metricdata = {
      'MetricName': metricname,
      'Value': metricvalue,
      'Dimensions': metricdimensions,
      'Timestamp': datetime.now(timezone.utc)
    }
    if metricunit:
      metricdata['Unit'] = metricunit
    monitorinfo['metrics']['queue'].put(metricdata)


# Decode HTTP response
def decoderesponse(response, utf:bool):
  isgzip = False
  if 'Content-Encoding' in response.headers:
    if response.headers['Content-Encoding'] == 'gzip':
      isgzip = True
  if isgzip:
    if utf:
      return gzip.decompress(response.data).decode('utf-8')
    else:
      return gzip.decompress(response.data)
  else:
    if utf:
      return response.data.decode('utf-8')
    else:
      return response.data


# Get multivariant manifest fingerprint based on rendition lines only
def getmultivariantfingerprint(logger, manifest):
  try:
    lines = manifest.splitlines()
    filtered = []
    for i, line in enumerate(lines):
      stripped = line.strip()
      if stripped.startswith(('#EXT-X-VERSION:', '#EXT-X-MEDIA:', '#EXT-X-STREAM-INF:')):
        filtered.append(stripped)
        if stripped.startswith('#EXT-X-STREAM-INF:') and i + 1 < len(lines) and not lines[i + 1].strip().startswith('#'):
          filtered.append(lines[i + 1].strip())
    return hashlib.md5('\n'.join(filtered).encode('utf-8')).hexdigest()
  except Exception as e:
    logger.error(f"Error getting multivariant manifest fingerprint. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


# Save response to disk or to S3
def saveresponse(logger, response, monitorinfo:dict, filetypegroup:str, filename:str, binary:bool, rendition:str):
  isgzip = False
  extension = ''
  try:
    if response:
      now = datetime.now(timezone.utc)
      timestamp = f"{now.strftime('%Y_%m_%d_%H_%M_%S_%f')}"
      # Check technology for extension
      if filetypegroup == 'manifests':
        if monitorinfo['config']['technology'] == 'dash':
          extension = '.mpd'
        else:
          extension = '.m3u8'
      elif filetypegroup == 'tracking':
        extension = '.json'
      # Check if response is gzip
      if 'Content-Encoding' in response.headers:
        if response.headers['Content-Encoding'] == 'gzip':
          isgzip = True
      # Prepare destination path
      dst = f"archive/{monitorinfo['config']['type']}/{monitorinfo['config']['workload']}/{monitorinfo['config']['origin']}/{monitorinfo['config']['endpoint']}/{monitorinfo['config']['technology']}/{filetypegroup}/{now.strftime('%Y')}/{now.strftime('%m')}/{now.strftime('%d')}"
      if monitorinfo['config']['technology'] == 'hls' and filetypegroup == 'manifests' and rendition:
        dst = f"{dst}/{rendition}"
      dstpath = pathlib.Path(dst)
      # If local
      if monitorinfo['config']['endpointconfig'][filetypegroup]['save']['local']:
        dstpath.mkdir(parents=True, exist_ok=True)
        if binary:
          pass
        else:
          filepath = dstpath / f"{timestamp}{filename}{extension}.gz"
          if isgzip:
            with open(filepath, 'wb') as f:
              f.write(response.data)
          else:
            with gzip.open(filepath, 'wb') as f:
              f.write(response.data)
          logger.debug(f"Saved response to {filepath}")
      if monitorinfo['config']['endpointconfig'][filetypegroup]['save']['s3']:
        if binary:
          pass
        else:
          s3_key = f"{dst}/{timestamp}{filename}{extension}.gz"
          bucket = monitorinfo['settings']['aws']['bucket']
          # Prepare data for S3 upload
          if isgzip:
            body = response.data
          else:
            body = gzip.compress(response.data)
          # Queue S3 upload request (non-blocking)
          monitorinfo['s3_queue'].put((s3_key, body))
          logger.debug(f"Queued S3 upload to s3://{bucket}/{s3_key}")
  except Exception as e:
    logger.error(f"Error saving response. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


def initializemonitor(monitorinfo:dict, technology:str, renditionalias:str=''):
  if technology == 'dash':
    monitorinfo.update({
      'manifest': {
        'primary': {
          'foundlastsegment': False,
          'lastsegmentnotfoundcount': 0,
          'currentadbreak': {},
          'headers': {
            'manifestlastupdated': 0,
            'activeinput': None,
            'cmsd': {
              'n': None
            }
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
          },
          'consistency': {
            'previous': {
              'periods': []
            },
            'current': {
              'periods': []
            }
          },
          'periods': {}
        }
      }
    })
  elif technology == 'hls':
    monitorinfo['manifest'].update({
      renditionalias: {
        'mediasequence': 0,
        'foundlastsegment': False,
        'lastsegmentnotfoundcount': 0,
        'currentadbreak': {},
        'headers': {
          'manifestlastupdated': 0,
          'activeinput': None,
          'cmsd': {
            'n': None
          }
        },
        'new': {
          'segments': [],
          'duration': 0
        },
        'last': {
          'segment': {}
        },
        'buffer': {
          'window': {},
          'size': 20.0
        }
      }
    })


# Wait for a certain time
def wait(logger, starttime:float, duration:float):
  waittime = starttime - time.perf_counter() + duration
  if waittime > 0:
    time.sleep(waittime)
  else:
    time.sleep(random.uniform(0,5))
    logger.warning(f"Negative wait time between manifest requests, will back off")


def gettrackingevents(logger, ad):
  try:
    return [event.get('eventType') for event in ad.get('trackingEvents', [])]
  except Exception as e:
    logger.error(f"Encountered error when getting tracking events. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


# Read tracking response and capture avail information
def analysetracking(logger, monitorinfo:dict, response, playhead:int, init:bool):
  try:
    if response:
      # Find new ad breaks and update existing ones
      responsejson = json.loads(decoderesponse(response, True))
      if 'avails' in responsejson:
        for avail in responsejson['avails']:
          availid = avail.get('availId')
          if availid in monitorinfo['adbreaks'].keys():
            adbreak = monitorinfo['adbreaks'][availid]
            # Update tracking events
            for adbreakad in adbreak['ads']:
              if avail.get('ads'):
                for ad in avail['ads']:
                  if adbreakad['ad_id'] == ad.get('adId'):
                    new_tracking_events = gettrackingevents(logger, ad)
                    if adbreakad['tracking_events'] != new_tracking_events:
                      added = [e for e in new_tracking_events if e not in adbreakad['tracking_events']]
                      removed = [e for e in adbreakad['tracking_events'] if e not in new_tracking_events]
                      adbreakad['tracking_events'] = new_tracking_events
                      logger.info(f"Updated avail id {availid} ad id {adbreakad['ad_id']} tracking events, added: {added}, removed: {removed}")
                    break
          else:
            availstarttime = isodate.parse_duration(avail.get('startTime')).total_seconds()
            adbreak = {
              'observed': None,
              'type': 'regular',
              'avail_id': availid,
              'start_time_in_seconds': availstarttime,
              'filled_duration': avail.get('durationInSeconds'),
              'playhead': playhead,
              'playhead_delta': round(availstarttime - playhead, 3)
            }
            # Get advertised duration
            if avail.get('adMarkerDuration'):
              admarkderduration = isodate.parse_duration(avail.get('adMarkerDuration')).total_seconds()
              if admarkderduration > 0:
                adbreak['advertised_duration'] = admarkderduration
            # Get fill rate
            if adbreak['filled_duration'] and adbreak.get('advertised_duration'):
              adbreak['fill_rate'] = round(adbreak['filled_duration'] / adbreak['advertised_duration'], 3)
            # Get ads
            adbreak['ads'] = []
            if avail.get('ads'):
              for n, ad in enumerate(avail['ads']):
                # Get ad info
                adbreak['ads'].append({
                  'ad_id': ad.get('adId'),
                  'creative_id': ad.get('creativeId'),
                  'duration_in_seconds': ad.get('durationInSeconds'),
                  'tracking_events': gettrackingevents(logger, ad)
                })
                # Check first ad to find out if it's overlay
                if n == 0:
                  if 'mediaFiles' in ad.keys() and 'mediaFilesList' in ad['mediaFiles'].keys():
                    for mediafile in ad['mediaFiles']['mediaFilesList']:
                      if 'mediaType' in mediafile.keys() and mediafile['mediaType'] == 'null/null':
                        adbreak['type'] = 'overlay'
                        break
            # Update observed time
            if not init:
              adbreak['observed'] = f"{datetime.now(timezone.utc)}"
            # Log ad break
            summary = {k: adbreak[k] for k in ['type', 'start_time_in_seconds', 'filled_duration', 'fill_rate', 'playhead', 'playhead_delta'] if k in adbreak}
            if 'ads' in adbreak:
              summary['ads'] = len(adbreak['ads'])
            logger.info(f"Found {'new ' if not init else ''}avail id {availid} in tracking data: {json.dumps(summary)}")
            # Warn if ad break has no duration
            if not adbreak.get('advertised_duration') and monitorinfo['config']['endpointconfig']['validations']['custom']['check_ad_break_scte_duration']:
              logger.warning(f"Avail id {availid} has no duration", extra={'event': 'AD_BREAK_DURATION_NOT_FOUND'})
            # If this is new ad break
            if not init:
              # Compare ad break start time and playhead
              if monitorinfo['config']['endpointconfig']['validations']['custom']['check_ad_break_start_time']:
                if adbreak['playhead_delta'] + monitorinfo['config']['endpointconfig']['tracking']['frequency'] < 0:
                  logger.warning(f"Avail id {availid} has start time {adbreak['playhead_delta']} s in the past from current playhead", extra={'event': 'AD_BREAK_START_TIME_IN_PAST'})
              # Send metrics
              addmetric(logger, monitorinfo, 'Start', 1, 'Count', [{'Name': 'AdBreakType', 'Value': adbreak['type']}])
              if adbreak.get('advertised_duration'):
                addmetric(logger, monitorinfo, 'AdvertisedDuration', adbreak['advertised_duration'], 'Seconds', [{'Name': 'AdBreakType', 'Value': adbreak['type']}])
              if adbreak.get('fill_rate'):
                addmetric(logger, monitorinfo, 'FillRate', adbreak['fill_rate'], 'None', [{'Name': 'AdBreakType', 'Value': adbreak['type']}])
            # Update adbreaks
            monitorinfo['adbreaks'][availid] = adbreak
    # Go through ad breaks and validate the ones that completed
    for availid in monitorinfo['adbreaks'].keys():
      adbreak = monitorinfo['adbreaks'][availid]
      if not adbreak.get('ad_break_is_over'):
        # Check if ad break is over
        if adbreak['observed']:
          adbreakobserved = datetime.fromisoformat(adbreak['observed'])
          if (datetime.now(timezone.utc) - adbreakobserved).total_seconds() > adbreak.get('filled_duration', 0):
            adbreak['ad_break_is_over'] = True
            # Check for required tracking events
            required_events = set(monitorinfo['config']['endpointconfig']['validations']['custom']['required_tracking_events'])
            for adbreakad in adbreak['ads']:
              if required_events and not required_events.issubset(adbreakad['tracking_events']):
                logger.warning(f"Avail id {availid} ad id {adbreakad['ad_id']} has missing required tracking events: {required_events - set(adbreakad['tracking_events'])}", extra={'event': 'MISSING_REQUIRED_TRACKING_EVENTS'})
            logger.info(f"Completed avail id {availid} validations at ad break end")
  except Exception as e:
    logger.error(f"Encountered error when analysing tracking data. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


# Get tracking response
def tracking(logger, monitorinfo:dict, endpointconfig:dict):
  logging.config.dictConfig(monitorinfo['config']['logging'])
  monitorlogger = logging.getLogger('monitor')
  logger = getloggeradapterclass(monitorinfo['settings']['application']['json_logger'])(monitorlogger, {'type': monitorinfo['config']['type'], 'origin': monitorinfo['config']['origin'], 'workload': monitorinfo['config']['workload'], 'endpoint': monitorinfo['config']['endpoint'], 'technology': monitorinfo['config']['technology'], 'rendition': 'tracking'})
  if monitorinfo['config']['endpointconfig']['loglevel'] in loglevels.keys():
    logger.setLevel(loglevels[monitorinfo['config']['endpointconfig']['loglevel']])
  logger.info(f"Started monitoring tracking endpoint {endpointconfig['trackingurl']}")
  try:
    init = True
    while not monitorinfo['state']['stop'].is_set():
      starttime = time.perf_counter()
      if endpointconfig['tracking']['get']:
        trackingurl = endpointconfig['trackingurl']
        playhead = None
        # Calculate playhead
        if monitorinfo['config']['technology'] == 'dash':
          if 'availabilitystarttime' in monitorinfo['manifest']['primary'].keys():
            playhead = round((datetime.now(timezone.utc) - monitorinfo['manifest']['primary']['availabilitystarttime']).total_seconds())
        elif monitorinfo['config']['technology'] == 'hls':
          if 'primary' in monitorinfo['manifest'].keys() and 'contentdurationsincestart' in monitorinfo['manifest']['primary'].keys():
            playhead = round(monitorinfo['manifest']['primary']['contentdurationsincestart'])
        # Update tracking url
        if endpointconfig['tracking']['playhead']:
          if playhead:
            trackingurl = f"{trackingurl}?aws.playheadPositionInSeconds={playhead - endpointconfig['tracking']['playhead_delay']}"
          else:
            trackingurl = None
            logger.debug(f"Waiting for manifest content to calculate tracking playhead")
        # Request tracking
        if trackingurl:
          logger.debug(f"Requesting tracking")
          response = request(logger, 'GET', trackingurl, 'tracking', '', monitorinfo)
          # Analyse tracking
          if playhead:
            analysetracking(logger, monitorinfo, response, playhead, init)
            init = False
          # Save tracking
          if endpointconfig['tracking']['save']['local'] or endpointconfig['tracking']['save']['s3']:
            saveresponse(logger, response, monitorinfo, 'tracking', f"_playhead_{playhead - endpointconfig['tracking']['playhead_delay']}" if endpointconfig['tracking']['playhead'] else "", False, '')
      wait(logger, starttime, endpointconfig['tracking']['frequency'])
  except Exception as e:
    logger.error(f"Encountered error in tracking thread. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})
  finally:
    logger.info(f"Stopped tracking thread")


def checkforstaleness(logger, monitorinfo:dict, requesttime, renditionalias, renditionid):
  durationsum = 0.0
  todelete = []
  try:
    for timestamp, duration in monitorinfo['manifest'][renditionalias]['buffer']['window'].items():
      if timestamp > requesttime - monitorinfo['manifest'][renditionalias]['buffer']['size']:
        durationsum = durationsum + duration
      else:
        todelete.append(timestamp)
    durationsum = round(durationsum, 1)
    for timestamp in todelete:
      del monitorinfo['manifest'][renditionalias]['buffer']['window'][timestamp]
    # Send buffer metric
    dimensions = [{'Name': 'Rendition', 'Value': renditionid}] if monitorinfo['config']['technology'] == 'hls' else []
    addmetric(logger, monitorinfo, 'BufferFillDuration', durationsum, 'Seconds', dimensions)
    if durationsum == 0:
      logger.warning(f"Stale manifest", extra={'event': 'STALE_MANIFEST'})
  except Exception as e:
    logger.error(f"Error while checking for staleness. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


# Get manifest last updated header
def getmanifestlastupdated(response):
  manifestlastupdated = 0
  if 'X-MediaPackage-Manifest-Last-Updated' in response.headers:
    manifestlastupdated = int(response.headers['X-MediaPackage-Manifest-Last-Updated'])
  return manifestlastupdated


# Check manifest response headers
def checkresponseheaders(logger, monitorinfo, response, renditionalias='primary'):
  if 'X-Amzn-Mediapackage-Active-Input' in response.headers:
    activeinput = int(response.headers['X-Amzn-Mediapackage-Active-Input'])
    if monitorinfo['manifest'][renditionalias]['headers']['activeinput'] is not None:
      if monitorinfo['manifest'][renditionalias]['headers']['activeinput'] != activeinput:
        logger.warning(f"MediaPackage active input changed from {monitorinfo['manifest'][renditionalias]['headers']['activeinput']} to {activeinput}", extra={'event': 'ORIGIN_ACTIVE_INPUT_CHANGED'})
    monitorinfo['manifest'][renditionalias]['headers']['activeinput'] = activeinput
  if 'CMSD-Static' in response.headers:
    match = re.search('n="(.*?)"', response.headers['CMSD-Static'])
    if match:
      if monitorinfo['manifest'][renditionalias]['headers']['cmsd']['n'] is not None:
        if monitorinfo['manifest'][renditionalias]['headers']['cmsd']['n'] != match.group(1):
          logger.warning(f"MediaPackage endpoint changed from {monitorinfo['manifest'][renditionalias]['headers']['cmsd']['n']} to {match.group(1)}", extra={'event': 'ORIGIN_ENDPOINT_CHANGED'})
      monitorinfo['manifest'][renditionalias]['headers']['cmsd']['n'] = match.group(1)


# Calculate ad break duration delta
def updateadbreakdurationdelta(logger, monitorinfo, adbreakid, new):
  try:
    monitorinfo['adbreaks'][adbreakid]['segments_duration'] = round(monitorinfo['adbreaks'][adbreakid]['segments_duration'], 3)
    if monitorinfo['adbreaks'][adbreakid]['advertised_duration'] is not None and monitorinfo['adbreaks'][adbreakid]['advertised_duration'] > 0:
      monitorinfo['adbreaks'][adbreakid]['duration_delta'] = round(monitorinfo['adbreaks'][adbreakid]['segments_duration'] - monitorinfo['adbreaks'][adbreakid]['advertised_duration'], 3)
      if new:
        addmetric(logger, monitorinfo, 'DurationDelta', abs(monitorinfo['adbreaks'][adbreakid]['duration_delta']), 'Seconds', [{'Name': 'AdBreakType', 'Value': monitorinfo['adbreaks'][adbreakid]['type']}])
        if abs(monitorinfo['adbreaks'][adbreakid]['duration_delta']) > monitorinfo['config']['endpointconfig']['validations']['custom']['max_ad_break_duration_delta']:
          logger.warning(f"Ad break duration was {'longer' if monitorinfo['adbreaks'][adbreakid]['duration_delta'] > 0 else 'shorter'} than advertised by {abs(monitorinfo['adbreaks'][adbreakid]['duration_delta'])} seconds", extra={'event': 'AD_BREAK_DURATION_DELTA_BREACHED'})
  except Exception as e:
    logger.error(f"Error while getting ad break duration delta. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


# Respond with true if SCTE signal is one of ad break opportunity signals provided in the config file
def checkifadbreak(logger, monitorinfo:dict, event:dict):
  event.update({
    'is_opportunity': False,
    'type': 'regular'
  })
  try:
    # Check if SCTE message contains multiple descriptors
    if 'descriptors' in event['scte_message']['decoded'].keys():
      if len(event['scte_message']['decoded']['descriptors']) > 1:
        logger.warning(f"SCTE message contains multiple ({len(event['scte_message']['decoded']['descriptors'])}) segmentation descriptors: {event['scte_message']['decoded']['descriptors']}", extra={'event': 'MULTIPLE_SEGMENTATION_DESCRIPTORS'})
    for adbreaksignal in monitorinfo['config']['endpointconfig']['validations']['custom']['ad_break_scte_signals']:
      if not event['is_opportunity']:
        # Check for segmentation descriptors
        if isinstance(adbreaksignal, int) or adbreaksignal.isdigit():
          adbreaksignal = int(adbreaksignal)
          if 'descriptors' in event['scte_message']['decoded'].keys():
            for descriptor in event['scte_message']['decoded']['descriptors']:
              if 'segmentation_type' in descriptor.keys():
                if descriptor['segmentation_type'] == adbreaksignal:
                  event['is_opportunity'] = True
                  if descriptor['segmentation_type'] == 56:
                    event['type'] = 'overlay'
                  break
        elif adbreaksignal == 'splice_insert':
          # Check if splice insert
          if 'type' in event['scte_message']['decoded'].keys() and event['scte_message']['decoded']['type'] == 'splice_insert':
            if event['scte_message']['decoded'].get('out_of_network'):
              event['is_opportunity'] = True
              if 'descriptors' in event['scte_message']['decoded'].keys():
                for descriptor in event['scte_message']['decoded']['descriptors']:
                  if 'segmentation_type' in descriptor.keys():
                    if descriptor['segmentation_type'] == 56:
                      event['type'] = 'overlay'
                      break
  except Exception as e:
    logger.error(f"Error while checking if SCTE35 signal is ad break opportunity. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})