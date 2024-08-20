#!/usr/bin/env python3

import logging
import logging.config
import traceback
import threading
import time
import argparse
import sys
import datetime
import re
import urllib3
import io
import os
import signal
import socket
import platform
import json
import gzip
from collections import deque
from pathlib import Path
from urllib.parse import urljoin
from urllib.parse import urlparse


# Exceptions handling
def handle_threading_exception(args):
  logger.critical('Uncaught exception: ' + str(args) + ', traceback: ' + str(traceback.format_exc().splitlines()))
  print('Uncaught exception: ' + str(args) + ', traceback: ' + str(traceback.format_exc().splitlines()))


# Exceptions handling
def handle_sys_exception(exc_type, exc_value, tb):
  logger.critical('Uncaught exception, exc_type: ' + str(exc_type) + ', exc_value: ' + str(exc_value))
  print('Uncaught exception, exc_type: ' + str(exc_type) + ', exc_value: ' + str(exc_value))


# Http requests
def request3(logger, headers:dict, url:str, method:str, dsttype:str, metricstopublish:dict):
  headers.update({'User-Agent': 'CanaryMonitor (v2.0)'})
  start = time.perf_counter()
  try:
    response = http.request(method, url, headers = headers, retries = False, decode_content = False)
    if response.status >= 400:
      logger.warning('HTTP request response ' + str(response.status) + ', reason: ' + str(response.reason) + ', url: ' + url + ', response headers: ' + str(response.headers.items()))
      if userargs['cwmetrics']:
        if response.status >= 400 and response.status < 500:
          if dsttype == 'manifest':
            metricstopublish['manifest4xx'] = 1
          elif dsttype == 'tracking':
            metricstopublish['tracking4xx'] = 1
          elif dsttype == 'segment':
            addmetricvalue(metricstopublish, 'segment4xx', 1)
        elif response.status >= 500 and response.status < 600:
          if dsttype == 'manifest':
            metricstopublish['manifest5xx'] = 1
          elif dsttype == 'tracking':
            metricstopublish['tracking5xx'] = 1
          elif dsttype == 'segment':
            addmetricvalue(metricstopublish, 'segment5xx', 1)
      return None, int((time.perf_counter() - start) * 1000)
    else:
      return response, int((time.perf_counter() - start) * 1000)
  except urllib3.exceptions.NewConnectionError as e:
    logger.warning('HTTP request connection error, url: ' + url + ', exception: ' + str(e))
  except urllib3.exceptions.ConnectTimeoutError as e:
    logger.warning('HTTP request connection timeout, url: ' + url + ', exception: ' + str(e))
  except urllib3.exceptions.ReadTimeoutError as e:
    logger.warning('HTTP request read timeout, url: ' + url + ', exception: ' + str(e))
  except urllib3.exceptions.SSLError as e:
    logger.warning('HTTP request SSL error, url: ' + url + ', exception: ' + str(e))
  except urllib3.exceptions.HTTPError as e:
    logger.warning('HTTP request error, url: ' + url + ', exception: ' + str(e))
  except socket.timeout:
    logger.warning('HTTP request socket timeout, url: ' + url)
  except socket.gaierror:
    logger.warning('HTTP request name or service not known error, url: ' + url)
  except OSError:
    logger.warning('HTTP request OS error, url: ' + url)
  except Exception as e:
    logger.exception(e)
  # Collect info for metrics
  if userargs['cwmetrics']:
    if dsttype == 'manifest':
      metricstopublish['manifesttimeouterror'] = 1
    elif dsttype == 'tracking':
      metricstopublish['trackingtimeouterror'] = 1
    elif dsttype == 'segment':
      addmetricvalue(metricstopublish, 'segmenttimeouterror', 1)
  return None, int((time.perf_counter() - start) * 1000)


# Get response text
def getresponsetext(response, utf:bool):
  foundcontentencodinggzip = False
  for i in response.headers.keys():
    if 'content-encoding' == i.lower():
      if response.headers[i] == 'gzip':
        foundcontentencodinggzip = True
        break
  if foundcontentencodinggzip:
    text = gzip.decompress(response.data)
  else:
    text = response.data
  if utf:
    return text.decode('utf-8')
  else:
    return text


# Save response
def saveresponse(logger, content, folder, filename:str, binary:bool, compress:bool):
  if userargs['dayfolder'] == True:
    folderpath = Path(folder, datetime.datetime.utcnow().strftime('%Y-%m-%d'))
  else:
    folderpath = Path(folder)
  try:
    folderpath.mkdir(parents = True, exist_ok = True)
  except Exception:
    logger.exception('Error creating directory')
    return
  now = time.perf_counter()
  if binary:
    filepath = Path(folderpath, filename)
    try:
      with filepath.open('w+b') as f:
        f.write(content)
    except Exception:
      logger.exception('Error saving file')
      return
  else:
    if userargs['gzip'] == True:
      filepath = Path(folderpath, filename + '.gz')
      try:
        if compress:
          with gzip.open(str(filepath), 'w+b') as f:
            with io.TextIOWrapper(f, encoding='utf-8') as tiow:
              tiow.write(content)
        else:
          with filepath.open('w+b') as f:
            f.write(content)
      except Exception:
        logger.exception('Error saving file')
        return
    else:
      filepath = Path(folderpath, filename)
      try:
        with filepath.open('w+t') as f:
          f.write(content)
      except Exception:
        logger.exception('Error saving file')
        return
  timeittook = time.perf_counter() - now
  if timeittook > 2:
    logger.error('It took ' + '{:.3f}'.format(timeittook) + ' seconds to save file ' + str(filepath))
  return filepath


# Publish metrics to CloudWatch
def publishmetrics(logger, endpoint:dict, renditionname:str, metricstopublish:dict):
  publishlist = []
  for k in metricstopublish.keys():
    if type(metricstopublish[k]) == dict:
      try:
        publishlist.append({'MetricName': k, 'Dimensions': [{'Name': 'Endpoint', 'Value': renditionname}, {'Name': 'Type', 'Value': endpoint['type']}], 'Values': metricstopublish[k]['values'], 'Counts': metricstopublish[k]['counts']})
      except KeyError:
        logger.exception('Bad key')
    else:
      publishlist.append({'MetricName': k, 'Dimensions': [{'Name': 'Endpoint', 'Value': renditionname}, {'Name': 'Type', 'Value': endpoint['type']}], 'Value': metricstopublish[k]})
  if publishlist:
    now = time.perf_counter()
    try:
      cloudwatch.put_metric_data(Namespace = 'CanaryMonitor', MetricData = publishlist)
    except socket.gaierror:
      logger.error('Metrics publish request name or service not known error')
    except botocore.exceptions.EndpointConnectionError:
      logger.error('Metrics publish endpoint connection error')
    except Exception:
      logger.exception('Error sending metrics to Cloudwatch')
    timeittook = time.perf_counter() - now
    if timeittook > 7:
      logger.error('It took ' + '{:.3f}'.format(timeittook) + ' seconds to publish metrics')


# Check if this is multivariant HLS manifest
def checkifprimary(logger, response):
  isprimary = False
  for line in response.split('\n'):
    line = line.strip()
    if line.startswith('#EXT-X-STREAM-INF:') or line.startswith('#EXT-X-MEDIA:'):
      isprimary = True
      break
  return isprimary


# Find HLS and DASH renditions
def findrenditions(logger, responsetext, endpoint:dict):
  rendition = {} ; renditions = [] ; vcount = 0 ; acount = 0 ; scount = 0 ; tcount = 0
  # HLS
  if endpoint['type'] == 'hls':
    for line in responsetext.split('\n'):
      line = line.strip()
      if line.startswith('#EXT-X-STREAM-INF:') or line.startswith('#EXT-X-MEDIA:'):
        # Find TYPE
        match = re.search(r'(TYPE=)(\w*)', line)
        if match:
          rendition['TYPE'] = match.group(2)
        # Find URI
        match = re.search(r'(URI=\")(.*\.m3u8)', line)
        if match:
          rendition['URL'] = urljoin(endpoint['url'], match.group(2))
        # Find BANDWIDTH
        match = re.search(r'([^-]BANDWIDTH=)(\d+)', line)
        if match:
          rendition['BANDWIDTH'] = int(match.group(2))
        # Find AVERAGE-BANDWIDTH
        match = re.search(r'(AVERAGE-BANDWIDTH=)(\d+)', line)
        if match:
          rendition['AVERAGE-BANDWIDTH'] = int(match.group(2))
        # Find LANGUAGE
        match = re.search(r'(LANGUAGE=\")(\w*)', line)
        if match:
          rendition['LANGUAGE'] = match.group(2)
        # Find NAME
        match = re.search(r'(NAME=\")(\w*)', line)
        if match:
          rendition['NAME'] = match.group(2)
        # Find CHANNELS
        match = re.search(r'(CHANNELS=\")(\w*)', line)
        if match:
          rendition['CHANNELS'] = match.group(2)
        # Find GROUP-ID
        match = re.search(r'(GROUP-ID=\")(\w*)', line)
        if match:
          rendition['GROUP-ID'] = match.group(2)
        # If still need URL
        if line.startswith('#EXT-X-STREAM-INF'):
          continue
      elif not line.startswith('#') and len(line) != 0:
        rendition['TYPE'] = 'VIDEO'
        rendition['URL'] = urljoin(endpoint['url'], line)
      else:
        continue
      if all(i in rendition.keys() for i in ['TYPE', 'URL']):
        newrendition = True
        for i in renditions:
          if i['URL'] == rendition['URL']:
            newrendition = False
        if newrendition:
          if rendition['TYPE'] == 'VIDEO':
            vcount = vcount + 1
            rendition['NUM'] = vcount
          if rendition['TYPE'] == 'AUDIO':
            acount = acount + 1
            rendition['NUM'] = acount
          if rendition['TYPE'] == 'SUBTITLES':
            scount = scount + 1
            rendition['NUM'] = scount
          if 'NUM' in rendition.keys():
            renditions.append(rendition.copy())
          else:
            logger.error('Unknown rendition type')
      else:
        logger.error('Problem while finding renditions')
      rendition.clear()
  # DASH
  elif endpoint['type'] == 'dash':
    ns = {'default': 'urn:mpeg:dash:schema:mpd:2011', 'scte': 'urn:scte:scte35:2013:xml'}
    xmlroot = ET.fromstring(responsetext)
    # Get representations of last period
    if xmlroot != None:
      xmlperiods = xmlroot.findall('default:Period', ns)
      if xmlperiods:
        xmladaptationsets = xmlperiods[-1].findall('default:AdaptationSet', ns)
        for xmladaptationset in xmladaptationsets:
          xmlrepresentations = xmladaptationset.findall('default:Representation', ns)
          for xmlrepresentation in xmlrepresentations:
            if xmlrepresentation.get('id'):
              rendition['ID'] = xmlrepresentation.get('id')
            if xmladaptationset.get('mimeType'):
              rendition['TYPE'] = xmladaptationset.get('mimeType')
            if xmladaptationset.get('lang'):
              rendition['LANG'] = xmladaptationset.get('lang')
            if xmlrepresentation.get('bandwidth'):
              rendition['BANDWIDTH'] = xmlrepresentation.get('bandwidth')
            if xmlrepresentation.get('codecs'):
              rendition['CODECS'] = xmlrepresentation.get('codecs')
            if xmlrepresentation.get('frameRate'):
              rendition['FRAMERATE'] = xmlrepresentation.get('frameRate')
            if xmlrepresentation.get('width'):
              rendition['WIDTH'] = xmlrepresentation.get('width')
            if xmlrepresentation.get('height'):
              rendition['HEIGHT'] = xmlrepresentation.get('height')
            if 'TYPE' in rendition.keys() and 'ID' in rendition.keys():
              if rendition['TYPE'] == 'video/mp4':
                vcount = vcount + 1
                rendition['NUM'] = vcount
              elif rendition['TYPE'] == 'audio/mp4':
                acount = acount + 1
                rendition['NUM'] = acount
              elif rendition['TYPE'] == 'application/mp4':
                scount = scount + 1
                rendition['NUM'] = scount
              elif rendition['TYPE'] == 'image/jpeg':
                tcount = tcount + 1
                rendition['NUM'] = tcount
              else:
                logger.error('Unknown rendition type')
              if 'NUM' in rendition.keys():
                renditions.append(rendition.copy())
            rendition.clear()
  # Smooth
  elif endpoint['type'] == 'smooth':
    xmlroot = ET.fromstring(responsetext)
    if xmlroot != None:
      xmlstreamindices = xmlroot.findall('StreamIndex')
      if xmlstreamindices:
        for xmlstreamindex in xmlstreamindices:
          xmlqualitylevels = xmlstreamindex.findall('QualityLevel')
          for xmlqualitylevel in xmlqualitylevels:
            if xmlstreamindex.get('Type'):
              rendition['TYPE'] = xmlstreamindex.get('Type')
            if xmlstreamindex.get('Name'):
              rendition['Name'] = xmlstreamindex.get('Name')
            if xmlstreamindex.get('Language'):
              rendition['Language'] = xmlstreamindex.get('Language')
            if xmlqualitylevel.get('Index'):
              rendition['Index'] = xmlqualitylevel.get('Index')
            if xmlqualitylevel.get('Bitrate'):
              rendition['Bitrate'] = xmlqualitylevel.get('Bitrate')
            if xmlqualitylevel.get('FourCC'):
              rendition['FourCC'] = xmlqualitylevel.get('FourCC')
            if xmlqualitylevel.get('Channels'):
              rendition['Channels'] = xmlqualitylevel.get('Channels')
            if 'TYPE' in rendition.keys():
              if rendition['TYPE'] == 'video':
                vcount = vcount + 1
                rendition['NUM'] = vcount
              elif rendition['TYPE'] == 'audio':
                acount = acount + 1
                rendition['NUM'] = acount
              elif rendition['TYPE'] == 'text':
                scount = scount + 1
                rendition['NUM'] = scount
              else:
                logger.error('Unknown rendition type')
              if 'NUM' in rendition.keys():
                renditions.append(rendition.copy())
            rendition.clear()
  logger.info('Found these renditions (total: ' + str(len(renditions)) + ' ; video: ' + str(vcount) + ', audio: ' + str(acount) + ', subtitles: ' + str(scount) + '): ' + str(renditions))
  return renditions
  
  
# Check if video and audio DASH adaptation set exists
def checkadaptationsets(logger, adaptationsets:list, periodid:str):
  foundvideo = False ; foundaudio = False ; foundsubtitles = False
  for i in adaptationsets:
    mimetype = i.get('mimeType')
    if mimetype:
      if mimetype == 'video/mp4':
        foundvideo = True
      elif mimetype == 'audio/mp4':
        foundaudio = True
      elif mimetype == 'application/mp4':
        foundsubtitles = True
  if not foundvideo:
    logger.warning('Missing video adaptation set in period ' + periodid)
  if not foundaudio:
    logger.warning('Missing audio adaptation set in period ' + periodid)


# Check for DASH pto offset misalignment between renditions
def checkpresentationtimeoffsets(logger, presentationtimeoffsets:list, periodid:str):
  helpcompare = {'video': [], 'audio': [], 'subtitles': []} ; videoptomisalignment = False ; videoaudioptomisalignment = False ; videosubtitlesptomisalignment = False
  for i in presentationtimeoffsets:
    if i['mimeType'] == 'video/mp4':
      helpcompare['video'].append(float(i['presentationTimeOffset']) / float(i['timescale']))
    if i['mimeType'] == 'audio/mp4':
      helpcompare['audio'].append(float(i['presentationTimeOffset']) / float(i['timescale']))
    if i['mimeType'] == 'application/mp4':
      helpcompare['subtitles'].append(float(i['presentationTimeOffset']) / float(i['timescale']))
  if len(helpcompare['video']) > 0:
    for i in helpcompare['video']:
      if abs(helpcompare['video'][-1] - i) > 0:
        videoptomisalignment = True
    for i in helpcompare['audio']:
      if abs(helpcompare['video'][-1] - i) > 0.1:
        videoaudioptomisalignment = True
    for i in helpcompare['subtitles']:
      if abs(helpcompare['video'][-1] - i) > 0.1:
        videosubtitlesptomisalignment = True
  if videoptomisalignment or videoaudioptomisalignment or videosubtitlesptomisalignment:
    logger.warning('Presentation time offset misalignment in period ' + periodid + ', videoptomisalignment: ' + str(videoptomisalignment) + ', videoaudioptomisalignment: ' + str(videoaudioptomisalignment) + ', videosubtitlesptomisalignment: ' + str(videosubtitlesptomisalignment) + ', presentationTimeOffset / timescale: ' + str(helpcompare))


