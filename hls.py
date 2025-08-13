import logging
import logging.config
import traceback
import utils
import m3u8
from urllib.parse import urljoin
import threading
import time


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
    # Identify renditions
    multiplaylist = m3u8.loads(response)
    # Video
    for playlist in multiplaylist.playlists:
      if playlist.uri:
        url = urljoin(monitorinfo['config']['endpointconfig']['manifesturl'], playlist.uri)
        rendition = {
          'index': len(renditions['video']) + 1,
          'media': 'video',
          'bandwidth': playlist.stream_info.bandwidth
        }
        if url not in renditions['video'].keys():
          renditions['video'][url] = rendition
    # Other media
    for media in multiplaylist.media:
      if media.uri and media.type.lower() in {'audio', 'subtitles'}:
        url = urljoin(monitorinfo['config']['endpointconfig']['manifesturl'], media.uri)
        rendition = {
          'index': len(renditions[media.type.lower()]) + 1,
          'media': media.type.lower()
        }
        if url not in renditions[media.type.lower()].keys():
          renditions[media.type.lower()][url] = rendition
    logger.debug(f"Found {len(renditions['video'])} video, {len(renditions['audio'])} audio and {len(renditions['subtitles'])} subtitle renditions: {renditions}")
    # Start threads
    primary = True
    activerenditions = []
    for renditionstring in monitorinfo['config']['endpointconfig']['hls']['renditions']:
      if renditionstring:
        for media in renditions.keys():
          if media.startswith(renditionstring) or renditionstring == '*':
            for url, rendition in renditions[media].items():
              renditionid = f"{media[0]}{rendition['index']}"
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

