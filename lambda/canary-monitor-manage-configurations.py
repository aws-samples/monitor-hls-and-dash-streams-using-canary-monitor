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
    if 'widgetContext' in event:
        params = event.get('widgetContext', {}).get('params', {})
        bucket = params.get('bucket', '')
    else:
        bucket = event.get('bucket', '')
    
    if not bucket:
        return '<html><body><p>Error: No bucket specified in context</p></body></html>'
    
    # Default configuration content
    default_config = {
        "manifests": {
            "frequency": 5.0,
            "save": {
                "s3": False
            },
            "hls_renditions": ["video", "audio"],
            "ad_segment_prefix": "asset"
        },
        "tracking": {
            "frequency": 6.0,
            "get": True,
            "save": {
                "s3": False
            },
            "playhead": False,
            "playhead_delay": 10
        },
        "reports": {
            "frequency": 60
        },
        "validations": {
            "custom": {
                "required_renditions": ["video", "audio"],
                "ad_break_scte_signals": [ "splice_insert", 48, 50, 52, 54, 56 ],
                "required_tracking_events": [ "impression", "start", "firstQuartile", "midpoint", "thirdQuartile", "complete" ],
                "check_ad_break_scte_duration": True,
                "check_ad_break_start_time": True,
                "max_ad_break_duration_delta": 0.5,
                "max_pts_delta": 0.1,
                "max_segment_availability_delta": {
                  "in_past": 15,
                  "in_future": 5
                }
            }
        }
    }
    default_content = json.dumps(default_config, indent=2)
    
    # Get list of config files from S3
    s3_client = boto3.client('s3')
    config_files = []
    try:
        response = s3_client.list_objects_v2(Bucket=bucket, Prefix='configs/')
        config_files = [obj['Key'].replace('configs/', '') for obj in response.get('Contents', []) 
                       if obj['Key'].endswith('.json') and obj['Key'] != 'configs/']
    except Exception as e:
        print(f"Error listing config files: {str(e)}")
    
    # Build table rows
    config_rows = []
    delete_config_arn = get_lambda_arn(context, 'canary-monitor-delete-config')
    edit_config_arn = get_lambda_arn(context, 'canary-monitor-edit-config')
    
    for config_file in sorted(config_files):
        actions = []
        
        # Skip actions for default.json
        if config_file.lower() != 'default.json':
            # Delete icon (first)
            actions.append(f'''<span style="cursor: pointer; font-size: 18px; margin-right: 10px;" title="delete">🗑️</span>
<cwdb-action action="call" display="popup" endpoint="{delete_config_arn}">
{{"bucket": "{bucket}", "configFile": "{config_file}"}}
</cwdb-action>''')
            
            # Edit icon (second)
            actions.append(f'''<span style="cursor: pointer; font-size: 18px;" title="edit">🔧</span>
<cwdb-action action="call" display="popup" endpoint="{edit_config_arn}">
{{"bucket": "{bucket}", "configFile": "{config_file}"}}
</cwdb-action>''')
        
        action_cell = f'<td style="text-align: center;">{"".join(actions)}</td>'
        config_rows.append(f'<tr><td>{config_file}</td>{action_cell}</tr>')
    
    config_rows_html = ''.join(config_rows)
    
    html = f'''<html>
<head>
    <title>Manage Configurations</title>
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
        
        select {{
            padding: 10px;
            font-size: 14px;
            line-height: 1.6;
        }}
        
        select option {{
            padding: 8px;
        }}
    </style>
</head>
<body style="margin: 0;">
    <p style="text-align: center; font-size: 1.5em; font-weight: bold; margin: 0; margin-bottom: 20px;">Configurations &nbsp;<span style="cursor: pointer; font-size: 0.7em;">ⓘ</span><cwdb-action action="call" display="popup" endpoint="{get_lambda_arn(context, 'canary-monitor-config-info')}">{{"bucket": "{bucket}"}}</cwdb-action></p>
    
    <div class="tabs-container">
        <input type="radio" name="config-tabs" id="tab1" checked>
        <input type="radio" name="config-tabs" id="tab2">
        
        <div class="tab-labels">
            <label for="tab1">Add</label>
            <label for="tab2">Edit</label>
        </div>
        
        <div class="tab-contents">
            <div class="tab-content" id="content1">
                <div style="margin-bottom: 20px;">
                    <label style="display: block; margin-bottom: 5px; font-weight: bold;">Configuration json:</label>
                    <textarea id="newContent" name="newContent" rows="10" style="width: 100%; font-family: monospace; font-size: 12px;">{default_content}</textarea>
                </div>
                <div style="margin-bottom: 20px;">
                    <label style="display: block; margin-bottom: 5px; font-weight: bold;">File name:</label>
                    <input type="text" id="newConfigFile" name="newConfigFile" placeholder="myconfig.json" style="width: 100%; max-width: 400px; padding: 10px; font-size: 14px;" />
                </div>
                <button>Add</button>
                <cwdb-action action="call" display="popup" endpoint="{get_lambda_arn(context, 'canary-monitor-save-config')}">
                {{"bucket": "{bucket}"}}
                </cwdb-action>
            </div>
            
            <div class="tab-content" id="content2">
                <table border="1" style="width: 100%; border-collapse: collapse;">
                    <tr>
                        <th style="text-align: left;">File name</th>
                        <th style="text-align: left;">Action</th>
                    </tr>
                    {config_rows_html}
                </table>
            </div>
        </div>
    </div>
</body>
</html>'''
    
    return html