# Find last segment information from provided URL
def proberendition(logger, endpoint, rendition, responsetext = None):
  segmentinfo = {} ; adaptationsets = [] ; presentationtimeoffsets = [] ; manifestduration = 0.0
  if not responsetext:
    responsetext = ''
    response, responsetime = request3(logger, {'Accept-Encoding': 'gzip'}, rendition['URL'], 'GET', 'manifest', {})
    if response:
      if endpoint['type'] == 'hls':
        responsetext = getresponsetext(response, True)
      else:
        responsetext = getresponsetext(response, False)
  if responsetext:
    # HLS - get last media sequence
    if endpoint['type'] == 'hls':
      segmentcount = 0 ; manifestmediasequence = -1
      for line in responsetext.split('\n'):
        line = line.strip()
        if line.startswith('#EXT-X-MEDIA-SEQUENCE:'):
          match = re.search(r'\d+', line)
          if match:
            manifestmediasequence = int(match.group())
        elif line.startswith('#EXTINF:'):
          match = re.search(r'\d*\.?\d+', line)
          if match:
            manifestduration = manifestduration + float(match.group())
        elif not line.startswith('#') and len(line) != 0:
          segmentcount = segmentcount + 1
      if manifestmediasequence >= 0:
        lastmediasequence = manifestmediasequence + segmentcount - 1
        logger.debug('Probed rendition and identified last media sequence: ' + str(lastmediasequence))
        return {'lastmediasequence': lastmediasequence, 'manifestduration': manifestduration}
    # DASH - get last segment info
    elif endpoint['type'] == 'dash':
      ns = {'default': 'urn:mpeg:dash:schema:mpd:2011', 'scte': 'urn:scte:scte35:2013:xml'}
      xmlroot = ET.fromstring(responsetext)
      if xmlroot != None:
        xmlperiods = xmlroot.findall('default:Period', ns)
        if xmlperiods:
          if xmlperiods[-1].get('id'):
            segmentinfo['period'] = xmlperiods[-1].get('id')
            xmladaptationsets = xmlperiods[-1].findall('default:AdaptationSet', ns)
            for xmladaptationset in xmladaptationsets:
              adaptationsets.append(xmladaptationset.attrib)
              if xmladaptationset.get('mimeType'):
                if xmladaptationset.get('mimeType') == rendition['TYPE']:
                  xmlrepresentations = xmladaptationset.findall('default:Representation', ns)
                  xmlsegmenttemplatecompact = xmladaptationset.find('default:SegmentTemplate', ns)
                  for xmlrepresentation in xmlrepresentations:
                    if xmlrepresentation.get('id'):
                      if xmlrepresentation.get('id') == rendition['ID']:
                        xmlsegmenttemplate = xmlrepresentation.find('default:SegmentTemplate', ns)
                        if xmlsegmenttemplate == None:
                          xmlsegmenttemplate = xmlsegmenttemplatecompact
                          logger.debug('Looks like a compact manifest')
                        if xmlsegmenttemplate != None:
                          if xmlsegmenttemplate.get('startNumber'):
                            segmentinfo['n'] = int(xmlsegmenttemplate.get('startNumber')) - 1
                            xmlsegmenttimeline = xmlsegmenttemplate.find('default:SegmentTimeline', ns)
                            if xmlsegmenttimeline != None:
                              for element in xmlsegmenttimeline:
                                # Find last segment number
                                if element.tag == '{' + ns['default'] + '}' + 'S': # '{urn:mpeg:dash:schema:mpd:2011}S'
                                  xmlr = element.get('r')
                                  sr = int(xmlr) if xmlr else 0
                                  for i in range(sr + 1):
                                    segmentinfo['n'] = segmentinfo['n'] + 1
                                if element.tag == '{' + ns['default'] + '}' + 'Pattern': # '{urn:mpeg:dash:schema:mpd:2011}Pattern'
                                  xmlr = element.get('r')
                                  pr = int(xmlr) if xmlr else 0
                                  for i in range(pr + 1):
                                    xmlss = element.findall('default:S', ns)
                                    for xmls in xmlss:
                                      xmlr = xmls.get('r')
                                      sr = int(xmlr) if xmlr else 0
                                      for i in range(sr + 1):
                                        segmentinfo['n'] = segmentinfo['n'] + 1
                        else:
                          logger.error('Did not find any SegmentTemplate')
            # Check adaptation sets
            checkadaptationsets(logger, adaptationsets, segmentinfo['period'])
            # Check presentation time offsets
            for xmladaptationset in xmladaptationsets:
              xmladaptationsetmimetype = xmladaptationset.get('mimeType')
              if xmladaptationsetmimetype != None and xmladaptationsetmimetype != 'image/jpeg':
                xmlrepresentations = xmladaptationset.findall('default:Representation', ns)
                for xmlrepresentation in xmlrepresentations:
                  xmlsegmenttemplates = xmlrepresentation.findall('default:SegmentTemplate', ns)
                  for xmlsegmenttemplate in xmlsegmenttemplates:
                    xmlpresentationtimeoffset = xmlsegmenttemplate.get('presentationTimeOffset')
                    xmltimescale = xmlsegmenttemplate.get('timescale')
                    if xmlpresentationtimeoffset != None and xmltimescale != None:
                      presentationtimeoffsets.append({'mimeType': xmladaptationsetmimetype, 'presentationTimeOffset': xmlpresentationtimeoffset, 'timescale': xmltimescale})
            checkpresentationtimeoffsets(logger, presentationtimeoffsets, segmentinfo['period'])
      if all(i in segmentinfo.keys() for i in ['period', 'n']):
        logger.debug('Probed rendition and identified last segment: ' + str(segmentinfo))
        return segmentinfo
    # Smooth - get last segment info from first representation
    elif endpoint['type'] == 'smooth':
      xmlroot = ET.fromstring(responsetext)
      if xmlroot != None:
        xmlstreamindices = xmlroot.findall('StreamIndex')
        if xmlstreamindices:
          for xmlstreamindex in xmlstreamindices:
            xmlcs = xmlstreamindex.findall('c')
            for xmlc in xmlcs:
              xmlt = xmlc.get('t')
              if xmlt:
                segmentinfo['t'] = int(xmlt)
              else:
                if 't' in segmentinfo.keys():
                  xmld = xmlc.get('d')
                  if xmld:
                    segmentinfo['t'] = segmentinfo['t'] + int(xmld)
            break
          # Check stream indices for audio, video and captions
      if 't' in segmentinfo.keys():
        return segmentinfo
  return {}


# Find rendition type
def findrenditiontype(logger, endpoint, renditions):
  supportedrenditiontype = False ; foundrenditiontype = False
  match = re.search(r'\d+', userargs['renditiontype'])
  if match:
    rn = int(match.group())
    match = re.search(r'[vas]', userargs['renditiontype'])
    if match:
      rt = match.group()
      supportedrenditiontype = True
  if supportedrenditiontype:
    # HLS
    if endpoint['type'] == 'hls':
      for rendition in renditions:
        if rendition['TYPE'][0].lower() == rt:
          if rendition['NUM'] == rn:
            foundrenditiontype = True
            break
    # DASH
    if endpoint['type'] == 'dash':
      for rendition in renditions:
        if (rendition['TYPE'] == "video/mp4" and rt == 'v') or (rendition['TYPE'] == "audio/mp4" and rt == 'a') or (rendition['TYPE'] == "application/mp4" and rt == 's'):
          if rendition['NUM'] == rn:
            foundrenditiontype = True
            break
    # Smooth
    if endpoint['type'] == 'smooth':
      for rendition in renditions:
        if (rendition['TYPE'] == 'video' and rt == 'v') or (rendition['TYPE'] == 'audio' and rt == 'a') or (rendition['TYPE'] == 'text' and rt == 's'):
          if rendition['NUM'] == rn:
            foundrenditiontype = True
            break
    if foundrenditiontype:
      logger.debug('Found rendition type ' + userargs['renditiontype'] + ' in primary manifest: ' + str(rendition))
      return rendition
  else:
    logger.error('Unsupported rendition type attribute. Usage: 1v (stands for 1st video rendition)')


# Compare tags across HLS renditions
def comparerenditionssegments(logger, lock, sharedlist:list, threadcount:int):
  setofdifferences = set() ; initdict = {}

  lock.acquire()

  for i in sharedlist:
    setofdifferences.clear() ; initdict.clear()
    # If same mediasequence was discovered by all renditions
    if len(i['segments']) == threadcount:
      # Compare segments and remove after comparing
      try:
        for j in i['segments']:
          if j['type'] == 'VIDEO':
            initdict['discontinuitysequence'] = j['discontinuitysequence']
            initdict['discontinuity'] = j['discontinuity']
            initdict['videoduration'] = j['duration']
            if 'explicitpdt' in j.keys():
              if j['explicitpdt'] == True:
                initdict['videopdt'] = j['pdt']
        for j in i['segments']:
          # Compare video renditions explicit pdt values and durations
          if j['type'] == 'VIDEO':
            if 'videopdt' in initdict.keys():
              if j['pdt'] != initdict['videopdt']:
                setofdifferences.add('videopdt')
            if 'videoduration' in initdict.keys():
              if j['duration'] != initdict['videoduration']:
                setofdifferences.add('videoduration')
          # Compare discontinuitysequence
          if j['discontinuitysequence'] != initdict['discontinuitysequence']:
            setofdifferences.add('discontinuitysequence')
          # Compare discontinuity
          if j['discontinuity'] != initdict['discontinuity']:
            setofdifferences.add('discontinuity')
      except KeyError:
        logger.exception('Bad key')

      if len(setofdifferences) > 0:
        logger.warning('Found differences in mediasequence ' + str(i['mediasequence']) + ' between renditions, differences: ' + str(setofdifferences) + ', segmentinfo: ' + str(i['segments']))
      sharedlist.remove(i)
  lock.release()


# Helper
def addrenditionname(lockm, endpoint:dict, renditionname:str):
  lockm.acquire()
  renditionnames[endpoint['type']].append(renditionname)
  lockm.release()


# Collect URL info before monitoring start
def premonitor(tlogger, endpoint:dict, lockm):
  logger = logging.LoggerAdapter(tlogger, {'endpointtype': endpoint['type'], 'renditionname': endpoint['name']})
  failure = False ; lock = threading.Lock() ; sharedlist = [] ; stoprunning = threading.Event() ; renditionnamesadded = False
  while not terminatethreads:
    response, responsetime = request3(logger, {'Accept-Encoding': 'gzip'}, endpoint['url'], 'GET', 'manifest', {})
    if not response:
      time.sleep(5)
      continue
    else:
      if failure:
        time.sleep(5)
      # HLS
      if endpoint['type'] == 'hls':
        responsetext = getresponsetext(response, True)
        isprimary = checkifprimary(logger, responsetext)
        if isprimary:
          renditions = findrenditions(logger, responsetext, endpoint)
          # Primary playlist and monitor all renditions
          if userargs['allrenditions'] == True or userargs['playerrenditions'] == True:
            if len(renditions) > 0:
              proberesponse = proberendition(logger, endpoint, renditions[0])
              time.sleep(2)
              if proberesponse:
                threads = [] ; sharedlist.clear() ; stoprunning.clear() ; pickedvideo = False ; pickedaudio = False ; pickedsubtitles = False ; alreadytracking = False
                for rendition in renditions:
                  # Player rendition picks 1 rendition from each type
                  if userargs['playerrenditions'] == True:
                    if not pickedvideo and rendition['TYPE'] == 'VIDEO':
                      pickedvideo = True
                    elif not pickedaudio and rendition['TYPE'] == 'AUDIO':
                      pickedaudio = True
                    elif not pickedsubtitles and rendition['TYPE'] == 'SUBTITLES':
                      pickedsubtitles = True
                    else:
                      continue
                  dotracking = False
                  if alreadytracking == False and endpoint['tracking'] != '':
                    if userargs['trackingrequests'] == True:
                      dotracking = True ; alreadytracking = True
                  renditionname = endpoint['name'] + '-' + rendition['TYPE'][0].lower() + str(rendition['NUM'])
                  if userargs['manifests'] == True:
                    if userargs['gzip'] == True:
                      filepath = saveresponse(logger, responsetext, Path(userargs['manifestsfolder'], renditionname), datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + '_primary.m3u8', False, True)
                    else:
                      filepath = saveresponse(logger, responsetext, Path(userargs['manifestsfolder'], renditionname), datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + '_primary.m3u8', False, False)
                    if filepath:
                      logger.debug('Saved file ' + str(filepath))
                  if not renditionnamesadded:
                    addrenditionname(lockm, endpoint, renditionname)
                  x = threading.Thread(target = monitor, args = (tlogger, endpoint, rendition, renditionname, proberesponse, True, stoprunning, lock, sharedlist, dotracking))
                  threads.append(x)
                  x.start()
                renditionnamesadded = True
                while True:
                  # Check threads status
                  alivecount = 0
                  for i in threads:
                    if i.is_alive():
                      alivecount = alivecount + 1
                  if alivecount == 0:
                    threads.clear()
                    break
                  if len(threads) != alivecount:
                    stoprunning.set()
                    logger.info('Waiting for all threads to stop')
                    time.sleep(1)
                    continue
                  # Compare segments across renditions
                  comparerenditionssegments(logger, lock, sharedlist, len(threads))
                  if len(sharedlist) > userargs['frequency'] * 5:
                    logger.error('Unexpectedely long list of segments not discovered across all renditions with same media sequence. Length: ' + str(len(sharedlist)))
                    stoprunning.set()
                  time.sleep(5)
                logger.info('Stopped monitoring')
              else:
                logger.error('Failed probing rendition to find out latest segment')
          # Primary playlist, but monitor only 1 rendition
          else:
            if len(renditions) > 0:
              rendition = findrenditiontype(logger, endpoint, renditions)
              if rendition:
                proberesponse = proberendition(logger, endpoint, rendition)
                time.sleep(2)
                if proberesponse:
                  renditionname = endpoint['name'] + '-' + userargs['renditiontype']
                  if userargs['manifests'] == True:
                    filepath = ''
                    if userargs['gzip'] == True:
                      filepath = saveresponse(logger, responsetext, Path(userargs['manifestsfolder'], renditionname), datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + '_primary.m3u8', False, True)
                    else:
                      filepath = saveresponse(logger, responsetext, Path(userargs['manifestsfolder'], renditionname), datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + '_primary.m3u8', False, False)
                    if filepath:
                      logger.debug('Saved file ' + str(filepath))
                  if not renditionnamesadded:
                    addrenditionname(lockm, endpoint, renditionname)
                    renditionnamesadded = True
                  dotracking = False
                  if endpoint['tracking'] != '':
                    if userargs['trackingrequests'] == True:
                      dotracking = True
                  monitor(tlogger, endpoint, rendition, renditionname, proberesponse, True, stoprunning, lock, sharedlist, dotracking)
                  logger.info('Stopped monitoring')
                else:
                  logger.error('Failed probing rendition to find out latest segment')
              else:
                logger.error('Did not find rendition type ' + userargs['renditiontype'] + ' in primary manifest')
        # Media playlist
        else:
          proberesponse = proberendition(logger, endpoint, {'URL': endpoint['url']}, responsetext)
          time.sleep(2)
          if proberesponse:
            renditionname = endpoint['name'] + '-' + '??'
            if not renditionnamesadded:
              addrenditionname(lockm, endpoint, renditionname)
              renditionnamesadded = True
            dotracking = False
            if endpoint['tracking'] != '':
              if userargs['trackingrequests'] == True:
                dotracking = True
            monitor(tlogger, endpoint, {'URL': endpoint['url']}, renditionname, proberesponse, False, stoprunning, lock, sharedlist, dotracking)
            logger.info('Stopped monitoring')
            break
          else:
            logger.error('Failed probing rendition to find out latest segment')
        failure = True
      # DASH
      elif endpoint['type'] == 'dash':
        if userargs['loadtest'] == False:
          responsetext = getresponsetext(response, False)
          renditions = findrenditions(logger, responsetext, endpoint)
          if len(renditions) > 0:
            rendition = findrenditiontype(logger, endpoint, renditions)
            if rendition:
              #lastsegmentinfo = {'period': '1841924', 'n': 1842100, 't': 1}
              lastsegmentinfo = proberendition(logger, endpoint, rendition, responsetext)
              time.sleep(2)
              if lastsegmentinfo:
                # renditionname = endpoint['name'] + '-' + userargs['renditiontype']
                renditionname = endpoint['name']
                if not renditionnamesadded:
                  addrenditionname(lockm, endpoint, renditionname)
                  renditionnamesadded = True
                monitor(tlogger, endpoint, rendition, renditionname, lastsegmentinfo, True, stoprunning, lock, sharedlist)
                logger.info('Stopped monitoring')
              else:
                logger.error('Failed probing rendition to find out latest segment')
        else:
          dotracking = False
          if endpoint['tracking'] != '' and userargs['trackingrequests'] == True:
            dotracking = True
          monitor(tlogger, endpoint, {}, endpoint['name'], {}, True, stoprunning, lock, sharedlist, dotracking)
          logger.info('Stopped monitoring')
      # Smooth
      elif endpoint['type'] == 'smooth':
        responsetext = getresponsetext(response, False)
        renditions = findrenditions(logger, responsetext, endpoint)
        if len(renditions) > 0:
          rendition = findrenditiontype(logger, endpoint, renditions)
          if rendition:
            lastsegmentinfo = proberendition(logger, endpoint, rendition, responsetext)
            time.sleep(2)
            if lastsegmentinfo:
              renditionname = endpoint['name']
              # if not renditionnamesadded:
              #   addrenditionname(lockm, endpoint, renditionname)
              #   renditionnamesadded = True
              monitor(tlogger, endpoint, rendition, renditionname, lastsegmentinfo, True, stoprunning, lock, sharedlist)
            else:
              logger.error('Failed probing rendition to find out latest segment')


# Compare HLS tags, which should be static
def comparevalues(logger, source:dict, key:str, value):
  if key in source.keys():
    if not source[key] == value:
      logger.warning('Manifest value has changed for ' + key + ', previously: ' + str(source[key]) + ', now: ' + str(value))
      source[key] = value
      return False
  else:
    source[key] = value
  return True


# Compare if last known segment info has changed with new manifest response
def comparelastsegment(logger, new:dict, old:dict):
  newlist = [] ; oldlist = [] ; arethesame = True
  for i in new.keys():
    if i in old.keys():
      if new[i] != old[i]:
        if i == 'pdt':
          if new['explicitpdt'] == False:
            continue
        if i == 'explicitpdt':
          continue
        newlist.append({i: new[i]})
        oldlist.append({i: old[i]})
        arethesame = False
  if not arethesame:
    logger.warning('Last segment info has changed, previously: ' + str(oldlist) + ', now: ' + str(newlist))
  return arethesame


# CloudWatch metrics
def addmetricvalue(metricstopublish:dict, metric:str, value):
  if metric in metricstopublish.keys():
    if value in metricstopublish[metric]['values']:
      index = metricstopublish[metric]['values'].index(value)
      metricstopublish[metric]['counts'][index] = metricstopublish[metric]['counts'][index] + 1
    else:
      metricstopublish[metric]['values'].append(value)
      metricstopublish[metric]['counts'].append(1)
  else:
    metricstopublish[metric] = {'values': [value], 'counts': [1]}


# Helper
def getperiodstartdelta(logger, periodstarts: list):
  periodstartsseconds = []
  for i in periodstarts:
    match = re.search(r'(\d+)(H)', i)
    hours = int(match.group(1)) if match else 0
    match = re.search(r'(\d+)(M)', i)
    minutes = int(match.group(1)) if match else 0
    match = re.search(r'(\d*\.?\d+)(S)', i)
    seconds = float(match.group(1)) if match else 0.0
    periodstartsseconds.append(hours * 3600 + minutes * 60 + seconds)
  return periodstartsseconds[1] - periodstartsseconds[0]


# Helper
def addsegmenttonewsegments(manifestinfo, segmentinfo, xmlperiodid):
  # Compute PTS
  if 'pto' in segmentinfo.keys():
    pts = round((segmentinfo['t'] - segmentinfo['pto']) / segmentinfo['timescale'], 3)
  else:
    pts = round(segmentinfo['t'] / segmentinfo['timescale'], 3)
  # Add PTS to list
  if (xmlperiodid, segmentinfo['n']) in manifestinfo['newsegmentspts'].keys():
    manifestinfo['newsegmentspts'][(xmlperiodid, segmentinfo['n'])].append(pts)
  else:
    manifestinfo['newsegmentspts'][(xmlperiodid, segmentinfo['n'])] = [pts]


