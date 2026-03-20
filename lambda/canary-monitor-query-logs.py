import boto3
import json
import time

def lambda_handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    # Handle both direct invocation and widget context
    if 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        forms = event.get('widgetContext', {}).get('forms', {}).get('all', {})
        time_range = event.get('widgetContext', {}).get('timeRange', {})
        
        type_val = params.get('type', 'live')
        workload = params.get('workload', '')
        origin = params.get('origin', '')
        endpoint = forms.get('endpoint', '').strip()
        technology = forms.get('technology', '').strip()
        rendition = forms.get('rendition', '').strip()
        log_levels = forms.get('logLevel', [])
        
        # Get time range
        start_time = time_range.get('start')
        end_time = time_range.get('end')
        time_range_mode = time_range.get('mode', 'absolute')
        relative_start = time_range.get('relativeStart', 0)
    else:
        type_val = event.get('type', 'live')
        workload = event.get('workload', '')
        origin = event.get('origin', '')
        endpoint = event.get('endpoint', '').strip()
        technology = event.get('technology', '').strip()
        rendition = event.get('rendition', '').strip()
        technology = event.get('technology', '').strip()
        log_levels = event.get('logLevel', [])
        start_time = event.get('start_time')
        end_time = event.get('end_time')
        time_range_mode = 'absolute'
        relative_start = 0
    
    # Ensure log_levels is a list
    if isinstance(log_levels, str):
        log_levels = [log_levels]
    elif not isinstance(log_levels, list):
        log_levels = []
    
    print(f"Log levels received: {log_levels} (type: {type(log_levels)})")
    
    if not log_levels:
        return error_response('Please select at least one log level')
    
    # Convert timestamps to milliseconds
    if isinstance(start_time, (int, float)):
        start_time_ms = int(start_time)
    else:
        from datetime import datetime
        start_time_ms = int(datetime.fromisoformat(str(start_time).replace('Z', '+00:00')).timestamp() * 1000)
        
    if isinstance(end_time, (int, float)):
        end_time_ms = int(end_time)
    else:
        from datetime import datetime
        end_time_ms = int(datetime.fromisoformat(str(end_time).replace('Z', '+00:00')).timestamp() * 1000)
    
    # Build log level filter
    log_level_pattern = '|'.join(log_levels)
    
    # Build query
    query_parts = [
        f'filter type == "{type_val}"',
        f'filter origin == "{origin}"',
        f'filter workload == "{workload}"',
        f'filter levelname =~ /{log_level_pattern}/'
    ]
    
    if endpoint:
        query_parts.append(f'filter endpoint == "{endpoint}"')
    
    if technology:
        query_parts.append(f'filter technology == "{technology}"')
    
    if rendition:
        query_parts.append(f'filter rendition == "{rendition}"')
    
    query = '\n| '.join(query_parts)
    query += '\n| display @timestamp, levelname, technology, endpoint, rendition, event, message'
    query += '\n| sort @timestamp desc'
    query += '\n| limit 5000'
    
    print(f"Query: {query}")
    
    # Execute CloudWatch Insights query
    logs_client = boto3.client('logs')
    
    try:
        response = logs_client.start_query(
            logGroupName='CanaryMonitor/MonitorLogs',
            startTime=start_time_ms,
            endTime=end_time_ms,
            queryString=query
        )
        
        query_id = response['queryId']
        
        # Wait for query to complete (max 60 seconds)
        max_wait = 60
        elapsed = 0
        while elapsed < max_wait:
            result = logs_client.get_query_results(queryId=query_id)
            status = result['status']
            
            if status == 'Complete':
                return format_results(result.get('results', []), query, time_range_mode, relative_start, start_time, end_time)
            elif status == 'Failed':
                return error_response(f'Query failed: {result}')
            
            time.sleep(1)
            elapsed += 1
        
        return error_response('Query timed out after 60 seconds')
        
    except Exception as e:
        return error_response(f'Error executing query: {str(e)}')


