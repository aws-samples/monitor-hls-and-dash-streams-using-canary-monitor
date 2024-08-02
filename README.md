## Monitor HLS and DASH Streams Using Canary Monitor

The canary monitor is a tool, which, like a player, downloads and inspects HLS or DASH manifests from an origin at regular intervals. It can also download segments and ad-tracking data. It inspects the data after every download, runs a check for a large set of validations, creates logs and publishes custom metrics to Amazon CloudWatch. It works with various origins, but has been primarily designed to monitor  streams originating from AWS Elemental MediaPackage and AWS Elemental MediaTailor. When pointed towards a MediaTailor origin, it can detect ad-break content replaced by the service and validate the ad-tracking data during ad breaks. 

The script expects the user to provide one or more HLS or DASH live stream manifest URLs to monitor, which must be accessible from the machine where the script is running. The HLS manifest URL can be pointing to either the top-level (multivariant) manifest, or a specific rendition’s manifest. When pointed to the top-level HLS manifest, the script can pick and monitor one specific rendition, multiple renditions, or all renditions. Once started, the script  continues to send an HTTP GET request for all provided manifest URLs at regular intervals (default 5 seconds) until you stop the process.

Each time the script downloads a manifest, it performs checks on the content of the manifest and optionally emits CloudWatch metrics. The checks include monitoring for staleness, discontinuities, spec compliance, ad breaks, and audio/video misalignment. If monitoring of MediaTailor ad-tracking data is enabled, the script looks for avail information during detected ad breaks. See the following table, which highlights important log messages and details the type of checks the script performs.

### Checks

|Name	|Description	|Log level	|Impact category	|
|---	|---	|---	|---	|
|HTTP errors and timeouts	|Occurs when a manifest, segment or ad-tracking data request receives a non 200 response or times out.	|WARNING	|Playback	|
|Possible lip sync issue	|Occurs when presentation time of a segment n from an adaptation set is more than 100 ms away from presentation time of segment n from any other adaptation set in a DASH manifest.	|WARNING	|Playback	|
|Last segment not found	|Occurs when last known segment (identified by media sequence id) is not found in the most recent HLS manifest. Occurs when last known segment (identified by period id and segment number) is not found in the most recent DASH manifest.	|WARNING	|Playback	|
|Last segment info has changed	|Occurs when last known segment (identified by media sequence id in HLS and period id and segment number in DASH) is found, but some attributes about the segment have changed like name or duration.	|WARNING	|Playback	|
|Discontinuity	|Occurs when EXT-X-DISCONTINUITY is found in an HLS manifest. Occurs when "t" value of segment n+1 does not equal "t" + "d" value of segment n and segments are in the same DASH manifest period.	|WARNING	|Playback	|
|Content shortage	|Occurs when the sum of new content durations in the 2 most recent manifest requests is less than 25% of the expected new content duration in the time window. This metrics works only when manifest request frequency is more than the average segment duration.	|WARNING	|Playback	|
|Staleness	|Occurs when no new segment is found in the manifest response for x seconds, where x is the value defined by --stale argument.	|WARNING	|Playback	|
|Differences between segments across renditions	|Occurs when the same media sequence segments across renditions differ in any of the following associated attributes when monitoring multipel HLS renditions: discontinuity (has EXT-X-DISCONTINUITY tag), discontinuity sequence, EXT-X-PROGRAM-DATE-TIME (only video segments).	|WARNING	|Playback	|
|Manifest value has changed	|Occurs when EXT-X-VERSION or EXT-X-TARGETDURATION value have changed in an HLS manifest.	|WARNING	|Playback	|
|Segment duration exceeded target duration	|Occurs when rounded segment duration (EXTINF) to the nearest integer is larger than the value of EXT-X-TARGETDURATION in an HLS manifest.	|WARNING	|Playback	|
|Duration of period was less than 500 milliseconds	|Occurs when duration of a DASH period was less than 500 milliseconds.	|WARNING	|Playback	|
|Inconsistency in manifest periods	|Occurs when list of periods in a DASH manifest identified by period id is not a subset of a list of periods from previous manifest, exluding any new periods.	|WARNING	|Playback	|
|Missing audio or video adaptation set	|Occurs when a new period doesn't contain a video or an audio adaptation set.	|WARNING	|Playback	|
|Jump in PDT value	|Occurs when monitoring and HLS endpoint a negative jump in EXT-X-PROGRAM-DATE-TIME or a postitive jump by more than 2x EXT-X-TARGETDURATION in EXT-X-PROGRAM-DATE-TIME is detected for subsequent segments.	|WARNING	|Playback	|
|Ad break was longer/shorter than advertised	|Occurs when the sum of segment durations between ad break start and end does not match the advertised ad break duration (+- 1 second).	|WARNING	|Monetization	|
|Nested ad break start	|Occurs when an ad break start manifest decoration is found while inside of an ad break.	|WARNING	|Monetization	|
|Did not find expected tracking info during ad break	|Occurs when an avail was not found in the ad-tracking data throughout the duration of an ad break when monitoring a MediaTailor endpoint. An avail is found when calculated playhead during an ad break falls within the startTimeInSeconds to (startTimeInSeconds + durationInSeconds) range for any avail found in the ad-tracking data.	|WARNING	|Monetization	|
|Playhead is drifted	|Occurs when monitoring a MediaTailor endpoint including ad-tracking data and the calculated playhead at ad break start is more than 15 seconds apart from the startTimeInSeconds for the avail id.	|WARNING	|Monetization	|
|Requesting manifest	|Occurs with each manifest request.	|DEBUG	|	|
|Found new segment	|Occurs when a new segment is found in the manifest. The log message includes details about the segment.	|DEBUG	|	|
|Found new period	|Occurs when a new period is found in a DASH manifest. The log message includes parsed SCTE-35 information if present.	|INFO	|	|
|Found avail in tracking response	|Occurs when monitoring a MediaTailor endpoint and an avail is found in the ad-tracking data during an ad break. An avail is found when calculated playhead during an ad break falls within the startTimeInSeconds to (startTimeInSeconds + durationInSeconds) range for any avail found in the ad-tracking data. The log message includes details about the avail (availId, durationInSeconds, availadscount, creatives)	|INFO	|	|