# Main function for monitoring
def monitor(tlogger, endpoint:dict, rendition:dict, renditionname:str, proberesponse:dict, fromprimary:bool, stoprunning, lock, sharedlist:list, dotracking = False):
  logger = logging.LoggerAdapter(tlogger, {'endpointtype': endpoint['type'], 'renditionname': renditionname})
  # Loadtest
  if userargs['loadtest'] == True:
    while not terminatethreads and not stoprunning.is_set():
      start = time.perf_counter() ; manifestresponsetime = 0 ; trackingresponsetime = 0
      # Request manifest
      if endpoint['type'] == 'hls':
        manifestresponse, manifestresponsetime = request3(logger, {'Accept-Encoding': 'gzip'}, rendition['URL'], 'GET', 'manifest', {})
      elif endpoint['type'] == 'dash':
        manifestresponse, manifestresponsetime = request3(logger, {'Accept-Encoding': 'gzip'}, endpoint['url'], 'GET', 'manifest', {})
      # Request tracking
      if dotracking:
        trackingresponse, trackingresponsetime = request3(logger, {'Accept-Encoding': 'gzip'}, endpoint['tracking'], 'GET', 'tracking', {})
      # Wait between requests
      waittime = start - time.perf_counter() + userargs['frequency']
      if waittime > 0:
        time.sleep(waittime)
      elif waittime < -1:
        logger.error('Negative wait time ' + '{:.3f}'.format(waittime) + ' sec between manifest requests, manifest response time: ' + str(manifestresponsetime) + ' msec, tracking response time: ' + str(trackingresponsetime))
    return
  
  metricstopublish = {} ; segmentinfo = {} ; startsession = True ; segmenttags = [] ; manifestinfo = {} ; scteinfo = {} ; segmentationdescriptorinfo = {} ; segmentationdescriptors = [] ; stale = False ; oldperiods = [] ; newperiods = [] ; adaptationsets = [] ; presentationtimeoffsets = [] ; lastcontentdurations = deque(maxlen = 10) ; eventtypesdiscovered = set() ; adsinfo = {} ; adinfo = {} ; trackingresponsedict = {} ; ptsmisalignment = False

  # Common initial settings
  now = time.perf_counter()
  nextstaletime = now + userargs['stale']
  nextdurationcalctime = now

  # Configure manifests download folder for this endpoint
  if userargs['manifests'] == True:
    manifestsfolder = Path(userargs['manifestsfolder'], renditionname)
  if userargs['segments'] == True:
    segmentsfolder = Path(userargs['segmentsfolder'], renditionname)
  if userargs['tracking'] == True:
    trackingfolder = Path(userargs['trackingfolder'], renditionname)

  # DASH monitor
  if endpoint['type'] == 'dash':

    # Initial settings
    fileextension = '.mpd'
    manifestinfo = {
      'url': endpoint['url'],
      'lastperiod': proberesponse['period'],
      'lastn': proberesponse['n'],
      'lastsegmentinfo': proberesponse.copy(), # for finding discontinuities and comparing last found segment info on 1st video representation
      'newsegmentinfo': proberesponse.copy(),
      'newsegmentspts': {} # for comparing t value across representations
    }

    logger.info('Started monitoring manifest URL ' + manifestinfo['url'])

    while not terminatethreads and not stoprunning.is_set():
      metricstopublish.clear() ; segmentinfo.clear() ; segmenttags.clear() ; oldperiods.clear() ; newperiods.clear() ; segmentcount = 0 ; discontinuitysequence = 0 ; foundlastsegment = False ; foundnewsegment = False ; durationsum = 0.0 ; durationsumforpdt = 0.0 ; foundpdt = False ; foundsupplementalproperty = False ; calculatemanifestduration = False ; manifestinfo['foundlastsegment'] = False ; manifestinfo['foundnewsegment'] = False ; manifestinfo['foundnewperiod'] = False ; manifestinfo['foundlastperiod'] = False ; manifestinfo['newsegmentspts'].clear() ; newcontentduration = 0.0
    
      mrequesttime = time.perf_counter()
    
      # Initialize session
      if startsession:
        sessionstarttime = mrequesttime ; sessioncontentduration = 0.0 ; startsession = False

      # Request manifest
      logger.debug('Requesting manifest')
      response, responsetime = request3(logger, {'Accept-Encoding': 'gzip'}, manifestinfo['url'], 'GET', 'manifest', metricstopublish)

      # Manifest response time
      manifestinfo['latency'] = responsetime
      if userargs['cwmetrics'] == True:
        metricstopublish['manifestresponsetime'] = manifestinfo['latency']
      
      if response:
        responsetext = getresponsetext(response, False)
        
        # Manifest size https://docs.python.org/3/library/email.compat32-message.html#email.message.Message
        manifestinfo['size'] = len(responsetext)
        if userargs['cwmetrics'] == True:
          metricstopublish['manifestsize'] = manifestinfo['size']

        # Save response
        if userargs['manifests'] == True:
          filepath = ''
          if userargs['gzip'] == True:
            foundcontentencodinggzip = False
            for i in response.headers.keys():
              if 'content-encoding' == i.lower():
                if response.headers[i] == 'gzip':
                  foundcontentencodinggzip = True
                  break
            if foundcontentencodinggzip:
              filepath = saveresponse(logger, response.data, manifestsfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + fileextension, False, False)
            else:
              filepath = saveresponse(logger, responsetext.decode('utf-8'), manifestsfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + fileextension, False, True)
          else:
            filepath = saveresponse(logger, responsetext.decode('utf-8'), manifestsfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + fileextension, False, False)
          if filepath:
            logger.debug('Saved file ' + str(filepath))
        
        if time.perf_counter() > nextdurationcalctime:
          calculatemanifestduration = True
          
        # Parse XML
        ns = {'default': 'urn:mpeg:dash:schema:mpd:2011', 'scte': 'urn:scte:scte35:2013:xml'}
        xmlroot = ET.fromstring(responsetext)
        xmlbaseurlglobal = xmlroot.find('default:BaseURL', ns)
        if xmlroot != None:
          xmlavailabilitystarttime = xmlroot.get('availabilityStartTime')
          # Check availability start time
          if xmlavailabilitystarttime:
            if 'xmlavailabilitystarttime' in manifestinfo.keys():
              if xmlavailabilitystarttime != manifestinfo['xmlavailabilitystarttime']:
                logger.warning('Manifest value has changed for availabilityStartTime from ' + str(manifestinfo['xmlavailabilitystarttime']) + ' to ' + str(xmlavailabilitystarttime))
                try:
                  manifestinfo['xmlavailabilitystarttime'] = xmlavailabilitystarttime
                  manifestinfo['availabilitystarttimedatetime'] = datetime.datetime.strptime(xmlavailabilitystarttime[0:19],'%Y-%m-%dT%H:%M:%S')
                except Exception:
                  logger.error('Error parsing availabilityStartTime UTC time: ' + str(xmlavailabilitystarttime))
            else:
              try:
                manifestinfo['xmlavailabilitystarttime'] = xmlavailabilitystarttime
                manifestinfo['availabilitystarttimedatetime'] = datetime.datetime.strptime(xmlavailabilitystarttime[0:19],'%Y-%m-%dT%H:%M:%S')
              except Exception:
                logger.error('Error parsing availabilityStartTime UTC time: ' + str(xmlavailabilitystarttime))
          xmlperiods = xmlroot.findall('default:Period', ns)
          for xmlperiod in xmlperiods:
            scteinfo.clear() ; segmentationdescriptors.clear() ; adaptationsets.clear() ; presentationtimeoffsets.clear()
            foundsupplementalproperty = False
            xmlperiodid = xmlperiod.get('id')
            if xmlperiodid != None:
              xmlbaseurl = xmlperiod.find('default:BaseURL', ns)

              # Collect periods information and later compare periods for integrity
              # workingperiod = {'periodid': xmlperiodid, 'baseurl': xmlbaseurl.text if xmlbaseurl != None else None}
              workingperiod = {'periodid': xmlperiodid}
              if manifestinfo['foundlastsegment']:
                newperiods.append(workingperiod)
              else:
                oldperiods.append(workingperiod)

              # Go only through segments in the last known period or a new period unless calculating manifest duration
              if xmlperiodid == manifestinfo['lastperiod'] or manifestinfo['foundlastsegment'] or calculatemanifestduration:
                if xmlperiodid == manifestinfo['lastperiod']:
                  manifestinfo['foundlastperiod'] = True
                segmentinfo['period'] = xmlperiodid

                # New period
                if manifestinfo['foundlastsegment']:
                  manifestinfo['foundnewperiod'] = True ; newadbreak = False ; newadduration = False

                  # Check duration of previous period
                  if 'lastperiodduration' in manifestinfo.keys():
                    if manifestinfo['lastperiodduration'] < 0.5:
                      logger.warning('Duration of period was ' + '{:.3f}'.format(manifestinfo['lastperiodduration']) + ' seconds, which is less than 500 ms')

                  # Calculate EMT ad break duration
                  if userargs['emt'] == True and 'adbreak' in manifestinfo.keys():
                    if manifestinfo['adbreak'] == True:
                      if 'lastperiodduration' in manifestinfo.keys():
                        if 'emtadbreakduration' in manifestinfo.keys():
                          manifestinfo['emtadbreakduration'] = manifestinfo['emtadbreakduration'] + manifestinfo['lastperiodduration']
                        else:
                          manifestinfo['emtadbreakduration'] = manifestinfo['lastperiodduration']

                  # Calculate ad break duration and delta when closing non EMT ad break
                  if userargs['emt'] == False and 'adbreak' in manifestinfo.keys():
                    if manifestinfo['adbreak'] == True:
                      if manifestinfo['advertisedadbreakduration'] > 0:
                        if 'lastperiodduration' in manifestinfo.keys():
                          adbreakdurationdelta = manifestinfo['lastperiodduration'] - manifestinfo['advertisedadbreakduration']
                          if adbreakdurationdelta > 1:
                            logger.warning('Ad break was longer than advertised by ' + '{:.3f}'.format(adbreakdurationdelta) + ' seconds, advertised: ' + str(manifestinfo['advertisedadbreakduration']) + ', actual: ' + '{:.3f}'.format(manifestinfo['lastperiodduration']))
                          if adbreakdurationdelta < -1:
                            logger.warning('Ad break was shorter than advertised by ' + '{:.3f}'.format(abs(adbreakdurationdelta)) + ' seconds, advertised: ' + str(manifestinfo['advertisedadbreakduration']) + ', actual: ' + '{:.3f}'.format(manifestinfo['lastperiodduration']))
                          if userargs['cwmetrics'] == True:
                            addmetricvalue(metricstopublish, 'addurationdelta', round(adbreakdurationdelta, 3))
                      if userargs['cwmetrics'] == True:
                        if 'lastperiodduration' in manifestinfo.keys():
                          addmetricvalue(metricstopublish, 'addurationactual', round(manifestinfo['lastperiodduration'], 3))

                  # EMT ad break start
                  if 'lastperiod' in manifestinfo.keys():
                    if userargs['emt'] == True and '_' in segmentinfo['period'] and '_' not in manifestinfo['lastperiod']:
                      if userargs['cwmetrics'] == True:
                        if 'adbreak' not in manifestinfo.keys():
                          addmetricvalue(metricstopublish, 'adbreak', 1)
                          manifestinfo['emtadbreakduration'] = 0
                        elif manifestinfo['adbreak'] == False:
                          addmetricvalue(metricstopublish, 'adbreak', 1)
                          manifestinfo['emtadbreakduration'] = 0
                      manifestinfo['adbreak'] = True
                      manifestinfo['trackingconfirmed'] = False
                      manifestinfo['adbreakstart'] = time.perf_counter()

                  # Capture scte info of a new period when non EMT
                  xmleventstream = xmlperiod.find('default:EventStream', ns)
                  if userargs['emt'] == False and xmleventstream != None:
                    xmlevent = xmleventstream.find('default:Event', ns)
                    if xmlevent != None:
                      xmlspliceinfosection = xmlevent.find('scte:SpliceInfoSection', ns)
                      if xmlspliceinfosection != None:
                        # Splice insert
                        xmlspliceinsert = xmlspliceinfosection.find('scte:SpliceInsert', ns)
                        if xmlspliceinsert != None:
                          scteinfo['spliceType'] = 'spliceInsert'
                          if xmlspliceinsert.get('outOfNetworkIndicator'):
                            scteinfo['outOfNetworkIndicator'] = xmlspliceinsert.get('outOfNetworkIndicator')
                          if xmlspliceinsert.get('spliceImmediateFlag'):
                            scteinfo['spliceImmediateFlag'] = xmlspliceinsert.get('spliceImmediateFlag')
                          if xmlspliceinsert.get('spliceEventId'):
                            scteinfo['spliceEventId'] = xmlspliceinsert.get('spliceEventId')
                          if xmlspliceinsert.get('availNum'):
                            scteinfo['availNum'] = xmlspliceinsert.get('availNum')
                          if xmlspliceinsert.get('uniqueProgramId'):
                            scteinfo['uniqueProgramId'] = xmlspliceinsert.get('uniqueProgramId')
                          xmlbreakduration = xmlspliceinsert.find('scte:BreakDuration', ns)
                          if xmlbreakduration != None:
                            if xmlbreakduration.get('duration'):
                              scteinfo['duration'] = xmlbreakduration.get('duration')
                              if xmleventstream.get('timescale'):
                                scteinfo['timescale'] = xmleventstream.get('timescale')
                                scteinfo['durationSec'] = '{:.3f}'.format(float(scteinfo['duration']) / float(xmleventstream.get('timescale')))
                            if xmlbreakduration.get('autoReturn'):
                              scteinfo['autoReturn'] = xmlbreakduration.get('autoReturn')

                        # Time signal
                        xmltimesignal = xmlspliceinfosection.find('scte:TimeSignal', ns)
                        if xmltimesignal != None:
                          scteinfo['spliceType'] = 'timeSignal'

                        # Segmentation descriptor
                        scteinfo['segmentationDescriptor'] = False
                        xmlsegmentationdescriptors = xmlspliceinfosection.findall('scte:SegmentationDescriptor', ns)
                        for xmlsegmentationdescriptor in xmlsegmentationdescriptors:
                          scteinfo['segmentationDescriptor'] = True ; segmentationdescriptorinfo.clear()
                          if xmlsegmentationdescriptor.get('segmentationEventId'):
                            segmentationdescriptorinfo['segmentationEventId'] = xmlsegmentationdescriptor.get('segmentationEventId')
                          if xmlsegmentationdescriptor.get('segmentationDuration'):
                            segmentationdescriptorinfo['segmentationDuration'] = xmlsegmentationdescriptor.get('segmentationDuration')
                            if xmleventstream.get('timescale'):
                              segmentationdescriptorinfo['segmentationDurationSec'] = '{:.3f}'.format(float(segmentationdescriptorinfo['segmentationDuration']) / float(xmleventstream.get('timescale')))
                          xmlsegmentationupid = xmlsegmentationdescriptor.find('scte:SegmentationUpid', ns)
                          if xmlsegmentationupid != None:
                            if xmlsegmentationupid.get('segmentationTypeId'):
                              segmentationdescriptorinfo['segmentationTypeId'] = xmlsegmentationupid.get('segmentationTypeId')
                              if xmlsegmentationupid.get('segmentationTypeId') in segmentationtypeidmap.keys():
                                segmentationdescriptorinfo['segmentationTypeName'] = segmentationtypeidmap[segmentationdescriptorinfo['segmentationTypeId']]
                              else:
                                segmentationdescriptorinfo['segmentationTypeName'] = 'unknown'
                          segmentationdescriptors.append(segmentationdescriptorinfo.copy())

                        # Check if ad break start
                        if 'outOfNetworkIndicator' in scteinfo.keys():
                          if scteinfo['outOfNetworkIndicator'] == 'true':
                            newadbreak = True
                        if not newadbreak:
                          for i in segmentationdescriptors:
                            if 'segmentationTypeId' in i.keys():
                              if i['segmentationTypeId'] in adbreakstartsegmentationtypeids:
                                newadbreak = True
                        if newadbreak:
                          # Check if ad break with duration
                          if 'durationSec' in scteinfo.keys():
                            newadduration = True
                            adduration = float(scteinfo['durationSec'])
                          if not newadduration:
                            for i in segmentationdescriptors:
                              if 'segmentationTypeId' in i.keys():
                                if i['segmentationTypeId'] in adbreakstartsegmentationtypeids:
                                  if 'segmentationDurationSec' in i.keys():
                                    newadduration = True
                                    adduration = float(i['segmentationDurationSec'])
                          # Check if nested ad break start and update manifest info with ad break start info
                          if 'adbreak' in manifestinfo.keys():
                            if manifestinfo['adbreak'] == True:
                              logger.warning('Nested ad break start')
                          manifestinfo['adbreak'] = True
                          if newadduration:
                            manifestinfo['advertisedadbreakduration'] = adduration
                          else:
                            manifestinfo['advertisedadbreakduration'] = 0.0

                        # Publish metrics
                        if userargs['cwmetrics']:
                          if newadbreak:
                            addmetricvalue(metricstopublish, 'adbreak', 1)
                            if 'availNum' in scteinfo.keys():
                              addmetricvalue(metricstopublish, 'adavailnum', int(scteinfo['availNum']))
                            # if 'spliceEventId' in scteinfo.keys():
                            #   addmetricvalue(metricstopublish, 'adspliceeventid', int(scteinfo['spliceEventId']))
                          if newadduration:
                            addmetricvalue(metricstopublish, 'addurationadvertised', adduration)

                  logger.info('Found new period: {\'id\': ' + xmlperiodid + ', \'SCTE35 - signal type\': ' + str(scteinfo) + ', \'segmentation descriptor info\': ' + str(segmentationdescriptors) + '}')

                  manifestinfo['lastperiodduration'] = 0.0

                  # Check if ad break ended for non EMT
                  if userargs['emt'] == False:
                    if not newadbreak:
                      manifestinfo['adbreak'] = False
                  # Check if ad break ended for EMT
                  else:
                    if 'adbreak' in manifestinfo.keys():
                      if '_' not in segmentinfo['period'] and manifestinfo['adbreak'] == True:
                        if 'emtadbreakduration' in manifestinfo.keys():
                          if userargs['cwmetrics'] == True:
                            addmetricvalue(metricstopublish, 'addurationactual', round(manifestinfo['emtadbreakduration'], 3))
                        manifestinfo['adbreak'] = False
                        if 'trackingconfirmed' in manifestinfo.keys() and endpoint['tracking'] != '':
                          if manifestinfo['trackingconfirmed'] == False and userargs['trackingrequests'] == True:
                            logger.warning('Did not find expected tracking info during ad break')

                  # Check adaptation sets of new period
                  xmladaptationsets = xmlperiod.findall('default:AdaptationSet', ns)
                  for xmladaptationset in xmladaptationsets:
                    adaptationsets.append(xmladaptationset.attrib)
                  checkadaptationsets(logger, adaptationsets, xmlperiodid)

                  # Check presentation time offsets of new period
                  for xmladaptationset in xmladaptationsets:
                    xmladaptationsetmimetype = xmladaptationset.get('mimeType')
                    if xmladaptationsetmimetype != None and xmladaptationsetmimetype != 'image/jpeg':
                      xmlrepresentations = xmladaptationset.findall('default:Representation', ns)
                      for xmlrepresentation in xmlrepresentations:
                        xmlsegmenttemplates = xmlrepresentation.findall('default:SegmentTemplate', ns)
                        for xmlsegmenttemplate in xmlsegmenttemplates:
                          xmlpresentationtimeoffset = xmlsegmenttemplate.get('presentationTimeOffset')
                          xmltimescale = xmlsegmenttemplate.get('timescale')
                          if xmlpresentationtimeoffset != None and xmltimescale != None:
                            presentationtimeoffsets.append({'mimeType': xmladaptationsetmimetype, 'presentationTimeOffset': xmlpresentationtimeoffset, 'timescale': xmltimescale})
                  if presentationtimeoffsets:
                    checkpresentationtimeoffsets(logger, presentationtimeoffsets, xmlperiodid)

                # Find supplemental property
                xmlsupplementalproperty = xmlperiod.find('default:SupplementalProperty', ns)
                if xmlsupplementalproperty != None:
                  xmlsupplementalpropertyutctime = xmlsupplementalproperty.get('value')
                  if xmlsupplementalpropertyutctime != None:
                    try:
                      manifestinfo['spdatetime'] = datetime.datetime.strptime(xmlsupplementalpropertyutctime, '%Y-%m-%dT%H:%M:%S.%fZ')
                      foundsupplementalproperty = True
                    except Exception:
                      try:
                        manifestinfo['spdatetime'] = datetime.datetime.strptime(xmlsupplementalpropertyutctime, '%Y-%m-%dT%H:%M:%SZ')
                        foundsupplementalproperty = True
                      except Exception:
                        logger.error('Error parsing SupplementalProperty UTC time: ' + str(xmlsupplementalpropertyutctime))

                # Go through adaptation sets
                xmladaptationsets = xmlperiod.findall('default:AdaptationSet', ns)
                for xmladaptationset in xmladaptationsets:
                  xmladaptationsetmimetype = xmladaptationset.get('mimeType')
                  xmlrepresentations = xmladaptationset.findall('default:Representation', ns)
                  xmlsegmenttemplatecompact = xmladaptationset.find('default:SegmentTemplate', ns)
                  if xmladaptationsetmimetype and xmlrepresentations != None:
                    if xmladaptationsetmimetype == 'video/mp4':
                      foundrepresentationid = False
                      for xmlrepresentation in xmlrepresentations:
                        xmlrepresentationid = xmlrepresentation.get('id')
                        if xmlrepresentationid:
                          if rendition['ID'] == xmlrepresentationid:
                            foundrepresentationid = True
                            break
                      if foundrepresentationid == False:
                        logger.warning('Updated primary monitoring representation id because representation id ' + rendition['ID'] + ' is not present in period ' + segmentinfo['period'])
                        rendition['ID'] = xmlrepresentations[0].get('id')
                  for xmlrepresentation in xmlrepresentations:
                    foundlastsegment = True if manifestinfo['foundnewperiod'] == True else False
                    xmlrepresentationid = xmlrepresentation.get('id')
                    if xmladaptationsetmimetype and xmlrepresentationid:
                      # Go through segments in segment template
                      xmlsegmenttemplate = xmlrepresentation.find('default:SegmentTemplate', ns)
                      if xmlsegmenttemplate == None:
                        xmlsegmenttemplate = xmlsegmenttemplatecompact
                      if xmlsegmenttemplate != None:
                        xmlinitialization = xmlsegmenttemplate.get('initialization')
                        xmlmedia = xmlsegmenttemplate.get('media')
                        xmlstartnumber = xmlsegmenttemplate.get('startNumber')
                        xmltimescale = xmlsegmenttemplate.get('timescale')
                        xmlpresentationtimeoffset = xmlsegmenttemplate.get('presentationTimeOffset')
                        if xmlstartnumber and xmltimescale:
                          segmentinfo['n'] = int(xmlstartnumber) - 1
                          segmentinfo['timescale'] = int(xmltimescale)
                          if xmlpresentationtimeoffset != None:
                            segmentinfo['pto'] = int(xmlpresentationtimeoffset)
                          else:
                            segmentinfo.pop('pto', None)
                          xmlsegmenttimeline = xmlsegmenttemplate.find('default:SegmentTimeline', ns)
                          if xmlsegmenttimeline != None:
                            for element in xmlsegmenttimeline:
                              # Go through all segments in 1st video representation
                              if xmladaptationsetmimetype == 'video/mp4' and xmlrepresentationid == rendition['ID']:
                                if element.tag == '{' + ns['default'] + '}' + 'S': # '{urn:mpeg:dash:schema:mpd:2011}S'
                                  xmlt = element.get('t') ; xmld = element.get('d') ; xmlr = element.get('r')
                                  sr = int(xmlr) if xmlr else 0
                                  if xmlt and xmld:
                                    segmentinfo['d'] = int(xmld) ; segmentinfo['dsec'] = segmentinfo['d'] / segmentinfo['timescale'] ; segmentinfo['t'] = int(xmlt)
                                    # Check for discontinuity
                                    if foundlastsegment and 'nextt' in manifestinfo['lastsegmentinfo'].keys():
                                      if manifestinfo['lastsegmentinfo']['nextt'] != segmentinfo['t']:
                                        if manifestinfo['lastsegmentinfo']['period'] == segmentinfo['period']:
                                          logger.warning('Discontinuity inside period ' + str(segmentinfo['period']))
                                        if userargs['cwmetrics'] == True:
                                          addmetricvalue(metricstopublish, 'discontinuity', 1)
                                    for i in range(sr + 1):
                                      segmentinfo['n'] = segmentinfo['n'] + 1 ; segmentinfo['t'] = int(xmlt) + segmentinfo['d'] * i ; segmentinfo['nextt'] = segmentinfo['t'] + segmentinfo['d']
                                      manifestinfo['lastsegmentinfo'] = segmentinfo.copy()
                                      if calculatemanifestduration:
                                        durationsum = durationsum + segmentinfo['dsec']
                                      # If last known segment
                                      if xmlperiodid == manifestinfo['lastperiod'] and segmentinfo['n'] == manifestinfo['lastn']:
                                        # Compare segment info with previous manifest request segment info
                                        if 'newsegmentinfo' in manifestinfo.keys():
                                          comparelastsegment(logger, segmentinfo, manifestinfo['newsegmentinfo'])
                                        foundlastsegment = True ; manifestinfo['foundlastsegment'] = True
                                      # If new segment
                                      elif foundlastsegment:
                                        segmentinfo['name'] = xmlmedia if xmlmedia != None else ''
                                        segmentinfo['name'] = re.sub(r'\$Number\$', str(segmentinfo['n']), segmentinfo['name'])
                                        # Publish segment duration
                                        addmetricvalue(metricstopublish, 'segmentduration', round(segmentinfo['dsec'], 3))
                                        match = re.search(r'(\$Number\%)([0-9]+)', xmlmedia)
                                        if match:
                                          numberprefixlength = match.group(2)
                                          segmentinfo['name'] = re.sub(r'\$Number\S+\$', str(segmentinfo['n']).zfill(int(numberprefixlength)), segmentinfo['name'])
                                        segmentinfo['name'] = re.sub(r'\$Representation\S*\$', rendition['ID'], segmentinfo['name'])
                                        segmentinfo['url'] = urljoin(endpoint['url'], segmentinfo['name'])
                                        if xmlbaseurlglobal != None:
                                          segmentinfo['url'] = urljoin(endpoint['url'], xmlbaseurlglobal.text)
                                          segmentinfo['url'] = urljoin(segmentinfo['url'], segmentinfo['name'])
                                        if xmlbaseurl != None:
                                          segmentinfo['url'] = urljoin(endpoint['url'], xmlbaseurl.text)
                                          segmentinfo['url'] = urljoin(segmentinfo['url'], segmentinfo['name'])
                                        logger.debug('Found new segment in the first video representation, segment info: ' + str(segmentinfo))
                                        manifestinfo['foundnewsegment'] = True ; manifestinfo['newsegmentinfo'] = segmentinfo.copy()
                                        sessioncontentduration = sessioncontentduration + segmentinfo['dsec'] ; newcontentduration = newcontentduration + segmentinfo['dsec']
                                        if 'lastperiodduration' in manifestinfo.keys():
                                          manifestinfo['lastperiodduration'] = manifestinfo['lastperiodduration'] + segmentinfo['dsec']
                                        # Send segment request or download segment
                                        if userargs['segmentrequests'] == True or userargs['segments'] == True:
                                          if userargs['segmentrequests'] == True:
                                            segmentresponse, segmentresponsetime = request3(logger, {}, segmentinfo['url'], 'HEAD', 'segment', metricstopublish)
                                          elif userargs['segments'] == True:
                                            segmentresponse, segmentresponsetime = request3(logger, {}, segmentinfo['url'], 'GET', 'segment', metricstopublish)
                                          # Segment response time
                                          srtime = segmentresponsetime
                                          if userargs['cwmetrics'] == True:
                                            addmetricvalue(metricstopublish, 'segmentresponsetime', srtime)
                                          if segmentresponse != None:
                                            # Save response
                                            if userargs['segments'] == True:
                                              segmentname = segmentinfo['name'].split('?')[0]
                                              filepath = saveresponse(logger, segmentresponse.data, segmentsfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + '_' + segmentname, True, False)
                                              if filepath:
                                                logger.debug('Saved file ' + str(filepath))

                                            # Check segment size
                                            foundcontentlength = False
                                            for i in segmentresponse.headers.keys():
                                              if 'content-length' == i.lower():
                                                foundcontentlength = True
                                                break
                                            if foundcontentlength:
                                              if userargs['cwmetrics'] == True:
                                                addmetricvalue(metricstopublish, 'segmentsize', int(segmentresponse.headers[i]))

                                        # Collect segment PTS information
                                        addsegmenttonewsegments(manifestinfo, segmentinfo, xmlperiodid)
                                elif element.tag == '{' + ns['default'] + '}' + 'Pattern': # '{urn:mpeg:dash:schema:mpd:2011}Pattern'
                                  xmlt = element.get('t') ; xmlr = element.get('r')
                                  pr = int(xmlr) if xmlr else 0
                                  if xmlt:
                                    segmentinfo['t'] = int(xmlt)
                                    # Check for discontinuity
                                    if foundlastsegment and 'nextt' in manifestinfo['lastsegmentinfo'].keys():
                                      if manifestinfo['lastsegmentinfo']['nextt'] != segmentinfo['t']:
                                        if manifestinfo['lastsegmentinfo']['period'] == segmentinfo['period']:
                                          logger.warning('Discontinuity inside period' + str(segmentinfo['period']))
                                        if userargs['cwmetrics'] == True:
                                          addmetricvalue(metricstopublish, 'discontinuity', 1)
                                    helpt = int(xmlt)
                                    for i in range(pr + 1):
                                      xmlss = element.findall('default:S', ns)
                                      for xmls in xmlss:
                                        xmld = xmls.get('d') ; xmlr = xmls.get('r')
                                        sr = int(xmlr) if xmlr else 0
                                        if xmld:
                                          segmentinfo['d'] = int(xmld) ; segmentinfo['dsec'] = segmentinfo['d'] / segmentinfo['timescale']
                                          for i in range(sr + 1):
                                            segmentinfo['n'] = segmentinfo['n'] + 1 ; segmentinfo['t'] = helpt + segmentinfo['d'] * i ; segmentinfo['nextt'] = segmentinfo['t'] + segmentinfo['d']
                                            manifestinfo['lastsegmentinfo'] = segmentinfo.copy()
                                            if calculatemanifestduration:
                                              durationsum = durationsum + segmentinfo['dsec']
                                            # If last known segment
                                            if xmlperiodid == manifestinfo['lastperiod'] and segmentinfo['n'] == manifestinfo['lastn']:
                                              # Compare segment info with previous manifest request segment info
                                              if 'newsegmentinfo' in manifestinfo.keys():
                                                comparelastsegment(logger, segmentinfo, manifestinfo['newsegmentinfo'])
                                              foundlastsegment = True ; manifestinfo['foundlastsegment'] = True
                                            # If new segment
                                            elif foundlastsegment:
                                              segmentinfo['name'] = xmlmedia if xmlmedia != None else ''
                                              segmentinfo['name'] = re.sub(r'\$Number\$', str(segmentinfo['n']), segmentinfo['name'])
                                              # Publish segment duration
                                              addmetricvalue(metricstopublish, 'segmentduration', round(segmentinfo['dsec'], 3))
                                              match = re.search(r'(\$Number\%)([0-9]+)', xmlmedia)
                                              if match:
                                                numberprefixlength = match.group(2)
                                                segmentinfo['name'] = re.sub(r'\$Number\S+\$', str(segmentinfo['n']).zfill(int(numberprefixlength)), segmentinfo['name'])
                                              segmentinfo['name'] = re.sub(r'\$Representation\S*\$', rendition['ID'], segmentinfo['name'])
                                              segmentinfo['url'] = urljoin(endpoint['url'], segmentinfo['name'])
                                              if xmlbaseurlglobal != None:
                                                segmentinfo['url'] = urljoin(endpoint['url'], xmlbaseurlglobal.text)
                                                segmentinfo['url'] = urljoin(segmentinfo['url'], segmentinfo['name'])
                                              if xmlbaseurl != None:
                                                segmentinfo['url'] = urljoin(endpoint['url'], xmlbaseurl.text)
                                                segmentinfo['url'] = urljoin(segmentinfo['url'], segmentinfo['name'])
                                              logger.debug('Found new segment in the first video representation, segment info: ' + str(segmentinfo))
                                              sessioncontentduration = sessioncontentduration + segmentinfo['dsec'] ; newcontentduration = newcontentduration + segmentinfo['dsec']
                                              manifestinfo['foundnewsegment'] = True ; manifestinfo['newsegmentinfo'] = segmentinfo.copy()
                                              if 'lastperiodduration' in manifestinfo.keys():
                                                manifestinfo['lastperiodduration'] = manifestinfo['lastperiodduration'] + segmentinfo['dsec']
                                              # Send segment request or download segment
                                              if userargs['segmentrequests'] == True or userargs['segments'] == True:
                                                if userargs['segmentrequests'] == True:
                                                  segmentresponse, segmentresponsetime = request3(logger, {}, segmentinfo['url'], 'HEAD', 'segment', metricstopublish)
                                                elif userargs['segments'] == True:
                                                  segmentresponse, segmentresponsetime = request3(logger, {}, segmentinfo['url'], 'GET', 'segment', metricstopublish)
                                                if segmentresponse != None:
                                                  srtime = segmentresponsetime
                                                  if userargs['cwmetrics'] == True:
                                                    addmetricvalue(metricstopublish, 'segmentresponsetime', srtime)
                                                  # Save response
                                                  if userargs['segments'] == True:
                                                    segmentname = segmentinfo['name'].split('?')[0]
                                                    filepath = saveresponse(logger, segmentresponse.data, segmentsfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + '_' + segmentname, True, False)
                                                    if filepath:
                                                      logger.debug('Saved file ' + str(filepath))

                                                  # Check segment size
                                                  foundcontentlength = False
                                                  for i in segmentresponse.headers.keys():
                                                    if 'content-length' == i.lower():
                                                      foundcontentlength = True
                                                      break
                                                  if foundcontentlength:
                                                    if userargs['cwmetrics'] == True:
                                                      addmetricvalue(metricstopublish, 'segmentsize', int(segmentresponse.headers[i]))
                                              # Collect segment PTS information
                                              addsegmenttonewsegments(manifestinfo, segmentinfo, xmlperiodid)
                                          helpt = segmentinfo['t'] + segmentinfo['d']
                              elif xmladaptationsetmimetype == 'video/mp4' or xmladaptationsetmimetype == 'audio/mp4' or xmladaptationsetmimetype == 'application/mp4':
                                if element.tag == '{' + ns['default'] + '}' + 'S': # '{urn:mpeg:dash:schema:mpd:2011}S'
                                  xmlt = element.get('t') ; xmld = element.get('d') ; xmlr = element.get('r')
                                  sr = int(xmlr) if xmlr else 0
                                  if xmlt and xmld:
                                    segmentinfo['d'] = int(xmld) ; segmentinfo['dsec'] = segmentinfo['d'] / segmentinfo['timescale'] ; segmentinfo['t'] = int(xmlt)
                                    for i in range(sr + 1):
                                      segmentinfo['n'] = segmentinfo['n'] + 1 ; segmentinfo['t'] = int(xmlt) + segmentinfo['d'] * i ; segmentinfo['nextt'] = segmentinfo['t'] + segmentinfo['d']
                                      # If last known segment
                                      if xmlperiodid == manifestinfo['lastperiod'] and segmentinfo['n'] == manifestinfo['lastn']:
                                        foundlastsegment = True
                                      # If new segment
                                      elif foundlastsegment:
                                        addsegmenttonewsegments(manifestinfo, segmentinfo, xmlperiodid)
                                elif element.tag == '{' + ns['default'] + '}' + 'Pattern': # '{urn:mpeg:dash:schema:mpd:2011}Pattern'
                                  xmlt = element.get('t') ; xmlr = element.get('r')
                                  pr = int(xmlr) if xmlr else 0
                                  if xmlt:
                                    segmentinfo['t'] = int(xmlt)
                                    helpt = int(xmlt)
                                    for i in range(pr + 1):
                                      xmlss = element.findall('default:S', ns)
                                      for xmls in xmlss:
                                        xmld = xmls.get('d') ; xmlr = xmls.get('r')
                                        sr = int(xmlr) if xmlr else 0
                                        if xmld:
                                          segmentinfo['d'] = int(xmld) ; segmentinfo['dsec'] = segmentinfo['d'] / segmentinfo['timescale']
                                          for i in range(sr + 1):
                                            segmentinfo['n'] = segmentinfo['n'] + 1 ; segmentinfo['t'] = helpt + segmentinfo['d'] * i ; segmentinfo['nextt'] = segmentinfo['t'] + segmentinfo['d']
                                            # If last known segment
                                            if xmlperiodid == manifestinfo['lastperiod'] and segmentinfo['n'] == manifestinfo['lastn']:
                                              foundlastsegment = True
                                            # If new segment
                                            elif foundlastsegment:
                                              addsegmenttonewsegments(manifestinfo, segmentinfo, xmlperiodid)
                                          helpt = segmentinfo['t'] + segmentinfo['d']
                      else:
                        logger.error('Did not find any SegmentTemplate')
        # After parsing manifest
        if manifestinfo['foundnewsegment']:
          # Update stale time
          stale = False
          nextstaletime = time.perf_counter() + userargs['stale']
          # Update last segment info
          manifestinfo['lastperiod'] = manifestinfo['lastsegmentinfo']['period'] ; manifestinfo['lastn'] = manifestinfo['lastsegmentinfo']['n']
          # Compare pts value across all representations for each new segment
          for key in manifestinfo['newsegmentspts']:
            maxpts = max(manifestinfo['newsegmentspts'][key]) ; minpts = min(manifestinfo['newsegmentspts'][key])
            ptsdelta = round(abs(maxpts - minpts), 3)
            if ptsdelta > 0.1:
              if ptsmisalignment == False:
                logger.warning('Possible lip sync issue, maximum absolute PTS delta across representations: ' + str(ptsdelta) + ' sec, segment number: ' + str(key[1])  + ', PTS values: ' + str(manifestinfo['newsegmentspts'][key]))
              ptsmisalignment = True
            else:
              if ptsmisalignment == True:
                logger.info('PTS delta across representations is now within 100 ms')
              ptsmisalignment = False
            if userargs['cwmetrics'] == True:
              addmetricvalue(metricstopublish, 'ptsdelta', ptsdelta)

        # Check if found last segment
        if not manifestinfo['foundlastsegment']:
          lastmanifestheaders = str(manifestinfo['lastmanifestheaders']) if 'lastmanifestheaders' in manifestinfo.keys() else '[]'
          logger.warning('Last segment {\'period\': ' + manifestinfo['lastperiod'] + ', \'n\': ' + str(manifestinfo['lastn']) + '}' + ' not found, previous manifest response headers: ' + lastmanifestheaders + ', manifest response headers now: ' + str(response.headers.items()) )

        # Check PDT delta (includes the duration of last segment)
        if foundsupplementalproperty:
          if all(i in manifestinfo['lastsegmentinfo'].keys() for i in ['t', 'd', 'timescale']):
            if 'pto' in manifestinfo['lastsegmentinfo'].keys():
              helpt = (manifestinfo['lastsegmentinfo']['t'] + manifestinfo['lastsegmentinfo']['d'] - manifestinfo['lastsegmentinfo']['pto']) / manifestinfo['lastsegmentinfo']['timescale']
            else:
              helpt = (manifestinfo['lastsegmentinfo']['t'] + manifestinfo['lastsegmentinfo']['d']) / manifestinfo['lastsegmentinfo']['timescale']
            manifestinfo['pdtdelta'] = (manifestinfo['spdatetime'] - datetime.datetime.utcnow()).total_seconds() + helpt
            if userargs['cwmetrics'] == True and manifestinfo['foundnewsegment']:
              metricstopublish['pdtdelta'] = round(manifestinfo['pdtdelta'])

        # Compare manifests
        if manifestinfo['foundlastsegment']:
          if 'periods' in manifestinfo.keys():
            # Check if all not-new periods in this request are present in previous request
            if not all(x in manifestinfo['periods'] for x in oldperiods):
              lastmanifestheaders = str(manifestinfo['lastmanifestheaders']) if 'lastmanifestheaders' in manifestinfo.keys() else '[]'
              logger.warning('Inconsistency in manifest periods, previous manifest response headers: ' + lastmanifestheaders + ', this manifest response headers: ' + str(response.headers.items()) + ', these periods: ' + str(oldperiods) + ' are not subset of these periods: ' + str(manifestinfo['periods']))

        # Save all periods
        manifestinfo['periods'] = oldperiods + newperiods         

        # Manifest duration
        if calculatemanifestduration:
          if userargs['cwmetrics'] == True:
            metricstopublish['manifestduration'] = round(durationsum / 60, 1)
          nextdurationcalctime = time.perf_counter() + 300

        # Get tracking
        if endpoint['tracking'] != '':
          if userargs['trackingrequests'] == True:
            foundplayerplayhead = False ; playerplayhead = 0.0
            if 'availabilitystarttimedatetime' in manifestinfo.keys():
              playerplayhead = (datetime.datetime.utcnow() - manifestinfo['availabilitystarttimedatetime']).total_seconds()
              foundplayerplayhead = True
            if userargs['playheadawaretracking'] and foundplayerplayhead:
              trackingurl = endpoint['tracking'] + '?aws.playheadPositionInSeconds=' + str(round(playerplayhead))
            else:
              trackingurl = endpoint['tracking']
            trackingresponse, trackingresponsetime = request3(logger, {'Accept-Encoding': 'gzip'}, trackingurl, 'GET', 'tracking', metricstopublish)
            if userargs['cwmetrics'] == True:
              metricstopublish['trackingresponsetime'] = trackingresponsetime
            if trackingresponse:
              if all(i in manifestinfo.keys() for i in ['adbreak', 'adbreakstart', 'trackingconfirmed']):
                if manifestinfo['adbreak'] == True:
                  # Parse tracking response
                  trackingresponsetext = getresponsetext(trackingresponse, True) ; trackingresponsedict.clear()
                  try:
                    trackingresponsedict = json.loads(trackingresponsetext)
                  except json.decoder.JSONDecodeError:
                    logger.error('Tracking response could not be converted to json')
                  if trackingresponsedict:
                    if 'avails' in trackingresponsedict.keys():
                      for avail in trackingresponsedict['avails']:
                        if all(i in avail.keys() for i in ['availId', 'durationInSeconds', 'startTimeInSeconds']):
                          if float(avail['startTimeInSeconds']) <= playerplayhead <= float(avail['startTimeInSeconds']) + float(avail['durationInSeconds']):
                            availadscount = 0 ; adsinfo.clear()
                            if 'ads' in avail.keys():
                              availadscount = len(avail['ads'])
                              if manifestinfo['trackingconfirmed'] == False:
                                for index, ad in enumerate(avail['ads']):
                                  adinfo.clear()
                                  if 'adId' in ad.keys():
                                    adinfo['adId'] = ad['adId']
                                  if 'durationInSeconds' in ad.keys():
                                    adinfo['durationInSeconds'] = ad['durationInSeconds']
                                  if 'creativeId' in ad.keys():
                                    adinfo['creativeId'] = ad['creativeId']
                                  if 'adTitle' in ad.keys():
                                    adinfo['adTitle'] = ad['adTitle']
                                  adsinfo[str(index + 1)] = adinfo.copy()
                              if userargs['checktrackingevents'] == True:
                                for index, ad in enumerate(avail['ads']):
                                  eventtypesdiscovered.clear()
                                  if 'trackingEvents' in ad.keys():
                                    for trackingevent in ad['trackingEvents']:
                                      if 'eventType' in trackingevent.keys():
                                        eventtypesdiscovered.add(trackingevent['eventType'])
                                      else:
                                        logger.warning('Missing eventType in trackingEvent in avail')
                                    if len(eventtypestolookfor - eventtypesdiscovered) > 0:
                                      logger.warning('Missing event types in avail ad: ' + str(eventtypestolookfor - eventtypesdiscovered))
                                  else:
                                    logger.warning('Missing trackingEvents in ad')
                            if manifestinfo['trackingconfirmed'] == False:
                              manifestinfo['trackingconfirmed'] = True
                              timesinceadbreakstart = round(time.perf_counter() - manifestinfo['adbreakstart'])
                              if foundplayerplayhead:
                                playerplayheaddelta = round(playerplayhead - avail['startTimeInSeconds'])
                                if abs(playerplayheaddelta) > userargs['frequency'] * 3 or abs(timesinceadbreakstart) > userargs['frequency'] * 3:
                                  logger.warning('Found avail in tracking response, but playhead is drifted, avail id: ' + str(avail['availId']) + ', duration: ' + str(avail['durationInSeconds']) + ', ads count: ' + str(availadscount) + ', ads info: ' + str(adsinfo) + ', time since ad break start: ' + str(timesinceadbreakstart) + ' sec, playhead: ' + str(round(playerplayhead)) + ' sec, avail startTimeInSeconds: ' + str(round(avail['startTimeInSeconds'])) + ' sec, delta: ' + str(playerplayheaddelta) + ' sec, tracking url: ' + trackingurl + ', response headers: ' + str(trackingresponse.headers.items()))
                                else:
                                  logger.info('Found avail in tracking response, avail id: ' + str(avail['availId']) + ', duration: ' + str(avail['durationInSeconds']) + ', ads count: ' + str(availadscount) + ', ads info: ' + str(adsinfo) + ', time since ad break start: ' + str(timesinceadbreakstart) + ' sec, playhead: ' + str(round(playerplayhead)) + ' sec, avail startTimeInSeconds: ' + str(round(avail['startTimeInSeconds'])) + ' sec, delta: ' + str(playerplayheaddelta) + ' sec, tracking url: ' + trackingurl)
                              else:
                                logger.warning('Found avail in tracking response, but cannot get playhead, avail id: ' + str(avail['availId']) + ', duration: ' + str(avail['durationInSeconds']) + ', ads count: ' + str(availadscount) + ', time since ad break start: ' + str(timesinceadbreakstart) + ' sec, tracking url: ' + trackingurl + ', response headers: ' + str(trackingresponse.headers.items()))
                        else:
                          logger.warning('Missing some important fields in avail')
                    else:
                      logger.warning('No avails in tracking response')
                  else:
                    logger.warning('Empty tracking response')
              # Save tracking response
              if userargs['tracking']:
                filepath = ''
                if userargs['gzip'] == True:
                  foundcontentencodinggzip = False
                  for i in trackingresponse.headers.keys():
                    if 'content-encoding' == i.lower():
                      if trackingresponse.headers[i] == 'gzip':
                        foundcontentencodinggzip = True
                        break
                  if foundcontentencodinggzip:
                    filepath = saveresponse(logger, trackingresponse.data, trackingfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + '.json', False, False)
                  else:
                    filepath = saveresponse(logger, getresponsetext(trackingresponse, True), trackingfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + '.json', False, True)
                else:
                  filepath = saveresponse(logger, getresponsetext(trackingresponse, True), trackingfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + '.json', False, False)
                if filepath:
                  logger.debug('Saved file ' + str(filepath))

        # Check for new content shortage
        lastcontentdurations.append(newcontentduration)
        if len(lastcontentdurations) == lastcontentdurations.maxlen:
          lasttwocontentdurations = 0.0;
          goahead = True
          for i in range(0, 10):
            if not lastcontentdurations[i] > 0 and i < 8:
              goahead = False
            if i > 7:
              lasttwocontentdurations = lasttwocontentdurations + lastcontentdurations[i]
          if goahead == True and lasttwocontentdurations < 0.25 * 2 * userargs['frequency']:
            lastmanifestheaders = str(manifestinfo['lastmanifestheaders']) if 'lastmanifestheaders' in manifestinfo.keys() else '[]'
            thismanifestheaders = str(response.headers.items()) if response else '[]'
            logger.warning('Content shortage during last 2 manifest requests. New content duration of last 10 manifest requests: ' + str(lastcontentdurations) + ', previous manifest response headers: ' + lastmanifestheaders + ', this manifest response headers: ' + thismanifestheaders)
            if userargs['cwmetrics'] == True:
              metricstopublish['contentshortage'] = 1
              
        # Update last manifest headers
        manifestinfo['lastmanifestheaders'] = response.headers.items()
        
      # If timeout or error response 
      else:
        pass

      # Check input buffer size
      inputbuffersize = int(userargs['initialinputbuffersize'] - (mrequesttime - sessionstarttime) + sessioncontentduration)
      if userargs['cwmetrics'] == True:
        metricstopublish['inputbuffersize'] = inputbuffersize
      if inputbuffersize < 0:
        if manifestinfo['foundnewsegment']:
          startsession = True

      # Check if stale
      now = time.perf_counter()
      if now > nextstaletime:
        stale = True
      if stale:
        manifestheaders = str(response.headers.items()) if response else ''
        if manifestheaders:
          logger.warning('Stale manifest, last manifest headers: ' + manifestheaders)
        else:
          logger.warning('Stale manifest')
        if userargs['cwmetrics'] == True:
          metricstopublish['stale'] = 1

      # Publish metrics
      if userargs['cwmetrics'] == True:
        publishmetrics(logger, endpoint, renditionname, metricstopublish)
    
      # Stop if stale and rendition is from primary manifest
      if stale and fromprimary:
        break

      # Wait between requests
      waittime = mrequesttime - time.perf_counter() + userargs['frequency']
      if waittime > 0:
        time.sleep(waittime)
      elif not calculatemanifestduration and waittime < -1:
        logger.error('Negative wait time ' + '{:.3f}'.format(waittime) + ' sec between manifest requests')
  
  # HLS monitor      
  elif endpoint['type'] == 'hls':

    # Initial settings
    fileextension = '.m3u8'
    manifestinfo['lastmediasequence'] = proberesponse['lastmediasequence']
    manifestinfo['initialmanifestduration'] = proberesponse['manifestduration']
    manifestinfo['url'] = rendition['URL']

    logger.info('Started monitoring manifest URL ' + manifestinfo['url'])

    # Loop until terminated by parent thread
    while not terminatethreads and not stoprunning.is_set():
      metricstopublish.clear() ; segmentinfo.clear() ; segmenttags.clear() ; segmentcount = 0 ; discontinuitysequence = 0 ; foundlastsegment = False ; foundnewsegment = False ; durationsum = 0.0 ; durationsumforpdt = 0.0 ; foundpdt = False ; lastsequenceofthismanifest = 0 ; samefirstsegment = False ; calculatemanifestduration = False ; manifestinfo['foundlastsegment'] = False ; manifestinfo['foundnewsegment'] = False ; newcontentduration = 0.0 ; gonethroughheaders = False

      mrequesttime = time.perf_counter()
    
      # Initialize session
      if startsession:
        sessionstarttime = mrequesttime ; sessioncontentduration = 0.0 ; startsession = False

      # Request manifest
      logger.debug('Requesting manifest')
      response, responsetime = request3(logger, {'Accept-Encoding': 'gzip'}, manifestinfo['url'], 'GET', 'manifest', metricstopublish)
      
      # Manifest response time
      manifestinfo['latency'] = responsetime
      if userargs['cwmetrics'] == True:
        metricstopublish['manifestresponsetime'] = manifestinfo['latency']

      if response:
        responsetext = getresponsetext(response, True)
        
        # Manifest size
        manifestinfo['size'] = len(responsetext)
        if userargs['cwmetrics'] == True:
          metricstopublish['manifestsize'] = manifestinfo['size']
          
        # Save response
        if userargs['manifests'] == True:
          filepath = ''
          if userargs['gzip'] == True:
            foundcontentencodinggzip = False
            for i in response.headers.keys():
              if 'content-encoding' == i.lower():
                if response.headers[i] == 'gzip':
                  foundcontentencodinggzip = True
                  break
            if foundcontentencodinggzip:
              filepath = saveresponse(logger, response.data, manifestsfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + fileextension, False, False)
            else:
              filepath = saveresponse(logger, responsetext, manifestsfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + fileextension, False, True)
          else:
            filepath = saveresponse(logger, responsetext, manifestsfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + fileextension, False, False)
          if filepath:
            logger.debug('Saved file ' + str(filepath))

        for line in responsetext.split('\n'):
          line = line.strip()
          if len(line) == 0:
            continue

          # EXT
          if line.startswith('#EXT'):
            segmenttags.append(line)

          if gonethroughheaders == False:
            # EXT-X-VERSION
            if line.startswith('#EXT-X-VERSION:'):
              match = re.search(r'\d+', line)
              if match:
                # Check if changed
                comparevalues(logger, manifestinfo, 'EXT-X-VERSION', int(match.group()))
              continue

            # EXT-X-TARGETDURATION
            elif line.startswith('#EXT-X-TARGETDURATION:'):
              match = re.search(r'\d+', line)
              if match:
                # Check if changed
                comparevalues(logger, manifestinfo, 'EXT-X-TARGETDURATION', int(match.group()))
              continue

            # EXT-X-MEDIA-SEQUENCE
            elif line.startswith('#EXT-X-MEDIA-SEQUENCE:'):
              match = re.search(r'\d+', line)
              if match:
                if 'EXT-X-MEDIA-SEQUENCE' in manifestinfo.keys():
                  if int(match.group()) == manifestinfo['EXT-X-MEDIA-SEQUENCE']:
                    samefirstsegment = True
                manifestinfo['EXT-X-MEDIA-SEQUENCE'] = int(match.group())
              continue

            # EXT-X-DISCONTINUITY-SEQUENCE
            elif line.startswith('#EXT-X-DISCONTINUITY-SEQUENCE:'):
              match = re.search(r'\d+', line)
              if match:
                manifestinfo['EXT-X-DISCONTINUITY-SEQUENCE'] = int(match.group())
                discontinuitysequence = int(match.group())
              continue

          # EXT-X-PROGRAM-DATE-TIME
          if line.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
            # segmentinfo['pdt'] = line[25:48]
            match = re.search(r'(#EXT-X-PROGRAM-DATE-TIME:)(\S+)', line) # (\d+\-\d+\-\d+T\d+\:\d+\:\d+\.?\w*)
            if match:
              segmentinfo['pdt'] = match.group(2) ; segmentinfo['explicitpdt'] = True ; foundpdt = True ; durationsumforpdt = 0.0
              # With milliseconds
              match = re.search(r'\d+\-\d+\-\d+T\d+\:\d+\:\d+\.\d+', segmentinfo['pdt'])
              if match:
                manifestinfo['lastexplicitpdtdate'] = datetime.datetime.strptime(match.group(), '%Y-%m-%dT%H:%M:%S.%f')
              # Without milliseconds
              else:
                match = re.search(r'\d+\-\d+\-\d+T\d+\:\d+\:\d+', segmentinfo['pdt'])
                if match:
                  manifestinfo['lastexplicitpdtdate'] = datetime.datetime.strptime(match.group(), '%Y-%m-%dT%H:%M:%S')
                else:
                  logger.error('Error parsing EXT-X-PROGRAM-DATE-TIME: ' + line)

          # EXTINF
          elif line.startswith('#EXTINF:'):
            match = re.search(r'\d*\.?\d+', line)
            if match:
              segmentinfo['duration'] = float(match.group())

          # EXT-X-DISCONTINUITY
          elif line.startswith('#EXT-X-DISCONTINUITY') and not line.startswith('#EXT-X-DISCONTINUITY-SEQUENCE'):
            discontinuitysequence = discontinuitysequence + 1
            segmentinfo['discontinuity'] = True

          # Segment
          elif not line.startswith('#') and line:
            gonethroughheaders = True

            # For each segment
            if 'duration' not in segmentinfo.keys():
              logger.error('Missing segment duration')
              break
            segmentinfo['mediasequence'] = manifestinfo['EXT-X-MEDIA-SEQUENCE'] + segmentcount
            lastsequenceofthismanifest = segmentinfo['mediasequence']

            # Is last or new segment
            if segmentinfo['mediasequence'] >= manifestinfo['lastmediasequence']:
              # Update attributes
              segmentinfo['mediasequence'] = manifestinfo['EXT-X-MEDIA-SEQUENCE'] + segmentcount
              segmentinfo['discontinuitysequence'] = discontinuitysequence
              segmentinfo['name'] = line
              segmentinfo['url'] = urljoin(rendition['URL'], line)
              if 'discontinuity' not in segmentinfo.keys():
                segmentinfo['discontinuity'] = False

              # Compute PDT
              if 'pdt' not in segmentinfo.keys() and 'lastexplicitpdtdate' in manifestinfo.keys() and foundpdt:
                try:
                  segmentinfo['explicitpdt'] = False
                  segmentinfo['pdt'] = (manifestinfo['lastexplicitpdtdate'] + datetime.timedelta(milliseconds = durationsumforpdt * 1000)).strftime('%Y-%m-%dT%H:%M:%S.%f')[0:23]
                except Exception:
                  logger.exception('Error computing PDT value')

            # Is last segment
            if segmentinfo['mediasequence'] == manifestinfo['lastmediasequence']:
              # Check if different from last segment
              if 'lastsegmentinfo' in manifestinfo.keys():
                comparelastsegment(logger, segmentinfo, manifestinfo['lastsegmentinfo'])
              foundlastsegment = True

            # Is new segment
            if segmentinfo['mediasequence'] > manifestinfo['lastmediasequence']:
              logger.debug('Found new segment, segment info: ' + str(segmentinfo) + ', segment tags: ' + str(segmenttags))
              foundnewsegment = True ; manifestinfo['foundnewsegment'] = True
              sessioncontentduration = sessioncontentduration + segmentinfo['duration'] ; newcontentduration = newcontentduration + segmentinfo['duration']
              # Publish segment duration
              addmetricvalue(metricstopublish, 'segmentduration', segmentinfo['duration'])

              # Check segment duration
              if 'EXT-X-TARGETDURATION' in manifestinfo.keys():
                if round(segmentinfo['duration']) > manifestinfo['EXT-X-TARGETDURATION']:
                  logger.warning('Segment duration exceeded target duration (EXT-X-TARGETDURATION: ' + str(manifestinfo['EXT-X-TARGETDURATION']) + ', segment duration: ' + str(segmentinfo['duration']) + ')')
                

              # Check for PDT jump
              if 'lastsegmentinfo' in manifestinfo.keys() and 'EXT-X-TARGETDURATION' in manifestinfo.keys():
                if 'pdt' in segmentinfo.keys() and 'pdt' in manifestinfo['lastsegmentinfo'].keys():
                  match1 = re.search(r'\d+\-\d+\-\d+T\d+\:\d+\:\d+\.\d+', segmentinfo['pdt']) ; match2 = re.search(r'\d+\-\d+\-\d+T\d+\:\d+\:\d+\.\d+', manifestinfo['lastsegmentinfo']['pdt'])
                  if match1 and match2:
                    pdtjump = datetime.datetime.strptime(match1.group(), '%Y-%m-%dT%H:%M:%S.%f') - datetime.datetime.strptime(match2.group(), '%Y-%m-%dT%H:%M:%S.%f')
                    if pdtjump < datetime.timedelta():
                      logger.warning('Negative jump in PDT value, from ' + manifestinfo['lastsegmentinfo']['pdt'] + ' to ' + segmentinfo['pdt'])
                    # if pdtjump > datetime.timedelta(milliseconds = manifestinfo['lastsegmentinfo']['duration'] * 2500):
                    if pdtjump > datetime.timedelta(milliseconds = int(manifestinfo['EXT-X-TARGETDURATION']) * 2 * 1000):
                      logger.warning('Positive jump in PDT value by more than 2x EXT-X-TARGETDURATION, from ' + manifestinfo['lastsegmentinfo']['pdt'] + ' to ' + segmentinfo['pdt'])
                  else:
                    logger.error('Cannot compute PDT jump')

              # Check segment tags
              for i in segmenttags:

                # Ad break end non EMT
                if 'adbreak' in manifestinfo.keys():
                  if manifestinfo['adbreak'] == True and userargs['emt'] == False:
                    adbreakend = False
                    if i.startswith('#EXT-X-CUE-IN'):
                      adbreakend = True
                    elif i.startswith('#EXT-X-DATERANGE:') and 'SCTE35-IN' in i:
                      tagsplit = i[17:].split(',')
                      # Look for id
                      for pair in tagsplit:
                        if pair.startswith('ID='):
                          match = re.search(r'([\'\"])(\S*)([\'\"])', pair)
                          if match:
                            if 'datarangeid' in manifestinfo.keys():
                              if match.group(2) == manifestinfo['datarangeid']:
                                adbreakend = True
                              else:
                                logger.warning('Found ad break end SCTE35-IN EXT-X-DATERANGE tag with id ' + match.group(2) + ', which is not maching ad break start SCTE35-OUT EXT-X-DATERANGE tag id ' + manifestinfo['datarangeid'])
                          break
                    if adbreakend:
                      if manifestinfo['advertisedadbreakduration'] > 0:
                        if 'actualadbreakduration' in manifestinfo.keys():
                          adbreakdurationdelta = manifestinfo['actualadbreakduration'] - manifestinfo['advertisedadbreakduration']
                          if adbreakdurationdelta > 1:
                            logger.warning('Ad break was longer than advertised by ' + '{:.3f}'.format(adbreakdurationdelta) + ' seconds, advertised: ' + str(manifestinfo['advertisedadbreakduration']) + ', actual: ' + '{:.3f}'.format(manifestinfo['actualadbreakduration']))
                          if adbreakdurationdelta < -1:
                            logger.warning('Ad break was shorter than advertised by ' + '{:.3f}'.format(abs(adbreakdurationdelta)) + ' seconds, advertised: ' + str(manifestinfo['advertisedadbreakduration']) + ', actual: ' + '{:.3f}'.format(manifestinfo['actualadbreakduration']))
                          if userargs['cwmetrics'] == True:
                            addmetricvalue(metricstopublish, 'addurationdelta', round(adbreakdurationdelta, 3))
                      if userargs['cwmetrics'] == True:
                        addmetricvalue(metricstopublish, 'addurationactual', round(manifestinfo['actualadbreakduration'], 3))
                      manifestinfo['adbreak'] = False

                # Ad break end EMT
                if userargs['emt'] == True and userargs['emtadsegmentstring'] not in segmentinfo['name'] and i.startswith('#EXT-X-DISCONTINUITY'):
                  if 'adbreak' in manifestinfo.keys():
                    if manifestinfo['adbreak'] == True:
                      if 'trackingconfirmed' in manifestinfo.keys() and endpoint['tracking'] != '':
                        if manifestinfo['trackingconfirmed'] == False and userargs['trackingrequests'] == True:
                          logger.warning('Did not find expected tracking info during ad break')
                      if userargs['cwmetrics'] == True:
                        if 'actualadbreakduration' in manifestinfo.keys():
                          addmetricvalue(metricstopublish, 'addurationactual', round(manifestinfo['actualadbreakduration'], 3))
                  manifestinfo['adbreak'] = False
              
                # Ad break start non EMT
                if userargs['emt'] == False:
                  if i.startswith('#EXT-X-CUE-OUT') and not i.startswith('#EXT-X-CUE-OUT-CONT'):
                    if 'adbreak' in manifestinfo.keys():
                      if manifestinfo['adbreak'] == True:
                        logger.warning('Nested ad break start')
                    manifestinfo['adbreak'] = True
                    manifestinfo['actualadbreakduration'] = 0.0
                    manifestinfo['advertisedadbreakduration'] = 0.0
                    if userargs['cwmetrics'] == True:
                      addmetricvalue(metricstopublish, 'adbreak', 1)
                    # Look for duration
                    match = re.search(r'\d*\.?\d+', i)
                    if match:
                      manifestinfo['advertisedadbreakduration'] = float(match.group())
                      if userargs['cwmetrics'] == True:
                        addmetricvalue(metricstopublish, 'addurationadvertised', manifestinfo['advertisedadbreakduration'])
                  if i.startswith('#EXT-X-DATERANGE:') and 'SCTE35-OUT' in i:
                    tagsplit = i[17:].split(',') ; manifestinfo['datarangeid'] = '' ; manifestinfo['advertisedadbreakduration'] = 0.0
                    # Look for ID
                    for pair in tagsplit:
                      if pair.startswith('ID='):
                        match = re.search(r'([\'\"])(\S*)([\'\"])', pair)
                        if match:
                          manifestinfo['datarangeid'] = match.group(2)
                          break
                    if manifestinfo['datarangeid']:
                      if 'adbreak' in manifestinfo.keys():
                        if manifestinfo['adbreak'] == True:
                          logger.warning('Nested ad break start')
                      manifestinfo['adbreak'] = True
                      manifestinfo['actualadbreakduration'] = 0.0
                      manifestinfo['advertisedadbreakduration'] = 0.0
                      if userargs['cwmetrics'] == True:
                        addmetricvalue(metricstopublish, 'adbreak', 1)
                      # Look for duration
                      for pair in tagsplit:
                        if pair.startswith('DURATION=') or pair.startswith('PLANNED-DURATION='):
                          match = re.search(r'\d*\.?\d+', pair)
                          if match:
                            manifestinfo['advertisedadbreakduration'] = float(match.group())
                            break
                      if manifestinfo['advertisedadbreakduration'] > 0:
                        if userargs['cwmetrics'] == True:
                          addmetricvalue(metricstopublish, 'addurationadvertised', manifestinfo['advertisedadbreakduration'])
                    else:
                      logger.warning('No ID found in EXT-X-DATERANGE tag ' + i)

                # Ad break start EMT
                if userargs['emt'] == True and i.startswith('#EXT-X-DISCONTINUITY') and userargs['emtadsegmentstring'] in segmentinfo['name']:
                  if 'lastsegmentinfo' in manifestinfo.keys():
                    if userargs['emtadsegmentstring'] not in manifestinfo['lastsegmentinfo']['name']:
                      if userargs['cwmetrics'] == True:
                        if 'adbreak' not in manifestinfo.keys():
                          addmetricvalue(metricstopublish, 'adbreak', 1)
                        elif manifestinfo['adbreak'] == False:
                          addmetricvalue(metricstopublish, 'adbreak', 1)
                      manifestinfo['advertisedadbreakduration'] = 0.0
                      manifestinfo['actualadbreakduration'] = 0.0
                      manifestinfo['adbreak'] = True
                      manifestinfo['adbreakstart'] = time.perf_counter()
                      manifestinfo['trackingconfirmed'] = False

                # Discontinuity
                if i == '#EXT-X-DISCONTINUITY':
                  if userargs['cwmetrics'] == True:
                    addmetricvalue(metricstopublish, 'discontinuity', 1)
                  if userargs['emt'] == False:
                    logger.warning('Discontinuity')

              # Count segment length to ad break duration
              if 'adbreak' in manifestinfo.keys():
                if manifestinfo['adbreak'] == True:
                  if 'actualadbreakduration' in manifestinfo.keys():
                    manifestinfo['actualadbreakduration'] = manifestinfo['actualadbreakduration'] + segmentinfo['duration']
                  else:
                    manifestinfo['actualadbreakduration'] = segmentinfo['duration']

              # Send segment request or download segment
              if userargs['segmentrequests'] == True or userargs['segments'] == True:
                if userargs['segmentrequests'] == True:
                  segmentresponse, segmentresponsetime = request3(logger, {}, segmentinfo['url'], 'HEAD', 'segment', metricstopublish)
                elif userargs['segments'] == True:
                  segmentresponse, segmentresponsetime = request3(logger, {}, segmentinfo['url'], 'GET', 'segment', metricstopublish)
                # Segment response time
                srtime = segmentresponsetime
                if userargs['cwmetrics'] == True:
                  addmetricvalue(metricstopublish, 'segmentresponsetime', srtime)
                if segmentresponse:
                  # Save response
                  if userargs['segments'] == True:
                    segmentname = segmentinfo['name'].split('/')[-1]
                    segmentname = segmentname.split('?')[0]
                    filepath = saveresponse(logger, segmentresponse.data, segmentsfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + '_' + segmentname, True, False)
                    if filepath:
                      logger.debug('Saved file ' + str(filepath))

                  # Check segment size
                  foundcontentlength = False
                  for i in segmentresponse.headers.keys():
                    if 'content-length' == i.lower():
                      foundcontentlength = True
                      break
                  if foundcontentlength:
                    if userargs['cwmetrics'] == True:
                      addmetricvalue(metricstopublish, 'segmentsize', int(segmentresponse.headers[i]))

              # Share segment info for parent thread to compare between renditions
              if userargs['allrenditions']:
                segmentinfo['type'] = rendition['TYPE']
                lock.acquire()
                foundsegmentinsharedlist = False
                for i in sharedlist:
                  if i['mediasequence'] == segmentinfo['mediasequence']:
                    i['segments'].append(segmentinfo.copy())
                    foundsegmentinsharedlist = True
                    break
                if not foundsegmentinsharedlist:
                  sharedlist.append({'mediasequence': segmentinfo['mediasequence'], 'segments': [segmentinfo.copy()]})
                lock.release()

              # Update manifest info about last segment
              manifestinfo['lastmediasequence'] = segmentinfo['mediasequence']
              manifestinfo['lastsegmentinfo'] = segmentinfo.copy()

            # After processing segment line
            durationsum = durationsum + segmentinfo['duration'] ; durationsumforpdt = durationsumforpdt + segmentinfo['duration'] ; segmentcount = segmentcount + 1
            segmentinfo.clear() ; segmenttags.clear()

        # After parsing manifest
        if foundnewsegment:
          stale = False
          nextstaletime = time.perf_counter() + userargs['stale']

        # Check if last segment was present
        if not foundlastsegment:
          lastmanifestheaders = str(manifestinfo['lastmanifestheaders']) if 'lastmanifestheaders' in manifestinfo.keys() else '[]'
          logger.warning('Last segment not found, previously: ' + str(manifestinfo['lastmediasequence']) + ', now: ' + str(lastsequenceofthismanifest) + ', previous manifest response headers: ' + lastmanifestheaders + ', this manifest response headers: ' + str(response.headers.items()))

        # Compare manifests
        if userargs['comparemanifests'] == True:
          if foundlastsegment and samefirstsegment:
            if 'lastmanifestcontent' in manifestinfo.keys() and 'lastmanifestlength' in manifestinfo.keys():
              if manifestinfo['lastmanifestcontent'] != responsetext[:manifestinfo['lastmanifestlength']]:
                lastmanifestheaders = str(manifestinfo['lastmanifestheaders']) if 'lastmanifestheaders' in manifestinfo.keys() else '[]'
                logger.warning('Manifests do not match, previous manifest response headers: ' + lastmanifestheaders + ', this manifest response headers: ' + str(response.headers.items()))
          manifestinfo['lastmanifestlength'] = len(responsetext)
          manifestinfo['lastmanifestcontent'] = responsetext

        # Check PDT delta (includes the duration of last segment)
        if 'lastexplicitpdtdate' in manifestinfo.keys() and foundnewsegment:
          manifestinfo['pdtdelta'] = (manifestinfo['lastexplicitpdtdate'] - datetime.datetime.utcnow()).total_seconds() + durationsumforpdt
          if userargs['cwmetrics'] == True and foundnewsegment:
            metricstopublish['pdtdelta'] = round(manifestinfo['pdtdelta'])

        # Publish manifest duration
        if userargs['cwmetrics'] == True:
          metricstopublish['manifestduration'] = round(durationsum / 60, 1)

        # Get tracking
        if dotracking == True:
          if userargs['trackingrequests'] == True:
            playerplayhead = manifestinfo['initialmanifestduration'] + sessioncontentduration
            if userargs['playheadawaretracking']:
              trackingurl = endpoint['tracking'] + '?aws.playheadPositionInSeconds=' + str(round(playerplayhead))
            else:
              trackingurl = endpoint['tracking']
            trackingresponse, trackingresponsetime = request3(logger, {'Accept-Encoding': 'gzip'}, trackingurl, 'GET', 'tracking', metricstopublish)
            if userargs['cwmetrics'] == True:
              metricstopublish['trackingresponsetime'] = trackingresponsetime
            if trackingresponse:
              if all(i in manifestinfo.keys() for i in ['adbreak', 'adbreakstart', 'trackingconfirmed']):
                if manifestinfo['adbreak'] == True:
                  # Parse tracking response
                  trackingresponsetext = getresponsetext(trackingresponse, True) ; trackingresponsedict.clear()
                  try:
                    trackingresponsedict = json.loads(trackingresponsetext)
                  except json.decoder.JSONDecodeError:
                    logger.error('Tracking response could not be converted to json')
                  if trackingresponsedict:
                    if 'avails' in trackingresponsedict.keys():
                      for avail in trackingresponsedict['avails']:
                        if all(i in avail.keys() for i in ['availId', 'durationInSeconds', 'startTimeInSeconds']):
                          if float(avail['startTimeInSeconds']) <= playerplayhead <= float(avail['startTimeInSeconds']) + float(avail['durationInSeconds']):
                            availadscount = 0 ; adsinfo.clear()
                            if 'ads' in avail.keys():
                              availadscount = len(avail['ads'])
                              if manifestinfo['trackingconfirmed'] == False:
                                for index, ad in enumerate(avail['ads']):
                                  adinfo.clear()
                                  if 'adId' in ad.keys():
                                    adinfo['adId'] = ad['adId']
                                  if 'durationInSeconds' in ad.keys():
                                    adinfo['durationInSeconds'] = ad['durationInSeconds']
                                  if 'creativeId' in ad.keys():
                                    adinfo['creativeId'] = ad['creativeId']
                                  if 'adTitle' in ad.keys():
                                    adinfo['adTitle'] = ad['adTitle']
                                  adsinfo[str(index + 1)] = adinfo.copy()
                              if userargs['checktrackingevents'] == True:
                                for index, ad in enumerate(avail['ads']):
                                  eventtypesdiscovered.clear()
                                  if 'trackingEvents' in ad.keys():
                                    for trackingevent in ad['trackingEvents']:
                                      if 'eventType' in trackingevent.keys():
                                        eventtypesdiscovered.add(trackingevent['eventType'])
                                      else:
                                        logger.warning('Missing eventType in trackingEvent in avail')
                                    if len(eventtypestolookfor - eventtypesdiscovered) > 0:
                                      logger.warning('Missing event types in avail ad: ' + str(eventtypestolookfor - eventtypesdiscovered))
                                  else:
                                    logger.warning('Missing trackingEvents in ad')
                            if manifestinfo['trackingconfirmed'] == False:
                              timesinceadbreakstart = round(time.perf_counter() - manifestinfo['adbreakstart'])
                              playerplayheaddelta = round(playerplayhead - avail['startTimeInSeconds'])
                              if abs(playerplayheaddelta) > userargs['frequency'] * 3 or abs(timesinceadbreakstart) > userargs['frequency'] * 3:
                                logger.warning('Found avail in tracking response, but timing is not exact, avail id: ' + str(avail['availId']) + ', duration: ' + str(avail['durationInSeconds']) + ', ads count: ' + str(availadscount) + ', ads info: ' + str(adsinfo) + ', time since ad break start: ' + str(timesinceadbreakstart) + ' sec, playhead: ' + str(round(playerplayhead)) + ' sec, avail startTimeInSeconds: ' + str(round(avail['startTimeInSeconds'])) + ' sec, delta: ' + str(playerplayheaddelta) + ' sec, tracking url: ' + trackingurl + ', response headers: ' + str(trackingresponse.headers.items()))
                              else:
                                logger.info('Found avail in tracking response, avail id: ' + str(avail['availId']) + ', duration: ' + str(avail['durationInSeconds']) + ', ads count: ' + str(availadscount) + ', ads info: ' + str(adsinfo) + ', time since ad break start: ' + str(timesinceadbreakstart) + ' sec, playhead: ' + str(round(playerplayhead)) + ' sec, avail startTimeInSeconds: ' + str(round(avail['startTimeInSeconds'])) + ' sec, delta: ' + str(playerplayheaddelta) + ' sec, tracking url: ' + trackingurl)
                              manifestinfo['trackingconfirmed'] = True
                        else:
                          logger.warning('Missing some important fields in avail')
                    else:
                      logger.warning('No avails in tracking response')
                  else:
                    logger.warning('Empty tracking response')
              # Save tracking response
              if userargs['tracking']:
                filepath = ''
                if userargs['gzip'] == True:
                  foundcontentencodinggzip = False
                  for i in trackingresponse.headers.keys():
                    if 'content-encoding' == i.lower():
                      if trackingresponse.headers[i] == 'gzip':
                        foundcontentencodinggzip = True
                        break
                  if foundcontentencodinggzip:
                    filepath = saveresponse(logger, trackingresponse.data, trackingfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + '.json', False, False)
                  else:
                    filepath = saveresponse(logger, getresponsetext(trackingresponse, True), trackingfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + '.json', False, True)
                else:
                  filepath = saveresponse(logger, getresponsetext(trackingresponse, True), trackingfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + '.json', False, False)
                if filepath:
                  logger.debug('Saved file ' + str(filepath))

        # Check for new content shortage
        lastcontentdurations.append(newcontentduration)
        if len(lastcontentdurations) == lastcontentdurations.maxlen:
          lasttwocontentdurations = 0.0;
          goahead = True
          for i in range(0, 10):
            if not lastcontentdurations[i] > 0 and i < 8:
              goahead = False
            if i > 7:
              lasttwocontentdurations = lasttwocontentdurations + lastcontentdurations[i]
          if goahead == True and lasttwocontentdurations < 0.25 * 2 * userargs['frequency']:
            lastmanifestheaders = str(manifestinfo['lastmanifestheaders']) if 'lastmanifestheaders' in manifestinfo.keys() else '[]'
            thismanifestheaders = str(response.headers.items()) if response else '[]'
            logger.warning('Content shortage during last 2 manifest requests. New content duration of last 10 manifest requests: ' + str(lastcontentdurations) + ', previous manifest response headers: ' + lastmanifestheaders + ', this manifest response headers: ' + thismanifestheaders)
            if userargs['cwmetrics'] == True:
              metricstopublish['contentshortage'] = 1
              
        # Update last manifest headers
        manifestinfo['lastmanifestheaders'] = response.headers.items()
        
      # If timeout or error response 
      else:
        pass

      # Check input buffer size
      inputbuffersize = int(userargs['initialinputbuffersize'] - (mrequesttime - sessionstarttime) + sessioncontentduration)
      if userargs['cwmetrics'] == True:
        metricstopublish['inputbuffersize'] = inputbuffersize
      if inputbuffersize < 0:
        if manifestinfo['foundnewsegment']:
          startsession = True

      # Check if stale
      now = time.perf_counter()
      if now > nextstaletime:
        stale = True
      if stale:
        manifestheaders = str(response.headers.items()) if response else ''
        if manifestheaders:
          logger.warning('Stale manifest, last manifest headers: ' + manifestheaders)
        else:
          logger.warning('Stale manifest')
        if userargs['cwmetrics'] == True:
          metricstopublish['stale'] = 1

      # Publish metrics
      if userargs['cwmetrics'] == True:
        publishmetrics(logger, endpoint, renditionname, metricstopublish)
    
      # Stop if stale and rendition is from primary manifest
      if stale and fromprimary:
        break

      # Wait between requests
      waittime = mrequesttime - time.perf_counter() + userargs['frequency']
      if waittime > 0:
        time.sleep(waittime)
      elif not calculatemanifestduration and waittime < -1:
        logger.error('Negative wait time ' + '{:.3f}'.format(waittime) + ' sec between manifest requests')

  # Smooth monitor
  elif endpoint['type'] == 'smooth':

    # Initial settings
    fileextension = '.Manifest'
    manifestinfo = {
      'url': endpoint['url'],
      'lastsegmentinfo': proberesponse.copy(),
      'newvideosegments': [],
      'newaudiosegments': [],
      'newsubtitlesegments': []
    }

    logger.info('Started monitoring manifest URL ' + manifestinfo['url'])

    # Loop until terminated by parent thread
    while not terminatethreads and not stoprunning.is_set():
      metricstopublish.clear() ; segmentinfo.clear() ; segmenttags.clear() ; discontinuitysequence = 0 ; foundlastsegment = False ; foundnewsegment = False ; durationsum = 0.0 ; durationsumforpdt = 0.0 ; foundpdt = False ; lastsequenceofthismanifest = 0 ; samefirstsegment = False ; calculatemanifestduration = False ; manifestinfo['foundlastsegment'] = False ; manifestinfo['foundnewsegment'] = False ; manifestinfo['newvideosegments'].clear() ; manifestinfo['newaudiosegments'].clear() ; manifestinfo['newsubtitlesegments'].clear()

      mrequesttime = time.perf_counter()
    
      # Initialize session
      if startsession:
        sessionstarttime = mrequesttime ; sessioncontentduration = 0.0 ; startsession = False

      # Request manifest
      logger.debug('Requesting manifest')
      response, responsetime = request3(logger, {'Accept-Encoding': 'gzip'}, manifestinfo['url'], 'GET', 'manifest', {})
      
      if response:
        responsetext = getresponsetext(response, False)
        
        # Manifest response time
        manifestinfo['latency'] = responsetime
        if userargs['cwmetrics'] == True:
          metricstopublish['manifestresponsetime'] = manifestinfo['latency']
          
        # Manifest size https://docs.python.org/3/library/email.compat32-message.html#email.message.Message
        manifestinfo['size'] = len(responsetext)
        if userargs['cwmetrics'] == True:
          metricstopublish['manifestsize'] = manifestinfo['size']
          
        # Save response
        if userargs['manifests'] == True:
          filepath = ''
          if userargs['gzip'] == True:
            foundcontentencodinggzip = False
            for i in response.headers.keys():
              if 'content-encoding' == i.lower():
                if response.headers[i] == 'gzip':
                  foundcontentencodinggzip = True
                  break
            if foundcontentencodinggzip:
              filepath = saveresponse(logger, response.data, manifestsfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + fileextension, False, False)
            else:
              filepath = saveresponse(logger, responsetext.decode('utf-8'), manifestsfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + fileextension, False, True)
          else:
            filepath = saveresponse(logger, responsetext.decode('utf-8'), manifestsfolder, datetime.datetime.utcnow().strftime('%Y_%m_%d_%H_%M_%S_%f') + fileextension, False, False)
          if filepath:
            logger.debug('Saved file ' + str(filepath))
        
        # Parse XML 
        xmlroot = ET.fromstring(responsetext)
        if xmlroot != None:
          xmlstreamindices = xmlroot.findall('StreamIndex')
          if xmlstreamindices:
            for xmlstreamindex in xmlstreamindices:
              xmlstreamindextype = xmlstreamindex.get('Type')
              if xmlstreamindextype == 'audio':
                xmlqualitylevel = xmlstreamindex.find('QualityLevel')
                if xmlqualitylevel != None:
                  xmlfourcc = xmlqualitylevel.get('FourCC')
                  if xmlfourcc != 'AACL':
                    continue
              xmlstreamindextimescale = xmlstreamindex.get('TimeScale')
              if not xmlstreamindextimescale:
                break
              xmlcs = xmlstreamindex.findall('c')
              segmentinfo.clear()
              for xmlc in xmlcs:
                xmlt = xmlc.get('t')
                xmld = xmlc.get('d')
                # Find segment
                if xmlt and xmld:
                  segmentinfo['t'] = int(xmlt)
                  segmentinfo['nextt'] = int(xmlt) + int(xmld)
                  segmentinfo['tsec'] = float(xmlt) / float(xmlstreamindextimescale)
                  segmentinfo['d'] = int(xmld)
                  segmentinfo['dsec'] = float(xmld) / float(xmlstreamindextimescale)
                elif xmld:
                  if 't' in segmentinfo.keys():
                    segmentinfo['t'] = segmentinfo['t'] + segmentinfo['d']
                    segmentinfo['nextt'] = segmentinfo['t'] + int(xmld)
                    segmentinfo['tsec'] = float(segmentinfo['t']) / float(xmlstreamindextimescale)
                    segmentinfo['d'] = int(xmld)
                    segmentinfo['dsec'] = float(xmld) / float(xmlstreamindextimescale)
                  else:
                    logger.error('Error parsing segment line')
                    break
                else:
                  logger.error('Error parsing segment line')
                  break
                # After finding a segment
                if 't' in segmentinfo.keys():
                  # Video
                  if xmlstreamindextype == 'video':
                    # Found last segment
                    if segmentinfo['t'] == manifestinfo['lastsegmentinfo']['t']:
                      foundlastsegment = True 
                    # Found new segment
                    if segmentinfo['t'] > manifestinfo['lastsegmentinfo']['t']:
                      sessioncontentduration = sessioncontentduration + segmentinfo['dsec']
                      logger.debug('Found new segment, segment info: ' + str(segmentinfo))
                      foundnewsegment = True ; manifestinfo['foundnewsegment'] = True
                      # Check discontinuity
                      if foundlastsegment and 'lastvideosegmentinfo' in manifestinfo.keys():
                        if segmentinfo['t'] != manifestinfo['lastvideosegmentinfo']['nextt']:
                          logger.warning('Discontinuity')
                      # Check duration
                      if segmentinfo['dsec'] != 2:
                        logger.warning('Unexpected segment duration ' + '{:.3f}'.format(segmentinfo['dsec']) + ' sec')
                      # Save last video segment for discontinuity check
                    manifestinfo['lastvideosegmentinfo'] = segmentinfo.copy()
                  # Audio
                  elif xmlstreamindextype == 'audio':
                    manifestinfo['lastaudiosegmentinfo'] = segmentinfo.copy()
                  # Subtitles
                  elif xmlstreamindextype == 'text':
                    manifestinfo['lastsubtitlesegmentinfo'] = segmentinfo.copy()

            # Compare PTS
            if all(i in manifestinfo.keys() for i in ['lastvideosegmentinfo', 'lastaudiosegmentinfo', 'lastsubtitlesegmentinfo']):
              ptsdelta = abs(manifestinfo['lastvideosegmentinfo']['tsec'] - manifestinfo['lastaudiosegmentinfo']['tsec'])
              if ptsdelta > 0.05:
                logger.warning('Possible lip sync issue. AV PTS delta: ' + '{:.3f}'.format(ptsdelta))
              ptsdelta = abs(manifestinfo['lastvideosegmentinfo']['tsec'] - manifestinfo['lastsubtitlesegmentinfo']['tsec'])
              if ptsdelta > 0.5:
                logger.warning('Subtitles out of sync. PTS delta: ' + '{:.3f}'.format(ptsdelta))

            # Update stale time
            if foundnewsegment:
              stale = False
              nextstaletime = time.perf_counter() + userargs['stale']

            # Check if found last segment
            if not foundlastsegment:
              lastmanifestheaders = str(manifestinfo['lastmanifestheaders']) if 'lastmanifestheaders' in manifestinfo.keys() else '[]'
              logger.warning('Last segment ' + str(manifestinfo['lastsegmentinfo']) + ' not found, previous manifest response headers: ' + lastmanifestheaders + ', this manifest response headers: ' + str(response.headers.items()))

            # Update last segment info
            manifestinfo['lastsegmentinfo'] = manifestinfo['lastvideosegmentinfo'].copy()

            # Update last manifest headers
            manifestinfo['lastmanifestheaders'] = response.headers.items()
                        
      # If timeout or error response 
      else:
        pass

      # Check input buffer size
      inputbuffersize = int(userargs['initialinputbuffersize'] - (mrequesttime - sessionstarttime) + sessioncontentduration)
      if userargs['cwmetrics'] == True:
        metricstopublish['inputbuffersize'] = inputbuffersize
      if inputbuffersize < 0:
        if manifestinfo['foundnewsegment']:
          startsession = True

      # Check if stale
      now = time.perf_counter()
      if now > nextstaletime:
        stale = True
      if stale:
        logger.warning('Stale manifest')
        if userargs['cwmetrics'] == True:
          metricstopublish['stale'] = 1

      # Publish metrics
      # if userargs['cwmetrics'] == True:
      #   publishmetrics(logger, endpoint, renditionname, metricstopublish)
    
      # Stop if stale and rendition is from primary manifest
      if stale and fromprimary:
        break

      # Wait between requests
      waittime = mrequesttime - time.perf_counter() + userargs['frequency']
      if waittime > 0:
        time.sleep(waittime)
      elif not calculatemanifestduration and waittime < -1:
        logger.error('Negative wait time ' + '{:.3f}'.format(waittime) + ' sec between manifest requests')

