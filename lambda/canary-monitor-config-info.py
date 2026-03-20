import boto3
import base64

def lambda_handler(event, context):
    # Get bucket from widget context
    if 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        bucket = params.get('bucket', '')
    else:
        bucket = event.get('bucket', '')
    
    if not bucket:
        return '<html><body><p>Error: No bucket specified in context</p></body></html>'
    
    key = 'deploy/config_info.jpg'
    
    try:
        s3_client = boto3.client('s3')
        response = s3_client.get_object(Bucket=bucket, Key=key)
        image_data = response['Body'].read()
        image_base64 = base64.b64encode(image_data).decode('utf-8')
        
        html = f'''<html>
<head>
    <title>Configuration Info</title>
    <style>
        body {{
            margin: 0;
            padding: 20px;
            text-align: center;
        }}
        img {{
            max-width: 100%;
            height: auto;
        }}
    </style>
</head>
<body>
    <img src="data:image/jpeg;base64,{image_base64}" alt="Configuration Info" />
</body>
</html>'''
        
        return html
    except Exception as e:
        return f'''<html>
<head><title>Error</title></head>
<body style="margin: 20px;">
    <h2 style="color: #ff6361;">Error</h2>
    <p>Failed to load image: {str(e)}</p>
</body>
</html>'''
