import json

def lambda_handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    # Failure codes from README
    failure_codes = [
        {
            "code": "MANIFEST_PARSE_ERROR",
            "description": "Occurs when the canary monitor encounters an issue parsing the manifest."
        },
        {
            "code": "NON_COMPLIANT_MANIFEST",
            "description": "Occurs when the canary monitor encounters a non-comppliance in an HLS or DASH manifest."
        },
        {
            "code": "STALE_MANIFEST",
            "description": "Occurs when manifest contains no new segments in last 20 seconds."
        },
        {
            "code": "LIP_SYNC",
            "description": "Occurs when the last manifest segments across all renditions have PTS values which differ by more than what is configured in \"max_pts_delta\" in the config file."
        },
        {
            "code": "LAST_SEGMENT_NOT_FOUND",
            "description": "Occurs when last known segment identified by media sequence number in HLS and period id and t value in DASH is not found in the most recent manifest, e.g. the manifest goes backwards."
        },
        {
            "code": "LAST_SEGMENT_CHANGED",
            "description": "Occurs when last known segment identified by media sequence number in HLS and period id and t value in DASH from previous manifest request is found, but name of the segment has changed."
        },
        {
            "code": "DISCONTINUITY",
            "description": "Occurs when EXT-X-DISCONTINUITY is found in an HLS manifest. Occurs when \"t\" value of segment n + 1 does not equal \"t\" + \"d\" value of segment n."
        },
        {
            "code": "MULTIVARIANT_MANIFEST_CHANGED",
            "description": "Occurs when HLS multivariant manifest changed and origin is not DAI."
        },
        {
            "code": "INCONSISTENT_MANIFEST_PERIODS",
            "description": "Occurs when DASH manifest doesn't contain all and exactly the same periods in the same order from previous manifest request other than periods which rolled over."
        },
        {
            "code": "AVAILABILITY_START_TIME_NOT_FOUND",
            "description": "Occurs when availabilityStartTime is missing in DASH manifest."
        },
        {
            "code": "ORIGIN_ACTIVE_INPUT_CHANGED",
            "description": "Occurs when AWS MediaPackage active input changed from one pipeline to another based on \"X-Amzn-Mediapackage-Active-Input\" header values."
        },
        {
            "code": "ORIGIN_ENDPOINT_CHANGED",
            "description": "Occurs when AWS MediaPackage endpoint changed as result of CDN origin failover based on \"CMSD-Static\" header values."
        },
        {
            "code": "SEGMENT_AVAILABILITY_IN_FUTURE",
            "description": "Occurs when a DASH segment availability time computed as availabilityStartTime + period start + (t – presentationTimeOffset) / timescale on any new segment is more than \"max_future_segment_availability\" seconds in the future when compared with the wall clock time of when manifest was received."
        },
        {
            "code": "RENDITION_NOT_FOUND",
            "description": "Occurs when a rendition listed in \"required_renditions\" is missing."
        },
        {
            "code": "MULTIPLE_VIDEO_ADAPTATION_SETS",
            "description": "Occurs when a DASH period has multiple video adaptation sets."
        },
        {
            "code": "NON_LIVE_MANIFEST",
            "description": "Occurs when a DASH manifest type is not \"dynamic\" or HLS manfiest is VOD."
        },
        {
            "code": "BACK_TO_BACK_AD_BREAK",
            "description": "Occurs when a new ad break starts while another ad break is in progress."
        },
        {
            "code": "MULTIPLE_SEGMENTATION_DESCRIPTORS",
            "description": "Occurs when manifest ad break decoration contains multiple segmentation descriptors, which can lead to a failure to detect an ad break opportunity."
        },
        {
            "code": "AD_BREAK_DURATION_DELTA_BREACHED",
            "description": "Occurs when the sum of segment durations between ad break start and end does not match the advertised ad break duration +- value in \"max_ad_break_duration_delta\" in seconds. This can happen when an ad break is cut short early or when the manifest ad break decorations are incorrect."
        },
        {
            "code": "AD_BREAK_DURATION_NOT_FOUND",
            "description": "Occurs when an ad break is advertised without duration and \"check_ad_break_scte_duration\" is set."
        },
        {
            "code": "MULTIPLE_AD_BREAK_OPPORTUNITY_EVENTS",
            "description": "Occurs when a period in DASH manifest has more than one ad break opportunity start event in the EventStream"
        }
    ]
    
    # Build table rows
    rows = []
    for idx, failure in enumerate(failure_codes, 1):
        rows.append(f'''
        <tr>
            <td style="text-align: center;">{idx}</td>
            <td style="font-family: monospace;">{failure['code']}</td>
            <td>{failure['description']}</td>
        </tr>''')
    
    html = f'''<html>
<head>
    <title>Code Descriptions</title>
    <style>
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 10px;
            text-align: left;
        }}
        th {{
            background-color: #f2f2f2;
            font-weight: bold;
        }}
        tr:hover {{
            background-color: #f5f5f5;
        }}
    </style>
</head>
<body style="margin: 20px;">
    <p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Code Descriptions</p>
    <table>
        <tr>
            <th style="width: 50px;">ID</th>
            <th style="width: 300px;">Code Name</th>
            <th>Description</th>
        </tr>
        {''.join(rows)}
    </table>
</body>
</html>'''
    
    return html
