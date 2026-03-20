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
    bucket = event.get('bucket', '')
    config_file = event.get('configFile', '')
    confirm = event.get('confirm', '')
    
    if 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        forms = event.get('widgetContext', {}).get('forms', {}).get('all', {})
        if not bucket:
            bucket = params.get('bucket', '')
        if not config_file:
            config_file_value = forms.get('existingConfigFile', params.get('configFile', ''))
            # Handle array from multiple select
            if isinstance(config_file_value, list):
                config_file = config_file_value[0] if config_file_value else ''
            else:
                config_file = config_file_value
        if not confirm:
            confirm = params.get('confirm', '')
    
    if not bucket:
        return error_response('No bucket specified')
    
    if not config_file:
        return error_response('No configuration file selected')
    
    # Prevent deleting default.json
    if config_file.lower() == 'default.json':
        return error_response('Cannot delete default.json - this file is protected')
    
    delete_config_arn = get_lambda_arn(context, 'canary-monitor-delete-config')
    manage_configs_arn = get_lambda_arn(context, 'canary-monitor-manage-configurations')
    
    # If not confirmed, show confirmation page
    if confirm != 'yes':
        html = f'''<html>
<head><title>Confirm Delete</title></head>
<body style="margin: 20px;">
    <p>Are you sure you want to delete configuration file '{config_file}'?</p>
    <br>
    <b style="cursor: pointer; margin-right: 20px;">Yes, delete</b>
    <cwdb-action action="call" endpoint="{delete_config_arn}">
    {{"bucket": "{bucket}", "configFile": "{config_file}", "confirm": "yes"}}
    </cwdb-action>
    <b style="cursor: pointer;">No</b>
    <cwdb-action action="call" display="popup" endpoint="{manage_configs_arn}">
    {{"bucket": "{bucket}"}}
    </cwdb-action>
</body>
</html>'''
        return html
    
    # Delete from S3
    s3_client = boto3.client('s3')
    try:
        s3_client.delete_object(Bucket=bucket, Key=f'configs/{config_file}')
        return success_response(config_file)
    except Exception as e:
        return error_response(f'Failed to delete config file: {str(e)}')


def success_response(config_file):
    return f'''<html>
<head><title>Success</title></head>
<body style="margin: 20px;">
    <h2 style="color: #558f50; margin-bottom: 30px;">✓ Success</h2>
    <p>Successfully deleted <strong>{config_file}</strong></p>
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
