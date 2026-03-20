import boto3
import json

def lambda_handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    # Handle both direct invocation and widget context
    if 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        forms = event.get('widgetContext', {}).get('forms', {}).get('all', {})
        bucket = params.get('bucket', '')
        
        endpoint_type = 'live'  # Always live
        technology = forms.get('technology', '').strip()
        workload_name = forms.get('workloadName', '').strip()
        endpoint_name = forms.get('endpointName', '').strip()
        origin_name = forms.get('originName', '').strip()
        is_dai = forms.get('isDai', '').strip()
        config_file = forms.get('configFile', '').strip()
        manifest_url = forms.get('manifestUrl', '').strip()
        tracking_url = forms.get('trackingUrl', '').strip()
    else:
        bucket = event.get('bucket', '')
        endpoint_type = 'live'  # Always live
        technology = event.get('technology', '').strip()
        workload_name = event.get('workloadName', '').strip()
        endpoint_name = event.get('endpointName', '').strip()
        origin_name = event.get('originName', '').strip()
        is_dai = event.get('isDai', '').strip()
        config_file = event.get('configFile', '').strip()
        manifest_url = event.get('manifestUrl', '').strip()
        tracking_url = event.get('trackingUrl', '').strip()
    
    if not bucket:
        return error_response('No bucket specified')
    
    # Validate technology
    if technology.lower() not in ['hls', 'dash']:
        return error_response('Technology must be "hls" or "dash"')
    
    # Validate is_dai
    if is_dai.lower() not in ['true', 'false']:
        return error_response('Is DAI must be "true" or "false"')
    
    # Validate required fields
    if not workload_name:
        return error_response('Workload name is required')
    
    if not endpoint_name:
        return error_response('Endpoint name is required')
    
    if not origin_name:
        return error_response('Origin name is required')
    
    if not config_file:
        return error_response('Config file is required')
    
    if not manifest_url:
        return error_response('Manifest URL is required')
    
    # Validate manifest URL matches technology
    if technology.lower() == 'hls' and '.m3u8' not in manifest_url:
        return error_response('Manifest URL must contain .m3u8 for HLS technology')
    
    if technology.lower() == 'dash' and '.mpd' not in manifest_url:
        return error_response('Manifest URL must contain .mpd for DASH technology')
    
    # Validate config file exists in S3
    s3_client = boto3.client('s3')
    try:
        s3_client.head_object(Bucket=bucket, Key=f'configs/{config_file}')
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == '404':
            return error_response(f'Config file "{config_file}" does not exist in configs folder')
        else:
            return error_response(f'Error checking config file: {str(e)}')
    
    # Build CSV line
    csv_line = f"{endpoint_type.lower()}, {technology.lower()}, {workload_name}, {endpoint_name}, {origin_name}, {is_dai.lower()}, {config_file}, {manifest_url}"
    if tracking_url:
        csv_line += f", {tracking_url}"
    
    # Append to from-lambda.csv
    try:
        # Try to get existing file with ETag
        etag = None
        try:
            obj = s3_client.get_object(Bucket=bucket, Key='origins/from-lambda.csv')
            existing_content = obj['Body'].read().decode('utf-8')
            etag = obj['ETag']
        except s3_client.exceptions.NoSuchKey:
            existing_content = ''
        
        # Parse existing lines and check for duplicate endpoint
        endpoint_id = (endpoint_type.lower(), technology.lower(), workload_name, endpoint_name, origin_name, is_dai.lower())
        existing_lines = []
        is_update = False
        
        for line in existing_content.splitlines():
            check_line = line.strip()
            if check_line.startswith('#'):
                check_line = check_line[1:].strip()
            
            should_keep = True
            if check_line and not check_line.startswith('#'):
                parts = [p.strip() for p in check_line.split(',')]
                if len(parts) >= 6:
                    line_endpoint_id = tuple(parts[:6])
                    if line_endpoint_id == endpoint_id:
                        should_keep = False
                        is_update = True
            
            if should_keep:
                existing_lines.append(line)
        
        # Build new content
        new_content = '\n'.join(existing_lines)
        if new_content and not new_content.endswith('\n'):
            new_content += '\n'
        new_content += csv_line + '\n'
        
        # Save back to S3 with conditional write
        put_params = {
            'Bucket': bucket,
            'Key': 'origins/from-lambda.csv',
            'Body': new_content.encode('utf-8'),
            'ContentType': 'text/csv'
        }
        if etag:
            put_params['IfMatch'] = etag
        
        s3_client.put_object(**put_params)
        
        return success_response(is_update)
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == 'PreconditionFailed':
            return error_response('File was modified by another user. Please refresh and try again.')
        return error_response(f'Failed to save origin: {str(e)}')
    except Exception as e:
        return error_response(f'Failed to save origin: {str(e)}')


def success_response(is_update):
    action = "Updated" if is_update else "Added"
    return f'''<html>
<head><title>Success</title></head>
<body style="margin: 20px;">
    <h2 style="color: #558f50; margin-bottom: 30px;">✓ Success</h2>
    <p>Successfully {action.lower()} origin endpoint</p>
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
