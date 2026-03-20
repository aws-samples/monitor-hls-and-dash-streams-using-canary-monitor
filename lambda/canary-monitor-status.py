import boto3
import json

def lambda_handler(event, context):
    print(f"Event: {json.dumps(event)}")
    
    # Handle both direct invocation and widget context
    if 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        alarm_arn = params.get('alarm', '')
    else:
        alarm_arn = event.get('alarm', '')
    
    if not alarm_arn:
        return '<html><body><p>Error: No alarm ARN specified</p></body></html>'
    
    # Extract alarm name from ARN
    alarm_name = alarm_arn.split(':alarm:')[-1]
    
    # Get alarm state
    cloudwatch_client = boto3.client('cloudwatch')
    try:
        response = cloudwatch_client.describe_alarms(AlarmNames=[alarm_name])
        
        if not response.get('MetricAlarms'):
            return '<html><body><p>Error: Alarm not found</p></body></html>'
        
        alarm = response['MetricAlarms'][0]
        state = alarm['StateValue']
        
        # Determine dot color based on state
        if state == 'OK':
            status_dot = '<span style="color: #cae7ca;">⬤</span>'
        elif state == 'ALARM':
            status_dot = '<span style="color: #f5c9c9;">⬤</span>'
        else:  # INSUFFICIENT_DATA
            status_dot = '<span style="color: #c7c7c7;">⬤</span>'
        
        html = f'''<html>
<head><title>Status</title></head>
<body style="margin: 0;">
    <p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Status</p>
    <p style="text-align: center; font-size: 2em; margin: 0;">{status_dot}</p>
</body>
</html>'''
        
        return html
        
    except Exception as e:
        return f'<html><body><p>Error checking alarm status: {str(e)}</p></body></html>'
