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
    else:
        s3_path = event.get('s3_path', '')
        technology = event.get('technology', '').lower()
    
    if not s3_path:
        return error_response('No S3 path specified')
    
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
    
    # Get latest epoch and renditions
    if not report_data:
        return error_response('Report file is empty')
    
    latest_epoch = max(report_data.keys(), key=int)
    latest_report = report_data[latest_epoch]
    
    # For HLS, renditions are at top level
    # For DASH, we need to get adaptation sets from the latest period
    if technology == 'dash':
        periods_list = latest_report.get('periods', [])
        if periods_list:
            # Get the last period (most recent)
            last_period_entry = periods_list[-1]
            # Get the first (and should be only) period dict in the entry
            for period_id, period_data in last_period_entry.items():
                renditions_dict = period_data.get('adaptation_sets', [])
                break
        else:
            renditions_dict = []
    else:
        renditions_dict = latest_report.get('renditions', {})
    
    if not renditions_dict:
        return error_response('No renditions found in report')
    
    # Build table based on technology
    if technology == 'hls':
        table_html = build_hls_table(renditions_dict)
    elif technology == 'dash':
        table_html = build_dash_table(renditions_dict)
    else:
        table_html = build_hls_table(renditions_dict)  # Default to HLS
    
    html = f'''<html>
<head>
    <title>Renditions</title>
    <style>
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
            margin-bottom: 20px;
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
    {table_html}
</body>
</html>'''
    
    return html