### Metrics

The script sends several metrics to CloudWatch when run with --cwmetrics argument. Metrics are created under *CanaryMonitor* custom namespace with 2 additional dimensions - *Endpoint* (endpoint identifier) ** and *Type* (*hls* or *dash)*.

| Metric Name	| Description | Value	|
|---	|---	|---	|
|discontinuity	|Occurs when EXT-X-DISCONTINUITY is found in HLS or when "t" of segment n+1 does not equal "t" + "d" of segment n in DASH. 	|	|
|manifest4xx	|Occurs with manifest 4xx HTTP response	|	|
|tracking4xx	|Occurs with ad-tracking 4xx HTT response	|	|
|segment4xx	|Occurs with segment 4xx HTTP response	|	|
|manifest5xx	|Occurs with manifest 5xx HTTP response	|	|
|tracking5xx	|Occurs with ad-tracking 5xx HTTP response	|	|
|segment5xx	|Occurs with segment 5xx HTTP response	|	|
|manifesttimeouterror	|Occurs with manifest request timeout or connection error	|	|
|trackingtimeouterror	|Occurs with ad-tracking requests timeout or connection error	|	|
|segmenttimeouterror	|Occurs with segment request timeout or connection error	|	|
|manifestresponsetime	|Manifest response time	|milliseconds	|
|trackingresponsetime	|Tracking response time	|milliseconds	|
|segmentresponsetime	|Segment response time	|milliseconds	|
|manifestsize	|Uncompressed manifest size	|bytes	|
|segmentsize	|Segment size	|bytes	|
|manifestduration	|Sum of all segment durations in manifest. For DASH emited every 5 minutes, for HLS after every manifest request.	|minutes	|
|stale	|Occurs when no new segment is found in the manifest response for x seconds, where x is the value defined by --stale argument.	|	|
|contentshortage	|Occurs when the sum of new content durations in the 2 most recent manifest requests is less than 25% of the expected new content duration in the time window. This metrics works only when manifest request frequency is more than the average segment duration.	|	|
|adbreak	|Occurs when EXT-X-CUE-OUT tag or EXT-X-DATERANGE + SCTE35-OUT tag is found in HLS. For DASH, a new period must occur, which contains a SpliceInfoSection and splice type is a) splice insert with outOfNetworkIndicator = True or b) time signal with segmentationTypeId == 34 (Break Start) or 48 (Provider Advertisement Start) or 50 (Distributor Advertisement Start) or 52 (Provider Placement Opportunity Start) or 54 (Distributor Placement Opportunity Start). For AWS MediaTailor endpoints occurs when replaced ad break content is detected.	|	|
|addurationadvertised	|Duration as advertised in EXT-X-CUE-OUT tag or EXT-X-DATERANGE + DURATION tag for HLS and duration as advertised in a new period, which contains SpliceInfoSection with BreakDuration or segmentationDuration for DASH.	|seconds	|
|addurationactual	|The sum of segment durations between ad break start and ad break end. Occurs when EXT-X-CUE-IN tag or EXT-X-DATERANGE + SCTE35-IN tag is found in HLS or a new period is found in DASH which signals an ad break end. For AWS MediaTailor endpoints, the sum of replaced segment durations during detected ad break.	|seconds	|
|addurationdelta	|Difference between actual and advertised ad break duration, measured as actual - advertised.	|seconds	|
|adavailnum	|Occurs only for DASH streams when a new period is found, which contains a SpliceInfoSection with availNum value.	|	|
|pdtdelta	|Difference between creation time of the last segment in manifest and current wall clock time. Published only when EXT-X-PROGRAM-DATE-TIME is present in HLS manifest and the metric represents the following calculation: EXT-X-PROGRAM-DATE-TIME of the newest segment + segment duration - current wall clock time. For DASH, this metric is calculated as SupplementalProperty of the newest period + (t - pto)/timescale of the newest segment + segment duration - current wall clock time.	|seconds	|
|ptsdelta	|The maximum difference between (t - pto)/timescale across all adaptations sets for all new segments in DASH.	|seconds	|
|inputbuffersize	|Size of a hypothetical input buffer in seconds, which starts at 60. Every time when the canary monitor downloads a manifest, it compares how many seconds of new segment content it found since start compared with time that elapsed since start and it adds the value to the initial buffer size of 60. Example: Value 75 would mean that the canary monitor received 15 seconds more content compared to the elapsed time since start.	|seconds	|

