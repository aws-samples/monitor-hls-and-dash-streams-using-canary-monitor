import logging
import logging.config
import os
from loggeradapter import getloggeradapterclass
import traceback
import utils
from urllib.parse import urljoin, urlparse
import threading
import time
import re
from datetime import datetime, timezone, timedelta


def parsetag(logger, tag:str, value:str):
  try:
    if tag == 'EXTINF':
      match = re.match(r'^\d*\.?\d+', value)
      if match:
        return float(match.group())
      raise ValueError()
    elif tag == 'EXT-X-CUE-OUT':
      match = re.match(r'^(?:DURATION=)?([0-9]*\.?[0-9]+)?$', value)
      if match:
        return float(match.group(1)) if match.group(1) else None
      raise ValueError()
    elif tag == 'EXT-X-DATERANGE':
      parts = re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', value)
      attrs = {k.strip(): v.strip().strip('"') for kv in parts if '=' in kv for k, v in [kv.split('=', 1)]}
      return attrs
    else:
      return value
  except Exception as e:
    logger.error(f"Error parsing tag '{tag}' with value '{value}'. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'MANIFEST_PARSE_ERROR'})


def getmetadatatags(logger, renditionalias, responselines, monitorinfo:dict):
  try:
    for line in responselines:
      line = line.strip()
      if line.startswith('#'):
        tag, value = (line[1:].split(':', 1)) if ':' in line else (line[1:], None)
        if tag == 'EXT-X-MEDIA-SEQUENCE' and value and re.fullmatch(r"-?\d+", value):
          monitorinfo['manifest'][renditionalias]['mediasequence'] = int(value)
      else:
        break
  except Exception as e:
    logger.error(f"Error getting metadata tags. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'MANIFEST_PARSE_ERROR'})
    raise


def resetsegmentinfo():
  return {
    'segmentduration': None,
    'pdttimestamp': None,
    'tags': []
  }


def getsegmentinfo(logger, renditionid, renditionalias, responselines, monitorinfo:dict, allsegments:bool=False):
  try:
    mediasequence = monitorinfo['manifest'][renditionalias]['mediasequence']
    implicitpdttimestamp = None
    segmentinfo = resetsegmentinfo()
    manifestduration = 0.0
    # Go through all lines in manifest file
    for line in responselines:
      line = line.strip()
      if line.startswith('#'):
        tag, value = (line[1:].split(':', 1)) if ':' in line else (line[1:], '')
        segmentinfo['tags'].append((tag, value))
        if tag == 'EXTINF' and value:
          match = re.match(r'^\d*\.?\d+', value)
          if match:
            segmentinfo['segmentduration'] = float(match.group()) # type: ignore
            manifestduration += segmentinfo['segmentduration']
        elif tag == 'EXT-X-PROGRAM-DATE-TIME':
          if value.endswith('Z'):
            value = value[:-1] + '+00:00'
          segmentinfo['pdttimestamp'] = datetime.fromisoformat(value) # type: ignore
          implicitpdttimestamp = segmentinfo['pdttimestamp']
      elif line:
        if monitorinfo['manifest'][renditionalias]['foundlastsegment'] or allsegments:
          segment = {
            'msn': mediasequence,
            'pdt': implicitpdttimestamp,
            'tags': segmentinfo['tags'],
            'uri': line,
            'name': os.path.basename(urlparse(line).path)
          }
          if segmentinfo['segmentduration']:
            segment['dsec'] = round(segmentinfo['segmentduration'], 3)
            monitorinfo['manifest'][renditionalias]['new']['segments'].append(segment)
            if not allsegments:
              logger.debug(f"Found new segment: {utils.printdictionary(logger, segment)}")
          else:
            logger.warning(f"Segment has no duration, segment: {segment}", extra={'event': 'NON_COMPLIANT_MANIFEST'})
            continue
        elif not allsegments:
          if mediasequence == monitorinfo['manifest'][renditionalias]['last']['segment']['msn']:
            monitorinfo['manifest'][renditionalias]['foundlastsegment'] = True
            if line.split('?')[0] != monitorinfo['manifest'][renditionalias]['last']['segment']['uri'].split('?')[0]:
              logger.warning(f"URI of segment with sequence id {mediasequence} has changed, previously: {monitorinfo['manifest'][renditionalias]['last']['segment']['uri']}, now: {line}", extra={'event': 'LAST_SEGMENT_CHANGED'})
        if implicitpdttimestamp and segmentinfo['segmentduration']:
          implicitpdttimestamp += timedelta(seconds=segmentinfo['segmentduration'])
        mediasequence += 1
        segmentinfo = resetsegmentinfo()
    # Send metric for manifest duration
    utils.addmetric(logger, monitorinfo, 'ManifestDuration', round(manifestduration), 'Seconds', [{'Name': 'Rendition', 'Value': renditionid}])
  except Exception as e:
    logger.error(f"Error getting segment info. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


def startadbreak(logger, segment, monitorinfo:dict, new, adbreak:dict):
  try:
    # Check for back to back ad break
    if monitorinfo['manifest']['primary']['currentadbreak']:
      logger.warning(f"Back to back ad break without proper closure of previous ad break", extra={'event': 'BACK_TO_BACK_AD_BREAK'})
      endadbreak(logger, segment, monitorinfo, new)
    # Update current ad break
    monitorinfo['manifest']['primary']['currentadbreak'] = {'id': segment['msn'], 'daterange_id': adbreak.get('daterange_id', '')}
    # Update ad breaks info
    monitorinfo['adbreaks'][segment['msn']] = adbreak
    if new:
      # Send metrics for ad break start and advertised duration if present
      utils.addmetric(logger, monitorinfo, 'Start', 1, 'Count', [{'Name': 'AdBreakType', 'Value': adbreak['type']}])
      if adbreak.get('advertised_duration') and adbreak['advertised_duration'] > 0:
        utils.addmetric(logger, monitorinfo, 'AdvertisedDuration', adbreak['advertised_duration'], 'Seconds', [{'Name': 'AdBreakType', 'Value': adbreak['type']}])
      elif monitorinfo['config']['endpointconfig']['validations']['custom']['check_ad_break_scte_duration']:
        logger.warning(f"Ad break has no duration", extra={'event': 'AD_BREAK_DURATION_NOT_FOUND'})
  except Exception as e:
    logger.error(f"Error at ad break start. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


def endadbreak(logger, segment, monitorinfo:dict, new):
  try:
    currentadbkreakid = monitorinfo['manifest']['primary']['currentadbreak'].get('id')
    if currentadbkreakid:
      utils.updateadbreakdurationdelta(logger, monitorinfo, currentadbkreakid, new)
    if new:
      # Send metric for ad break segments duration
      utils.addmetric(logger, monitorinfo, 'SegmentsDuration', monitorinfo['adbreaks'][currentadbkreakid]['segments_duration'], 'Seconds', [{'Name': 'AdBreakType', 'Value': monitorinfo['adbreaks'][currentadbkreakid]['type']}])
    # Clear current ad break
    monitorinfo['manifest']['primary']['currentadbreak'] = {}
  except Exception as e:
    logger.error(f"Error at ad break end. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


def gothroughsegments(logger, renditionalias, renditionid, monitorinfo:dict, new:bool=False):
  try:
    for segment in monitorinfo['manifest'][renditionalias]['new']['segments']:
      # Go through segment tags
      for tag, value in segment['tags']:
        # Check for ad break on non DAI origins
        if renditionalias == 'primary':
          if monitorinfo['config']['isdai']:
            pass
          else:
            if tag == 'EXT-X-CUE-OUT':
              base64 = ''
              for t, v in segment['tags']:
                if t == 'EXT-OATCLS-SCTE35':
                  base64 = v
                  break
              adbreak = {
                'observed': f"{datetime.now(timezone.utc)}" if new else None,
                'scte_message': {
                  'raw': base64,
                  'decoded': utils.decodesctestring(logger, base64)
                },
                'advertised_duration': parsetag(logger, tag, value),
                'segments_duration': 0.0
              }
              utils.checkifadbreak(logger, monitorinfo, adbreak)
              startadbreak(logger, segment, monitorinfo, new, adbreak)
            elif tag == 'EXT-X-CUE-IN':
              if monitorinfo['manifest']['primary']['currentadbreak']:
                endadbreak(logger, segment, monitorinfo, new)
            elif tag == 'EXT-X-DATERANGE':
              parseddaterange = parsetag(logger, tag, value)
              sctestring = parseddaterange.get('SCTE35-OUT', None)
              if sctestring is not None:
                if monitorinfo['manifest']['primary']['currentadbreak'].get('id') != segment['msn']:
                  duration = parseddaterange.get('DURATION') if 'DURATION' in parseddaterange.keys() else parseddaterange.get('PLANNED-DURATION')
                  adbreak = {
                    'observed': f"{datetime.now(timezone.utc)}" if new else None,
                    'scte_message': {
                      'raw': sctestring,
                      'decoded': utils.decodesctestring(logger, sctestring)
                    },
                    'advertised_duration': float(duration) if duration else None,
                    'segments_duration': 0.0,
                    'daterange_id': parseddaterange.get('ID', '')
                  }
                  utils.checkifadbreak(logger, monitorinfo, adbreak)
                  startadbreak(logger, segment, monitorinfo, new, adbreak)
              elif 'SCTE35-IN=' in value:
                if monitorinfo['manifest']['primary']['currentadbreak']:
                  if parseddaterange.get('ID', '') == monitorinfo['manifest']['primary']['currentadbreak']['daterange_id']:
                    endadbreak(logger, segment, monitorinfo, new)
        # Check for discontinuity
        if tag == 'EXT-X-DISCONTINUITY':
          ad_break_boundary = False
          if new:
            if monitorinfo['config']['isdai']:
              segment_name = os.path.basename(urlparse(segment['uri']).path)
              if monitorinfo['config']['endpointconfig']['manifests']['ad_segment_prefix'] in segment['name'] or monitorinfo['config']['endpointconfig']['manifests']['ad_segment_prefix'] in monitorinfo['manifest'][renditionalias]['last']['segment']['name']:
                ad_break_boundary = True
            if ad_break_boundary:
              logger.debug(f"Discontinuity on ad break boundary")
            else:
              logger.warning(f"Discontinuity", extra={'event': 'DISCONTINUITY'})
              utils.addmetric(logger, monitorinfo, 'Discontinuity', 1, 'Count', [{'Name': 'Rendition', 'Value': renditionid}])
      # Check for ad break on EMT origin
      # if renditionalias == 'primary':
      #   if monitorinfo['config']['origin'] == 'emt':
      #     if monitorinfo['config']['endpointconfig']['manifests']['ad_segment_prefix'] in segment['name']:
      #       if not monitorinfo['manifest']['primary']['currentadbreak']:
      #         adbreak = {
      #           'observed': f"{datetime.now(timezone.utc)}" if new else None,
      #           'advertised_duration': None,
      #           'segments_duration': 0.0,
      #           'type': 'regular'
      #         }
      #         startadbreak(logger, segment, monitorinfo, new, adbreak)
      #     else:
      #       if monitorinfo['manifest']['primary']['currentadbreak']:
      #         endadbreak(logger, segment, monitorinfo, new)
      if new:
        # Update new segments duration
        monitorinfo['manifest'][renditionalias]['new']['duration'] = monitorinfo['manifest'][renditionalias]['new']['duration'] + segment['dsec']
        # Send metric for segment duration
        utils.addmetric(logger, monitorinfo, 'SegmentDuration', segment['dsec'], 'Seconds', [{'Name': 'Rendition', 'Value': renditionid}])
      # Update last segment
      monitorinfo['manifest'][renditionalias]['last']['segment'] = segment.copy()
      if renditionalias == 'primary':
        # Update content duration since start for tracking playhead
        monitorinfo['manifest']['primary']['contentdurationsincestart'] = monitorinfo['manifest']['primary'].setdefault('contentdurationsincestart', 0) + segment['dsec']
      # Update ad break segments duration
      if renditionalias == 'primary':
        if monitorinfo['manifest']['primary']['currentadbreak']:
          adbreakid = monitorinfo['manifest']['primary']['currentadbreak']['id']
          monitorinfo['adbreaks'][adbreakid]['segments_duration'] = monitorinfo['adbreaks'][adbreakid]['segments_duration'] + segment['dsec']
    if new:
      # Check if found last segment
      monitorinfo['manifest'][renditionalias]['lastsegmentnotfoundcount'] = 0 if monitorinfo['manifest'][renditionalias]['foundlastsegment'] else monitorinfo['manifest'][renditionalias]['lastsegmentnotfoundcount'] + 1
      if 0 < monitorinfo['manifest'][renditionalias]['lastsegmentnotfoundcount'] < 3:
        logger.warning(f"Last segment not found", extra={'event': 'LAST_SEGMENT_NOT_FOUND'})
      elif monitorinfo['manifest'][renditionalias]['lastsegmentnotfoundcount'] == 3:
        logger.warning(f"Last segment not found, restarting", extra={'event': 'LAST_SEGMENT_NOT_FOUND'})
        monitorinfo['state']['status'] = 'init'
      if renditionalias == 'primary':
        # Check PDT delta
        if monitorinfo['manifest'][renditionalias]['last']['segment']['pdt']:
          pdtdelta = round((monitorinfo['manifest'][renditionalias]['last']['segment']['pdt'] - datetime.now(timezone.utc)).total_seconds())
          utils.addmetric(logger, monitorinfo, 'PdtDelta', pdtdelta, 'Seconds', [])
  except Exception as e:
    logger.error(f"Error going through segments. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


def monitor(renditionid, url:str, rendition:dict, monitorinfo:dict, primary:bool):
  logging.config.dictConfig(monitorinfo['config']['logging'])
  monitorlogger = logging.getLogger('monitor')
  logger = getloggeradapterclass(monitorinfo['settings']['application']['json_logger'])(monitorlogger, {'type': monitorinfo['config']['type'], 'origin': monitorinfo['config']['origin'], 'workload': monitorinfo['config']['workload'], 'endpoint': monitorinfo['config']['endpoint'], 'technology': monitorinfo['config']['technology'], 'rendition': renditionid})
  if monitorinfo['config']['endpointconfig']['loglevel'] in utils.loglevels.keys():
    logger.setLevel(utils.loglevels[monitorinfo['config']['endpointconfig']['loglevel']])
  logger.info(f"Started monitoring origin endpoint {url}")
  renditionalias = 'primary' if primary else renditionid
  utils.initializemonitor(monitorinfo, 'hls', renditionalias)
  try:
    while not monitorinfo['state']['stop'].is_set():
      requesttime = time.perf_counter()
      # Clear state
      monitorinfo['manifest'][renditionalias]['foundlastsegment'] = False
      monitorinfo['manifest'][renditionalias]['new']['segments'].clear()
      monitorinfo['manifest'][renditionalias]['new']['duration'] = 0
      # Request manifest
      logger.debug(f"Requesting manifest")
      response = utils.request(logger, 'GET', url, 'manifest', renditionid, monitorinfo)
      # Save manifest response
      if monitorinfo['config']['endpointconfig']['manifests']['save']['local'] or monitorinfo['config']['endpointconfig']['manifests']['save']['s3']:
        utils.saveresponse(logger, response, monitorinfo, 'manifests', "", False, renditionid)
      if monitorinfo['config']['endpointconfig']['validations']['perform']:
        if response:
          # Perform validations
          utils.checkresponseheaders(logger, monitorinfo, response, renditionalias)
          manifestlastupdated = utils.getmanifestlastupdated(response)
          if manifestlastupdated != monitorinfo['manifest'][renditionalias]['headers']['manifestlastupdated'] or manifestlastupdated == 0:
            responselines = utils.decoderesponse(response, True).splitlines()
            getmetadatatags(logger, renditionalias, responselines, monitorinfo)
            if not monitorinfo['manifest'][renditionalias]['last']['segment']:
              getsegmentinfo(logger, renditionid, renditionalias, responselines, monitorinfo, True)
              gothroughsegments(logger, renditionalias, renditionid, monitorinfo)
            else:
              getsegmentinfo(logger, renditionid, renditionalias, responselines, monitorinfo)
              gothroughsegments(logger, renditionalias, renditionid, monitorinfo, True)
          monitorinfo['manifest'][renditionalias]['headers']['manifestlastupdated'] = manifestlastupdated
        # Update new duration
        monitorinfo['manifest'][renditionalias]['buffer']['window'][requesttime] = monitorinfo['manifest'][renditionalias]['new']['duration']
        # Check for staleness
        if requesttime - monitorinfo['state']['starttimeperf'] > max(monitorinfo['manifest'][renditionalias]['buffer']['size'], monitorinfo['config']['endpointconfig']['manifests']['frequency']):
          utils.checkforstaleness(logger, monitorinfo, requesttime, renditionalias, renditionid)
      # Wait - use stop event so thread exits promptly when told to stop
      waittime = requesttime - time.perf_counter() + monitorinfo['config']['endpointconfig']['manifests']['frequency']
      if waittime > 0:
        monitorinfo['state']['stop'].wait(timeout=waittime)
  except Exception as e:
    logger.error(f"Encountered error while monitoring. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})
  finally:
    logger.info(f"Stopped monitoring")


def startthreads(logger, monitorinfo:dict, response, info):
  renditions = {
    'video': {},
    'audio': {},
    'subtitles': {}
  }
  try:
    # Log start reason if present
    if info.get('reason'):
      logger.info(f"Restarting monitoring due to {info['reason']}")
    # Stop active rendition threads if any are running
    monitorinfo['state']['stop'].set()
    for thread in monitorinfo['state']['threads'].keys():
      if thread != 'tracking':
        monitorinfo['state']['threads'][thread].join()
    monitorinfo['state']['stop'].clear()
    # Go through manifest and start monitoring renditions
    lines = response.splitlines()
    for i, line in enumerate(lines):
      line = line.strip()
      # Identify video renditions
      if line.startswith('#EXT-X-STREAM-INF:'):
        parts = re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', line[len('#EXT-X-STREAM-INF:'):])
        attrs = {k.strip(): v.strip().strip('"') for kv in parts if '=' in kv for k, v in [kv.split('=', 1)]}
        if i + 1 < len(lines) and not lines[i + 1].strip().startswith('#'):
          url = urljoin(monitorinfo['config']['endpointconfig']['manifesturl'], lines[i + 1].strip())
          if url not in renditions['video'].keys():
            rendition = {
              'index': len(renditions['video']) + 1,
              'media': "video",
              'bandwidth': int(attrs.get('BANDWIDTH', 0)),
              'averagebandwidth': int(attrs.get('AVERAGE-BANDWIDTH', 0)),
              'resolution': attrs.get('RESOLUTION', ''),
              'framerate': float(attrs.get('FRAME-RATE', 0)),
              'videorange': attrs.get('VIDEO-RANGE', ''),
              'codecs': attrs.get('CODECS', ''),
              'audio': attrs.get('AUDIO', ''),
              'ismonitored': False
            }
            renditions['video'][url] = rendition
          else:
            existing = renditions['video'][url]
            # Convert to list and append for these fields
            for field, new_val in [('bandwidth', int(attrs.get('BANDWIDTH', 0))), ('averagebandwidth', int(attrs.get('AVERAGE-BANDWIDTH', 0))), ('codecs', attrs.get('CODECS', '')), ('audio', attrs.get('AUDIO', ''))]:
              if not isinstance(existing[field], list):
                existing[field] = [existing[field]]
              existing[field].append(new_val)
      # Identify audio and subtitles
      elif line.startswith('#EXT-X-MEDIA:'):
        parts = re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', line[len('#EXT-X-MEDIA:'):])
        attrs = {k.strip(): v.strip().strip('"') for kv in parts if '=' in kv for k, v in [kv.split('=', 1)]}
        media = attrs.get('TYPE', '').lower()
        uri = attrs.get('URI', '').strip()
        if media and media in {'audio', 'subtitles', 'video'} and uri:
          url = urljoin(monitorinfo['config']['endpointconfig']['manifesturl'], uri)
          if media == 'video':
            rendition = {
              'index': len(renditions[media]) + 1,
              'media': media,
              'bandwidth': int(attrs.get('BANDWIDTH', 0)),
              'averagebandwidth': int(attrs.get('AVERAGE-BANDWIDTH', 0)),
              'resolution': attrs.get('RESOLUTION', ''),
              'framerate': float(attrs.get('FRAME-RATE', 0)),
              'videorange': attrs.get('VIDEO-RANGE', ''),
              'codecs': attrs.get('CODECS', ''),
              'audio': attrs.get('AUDIO', ''),
              'ismonitored': False
            }
          else:
            rendition = {
              'index': len(renditions[media]) + 1,
              'media': media,
              'language': attrs.get('LANGUAGE', ''),
              'name': attrs.get('NAME', ''),
              'channels': attrs.get('CHANNELS', ''),
              'groupid': attrs.get('GROUP-ID', ''),
              'ismonitored': False
            }
          if url not in renditions[media].keys():
            renditions[media][url] = rendition
    logger.info(f"Found {len(renditions['video'])} video, {len(renditions['audio'])} audio and {len(renditions['subtitles'])} subtitles renditions: {renditions}")
    # Check if required renditions are present
    for requiredtype in monitorinfo['config']['endpointconfig']['validations']['custom']['required_renditions']:
      if len(renditions.get(requiredtype, {})) == 0:
        logger.warning(f"Required rendition '{requiredtype}' not found", extra={'event': 'RENDITION_NOT_FOUND'})
    # Start threads
    primary = True
    activerenditions = []
    for renditionstring in monitorinfo['config']['endpointconfig']['manifests']['hls_renditions']:
      if renditionstring:
        for media in renditions.keys():
          if media.startswith(renditionstring) or renditionstring == '*':
            for url, rendition in renditions[media].items():
              renditionid = f"{media[0]}{rendition['index']}"
              if renditionid not in activerenditions:
                activerenditions.append(renditionid)
                monitorinfo['state']['threads'][renditionid] = threading.Thread(target=monitor, args=(renditionid, url, rendition, monitorinfo, primary))
                monitorinfo['state']['threads'][renditionid].start()
                rendition['ismonitored'] = True
                primary = False
                if renditionstring != '*':
                  break
    # Update shared object with main process to inform about renditions
    monitorinfo['config']['sharedwithmain'][(monitorinfo['config']['type'], monitorinfo['config']['technology'], monitorinfo['config']['workload'], monitorinfo['config']['endpoint'], monitorinfo['config']['origin'], monitorinfo['config']['isdai'])] = {'hls_renditions': activerenditions}
    # Update renditions for report
    monitorinfo['manifest']['multi']['renditions'] = renditions
  except Exception as e:
    logger.error(f"Error starting threads. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})