def build_hls_table(renditions_dict):
    """Build HLS renditions table"""
    tables = []
    
    def format_value(val):
        """Format value - if list, join with comma"""
        if isinstance(val, list):
            return ', '.join(str(v) for v in val)
        return val if val else 'n/a'
    
    # Video table
    if 'video' in renditions_dict and renditions_dict['video']:
        video_rows = []
        for url, data in renditions_dict['video'].items():
            index = data.get('index', 'n/a')
            audio_val = data.get('audio')
            
            # Check if audio is a list to determine if we need multiple rows
            if isinstance(audio_val, list) and len(audio_val) > 0:
                # Multiple rows needed
                for i, audio_item in enumerate(audio_val):
                    # Get values - use list index if list, otherwise use single value
                    bandwidth_val = data.get('bandwidth')
                    bandwidth = bandwidth_val[i] if isinstance(bandwidth_val, list) and i < len(bandwidth_val) else (bandwidth_val if not isinstance(bandwidth_val, list) else 'n/a')
                    
                    avg_bandwidth_val = data.get('averagebandwidth')
                    avg_bandwidth = avg_bandwidth_val[i] if isinstance(avg_bandwidth_val, list) and i < len(avg_bandwidth_val) else (avg_bandwidth_val if not isinstance(avg_bandwidth_val, list) else 'n/a')
                    
                    codecs_val = data.get('codecs')
                    codecs = codecs_val[i] if isinstance(codecs_val, list) and i < len(codecs_val) else (codecs_val if not isinstance(codecs_val, list) else 'n/a')
                    
                    # Single values - copy to all rows
                    resolution = data.get('resolution') or 'n/a'
                    framerate = data.get('framerate') or 'n/a'
                    video_range = data.get('videorange') or 'n/a'
                    
                    # Show index only on first row
                    display_index = index if i == 0 else ''
                    is_monitored = 'Yes' if data.get('ismonitored', False) else 'No'
                    
                    video_rows.append(f'''<tr>
                        <td>{display_index}</td>
                        <td>{bandwidth}</td>
                        <td>{avg_bandwidth}</td>
                        <td>{resolution}</td>
                        <td>{framerate}</td>
                        <td>{video_range}</td>
                        <td>{codecs}</td>
                        <td>{audio_item}</td>
                        <td style="word-break: break-all;">{url}</td>
                        <td>{is_monitored}</td>
                    </tr>''')
            else:
                # Single row
                bandwidth = format_value(data.get('bandwidth'))
                avg_bandwidth = format_value(data.get('averagebandwidth'))
                resolution = data.get('resolution') or 'n/a'
                framerate = data.get('framerate') or 'n/a'
                video_range = data.get('videorange') or 'n/a'
                codecs = format_value(data.get('codecs'))
                audio = format_value(audio_val)
                is_monitored = 'Yes' if data.get('ismonitored', False) else 'No'
                
                video_rows.append(f'''<tr>
                    <td>{index}</td>
                    <td>{bandwidth}</td>
                    <td>{avg_bandwidth}</td>
                    <td>{resolution}</td>
                    <td>{framerate}</td>
                    <td>{video_range}</td>
                    <td>{codecs}</td>
                    <td>{audio}</td>
                    <td style="word-break: break-all;">{url}</td>
                    <td>{is_monitored}</td>
                </tr>''')
        
        tables.append(f'''<p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Video</p>
<table>
    <tr>
        <th>Index</th>
        <th>Bandwidth</th>
        <th>Average Bandwidth</th>
        <th>Resolution</th>
        <th>Frame Rate</th>
        <th>Video Range</th>
        <th>Codecs</th>
        <th>Audio</th>
        <th>URL</th>
        <th>Is Monitored</th>
    </tr>
    {''.join(video_rows)}
</table><br>''')
    
    # Audio table
    if 'audio' in renditions_dict and renditions_dict['audio']:
        audio_rows = []
        for url, data in renditions_dict['audio'].items():
            index = data.get('index', 'n/a')
            language = data.get('language') or 'n/a'
            name = data.get('name') or 'n/a'
            channels = data.get('channels') or 'n/a'
            group_id = data.get('groupid') or 'n/a'
            is_monitored = 'Yes' if data.get('ismonitored', False) else 'No'
            
            audio_rows.append(f'''<tr>
                <td>{index}</td>
                <td>{language}</td>
                <td>{name}</td>
                <td>{channels}</td>
                <td>{group_id}</td>
                <td style="word-break: break-all;">{url}</td>
                <td>{is_monitored}</td>
            </tr>''')
        
        tables.append(f'''<p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Audio</p>
<table>
    <tr>
        <th>Index</th>
        <th>Language</th>
        <th>Name</th>
        <th>Channels</th>
        <th>Group ID</th>
        <th>URL</th>
        <th>Is Monitored</th>
    </tr>
    {''.join(audio_rows)}
</table><br>''')
    
    # Subtitles table
    if 'subtitles' in renditions_dict and renditions_dict['subtitles']:
        subtitle_rows = []
        for url, data in renditions_dict['subtitles'].items():
            index = data.get('index', 'n/a')
            language = data.get('language') or 'n/a'
            name = data.get('name') or 'n/a'
            channels = data.get('channels') or 'n/a'
            group_id = data.get('groupid') or 'n/a'
            is_monitored = 'Yes' if data.get('ismonitored', False) else 'No'
            
            subtitle_rows.append(f'''<tr>
                <td>{index}</td>
                <td>{language}</td>
                <td>{name}</td>
                <td>{channels}</td>
                <td>{group_id}</td>
                <td style="word-break: break-all;">{url}</td>
                <td>{is_monitored}</td>
            </tr>''')
        
        tables.append(f'''<p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Subtitles</p>
<table>
    <tr>
        <th>Index</th>
        <th>Language</th>
        <th>Name</th>
        <th>Channels</th>
        <th>Group ID</th>
        <th>URL</th>
        <th>Is Monitored</th>
    </tr>
    {''.join(subtitle_rows)}
</table>''')
    
    return ''.join(tables) if tables else '<table><tr><td style="text-align: center;">No renditions found</td></tr></table>'


