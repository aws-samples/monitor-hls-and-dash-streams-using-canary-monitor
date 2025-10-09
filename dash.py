import isodate
import traceback
from lxml import etree as et
import utils
from datetime import datetime, timezone, timedelta

# Custom exceptions
class UnsupportedManifest(Exception):
  pass


def gothroughsegmenttemplatesofperiod(logger, monitorinfo:dict, xmlperiod, segmenttemplates:list):
  ptsvalues = set()
  try:
    ns = {'default': 'urn:mpeg:dash:schema:mpd:2011'}
    for segmenttemplate in segmenttemplates:
      pts = getsegmentinfo(logger, monitorinfo, segmenttemplate, xmlperiod, False, True)
      ptsvalues.add(pts)
    # Compare PTS values
    if ptsvalues:
      maxptsdelta = round(max(ptsvalues) - min(ptsvalues), 3)
      utils.addmetric(logger, monitorinfo, 'PtsDelta', maxptsdelta, 'Seconds', [])
      if maxptsdelta > monitorinfo['config']['endpointconfig']['validations']['custom']['maxptsdelta']:
        logger.warning(f"Max PTS delta across segmentation templates is {maxptsdelta} s, possible lip sync issue")
  except Exception as e:
    logger.error(f"Error going through segment templates. Exception: {str(e)} Traceback: {traceback.format_exc()}")


# Find new segments in a period
def getsegmentinfo(logger, monitorinfo:dict, segmenttemplate, xmlperiod, allsegments:bool, onlyvalidation:bool):
  ns = {'default': 'urn:mpeg:dash:schema:mpd:2011'}
  periodid = xmlperiod.get('id', '')
  try:
    compt = 0
    availabilitystarttime = monitorinfo['manifest']['primary'].get('availabilitystarttime', None)
    periodstart = isodate.parse_duration(xmlperiod.get('start', 'PT0S')).total_seconds()
    timescale = int(segmenttemplate['xmlsegmenttemplate'].get('timescale', 1))
    segmentnumber = int(segmenttemplate['xmlsegmenttemplate'].get('startNumber', 0))
    pto = int(segmenttemplate['xmlsegmenttemplate'].get('presentationTimeOffset', 0))
    xmlsegmenttimeline = segmenttemplate['xmlsegmenttemplate'].find('default:SegmentTimeline', ns)
    for element in xmlsegmenttimeline:
      # No pattern
      if element.tag == f"{{{ns['default']}}}S":
        d = int(element.get('d'))
        t = int(element.get('t', compt))
        r = int(element.get('r', 0))
        if t != compt:
          compt = t
        for i in range(r + 1):
          if not onlyvalidation:
            if monitorinfo['manifest']['primary']['foundlastsegment'] or allsegments:
              segment = {
                'n': segmentnumber,
                'd': d,
                'dsec': d / timescale,
                't': compt,
                'nextt': compt + d,
                'pts': periodstart + (compt - pto) / timescale,
                'ast+pts': availabilitystarttime + timedelta(seconds=periodstart + (compt - pto) / timescale) if availabilitystarttime else None
              }
              monitorinfo['manifest']['primary']['new']['segments'].setdefault(periodid, []).append(segment)
              if not allsegments:
                logger.debug(f"Found new segment in period {periodid}: {utils.printdictionary(logger, segment)}")
            else:
              if periodid == monitorinfo['manifest']['primary']['last']['period']:
                if compt == monitorinfo['manifest']['primary']['last']['segment']['t']:
                  monitorinfo['manifest']['primary']['foundlastsegment'] = True
          compt = compt + d
          segmentnumber = segmentnumber + 1
    return round(periodstart + (compt - pto) / timescale, 3)
  except Exception as e:
    logger.error(f"Error finding new segments in period {periodid}. Exception: {str(e)} Traceback: {traceback.format_exc()} Segmenttemplate: {et.tostring(segmenttemplate['xmlsegmenttemplate'], encoding='unicode')}")