def configurelogging(logsfolder:str):
  loggingconfig = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
      'standardformatter': {
        'format': '%(asctime)s.%(msecs)03d %(levelname).1s %(label)s %(message)s',
        'datefmt': '%Y-%m-%d %H:%M:%S'
      },
      'threadformatter': {
        'format': '%(asctime)s.%(msecs)03d %(levelname).1s %(renditionname)s %(endpointtype)s %(message)s',
        'datefmt': '%Y-%m-%d %H:%M:%S'
      }
    },
    'handlers': {
      'consolehandlermain': {
        'level': userargs['loglevel'],
        'class': 'logging.StreamHandler',
        'formatter': 'standardformatter',
        'stream': 'ext://sys.stdout'
      },
      'filehandlermain': {
        'level': userargs['loglevel'],
        'class': 'logging.FileHandler',
        'formatter': 'standardformatter',
        'filename': logsfolder + '/main.log',
        'mode': 'a'
      },
      'filehandlerthread': {
        'level': userargs['loglevel'],
        'class': 'logging.FileHandler',
        'formatter': 'threadformatter',
        'filename': logsfolder + '/monitor.log',
        'mode': 'a'
      },
      'consolehandlerthread': {
        'level': userargs['loglevel'],
        'class': 'logging.StreamHandler',
        'formatter': 'threadformatter',
        'stream': 'ext://sys.stdout'
      }
    },
    'loggers': {
      'main': {
        'level': 'INFO',
        'propagate': True,
        'handlers': ['filehandlermain']
      },
      'thread': {
        'level': userargs['loglevel'],
        'propagate': True,
        'handlers': ['filehandlerthread']
      },
      'threadstdout': {
        'level': userargs['loglevel'],
        'propagate': True,
        'handlers': ['filehandlerthread', 'consolehandlerthread']
      },
      'mainstdout': {
        'level': 'INFO',
        'propagate': True,
        'handlers': ['consolehandlermain', 'filehandlermain']
      }
    }
  }

  return loggingconfig

