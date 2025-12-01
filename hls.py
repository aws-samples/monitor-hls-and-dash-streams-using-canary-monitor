import logging
import logging.config
import traceback
import utils
from urllib.parse import urljoin
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
    logger.error(f"Error parsing tag '{tag}' with value '{value}'. Exception: {str(e)} Traceback: {traceback.format_exc()}")


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
    logger.error(f"Error getting metadata tags. Exception: {str(e)} Traceback: {traceback.format_exc()}")
    raise


def resetsegmentinfo():
  return {
    'segmentduration': None,
    'pdttimestamp': None,
    'tags': []
  }


def getsegmentinfo(logger, renditionalias, responselines, monitorinfo:dict, allsegments:bool=False):
  try:
    mediasequence = monitorinfo['manifest'][renditionalias]['mediasequence']
    implicitpdttimestamp = None
    segmentinfo = resetsegmentinfo()
    for line in responselines:
      line = line.strip()
      if line.startswith('#'):
        tag, value = (line[1:].split(':', 1)) if ':' in line else (line[1:], '')
        segmentinfo['tags'].append((tag, value))
        if tag == 'EXTINF' and value:
          match = re.match(r'^\d*\.?\d+', value)
          if match:
            segmentinfo['segmentduration'] = float(match.group()) # type: ignore
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
            'name': line
          }
          if segmentinfo['segmentduration']:
            segment['dsec'] = round(segmentinfo['segmentduration'], 3)
            monitorinfo['manifest'][renditionalias]['new']['segments'].append(segment)
            if not allsegments:
              logger.debug(f"Found new segment: {utils.printdictionary(logger, segment)}")
          else:
            logger.warning(f"Segment {segment} has no duration")
        elif not allsegments:
          if mediasequence == monitorinfo['manifest'][renditionalias]['last']['segment']['msn']:
            monitorinfo['manifest'][renditionalias]['foundlastsegment'] = True
        if implicitpdttimestamp and segmentinfo['segmentduration']:
          implicitpdttimestamp += timedelta(seconds=segmentinfo['segmentduration'])
        mediasequence += 1
        segmentinfo = resetsegmentinfo()
  except Exception as e:
    logger.error(f"Error getting segment info. Exception: {str(e)} Traceback: {traceback.format_exc()}")


def startadbreak(logger, segment, monitorinfo:dict, new, segmentadbreakinfo:dict):
  try:
    adbreakinfo = {
      'observed': f"{datetime.now(timezone.utc)}" if new else None,
      'advertisedduration': segmentadbreakinfo['durationfromtag'],
      'segmentsduration': 0.0,
      'durationdelta': None,
      'type': segmentadbreakinfo['adbreaktype'],
      'scte': segmentadbreakinfo.get('decodedscte', None)
    }
    # Check for nested ad break
    if monitorinfo['manifest']['primary']['currentadbreak']:
      logger.warning(f"A001: New ad break started without proper ending of previous ad break")
      monitorinfo['reporting']['validations']['failures'].add('A001')
    if new:
      # Send metrics for ad break start and advertised duration if present
      utils.addmetric(logger, monitorinfo, 'Start', 1, 'Count', [{'Name': 'AdBreakType', 'Value': adbreakinfo['type']}])
      if adbreakinfo['advertisedduration'] is not None and adbreakinfo['advertisedduration'] > 0:
        utils.addmetric(logger, monitorinfo, 'AdvertisedDuration', adbreakinfo['advertisedduration'], 'Seconds', [{'Name': 'AdBreakType', 'Value': adbreakinfo['type']}])
      elif monitorinfo['config']['endpointconfig']['validations']['custom']['checkadbreakscteduration']:
        if monitorinfo['config']['origin'] != 'emt':
          logger.warning(f"A004: Ad break has no duration")
          monitorinfo['reporting']['validations']['failures'].add('A004')
    # Upddate reporting
    monitorinfo['reporting']['adbreaks'][segment['msn']] = adbreakinfo
    # Update current ad break
    monitorinfo['manifest']['primary']['currentadbreak'] = {'id': segment['msn'], 'daterangeid': segmentadbreakinfo.get('daterangeid', '')}
  except Exception as e:
    logger.error(f"Error at ad break start. Exception: {str(e)} Traceback: {traceback.format_exc()}")


