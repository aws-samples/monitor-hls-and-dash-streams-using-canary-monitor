import boto3
import json
from collections import defaultdict

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
        domain = event.get('widgetContext', {}).get('domain', '')
    else:
        bucket = event.get('bucket', '')
        domain = ''
    
    if not bucket:
        return '<html><body><p>Error: No bucket specified in context</p></body></html>'
    
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
                technology = parts[1].lower()
                
                endpoints.append({
                    'workload': workload,
                    'endpoint': endpoint,
                    'origin': origin,
                    'technology': technology,
                    'status': 'active' if is_active else 'inactive'
                })
        except Exception as e:
            print(f"Error reading {csv_file}: {str(e)}")
    
    # Show summary table grouped by workload/origin
    grouped = defaultdict(lambda: {'active': 0, 'inactive': 0})
    for ep in endpoints:
        key = (ep['workload'], ep['origin'])
        grouped[key][ep['status']] += 1
    
    # Get CloudWatch client and account ID
    cloudwatch_client = boto3.client('cloudwatch')
    account_id = context.invoked_function_arn.split(':')[4]
    region = context.invoked_function_arn.split(':')[3]
    
    # List all dashboards with pagination
    existing_dashboards = set()
    next_token = None
    try:
        while True:
            if next_token:
                response = cloudwatch_client.list_dashboards(NextToken=next_token)
            else:
                response = cloudwatch_client.list_dashboards()
            
            existing_dashboards.update(dash['DashboardName'] for dash in response.get('DashboardEntries', []))
            next_token = response.get('NextToken')
            if not next_token:
                break
    except Exception as e:
        print(f"Error listing dashboards: {str(e)}")
    
    rows_html = []
    for (workload, origin), counts in sorted(grouped.items()):
        running = counts['active']
        stopped = counts['inactive']
        
        # Endpoints count cell with running/stopped breakdown
        endpoints_cell = f'<td><span style="color: #558f50; font-weight: bold;">{running}</span> / <span style="color: #ff8531; font-weight: bold;">{stopped}</span></td>'
        
        dashboard_name = f"{workload}_{origin}_canary-monitor"
        has_dashboard = dashboard_name in existing_dashboards
        
        # Actions cell with delete, manage, archive, and dashboard icons
        actions = []
        
        delete_arn = get_lambda_arn(context, 'canary-monitor-delete-endpoints')
        manage_arn = get_lambda_arn(context, 'canary-monitor-manage-endpoint-stop-start')
        csv_lines_arn = get_lambda_arn(context, 'canary-monitor-get-csv-lines')
        
        # Delete icon (first)
        actions.append(f'''<span style="cursor: pointer; font-size: 18px; margin-right: 10px;" title="delete">🗑️</span>
<cwdb-action action="call" display="popup" endpoint="{delete_arn}">
{{"bucket": "{bucket}", "workload": "{workload}", "origin": "{origin}"}}
</cwdb-action>''')
        
        # CSV lines icon (second)
        actions.append(f'''<span style="cursor: pointer; font-size: 18px; margin-right: 10px;" title="csv lines">📝</span>
<cwdb-action action="call" display="popup" endpoint="{csv_lines_arn}">
{{"bucket": "{bucket}", "workload": "{workload}", "origin": "{origin}"}}
</cwdb-action>''')
        
        # Manage icon (third)
        actions.append(f'''<span style="cursor: pointer; font-size: 18px; margin-right: 10px;" title="start/stop">⚙️</span>
<cwdb-action action="call" display="popup" endpoint="{manage_arn}">
{{"bucket": "{bucket}", "workload": "{workload}", "origin": "{origin}"}}
</cwdb-action>''')
        
        # Archive icon (fourth) - S3 console link (always AWS console)
        if 'console.aws.amazon.com' in domain:
            s3_url = f"{domain}/s3/buckets/{bucket}?region={region}&prefix=archive/live/{workload}/{origin}/&showversions=false"
        else:
            # Fallback if domain is internal - use Lambda context region
            s3_url = f"https://{region}.console.aws.amazon.com/s3/buckets/{bucket}?region={region}&prefix=archive/live/{workload}/{origin}/&showversions=false"
        actions.append(f'<a href="{s3_url}" target="_blank" style="font-size: 18px; text-decoration: none; margin-right: 15px;" title="archive">🗂️</a>')
        
        # Dashboard icon (last, if available)
        if has_dashboard:
            # Build dashboard URL based on domain
            if 'console.aws.amazon.com' in domain:
                # Extract region from domain
                region = domain.split('.console.aws.amazon.com')[0].split('.')[-1]
                dashboard_url = f"{domain}/cloudwatch/home?region={region}#dashboards/dashboard/{dashboard_name}"
            else:
                # Default to internal dashboard
                dashboard_url = f"{domain}/cloudwatch/dashboardInternal?accountId={account_id}#dashboards/dashboard/{dashboard_name}"
            actions.append(f'<a href="{dashboard_url}" target="_blank" style="font-size: 18px; text-decoration: none;" title="dashboard">📊</a>')
        
        action_cell = f'<td style="text-align: center;">{"".join(actions)}</td>'
        
        rows_html.append(f'<tr><td>{workload}</td><td>{origin}</td>{endpoints_cell}{action_cell}</tr>')
    
    html = f'''<html>
<head><title>Monitoring Status</title></head>
<body style="margin: 0;">
    <p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Workloads</p>
    <table border="1" style="width: 100%; border-collapse: collapse;">
        <tr><th style="text-align: left;">Workload</th><th style="text-align: left;">Origin</th><th style="text-align: left;">Endpoints</th><th style="text-align: left;">Action</th></tr>
        {''.join(rows_html)}
    </table>
</body>
</html>'''
    
    return html
