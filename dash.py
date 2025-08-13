import traceback
from lxml import etree as et
import utils
from datetime import datetime, timezone

# Custom exceptions
class UnsupportedManifest(Exception):
  pass

# Find new segments in a period
def getsegmentinfo(logger, monitorinfo:dict, primarysegmenttemplate, xmlperiod, allsegments:bool):
  ns = {'default': 'urn:mpeg:dash:schema:mpd:2011', 'scte': 'urn:scte:scte35:2013:xml'}
  try:
    periodid = xmlperiod.get('id')
    compt = 0
    n = int(primarysegmenttemplate['xmlsegmenttemplate'].get('startNumber', '1'))
    timescale = int(primarysegmenttemplate['xmlsegmenttemplate'].get('timescale'))
    xmlsegmenttimeline = primarysegmenttemplate['xmlsegmenttemplate'].find('default:SegmentTimeline', ns)
    for element in xmlsegmenttimeline:
      # No pattern
      if element.tag == f"{{{ns['default']}}}S":
        d = int(element.get('d'))
        t = int(element.get('t', compt))
        r = int(element.get('r', 0))
        if t != compt:
          compt = t
        for i in range(r + 1):
          if monitorinfo['manifest']['primary']['foundlastsegment'] or allsegments:
            # Add segment to new segments
            segment = {
              'd': d,
              'dsec': round(d / timescale, 3),
              't': t + d * i,
              'nextt': t + d * (i + 1),
              'n': n
            }
            monitorinfo['manifest']['primary']['new']['segments'].setdefault(periodid, []).append(segment)
            if monitorinfo['manifest']['primary']['foundlastsegment']:
              logger.debug(f"Found new segment in period {periodid}: {segment}")
          else:
            if periodid == monitorinfo['manifest']['primary']['last']['period']:
              if n == monitorinfo['manifest']['primary']['last']['segment']['n']:
                monitorinfo['manifest']['primary']['foundlastsegment'] = True
          n = n + 1
        compt = compt + d * (r + 1)
  except Exception as e:
    logger.error(f"Error finding new segments. Primarysegmenttemplate: {primarysegmenttemplate} Exception: {str(e)} Traceback: {traceback.format_exc()}")


# Return all available segment templates and mark a primary
def getsegmenttemplateinfo(logger, xmlperiod, monitorinfo):
  segmenttemplates = []
  primarysegmenttemplate = None
  ns = {'default': 'urn:mpeg:dash:schema:mpd:2011', 'scte': 'urn:scte:scte35:2013:xml'}
  try:
    xmladaptationsets = xmlperiod.findall('default:AdaptationSet', ns)
    for xmladaptationset in xmladaptationsets:
      xmlsegmenttemplate = xmladaptationset.find('default:SegmentTemplate', ns)
      # Compact plus check EMT use case where SegmentTemplate is empty on this level
      if xmlsegmenttemplate is not None and xmlsegmenttemplate.find('default:SegmentTimeline', ns) is not None:
        item = {
          'primary': False,
          'mimetype': xmladaptationset.get('mimeType'),
          'xmlsegmenttemplate': xmlsegmenttemplate,
          'representations': []
        }
        xmlrepresentations = xmladaptationset.findall('default:Representation', ns)
        for xmlrepresentation in xmlrepresentations:
          xmlrepresentationid = xmlrepresentation.get('id')
          if xmlrepresentationid is not None:
            item['representations'].append(xmlrepresentationid)
        segmenttemplates.append(item)
      # Non-compact
      else:
        xmlrepresentations = xmladaptationset.findall('default:Representation', ns)
        for xmlrepresentation in xmlrepresentations:
          item = {
            'primary': False,
            'mimetype': xmladaptationset.get('mimeType'),
            'representations': []
          }
          xmlrepresentationid = xmlrepresentation.get('id')
          if xmlrepresentationid is not None:
            item['representations'].append(xmlrepresentationid)
          xmlsegmenttemplate = xmlrepresentation.find('default:SegmentTemplate', ns)
          if xmlsegmenttemplate is not None:
            item['xmlsegmenttemplate'] = xmlsegmenttemplate
            segmenttemplates.append(item)
    # Assing one segment template to be primary
    primarysegmenttemplate = next((item for item in segmenttemplates if item['mimetype'] == 'video/mp4'), None)
    if primarysegmenttemplate:
      primarysegmenttemplate['primary'] = True
    else:
      logger.warning(f"Did not assign a primary segment template")
    # Update manifest information
  except Exception as e:
    logger.error(f"Error getting segment template. Exception: {str(e)} Traceback: {traceback.format_exc()}")
  return segmenttemplates, primarysegmenttemplate