def endadbreak(logger, segment, monitorinfo:dict, new):
  try:
    adbreakid = monitorinfo['manifest']['primary']['currentadbreak']['id']
    utils.updateadbreakdurationdelta(logger, monitorinfo, adbreakid, new)
    if new:
      # Send metric for ad break segments duration
      utils.addmetric(logger, monitorinfo, 'SegmentsDuration', monitorinfo['reporting']['adbreaks'][adbreakid]['segmentsduration'], 'Seconds', [{'Name': 'AdBreakType', 'Value': monitorinfo['reporting']['adbreaks'][adbreakid]['type']}])
    # Clear current ad break
    monitorinfo['manifest']['primary']['currentadbreak'] = {}
  except Exception as e:
    logger.error(f"Error at ad break end. Exception: {str(e)} Traceback: {traceback.format_exc()}")


def gothroughsegments(logger, renditionalias, renditionid, monitorinfo:dict, new:bool=False):
  try:
    for segment in monitorinfo['manifest'][renditionalias]['new']['segments']:
      # Go through segment tags
      for tag, value in segment['tags']:
        # Check for ad break on non EMT origins
        if renditionalias == 'primary':
          if monitorinfo['config']['origin'] != 'emt':
            if tag == 'EXT-X-CUE-OUT':
              segmentadbreakinfo = {
                'adbreaktype': 'regular',
                'daterange': False,
                'durationfromtag': parsetag(logger, tag, value)
              }
              startadbreak(logger, segment, monitorinfo, new, segmentadbreakinfo)
            elif tag == 'EXT-X-DATERANGE' and 'SCTE35-OUT=' in value:
              parsedtag = parsetag(logger, tag, value)
              duration = parsedtag.get('DURATION') if 'DURATION' in parsedtag.keys() else parsedtag.get('PLANNED-DURATION')
              segmentadbreakinfo = {
                'adbreaktype': 'regular',
                'daterange': True,
                'daterangeid': parsedtag.get('ID', ''),
                'durationfromtag': float(duration) if duration else None,
                'sctestring': parsedtag.get('SCTE35-OUT', None)
              }
              if segmentadbreakinfo['sctestring'] is None:
                startadbreak(logger, segment, monitorinfo, new, segmentadbreakinfo)
              else:
                segmentadbreakinfo['decodedscte'] = utils.decodesctestring(logger, segmentadbreakinfo['sctestring'])
                if utils.checkifsignalisadbreak(logger, monitorinfo, segmentadbreakinfo):
                  startadbreak(logger, segment, monitorinfo, new, segmentadbreakinfo)
                else:
                  logger.warning(f"A005: Found unexpected ad break start SCTE signal {segmentadbreakinfo['decodedscte']}, which is not one of {monitorinfo['config']['endpointconfig']['validations']['custom']['adbreaksctesignals']} from the config file")
                  monitorinfo['reporting']['validations']['failures'].add('A005')
            elif tag == 'EXT-X-CUE-IN':
              if monitorinfo['manifest']['primary']['currentadbreak']:
                endadbreak(logger, segment, monitorinfo, new)
            elif tag == 'EXT-X-DATERANGE' and 'SCTE35-OUT=' not in value:
              if monitorinfo['manifest']['primary']['currentadbreak']:
                parseddaterange = parsetag(logger, tag, value)
                if parseddaterange.get('ID', '') == monitorinfo['manifest']['primary']['currentadbreak']['daterangeid']:
                  endadbreak(logger, segment, monitorinfo, new)
        # Check for discontinuity
        if tag == 'EXT-X-DISCONTINUITY':
          if new:
            logger.warning(f"Discontinuity")
            utils.addmetric(logger, monitorinfo, 'Discontinuity', 1, 'Count', [{'Name': 'Rendition', 'Value': renditionid}])
      # Check for ad break on EMT origin
      if renditionalias == 'primary':
        if monitorinfo['config']['origin'] == 'emt':
          if monitorinfo['config']['endpointconfig']['manifests']['adsegmentprefix'] in segment['name']:
            if not monitorinfo['manifest']['primary']['currentadbreak']:
              info = {
                'adbreaktype': 'regular',
                'daterange': False,
                'durationfromtag': None
              }
              startadbreak(logger, segment, monitorinfo, new, info)
          else:
            if monitorinfo['manifest']['primary']['currentadbreak']:
              endadbreak(logger, segment, monitorinfo, new)
      if new:
        # Update new segments duration
        monitorinfo['manifest'][renditionalias]['new']['duration'] = monitorinfo['manifest'][renditionalias]['new']['duration'] + segment['dsec']
      # Update last segment
      monitorinfo['manifest'][renditionalias]['last']['segment'] = segment.copy()
      if renditionalias == 'primary':
        # Update content duration since start for tracking playhead
        monitorinfo['manifest']['primary']['contentdurationsincestart'] = monitorinfo['manifest']['primary'].setdefault('contentdurationsincestart', 0) + segment['dsec']
      # Update ad break segments duration
      if monitorinfo['manifest'][renditionalias]['currentadbreak']:
        adbreakid = monitorinfo['manifest'][renditionalias]['currentadbreak']['id']
        monitorinfo['reporting']['adbreaks'][adbreakid]['segmentsduration'] = monitorinfo['reporting']['adbreaks'][adbreakid]['segmentsduration'] + segment['dsec']
    if new:
      # Check if found last segment
      monitorinfo['manifest'][renditionalias]['lastsegmentnotfoundcount'] = 0 if monitorinfo['manifest'][renditionalias]['foundlastsegment'] else monitorinfo['manifest'][renditionalias]['lastsegmentnotfoundcount'] + 1
      if 0 < monitorinfo['manifest'][renditionalias]['lastsegmentnotfoundcount'] < 3:
        logger.warning(f"Last segment not found")
      elif monitorinfo['manifest'][renditionalias]['lastsegmentnotfoundcount'] == 3:
        monitorinfo['state']['restart'] = (True, 'Last segment not found in 3 consecutive manifest requests')
      if renditionalias == 'primary':
        # Check PDT delta
        if monitorinfo['manifest'][renditionalias]['last']['segment']['pdt']:
          pdtdelta = round((monitorinfo['manifest'][renditionalias]['last']['segment']['pdt'] - datetime.now(timezone.utc)).total_seconds())
          utils.addmetric(logger, monitorinfo, 'PdtDelta', pdtdelta, 'Seconds', [])
  except Exception as e:
    logger.error(f"Error going through segments. Exception: {str(e)} Traceback: {traceback.format_exc()}")