# Return all available segment templates and mark a primary
def getsegmenttemplateinfo(logger, xmlperiod, monitorinfo):
  segmenttemplates = []
  primarysegmenttemplate = None
  ns = {'default': 'urn:mpeg:dash:schema:mpd:2011'}
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
      return segmenttemplates, primarysegmenttemplate
    else:
      raise UnsupportedManifest(f"No segment template")
  except Exception as e:
    logger.error(f"Error getting segment template. Exception: {str(e)} Traceback: {traceback.format_exc()} Period: {et.tostring(xmlperiod, encoding='unicode')}")
    raise


# Find out if period is an ad break and collect ad break type and duration
def determineifadbreak(logger, xmlperiodid, monitorinfo, periodinfo, observetime):
  adbreakinfo = {
    'observed': observetime,
    'advertisedduration': 0.0,
    'segmentsduration': 0.0
  }
  try:
    if monitorinfo['config']['origin'].lower() == 'emt':
      if '_' in xmlperiodid:
        periodinfo['isadbreak'] = True
        adbreakinfo['type'] = 'regular'
      else:
        for sctemessage in periodinfo['spliceinfo']:
          if 'descriptors' in sctemessage.keys():
            for descriptor in sctemessage['descriptors']:
              if 'segmentationtype' in descriptor.keys():
                if descriptor['segmentationtype'] == 56:
                  periodinfo['isadbreak'] = True
                  adbreakinfo['type'] = 'overlay'
                  if 'availnum' in sctemessage.keys():
                    adbreakinfo['availnum'] = sctemessage['availnum']
                  adbreakinfo['advertisedduration'] = descriptor['duration'] if 'duration' in descriptor.keys() else 0.0
    else:
      for adbreaksctesignal in monitorinfo['config']['endpointconfig']['validations']['custom']['adbreaksctesignals']:
        if isinstance(adbreaksctesignal, int) or adbreaksctesignal.isdigit():
          adbreaksctesignal = int(adbreaksctesignal)
          for sctemessage in periodinfo['spliceinfo']:
            if 'descriptors' in sctemessage.keys():
              for descriptor in sctemessage['descriptors']:
                if 'segmentationtype' in descriptor.keys():
                  if descriptor['segmentationtype'] == adbreaksctesignal:
                    periodinfo['isadbreak'] = True
                    if 'duration' in descriptor.keys():
                      adbreakinfo['advertisedduration'] = descriptor['duration']
                    else:
                      adbreakinfo['advertisedduration'] = 0
                      if monitorinfo['config']['endpointconfig']['validations']['custom']['checkadbreakscteduration']:
                        logger.warning(f"SCTE message with segmentation descriptor type {adbreaksctesignal} contains no duration ")
                    if 'availnum' in sctemessage.keys():
                      adbreakinfo['availnum'] = sctemessage['availnum']
                    adbreakinfo['type'] = 'overlay' if adbreaksctesignal == 56 else 'regular'
        else:
          for sctemessage in periodinfo['spliceinfo']:
            if 'type' in sctemessage.keys() and sctemessage['type'] == adbreaksctesignal:
              if 'outofnetwork' in sctemessage.keys() and sctemessage['outofnetwork']:
                periodinfo['isadbreak'] = True
                if 'duration' in sctemessage.keys():
                  adbreakinfo['advertisedduration'] = sctemessage['duration']
                else:
                  adbreakinfo['advertisedduration'] = 0
                  if monitorinfo['config']['endpointconfig']['validations']['custom']['checkadbreakscteduration']:
                    logger.warning(f"SCTE message type {adbreaksctesignal} contains no duration")
                if 'availnum' in sctemessage.keys():
                  adbreakinfo['availnum'] = sctemessage['availnum']
                adbreakinfo['type'] = 'regular'
                if 'descriptors' in sctemessage.keys():
                  for descriptor in sctemessage['descriptors']:
                    if 'segmentationtype' in descriptor.keys() and descriptor['segmentationtype'] == 56:
                      adbreakinfo['type'] = 'overlay'
    return adbreakinfo
  except Exception as e:
    logger.error(f"Error determining if period as an ad break. Exception: {str(e)} Traceback: {traceback.format_exc()}")


