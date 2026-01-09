import boto3
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

def lambda_handler(event, context):
    print(f"Event: {json.dumps(event)}")
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
    
    # Run S3, CloudWatch, and Logs checks in parallel
    import time
    start_total = time.time()
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        # Submit all tasks with timing
        start_s3 = time.time()
        s3_future = executor.submit(check_all_s3_files, s3_client, sorted_endpoints)
        
        start_metrics = time.time()
        metrics_future = executor.submit(get_ad_break_metrics_optimized, cloudwatch_client, sorted_endpoints, params, start_time, end_time)
        
        start_events = time.time()
        events_future = executor.submit(get_log_events, logs_client, sorted_endpoints, params, start_time, end_time)
        
        # Get results with timing
        file_exists = s3_future.result()
        end_s3 = time.time()
        
        regular_counts, overlay_counts = metrics_future.result()
        end_metrics = time.time()
        
        events_dict = events_future.result()
        end_events = time.time()
    
    end_total = time.time()
    print(f"Thread timings - S3: {end_s3 - start_s3:.2f}s, Metrics: {end_metrics - start_metrics:.2f}s, Events: {end_events - start_events:.2f}s, Total: {end_total - start_total:.2f}s")
    
    # Check if logs query failed
    logs_query_failed = events_dict is None
    
    # Build HTML table
    rows = []
    for i, ep in enumerate(sorted_endpoints):
        # Report column
        report_location = ep.get('report', {}).get('location', '')
        if report_location and file_exists.get(i, False):
            # REPLACE
            report_cell = f'''<td><b style="cursor: pointer;">show</b>
<cwdb-action action="call" display="popup" endpoint="arn:aws:lambda:us-west-2:012345678910:function:canary-monitor-report-fetcher">
{{"s3_path": "{report_location}"}}
</cwdb-action></td>'''
        else:
            report_cell = '<td>n/a</td>'
        
        # Ad breaks columns
        regular_count = regular_counts.get(i, 'n/a')
        overlay_count = overlay_counts.get(i, 'n/a')
        regular_cell = f'<td>{regular_count}</td>'
        overlay_cell = f'<td>{overlay_count}</td>'
        
        # Events column
        events_key = f"{ep['endpoint']}_{ep.get('technology', '')}"
        events = events_dict.get(events_key, {}) if events_dict else {}
        events_list = [f"{event} ({count})" for event, count in sorted(events.items())]
        events_cell = f'<td>{", ".join(events_list)}</td>'
        
        # Row background color if events exist
        row_style = ' style="background-color: #f5c9c9;"' if events else ''
            
        rows.append(f'<tr{row_style}><td>{i + 1}</td><td>{ep["endpoint"]}</td><td>{ep.get("technology", "")}</td>{report_cell}{regular_cell}{overlay_cell}{events_cell}</tr>')
    
    rows_html = ''.join(rows)
    
    # Calculate summary info
    total_endpoints = len(sorted_endpoints)
    has_events = any(events_dict.values()) if events_dict else False
    
    if logs_query_failed:
        status_dot = '<span style="color: #c7c7c7;">⬤</span>'
    elif has_events:
        status_dot = '<span style="color: #f5c9c9;">⬤</span>'
    else:
        status_dot = '<span style="color: #cae7ca;">⬤</span>'
    
    html = f'''<html>
<head><title>Report</title></head>
<body style="margin: 0; text-align: center;">
    <p style="font-size: 20px; margin-bottom: 20px; text-align: center;">Status: {status_dot}</p>
    <table border="1" style="margin: 0 auto;">
        <tr><th>id</th><th>endpoint</th><th>technology</th><th>report</th><th>regular ad breaks</th><th>overlay ad breaks</th><th>unexpected events</th></tr>
        {rows_html}
    </table>
</body>
</html>'''
    
    return html

def check_all_s3_files(s3_client, endpoints):
    file_exists = {}
    for i, ep in enumerate(endpoints):
        report_location = ep.get('report', {}).get('location', '')
        if report_location:
            file_exists[i] = s3_file_exists(s3_client, report_location)
        else:
            file_exists[i] = False
    return file_exists

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
        fields endpoint, technology, event
        | filter levelname in ["WARNING", "ERROR"]
        | filter type = "{params.get('type', 'live')}"
        | filter workload = "{params.get('workload', '')}"
        | filter origin = "{params.get('origin', '')}"
        | filter ispresent(event)
        | stats count() by endpoint, technology, event
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
                count = int(row_dict.get('count', row_dict.get('count()', 1)))  # Handle different field names
                events_dict[key][event_name] = count
       
        return events_dict
        
    except Exception as e:
        print(f"Error getting log events: {str(e)}")
        return None
