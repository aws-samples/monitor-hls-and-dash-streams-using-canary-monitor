import boto3
import json

def lambda_handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    # Handle both direct invocation and widget context
    if 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        bucket = params.get('bucket', '')
    else:
        bucket = event.get('bucket', '')
    
    if not bucket:
        return '<html><body><p>Error: No bucket specified in context</p></body></html>'
    
    # Get default.json content
    s3_client = boto3.client('s3')
    default_content = ''
    try:
        obj = s3_client.get_object(Bucket=bucket, Key='configs/default.json')
        default_json = obj['Body'].read().decode('utf-8')
        default_content = json.dumps(json.loads(default_json), indent=2)
    except Exception as e:
        default_content = f'Error loading default.json: {str(e)}'
    
    html = f'''<html>
<head>
    <title>Default Configuration</title>
    <style>
        body {{
            margin: 20px;
            font-family: Arial, sans-serif;
        }}
        .content-display {{
            background: #f5f5f5;
            padding: 15px;
            border-radius: 8px;
            font-family: monospace;
            font-size: 12px;
            white-space: pre-wrap;
            word-wrap: break-word;
            max-height: 500px;
            overflow-y: auto;
        }}
    </style>
</head>
<body>
    <div class="content-display">{default_content}</div>
</body>
</html>'''
    
    return html
