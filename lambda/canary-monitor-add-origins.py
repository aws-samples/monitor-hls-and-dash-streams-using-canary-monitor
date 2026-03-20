import boto3
import json
import re

def lambda_handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    # Handle both direct invocation and widget context
    if 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        forms = event.get('widgetContext', {}).get('forms', {}).get('all', {})
        bucket = params.get('bucket', '')
        csv_lines = forms.get('csvLines', '')  # Get from forms, not params
        single_entry = params.get('singleEntry', {})
        filename = params.get('filename', 'from-lambda.csv')
    else:
        bucket = event.get('bucket', '')
        csv_lines = event.get('csvLines', '')
        single_entry = event.get('singleEntry', {})
        filename = event.get('filename', 'from-lambda.csv')
    
    if not bucket:
        return error_response('No bucket specified')
    
    print(f"CSV lines provided: {csv_lines}")
    
    s3_client = boto3.client('s3')
    
    # Determine input type
    if csv_lines:
        lines_to_add = [line.strip() for line in csv_lines.strip().split('\n') if line.strip() and not line.strip().startswith('#')]
    elif single_entry:
        # Build CSV line from single entry
        line = f"{single_entry.get('type', '')}, {single_entry.get('technology', '')}, {single_entry.get('workload', '')}, {single_entry.get('endpoint', '')}, {single_entry.get('origin', '')}, {single_entry.get('isMediaTailor', '')}, {single_entry.get('configFile', '')}, {single_entry.get('manifestUrl', '')}"
        if single_entry.get('trackingUrl'):
            line += f", {single_entry['trackingUrl']}"
        lines_to_add = [line]
    else:
        return error_response('No CSV lines or single entry provided')
    
    # Validate and process lines
    try:
        # Read existing file or create new with ETag
        csv_key = f'origins/{filename}'
        etag = None
        try:
            obj = s3_client.get_object(Bucket=bucket, Key=csv_key)
            existing_content = obj['Body'].read().decode('utf-8')
            etag = obj['ETag']
        except s3_client.exceptions.NoSuchKey:
            existing_content = ''
        
        # Get existing endpoints from this file only
        existing_endpoints = {}
        existing_lines = []
        for line_num, line in enumerate(existing_content.splitlines()):
            existing_lines.append(line)
            check_line = line.strip()
            if check_line.startswith('#'):
                check_line = check_line[1:].strip()
            if check_line and not check_line.startswith('#'):
                parts = [p.strip() for p in check_line.split(',')]
                if len(parts) >= 6:
                    endpoint_id = tuple(parts[:6])
                    existing_endpoints[endpoint_id] = line_num
        
        # Validate ALL lines first - if any fail, don't make any changes
        errors = []
        valid_lines = []
        line_statuses = []
        
        for idx, line in enumerate(lines_to_add, 1):
            validation_error = validate_line(line, set(), s3_client, bucket)
            if validation_error:
                errors.append(f"Line {idx}: {validation_error}")
            else:
                valid_lines.append(line)
                parts = [p.strip() for p in line.split(',')]
                endpoint_id = tuple(parts[:6])
                is_update = endpoint_id in existing_endpoints
                line_statuses.append({'line': line, 'is_update': is_update})
        
        # If ANY validation failed, return error without making changes
        if errors:
            return error_response('<br>'.join(errors))
        
        # All validations passed - remove duplicates and add new lines
        new_endpoint_ids = set()
        for line in valid_lines:
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 6:
                new_endpoint_ids.add(tuple(parts[:6]))
        
        # Remove duplicate lines from existing content
        updated_lines = []
        for line_num, line in enumerate(existing_lines):
            check_line = line.strip()
            if check_line.startswith('#'):
                check_line = check_line[1:].strip()
            
            should_keep = True
            if check_line and not check_line.startswith('#'):
                parts = [p.strip() for p in check_line.split(',')]
                if len(parts) >= 6:
                    endpoint_id = tuple(parts[:6])
                    if endpoint_id in new_endpoint_ids:
                        should_keep = False
            
            if should_keep:
                updated_lines.append(line)
        
        # Build final content
        updated_content = '\n'.join(updated_lines)
        if updated_content and not updated_content.endswith('\n'):
            updated_content += '\n'
        
        # Append new lines uncommented
        new_lines = '\n'.join(valid_lines)
        updated_content += new_lines + '\n'
        
        # Upload updated CSV with conditional write
        put_params = {
            'Bucket': bucket,
            'Key': csv_key,
            'Body': updated_content.encode('utf-8'),
            'ContentType': 'text/csv'
        }
        if etag:
            put_params['IfMatch'] = etag
        
        s3_client.put_object(**put_params)
        
        return success_response(bucket, filename, line_statuses)
        
    except s3_client.exceptions.ClientError as e:
        if e.response['Error']['Code'] == 'PreconditionFailed':
            return error_response('File was modified by another user. Please refresh and try again.')
        return error_response(f'Failed to create entries: {str(e)}')
    except Exception as e:
        return error_response(f'Failed to create entries: {str(e)}')


