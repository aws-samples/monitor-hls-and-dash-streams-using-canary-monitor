import boto3
import json

def get_lambda_arn(context, function_name):
    """Build Lambda ARN dynamically from context"""
    arn_parts = context.invoked_function_arn.split(':')
    region = arn_parts[3]
    account = arn_parts[4]
    return f"arn:aws:lambda:{region}:{account}:function:{function_name}"

def lambda_handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    # Handle both direct invocation and widget context
    if 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        bucket = params.get('bucket', '')
        selected_workload = params.get('workload', '')
        selected_origin = params.get('origin', '')
    else:
        bucket = event.get('bucket', '')
        selected_workload = event.get('workload', '')
        selected_origin = event.get('origin', '')
    
    if not bucket or not selected_workload or not selected_origin:
        return '<html><body><p>Error: Missing required parameters</p></body></html>'
    
    s3_client = boto3.client('s3')
    
    # Get all CSV files from origins/ folder
    try:
        response = s3_client.list_objects_v2(Bucket=bucket, Prefix='origins/')
        csv_files = [obj['Key'] for obj in response.get('Contents', []) if obj['Key'].endswith('.csv')]
    except Exception as e:
        return f'<html><body><p>Error listing S3 files: {str(e)}</p></body></html>'
    
    # Parse CSV files
    endpoints = []
    
    for csv_file in csv_files:
        try:
            obj = s3_client.get_object(Bucket=bucket, Key=csv_file)
            content = obj['Body'].read().decode('utf-8')
            
            for line in content.splitlines():
                line = line.strip()
                if not line:
                    continue
                
                is_active = not line.startswith('#')
                if not is_active:
                    line = line[1:].strip()
                
                if not line or line.startswith('#'):
                    continue
                
                parts = [p.strip() for p in line.split(',')]
                if len(parts) < 8:
                    continue
                
                workload = parts[2]
                endpoint = parts[3]
                origin = parts[4]
                is_dai = parts[5]
                config_file = parts[6]
                technology = parts[1].lower()
                
                endpoints.append({
                    'workload': workload,
                    'endpoint': endpoint,
                    'origin': origin,
                    'is_dai': is_dai,
                    'config_file': config_file,
                    'technology': technology,
                    'status': 'running' if is_active else 'stopped'
                })
        except Exception as e:
            print(f"Error reading {csv_file}: {str(e)}")
    
    # Filter endpoints by selected workload and origin
    filtered = [ep for ep in endpoints if ep['workload'] == selected_workload and ep['origin'] == selected_origin]
    filtered.sort(key=lambda x: (x['endpoint'], x['technology']))
    
    print(f"Filtered endpoints for workload={selected_workload}, origin={selected_origin}: {len(filtered)} endpoints")
    
    rows_html = []
    for ep in filtered:
        # Determine action icon and color based on status
        if ep['status'] == 'running':
            action_icon = '⏹️'
            action_color = ''  # emoji has its own color
            action_size = '18px'
            action_arn = get_lambda_arn(context, 'canary-monitor-stop-monitoring')
            action_title = 'stop'
        else:
            action_icon = '▶️'
            action_color = ''  # emoji has its own color
            action_size = '18px'
            action_arn = get_lambda_arn(context, 'canary-monitor-start-monitoring')
            action_title = 'start'
        
        action_json = {
            "bucket": bucket,
            "workload": ep['workload'],
            "origin": ep['origin'],
            "endpoint": ep['endpoint'],
            "is_dai": ep['is_dai'],
            "technology": ep['technology']
        }
        
        delete_arn = get_lambda_arn(context, 'canary-monitor-delete-endpoint')
        action_cell = f'''<td style="text-align: center;">
<span style="cursor: pointer; font-size: 18px; margin-right: 10px;" title="delete">🗑️</span>
<cwdb-action action="call" endpoint="{delete_arn}">
{{"bucket": "{bucket}", "workload": "{ep['workload']}", "origin": "{ep['origin']}", "endpoint": "{ep['endpoint']}", "is_dai": "{ep['is_dai']}", "technology": "{ep['technology']}"}}
</cwdb-action>
<span style="cursor: pointer; font-size: {action_size};{f" color: {action_color};" if action_color else ""}" title="{action_title}">{action_icon}</span>
<cwdb-action action="call" endpoint="{action_arn}">
{{"bucket": "{bucket}", "workload": "{ep['workload']}", "origin": "{ep['origin']}", "endpoint": "{ep['endpoint']}", "is_dai": "{ep['is_dai']}", "technology": "{ep['technology']}"}}
</cwdb-action>
</td>'''
        
        rows_html.append(
            f'<tr><td>{ep["workload"]}</td><td>{ep["origin"]}</td><td>{ep["endpoint"]}</td><td>{ep["technology"]}</td><td>{ep["config_file"]}</td><td>{ep["is_dai"]}</td>'
            f'<td style="color: {"#558f50" if ep["status"] == "running" else "#ff8531"}; font-weight: bold;">{ep["status"]}</td>'
            f'{action_cell}</tr>'
        )
    
    html = f'''<html>
<head>
    <title>Monitoring Status</title>
    <style>
        button {{
            padding: 10px 20px;
            font-size: 14px;
            cursor: pointer;
            color: white;
            border: none;
            border-radius: 4px;
            font-weight: bold;
            margin: 0 10px;
        }}
        .start-btn {{
            background: #558f50;
        }}
        .stop-btn {{
            background: #ff8531;
        }}
    </style>
</head>
<body style="margin: 0;">
    <div style="text-align: center; padding: 0 0 25px 0;">
        <button class="start-btn">Start all</button>
        <cwdb-action action="call" endpoint="{get_lambda_arn(context, 'canary-monitor-start-monitoring')}">
        {{"bucket": "{bucket}", "workload": "{selected_workload}", "origin": "{selected_origin}"}}
        </cwdb-action>
        <button class="stop-btn">Stop all</button>
        <cwdb-action action="call" endpoint="{get_lambda_arn(context, 'canary-monitor-stop-monitoring')}">
        {{"bucket": "{bucket}", "workload": "{selected_workload}", "origin": "{selected_origin}"}}
        </cwdb-action>
    </div>
    <table border="1" style="width: 100%; border-collapse: collapse;">
        <tr><th>Workload</th><th>Origin</th><th>Endpoint</th><th>Technology</th><th>Config</th><th>Is DAI</th><th>Status</th><th>Action</th></tr>
        {''.join(rows_html)}
    </table>
</body>
</html>'''
    
    return html
