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
        type_val = params.get('type', 'live')
        workload = params.get('workload', '')
        origin = params.get('origin', '')
        endpoints = params.get('endpoints', [])
    else:
        type_val = event.get('type', 'live')
        workload = event.get('workload', '')
        origin = event.get('origin', '')
        endpoints = event.get('endpoints', [])
    
    # Build endpoint dropdown options - only unique endpoint names
    unique_endpoints = sorted(set(endpoints))
    endpoint_options = '<option value="">*</option>'
    for ep in unique_endpoints:
        endpoint_options += f'<option value="{ep}">{ep}</option>'
    
    html = f'''<html>
<head>
    <title>Search Logs</title>
    <style>
        body {{
            margin: 0;
        }}
        .form-group {{
            margin-bottom: 15px;
            padding: 0 20px;
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
        .checkbox-group {{
            display: flex;
            gap: 15px;
        }}
        .checkbox-group label {{
            display: inline;
            font-weight: normal;
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
            margin-top: 10px;
            margin-left: 20px;
        }}
    </style>
</head>
<body style="margin: 0;">
    <p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Logs</p>
    
    <div class="form-group">
        <label style="display: block; margin-bottom: 5px; font-weight: bold;">Log level:</label>
        <select id="logLevel" name="logLevel" multiple style="width: 100%; max-width: 400px; padding: 10px; font-size: 14px; border-radius: 4px; height: 120px;">
            <option value="DEBUG" selected>Debug</option>
            <option value="INFO" selected>Info</option>
            <option value="WARNING" selected>Warning</option>
            <option value="ERROR" selected>Error</option>
            <option value="CRITICAL" selected>Critical</option>
        </select>
        <p style="font-size: 12px; color: #666; margin-top: 5px;">Hold Ctrl/Cmd to select multiple</p>
    </div>
    
    <div class="form-group">
        <label style="display: block; margin-bottom: 5px; font-weight: bold;">Endpoint:</label>
        <select id="endpoint" name="endpoint">
            {endpoint_options}
        </select>
    </div>
    
    <div class="form-group">
        <label style="display: block; margin-bottom: 5px; font-weight: bold;">Technology:</label>
        <select id="technology" name="technology">
            <option value="">*</option>
            <option value="hls">hls</option>
            <option value="dash">dash</option>
        </select>
    </div>
    
    <div class="form-group">
        <label style="display: block; margin-bottom: 5px; font-weight: bold;">Rendition:</label>
        <select id="rendition" name="rendition">
            <option value="">*</option>
            <option value="multi">multi</option>
            <option value="v1">v1</option>
            <option value="a1">a1</option>
            <option value="s1">s1</option>
            <option value="tracking">tracking</option>
        </select>
    </div>
    
    <button>Search</button>
    <cwdb-action action="call" display="popup" endpoint="{get_lambda_arn(context, 'canary-monitor-query-logs')}">
    {{"type": "{type_val}", "workload": "{workload}", "origin": "{origin}"}}
    </cwdb-action>
</body>
</html>'''
    
    return html
