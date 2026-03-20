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
        forms = event.get('widgetContext', {}).get('forms', {}).get('all', {})
        bucket = params.get('bucket', '')
        config_file_value = forms.get('existingConfigFile', params.get('configFile', ''))
        # Handle array from multiple select
        if isinstance(config_file_value, list):
            config_file = config_file_value[0] if config_file_value else ''
        else:
            config_file = config_file_value
    else:
        bucket = event.get('bucket', '')
        config_file = event.get('configFile', '')
    
    if not bucket:
        return error_response('No bucket specified')
    
    if not config_file:
        return error_response('No configuration file selected')
    
    # Prevent editing default.json
    if config_file.lower() == 'default.json':
        return error_response('Cannot edit default.json - this file is protected')
    
    # Read the config file from S3
    s3_client = boto3.client('s3')
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=f'configs/{config_file}')
        current_content = obj['Body'].read().decode('utf-8')
    except Exception as e:
        return error_response(f'Failed to read config file: {str(e)}')
    
    # Format JSON for display
    try:
        formatted_content = json.dumps(json.loads(current_content), indent=2)
    except:
        formatted_content = current_content
    
    html = f'''<html>
<head>
    <title>Edit Configuration</title>
    <style>
        body {{
            margin: 0;
            font-family: Arial, sans-serif;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
        }}
        .container {{
            display: flex;
            gap: 20px;
            align-items: flex-start;
        }}
        textarea {{
            width: 600px !important;
            font-family: monospace;
            font-size: 12px;
            resize: none;
        }}
        
        button {{
            padding: 10px 20px;
            font-size: 14px;
            cursor: pointer;
            background: #49b1e3;
            color: white;
            border: none;
            border-radius: 4px;
            font-weight: bold;
        }}
    </style>
</head>
<body>
    <div class="container">
        <textarea id="newContent" name="newContent" rows="16">{formatted_content}</textarea>
        <div>
            <button>Save</button>
            <cwdb-action action="call" display="popup" endpoint="{get_lambda_arn(context, 'canary-monitor-save-config')}">
            {{"bucket": "{bucket}", "configFile": "{config_file}"}}
            </cwdb-action>
        </div>
    </div>
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