def format_results(results, query, time_range_mode, relative_start, start_time, end_time):
    # Format time range description
    if time_range_mode == 'relative' and relative_start:
        # Convert milliseconds to human readable
        hours = relative_start // 3600000
        minutes = (relative_start % 3600000) // 60000
        if hours > 0:
            time_desc = f"in the last {hours} hour{'s' if hours > 1 else ''}"
        elif minutes > 0:
            time_desc = f"in the last {minutes} minute{'s' if minutes > 1 else ''}"
        else:
            time_desc = "in recent time"
    else:
        # Absolute time range
        from datetime import datetime, timezone
        if isinstance(start_time, (int, float)):
            start_dt = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc)
            end_dt = datetime.fromtimestamp(end_time / 1000, tz=timezone.utc)
        else:
            start_dt = datetime.fromisoformat(str(start_time).replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(str(end_time).replace('Z', '+00:00'))
        time_desc = f"between {start_dt.strftime('%Y-%m-%d %H:%M:%S')} and {end_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC"
    
    if not results:
        return success_response(f'No logs found matching the criteria {time_desc}', query)
    
    # Build HTML table
    rows = []
    for result_row in results:
        row_dict = {item['field']: item['value'] for item in result_row}
        timestamp = row_dict.get('@timestamp', '')
        level = row_dict.get('levelname', '')
        technology = row_dict.get('technology', '')
        endpoint = row_dict.get('endpoint', '')
        rendition = row_dict.get('rendition', '')
        event = row_dict.get('event', '')
        message = row_dict.get('message', '')
        
        # Color code by log level
        if level == 'CRITICAL':
            row_style = ' style="background-color: #f5c9c9;"'
        elif level == 'ERROR':
            row_style = ' style="background-color: #ffe6e6;"'
        elif level == 'WARNING':
            row_style = ' style="background-color: #fff4e6;"'
        elif level == 'INFO':
            row_style = ' style="background-color: #d4edda;"'
        else:
            row_style = ''
        
        rows.append(f'<tr{row_style}><td>{timestamp}</td><td>{level}</td><td>{technology}</td><td>{endpoint}</td><td>{rendition}</td><td>{event}</td><td>{message}</td></tr>')
    
    rows_html = ''.join(rows)
    
    html = f'''<html>
<head><title>Log Results</title></head>
<body style="margin: 20px;">
    <p style="font-size: 1.2em; font-weight: bold; margin-bottom: 15px;">Found {len(results)} log entries {time_desc}</p>
    <details style="margin-bottom: 15px;">
        <summary style="cursor: pointer; font-weight: bold;">Query</summary>
        <pre style="background: #f5f5f5; padding: 10px; border-radius: 4px; overflow-x: auto; font-size: 12px;">{query}</pre>
    </details>
    <div style="overflow-x: auto;">
        <table border="1" style="width: 100%; border-collapse: collapse; font-size: 12px;">
            <tr><th>Timestamp</th><th>Level</th><th>Technology</th><th>Endpoint</th><th>Rendition</th><th>Event</th><th>Message</th></tr>
            {rows_html}
        </table>
    </div>
</body>
</html>'''
    
    return html


def success_response(message, query):
    return f'''<html>
<head><title>Results</title></head>
<body style="margin: 20px;">
    <p style="font-size: 1.2em; font-weight: bold; margin-bottom: 15px;">{message}</p>
    <details style="margin-top: 15px;">
        <summary style="cursor: pointer; font-weight: bold;">Query</summary>
        <pre style="background: #f5f5f5; padding: 10px; border-radius: 4px; overflow-x: auto; font-size: 12px;">{query}</pre>
    </details>
</body>
</html>'''


def error_response(message):
    return f'''<html>
<head><title>Error</title></head>
<body style="margin: 20px;">
    <h2 style="color: #ff6361; margin-bottom: 30px;">✗ Error</h2>
    <p>{message}</p>
</body>
</html>'''