def monitor(renditionid, url:str, rendition:dict, monitorinfo:dict, primary:bool):
  logging.config.dictConfig(monitorinfo['config']['logging'])
  monitorlogger = logging.getLogger('monitor')
  logger = logging.LoggerAdapter(monitorlogger, {'type': monitorinfo['config']['type'], 'origin': monitorinfo['config']['origin'], 'workload': monitorinfo['config']['workload'], 'endpoint': monitorinfo['config']['endpoint'], 'technology': monitorinfo['config']['technology'], 'rendition': renditionid})
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
      if monitorinfo['config']['endpointconfig']['manifests']['save']['local']:
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
              getsegmentinfo(logger, renditionalias, responselines, monitorinfo, True)
              gothroughsegments(logger, renditionalias, renditionid, monitorinfo)
            else:
              getsegmentinfo(logger, renditionalias, responselines, monitorinfo)
              gothroughsegments(logger, renditionalias, renditionid, monitorinfo, True)
          monitorinfo['manifest'][renditionalias]['headers']['manifestlastupdated'] = manifestlastupdated
        # Update new duration
        monitorinfo['manifest'][renditionalias]['buffer']['window'][requesttime] = monitorinfo['manifest'][renditionalias]['new']['duration']
        # Check for staleness
        if requesttime - monitorinfo['state']['starttimeperf'] > max(monitorinfo['manifest'][renditionalias]['buffer']['size'], monitorinfo['config']['endpointconfig']['manifests']['frequency']):
          utils.checkforstaleness(logger, monitorinfo, requesttime, renditionalias, renditionid)
      # Wait
      utils.wait(logger, requesttime, monitorinfo['config']['endpointconfig']['manifests']['frequency'])
  except Exception as e:
    logger.error(f"Encountered error while monitoring. Exception: {str(e)} Traceback: {traceback.format_exc()}")
  finally:
    logger.info(f"Stopped monitoring")


