import boto3
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

def get_lambda_arn(context, function_name):
    """Build Lambda ARN dynamically from context"""
    arn_parts = context.invoked_function_arn.split(':')
    region = arn_parts[3]
    account = arn_parts[4]
    return f"arn:aws:lambda:{region}:{account}:function:{function_name}"

def lambda_handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    failure_codes_arn = get_lambda_arn(context, 'canary-monitor-error-codes')
    widget_context = event.get('widgetContext', {})
    endpoints = widget_context.get('params', {}).get('endpoints', [])
    params = widget_context.get('params', {})
    
    # Get time range from widget context
    time_range = widget_context.get('timeRange', {})
    start_time = time_range.get('start')
    end_time = time_range.get('end')
    
    # Sort endpoints by endpoint name, then by technology
    sorted_endpoints = sorted(endpoints, key=lambda x: (x.get('endpoint', ''), x.get('technology', '')))
    
    # Configure clients
    s3_client = boto3.client('s3')
    cloudwatch_client = boto3.client('cloudwatch')
    logs_client = boto3.client('logs')
    
    # Run CloudWatch and Logs checks in parallel
    import time
    start_total = time.time()
    
    with ThreadPoolExecutor(max_workers=2) as executor:
        # Submit all tasks with timing
        start_metrics = time.time()
        metrics_future = executor.submit(get_ad_break_metrics_optimized, cloudwatch_client, sorted_endpoints, params, start_time, end_time)
        
        start_events = time.time()
        events_future = executor.submit(get_log_events, logs_client, sorted_endpoints, params, start_time, end_time)
        
        # Get results with timing
        regular_counts, overlay_counts = metrics_future.result()
        end_metrics = time.time()
        
        events_dict = events_future.result()
        end_events = time.time()
    
    end_total = time.time()
    print(f"Thread timings - Metrics: {end_metrics - start_metrics:.2f}s, Events: {end_events - start_events:.2f}s, Total: {end_total - start_total:.2f}s")
    
    # Check if logs query failed
    logs_query_failed = events_dict is None
    
    # Load all reports in parallel
    def load_report_info(ep):
        """Load info from report.json for an endpoint"""
        report_location = ep.get('report', {}).get('location', '')
        if not report_location:
            return ([] if ep.get('technology', '').lower() == 'dash' else {}, 0)
        
        try:
            # Parse S3 path
            if report_location.startswith('s3://'):
                report_location = report_location[5:]
            bucket, key = report_location.split('/', 1)
            
            # Load report from S3
            obj = s3_client.get_object(Bucket=bucket, Key=key)
            report_data = json.loads(obj['Body'].read().decode('utf-8'))
            
            # Initialize defaults
            renditions = [] if ep.get('technology', '').lower() == 'dash' else {}
            periods_count = 0
            
            # Get latest epoch (highest key)
            if report_data:
                latest_epoch = max(report_data.keys(), key=int)
                latest_report = report_data[latest_epoch]
                
                # For HLS, renditions are at top level
                # For DASH, we need to get adaptation sets from the latest period
                if ep.get('technology', '').lower() == 'dash':
                    periods_list = latest_report.get('periods', [])
                    if periods_list:
                        # Get the last period (most recent)
                        last_period_entry = periods_list[-1]
                        # Get the first (and should be only) period dict in the entry
                        for period_id, period_data in last_period_entry.items():
                            renditions = period_data.get('adaptation_sets', [])
                            break
                    else:
                        renditions = []
                else:
                    renditions = latest_report.get('renditions', {})
                
                # Count periods within time window for DASH
                if ep.get('technology', '').lower() == 'dash':
                    # Convert start_time and end_time to datetime for comparison
                    if isinstance(start_time, (int, float)):
                        start_dt = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc)
                    elif isinstance(start_time, str):
                        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                    else:
                        start_dt = start_time
                        
                    if isinstance(end_time, (int, float)):
                        end_dt = datetime.fromtimestamp(end_time / 1000, tz=timezone.utc)
                    elif isinstance(end_time, str):
                        end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
                    else:
                        end_dt = end_time
                    
                    # Iterate through all epochs to count periods
                    for epoch_key, epoch_data in report_data.items():
                        periods_list = epoch_data.get('periods', [])
                        for period_entry in periods_list:
                            for period_id, period_data in period_entry.items():
                                observed = period_data.get('observed')
                                if observed:
                                    try:
                                        observed_dt = datetime.fromisoformat(observed.replace('Z', '+00:00'))
                                        if start_dt <= observed_dt <= end_dt:
                                            periods_count += 1
                                    except Exception as ex:
                                        print(f"Error parsing observed time {observed}: {ex}")
                                    pass
                
                return (renditions, periods_count)
        except Exception as e:
            print(f"Error loading report for {ep['endpoint']}: {e}")
        
        return ([] if ep.get('technology', '').lower() == 'dash' else {}, 0)
    
    # Load all reports in parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        report_info_list = list(executor.map(load_report_info, sorted_endpoints))
    
    # Build HTML table
    rows = []
    has_dash = any(ep.get('technology', '').lower() == 'dash' for ep in sorted_endpoints)
    
    for i, ep in enumerate(sorted_endpoints):
        # Get info from parallel load
        renditions, periods_count = report_info_list[i]
        report_location = ep.get('report', {}).get('location', '')
        # Ad breaks column (regular / overlay) - clickable
        regular_count = regular_counts.get(i, 'n/a')
        overlay_count = overlay_counts.get(i, 'n/a')
        ad_breaks_arn = get_lambda_arn(context, 'canary-monitor-ad-breaks')
        ad_breaks_cell = f'''<td><b style="cursor: pointer;">{regular_count} / {overlay_count}</b>
<cwdb-action action="call" display="popup" endpoint="{ad_breaks_arn}">
{{"s3_path": "{report_location}"}}
</cwdb-action></td>'''
        
        # Renditions column (video / audio / subtitles) - clickable
        if isinstance(renditions, dict):
            # HLS format
            video_count = len(renditions.get('video', {}))
            audio_count = len(renditions.get('audio', {}))
            subtitles_count = len(renditions.get('subtitles', {}))
        elif isinstance(renditions, list):
            # DASH format (list of adaptation sets)
            video_count = sum(len(a.get('representations', [])) for a in renditions if 'video' in a.get('mime_type', ''))
            audio_count = sum(len(a.get('representations', [])) for a in renditions if 'audio' in a.get('mime_type', ''))
            subtitles_count = sum(len(a.get('representations', [])) for a in renditions if 'application' in a.get('mime_type', ''))
        else:
            video_count = 0
            audio_count = 0
            subtitles_count = 0
        renditions_arn = get_lambda_arn(context, 'canary-monitor-renditions')
        renditions_cell = f'''<td><b style="cursor: pointer;">{video_count} / {audio_count} / {subtitles_count}</b>
<cwdb-action action="call" display="popup" endpoint="{renditions_arn}">
{{"s3_path": "{report_location}", "technology": "{ep.get('technology', '')}", "workload": "{params.get('workload', '')}", "origin": "{params.get('origin', '')}", "endpoint": "{ep.get('endpoint', '')}"}}
</cwdb-action></td>'''
        
        # Periods column (only if DASH endpoints exist)
        if has_dash:
            if ep.get('technology', '').lower() == 'dash':
                # Convert timestamps to ISO format for Lambda parameter
                if isinstance(start_time, (int, float)):
                    start_time_iso = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc).isoformat()
                elif isinstance(start_time, str):
                    start_time_iso = start_time
                else:
                    start_time_iso = start_time.isoformat()
                    
                if isinstance(end_time, (int, float)):
                    end_time_iso = datetime.fromtimestamp(end_time / 1000, tz=timezone.utc).isoformat()
                elif isinstance(end_time, str):
                    end_time_iso = end_time
                else:
                    end_time_iso = end_time.isoformat()
                
                periods_arn = get_lambda_arn(context, 'canary-monitor-periods')
                periods_cell = f'''<td><b style="cursor: pointer;">{periods_count}</b>
<cwdb-action action="call" display="popup" endpoint="{periods_arn}">
{{"s3_path": "{report_location}", "start_time": "{start_time_iso}", "end_time": "{end_time_iso}"}}
</cwdb-action></td>'''
            else:
                periods_cell = '<td></td>'
        
        # Events column
        events_key = f"{ep['endpoint']}_{ep.get('technology', '')}"
        events = events_dict.get(events_key, {}) if events_dict else {}
        events_list = [f"{event} (count: {data['count']}, last: {data['last_occurrence']})" for event, data in sorted(events.items())]
        events_cell = f'<td>{"<br>".join(events_list)}</td>'
        
        # Status column
        if events:
            status_cell = '<td style="text-align: center;">⚠️</td>'
        else:
            status_cell = '<td style="text-align: center;">✅</td>'
        
        # Capitalize technology
        technology = ep.get("technology", "").upper()
        
        # Build row with or without periods column
        if has_dash:
            rows.append(f'<tr><td>{i + 1}</td><td>{ep["endpoint"]}</td><td>{technology}</td>{renditions_cell}{ad_breaks_cell}{periods_cell}{events_cell}{status_cell}</tr>')
        else:
            rows.append(f'<tr><td>{i + 1}</td><td>{ep["endpoint"]}</td><td>{technology}</td>{renditions_cell}{ad_breaks_cell}{events_cell}{status_cell}</tr>')
    
    rows_html = ''.join(rows)
    
    # Calculate summary info
    total_endpoints = len(sorted_endpoints)
    has_events = any(events_dict.values()) if events_dict else False
    
    # Show warning icon if there are events, checkmark if all good
    if has_events:
        status_indicator = '&nbsp;&nbsp;⚠️'
    else:
        status_indicator = '&nbsp;&nbsp;✅'
    
    # Build table header based on whether DASH endpoints exist
    if has_dash:
        table_header = f'<tr><th>ID</th><th>Endpoint</th><th>Technology</th><th>Current Renditions (video/audio/subtitles)</th><th>Ad Breaks (regular/overlay)</th><th>Periods</th><th>Warnings &nbsp;<span style="cursor: pointer;">ⓘ</span><cwdb-action action="call" display="popup" endpoint="{failure_codes_arn}">{{}}</cwdb-action></th><th>Status</th></tr>'
    else:
        table_header = f'<tr><th>ID</th><th>Endpoint</th><th>Technology</th><th>Current Renditions (video/audio/subtitles)</th><th>Ad Breaks (regular/overlay)</th><th>Warnings &nbsp;<span style="cursor: pointer;">ⓘ</span><cwdb-action action="call" display="popup" endpoint="{failure_codes_arn}">{{}}</cwdb-action></th><th>Status</th></tr>'
    
    html = f'''<html>
<head><title>Report</title></head>
<body style="margin: 0;">
    <p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Status{status_indicator}</p>
    <table border="1" style="width: 100%; border-collapse: collapse;">
        {table_header}
        {rows_html}
    </table>
</body>
</html>'''
    
    return html