# Find out information about period
def getperiodinfo(logger, xmlperiod, monitorinfo:dict):
  ns = {'default': 'urn:mpeg:dash:schema:mpd:2011'}
  xmlperiodid = xmlperiod.get('id')
  if xmlperiodid is not None:
    observetime = f"{datetime.now(timezone.utc)}" if monitorinfo['manifest']['primary']['foundlastsegment'] else None
    try:
      # Get period info
      periodinfo = {
        'observed': observetime,
        'compact': False,
        'isadbreak': False,
        'spliceinfo': []
      }
      # Compactness info
      xmladaptationsets = xmlperiod.findall('default:AdaptationSet', ns)
      for xmladaptationset in xmladaptationsets:
        xmlsegmenttemplate = xmladaptationset.find('default:SegmentTemplate', ns)
        if xmlsegmenttemplate is not None and xmlsegmenttemplate.find('default:SegmentTimeline', ns) is not None:
          periodinfo['compact'] = True
          break
      # Splice info
      xmleventstream = xmlperiod.find('default:EventStream', ns)
      if xmleventstream is not None:
        xmlevents = xmleventstream.findall('default:Event', ns)
        for xmlevent in xmlevents:
          xmlspliceinfosection = xmlevent.find('.//{*}SpliceInfoSection')
          # Text
          if xmlspliceinfosection is not None:
            sctemessage = {}
            # Splice insert
            xmlspliceinsert = xmlspliceinfosection.find('.//{*}SpliceInsert')
            if xmlspliceinsert is not None:
              sctemessage['type'] = 'spliceinsert'
              sctemessage['outofnetwork'] = False
              if xmlspliceinsert.get('outOfNetworkIndicator') == 'true':
                sctemessage['outofnetwork'] = True
              if xmlspliceinsert.get('availNum'):
                sctemessage['availnum'] = int(xmlspliceinsert.get('availNum'))
              if xmlevent.get('duration'):
                if xmleventstream.get('timescale'):
                  sctemessage['duration'] = round(int(xmlevent.get('duration'))/int(xmleventstream.get('timescale')), 3)
            # Time signal
            xmltimesignal = xmlspliceinfosection.find('.//{*}TimeSignal')
            if xmltimesignal is not None:
              sctemessage['type'] = 'timesignal'
            # Segmentation descriptors
            # todo: get duration from descriptor
            xmlsegmentationdescriptors = xmlspliceinfosection.findall('.//{*}SegmentationDescriptor')
            for xmlsegmentationdescriptor in xmlsegmentationdescriptors:
              segmentationdescriptor = {}
              xmlsegmentationupid = xmlsegmentationdescriptor.find('.//{*}SegmentationUpid')
              if xmlsegmentationupid is not None:
                if xmlsegmentationupid.get('segmentationTypeId'):
                  segmentationdescriptor['segmentationtype'] = int(xmlsegmentationupid.get('segmentationTypeId'))
                  segmentationdescriptor['segmentationmessage'] = utils.segmentationmessagemap.get(xmlsegmentationupid.get('segmentationTypeId'), 'Unknown')
                if xmlsegmentationdescriptor.get('segmentationDuration') and xmleventstream.get('timescale'):
                  segmentationdescriptor['duration'] = round(int(xmlsegmentationdescriptor.get('segmentationDuration')) / int(xmleventstream.get('timescale')), 3)
              sctemessage.setdefault('descriptors', []).append(segmentationdescriptor)
            periodinfo['spliceinfo'].append(sctemessage)
          # Binary
          xmlsignal = xmlevent.find('.//{*}Signal')
          if xmlsignal is not None:
            xmlbinary = xmlsignal.find('.//{*}Binary', ns)
            if xmlbinary is not None:
              periodinfo['spliceinfo'].append(utils.decodesctestring(logger, xmlbinary.text))
      # Identify ad break type
      adbreakinfo = {
        'observed': observetime,
        'segmentsduration': 0.0
      }
      # EMT origin
      if monitorinfo['config']['origin'].lower() == 'emt':
        if '_' in xmlperiodid:
          adbreakinfo['type'] = 'regular'
        else:
          keepgoing = True
          for sctemessage in periodinfo['spliceinfo']:
            if 'descriptors' in sctemessage.keys():
              for descriptor in sctemessage['descriptors']:
                if keepgoing:
                  if 'segmentationtype' in descriptor.keys():
                    if descriptor['segmentationtype'] == 56:
                      keepgoing = False
                      if 'availnum' in sctemessage.keys():
                        adbreakinfo['availnum'] = sctemessage['availnum']
                      adbreakinfo['advertisedduration'] = descriptor['duration'] if 'duration' in descriptor.keys() else 0.0
                      adbreakinfo['type'] = 'overlay'
      # Non-EMT origin
      else:
        keepgoing = True
        for sctemessage in periodinfo['spliceinfo']:
          if 'descriptors' in sctemessage.keys():
            for descriptor in sctemessage['descriptors']:
              if keepgoing:
                if 'segmentationtype' in descriptor.keys():
                  if descriptor['segmentationtype'] in [34, 48, 50, 52, 54, 56]:
                    keepgoing = False
                    if 'availnum' in sctemessage.keys():
                      adbreakinfo['availnum'] = sctemessage['availnum']
                    adbreakinfo['advertisedduration'] = descriptor['duration'] if 'duration' in descriptor.keys() else 0.0
                    adbreakinfo['type'] = 'overlay' if descriptor['segmentationtype'] == 56 else 'regular'
          else:
            if keepgoing:
              if 'outofnetwork' in sctemessage.keys():
                if sctemessage['outofnetwork']:
                  keepgoing = False
                  if 'availnum' in sctemessage.keys():
                      adbreakinfo['availnum'] = sctemessage['availnum']
                  adbreakinfo['advertisedduration'] = sctemessage['duration'] if 'duration' in sctemessage.keys() else 0.0
                  adbreakinfo['type'] = 'regular'
      # Update manifest ad break information, send metric for ad break start and type
      if 'type' in adbreakinfo.keys():
        periodinfo['isadbreak'] = True
        adbreakid = xmlperiodid.split('_')[0]
        if adbreakid not in monitorinfo['manifest']['primary']['adbreaks'].keys():
          monitorinfo['manifest']['primary']['adbreaks'][adbreakid] = adbreakinfo
          if monitorinfo['manifest']['primary']['foundlastsegment']:
            utils.addmetric(logger, monitorinfo, 'Start', 1, 'Count', [{'Name': 'AdBreakType', 'Value': adbreakinfo['type']}])
            if 'advertisedduration' in adbreakinfo.keys():
              utils.addmetric(logger, monitorinfo, 'AdvertisedDuration', adbreakinfo['advertisedduration'], 'Seconds', [{'Name': 'AdBreakType', 'Value': adbreakinfo['type']}])
            if 'availnum' in adbreakinfo.keys():
              utils.addmetric(logger, monitorinfo, 'AvailNum', adbreakinfo['availnum'], 'Count', [{'Name': 'AdBreakType', 'Value': adbreakinfo['type']}])
      # Update manifest period information
      monitorinfo['manifest']['primary']['periods'][xmlperiodid] = periodinfo
      logger.debug(f"Found {'new ' if monitorinfo['manifest']['primary']['foundlastsegment'] else ''}period {xmlperiodid}: compact={periodinfo['compact']}, adbreak={periodinfo['isadbreak']}{', type=' + adbreakinfo['type'] if 'type' in adbreakinfo.keys() else ''}{', spliceinfo=' + str(periodinfo['spliceinfo']) if len(periodinfo['spliceinfo']) > 0 else ''}")
    except Exception as e:
      logger.error(f"Error getting period information. Exception: {str(e)} Traceback: {traceback.format_exc()}")
  else:
    raise UnsupportedManifest(f"Period has no id")