def build_dash_table(renditions_dict):
    """Build DASH renditions table from adaptation sets"""
    tables = []
    
    # Video table
    video_rows = []
    video_index = 1
    for adaptation_set in renditions_dict:
        mime_type = adaptation_set.get('mime_type', '')
        if 'video' in mime_type:
            language = adaptation_set.get('lang', '')
            for representation in adaptation_set.get('representations', []):
                rep_id = representation.get('id', '')
                bandwidth = representation.get('bandwidth', '')
                resolution = representation.get('resolution', '')
                frame_rate = representation.get('frame_rate', '')
                codecs = representation.get('codecs', '')
                
                video_rows.append(f'''<tr>
                    <td>{video_index}</td>
                    <td>{rep_id}</td>
                    <td>{bandwidth}</td>
                    <td>{resolution}</td>
                    <td>{frame_rate}</td>
                    <td>{codecs}</td>
                </tr>''')
                video_index += 1
    
    if video_rows:
        tables.append(f'''<p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Video</p>
<table>
    <tr>
        <th>Index</th>
        <th>Representation ID</th>
        <th>Bandwidth</th>
        <th>Resolution</th>
        <th>Frame Rate</th>
        <th>Codec</th>
    </tr>
    {''.join(video_rows)}
</table><br>''')
    
    # Audio table
    audio_rows = []
    audio_index = 1
    for adaptation_set in renditions_dict:
        mime_type = adaptation_set.get('mime_type', '')
        if 'audio' in mime_type:
            language = adaptation_set.get('lang', '')
            for representation in adaptation_set.get('representations', []):
                rep_id = representation.get('id', '')
                bandwidth = representation.get('bandwidth', '')
                sampling_rate = representation.get('audio_sampling_rate', '')
                codecs = representation.get('codecs', '')
                
                audio_rows.append(f'''<tr>
                    <td>{audio_index}</td>
                    <td>{rep_id}</td>
                    <td>{language}</td>
                    <td>{bandwidth}</td>
                    <td>{sampling_rate}</td>
                    <td>{codecs}</td>
                </tr>''')
                audio_index += 1
    
    if audio_rows:
        tables.append(f'''<p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Audio</p>
<table>
    <tr>
        <th>Index</th>
        <th>Representation ID</th>
        <th>Language</th>
        <th>Bandwidth</th>
        <th>Sampling Rate</th>
        <th>Codec</th>
    </tr>
    {''.join(audio_rows)}
</table><br>''')
    
    # Subtitles table
    subtitle_rows = []
    subtitle_index = 1
    for adaptation_set in renditions_dict:
        mime_type = adaptation_set.get('mime_type', '')
        if 'application' in mime_type:
            language = adaptation_set.get('lang', '')
            for representation in adaptation_set.get('representations', []):
                rep_id = representation.get('id', '')
                
                subtitle_rows.append(f'''<tr>
                    <td>{subtitle_index}</td>
                    <td>{rep_id}</td>
                    <td>{language}</td>
                </tr>''')
                subtitle_index += 1
    
    if subtitle_rows:
        tables.append(f'''<p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Subtitles</p>
<table>
    <tr>
        <th>Index</th>
        <th>Representation ID</th>
        <th>Language</th>
    </tr>
    {''.join(subtitle_rows)}
</table><br>''')
    
    # Thumbnails table
    thumbnail_rows = []
    thumbnail_index = 1
    for adaptation_set in renditions_dict:
        mime_type = adaptation_set.get('mime_type', '')
        if 'image' in mime_type:
            for representation in adaptation_set.get('representations', []):
                rep_id = representation.get('id', '')
                bandwidth = representation.get('bandwidth', '')
                resolution = representation.get('resolution', '')
                
                thumbnail_rows.append(f'''<tr>
                    <td>{thumbnail_index}</td>
                    <td>{rep_id}</td>
                    <td>{bandwidth}</td>
                    <td>{resolution}</td>
                </tr>''')
                thumbnail_index += 1
    
    if thumbnail_rows:
        tables.append(f'''<p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Thumbnails</p>
<table>
    <tr>
        <th>Index</th>
        <th>Representation ID</th>
        <th>Bandwidth</th>
        <th>Resolution</th>
    </tr>
    {''.join(thumbnail_rows)}
</table>''')
    
    return ''.join(tables) if tables else '<table><tr><td style="text-align: center;">No renditions found</td></tr></table>'


def error_response(message):
    return f'''<html>
<head><title>Error</title></head>
<body style="margin: 20px;">
    <h2 style="color: #ff6361; margin-bottom: 30px;">✗ Error</h2>
    <p>{message}</p>
</body>
</html>'''