def loadendpointsfromfile(logger):
  endpointslist = []

  endpointslistfilepath = Path(userargs['endpointslistfile'])
  if not endpointslistfilepath.is_file():
    logger.error('No such file: ' + userargs['endpointslistfile'])
    return endpointslist

  with open(userargs['endpointslistfile']) as f:
    for index, line in enumerate(f):
      if not line.startswith('#') and line.strip():
        splitline = re.split(r',', line)
        if len(splitline) > 1:
          endpointname = splitline[0].strip()
          endpointurl = splitline[1].strip()
          trackingurl = ''
        if len(splitline) > 2:
          trackingurl = splitline[2].strip()
        if len(endpointname) == 0 or len(endpointurl) == 0:
          logger.error('Invalid line in ' + str(endpointslistfilepath) + ': ' + line.strip())
          continue
        # Find type
        parsedurl = urlparse(endpointurl)
        if len(parsedurl.path.split('/')) > 1:
          if '.m3u8' in parsedurl.path.split('/')[-1] or userargs['endpointtype'] == 'hls':
            endpointtype = 'hls'
          elif '.mpd' in parsedurl.path.split('/')[-1] or userargs['endpointtype'] == 'dash':
            endpointtype = 'dash'
          elif parsedurl.path.split('/')[-1] == 'Manifest' or '.ism' in parsedurl.path.split('/')[-2] or userargs['endpointtype'] == 'smooth':
            endpointtype = 'smooth'
          else:
            logger.error('Unable to detect stream type for url on line: ' + line)
            continue
        else:
          logger.error('Invalid manifest URL on line: ' + line)
          continue
      else:
        continue
      # Insert endpoint to list
      endpointslist.append({'type': endpointtype, 'name': endpointname, 'url': endpointurl, 'tracking': trackingurl})

  return endpointslist