def gothroughsegments(logger, monitorinfo:dict):
  try:
    for period, segments in monitorinfo['manifest']['primary']['new']['segments'].items():
      # Update ad break duration if period is ad break
      if monitorinfo['manifest']['primary']['periods'][period]['isadbreak']:
        adbreakid = period.split('_')[0]
        if adbreakid in monitorinfo['manifest']['primary']['adbreaks'].keys():
          for segment in segments:
            monitorinfo['manifest']['primary']['adbreaks'][adbreakid]['segmentsduration'] = monitorinfo['manifest']['primary']['adbreaks'][adbreakid]['segmentsduration'] + segment['dsec']
      # Go through all segments
      for segment in segments:
        # Update last segment
        monitorinfo['manifest']['primary']['last']['segment'] = segment.copy()
      # Update last period
      monitorinfo['manifest']['primary']['last']['period'] = period
  except Exception as e:
    logger.error(f"Error going through segments. Exception: {str(e)} Traceback: {traceback.format_exc()}")


def gothroughnewsegments(logger, monitorinfo:dict):
  try:
    for period, segments in monitorinfo['manifest']['primary']['new']['segments'].items():
      # Update ad break duration if period is ad break
      if monitorinfo['manifest']['primary']['periods'][period]['isadbreak']:
        adbreakid = period.split('_')[0]
        if adbreakid in monitorinfo['manifest']['primary']['adbreaks'].keys():
          for segment in segments:
            monitorinfo['manifest']['primary']['adbreaks'][adbreakid]['segmentsduration'] = monitorinfo['manifest']['primary']['adbreaks'][adbreakid]['segmentsduration'] + segment['dsec']
      # If last period was an ad break send last ad break info
      if monitorinfo['manifest']['primary']['periods'][monitorinfo['manifest']['primary']['last']['period']]['isadbreak']:
        lastadbreakid = monitorinfo['manifest']['primary']['last']['period'].split('_')[0]
        if lastadbreakid not in period:
          if lastadbreakid in monitorinfo['manifest']['primary']['adbreaks'].keys():
            monitorinfo['manifest']['primary']['adbreaks'][lastadbreakid]['segmentsduration'] = round(monitorinfo['manifest']['primary']['adbreaks'][lastadbreakid]['segmentsduration'], 3)
            utils.addmetric(logger, monitorinfo, 'SegmentsDuration', monitorinfo['manifest']['primary']['adbreaks'][lastadbreakid]['segmentsduration'], 'Seconds', [{'Name': 'AdBreakType', 'Value': monitorinfo['manifest']['primary']['adbreaks'][lastadbreakid]['type']}])
            if 'advertisedduration' in monitorinfo['manifest']['primary']['adbreaks'][lastadbreakid].keys() and monitorinfo['manifest']['primary']['adbreaks'][lastadbreakid]['advertisedduration'] > 0:
              monitorinfo['manifest']['primary']['adbreaks'][lastadbreakid]['durationdelta'] = round(monitorinfo['manifest']['primary']['adbreaks'][lastadbreakid]['segmentsduration'] - monitorinfo['manifest']['primary']['adbreaks'][lastadbreakid]['advertisedduration'], 3)
              utils.addmetric(logger, monitorinfo, 'DurationDelta', abs(monitorinfo['manifest']['primary']['adbreaks'][lastadbreakid]['durationdelta']), 'Seconds', [{'Name': 'AdBreakType', 'Value': monitorinfo['manifest']['primary']['adbreaks'][lastadbreakid]['type']}])
              if abs(monitorinfo['manifest']['primary']['adbreaks'][lastadbreakid]['durationdelta']) > 0.1:
                logger.warning(f"Ad break duration was {'longer' if monitorinfo['manifest']['primary']['adbreaks'][lastadbreakid]['durationdelta'] > 0 else 'shorter'} than advertised by {abs(monitorinfo['manifest']['primary']['adbreaks'][lastadbreakid]['durationdelta'])} seconds")
      # Go through all segments
      for segment in segments:
        # Update new segments duration
        monitorinfo['manifest']['primary']['new']['duration'] = monitorinfo['manifest']['primary']['new']['duration'] + segment['dsec']
        # Check for discontinuity
        if segment['t'] != monitorinfo['manifest']['primary']['last']['segment']['nextt']:
          logger.warning(f"Discontinuity")
          utils.addmetric(logger, monitorinfo, 'Discontinuity', 1, 'Count', [])
        # Update last segment
        monitorinfo['manifest']['primary']['last']['segment'] = segment.copy()
      # Update last period
      monitorinfo['manifest']['primary']['last']['period'] = period
    # Check if found last segment
    if not monitorinfo['manifest']['primary']['foundlastsegment']:
      logger.warning(f"Last segment not found")
    # Update playhead
    if 'availabilitystarttime' in monitorinfo['manifest'].keys():
      monitorinfo['manifest']['primary']['playhead'] = round((datetime.now(timezone.utc) - monitorinfo['manifest']['availabilitystarttime']).total_seconds())
  except Exception as e:
    logger.error(f"Error going through new segments. Exception: {str(e)} Traceback: {traceback.format_exc()}")