def get_ad_break_metrics_optimized(cloudwatch_client, endpoints, params, start_time, end_time):
    try:
        # Convert timestamps to datetime objects
        if isinstance(start_time, (int, float)):
            start_time = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc)
        elif isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            
        if isinstance(end_time, (int, float)):
            end_time = datetime.fromtimestamp(end_time / 1000, tz=timezone.utc)
        elif isinstance(end_time, str):
            end_time = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
        
        # Calculate appropriate period based on time range
        time_diff = (end_time - start_time).total_seconds()
        if time_diff > 259200:  # More than 3 days
            period = 3600  # 1 hour
        elif time_diff > 86400:  # More than 1 day
            period = 300  # 5 minutes
        else:
            period = 60   # 1 minute
        
        # Build metric queries for all endpoints at once
        metric_data_queries = []
        
        for i, ep in enumerate(endpoints):
            # Base dimensions
            dimensions = [
                {'Name': 'Origin', 'Value': params.get('origin', '')},
                {'Name': 'Type', 'Value': params.get('type', 'live')},
                {'Name': 'Endpoint', 'Value': ep.get('endpoint', '')},
                {'Name': 'Technology', 'Value': ep.get('technology', '')},
                {'Name': 'Workload', 'Value': params.get('workload', '')}
            ]
            
            # Filter out empty dimensions
            dimensions = [d for d in dimensions if d['Value']]
            
            # Regular ad breaks query
            metric_data_queries.append({
                'Id': f'regular_{i}',
                'MetricStat': {
                    'Metric': {
                        'Namespace': 'CanaryMonitor',
                        'MetricName': 'Start',
                        'Dimensions': dimensions + [{'Name': 'AdBreakType', 'Value': 'regular'}]
                    },
                    'Period': period,
                    'Stat': 'Sum'
                }
            })
            
            # Overlay ad breaks query
            metric_data_queries.append({
                'Id': f'overlay_{i}',
                'MetricStat': {
                    'Metric': {
                        'Namespace': 'CanaryMonitor',
                        'MetricName': 'Start',
                        'Dimensions': dimensions + [{'Name': 'AdBreakType', 'Value': 'overlay'}]
                    },
                    'Period': period,
                    'Stat': 'Sum'
                }
            })
        
        # Make single API call for all metrics      
        response = cloudwatch_client.get_metric_data(
            MetricDataQueries=metric_data_queries,
            StartTime=start_time,
            EndTime=end_time
        )
        
        
        # Process results
        regular_counts = {}
        overlay_counts = {}
        
        for result in response.get('MetricDataResults', []):
            metric_id = result['Id']
            values = result.get('Values', [])
            total = int(sum(values)) if values else 0
            
            if metric_id.startswith('regular_'):
                endpoint_index = int(metric_id.split('_')[1])
                regular_counts[endpoint_index] = total
            elif metric_id.startswith('overlay_'):
                endpoint_index = int(metric_id.split('_')[1])
                overlay_counts[endpoint_index] = total
        
        return regular_counts, overlay_counts
        
    except Exception as e:
        print(f"Error getting ad break metrics: {str(e)}")
        # Fallback to empty results
        return {}, {}

