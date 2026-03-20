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
    workload = event.get('workload', '')
    origin = event.get('origin', '')
    confirm = event.get('confirm', '')
    delete_s3 = ''
    delete_dashboard = ''
    
    # If not in direct params, check widgetContext
    if not bucket and 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        bucket = params.get('bucket', '')
        workload = params.get('workload', '')
        origin = params.get('origin', '')
        confirm = params.get('confirm', '')
    
    # Always get form values from widgetContext.forms.all
    if 'widgetContext' in event:
        forms = event.get('widgetContext', {}).get('forms', {}).get('all', {})
        delete_s3 = forms.get('deleteS3', '')
        delete_dashboard = forms.get('deleteDashboard', '')
    
    print(f"Bucket: {bucket}, Workload: {workload}, Origin: {origin}, Confirm: {confirm}, Delete S3: '{delete_s3}', Delete Dashboard: '{delete_dashboard}'")
    
    if not bucket or not workload or not origin:
        return '<html><body><p>Error: Missing required parameters</p></body></html>'
    
    # If confirmed as "no", show cancellation message
    if confirm == 'no':
        return f'''<html>
<head><title>Delete Cancelled</title></head>
<body style="margin: 20px;">
    <p>Deletion cancelled. No endpoints were deleted.</p>
</body>
</html>'''
    
    # If not confirmed, show confirmation page
    if confirm != 'yes':
        delete_arn = get_lambda_arn(context, 'canary-monitor-delete-endpoints')
        
        dashboard_name = f"{workload}_{origin}_canary-monitor"
        s3_location = f"s3://{bucket}/archive/live/{workload}/{origin}/"
        
        return f'''<html>
<head>
    <title>Confirm Delete</title>
    <style>
        body {{
            margin: 0;
        }}
        .form-group {{
            margin-bottom: 15px;
            padding: 0 20px;
            margin-top: 20px;
        }}
        label {{
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
        }}
        select {{
            width: 100%;
            max-width: 400px;
            padding: 10px;
            font-size: 14px;
            border-radius: 4px;
        }}
    </style>
</head>
<body style="margin: 0;">
    <p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Confirm Delete</p>
    
    <p style="padding: 0 20px;">Are you sure?</p>
    
    <div class="form-group">
        <label style="font-weight: bold;">Delete S3 data:</label>
        <select id="deleteS3" name="deleteS3">
            <option value="yes">Yes</option>
            <option value="no">No</option>
        </select>
    </div>
    
    <div class="form-group">
        <label style="font-weight: bold;">Delete CW dashboard:</label>
        <select id="deleteDashboard" name="deleteDashboard">
            <option value="yes">Yes</option>
            <option value="no">No</option>
        </select>
    </div>
    
    <div style="padding: 0 20px; margin-top: 30px;">
        <b style="cursor: pointer; margin-right: 20px;">Yes, delete</b>
        <cwdb-action action="call" endpoint="{delete_arn}">
        {{"bucket": "{bucket}", "workload": "{workload}", "origin": "{origin}", "confirm": "yes"}}
        </cwdb-action>
        <b style="cursor: pointer;">No</b>
        <cwdb-action action="call" endpoint="{delete_arn}">
        {{"bucket": "{bucket}", "workload": "{workload}", "origin": "{origin}", "confirm": "no"}}
        </cwdb-action>
    </div>
</body>
</html>'''
    
    # Confirmed - proceed with deletion
    s3_client = boto3.client('s3')
    
    # Get all CSV files from origins/ folder
    try:
        response = s3_client.list_objects_v2(Bucket=bucket, Prefix='origins/')
        csv_files = [obj['Key'] for obj in response.get('Contents', []) if obj['Key'].endswith('.csv')]
        print(f"Found CSV files: {csv_files}")
    except Exception as e:
        error_msg = f'Error listing S3 files: {str(e)}'
        print(error_msg)
        return f'<html><body><p>{error_msg}</p></body></html>'
    
    modified_files = []
    
    for csv_file in csv_files:
        try:
            obj = s3_client.get_object(Bucket=bucket, Key=csv_file)
            content = obj['Body'].read().decode('utf-8')
            etag = obj['ETag']
            lines = content.splitlines()
            modified = False
            
            print(f"Processing {csv_file} with {len(lines)} lines")
            
            new_lines = []
            for i, line in enumerate(lines):
                stripped = line.strip()
                if not stripped:
                    new_lines.append(line)
                    continue
                
                # Handle commented lines
                is_commented = stripped.startswith('#')
                uncommented = stripped[1:].strip() if is_commented else stripped
                
                if not uncommented or uncommented.startswith('#'):
                    new_lines.append(line)
                    continue
                
                parts = [p.strip() for p in uncommented.split(',')]
                
                # Match workload AND origin
                if len(parts) >= 5 and parts[2] == workload and parts[4] == origin:
                    print(f"Match on line {i}: workload={parts[2]}, origin={parts[4]}")
                    print(f"Deleting line {i}: {line}")
                    modified = True
                    # Skip this line (don't add to new_lines)
                else:
                    new_lines.append(line)
            
            if modified:
                new_content = '\n'.join(new_lines)
                print(f"Writing modified content back to {csv_file}")
                try:
                    s3_client.put_object(
                        Bucket=bucket,
                        Key=csv_file,
                        Body=new_content.encode('utf-8'),
                        IfMatch=etag
                    )
                    modified_files.append(csv_file)
                except s3_client.exceptions.ClientError as e:
                    if e.response['Error']['Code'] == 'PreconditionFailed':
                        return error_response('File was modified by another user. Please refresh and try again.')
                    raise
        except Exception as e:
            error_msg = f"Error processing {csv_file}: {str(e)}"
            print(error_msg)
    
    if modified_files:
        message = f"Deleted all endpoints for workload '{workload}' and origin '{origin}'."
    else:
        message = f"No endpoints found for workload '{workload}' and origin '{origin}'."
    
    # Handle S3 data deletion via background Lambda
    if delete_s3 == 'yes':
        try:
            lambda_client = boto3.client('lambda')
            background_lambda_arn = get_lambda_arn(context, 'canary-monitor-background-delete')
            
            payload = {
                'bucket': bucket,
                's3_prefix': f"archive/live/{workload}/{origin}/"
            }
            
            lambda_client.invoke(
                FunctionName=background_lambda_arn,
                InvocationType='Event',  # Asynchronous
                Payload=json.dumps(payload)
            )
            
            message += " S3 deletion started in background."
            print(f"Invoked background deletion for s3://{bucket}/{payload['s3_prefix']}")
        except Exception as e:
            print(f"Error invoking background deletion: {str(e)}")
            message += f" Error starting S3 deletion: {str(e)}"
    
    # Handle CloudWatch dashboard deletion
    if delete_dashboard == 'yes':
        try:
            dashboard_name = f"{workload}_{origin}_canary-monitor"
            print(f"Deleting CloudWatch dashboard: {dashboard_name}")
            
            cloudwatch_client = boto3.client('cloudwatch')
            cloudwatch_client.delete_dashboards(DashboardNames=[dashboard_name])
            message += f" Deleted CloudWatch dashboard '{dashboard_name}'."
        except cloudwatch_client.exceptions.ResourceNotFoundException:
            message += f" CloudWatch dashboard '{dashboard_name}' not found."
        except Exception as e:
            print(f"Error deleting CloudWatch dashboard: {str(e)}")
            message += f" Error deleting CloudWatch dashboard: {str(e)}"
    
    html = f'''<html>
<head><title>Delete Endpoints</title></head>
<body style="margin: 20px;">
    <p>{message}</p>
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
