## Monitor HLS and DASH Streams Using Canary Monitor

**The current version 2 introduces operational and functional improvements. Ad break detection is currently not supported for HLS endpoints.** 

The canary monitor is a tool, which, like a player, downloads and inspects HLS or DASH manifests from a list of origins at regular intervals. It performs manifest and stream validations, writes logs and stores monitoring reports, sends metrics to AWS CloudWatch and creates CloudWatch dashboards. Optionally it can also download and inspect ad-tracking data for origins like AWS Elemental MediaTailor (EMT) where ad-tracking endpoints are available. It works with various origins, but has been primarily designed to monitor streams originating from AWS Elemental MediaPackage (EMP) and EMT.

[Demo.webm](https://github.com/user-attachments/assets/bb01aa1c-52a0-42cb-b444-8da890070228)

## Requirements

Python 3.9 or newer with following libraries:

- lxml
- deepdiff
- m3u8
- threefive
- Jinja2
- boto3
- botocore
- urllib3

You can use `pip install -r requirements.txt` to install all required libraries at once.

## User Input

The script expects the user to provide one or more HLS or DASH live stream origin endpoints to monitor. User can do so by creating or editing a CSV file in the `origins` folder after starting the tool.

The syntax of the provided CSV file content in the `origins` folder is as follows:

```
# endpoint type (live), technology (hls/dash), workload name, endpoint name, origin name, monitoring config file, manifest url, tracking url [optional]
live, dash, tnf25, feed01p1_pdx_1, emp, configs/default.json, https://abcd.mediapackage.us-west-2.amazonaws.com/out/v1/abcd/cenc.mpd
```

### Notes

An origin endpoint is identified by values in the first 5 columns. Each line should have a unique endpoint identifier. VOD endpoints are currently not supported, therefore endpoint type should always be `live`. EMT origin endpoints should have origin name set to `emt` for correct ad break detection. Users should create their own monitoring config files based on the default config `configs/default.json` file to match their monitoring requirements. Available HLS rendition identifiers in the config file are `v, a, s, *`, meaning the tool can monitor one or multiple video, audio or subtitle renditions.

## Dynamic Handling of Changes

The canary monitor picks changes in the input CSV files and in the monitoring config files. That means that the monitoring of individual endpoints is started, stopped or updated based on the changes in the `origins` folder CSV files and monintoring parameters are updated based on changes in the config files. Therefore, a user can add or remove origin endpoints and update the endpoint monitoring configuration at any time without stopping and starting the canary monitor itself. With that the canary monitor can run as a service.

## CloudWatch Metrics and Dashboards

To prevent the canary monitor from using AWS resources, use `-na` or `--no-aws` argument at start.

The tool sends metrics to CloudWatch for an endpoint if the endpoint is configured with `"cwmetrics": true` setting in the config file. If a user runs the script on an Amazon EC2 instance, they should have an IAM role with `cloudwatch:PutMetricData` permission assigned to the EC2 instance. Otherwise, they should have an IAM user with `cloudwatch:PutMetricData` permission configured with `aws configure` command on the machine where they run the script. User can control the AWS region for publishing metrics by `-r` or `--region` argument at start.

The canary monitor automatically creates or updates CloudWatch dashboards anytime a change is detected in the list of monitored endpoints. The tool groups the monitored endpoints by workload and origin name when creating the dasbhoards, meaning endpoints with the same workload and origin name are part of the same dashboard. Dashboards include only relevant metrics based on the values in the monitoring config file.

### CloudWatch Metrics

Common dimensions for all metrics are `Type`, `Technology`, `Workload`, `Endpoint` and `Origin` which identify each endpoint.

| Domain    | Metric Name        | Additional Metric Dimensions   | Description                                                                                                                                 |
|-----------|--------------------|--------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|
| Manifests | Discontinuity      | Rendition                      | Discontinuity in segments timeline                                                                                                          |
| Manifests | BufferFillDuration | Rendition                      | Sum of new segment durations in a rolling 20 seconds time window                                                                            |
| Manifests | Latency            | RequestType, Rendition         | HTTP request latency in milliseconds                                                                                                        |
| Manifests | Request            | RequestType, Rendition, Status | HTTP request response with "Status" dimension one of "4xx", "5xx" or "failure"                                                              |
| Tracking  | Latency            | RequestType                    | HTTP request latency in milliseconds                                                                                                        |
| Tracking  | Request            | RequestType, Status            | HTTP request response with "Status" dimension one of "4xx", "5xx" or "failure"                                                              |
| Ad breaks | Start              | AdBreakType                    | Start of ad break with "AdBreakType" dimension one of "regular" or "overlay"                                                                |
| Ad breaks | AdvertisedDuration | AdBreakType                    | Ad break SCTE duration in seconds with "AdBreakType" dimension one of "regular" or "overlay"                                                |
| Ad breaks | SegmentsDuration   | AdBreakType                    | Ad break segments duration sum in seconds with "AdBreakType" dimension one of "regular" or "overlay"                                        |
| Ad breaks | DurationDelta      | AdBreakType                    | Duration delta between advertised ad break duration and sum of ad break segments with "AdBreakType" dimension one of "regular" or "overlay" |
| Ad breaks | AvailNum           | AdBreakType                    | Ad break avail num from SCTE splice insert message with "AdBreakType" dimension one of "regular" or "overlay"                               |

## Reporting

Important information about each monitored endpoint (e.g. ad break info) is stored at regular intervals into a JSON file in the `archive` folder. Here is an example of a report file for a DASH endpoint, which includes ad breaks and periods information:

```
{
  "adbreaks": {
    "4281068": {
      "observed": "2025-07-30 19:11:58.925605+00:00",
      "advertisedduration": 30.0,
      "segmentsduration": 30.0,
      "type": "regular",
      "durationdelta": 0.0
    }
  },
  "periods": {
    "4279875": {
      "observed": null,
      "compact": true,
      "isadbreak": false,
      "spliceinfo": []
    },
    "4281068": {
      "observed": "2025-07-30 19:11:58.925605+00:00",
      "compact": true,
      "isadbreak": true,
      "spliceinfo": [
        {
          "type": "spliceinsert",
          "outofnetwork": true,
          "availnum": 1,
          "duration": 30.0,
          "descriptors": [
            {
              "segmentationtype": 52,
              "segmentationmessage": "Provider Placement Opportunity Start",
              "duration": 30.0
            }
          ]
        }
      ]
    },
    "4281083": {
      "observed": "2025-07-30 19:12:29.135682+00:00",
      "compact": true,
      "isadbreak": false,
      "spliceinfo": []
    }
  }
}
```

## Logging

Users can control logging by changing settings in `loggingconfig.json` file. By default, no logs get passed to the console and all logs are stored in the `logs` folder in `service.log` and `monitor.log` files. Users can control logging level on per endpoint basis by changing the `loglevel` setting in the monitoring config file. Available logging levels are `debug`, `info`, `warning`, `error`, `critical`.

## Starting and Stopping

The canary monitor supports the following arguments at start:

```
$ ./canarymonitor.py -h
usage: canarymonitor.py [-h] [-t] [-r REGION] [-nad]

options:
  -h, --help            show this help message and exit
  -t, --threads         use threads instead of processes
  -na, --no-aws         do not use AWS
  -r REGION, --region REGION
                        AWS region to use, default: us-west-2
```

Users should use `ctrl+c` or `kill -2 PID` to stop the canary monitor where PID is the process number as logged on each line in the `logs/service.log` log file.

### Running as a Service

Below are instructions for running the canary monitor as a system service on an EC2 instance with Amazon Linux. The steps assume that the user sshed to the instance and cloned the repo into `/home/ec2-user/` home folder. At the end of the steps, user can start the tool with `sudo systemctl start canarymonitor` and stop with `sudo systemctl stop canarymonitor`.

```
1. sudo touch /etc/systemd/system/canarymonitor.service
2. Edit /etc/systemd/system/canarymonitor.service to contain the following

[Unit]
Description=Canary Monitor for HLS and DASH streams
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/monitor-hls-and-dash-streams-using-canary-monitor/
ExecStart=/usr/bin/python3 /home/ec2-user/monitor-hls-and-dash-streams-using-canary-monitor/canarymonitor.py
Restart=no

[Install]
WantedBy=multi-user.target

3. sudo systemctl daemon-reload
4. sudo systemctl start canarymonitor

To enable start of canary monitor at boot: 

5. sudo systemctl enable canarymonitor.service
```

## License

Licensed under the MIT-0 License. See the LICENSE file.