def s3_file_exists(s3_client, s3_path):
    try:
        if s3_path.startswith('s3://'):
            s3_path = s3_path[5:]
        bucket, key = s3_path.split('/', 1)
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except:
        return False

def get_log_events(logs_client, endpoints, params, start_time, end_time):
    try:
        # Convert timestamps to milliseconds
        if isinstance(start_time, (int, float)):
            start_time_ms = int(start_time)
        else:
            start_time_ms = int(datetime.fromisoformat(start_time.replace('Z', '+00:00')).timestamp() * 1000)
            
        if isinstance(end_time, (int, float)):
            end_time_ms = int(end_time)
        else:
            end_time_ms = int(datetime.fromisoformat(end_time.replace('Z', '+00:00')).timestamp() * 1000)
        
        # Build query for all endpoints
        query = f'''
        fields endpoint, technology, event, @timestamp
        | filter levelname in ["WARNING", "CRITICAL", "ERROR"]
        | filter type = "{params.get('type', 'live')}"
        | filter workload = "{params.get('workload', '')}"
        | filter origin = "{params.get('origin', '')}"
        | filter ispresent(event)
        | stats count() as event_count, max(@timestamp) as last_occurrence by endpoint, technology, event
        '''
               
        response = logs_client.start_query(
            logGroupName='CanaryMonitor/MonitorLogs',
            startTime=start_time_ms,
            endTime=end_time_ms,
            queryString=query
        )
        
        query_id = response['queryId']
        
        # Wait for query to complete
        import time
        while True:
            result = logs_client.get_query_results(queryId=query_id)
            if result['status'] == 'Complete':
                break
            elif result['status'] == 'Failed':
                print(f"Query failed: {result}")
                return {}
            time.sleep(0.5)
               
        # Build events dictionary - only include endpoints from the provided list
        events_dict = {}
        endpoint_set = {f"{ep['endpoint']}_{ep.get('technology', '')}" for ep in endpoints}
        
        for result_row in result.get('results', []):
            row_dict = {item['field']: item['value'] for item in result_row}
            key = f"{row_dict['endpoint']}_{row_dict['technology']}"
            if key in endpoint_set:
                if key not in events_dict:
                    events_dict[key] = {}
                event_name = row_dict['event']
                count = int(row_dict.get('event_count', row_dict.get('count()', 1)))
                last_occurrence = row_dict.get('last_occurrence', '')
                events_dict[key][event_name] = {'count': count, 'last_occurrence': last_occurrence}
       
        return events_dict
        
    except Exception as e:
        print(f"Error getting log events: {str(e)}")
        return None
