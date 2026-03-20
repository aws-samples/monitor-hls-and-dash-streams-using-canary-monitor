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
        bucket = params.get('bucket', '')
        filename = params.get('filename', 'from-lambda.csv')
    else:
        bucket = event.get('bucket', '')
        filename = event.get('filename', 'from-lambda.csv')
    
    if not bucket:
        return '<html><body><p>Error: No bucket specified in context</p></body></html>'
    
    html = f'''<html>
<head>
    <title>Manage Origins</title>
    <style>
        .tabs-container input[type="radio"] {{
            display: none;
        }}
        
        .tab-labels {{
            display: flex;
            margin-bottom: 10px;
            border-bottom: 1px solid #ddd;
            justify-content: center;
        }}
        
        .tab-labels label {{
            padding: 15px 30px;
            cursor: pointer;
            margin-right: 5px;
            background: white;
            border-radius: 8px 8px 0 0;
            font-weight: bold;
            text-align: center;
        }}
        
        .tab-content {{
            display: none;
            padding: 20px;
        }}
        
        #tab1:checked ~ .tab-labels label[for="tab1"],
        #tab2:checked ~ .tab-labels label[for="tab2"] {{
            background: #ffd380;
        }}
        
        #tab1:checked ~ .tab-contents #content1,
        #tab2:checked ~ .tab-contents #content2 {{
            display: block;
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
        }}
    </style>
</head>
<body style="margin: 0;">
    <p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Endpoints</p>
    
    <div class="tabs-container">
        <input type="radio" name="origin-tabs" id="tab1" checked>
        <input type="radio" name="origin-tabs" id="tab2">
        
        <div class="tab-labels">
            <label for="tab1">Add single</label>
            <label for="tab2">Add bulk</label>
        </div>
        
        <div class="tab-contents">
            <div class="tab-content" id="content1">
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
                    <div>
                        <div style="margin-bottom: 15px;">
                            <label style="display: block; margin-bottom: 5px; font-weight: bold;">Technology (hls or dash):</label>
                            <input type="text" id="technology" name="technology" value="hls" style="width: 100%; padding: 10px; font-size: 14px;" />
                        </div>
                        <div style="margin-bottom: 15px;">
                            <label style="display: block; margin-bottom: 5px; font-weight: bold;">Workload name:</label>
                            <input type="text" id="workloadName" name="workloadName" placeholder="tnf-25" style="width: 100%; padding: 10px; font-size: 14px;" />
                        </div>
                        <div style="margin-bottom: 15px;">
                            <label style="display: block; margin-bottom: 5px; font-weight: bold;">Endpoint name:</label>
                            <input type="text" id="endpointName" name="endpointName" placeholder="feed-1-pdx-1" style="width: 100%; padding: 10px; font-size: 14px;" />
                        </div>
                        <div style="margin-bottom: 15px;">
                            <label style="display: block; margin-bottom: 5px; font-weight: bold;">Manifest URL:</label>
                            <input type="text" id="manifestUrl" name="manifestUrl" placeholder="https://..." style="width: 100%; padding: 10px; font-size: 14px;" />
                        </div>
                    </div>
                    <div>
                        <div style="margin-bottom: 15px;">
                            <label style="display: block; margin-bottom: 5px; font-weight: bold;">Origin name:</label>
                            <input type="text" id="originName" name="originName" placeholder="emp" style="width: 100%; padding: 10px; font-size: 14px;" />
                        </div>
                        <div style="margin-bottom: 15px;">
                            <label style="display: block; margin-bottom: 5px; font-weight: bold;">Is DAI (true or false):</label>
                            <input type="text" id="isDai" name="isDai" value="false" style="width: 100%; padding: 10px; font-size: 14px;" />
                        </div>
                        <div style="margin-bottom: 15px;">
                            <label style="display: block; margin-bottom: 5px; font-weight: bold;">Config file:</label>
                            <input type="text" id="configFile" name="configFile" value="default.json" style="width: 100%; padding: 10px; font-size: 14px;" />
                        </div>
                        <div style="margin-bottom: 15px;">
                            <label style="display: block; margin-bottom: 5px; font-weight: bold;">Tracking URL (optional):</label>
                            <input type="text" id="trackingUrl" name="trackingUrl" placeholder="https://..." style="width: 100%; padding: 10px; font-size: 14px;" />
                        </div>
                    </div>
                </div>
                <button>Add</button>
                <cwdb-action action="call" display="popup" endpoint="{get_lambda_arn(context, 'canary-monitor-add-origin')}">
                {{"bucket": "{bucket}"}}
                </cwdb-action>
            </div>
            
            <div class="tab-content" id="content2">
                <label style="display: block; margin-bottom: 5px; font-weight: bold;">CSV:</label>
                <textarea id="csvLines" name="csvLines" rows="10" style="width: 100%; font-family: monospace; font-size: 12px;" placeholder="endpoint type (live), technology (hls/dash), workload name, endpoint name, origin name, is dai (true/false), config file, manifest url, tracking url [optional]"></textarea>
                <br><br>
                <button>Add</button>
                <cwdb-action action="call" display="popup" endpoint="{get_lambda_arn(context, 'canary-monitor-add-origins')}">
                {{"bucket": "{bucket}", "filename": "{filename}"}}
                </cwdb-action>
            </div>
        </div>
    </div>
</body>
</html>'''
    
    return html
