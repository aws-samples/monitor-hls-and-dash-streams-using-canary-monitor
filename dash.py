import isodate
import traceback
from lxml import etree as et
import utils
from datetime import datetime, timezone, timedelta

# Custom exceptions
class UnsupportedManifest(Exception):
  pass


def checklipsync(logger, monitorinfo:dict, xmlperiod, segmenttemplates:list):
  templates_info = []
  try:
    ns = {'default': 'urn:mpeg:dash:schema:mpd:2011'}
    for segmenttemplate in segmenttemplates:
      if segmenttemplate.get('mimetype') != 'image/jpeg':
        template_info = getsegmentinfo(logger, monitorinfo, segmenttemplate, xmlperiod, False, True)
        templates_info.append(template_info)
    # Compare PTS values
    if templates_info:
      # Check if all segments have the same n value
      n_values = {item['last_n'] for item in templates_info}
      if len(n_values) == 1:
        # Compute max PTS delta
        pts_values = [seg['last_pts'] for seg in templates_info]
        maxptsdelta = round(max(pts_values) - min(pts_values), 3)
        utils.addmetric(logger, monitorinfo, 'PtsDelta', maxptsdelta, 'Seconds', [])
        if maxptsdelta > monitorinfo['config']['endpointconfig']['validations']['custom']['max_pts_delta']:
          logger.warning(f"Max PTS delta across segmentation templates in period {xmlperiod.get('id', '')} for segment number {next(iter(n_values))} is {maxptsdelta} s, possible lip sync issue", extra={'event': 'LIP_SYNC'})
      elif not monitorinfo['config']['isdai']:
        logger.warning(f"Segment templates in period {xmlperiod.get('id', '')} have different last segment number: {n_values}", extra={'event': 'DIFFERENT_SEGMENT_TEMPLATES'})
  except Exception as e:
    logger.error(f"Error when checking lip sync. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


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
    totalduration = 0.0
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
                'ast+pts': availabilitystarttime + timedelta(seconds=periodstart + (compt - pto) / timescale) if availabilitystarttime else None,
                't+d-pto': (compt + d - pto) / timescale
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
          totalduration += totalduration + d
    return {'last_pts': round(periodstart + (compt - pto) / timescale, 3), 'last_n': segmentnumber, 'total_duration': round(totalduration, 3)}
  except Exception as e:
    logger.error(f"Error finding new segments in period {periodid}. Exception: {str(e)} Traceback: {traceback.format_exc()} Segmenttemplate: {et.tostring(segmenttemplate['xmlsegmenttemplate'], encoding='unicode')}", extra={'event': 'INTERNAL_ERROR'})


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
    primarysegmenttemplate = next((item for item in segmenttemplates if 'video' in item['mimetype']), None)
    if not primarysegmenttemplate:
      primarysegmenttemplate = next((item for item in segmenttemplates if 'audio' in item['mimetype']), None)
    if primarysegmenttemplate:
      primarysegmenttemplate['primary'] = True
      return segmenttemplates, primarysegmenttemplate
    else:
      raise UnsupportedManifest(f"No primary segment template")
  except Exception as e:
    logger.error(f"Error getting segment template. Exception: {str(e)} Traceback: {traceback.format_exc()} Period: {et.tostring(xmlperiod, encoding='unicode')}", extra={'event': 'INTERNAL_ERROR'})
    raise


def geteventstreamsinfo(logger, xmlperiod, periodinfo, monitorinfo):
  ns = {'default': 'urn:mpeg:dash:schema:mpd:2011'}
  try:
    xmleventstreams = xmlperiod.findall('default:EventStream', ns)
    for xmleventstream in xmleventstreams:
      schemeiduri = xmleventstream.get('schemeIdUri', '')
      eventstream = {
        'scheme_id_uri': schemeiduri,
        'is_scte': 'scte' in schemeiduri.lower(),
        'events': []
      }
      xmlevents = xmleventstream.findall('default:Event', ns)
      for xmlevent in xmlevents:
        event = dict()
        # Get info about SCTE event
        if eventstream['is_scte']:
          if xmlevent.get('duration') and xmleventstream.get('timescale'):
            event['duration'] = round(int(xmlevent.get('duration')) / int(xmleventstream.get('timescale')), 3)
          # Raw
          xmlsignal = xmlevent.find('.//{*}Signal')
          if xmlsignal is not None:
            xmlbinary = xmlsignal.find('.//{*}Binary', ns)
            if xmlbinary is not None:
              event['scte_message'] = {
                'raw': xmlbinary.text,
                'decoded': utils.decodesctestring(logger, xmlbinary.text)
              }
          # Decoded
          xmlspliceinfosection = xmlevent.find('.//{*}SpliceInfoSection')
          if xmlspliceinfosection is not None:
            sctemessage = {}
            # Splice insert
            xmlspliceinsert = xmlspliceinfosection.find('.//{*}SpliceInsert')
            if xmlspliceinsert is not None:
              sctemessage['type'] = 'splice_insert'
              sctemessage['out_of_network'] = False
              if xmlspliceinsert.get('outOfNetworkIndicator') == 'true':
                sctemessage['out_of_network'] = True
              if xmlspliceinsert.get('avail_num'):
                sctemessage['avail_num'] = int(xmlspliceinsert.get('avail_num'))
              if xmlevent.get('duration') and xmleventstream.get('timescale'):
                sctemessage['duration'] = round(int(xmlevent.get('duration')) / int(xmleventstream.get('timescale')), 3)
            # Time signal
            xmltimesignal = xmlspliceinfosection.find('.//{*}TimeSignal')
            if xmltimesignal is not None:
              sctemessage['type'] = 'time_signal'
            # Segmentation descriptors
            xmlsegmentationdescriptors = xmlspliceinfosection.findall('.//{*}SegmentationDescriptor')
            for xmlsegmentationdescriptor in xmlsegmentationdescriptors:
              segmentationdescriptor = {}
              xmlsegmentationupid = xmlsegmentationdescriptor.find('.//{*}SegmentationUpid')
              if xmlsegmentationupid is not None:
                if xmlsegmentationupid.get('segmentationTypeId'):
                  segmentationdescriptor['segmentation_type'] = int(xmlsegmentationupid.get('segmentationTypeId'))
                  segmentationdescriptor['segmentation_message'] = utils.segmentationmessagemap.get(xmlsegmentationupid.get('segmentationTypeId'), 'Unknown')
                if xmlsegmentationdescriptor.get('segmentationDuration') and xmleventstream.get('timescale'):
                  segmentationdescriptor['duration'] = round(int(xmlsegmentationdescriptor.get('segmentationDuration')) / int(xmleventstream.get('timescale')), 3)
              sctemessage.setdefault('descriptors', []).append(segmentationdescriptor)
            event['scte_message'] = {
              'raw': '',
              'decoded': sctemessage
            }
          # Check if event is ad break opportunity and mark period as ad break period if it is
          if not monitorinfo['config']['isdai']:
            utils.checkifadbreak(logger, monitorinfo, event)
          if event.get('is_opportunity'):
            # Update periodinfo with ad break info
            periodinfo['adbreak'] = {
              'advertised_duration': event.get('duration', None),
              'type': event.get('type', None)
            }
            if periodinfo['is_adbreak']:
              logger.warning(f"Period {xmlperiod.get('id', '')} has more than one ad break opportunity start event in the EventStream", extra={'event': 'MULTIPLE_AD_BREAK_OPPORTUNITY_EVENTS'})
            periodinfo['is_adbreak'] = True
        # Add event info to event stream
        eventstream['events'].append(event)
      # Add event stream info to period info
      periodinfo['event_streams'].append(eventstream)
  except Exception as e:
    logger.error(f"Error getting event info. Exception: {str(e)} Traceback: {traceback.format_exc()} Period: {et.tostring(xmlperiod, encoding='unicode')}", extra={'event': 'INTERNAL_ERROR'})


# Look for adaptation sets in a period and return info
def getadaptationsetsinfo(logger, xmlperiod, periodinfo:dict, monitorinfo:dict):
  ns = {'default': 'urn:mpeg:dash:schema:mpd:2011'}
  mimetypes = set()
  try:
    periodid = xmlperiod.get('id', '')
    xmladaptationsets = xmlperiod.findall('default:AdaptationSet', ns)
    for xmladaptationset in xmladaptationsets:
      adaptationset = {
        'mime_type': xmladaptationset.get('mimeType'),
        'lang': xmladaptationset.get('lang'),
        'representations': []
      }
      # Find if adaptation set is compact
      xmlsegmenttemplate = xmladaptationset.find('default:SegmentTemplate', ns)
      if xmlsegmenttemplate is not None and xmlsegmenttemplate.find('default:SegmentTimeline', ns) is not None:
        periodinfo['is_compact'] = True
      # Find representations
      xmlrepresentations = xmladaptationset.findall('default:Representation', ns)
      for xmlrepresentation in xmlrepresentations:
        representation = {
          'id': xmlrepresentation.get('id'),
          'width': xmlrepresentation.get('width'),
          'height': xmlrepresentation.get('height'),
          'frame_rate': xmlrepresentation.get('frameRate'),
          'bandwidth': xmlrepresentation.get('bandwidth'),
          'codecs': xmlrepresentation.get('codecs'),
          'audio_sampling_rate': xmlrepresentation.get('audioSamplingRate')
        }
        representation['resolution'] = f"{representation['width']}x{representation['height']}" if representation['width'] and representation['height'] else None
        adaptationset['representations'].append(representation)
      # Check for repeating mime types
      if 'video' in adaptationset['mime_type'] and adaptationset['mime_type'] in mimetypes:
        logger.warning(f"Period {periodid} contains multiple video adaptation sets", extra={'event': 'MULTIPLE_VIDEO_ADAPTATION_SETS'})
      # Add adaptation set info to adaptation sets
      periodinfo['adaptation_sets'].append(adaptationset)
      # Add mime type to set
      mimetypes.add(adaptationset['mime_type'])
    # Check for required renditions
    mimetypemap = {
      'video': 'video',
      'audio': 'audio',
      'subtitles': 'application'
    }
    for requiredtype in monitorinfo['config']['endpointconfig']['validations']['custom']['required_renditions']:
      if mimetypemap.get(requiredtype):
        if not any(mimetypemap[requiredtype] in mimetype for mimetype in mimetypes):
          logger.warning(f"Required rendition '{requiredtype}' not found in period {periodid}", extra={'event': 'RENDITION_NOT_FOUND'})
  except Exception as e:
    logger.error(f"Error getting adaptation set info. Exception: {str(e)} Traceback: {traceback.format_exc()} Period: {et.tostring(xmlperiod, encoding='unicode')}", extra={'event': 'INTERNAL_ERROR'})


# Find out information about period
def getperiodinfo(logger, xmlperiod, monitorinfo:dict):
  ns = {'default': 'urn:mpeg:dash:schema:mpd:2011'}
  xmlperiodid = xmlperiod.get('id', '')
  if xmlperiodid is not None:
    observetime = f"{datetime.now(timezone.utc)}" if monitorinfo['manifest']['primary']['foundlastsegment'] else None
    try:
      periodinfo = {
        'observed': observetime,
        'advertised_duration': xmlperiod.get('duration', ''),
        'duration': 0.0,
        'is_compact': False,
        'is_adbreak': False,
        'event_streams': [],
        'adaptation_sets': [],
        'supplemental_property': {}
      }
      # Get adaptation sets info
      getadaptationsetsinfo(logger, xmlperiod, periodinfo, monitorinfo)
      # Get event stream info
      geteventstreamsinfo(logger, xmlperiod, periodinfo, monitorinfo)
      # Save period info
      monitorinfo['manifest']['primary']['periods'][xmlperiodid] = periodinfo
      logger.info(f"Found {'new ' if monitorinfo['manifest']['primary']['foundlastsegment'] else ''}period, id {xmlperiodid}: {periodinfo}")
      # Update manifest period information
    except Exception as e:
      logger.error(f"Error getting period information. Exception: {str(e)} Traceback: {traceback.format_exc()} Period: {et.tostring(xmlperiod, encoding='unicode')}", extra={'event': 'INTERNAL_ERROR'})
  else:
    raise UnsupportedManifest(f"Period has no id")


def startadbreak(logger, periodid, monitorinfo:dict, new: bool):
  try:
    # Check for back to back ad break
    lastperiod = monitorinfo['manifest']['primary']['last']['period']
    if lastperiod and monitorinfo['manifest']['primary']['periods'][lastperiod]['is_adbreak']:
      logger.warning(f"Back to back ad break without proper clousure of previous ad break", extra={'event': 'BACK_TO_BACK_AD_BREAK'})
    if new:
      # Send metrics
      if monitorinfo['manifest']['primary']['periods'][periodid].get('adbreak'):
        adbreak = monitorinfo['manifest']['primary']['periods'][periodid]['adbreak']
        utils.addmetric(logger, monitorinfo, 'Start', 1, 'Count', [{'Name': 'AdBreakType', 'Value': adbreak['type']}])
        if adbreak.get('advertised_duration') and adbreak['advertised_duration'] > 0:
          utils.addmetric(logger, monitorinfo, 'AdvertisedDuration', adbreak['advertised_duration'], 'Seconds', [{'Name': 'AdBreakType', 'Value': adbreak['type']}])
        elif monitorinfo['config']['endpointconfig']['validations']['custom']['check_ad_break_scte_duration']:
          logger.warning(f"Ad break has no duration", extra={'event': 'AD_BREAK_DURATION_NOT_FOUND'})
  except Exception as e:
    logger.error(f"Error at ad break start. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


def endadbreak(logger, monitorinfo:dict, new: bool):
  try:
    lastperiod = monitorinfo['manifest']['primary']['last']['period']
    if lastperiod:
      if monitorinfo['manifest']['primary']['periods'][lastperiod].get('is_adbreak'):
        adbreak = monitorinfo['manifest']['primary']['periods'][lastperiod].get('adbreak')
        if adbreak:
          adbreak['segments_duration'] = round(monitorinfo['manifest']['primary']['periods'][lastperiod]['duration'], 3)
          if new:
            # Send metric for ad break segments duration
            utils.addmetric(logger, monitorinfo, 'SegmentsDuration', adbreak['segments_duration'], 'Seconds', [{'Name': 'AdBreakType', 'Value': adbreak['type']}])
          # Get ad break duration delta
          if adbreak.get('advertised_duration') and adbreak['advertised_duration'] > 0:
            adbreak['duration_delta'] = round(adbreak['segments_duration'] - adbreak['advertised_duration'], 3)
            if new:
              utils.addmetric(logger, monitorinfo, 'DurationDelta', abs(adbreak['duration_delta']), 'Seconds', [{'Name': 'AdBreakType', 'Value': adbreak['type']}])
              if abs(adbreak['duration_delta']) > monitorinfo['config']['endpointconfig']['validations']['custom']['max_ad_break_duration_delta']:
                logger.warning(f"Ad break duration was {'longer' if adbreak['duration_delta'] > 0 else 'shorter'} than advertised by {abs(adbreak['duration_delta'])} seconds", extra={'event': 'AD_BREAK_DURATION_DELTA_BREACHED'})
  except Exception as e:
    logger.error(f"Error at ad break end. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


def gothroughsegments(logger, monitorinfo:dict, new:bool=False):
  try:
    # Go through all new periods
    for periodid, segments in monitorinfo['manifest']['primary']['new']['segments'].items():
      if monitorinfo['config']['isdai']:
        pass
      else:
        # If this is a new period, start and end ad breaks
        if periodid != monitorinfo['manifest']['primary']['last']['period']:
          endadbreak(logger, monitorinfo, new)
          if monitorinfo['manifest']['primary']['periods'][periodid]['is_adbreak']:
            startadbreak(logger, periodid, monitorinfo, new)
      # Go through all new segments
      for segment in segments:
        # Update period duration
        monitorinfo['manifest']['primary']['periods'][periodid]['duration'] += segment['dsec']
        if new:
          # Update new segments duration
          monitorinfo['manifest']['primary']['new']['duration'] += segment['dsec']
          # Check for discontinuity
          if segment['t'] != monitorinfo['manifest']['primary']['last']['segment']['nextt']:
            if monitorinfo['config']['isdai']:
              pass
            else:
              logger.warning(f"Discontinuity", extra={'event': 'DISCONTINUITY'})
              utils.addmetric(logger, monitorinfo, 'Discontinuity', 1, 'Count', [])
          # Send metric for segment duration
          utils.addmetric(logger, monitorinfo, 'SegmentDuration', segment['dsec'], 'Seconds', [])
        # Update last segment
        monitorinfo['manifest']['primary']['last']['segment'] = segment.copy()
      # Update last period
      monitorinfo['manifest']['primary']['last']['period'] = periodid
      # Check for last segment exceeding period duration
      period_advertised_duration = monitorinfo['manifest']['primary']['periods'][periodid].get('advertised_duration')
      if period_advertised_duration:
        period_advertised_duration_sec = isodate.parse_duration(period_advertised_duration).total_seconds()
        duration_delta = monitorinfo['manifest']['primary']['last']['segment']['t+d-pto'] - period_advertised_duration_sec
        if duration_delta > 1:
          logger.warning(f"Segments in period {periodid} exceed the period duration by {round(duration_delta, 3)} s", extra={'event': 'PERIOD_DURATION_EXCEEDED'})
    # If this is not 1st manifest request
    if new:
      lastsegment = monitorinfo['manifest']['primary']['last']['segment']
      # Check for segment availability delta of last new segment
      if lastsegment and monitorinfo['manifest']['primary']['new']['segments']:
        if lastsegment['ast+pts'] is not None:
          availabilitydelta = round((lastsegment['ast+pts'] - monitorinfo['manifest']['primary']['manifestrequesttime']).total_seconds(), 3)
          if availabilitydelta < 0 and abs(availabilitydelta) > monitorinfo['config']['endpointconfig']['validations']['custom']['max_segment_availability_delta']['in_past']:
            logger.warning(
              f"Segment availability time (availabilityStartTime + period start + (t – presentationTimeOffset) / timescale) is {abs(availabilitydelta)} seconds in the past, which is more than the configured 'max_segment_availability_delta' threshold of {monitorinfo['config']['endpointconfig']['validations']['custom']['max_segment_availability_delta']['in_past']}",
              extra={'event': 'SEGMENT_AVAILABILITY_DELTA'})
          elif availabilitydelta > monitorinfo['config']['endpointconfig']['validations']['custom']['max_segment_availability_delta']['in_future']:
            logger.warning(
              f"Segment availability time (availabilityStartTime + period start + (t – presentationTimeOffset) / timescale) is {abs(availabilitydelta)} seconds in the future, which is more than the configured 'max_segment_availability_delta' threshold of {monitorinfo['config']['endpointconfig']['validations']['custom']['max_segment_availability_delta']['in_future']}",
              extra={'event': 'SEGMENT_AVAILABILITY_DELTA'})
          utils.addmetric(logger, monitorinfo, 'SegmentAvailabilityDelta', availabilitydelta, 'Seconds', [])
      # Check if found last segment
      monitorinfo['manifest']['primary']['lastsegmentnotfoundcount'] = 0 if monitorinfo['manifest']['primary']['foundlastsegment'] else monitorinfo['manifest']['primary']['lastsegmentnotfoundcount'] + 1
      if 0 < monitorinfo['manifest']['primary']['lastsegmentnotfoundcount'] < 3:
        logger.warning(f"Last segment not found", extra={'event': 'LAST_SEGMENT_NOT_FOUND'})
      elif monitorinfo['manifest']['primary']['lastsegmentnotfoundcount'] == 3:
        logger.warning(f"Last segment not found, restarting", extra={'event': 'LAST_SEGMENT_NOT_FOUND'})
        monitorinfo['manifest']['primary']['last']['segment'] = {}
        monitorinfo['manifest']['primary']['last']['period'] = ''
  except Exception as e:
    logger.error(f"Error going through new segments. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


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
      logger.warning(f"Missing availabilityStartTime in manifest", extra={'event': 'AVAILABILITY_START_TIME_NOT_FOUND'})
    # Suggested presentation delay
    monitorinfo['manifest']['primary']['suggestedpresentationdelay'] = isodate.parse_duration(xmlroot.get('suggestedPresentationDelay', 'PT0S')).total_seconds()
    # Use timeShiftBufferDepth for detecting manifest duration
    xmltimeshiftbufferdepth = xmlroot.get('timeShiftBufferDepth')
    if xmltimeshiftbufferdepth:
      manifestduration = isodate.parse_duration(xmltimeshiftbufferdepth).total_seconds()
      # Send metric for manifest duration
      utils.addmetric(logger, monitorinfo, 'ManifestDuration', round(manifestduration), 'Seconds', [])
  except Exception as e:
    logger.error(f"Error getting availabilityStartTime. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


def checkmanifestconsistency(logger, monitorinfo:dict):
  inconsistency = False
  try:
    current = monitorinfo['manifest']['primary']['consistency']['current']['periods']
    previous = monitorinfo['manifest']['primary']['consistency']['previous']['periods']
    if len(current) == 0:
      inconsistency = True
    elif len(previous) > 0:
      # First current period must exist in previous
      first_current = current[0]
      if first_current not in previous:
        inconsistency = True
      else:
        # Get all periods from previous that should still be present
        start_idx = previous.index(first_current)
        expected_periods = previous[start_idx:]
        # Check if current starts with these expected periods (in order)
        if not current[:len(expected_periods)] == expected_periods:
          inconsistency = True
    if inconsistency:
      logger.warning(f"Manifests are inconsistent, previous periods: {previous}, current periods: {current}", extra={'event': 'INCONSISTENT_MANIFEST_PERIODS'})
    monitorinfo['manifest']['primary']['consistency']['previous']['periods'] = current.copy()
  except Exception as e:
    logger.error(f"Error during manifest consistency check. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})


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
            periodid = xmlperiod.get('id', '')
            if periodid == monitorinfo['manifest']['primary']['last']['period'] or monitorinfo['manifest']['primary']['foundlastsegment']:
              # If this is a new period
              if monitorinfo['manifest']['primary']['foundlastsegment']:
                getperiodinfo(logger, xmlperiod, monitorinfo)
              segmenttemplates, primarysegmenttemplate = getsegmenttemplateinfo(logger, xmlperiod, monitorinfo)
              getsegmentinfo(logger, monitorinfo, primarysegmenttemplate, xmlperiod, False, False)
              checklipsync(logger, monitorinfo, xmlperiod, segmenttemplates)
              # Update advertised period duration
              advertised_duration = xmlperiod.get('duration', '')
              if advertised_duration != monitorinfo['manifest']['primary']['periods'][periodid]['advertised_duration']:
                logger.debug(f"Period {periodid} has new advertised duration {advertised_duration}")
                monitorinfo['manifest']['primary']['periods'][periodid]['advertised_duration'] = advertised_duration
            # Collect period ids for manifest consistency check and check for duplicate periods
            if periodid and periodid in monitorinfo['manifest']['primary']['consistency']['current']['periods']:
              logger.warning(f"Manifest has duplicate period id '{periodid}'", extra={'event': 'DUPLICATE_PERIOD'})
            monitorinfo['manifest']['primary']['consistency']['current']['periods'].append(periodid)
          gothroughsegments(logger, monitorinfo, True)
          checkmanifestconsistency(logger, monitorinfo)
      else:
        logger.warning(f"Manifest type is '{xmlmpdtype}', should be 'dynamic'", extra={'event': 'NON_LIVE_MANIFEST'})
    else:
      raise UnsupportedManifest(f"No XML root")
  except UnsupportedManifest as e:
    logger.error(f"Unsupported manifest, will stop. Exception: {str(e)}", extra={'event': 'INTERNAL_ERROR'})
    raise
  except Exception as e:
    logger.error(f"Failed processing manifest. Exception: {str(e)} Traceback: {traceback.format_exc()}", extra={'event': 'INTERNAL_ERROR'})