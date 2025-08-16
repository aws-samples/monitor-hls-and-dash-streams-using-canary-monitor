import logging
import logging.config
import traceback
import utils
from urllib.parse import urljoin
import threading
import time
import re

def getmetadatatags(logger, responselines):
  metadata = {}
  try:
    for line in responselines:
      line = line.strip()
      if line.startswith('#'):
        if ':' in line:
          tag, value = line[1:].split(':', 1)
          metadata[tag] = int(value) if re.fullmatch(r"-?\d+", value) else value
      else:
        break
    return metadata
  except Exception as e:
    logger.error(f"Error getting metadata tags. Exception: {str(e)} Traceback: {traceback.format_exc()}")
    raise


def getsegmentinfo(logger, responselines, monitorinfo:dict, allsegments):
  try:
    for line in responselines:
      line = line.strip()
      if line.startswith('#'):
        pass
      else:
        pass
  except Exception as e:
    logger.error(f"Error getting metadata tags. Exception: {str(e)} Traceback: {traceback.format_exc()}")


def monitor(renditionid, url:str, rendition:dict, monitorinfo:dict, primary:bool):
  logging.config.dictConfig(monitorinfo['config']['logging'])
  monitorlogger = logging.getLogger('monitor')
  logger = logging.LoggerAdapter(monitorlogger, {'type': monitorinfo['config']['type'], 'origin': monitorinfo['config']['origin'], 'workload': monitorinfo['config']['workload'], 'endpoint': monitorinfo['config']['endpoint'], 'technology': monitorinfo['config']['technology'], 'rendition': renditionid})
  if monitorinfo['config']['endpointconfig']['loglevel'] in utils.loglevels.keys():
    logger.setLevel(utils.loglevels[monitorinfo['config']['endpointconfig']['loglevel']])
  logger.info(f"Started monitoring origin endpoint {url}")
  try:
    while not monitorinfo['state']['stop'].is_set():
      requesttime = time.perf_counter()
      logger.debug(f"Requesting manifest")
      response = utils.request(logger, 'GET', url, 'manifest', renditionid, monitorinfo)
      # Save manifest response
      if monitorinfo['config']['endpointconfig']['manifests']['save']['local']:
        utils.saveresponse(logger, response, monitorinfo, 'manifests', "", False, renditionid)
      if monitorinfo['config']['endpointconfig']['validations']['perform']:
        manifestlastupdated = utils.getmanifestlastupdated(response)
        if manifestlastupdated != monitorinfo['manifest']['primary']['headers']['manifestlastupdated'] or manifestlastupdated == 0:
          responselines = utils.decoderesponse(response, True).splitlines()
          metadatatags = getmetadatatags(logger, responselines)
          if not monitorinfo['manifest']['primary']['last']['segment']:
            getsegmentinfo(logger, responselines, monitorinfo, True)
          else:
            pass
        monitorinfo['manifest']['primary']['headers']['manifestlastupdated'] = manifestlastupdated
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


# Start new HLS monitoring treads
def restartthreads(logger, monitorinfo:dict, response):
  try:
    if monitorinfo['manifest']['multi']['lasthash']:
      logger.warning(f"Manifest has changed, will restart monitoring threads")
    # Stop HLS monitoring threads
    monitorinfo['state']['stop'].set()
    for thread in monitorinfo['state']['threads'].keys():
      if thread != 'tracking':
        monitorinfo['state']['threads'][thread].join()
    monitorinfo['state']['stop'].clear()
    # Start new HLS monitoring threads
    startthreads(logger, monitorinfo, response)
  except Exception as e:
    logger.error(f"Error restarting threads. Exception: {str(e)} Traceback: {traceback.format_exc()}")