# Find out information about period
def getperiodinfo(logger, xmlperiod, monitorinfo:dict):
  ns = {'default': 'urn:mpeg:dash:schema:mpd:2011'}
  xmlperiodid = xmlperiod.get('id', '')
  if xmlperiodid is not None:
    observetime = f"{datetime.now(timezone.utc)}" if monitorinfo['manifest']['primary']['foundlastsegment'] else None
    try:
      # Get period info
      periodinfo = {
        'observed': observetime,
        'compact': False,
        'isadbreak': False,
        'spliceinfo': [],
        'adaptationsets': [],
        'eventstream': None
      }
      # Adaptations sets
      xmladaptationsets = xmlperiod.findall('default:AdaptationSet', ns)
      for xmladaptationset in xmladaptationsets:
        periodinfo['adaptationsets'].append({
          'mimetype': xmladaptationset.get('mimeType', ''),
          'renditions': len(xmladaptationset.findall('default:Representation', ns))
        })
        xmlsegmenttemplate = xmladaptationset.find('default:SegmentTemplate', ns)
        # Compactness
        if xmlsegmenttemplate is not None and xmlsegmenttemplate.find('default:SegmentTimeline', ns) is not None:
          periodinfo['compact'] = True
      # Event stream info
      xmleventstream = xmlperiod.find('default:EventStream', ns)
      if xmleventstream is not None:
        periodinfo['eventstream'] = et.tostring(xmleventstream, encoding='unicode')
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
            if 'descriptors' in sctemessage.keys() and len(sctemessage['descriptors']) > 1:
              logger.warning(f"SCTE message contains multiple ({len(sctemessage['descriptors'])}) segmentation descriptors: {sctemessage['descriptors']}")
            periodinfo['spliceinfo'].append(sctemessage)
          # Binary
          xmlsignal = xmlevent.find('.//{*}Signal')
          if xmlsignal is not None:
            xmlbinary = xmlsignal.find('.//{*}Binary', ns)
            if xmlbinary is not None:
              periodinfo['spliceinfo'].append(utils.decodesctestring(logger, xmlbinary.text))
      # Determine if period is ad break
      adbreakinfo = determineifadbreak(logger, xmlperiodid, monitorinfo, periodinfo, observetime)
      # Update manifest ad break information, send metric for ad break start and type
      if periodinfo['isadbreak'] and adbreakinfo:
        adbreakid = xmlperiodid.split('_')[0]
        if adbreakid not in monitorinfo['reporting']['adbreaks'].keys():
          monitorinfo['reporting']['adbreaks'][adbreakid] = adbreakinfo
          if monitorinfo['manifest']['primary']['foundlastsegment']:
            utils.addmetric(logger, monitorinfo, 'Start', 1, 'Count', [{'Name': 'AdBreakType', 'Value': adbreakinfo['type']}])
            if 'advertisedduration' in adbreakinfo.keys():
              utils.addmetric(logger, monitorinfo, 'AdvertisedDuration', adbreakinfo['advertisedduration'], 'Seconds', [{'Name': 'AdBreakType', 'Value': adbreakinfo['type']}])
            if 'availnum' in adbreakinfo.keys():
              utils.addmetric(logger, monitorinfo, 'AvailNum', adbreakinfo['availnum'], 'Count', [{'Name': 'AdBreakType', 'Value': adbreakinfo['type']}])
      # Log
      # logger.debug(f"Found {'new ' if monitorinfo['manifest']['primary']['foundlastsegment'] else ''}period {xmlperiodid}: compact={periodinfo['compact']}, adbreak={periodinfo['isadbreak']}{', type=' + adbreakinfo['type'] if 'type' in adbreakinfo.keys() else ''}{', spliceinfo=' + str(periodinfo['spliceinfo']) + ', ' if len(periodinfo['spliceinfo']) > 0 else ''}, adaptation sets={periodinfo['adaptationsets']}")
      logger.debug(f"Found {'new ' if monitorinfo['manifest']['primary']['foundlastsegment'] else ''}period {xmlperiodid}: {periodinfo}")
      # Validate required renditions
      for requiredrendition in monitorinfo['config']['endpointconfig']['validations']['custom']['requiredrenditions']:
        found = any(item['mimetype'].startswith(requiredrendition) for item in periodinfo['adaptationsets'])
        if not found:
          logger.warning(f"Missing {requiredrendition} rendition")
      # Update manifest period information
      monitorinfo['reporting']['periods'][xmlperiodid] = periodinfo
    except Exception as e:
      logger.error(f"Error getting period information. Exception: {str(e)} Traceback: {traceback.format_exc()} Period: {et.tostring(xmlperiod, encoding='unicode')}")
  else:
    raise UnsupportedManifest(f"Period has no id")


