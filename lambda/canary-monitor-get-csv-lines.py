import boto3
import json

def lambda_handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    # Handle both direct invocation and widget context
    if 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        bucket = params.get('bucket', '')
        workload = params.get('workload', '')
        origin = params.get('origin', '')
    else:
        bucket = event.get('bucket', '')
        workload = event.get('workload', '')
        origin = event.get('origin', '')
    
    if not bucket or not workload or not origin:
        return '<html><body><p>Error: Missing required parameters</p></body></html>'
    
    s3_client = boto3.client('s3')
    
    # Get all CSV files from origins/ folder
    try:
        response = s3_client.list_objects_v2(Bucket=bucket, Prefix='origins/')
        csv_files = [obj['Key'] for obj in response.get('Contents', []) if obj['Key'].endswith('.csv')]
    except Exception as e:
        return f'<html><body><p>Error listing S3 files: {str(e)}</p></body></html>'
    
    matching_lines = []
    
    for csv_file in csv_files:
        try:
            obj = s3_client.get_object(Bucket=bucket, Key=csv_file)
            content = obj['Body'].read().decode('utf-8')
            lines = content.splitlines()
            
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                
                # Check if line is commented
                is_commented = stripped.startswith('#')
                uncommented = stripped[1:].strip() if is_commented else stripped
                
                # Skip header comments
                if uncommented.startswith('#'):
                    continue
                
                parts = [p.strip() for p in uncommented.split(',')]
                
                # Match workload (index 2) and origin (index 4)
                if len(parts) >= 5 and parts[2] == workload and parts[4] == origin:
                    matching_lines.append(line)
        except Exception as e:
            print(f"Error processing {csv_file}: {str(e)}")
    
    if not matching_lines:
        csv_content = f"# No endpoints found for workload '{workload}' and origin '{origin}'"
    else:
        csv_content = '\n'.join(matching_lines)
    
    html = f'''<html>
<head>
    <title>CSV Lines</title>
    <style>
        pre {{
            background: #f5f5f5;
            padding: 15px;
            border: 1px solid #ddd;
            border-radius: 4px;
            overflow-x: auto;
            font-family: monospace;
            font-size: 12px;
        }}
    </style>
</head>
<body style="margin: 20px;">
    <p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">CSV Lines</p>
    <p style="margin-bottom: 20px;">Workload: <strong>{workload}</strong>, Origin: <strong>{origin}</strong></p>
    <pre>{csv_content}</pre>
</body>
</html>'''
    
    return html