def validate_line(line, existing_endpoints, s3_client, bucket):
    """Validate CSV line format and check for duplicates"""
    parts = [p.strip() for p in line.split(',')]
    
    # Must have at least 8 columns
    if len(parts) < 8:
        return f"Must have at least 8 columns (found {len(parts)}): {line}"
    
    # Column 1: type (live or vod)
    if parts[0].lower() not in ['live', 'vod']:
        return f"Column 1 must be 'live' or 'vod', found '{parts[0]}'"
    
    # Column 2: technology (hls or dash)
    if parts[1].lower() not in ['hls', 'dash']:
        return f"Column 2 must be 'hls' or 'dash', found '{parts[1]}'"
    
    # Column 6: is mediatailor (true or false)
    if parts[5].lower() not in ['true', 'false']:
        return f"Column 6 must be 'true' or 'false', found '{parts[5]}'"
    
    # Column 7: config file must exist in bucket/configs
    config_filename = parts[6]
    config_file = f'configs/{config_filename}'
    
    try:
        s3_client.head_object(Bucket=bucket, Key=config_file)
    except:
        return f"Config file '{config_filename}' does not exist in bucket/configs folder"
    
    # Column 8: must be a URL
    manifest_url = parts[7]
    if not re.match(r'^https?://', manifest_url):
        return f"Column 8 must be a valid URL, found '{manifest_url}'"
    
    # Check for duplicate endpoint (first 6 columns)
    endpoint_id = tuple(parts[:6])
    if endpoint_id in existing_endpoints:
        return f"Endpoint already exists: {', '.join(parts[:6])}"
    
    return None


def success_response(bucket, filename, line_statuses):
    added = [status for status in line_statuses if not status['is_update']]
    updated = [status for status in line_statuses if status['is_update']]
    
    message = f'''<html>
<head><title>Success</title></head>
<body style="margin: 20px;">
    <h2 style="color: #558f50; margin-bottom: 30px;">✓ Success</h2>'''
    
    if added:
        message += f'<p style="font-size: 16px; font-weight: bold; margin-bottom: 10px;">Added ({len(added)}):</p><ul style="font-family: monospace; font-size: 12px; margin-top: 0;">'
        for item in added:
            message += f'<li>{item["line"]}</li>'
        message += '</ul>'
    
    if updated:
        message += f'<p style="font-size: 16px; font-weight: bold; margin-bottom: 10px; margin-top: 20px;">Updated ({len(updated)}):</p><ul style="font-family: monospace; font-size: 12px; margin-top: 0;">'
        for item in updated:
            message += f'<li>{item["line"]}</li>'
        message += '</ul>'
    
    message += '''</body>
</html>'''
    return message


def error_response(message):
    return f'''<html>
<head><title>Error</title></head>
<body style="margin: 20px;">
    <h2 style="color: #ff6361; margin-bottom: 30px;">✗ Error</h2>
    <p>{message}</p>
</body>
</html>'''