def gothroughsegments(logger, monitorinfo:dict, new:bool=False):
  try:
    for period, segments in monitorinfo['manifest']['primary']['new']['segments'].items():
      # Update ad break duration if period is ad break
      if monitorinfo['reporting']['periods'][period]['isadbreak']:
        adbreakid = period.split('_')[0]
        if adbreakid in monitorinfo['reporting']['adbreaks'].keys():
          for segment in segments:
            monitorinfo['reporting']['adbreaks'][adbreakid]['segmentsduration'] = monitorinfo['reporting']['adbreaks'][adbreakid]['segmentsduration'] + segment['dsec']
      if new:
        # If last period was an ad break send last ad break info
        if monitorinfo['reporting']['periods'][monitorinfo['manifest']['primary']['last']['period']]['isadbreak']:
          lastadbreakid = monitorinfo['manifest']['primary']['last']['period'].split('_')[0]
          if lastadbreakid not in period:
            if lastadbreakid in monitorinfo['reporting']['adbreaks'].keys():
              utils.updateadbreakdurationdelta(logger, monitorinfo, lastadbreakid, new)
              utils.addmetric(logger, monitorinfo, 'SegmentsDuration', monitorinfo['reporting']['adbreaks'][lastadbreakid]['segmentsduration'], 'Seconds', [{'Name': 'AdBreakType', 'Value': monitorinfo['reporting']['adbreaks'][lastadbreakid]['type']}])
      # Go through all segments
      for segment in segments:
        if new:
          # Update new segments duration
          monitorinfo['manifest']['primary']['new']['duration'] = monitorinfo['manifest']['primary']['new']['duration'] + segment['dsec']
          # Check for discontinuity
          if segment['t'] != monitorinfo['manifest']['primary']['last']['segment']['nextt']:
            logger.warning(f"Discontinuity")
            utils.addmetric(logger, monitorinfo, 'Discontinuity', 1, 'Count', [{'Name': 'Rendition', 'Value': "multi"}])
          # Check for segment availability delta
          if segment['ast+pts'] is not None:
            availabilitydelta = round((segment['ast+pts'] - monitorinfo['manifest']['primary']['manifestrequesttime']).total_seconds(), 3)
            if availabilitydelta > monitorinfo['config']['endpointconfig']['validations']['custom']['maxfuturesegmentavailability']:
              logger.warning(f"Segment availability time (availabilityStartTime + period start + (t – presentationTimeOffset) / timescale) is {availabilitydelta} seconds in the future, which is more than the configured 'maxfuturesegmentavailability' threshold of {monitorinfo['config']['endpointconfig']['validations']['custom']['maxfuturesegmentavailability']}")
        # Update last segment
        monitorinfo['manifest']['primary']['last']['segment'] = segment.copy()
      # Update last period
      monitorinfo['manifest']['primary']['last']['period'] = period
    if new:
      # Check if found last segment
      monitorinfo['manifest']['primary']['lastsegmentnotfoundcount'] = 0 if monitorinfo['manifest']['primary']['foundlastsegment'] else monitorinfo['manifest']['primary']['lastsegmentnotfoundcount'] + 1
      if 0 < monitorinfo['manifest']['primary']['lastsegmentnotfoundcount'] < 3:
        logger.warning(f"Last segment not found")
      elif monitorinfo['manifest']['primary']['lastsegmentnotfoundcount'] == 3:
        logger.warning(f"Last segment not found, restarting")
        monitorinfo['manifest']['primary']['last']['segment'] = {}
        monitorinfo['manifest']['primary']['last']['period'] = ''
  except Exception as e:
    logger.error(f"Error going through new segments. Exception: {str(e)} Traceback: {traceback.format_exc()}")


