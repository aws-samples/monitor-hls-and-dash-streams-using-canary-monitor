> [!IMPORTANT]
> Version 3 released 3/20/25 contains braking changes. Make sure to install all dependencies, stop using arguments and use settings.yaml file instead. Syntax in the input CSV file has changed too! The update provides major improvements to HLS and DASH stream validations and management option through AWS CloudWatch dashboard. See more info in below sections.

## Monitor HLS and DASH Streams Using Canary Monitor

The canary monitor is a tool, which, like a player, downloads and inspects HLS or DASH manifests from a list of origins at regular intervals. It performs manifest and stream validations, writes logs and stores monitoring reports, sends metrics to AWS CloudWatch and creates CloudWatch dashboards. Optionally it can also download and inspect ad-tracking data for origins like AWS Elemental MediaTailor (EMT) where ad-tracking endpoints are available. It works with various origins, but has been primarily designed to monitor streams originating from AWS Elemental MediaPackage (EMP) and EMT. The recommended environement for running the canary monitor is an EC2 instance with Amazon Linux system. Both x86 and arm architectures are supported.

[Demo.webm](https://github.com/user-attachments/assets/bb01aa1c-52a0-42cb-b444-8da890070228)

## Requirements

Python 3.9 or newer with following libraries:

- lxml
- deepdiff
- threefive
- Jinja2
- boto3
- botocore
- urllib3
- isodate
- python-json-logger
- pyyaml

You can use `pip install -r requirements.txt` to install all required libraries at once.

## Initial Setup and User Input

**At minimum**, you should install all Python dependencies and review the settings in `settings.yaml` file. The default settings don't make the canary monitor send any data to AWS and so you are not required to have an AWS account to run the monitor. With `input_location: local` the script expects the user to provide one or more HLS or DASH live stream origin endpoints to monitor by creating or editing a CSV file (must have .csv extension) in the `origins` folder after starting the tool.

Default `settings.yaml`:

```
application:
  threads: false
  json_logger: false
  input_location: local

aws:
  region: 
  metrics: false
  dashboards: false
  bucket: 
  lambda:
    report: 
    logs: 
```

The syntax of the CSV file content in the `origins` folder is as follows:

```
# endpoint type (live), technology (hls/dash), workload name, endpoint name, origin name, is DAI (dynami ad insertion endpoint like AWS MediaTailor), monitoring config file name, manifest url, tracking url [optional]
live, dash, tnf25, feed01p1_pdx_1, emp, false, default.json, https://abcd.mediapackage.us-west-2.amazonaws.com/out/v1/abcd/cenc.mpd
```

**To get the most out of the canary monitor** - to run it as a service and manage monitoring from a CloudWatch dashboard, consider setting up the environment and AWS resources using `tools/configure-and-manage.py` script.

The `tools/configure-and-manage.py` script has these menu options:

```
1. Check Permissions
2. Setup Host System
3. Setup AWS Resources
4. Manage Updates
5. Exit
```

After cloning the repo you can run the tool on an EC2 instance and select menu options 1, 2 to help you set up the EC2 IAM role and the host system for running the canary monitor as service. You can clone the repo in AWS CloudShell and run the `tools/configure-and-manage.py` script to help you set up AWS S3, Lambda and CloudWatch resources using menu option 3.

As result, you will be able to create configurations, workloads and start or stop monitoring endpoints using the management dashboard:

<img width="2562" height="1440" alt="Screenshot from 2026-03-20 14-19-19" src="https://github.com/user-attachments/assets/bf1fc1ea-0313-49ca-93cb-078d36244985" />

### Notes on Input

An origin endpoint is identified by values in the first 6 columns. Each line should have a unique endpoint identifier. VOD endpoints are currently not supported, therefore endpoint type should always be `live`. AWS Elemental MediaTailor origin endpoints should have DAI flag set to `true` and the tracking URL filled, as ad breaks detection and validation on such endpoints is done by using the tracking data.

### Notes on Configuration

Users should create their own monitoring config files based on the default config `configs/default.json` file to match their monitoring requirements. Available HLS rendition identifiers in the config file are `"video", "audio", "subtitles", "*"`.

Default configuration settings:

```
{
  "cwmetrics": true,
  "loglevel": "debug",
  "manifests": {
    "frequency": 5.0,
    "save": {
      "s3": false,
      "local": false
    },
    "hls_renditions": [ "video", "audio" ],
    "ad_segment_prefix": "asset"
  },
  "tracking": {
    "frequency": 6.0,
    "get": true,
    "save": {
      "s3": false,
      "local": false
    },
    "playhead": false,
    "playhead_delay": 10
  },
  "reports": {
    "frequency": 60,
    "save": {
      "s3": true
    }
  },
  "validations": {
    "perform": true,
    "custom": {
      "check_multivariant_change": true,
      "required_renditions": [ "video", "audio" ],
      "ad_break_scte_signals": [ "splice_insert", 48, 50, 52, 54, 56 ],
      "required_tracking_events": [ "impression", "start", "firstQuartile", "midpoint", "thirdQuartile", "complete" ],
      "check_ad_break_scte_duration": true,
      "check_ad_break_start_time": true,
      "max_ad_break_duration_delta": 0.5,
      "max_pts_delta": 0.1,
      "max_segment_availability_delta": {
        "in_past": 15,
        "in_future": 5
      }
    }
  }
}
```

## Dynamic Handling of Changes

The canary monitor picks changes in the input CSV files and in the monitoring config files. That means that the monitoring of individual endpoints is started, stopped or updated based on the changes in the `origins` folder CSV files and monintoring parameters are updated based on changes in the config files. Therefore, a user can add or remove origin endpoints and update the endpoint monitoring configuration at any time without stopping and starting the canary monitor itself. With that the canary monitor can run as a system service.

## CloudWatch Metrics and Dashboards

The tool sends metrics to CloudWatch for an endpoint if the endpoint is configured with `"cwmetrics": true` setting in the config file. If a user runs the script on an Amazon EC2 instance, they should have an IAM role with `cloudwatch:PutMetricData` permission assigned to the EC2 instance. Otherwise, they should have an IAM user with `cloudwatch:PutMetricData` permission configured with `aws configure` command on the machine where they run the script. User can control the AWS region for publishing metrics by `-r` or `--region` argument at start.

The canary monitor automatically creates or updates CloudWatch dashboards anytime a change is detected in the list of monitored endpoints. The tool groups the monitored endpoints by workload and origin name when creating the dasbhoards, meaning endpoints with the same workload and origin name are part of the same dashboard. Dashboards include only relevant metrics based on the values in the monitoring config file.

The dashboard includes a custom widget which calls an AWS Lambda function to create a table with additional information about health of each endpoint.

### CloudWatch Metrics

Common dimensions for all metrics are `Type`, `Technology`, `Workload`, `Endpoint` and `Origin` which identify each endpoint.

| Domain    | Metric Name              | Additional Metric Dimensions   | Description                                                                                                                                                                                         |
|-----------|--------------------------|--------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Manifests | Discontinuity            | Rendition                      | Discontinuity in segments timeline                                                                                                                                                                  |
| Manifests | BufferFillDuration       | Rendition                      | Sum of new segment durations in a rolling 20 seconds time window                                                                                                                                    |
| Manifests | Latency                  | RequestType, Rendition         | HTTP request latency in milliseconds                                                                                                                                                                |
| Manifests | Request                  | RequestType, Rendition, Status | HTTP request response with "Status" dimension one of "4xx", "5xx" or "failure"                                                                                                                      |
| Manifests | PdtDelta                 |                                | Only for HLS. Difference between program date time of the last segment and current wall clock time. Published for HLS when EXT-X-PROGRAM-DATE-TIME is present.                                      |
| Manifests | PtsDelta                 |                                | Only for DASH. The maximum difference between (t + d - pto)/timescale of last segments in the last period across all segment templates.                                                             |
| Manifests | ManifestDuration         | Rendition                      | Manifest duration in seconds                                                                                                                                                                        |
| Segments  | SegmentDuration          | Rendition                      | Segment duration in seconds                                                                                                                                                                         |
| Segments  | SegmentAvailabilityDelta |                                | Only for DASH. Difference between segment availability of the last segment computed as availabilityStartTime + period start + (t – presentationTimeOffset) / timescale and current wall clodk time. | 
| Tracking  | Latency                  | RequestType                    | HTTP request latency in milliseconds                                                                                                                                                                |
| Tracking  | Request                  | RequestType, Status            | HTTP request response with "Status" dimension one of "4xx", "5xx" or "failure"                                                                                                                      |
| Ad breaks | Start                    | AdBreakType                    | Start of ad break with "AdBreakType" dimension one of "regular" or "overlay"                                                                                                                        |
| Ad breaks | AdvertisedDuration       | AdBreakType                    | Ad break SCTE duration in seconds with "AdBreakType" dimension one of "regular" or "overlay"                                                                                                        |
| Ad breaks | SegmentsDuration         | AdBreakType                    | Ad break segments duration sum in seconds with "AdBreakType" dimension one of "regular" or "overlay"                                                                                                |
| Ad breaks | DurationDelta            | AdBreakType                    | Duration delta between advertised ad break duration and sum of ad break segments with "AdBreakType" dimension one of "regular" or "overlay"                                                         |
| Ad breaks | AvailNum                 | AdBreakType                    | Only for DASH. Ad break avail num from SCTE splice insert message with "AdBreakType" dimension one of "regular" or "overlay"                                                                        |

Example CloudWatch dashboard dynamically created by the canary monitor tool:

<img width="2568" height="1242" alt="Screenshot from 2026-03-20 14-16-51" src="https://github.com/user-attachments/assets/7d8432d6-ffab-4aa9-8b35-f67aa81b60ba" />


## Logging and Validations

The canary monitor logs any manifest compliance, parsing and validation issues that it encounters after each manifest request. Some validations can be controlled by changing configuration in the monitoring config file.

You can enable JSON format logging by providing `-jl` argument at start. You can control logging by changing settings in `loggingconfig.json` file. By default, no logs get passed to the console and all logs are stored in the `logs` folder in `service.log` and `monitor.log` files. You can control logging level on per endpoint basis by changing the `loglevel` setting in the monitoring config file. Available logging levels are `debug`, `info`, `warning`, `error`, `critical`.

Key log events include the following warnings and errors (the code name is included in the logs only when logging in JSON format):

| Impact      | Event Code Name                      | Description                                                                                                                                                                                                                                                                                            |
|-------------|--------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Playback    | MANIFEST_PARSE_ERROR                 | Occurs when the canary monitor encounters an issue parsing the manifest.                                                                                                                                                                                                                               |
| Playback    | NON_COMPLIANT_MANIFEST               | Occurs when the canary monitor encounters a non-comppliance in an HLS or DASH manifest.                                                                                                                                                                                                                |
| Playback    | STALE_MANIFEST                       | Occurs when manifest contains no new segments in last 20 seconds.                                                                                                                                                                                                                                      |
| Playback    | LIP_SYNC                             | Occurs when the last manifest segments across all renditions have PTS values which differ by more than what is configured in "max_pts_delta" in the config file.                                                                                                                                       |
| Playback    | LAST_SEGMENT_NOT_FOUND               | Occurs when last known segment identified by media sequence number in HLS and period id and t value in DASH is not found in the most recent manifest, e.g. the manifest goes backwards.                                                                                                                |
| Playback    | LAST_SEGMENT_CHANGED                 | Occurs when last known segment identified by media sequence number in HLS and period id and t value in DASH from previous manifest request is found, but name of the segment has changed.                                                                                                              |
| Playback    | DISCONTINUITY                        | Occurs when EXT-X-DISCONTINUITY is found in an HLS manifest. Occurs when "t" value of segment n + 1 does not equal "t" + "d" value of segment n.                                                                                                                                                       |
| Playback    | MULTIVARIANT_MANIFEST_CHANGED        | Occurs when HLS multivariant manifest changed and origin is not DAI.                                                                                                                                                                                                                                   |
| Playback    | INCONSISTENT_MANIFEST_PERIODS        | Occurs when DASH manifest doesn't contain all and exactly the same periods in the same order from previous manifest request other than periods which rolled over.                                                                                                                                      |
| Playback    | DUPLICATE_PERIOD                     | Occurs when DASH manifest contains more than one period with the same id.                                                                                                                                                                                                                              |
| Playback    | DIFFERENT_SEGMENT_TEMPLATES          | Occurs when number of segments across segment templates is different or n number of last segment across segment templates is different.                                                                                                                                                                | 
| Playback    | AVAILABILITY_START_TIME_NOT_FOUND    | Occurs when availabilityStartTime is missing in DASH manifest.                                                                                                                                                                                                                                         |
| Playback    | ORIGIN_ACTIVE_INPUT_CHANGED          | Occurs when AWS MediaPackage active input changed from one pipeline to another based on "X-Amzn-Mediapackage-Active-Input" header values.                                                                                                                                                              |
| Playback    | ORIGIN_ENDPOINT_CHANGED              | Occurs when AWS MediaPackage endpoint changed as result of CDN origin failover based on "CMSD-Static" header values.                                                                                                                                                                                   |     
| Playback    | SEGMENT_AVAILABILITY_DELTA           | Occurs when a DASH segment availability time computed as availabilityStartTime + period start + (t – presentationTimeOffset) / timescale on any new segment is more than "max_future_segment_availability" seconds in the future when compared with the wall clock time of when manifest was received. |
| Playback    | RENDITION_NOT_FOUND                  | Occurs when a rendition listed in "required_renditions" is missing.                                                                                                                                                                                                                                    |
| Playback    | MULTIPLE_VIDEO_ADAPTATION_SETS       | Occurs when a DASH period has multiple video adaptation sets.                                                                                                                                                                                                                                          |
| Playback    | NON_LIVE_MANIFEST                    | Occurs when a DASH manifest type is not "dynamic" or HLS manfiest is VOD.                                                                                                                                                                                                                              |
| Advertising | BACK_TO_BACK_AD_BREAK                | Occurs when a new ad break starts while another ad break is in progress.                                                                                                                                                                                                                               |
| Advertising | MULTIPLE_SEGMENTATION_DESCRIPTORS    | Occurs when manifest ad break decoration contains multiple segmentation descriptors, which can lead to a failure to detect an ad break opportunity.                                                                                                                                                    |
| Advertising | AD_BREAK_DURATION_DELTA_BREACHED     | Occurs when the sum of segment durations between ad break start and end does not match the advertised ad break duration +- value in "max_ad_break_duration_delta" in seconds. This can happen when an ad break is cut short early or when the manifest ad break decorations are incorrect.             |
| Advertising | AD_BREAK_DURATION_NOT_FOUND          | Occurs when an ad break is advertised without duration and "check_ad_break_scte_duration" is set.                                                                                                                                                                                                      |
| Advertising | MULTIPLE_AD_BREAK_OPPORTUNITY_EVENTS | Occurs when a period in DASH manifest has more than one ad break opportunity start event in the EventStream.                                                                                                                                                                                           |
| Advertising | MISSING_REQUIRED_TRACKING_EVENTS     | Occurs when an ad in the tracking data doesn't contain all required tracking events listed in "required_tracking_events" config validation list.                                                                                                                                                       | 
| Advertising | AD_BREAK_START_TIME_IN_PAST          | Occurs when requesting tracking data with playehad and the avail start time for a new ad break in the tracking data is in the past when compared to the current playhead                                                                                                                               | 


## Reporting

Important information about each monitored endpoint (e.g. ad break info) is stored at regular intervals into `report.json` JSON file in the `archive` folder for each endpoint. Below is an example of a report file for a DASH endpoint, which includes ad breaks and periods information.

The tool can store report files automatically in an AWS S3 bucket if the script is started with a provided bucket name using`-b` option and when configuration JSON contains `"s3": true` in the "reports" section. When reports are saved to CloudWatch, the auto created CloudWatch dashboard includes a custom widget, which can contain analysed report data by an AWS Lambda function. If you want to have the reports analysed in the dashboard, you need to store the provided 2 AWS Lambda functions in the `lambda` folder to your AWS account and start the script with `-l` argument which takes as argument the arn pointing to the `canary-monitor-report-analyser` AWS Lambda function. Both AWS Lambda functions require an IAM role with access to the S3 bucket where reports are getting saved.

Example of data captured in a report file.

```
"ad_breaks": {
      "443366461": {
        "observed": 2026-03-14 05:30:06.237362+00:00,
        "scte_message": {
          "raw": "0xFC305E00000000000000FFF01405000000037FEFFE8C21EAD8FE00A4CB80000103010039023743554549000000017FCF0000A4CB800C2141424344617373657449643A636B616C64742D4550303135333331383130313931340000000035C8D9F7",
          "decoded": {
            "type": "splice_insert",
            "out_of_network": true,
            "splice_event_id": 3,
            "splice_immediate": false,
            "auto_return": true,
            "duration": 120.0,
            "avail_num": 3,
            "descriptors": [
              {
                "segmentation_type": 52,
                "segmentation_message": "Provider Placement Opportunity Start",
                "duration": 120.0,
                "upid_private_data": "assetId:ckaldt-EP015331810191"
              }
            ]
          }
        },
        "advertised_duration": 120.0,
        "segments_duration": 37.8,
        "daterange_id": "1773465840966-34-1",
        "is_opportunity": true,
        "type": "regular",
        "duration_delta": -82.2
      }
```

## Starting and Stopping

You can start the canary monitor with `python3 canarymonitor.py` after you confirmed settings in `settings.yaml` file. After that you can update existing CSV files or create new CSV files in the `origins` folder with each endpoint represented by one line in the CSV file. You should use `ctrl+c` or `kill -2 PID` to stop the canary monitor where PID is the process number as logged on each line in the `logs/service.log` log file.

Use the `tools/configure-and-manage.py` and `Setup Host System` menu option to configure canary monitor as service.

## License

Licensed under the MIT-0 License. See the LICENSE file.
