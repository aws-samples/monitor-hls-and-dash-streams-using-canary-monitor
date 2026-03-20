import boto3
import json
from datetime import datetime, timezone

def lambda_handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    # Handle widget context
    if 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        s3_path = params.get('s3_path', '')
        technology = params.get('technology', '').lower()
        time_range = event.get('widgetContext', {}).get('timeRange', {})
        start_time = time_range.get('start')
        end_time = time_range.get('end')
    else:
        s3_path = event.get('s3_path', '')
        technology = event.get('technology', '').lower()
        start_time = event.get('start_time')
        end_time = event.get('end_time')
    
    # Infer technology from s3_path if not provided
    if not technology and s3_path:
        if '/dash/' in s3_path:
            technology = 'dash'
        elif '/hls/' in s3_path:
            technology = 'hls'
    
    if not s3_path:
        return error_response('No S3 path specified')
    
    # Convert timestamps to datetime objects
    if isinstance(start_time, (int, float)):
        start_dt = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc)
    elif isinstance(start_time, str):
        start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
    else:
        start_dt = None
    
    if isinstance(end_time, (int, float)):
        end_dt = datetime.fromtimestamp(end_time / 1000, tz=timezone.utc)
    elif isinstance(end_time, str):
        end_dt = datetime.fromisoformat(end_time.replace('Z', '+00:00'))
    else:
        end_dt = None
    
    # Parse S3 path
    if s3_path.startswith('s3://'):
        s3_path = s3_path[5:]
    
    try:
        bucket, key = s3_path.split('/', 1)
    except ValueError:
        return error_response('Invalid S3 path format')
    
    # Load report.json from S3
    s3_client = boto3.client('s3')
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        report_data = json.loads(response['Body'].read().decode('utf-8'))
    except s3_client.exceptions.NoSuchKey:
        return error_response('Report file does not exist')
    except Exception as e:
        return error_response(f'Error loading report file: {str(e)}')
    
    # Collect ad breaks within time window
    ad_breaks = []
    
    if technology == 'dash':
        # For DASH, look through periods for adbreak key
        for epoch_key, epoch_data in report_data.items():
            periods_list = epoch_data.get('periods', [])
            
            for period_entry in periods_list:
                for period_id, period_data in period_entry.items():
                    observed = period_data.get('observed')
                    
                    if not observed:
                        continue
                    
                    # Parse observed time
                    try:
                        observed_dt = datetime.fromisoformat(observed.replace('Z', '+00:00'))
                    except:
                        continue
                    
                    # Check if within time window
                    if start_dt and end_dt:
                        if not (start_dt <= observed_dt <= end_dt):
                            continue
                    
                    # Check if adbreak key exists and is not empty
                    adbreak = period_data.get('adbreak')
                    if adbreak:
                        ad_breaks.append({
                            'observed': observed,
                            'observed_dt': observed_dt,
                            'period_id': period_id,
                            'advertised_duration': adbreak.get('advertised_duration', 'n/a'),
                            'duration_delta': adbreak.get('duration_delta', ''),
                            'type': adbreak.get('type', 'n/a')
                        })
    else:
        # For HLS, use existing ad_breaks logic
        for report_start_time, report_content in report_data.items():
            if 'ad_breaks' not in report_content:
                continue
            
            for ad_break_id, ad_break in report_content['ad_breaks'].items():
                observed = ad_break.get('observed')
                
                if not observed:
                    continue
                
                # Parse observed time
                try:
                    observed_dt = datetime.fromisoformat(observed.replace('Z', '+00:00'))
                except:
                    continue
                
                # Check if within time window
                if start_dt and end_dt:
                    if not (start_dt <= observed_dt <= end_dt):
                        continue
                
                ad_breaks.append({
                    'observed': observed,
                    'observed_dt': observed_dt,
                    'advertised_duration': ad_break.get('advertised_duration', 'n/a'),
                    'duration_delta': ad_break.get('duration_delta', 'n/a'),
                    'scte_message': ad_break.get('scte_message', {}),
                    'ads': ad_break.get('ads', []),
                    'daterange_id': ad_break.get('daterange_id', 'n/a'),
                    'type': ad_break.get('type', 'n/a')
                })
    
    # Sort by observed time
    ad_breaks.sort(key=lambda x: x['observed_dt'])
    
    # Build table rows based on technology
    rows = []
    if technology == 'dash':
        # DASH: Simple table with ID, Observed Start Time, Period ID, Advertised Duration, Duration Delta, Type
        for i, ad_break in enumerate(ad_breaks, 1):
            row_style = ' style="background-color: #f2f2f2;"' if ad_break['type'] == 'overlay' else ''
            rows.append(f'''<tr{row_style}>
                <td>{i}</td>
                <td>{ad_break['observed']}</td>
                <td>{ad_break['period_id']}</td>
                <td>{ad_break['advertised_duration']}</td>
                <td>{ad_break['duration_delta']}</td>
                <td>{ad_break['type']}</td>
            </tr>''')
        
        rows_html = ''.join(rows) if rows else '<tr><td colspan="6" style="text-align: center;">No ad breaks found in the selected time range</td></tr>'
        
        html = f'''<html>
<head>
    <title>Ad Breaks</title>
    <style>
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
        }}
        th {{
            background-color: #f2f2f2;
            font-weight: bold;
        }}
    </style>
</head>
<body style="margin: 0; padding: 20px;">
    <p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Ad Breaks</p>
    <table>
        <tr>
            <th>ID</th>
            <th>Observed Start Time</th>
            <th>Period ID</th>
            <th>Advertised Duration (sec)</th>
            <th>Segments Duration Delta (sec)</th>
            <th>Type</th>
        </tr>
        {rows_html}
    </table>
</body>
</html>'''
    else:
        # HLS: Full table with all columns
        for i, ad_break in enumerate(ad_breaks, 1):
            row_style = ' style="background-color: #f2f2f2;"' if ad_break['type'] == 'overlay' else ''
            
            scte_msg = ad_break['scte_message']
            if isinstance(scte_msg, dict) and scte_msg and 'decoded' in scte_msg:
                scte_formatted = format_dict_as_yaml(scte_msg['decoded'])
            elif isinstance(scte_msg, dict) and scte_msg:
                scte_formatted = format_dict_as_yaml(scte_msg)
            else:
                scte_formatted = 'n/a'
            
            ads = ad_break['ads']
            if isinstance(ads, list) and ads:
                ads_formatted = format_dict_as_yaml({'ads': ads})
            else:
                ads_formatted = 'n/a'
            
            rows.append(f'''<tr{row_style}>
                <td>{i}</td>
                <td>{ad_break['observed']}</td>
                <td>{ad_break['advertised_duration']}</td>
                <td>{ad_break['duration_delta']}</td>
                <td style="word-break: break-all; max-width: 400px; white-space: pre-wrap; font-family: monospace;">{scte_formatted}</td>
                <td style="word-break: break-all; max-width: 400px; white-space: pre-wrap; font-family: monospace;">{ads_formatted}</td>
                <td>{ad_break['daterange_id']}</td>
                <td>{ad_break['type']}</td>
            </tr>''')
        
        rows_html = ''.join(rows) if rows else '<tr><td colspan="8" style="text-align: center;">No ad breaks found in the selected time range</td></tr>'
        
        html = f'''<html>
<head>
    <title>Ad Breaks</title>
    <style>
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
        }}
        th {{
            background-color: #f2f2f2;
            font-weight: bold;
        }}
    </style>
</head>
<body style="margin: 0; padding: 20px;">
    <p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Ad Breaks</p>
    <table>
        <tr>
            <th>ID</th>
            <th>Observed Start Time</th>
            <th>Advertised Duration (sec)</th>
            <th>Segments Duration Delta (sec)</th>
            <th>SCTE Message</th>
            <th>Ads Info</th>
            <th>Date Range ID</th>
            <th>Type</th>
        </tr>
        {rows_html}
    </table>
</body>
</html>'''
    
    return html