# Get availabtilityStartTime from manifest
def gethighlevelmetadata(logger, xmlroot, monitorinfo:dict):
  try:
    # Availability start time
    xmlavailabilitystarttime = xmlroot.get('availabilityStartTime')
    if xmlavailabilitystarttime:
      if xmlavailabilitystarttime.endswith('Z'):
        xmlavailabilitystarttime = xmlavailabilitystarttime[:-1] + '+00:00'
      monitorinfo['manifest']['primary']['availabilitystarttime'] = datetime.fromisoformat(xmlavailabilitystarttime)
    else:
      logger.warning(f"Missing availabilityStartTime in manifest")
    # Suggested presentation delay
    monitorinfo['manifest']['primary']['suggestedpresentationdelay'] = isodate.parse_duration(xmlroot.get('suggestedPresentationDelay', 'PT0S')).total_seconds()
  except Exception as e:
    logger.error(f"Error getting availabilityStartTime. Exception: {str(e)} Traceback: {traceback.format_exc()}")


def checkmanifestconsistency(logger, monitorinfo:dict):
  try:
    foundoverlappingperiodid = False
    for item in monitorinfo['manifest']['primary']['consistency']['previous']['periods']:
      if foundoverlappingperiodid:
        if item not in monitorinfo['manifest']['primary']['consistency']['current']['periods']:
          logger.warning(f"Manifest is inconsistent, pervious periods: {monitorinfo['manifest']['primary']['consistency']['previous']['periods']}, current periods: {monitorinfo['manifest']['primary']['consistency']['current']['periods']}")
      elif item in monitorinfo['manifest']['primary']['consistency']['current']['periods']:
        foundoverlappingperiodid = True
    monitorinfo['manifest']['primary']['consistency']['previous']['periods'] = monitorinfo['manifest']['primary']['consistency']['current']['periods'].copy()
  except Exception as e:
    logger.error(f"Error during manifest consistency check. Exception: {str(e)} Traceback: {traceback.format_exc()}")


# Dash monitor
def monitor(logger, monitorinfo:dict, response:bytes):
  ns = {'default': 'urn:mpeg:dash:schema:mpd:2011'}
  xmlroot = et.fromstring(response)
  xmlmpdtype = xmlroot.get('type', '')
  segmenttemplates = []
  try:
    if xmlroot is not None:
      if xmlmpdtype == 'dynamic':
        xmlperiods = xmlroot.findall('default:Period', ns)
        gethighlevelmetadata(logger, xmlroot, monitorinfo)
        if not monitorinfo['manifest']['primary']['last']['segment']:
          # Go through all periods
          for xmlperiod in xmlperiods:
            getperiodinfo(logger, xmlperiod, monitorinfo)
            segmenttemplates, primarysegmenttemplate = getsegmenttemplateinfo(logger, xmlperiod, monitorinfo)
            getsegmentinfo(logger, monitorinfo, primarysegmenttemplate, xmlperiod, True, False)
          gothroughsegments(logger, monitorinfo)
          # Stop if did not find any segments
          if not monitorinfo['manifest']['primary']['last']['segment']:
            raise UnsupportedManifest(f"Unable to find segments")
        else:
          # Go through last and any new periods
          for xmlperiod in xmlperiods:
            xmlperiodid = xmlperiod.get('id', '')
            if xmlperiodid == monitorinfo['manifest']['primary']['last']['period'] or monitorinfo['manifest']['primary']['foundlastsegment']:
              # If this is a new period
              if monitorinfo['manifest']['primary']['foundlastsegment']:
                getperiodinfo(logger, xmlperiod, monitorinfo)
              segmenttemplates, primarysegmenttemplate = getsegmenttemplateinfo(logger, xmlperiod, monitorinfo)
              getsegmentinfo(logger, monitorinfo, primarysegmenttemplate, xmlperiod, False, False)
              gothroughsegmenttemplatesofperiod(logger, monitorinfo, xmlperiod, segmenttemplates)
            # Collect period ids for manifest consistency check
            monitorinfo['manifest']['primary']['consistency']['current']['periods'].append(xmlperiodid)
          gothroughsegments(logger, monitorinfo, True)
          checkmanifestconsistency(logger, monitorinfo)
      else:
        logger.warning(f"Manifest type is '{xmlmpdtype}', should be 'dynamic'")
    else:
      raise UnsupportedManifest(f"No XML root")
  except UnsupportedManifest as e:
    logger.error(f"Unsupported manifest, will stop. Exception: {str(e)}")
    raise
  except Exception as e:
    logger.error(f"Failed processing manifest. Exception: {str(e)} Traceback: {traceback.format_exc()}")