def loadendpointfromurl(logger):
  endpointtype = ''

  # Find type
  parsedurl = urlparse(userargs['url'])
  if len(parsedurl.path.split('/')) > 1:
    if '.m3u8' in parsedurl.path.split('/')[-1] or userargs['endpointtype'] == 'hls':
      endpointtype = 'hls'
    elif '.mpd' in parsedurl.path.split('/')[-1] or userargs['endpointtype'] == 'dash':
      endpointtype = 'dash'
    elif parsedurl.path.split('/')[-1] == 'Manifest' or '.ism' in parsedurl.path.split('/')[-2] or userargs['endpointtype'] == 'smooth':
      endpointtype = 'smooth'
    else:
      logger.error('Unable to detect stream type for provided url')
      return []

  return [{'type': endpointtype, 'name': 'testendpoint', 'url': userargs['url'], 'tracking': ''}]

def signalhandler(signalnumber, frame):
  logger.info('Received signal ' + str(signalnumber) + ', stopping now')
  raise KeyboardInterrupt('')

if __name__ == '__main__':
  threading.excepthook = handle_threading_exception
  sys.excepthook = handle_sys_exception

  # Global variables
  terminatethreads = False
  renditionnames = {'hls': [], 'dash': [], 'smooth': []}
  lockm = threading.Lock()
  dashboardcreated = False
  segmentationtypeidmap = {
    '00': 'Not Indicated',
    '01': 'Content Identification',
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
    '57': 'Provider Overlay Placement Opportunity End',
    '58': 'Distributor Overlay Placement Opportunity Start',
    '59': 'Distributor Overlay Placement Opportunity End'
  }
  adbreakstartsegmentationtypeids = { '34', '48', '50', '52', '54' }
  eventtypestolookfor = {'impression', 'start', 'firstQuartile', 'midpoint', 'thirdQuartile', 'complete'}

  # Register signals
  if platform.system() != 'Windows':
    signal.signal(signal.SIGHUP, signalhandler)
  signal.signal(signal.SIGINT, signalhandler)
  signal.signal(signal.SIGTERM, signalhandler)

  # Read user args
  parser = argparse.ArgumentParser(description = 'The script takes a list of URLs (--endpointslistfile endpoints.csv) or a single URL (--url) and it monitors a single rendition (--renditiontype) or all renditions (--allrenditions) or only some renditions (--playerrenditions) until it is stopped by user (ctrl + c). It does so by requesting the manifests at regular interval (--frequency). Manifests (--manifests), segments (--segments) and tracking data (--tracking) can be saved locally to \'manifests\', \'segments\' and \'tracking\' folders and optionally compressed (--gzip). The script can send metrics to AWS CloudWatch (--cwmetrics, --cwregion), create a dashboard json file (--dashboards) in \'dashboards\' folder and it performs several manifest validations. The logs are stored in \'logs/monitor.log\'and can be printed to standard output if needed (--stdout).')
  parser.add_argument('--endpointslistfile', type = str, help = 'file location for list of endpoints to monitor, where each line is in the following format "<endpoint_name>,<manifset_url>,<tracking_url>", e.g. my_test_endpoint_1 https://mediapackage.us-west-2.amazonaws.com/index.mpd (default: endpoints.csv)')
  parser.add_argument('--url', type = str, help = 'manifest url to use instead of list of endpoints from file, e.g. https://mediapackage.us-west-2.amazonaws.com/index.mpd')
  parser.add_argument('--allrenditions', action = 'store_true', help = 'monitor all renditions of provided endpoints when URL is HLS main playlist (default: False, applies only to HLS)')
  parser.add_argument('--playerrenditions', action = 'store_true', help = 'monitor only one of each types of renditions for the provided endpoints when URL is HLS main playlist, i.e. one audio, one video and one subtitle rendition (default: False, applies only to HLS)')
  parser.add_argument('--renditiontype', type = str, help = 'rendition type to pick for monitoring from provided endpoints when URL is HLS main playlist and --allrenditions and --playerrenditions are False, e.g. v3 (3rd video) or a1 (1st audio) or c1 (1st captions) (default: v1, applies only to HLS)')
  parser.add_argument('--frequency', type = float, help = 'time between manifest requests [seconds], e.g. 10 or 1.5 (default: 5, min: 0.5)')
  parser.add_argument('--stale', type = float, help = 'time after which stream is identified as stale [seconds], e.g. 20 or 15.5 (default: 12)')
  parser.add_argument('--manifests', action = 'store_true', help = 'download manifests (default: False)')
  parser.add_argument('--segments', action = 'store_true', help = 'download segments (default: False)')
  parser.add_argument('--tracking', action = 'store_true', help = 'download tracking response (default: False)')
  parser.add_argument('--playheadawaretracking', action = 'store_true', help = 'send tracking requests with playhead query string (default: False)')
  parser.add_argument('--checktrackingevents', action = 'store_true', help = 'check if each ad in a new avail contains all expected tracking event types (default: False)')
  parser.add_argument('--trackingrequests', action = 'store_true', help = 'send tracking requests (default: False)')
  parser.add_argument('--segmentrequests', action = 'store_true', help = 'send HTTP HEAD requests for new segments (default: False)')
  parser.add_argument('--cwmetrics', action = 'store_true', help = 'publish metrics to AWS CloudWatch under \'CanaryMonitor\' namespace (default: False)')
  parser.add_argument('--cwregion', type = str, help = 'CloudWatch region name for sending metrics, e.g. us-east-1 (default: \'us-west-2\')')
  parser.add_argument('--dashboards', action = 'store_true', help = 'crate AWS Cloudwatch and Wiki dashboards for monitored endpoints in \'dashboards\' folder (default: False)')
  parser.add_argument('--property', type = str, help = 'property name as root folder for logs and manifests, e.g. tnf (default: \'\')')
  parser.add_argument('--label', type = str, help = 'identifying label for the system process, also used as dimension for AWS Cloudwatch threading metrics, e.g. superbowl (default: \'test\')')
  parser.add_argument('--dayfolder', action = 'store_true', help = 'add yyyy-mm-dd folder to the download folder path (default: False)')
  parser.add_argument('--logsfolder', type = str, help = 'logs folder, e.g. /tmp/logs (default: local folder)')
  parser.add_argument('--manifestsfolder', type = str, help = 'manifests download folder, e.g. /tmp/manifests (default: local folder)')
  parser.add_argument('--segmentsfolder', type = str, help = 'segments download folder, e.g. /tmp/segments (default: local folder)')
  parser.add_argument('--templatesfolder', type = str, help = 'folder for AWS Cloudwatch dashboard templates, e.g. /tmp/templates (default: templates)')
  parser.add_argument('--dashboardsfolder', type = str, help = 'folder for storing created AWS Cloudwatch dashboard json file, e.g. /tmp/dashboards (default: local folder)')
  parser.add_argument('--trackingfolder', type = str, help = 'folder for storing tracking response files, e.g. /tmp/tracking (default: local folder)')
  parser.add_argument('--loglevel', type = str, help = 'logging detail level, i.e. DEBUG or INFO or WARNING (default: INFO)')
  parser.add_argument('--gzip', action = 'store_true', help = 'compress manifests and tracking when saving (default: False)')
  parser.add_argument('--endpointtype', type = str, help = 'force endpoint type, i.e. hls, dash or smooth (no default)')
  parser.add_argument('--stdout', action = 'store_true', help = 'print monitoring logs to standard output, (default: False)')
  parser.add_argument('--httptimeout', type = float, help = 'HTTP timeout for all HTTP requests [seconds], e.g. 5 (default: 3)')
  parser.add_argument('--comparemanifests', action = 'store_true', help = 'compare if current manifest content is the same as previous manifest content up to the last overlapping segment (default: False)')
  parser.add_argument('--loadtest', action = 'store_true', help = 'send requests without performing manifest parsing and validations (default: False)')
  parser.add_argument('--emt', action = 'store_true', help = 'use when monitoring EMT (Elemental MediaTailor) endpoints (default: False)')
  parser.add_argument('--emtadsegmentstring', type = str, help = 'string by which the ad segments in an EMT (Elemental MediaTailor) endpoint can be identified (default: asset)')

  args = parser.parse_args()

  userargs = {
    'endpointslistfile': args.endpointslistfile if args.endpointslistfile else 'endpoints.csv',
    'url': args.url if args.url else '',
    'httptimeout': args.httptimeout if args.httptimeout else 3,
    'frequency': args.frequency if args.frequency else 5,
    'stale': args.stale if args.stale else 12,
    'property': args.property if args.property else '',
    'label': args.label if args.label else 'test',
    'dayfolder': args.dayfolder if args.dayfolder else False,
    'cwmetrics': args.cwmetrics if args.cwmetrics else False,
    'cwregion': args.cwregion if args.cwregion else 'us-west-2',
    'dashboards': args.dashboards if args.dashboards else False,
    'gzip': args.gzip if args.gzip else False,
    'manifests': args.manifests if args.manifests else False,
    'segments': args.segments if args.segments else False,
    'tracking': args.tracking if args.tracking else False,
    'playheadawaretracking': args.playheadawaretracking if args.playheadawaretracking else False,
    'checktrackingevents': args.checktrackingevents if args.checktrackingevents else False,
    'trackingrequests': args.trackingrequests if args.trackingrequests else False,
    'logsfolder': args.logsfolder if args.logsfolder else '',
    'manifestsfolder': args.manifestsfolder if args.manifestsfolder else '',
    'segmentsfolder': args.segmentsfolder if args.segmentsfolder else '',
    'dashboardsfolder': args.dashboardsfolder if args.dashboardsfolder else '',
    'templatesfolder': args.templatesfolder if args.templatesfolder else str(Path(os.path.dirname(os.path.realpath(__file__)), 'templates')),
    'trackingfolder': args.trackingfolder if args.trackingfolder else '',
    'allrenditions': args.allrenditions if args.allrenditions else False,
    'playerrenditions': args.playerrenditions if args.playerrenditions else False,
    'renditiontype': args.renditiontype if args.renditiontype else 'v1',
    'endpointtype': args.endpointtype if args.endpointtype else '',
    'segmentrequests': args.segmentrequests if args.segmentrequests else False,
    'initialinputbuffersize': 60,
    'loglevel': args.loglevel if args.loglevel else 'INFO',
    'stdout': args.stdout if args.stdout else False,
    'comparemanifests': args.comparemanifests if args.comparemanifests else False,
    'loadtest': args.loadtest if args.loadtest else False,
    'emt': args.emt if args.emt else False,
    'emtadsegmentstring': args.emtadsegmentstring if args.emtadsegmentstring else 'asset'
  }

  # Create folder structure
  if userargs['logsfolder']:
    logsfolder = Path(userargs['logsfolder'])
  else:
    if userargs['property']:
      logsfolder = Path(userargs['property'], 'logs')
    else:
      logsfolder = Path('logs')
  if userargs['manifestsfolder']:
    manifestsfolder = Path(userargs['manifestsfolder'])
  else:
    if userargs['property']:
      manifestsfolder = Path(userargs['property'], 'manifests')
    else:
      manifestsfolder = Path('manifests')
  if userargs['segmentsfolder']:
    segmentsfolder = Path(userargs['segmentsfolder'])
  else:
    if userargs['property']:
      segmentsfolder = Path(userargs['property'], 'segments')
    else:
      segmentsfolder = Path('segments')
  if userargs['dashboardsfolder']:
    dashboardsfolder = Path(userargs['dashboardsfolder'])
  else:
    if userargs['property']:
      dashboardsfolder = Path(userargs['property'], 'dashboards')
    else:
      dashboardsfolder = Path('dashboards')
  if userargs['trackingfolder']:
    trackingfolder = Path(userargs['trackingfolder'])
  else:
    if userargs['property']:
      trackingfolder = Path(userargs['property'], 'tracking')
    else:
      trackingfolder = Path('tracking')

  userargs['logsfolder'] = str(logsfolder)
  userargs['manifestsfolder'] = str(manifestsfolder)
  userargs['segmentsfolder'] = str(segmentsfolder)
  userargs['dashboardsfolder'] = str(dashboardsfolder)
  userargs['trackingfolder'] = str(trackingfolder)

  try:
    logsfolder.mkdir(parents = True, exist_ok = True)
    manifestsfolder.mkdir(parents = True, exist_ok = True)
    segmentsfolder.mkdir(parents = True, exist_ok = True)
    dashboardsfolder.mkdir(parents = True, exist_ok = True)
    trackingfolder.mkdir(parents = True, exist_ok = True)
  except Exception as e:
    print(e)
    sys.exit()

  # Configure logging
  logging.config.dictConfig(configurelogging(str(logsfolder)))
  if userargs['stdout'] == True:
    tlogger = logging.getLogger("threadstdout")
    mlogger = logging.getLogger("mainstdout")
  else:
    tlogger = logging.getLogger("thread")
    mlogger = logging.getLogger("main")

  logger = logging.LoggerAdapter(mlogger, {'label': userargs['label']})

  # Load endpoints
  if userargs['url']:
    endpointslist = loadendpointfromurl(logger)
  else:
    endpointslist = loadendpointsfromfile(logger)
  logger.debug('Endpointslist: ' + str(endpointslist))

  # Load lxml library if any DASH or Smooth endpoint
  needxmllibrary = False
  for endpoint in endpointslist:
    if endpoint['type'] != 'hls' and userargs['loadtest'] == False:
      needxmllibrary = True
      break
  if needxmllibrary:
    from lxml import etree as ET

  # Configure urllib3 pool
  http = urllib3.PoolManager(num_pools = 25, maxsize = len(endpointslist), timeout = userargs['httptimeout'])

  # Configure CloudWatch
  if userargs['cwmetrics'] == True and userargs['loadtest'] == False:
    import boto3
    import botocore.exceptions
    from botocore.config import Config
    try:
      config = Config(
        region_name = userargs['cwregion'],
        read_timeout = 3,
        connect_timeout = 3,
        retries = {
          'max_attempts': 1
        }
      )
      cloudwatch = boto3.client('cloudwatch', config = config)
      # Get account id
      sts = boto3.client("sts")
      userargs['cwaccountid'] = sts.get_caller_identity()["Account"]
      logger.info('Cloudwatch configured successfully for account id ' + userargs['cwaccountid'])
      # logger.info('CloudWatch region: ' + cloudwatch.meta.region_name)
    except Exception:
      logger.exception('Error configuring Cloudwatch.')
      userargs['cwmetrics'] = False

  # Check frequency
  if userargs['frequency'] < 0.5:
    userargs['frequency'] = 0.5

  # Disable header requests if saving segments
  if userargs['segments'] == True:
    userargs['segmentrequests'] = False

  # Enable tracking requests if saving tracking
  if userargs['tracking'] == True:
    userargs['trackingrequests'] = True

  # Load jinja2 module
  if userargs['dashboards'] == True and userargs['loadtest'] == False:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

  # Log user args to be used for monitoring
  logger.info('User arguments ' + str(userargs))

  # Start monitoring threads
  threads = {}
  for i in endpointslist:
    x = threading.Thread(target = premonitor, args = (tlogger, i, lockm))
    i['thread'] = x
    x.start()
    time.sleep(0.05)
  logger.debug('Created a thread for each endpoint')

  try:
    deadlist = []
    while True:
      # Create dashboard from template 
      if userargs['dashboards'] == True and userargs['cwmetrics'] == True and userargs['loadtest'] == False and not dashboardcreated:
        time.sleep(15)
        metrics = []
        templatespath = Path(userargs['templatesfolder'])
        if templatespath.is_dir():
          env = Environment(loader = FileSystemLoader(os.path.join(os.path.dirname(os.path.realpath(__file__)), userargs['templatesfolder'])), trim_blocks = True, lstrip_blocks = True)

          templatehelper = {'all': ['cw_all.json']}
          for k in templatehelper.keys():
            for v in templatehelper[k]:
              templatepath = Path(userargs['templatesfolder'], v)
              if templatepath.is_file():
                template = env.get_template(v)
                if k == 'all':
                  for i in renditionnames.keys():
                    for j in renditionnames[i]:
                      metrics.append(f"\"Type\", \"{i}\", \"Endpoint\", \"{j}\"")
                else:
                  for i in renditionnames[k]:
                    metrics.append('"Type", "' + k + '", "Endpoint", "' + i + '"')
                render = template.render(region = userargs['cwregion'], metrics = metrics, accountid = userargs['cwaccountid'], propertyname = userargs['property'], rendersegments = True if (userargs['segments'] == True or userargs['segmentrequests'] == True) else False, rendertracking = True if userargs['trackingrequests'] == True else False)
                metrics.clear()
                # Save dashboard
                filepath = Path(userargs['dashboardsfolder'], v)
                try:
                  with filepath.open('w+t') as f:
                    f.write(render)
                except Exception:
                  logger.exception('Error saving dashboard')
                logger.info('Created dashboard file: ' + str(filepath))
              else:
                logger.error('Template file ' + v + ' not found in ' + userargs['templatesfolder'])
        else:
          logger.error('Templates folder ' + userargs['templatesfolder'] + ' not found')
        dashboardcreated = True
      time.sleep(30)

      # Report status of threads
      threadscount = 0 ; alivecount = 0 ; deadcount = 0
      for i in endpointslist:
        if 'thread' in i.keys():
          threadscount = threadscount + 1
          if i['thread'].is_alive():
            alivecount = alivecount + 1
          else:
            deadcount = deadcount + 1
            deadlist.append(i)
      if deadcount > 0:
        logger.error('Some threads stopped unexpectedly, count: ' + str(deadcount) + ', info: ' + str(deadlist))
      else:
        logger.debug('Total threads count: ' + str(threading.active_count()) + ', main threads count: ' + str(threadscount) + ', running main threads count: ' + str(alivecount) + ', stopped main threads count and info: ' + str(deadcount) + ' (' + str(deadlist) + ')')

      # Send main thread metrics
      if userargs['cwmetrics'] == True:
        response = cloudwatch.put_metric_data(Namespace = 'CanaryMonitor', MetricData = [{'MetricName': 'alivethreads', 'Dimensions': [{'Name': 'Property', 'Value': userargs['label']}], 'Value': threading.active_count()}, {'MetricName': 'deceasedthreads', 'Dimensions': [{'Name': 'Property', 'Value': userargs['label']}], 'Value': deadcount}])
      deadlist.clear()
        
  except KeyboardInterrupt:
    terminatethreads = True
  except Exception as e:
    logger.exception(e)
    