def startthreads(logger, monitorinfo:dict, response):
  renditions = {
    'video': {},
    'audio': {},
    'subtitles': {}
  }
  try:
    lines = response.splitlines()
    for i, line in enumerate(lines):
      line = line.strip()
      # Identify video renditions
      if line.startswith('#EXT-X-STREAM-INF:'):
        parts = re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', line[len('#EXT-X-STREAM-INF:'):])
        attrs = {k.strip(): v.strip().strip('"') for kv in parts if '=' in kv for k, v in [kv.split('=', 1)]}
        if i + 1 < len(lines) and not lines[i + 1].strip().startswith('#'):
          url = urljoin(monitorinfo['config']['endpointconfig']['manifesturl'], lines[i + 1].strip())
          rendition = {
            'index': len(renditions['video']) + 1,
            'media': "video",
            'bandwidth': int(attrs.get('BANDWIDTH', 0))
          }
          if url not in renditions['video'].keys():
            renditions['video'][url] = rendition
      # Identify audio and subtitles
      elif line.startswith('#EXT-X-MEDIA:'):
        parts = re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', line[len('#EXT-X-MEDIA:'):])
        attrs = {k.strip(): v.strip().strip('"') for kv in parts if '=' in kv for k, v in [kv.split('=', 1)]}
        media = attrs.get('TYPE', '').lower()
        uri = attrs.get('URI', '').strip()
        if media and media in {'audio', 'subtitles', 'video'} and uri:
          url = urljoin(monitorinfo['config']['endpointconfig']['manifesturl'], uri)
          rendition = {
            'index': len(renditions[media]) + 1,
            'media': media
          }
          if url not in renditions[media].keys():
            renditions[media][url] = rendition
    logger.debug(f"Found {len(renditions['video'])} video, {len(renditions['audio'])} audio and {len(renditions['subtitles'])} subtitle renditions: {renditions}")
    # Start threads
    primary = True
    activerenditions = []
    for renditionstring in monitorinfo['config']['endpointconfig']['manifests']['hlsrenditions']:
      if renditionstring:
        for media in renditions.keys():
          if media.startswith(renditionstring) or renditionstring == '*':
            for url, rendition in renditions[media].items():
              renditionid = f"{media[0]}{rendition['index']}"
              if renditionid not in activerenditions:
                activerenditions.append(renditionid)
                monitorinfo['state']['threads'][renditionid] = threading.Thread(target=monitor, args=(renditionid, url, rendition, monitorinfo, primary))
                monitorinfo['state']['threads'][renditionid].start()
                primary = False
                if renditionstring != '*':
                  break
    # Update shared object with main process to inform about renditions
    monitorinfo['config']['sharedwithmain'][(monitorinfo['config']['type'], monitorinfo['config']['technology'], monitorinfo['config']['workload'], monitorinfo['config']['endpoint'], monitorinfo['config']['origin'])] = {'hlsrenditions': activerenditions}
  except Exception as e:
    logger.error(f"Error starting threads. Exception: {str(e)} Traceback: {traceback.format_exc()}")


# Stop and start new HLS monitoring treads
def restartthreads(logger, monitorinfo:dict, response):
  try:
    if monitorinfo['state']['restart'][1]:
      logger.info(f"Restarting monitoring, reason: {monitorinfo['state']['restart'][1]}")
    # Stop HLS monitoring threads
    monitorinfo['state']['stop'].set()
    for thread in monitorinfo['state']['threads'].keys():
      if thread != 'tracking':
        monitorinfo['state']['threads'][thread].join()
    monitorinfo['state']['stop'].clear()
    # Start new HLS monitoring threads
    startthreads(logger, monitorinfo, response)
    # Clear state
    monitorinfo['state']['restart'] = (False, '')
  except Exception as e:
    logger.error(f"Error restarting threads. Exception: {str(e)} Traceback: {traceback.format_exc()}")