# Get availabtilityStartTime from manifest
def getavailabilitystarttime(logger, xmlroot, monitorinfo:dict):
  try:
    xmlavailabilitystarttime = xmlroot.get('availabilityStartTime')
    if xmlavailabilitystarttime:
      monitorinfo['manifest']['availabilitystarttime'] = datetime.fromisoformat(xmlavailabilitystarttime)
    else:
      logger.warning(f"Missing availabilityStartTime in manifest")
  except Exception as e:
    logger.error(f"Error getting availabilityStartTime. Exception: {str(e)} Traceback: {traceback.format_exc()}")


# Dash monitor
def monitor(logger, monitorinfo:dict, response:bytes):
  ns = {'default': 'urn:mpeg:dash:schema:mpd:2011', 'scte': 'urn:scte:scte35:2013:xml'}
  xmlroot = et.fromstring(response)
  try:
    if xmlroot is not None:
      xmlperiods = xmlroot.findall('default:Period', ns)
      getavailabilitystarttime(logger, xmlroot, monitorinfo)
      if not monitorinfo['manifest']['primary']['last']['segment']:
        # Go through all periods
        for xmlperiod in xmlperiods:
          getperiodinfo(logger, xmlperiod, monitorinfo)
          segmenttemplates, primarysegmenttemplate = getsegmenttemplateinfo(logger, xmlperiod, monitorinfo)
          getsegmentinfo(logger, monitorinfo, primarysegmenttemplate, xmlperiod, True)
        gothroughsegments(logger, monitorinfo)
      else:
        # Go through last and any new periods
        for xmlperiod in xmlperiods:
          if xmlperiod.get('id') == monitorinfo['manifest']['primary']['last']['period'] or monitorinfo['manifest']['primary']['foundlastsegment']:
            # If this is a new period
            if monitorinfo['manifest']['primary']['foundlastsegment']:
              getperiodinfo(logger, xmlperiod, monitorinfo)
            segmenttemplates, primarysegmenttemplate = getsegmenttemplateinfo(logger, xmlperiod, monitorinfo)
            getsegmentinfo(logger, monitorinfo, primarysegmenttemplate, xmlperiod, False)
        gothroughnewsegments(logger, monitorinfo)
      # Stop if did not find any segments
      if not monitorinfo['manifest']['primary']['last']['segment']:
        raise UnsupportedManifest(f"Unable to find segments")
    else:
      raise UnsupportedManifest(f"No XML root")
  except UnsupportedManifest as e:
    logger.error(f"Unsupported manifest, will stop. Exception: {str(e)}")
    raise
  except Exception as e:
    logger.error(f"Failed processing manifest. Exception: {str(e)} Traceback: {traceback.format_exc()}")