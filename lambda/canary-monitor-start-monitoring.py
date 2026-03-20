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
    # Check direct parameters first (from action), then fall back to widgetContext
    bucket = event.get('bucket', '')
    workload = event.get('workload', '')
    origin = event.get('origin', '')
    endpoint = event.get('endpoint', '')
    is_dai = event.get('is_dai', '')
    technology = event.get('technology', '')
    
    # If not in direct params, check widgetContext
    if not bucket and 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        bucket = params.get('bucket', '')
        workload = params.get('workload', '')
        origin = params.get('origin', '')
        endpoint = params.get('endpoint', '')
        is_dai = params.get('is_dai', '')
        technology = params.get('technology', '')
    
    print(f"Bucket: {bucket}, Workload: {workload}, Origin: {origin}, Endpoint: {endpoint}, Is DAI: {is_dai}, Technology: {technology}")
    
    # Determine mode
    if endpoint:
        print(f"MODE: Single endpoint")
    else:
        print(f"MODE: Bulk workload/origin")
    
    if not bucket:
        return '<html><body><p>Error: Missing bucket parameter</p></body></html>'
    
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
            
            for i, line in enumerate(lines):
                stripped = line.strip()
                if not stripped or not stripped.startswith('#'):
                    continue
                
                uncommented = stripped[1:].strip()
                if not uncommented or uncommented.startswith('#'):
                    continue
                
                parts = [p.strip() for p in uncommented.split(',')]
                
                # Check if this line matches our criteria
                match = False
                # Prioritize single endpoint mode if endpoint is provided
                if endpoint:
                    # Single endpoint mode - must match endpoint, technology, is_dai, workload AND origin
                    if len(parts) >= 6 and parts[3] == endpoint and parts[1] == technology and parts[5] == is_dai and parts[2] == workload and parts[4] == origin:
                        match = True
                        print(f"Single endpoint match on line {i}: endpoint={parts[3]}, technology={parts[1]}, is_dai={parts[5]}")
                elif workload and origin:
                    # Bulk mode - must match both workload AND origin
                    if len(parts) >= 5 and parts[2] == workload and parts[4] == origin:
                        match = True
                        print(f"Bulk match on line {i}: workload={parts[2]}, origin={parts[4]}")
                
                if match:
                    print(f"Uncommenting line {i}: {line}")
                    lines[i] = uncommented
                    modified = True
            
            if modified:
                new_content = '\n'.join(lines)
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
        if endpoint:
            message = f"Started monitoring for endpoint '{endpoint}'."
        else:
            message = f"Started monitoring for workload '{workload}' and origin '{origin}'."
    else:
        if endpoint:
            message = f"No stopped endpoint found for '{endpoint}'."
        else:
            message = f"No stopped endpoints found for workload '{workload}' and origin '{origin}'."
    
    # If workload and origin are provided, show reload option
    if workload and origin:
        html = f'''<html>
<head>
    <title>Start Monitoring</title>
</head>
<body style="margin: 20px;">
    <p>{message}</p>
    <div style="margin-top: 25px;">
        <b style="cursor: pointer;">Go back</b>
        <cwdb-action action="call" endpoint="{get_lambda_arn(context, 'canary-monitor-manage-endpoint-stop-start')}">
        {{"bucket": "{bucket}", "workload": "{workload}", "origin": "{origin}"}}
        </cwdb-action>
    </div>
</body>
</html>'''
    else:
        html = f'''<html>
<head><title>Start Monitoring</title></head>
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
