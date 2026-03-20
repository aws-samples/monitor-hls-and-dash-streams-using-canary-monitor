import boto3
import json

def lambda_handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    # Handle both direct invocation and widget context
    if 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        forms = event.get('widgetContext', {}).get('forms', {}).get('all', {})
        bucket = params.get('bucket', event.get('bucket', ''))
        # Check top-level event first (from edit-config), then params, then forms (from add new)
        config_file = event.get('configFile', params.get('configFile', forms.get('newConfigFile', forms.get('existingConfigFile', ''))))
        new_content = forms.get('newContent', '')
        is_new = 'newConfigFile' in forms  # True if coming from "Add" tab
    else:
        bucket = event.get('bucket', '')
        config_file = event.get('configFile', '')
        new_content = event.get('newContent', '')
        is_new = False
    
    if not bucket:
        return error_response('No bucket specified')
    
    if not config_file:
        return error_response('No configuration file specified')
    
    # Check if file has .json extension
    if not config_file.lower().endswith('.json'):
        return error_response('Configuration file must have .json extension')
    
    # Prevent editing default.json
    if config_file.lower() == 'default.json':
        return error_response('Cannot create or edit default.json - this file is protected')
    
    if not new_content:
        return error_response('No content provided')
    
    # Check if file exists when adding new
    if is_new:
        s3_client = boto3.client('s3')
        try:
            s3_client.head_object(Bucket=bucket, Key=f'configs/{config_file}')
            return error_response(f'Configuration file "{config_file}" already exists. Please use a different name or edit the existing file.')
        except s3_client.exceptions.ClientError as e:
            if e.response['Error']['Code'] != '404':
                return error_response(f'Error checking file existence: {str(e)}')
            # File doesn't exist, continue with creation
    
    # Validate JSON
    try:
        json.loads(new_content)
    except json.JSONDecodeError as e:
        return error_response(f'Invalid JSON format: {str(e)}')
    
    # Save to S3
    s3_client = boto3.client('s3')
    try:
        s3_client.put_object(
            Bucket=bucket,
            Key=f'configs/{config_file}',
            Body=new_content.encode('utf-8'),
            ContentType='application/json'
        )
        return success_response(config_file)
    except Exception as e:
        return error_response(f'Failed to save config file: {str(e)}')


def success_response(config_file):
    return f'''<html>
<head><title>Success</title></head>
<body style="margin: 20px;">
    <h2 style="color: #558f50; margin-bottom: 30px;">✓ Success</h2>
    <p>Successfully saved <strong>{config_file}</strong></p>
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