def format_dict_as_yaml(obj, indent=0):
    """Format dictionary as YAML-style string"""
    if not isinstance(obj, dict):
        return str(obj)
    
    lines = []
    for key, value in obj.items():
        prefix = '  ' * indent
        if isinstance(value, dict):
            lines.append(f'{prefix}{key}:')
            lines.append(format_dict_as_yaml(value, indent + 1))
        elif isinstance(value, list):
            lines.append(f'{prefix}{key}:')
            # Skip "-" if only one item
            if len(value) == 1:
                item = value[0]
                if isinstance(item, dict):
                    for k, v in item.items():
                        lines.append(f'{prefix}  {k}: {v}')
                else:
                    lines.append(f'{prefix}  {item}')
            else:
                for item in value:
                    if isinstance(item, dict):
                        lines.append(f'{prefix}  -')
                        for k, v in item.items():
                            lines.append(f'{prefix}    {k}: {v}')
                    else:
                        lines.append(f'{prefix}  - {item}')
        else:
            lines.append(f'{prefix}{key}: {value}')
    
    return '\n'.join(lines)


def error_response(message):
    return f'''<html>
<head><title>Error</title></head>
<body style="margin: 20px;">
    <h2 style="color: #ff6361; margin-bottom: 30px;">✗ Error</h2>
    <p>{message}</p>
</body>
</html>'''
