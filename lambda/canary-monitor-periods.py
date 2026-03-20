import boto3
import json
from datetime import datetime, timezone

def lambda_handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    # Handle widget context
    if 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        s3_path = params.get('s3_path', '')
        start_time_str = params.get('start_time', '')
        end_time_str = params.get('end_time', '')
    else:
        s3_path = event.get('s3_path', '')
        start_time_str = event.get('start_time', '')
        end_time_str = event.get('end_time', '')
    
    if not s3_path:
        return error_response('No S3 path specified')
    
    # Parse S3 path
    if s3_path.startswith('s3://'):
        s3_path = s3_path[5:]
    
    try:
        bucket, key = s3_path.split('/', 1)
    except ValueError:
        return error_response('Invalid S3 path format')
    
    # Convert time strings to datetime
    try:
        start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
        end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
    except Exception as e:
        return error_response(f'Invalid time format: {str(e)}')
    
    # Load report.json from S3
    s3_client = boto3.client('s3')
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        report_data = json.loads(response['Body'].read().decode('utf-8'))
    except s3_client.exceptions.NoSuchKey:
        return error_response('Report file does not exist')
    except Exception as e:
        return error_response(f'Error loading report file: {str(e)}')
    
    # Check if report has data
    if not report_data:
        return error_response('Report file is empty')
    
    # Build periods table - iterate through all epochs
    rows = []
    counter = 1
    
    # Iterate through all epochs in the report
    for epoch_key, epoch_data in report_data.items():
        periods_list = epoch_data.get('periods', [])
        
        for period_entry in periods_list:
            for period_id, period_data in period_entry.items():
                observed = period_data.get('observed')
                if observed:
                    try:
                        observed_dt = datetime.fromisoformat(observed.replace('Z', '+00:00'))
                        if start_time <= observed_dt <= end_time:
                            duration = period_data.get('duration', 'n/a')
                            if duration != 'n/a':
                                duration = round(duration, 3)
                            is_compact = 'Yes' if period_data.get('is_compact', False) else 'No'
                            
                            # Build adaptation sets / representations info
                            adaptation_sets = period_data.get('adaptation_sets', [])
                            adaptation_info_lines = []
                            for adaptation_set in adaptation_sets:
                                mime_type = adaptation_set.get('mime_type', 'n/a')
                                representations_count = len(adaptation_set.get('representations', []))
                                adaptation_info_lines.append(f"{mime_type}: {representations_count}")
                            adaptation_info = ', '.join(adaptation_info_lines) if adaptation_info_lines else 'n/a'
                            
                            # Build event streams YAML
                            event_streams = period_data.get('event_streams', [])
                            yaml_lines = []
                            is_ad_break = 'No'
                            
                            for i, event_stream in enumerate(event_streams):
                                if i > 0:
                                    yaml_lines.append("")
                                if 'duration' in event_stream:
                                    yaml_lines.append(f"- duration: {event_stream['duration']}")
                                else:
                                    yaml_lines.append("-")
                                
                                events = event_stream.get('events', [])
                                if events:
                                    yaml_lines.append("  events:")
                                    for event in events:
                                        yaml_lines.append("  -")
                                        if 'duration' in event:
                                            yaml_lines.append(f"    duration: {event['duration']}")
                                        if 'scte_message' in event and 'decoded' in event['scte_message']:
                                            yaml_lines.append(f"    scte_message:")
                                            yaml_lines.append(f"      decoded:")
                                            decoded = event['scte_message']['decoded']
                                            if isinstance(decoded, dict):
                                                for key, value in decoded.items():
                                                    if key == 'descriptors' and isinstance(value, list):
                                                        yaml_lines.append(f"        {key}:")
                                                        for desc in value:
                                                            yaml_lines.append(f"        -")
                                                            if isinstance(desc, dict):
                                                                for desc_key, desc_value in desc.items():
                                                                    yaml_lines.append(f"          {desc_key}: {desc_value}")
                                                    else:
                                                        yaml_lines.append(f"        {key}: {value}")
                                        
                                        if event.get('is_opportunity', False):
                                            is_ad_break = 'Yes'
                            
                            event_streams_yaml = '\n'.join(yaml_lines) if yaml_lines else ''
                            
                            # Add background color for ad break rows
                            row_style = ' style="background-color: #f2f2f2;"' if is_ad_break == 'Yes' else ''
                            
                            rows.append(f'''<tr{row_style}>
                                <td>{counter}</td>
                                <td>{observed}</td>
                                <td>{period_id}</td>
                                <td>{adaptation_info}</td>
                                <td><pre style="margin: 0; font-size: 11px; background: none;">{event_streams_yaml}</pre></td>
                                <td>{duration}</td>
                                <td>{is_compact}</td>
                                <td>{is_ad_break}</td>
                            </tr>''')
                            counter += 1
                    except Exception as e:
                        print(f"Error processing period {period_id}: {str(e)}")
    
    if not rows:
        return error_response('No periods found in the specified time window')
    
    rows_html = ''.join(rows)
    
    html = f'''<html>
<head>
    <title>Periods</title>
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
    <p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Periods</p>
    <table>
        <tr>
            <th>ID</th>
            <th>Observed Start Time</th>
            <th>Period ID</th>
            <th>Adaptation Set: Representations</th>
            <th>Event Streams</th>
            <th>Duration (sec)</th>
            <th>Is Compact</th>
            <th>Is Ad Break</th>
        </tr>
        {rows_html}
    </table>
</body>
</html>'''
    
    return html


def error_response(message):
    return f'''<html>
<head><title>Error</title></head>
<body style="margin: 20px;">
    <h2 style="color: #ff6361; margin-bottom: 30px;">✗ Error</h2>
    <p>{message}</p>
</body>
</html>'''