## Requirements

The canary monitor is a Python 3 script, which requires a system with Python environment that is of version 3.8 or newer. At minimum you will need the **urllib3** library, which is used for sending all HTTP requests. In addition you will need the following libraries based on your use case:

* **lxml** - for parsing DASH manifest responses
* **boto3** - for sending metrics to AWS Cloudwatch
* **botocore** - for sending metrics to AWS Cloudwatch
* **jinja2** - for creating an AWS Cloudwatch dashboard json file

See section “Sending metrics to AWS Cloudwatch” for additional requirements when sending metrics to AWS Cloudwatch.

## Running the Script

To start the script, you need to provide a single URL through --url argument or a list of URLs through the *endpoints.csv* file. The file must be in CSV format, where each line which is not a comment has 2 or 3 columns. The 1st column represents a unique identifier of your origin endpoint and it can be the same between HLS and DASH. It is used for identifying the endpoint in the logs and as a dimension for the metrics in CloudWatch. For HLS streams, a rendition identifier will be appended to the string, e.g *v1* for 1st video rendition or *a2* for 2nd audio rendition. The 2nd column represents the manifest URL. The 3rd column is optional and represents the tracking URL. See this example for an *endpoints.csv* file:

```
# endpoint_name,manifest_url,tracking_url
myliveevent-pdx,﻿https://mediapackage.us-west-2.amazonaws.com/pdx/live/clients/dash/enc/abc/out/v1/a/index.mpd﻿
myliveevent-pdx,﻿https://mediapackage.us-west-2.amazonaws.com/pdx/live/clients/hls/enc/abc/out/v1/a/index.m3u8﻿
myliveevent-iad,﻿https://mediapackage.us-west-2.amazonaws.com/pdx/live/clients/dash/enc/abc/out/v1/b/index.mpd﻿
myliveevent-iad,﻿https://mediapackage.us-west-2.amazonaws.com/pdx/live/clients/hls/enc/abc/out/v1/b/index.m3u8﻿
```

Example of single URL canary monitor start:
```
$ python3 canarymonitor.py --loglevel DEBUG --stdout --url ﻿https://mediapackage.us-west-2.amazonaws.com/pdx/live/clients/dash/enc/abc/out/v1/a/index.mpd
```

Example of multiple URLs canary monitor start for MediaTailor origins with manifest and tracking archiving and metrics to CloudWatch (assuming endpoints.csv file is in the local directory and it includes the MediaTailor playback and tracking URLs):
```
$ python3 canarymonitor.py --emt --emtadsegmentstring asset --manifests --tracking --gzip --cwmetrics --dashboards --loglevel INFO --frequency 5 --stale 18 --httptimeout 3 --label myliveevent_emt
```

Logs are by default written into _logs/monitor.log_ file in the local folder, but can be also printed on standard output with —stdout argument. Logs are of type debug, information, warning or error.

## Sending Metrics to CloudWatch

If you are running the script on an Amazon EC2 instance, you should have an IAM role with *cloudwatch:PutMetricData* permission assigned to the EC2 instance. Otherwise you should have an IAM user with *cloudwatch:PutMetricData* permission configured with *aws configure* command on the machine where you run the script.

The following script arguments are relevant when sending metrics to CloudWatch. 

|Argument	|Description	|
|---	|---	|
|--cwmetrics	|Tells the script to send metrics to CloudWatch	|
|--cwregion <region>	|Tells the script which AWS region to use for metrics, default "us-west-2"	|
|--dashboards	|Tells the script to create json dashboard file "cw_all.json" in the *dashboards* folder. You can copy paste the content of the json file when you edit or create a new dashboard under CloudWatch -> Dashboard → Actions -> View/edit source.	|

## Monitoring MediaTailor endpoints

If you are monitoring MediaTailor endpoints, your endpoint manifest URL should be the playback URL, which you get after initializing a session. You should also provide the tracking URL in the CSV file if you want to monitor for ad-tracking data. See the below script arguments, which are relevant when monitoring MediaTailor endpoints.

|Argument	|Description	|
|---	|---	|
|--emt	|Tells the script that the origin endpoints are from MediaTailor. That primarily means that the script will not use the ad break manifest decorations for detecting ad breaks, but will use a logic based on discontinuities and segment names.	|
|--emtadsegmentstring <string>	|Tells the script to use the provided string as an identifier for ad break segments. This should be a substring of an ad break segment name, which distinguishes an ad break segment from the main content segment.	|
|--trackingrequests	|Tells the script to send tracking requests.	|
|--tracking	|Tells the script to send tracking requests and save the responses.	|
|--checktrackingevents	|Tells the script to check if each ad in a new avail contains the following event types in the ad-tracking data - impression, start, firstQuartile, midpoint, thirdQuartile, complete	|

## Appendix 1 - How to Run Canary Monitor on an EC2 Instance

```
1. Create IAM role that you later attach to an EC2 instance with policy permissions to write to Cloudwatch
2. Create EC2 instance with attached role from step 1
3. Start EC2 instance and install required packages using the command line. Below examples are for latest Amazon Linux OS.
	sudo dnf install -y python3-pip
	pip3 install boto3 --user
	pip3 install lxml --user
	pip3 install jinja2 --user
4. Copy the canary monitor code to the EC2 instance
5. Start monitoring
```

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.