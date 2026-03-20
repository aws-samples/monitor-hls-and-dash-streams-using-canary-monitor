#!/usr/bin/env python3

import argparse
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

# Lambda settings
LAMBDA_RUNTIME = 'python3.14'
LAMBDA_TIMEOUT = 30
LAMBDA_ARCHITECTURE = 'arm64'
LAMBDA_MEMORY_DEFAULT = 128
LAMBDA_MEMORY_REPORT = 256
LAMBDA_ROLE_NAME = 'canary-monitor-lambda-role'

REGION_CODES = {
    'us-east-1': 'iad', 'us-east-2': 'cmh', 'us-west-1': 'sfo', 'us-west-2': 'pdx',
    'af-south-1': 'cpt', 'ap-east-1': 'hkg', 'ap-south-1': 'bom', 'ap-south-2': 'hyd',
    'ap-northeast-1': 'nrt', 'ap-northeast-2': 'icn', 'ap-northeast-3': 'kix',
    'ap-southeast-1': 'sin', 'ap-southeast-2': 'syd', 'ap-southeast-3': 'jkt', 'ap-southeast-4': 'mel',
    'ca-central-1': 'yul', 'ca-west-1': 'yvr',
    'eu-central-1': 'fra', 'eu-central-2': 'zrh', 'eu-west-1': 'dub', 'eu-west-2': 'lhr',
    'eu-west-3': 'cdg', 'eu-south-1': 'mxp', 'eu-south-2': 'mad', 'eu-north-1': 'arn',
    'il-central-1': 'tlv', 'me-south-1': 'bah', 'me-central-1': 'auh', 'sa-east-1': 'gru',
}

def signal_handler(sig, frame):
    print("\n\n✓ Exited")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)


# --- Helpers ---

_session = None

def init_session(profile_name=None):
    """Initialize global boto3 session with optional profile"""
    global _session
    import boto3
    _session = boto3.Session(profile_name=profile_name)

def get_client(service, region_name=None):
    """Get a boto3 client using the global session"""
    kwargs = {}
    if region_name:
        kwargs['region_name'] = region_name
    return _session.client(service, **kwargs)

def require_boto3():
    """Import boto3 on demand, exit with helpful message if not available"""
    try:
        import boto3
        return boto3
    except ImportError:
        print("✗ boto3 is required. Run 'pip install boto3' or use option 1 to install Python libraries first.")
        return None

def get_root_dir():
    """Get canary monitor root folder from this script's location"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_ec2_metadata():
    """Get account ID, region and instance ID from EC2 instance metadata. Returns empty strings on failure."""
    try:
        token_request = urllib.request.Request(
            'http://169.254.169.254/latest/api/token',
            headers={'X-aws-ec2-metadata-token-ttl-seconds': '21600'},
            method='PUT'
        )
        with urllib.request.urlopen(token_request, timeout=2) as response:
            token = response.read().decode('utf-8')

        region_request = urllib.request.Request(
            'http://169.254.169.254/latest/meta-data/placement/region',
            headers={'X-aws-ec2-metadata-token': token}
        )
        with urllib.request.urlopen(region_request, timeout=2) as response:
            region = response.read().decode('utf-8').strip()

        instance_request = urllib.request.Request(
            'http://169.254.169.254/latest/meta-data/instance-id',
            headers={'X-aws-ec2-metadata-token': token}
        )
        with urllib.request.urlopen(instance_request, timeout=2) as response:
            instance_id = response.read().decode('utf-8').strip()

        sts_client = get_client('sts', region_name=region)
        account_id = sts_client.get_caller_identity()['Account']

        return account_id, region, instance_id
    except Exception:
        return '', '', ''

def get_aws_defaults():
    """Get account ID and region, trying EC2 metadata first, then STS/boto3 config."""
    account_id, region, _ = get_ec2_metadata()
    if account_id and region:
        return account_id, region
    # Fallback: try STS with default boto3 credentials/config
    try:
        region = region or _session.region_name or ''
        sts_client = get_client('sts')
        account_id = sts_client.get_caller_identity()['Account']
        return account_id, region
    except Exception:
        return '', ''

def ask(question):
    """Ask a y/n question, default y"""
    answer = input(question).strip().lower()
    return answer != 'n'

def print_policy_file(filename, description):
    """Print a policy JSON file from the tools directory"""
    policy_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    if os.path.exists(policy_path):
        print(f"\n{description}:\n")
        with open(policy_path, 'r') as f:
            print(f.read(), end='')
    else:
        print(f"✗ Policy file not found: {policy_path}")

def restart_canary_monitor_if_running():
    """Ask to restart canary monitor if it is running as a service"""
    try:
        result = subprocess.run(['systemctl', 'is-active', 'canary-monitor'], capture_output=True, text=True)
        if result.stdout.strip() == 'active':
            if ask("\nCanary Monitor service is running. Would you like to restart it to pick up possible changes? [y]/n: "):
                subprocess.run(['sudo', 'systemctl', 'restart', 'canary-monitor'], check=True)
                print("✓ Canary Monitor restarted")
    except Exception:
        pass

def write_file_with_sudo(content, dest_path):
    """Write content to a file that requires sudo"""
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    result = os.system(f'sudo mv {tmp_path} {dest_path}')
    if result != 0:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        return False
    return True

def get_lambda_files():
    """Get sorted list of lambda .py files excluding deploy scripts"""
    lambda_dir = Path(get_root_dir()) / 'lambda'
    return sorted([f for f in lambda_dir.glob('*.py') if f.name not in ('deploy.py',)])


# --- Menu 1: EC2 Instance System Setup ---

def setup_python():
    """Install Python 3.12 and required libraries"""
    root_dir = get_root_dir()
    requirements_path = os.path.join(root_dir, 'requirements.txt')

    print("\nInstalling Python 3.12...")
    if os.system('sudo dnf install python3.12 -y') != 0:
        print("✗ Failed to install Python 3.12")
        return

    print("\nInstalling pip...")
    if os.system('/usr/bin/python3.12 -m ensurepip --upgrade') != 0:
        print("✗ Failed to install pip")
        return

    if os.path.exists(requirements_path):
        print(f"\nInstalling libraries from {requirements_path}...")
        if os.system(f'/usr/bin/python3.12 -m pip install -r {requirements_path}') != 0:
            print("✗ Failed to install required libraries")
            return
    else:
        print(f"✗ {requirements_path} not found")
        return

    print("✓ Python 3.12 and libraries installed")

def setup_cloudwatch_agent(region, instance_id):
    """Setup CloudWatch agent for logs and system metrics"""
    root_dir = get_root_dir()

    # Ensure json_logger is enabled in settings.yaml (required for CW agent log parsing)
    settings_path = os.path.join(root_dir, 'settings.yaml')
    if os.path.exists(settings_path):
        with open(settings_path, 'r') as f:
            content = f.read()
        if 'json_logger: false' in content:
            content = content.replace('json_logger: false', 'json_logger: true')
            with open(settings_path, 'w') as f:
                f.write(content)
            print("✓ Set json_logger to true in settings.yaml")

    cw_dir = os.path.expanduser('~/cw-agent')
    config_file = os.path.join(cw_dir, 'cw-agent.config')
    log_file = os.path.join(cw_dir, 'cw-agent.log')
    bashrc_file = os.path.expanduser('~/.bashrc')
    logs_dir = os.path.join(root_dir, 'logs')

    if not os.path.exists(cw_dir):
        os.makedirs(cw_dir)
        print(f"✓ Created {cw_dir}")

    if not os.path.exists(log_file):
        open(log_file, 'w').close()

    config_content = f"""{{
  "agent": {{
    "region": "{region}",
    "logfile": "{os.path.join(cw_dir, 'cw-agent.log')}",
    "metrics_collection_interval": 60
  }},
  "logs": {{
    "logs_collected": {{
      "files": {{
        "collect_list": [
          {{
            "file_path": "{os.path.join(logs_dir, 'monitor.log')}",
            "log_group_name": "CanaryMonitor/MonitorLogs",
            "log_stream_name": "{instance_id}",
            "timestamp_format": "%Y-%m-%d %H:%M:%S,%f",
            "timezone": "UTC"
          }},
          {{
            "file_path": "{os.path.join(logs_dir, 'service.log')}",
            "log_group_name": "CanaryMonitor/ServiceLogs",
            "log_stream_name": "{instance_id}",
            "timestamp_format": "%Y-%m-%d %H:%M:%S,%f",
            "timezone": "UTC"
          }}
        ]
      }}
    }}
  }},
  "metrics": {{
    "namespace": "CWAgent",
    "append_dimensions": {{
      "InstanceId": "${{aws:InstanceId}}"
    }},
    "metrics_collected": {{
      "disk": {{
        "measurement": ["used_percent"],
        "resources": ["/"]
      }},
      "mem": {{
        "measurement": ["mem_used_percent"]
      }},
      "procstat": [
        {{
          "pattern": "canarymonitor.py",
          "measurement": ["pid_count"]
        }}
      ]
    }}
  }}
}}
"""
    with open(config_file, 'w') as f:
        f.write(config_content)
    print(f"✓ Created {config_file}")

    # Add aliases to .bashrc
    aliases = [
        f"alias cw-agent-start='sudo /usr/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -c file:{config_file} -s'",
        "alias cw-agent-stop='sudo /usr/bin/amazon-cloudwatch-agent-ctl -a stop'"
    ]
    if os.path.exists(bashrc_file):
        with open(bashrc_file, 'r') as f:
            bashrc_content = f.read()
    else:
        bashrc_content = ""

    added = False
    for alias in aliases:
        if alias not in bashrc_content:
            bashrc_content += f"\n{alias}\n"
            added = True
    if added:
        with open(bashrc_file, 'w') as f:
            f.write(bashrc_content)
        print(f"✓ Added aliases to {bashrc_file}")

    os.system('sudo systemctl enable amazon-cloudwatch-agent 2>/dev/null')
    print("✓ Enabled CloudWatch agent at boot")

    agent_running = os.system('systemctl is-active --quiet amazon-cloudwatch-agent') == 0
    if agent_running:
        if ask("\nCloudWatch agent is running. Restart it with new configuration? [y]/n: "):
            os.system(f'sudo /usr/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -c file:{config_file} -s')
            print("✓ CloudWatch agent restarted")
    else:
        if ask("\nStart CloudWatch agent now? [y]/n: "):
            os.system(f'sudo /usr/bin/amazon-cloudwatch-agent-ctl -a fetch-config -m ec2 -c file:{config_file} -s')
            print("✓ CloudWatch agent started")

    print("✓ CloudWatch agent setup completed")

def setup_logrotate():
    """Setup logrotate for canary monitor logs"""
    root_dir = get_root_dir()
    logs_dir = os.path.join(root_dir, 'logs')
    logrotate_file = '/etc/logrotate.d/canary-logs'

    if os.path.exists(logrotate_file):
        print(f"✓ Logrotate config already exists: {logrotate_file}")
        return

    logrotate_content = f"""{os.path.join(logs_dir, '*.log')} {{
  su ec2-user ec2-user
  missingok
  size 100M
  rotate 3
  compress
  copytruncate
}}
"""
    if write_file_with_sudo(logrotate_content, logrotate_file):
        os.system(f'sudo chown root:root {logrotate_file}')
        os.system(f'sudo chmod 644 {logrotate_file}')
        print(f"✓ Created {logrotate_file}")
    else:
        print("✗ Failed to create logrotate config")

def setup_systemd_service():
    """Setup systemd service and aliases for canary monitor"""
    root_dir = get_root_dir()
    service_file = '/etc/systemd/system/canary-monitor.service'
    bashrc_file = os.path.expanduser('~/.bashrc')

    if not os.path.exists(service_file):
        service_content = f"""[Unit]
Description=Monitor for HLS, DASH streams
After=network.target

[Service]
LimitNOFILE=65535
Type=simple
User=ec2-user
WorkingDirectory={root_dir}
ExecStart=/usr/bin/python3.12 {os.path.join(root_dir, 'canarymonitor.py')}
Restart=no

[Install]
WantedBy=multi-user.target
"""
        if not write_file_with_sudo(service_content, service_file):
            print("✗ Failed to create service file")
            return

        print(f"✓ Created {service_file}")
        os.system('sudo systemctl daemon-reload')
        os.system('sudo systemctl enable canary-monitor')
        print("✓ Enabled canary-monitor at boot")
    else:
        print(f"✓ Service file already exists: {service_file}")

    # Add aliases
    aliases = [
        "alias canary-monitor-stop='sudo systemctl stop canary-monitor'",
        "alias canary-monitor-start='sudo systemctl start canary-monitor'"
    ]
    if os.path.exists(bashrc_file):
        with open(bashrc_file, 'r') as f:
            bashrc_content = f.read()
    else:
        bashrc_content = ""

    added = False
    for alias in aliases:
        if alias not in bashrc_content:
            bashrc_content += f"\n{alias}\n"
            added = True
    if added:
        with open(bashrc_file, 'w') as f:
            f.write(bashrc_content)
        print(f"✓ Added aliases to {bashrc_file}")

    # Check if canary monitor is running
    result = os.system('systemctl is-active --quiet canary-monitor')
    if result != 0:
        if ask("\nCanary Monitor is not running. Would you like to start it now? [y]/n: "):
            os.system('sudo systemctl start canary-monitor')
            print("✓ Canary Monitor started")

def menu_ec2_setup():
    """Host system setup"""
    account_id, ec2_region, instance_id = get_ec2_metadata()
    if not instance_id:
        print("✗ This option requires running on an EC2 instance")
        return

    # Check settings.yaml region against EC2 region
    region = ec2_region
    root_dir = get_root_dir()
    settings_path = os.path.join(root_dir, 'settings.yaml')
    if os.path.exists(settings_path):
        with open(settings_path, 'r') as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith('region:'):
                    settings_region = stripped.split(':', 1)[1].strip()
                    if not settings_region or settings_region != ec2_region:
                        if settings_region:
                            print(f"EC2 instance region is '{ec2_region}' but settings.yaml has '{settings_region}'")
                        else:
                            print(f"Region in settings.yaml is not set")
                        chosen = input(f"What is the correct region for Canary Monitor to use? [{ec2_region}]: ").strip() or ec2_region
                        region = chosen
                        with open(settings_path, 'r') as sf:
                            content = sf.read()
                        old_value = f'region: {settings_region}' if settings_region else 'region:'
                        content = content.replace(old_value, f'region: {region}', 1)
                        with open(settings_path, 'w') as sf:
                            sf.write(content)
                        print(f"✓ Updated settings.yaml region to '{region}'")
                    break

    if ask("\nWould you like to install Python 3.12 and required libraries? [y]/n: "):
        setup_python()

    if ask("\nWould you like to set up AWS CloudWatch Agent for sending Canary Monitor logs and system health metrics to CloudWatch? [y]/n: "):
        setup_cloudwatch_agent(region, instance_id)

    if ask("\nWould you like to set up logrotate for Canary Monitor logs to prevent the logs from consuming excessive disk space? [y]/n: "):
        setup_logrotate()

    if ask("\nWould you like to set up systemd service to start and stop Canary Monitor? [y]/n: "):
        setup_systemd_service()

    restart_canary_monitor_if_running()


# --- Menu 2: AWS S3, CloudWatch and Lambda Setup ---

def setup_s3_bucket(account_id, region):
    """Setup S3 bucket for canary monitor outputs"""
    from botocore.exceptions import ClientError
    root_dir = get_root_dir()
    default_bucket = f"canary-monitor-{REGION_CODES.get(region, region)}-{account_id}"
    bucket_name = input(f"\nS3 bucket name [{default_bucket}]: ").strip() or default_bucket

    s3_client = get_client('s3', region_name=region)

    # Check if bucket exists
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        print(f"✓ Bucket {bucket_name} already exists")
    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == '404':
            try:
                if region == 'us-east-1':
                    s3_client.create_bucket(Bucket=bucket_name)
                else:
                    s3_client.create_bucket(
                        Bucket=bucket_name,
                        CreateBucketConfiguration={'LocationConstraint': region}
                    )
                print(f"✓ Created bucket {bucket_name}")
            except Exception as e2:
                print(f"✗ Error creating bucket: {e2}")
                return
        else:
            print(f"✗ Error checking bucket: {e}")
            return

    # Create folders
    for folder in ['archive/', 'configs/', 'origins/']:
        try:
            response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=folder, MaxKeys=1)
            if 'Contents' not in response:
                s3_client.put_object(Bucket=bucket_name, Key=folder, Body=b'')
                print(f"✓ Created folder {folder}")
            else:
                print(f"✓ Folder {folder} already exists")
        except Exception as e:
            print(f"✗ Error creating folder {folder}: {e}")

    # Upload default.json
    default_config_path = os.path.join(root_dir, 'configs', 'default.json')
    if os.path.exists(default_config_path):
        try:
            s3_client.upload_file(default_config_path, bucket_name, 'configs/default.json')
            print("✓ Uploaded default.json to configs/")
        except Exception as e:
            print(f"✗ Error uploading default.json: {e}")

    print("✓ S3 bucket setup completed")

    # Check settings.yaml bucket
    settings_path = os.path.join(root_dir, 'settings.yaml')
    if os.path.exists(settings_path):
        with open(settings_path, 'r') as f:
            content = f.read()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith('bucket:'):
                settings_bucket = stripped.split(':', 1)[1].strip()
                if not settings_bucket:
                    content = content.replace('bucket:', f'bucket: {bucket_name}', 1)
                    with open(settings_path, 'w') as f:
                        f.write(content)
                    print(f"✓ Updated settings.yaml bucket to '{bucket_name}'")
                elif settings_bucket != bucket_name:
                    print(f"⚠ Warning: settings.yaml has bucket '{settings_bucket}' but you configured '{bucket_name}'")
                break

def ensure_lambda_role(account_id, region):
    """Create Lambda execution role if it doesn't exist, return role ARN"""
    iam_client = get_client('iam', region_name=region)
    role_arn = f"arn:aws:iam::{account_id}:role/{LAMBDA_ROLE_NAME}"

    try:
        iam_client.get_role(RoleName=LAMBDA_ROLE_NAME)
        print(f"✓ IAM role {LAMBDA_ROLE_NAME} already exists")
        return role_arn
    except iam_client.exceptions.NoSuchEntityException:
        pass

    print(f"Creating IAM role {LAMBDA_ROLE_NAME}...")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }

    try:
        iam_client.create_role(
            RoleName=LAMBDA_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description='Execution role for Canary Monitor Lambda functions'
        )
        print(f"✓ Created IAM role {LAMBDA_ROLE_NAME}")

        # Attach managed policies
        managed_policies = [
            'arn:aws:iam::aws:policy/AmazonS3FullAccess',
            'arn:aws:iam::aws:policy/CloudWatchFullAccessV2',
            'arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'
        ]
        for policy_arn in managed_policies:
            iam_client.attach_role_policy(RoleName=LAMBDA_ROLE_NAME, PolicyArn=policy_arn)
            print(f"✓ Attached {policy_arn.split('/')[-1]}")

        # Add inline policy for Lambda-to-Lambda invocation
        invoke_policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": "lambda:InvokeFunction",
                "Resource": f"arn:aws:lambda:*:{account_id}:function:canary-monitor-*"
            }]
        }
        iam_client.put_role_policy(
            RoleName=LAMBDA_ROLE_NAME,
            PolicyName='LambdaInvokePolicy',
            PolicyDocument=json.dumps(invoke_policy)
        )
        print("✓ Added Lambda invoke policy")

        # Wait for role propagation
        print("Waiting for IAM role propagation...")
        time.sleep(10)

        return role_arn
    except Exception as e:
        print(f"✗ Error creating IAM role: {e}")
        return None

def deploy_lambda_functions(account_id, region):
    """Create or update Lambda functions"""
    role_arn = ensure_lambda_role(account_id, region)
    if not role_arn:
        return

    lambda_client = get_client('lambda', region_name=region)
    lambda_files = get_lambda_files()

    if not lambda_files:
        print("✗ No Lambda functions found")
        return

    selected = lambda_files

    print(f"Deploying {len(selected)} Lambda function(s) to {region}\n")

    success_count = 0
    fail_count = 0

    for file_path in selected:
        function_name = file_path.stem
        memory_size = LAMBDA_MEMORY_REPORT if function_name == 'canary-monitor-report' else LAMBDA_MEMORY_DEFAULT
        timeout = 900 if function_name == 'canary-monitor-background-delete' else LAMBDA_TIMEOUT

        # Create deployment package
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            with open(file_path, 'rb') as f:
                zf.writestr('lambda_function.py', f.read())
        zip_content = zip_buffer.getvalue()

        try:
            # Check if function exists
            try:
                lambda_client.get_function(FunctionName=function_name)
                function_exists = True
            except lambda_client.exceptions.ResourceNotFoundException:
                function_exists = False

            if function_exists:
                lambda_client.update_function_code(
                    FunctionName=function_name,
                    ZipFile=zip_content,
                    Architectures=[LAMBDA_ARCHITECTURE]
                )
                if function_name == 'canary-monitor-background-delete':
                    time.sleep(2)
                    try:
                        lambda_client.update_function_configuration(
                            FunctionName=function_name,
                            Timeout=900
                        )
                    except Exception:
                        pass
                print(f"✓ Updated {function_name}")
            else:
                lambda_client.create_function(
                    FunctionName=function_name,
                    Runtime=LAMBDA_RUNTIME,
                    Role=role_arn,
                    Handler='lambda_function.lambda_handler',
                    Code={'ZipFile': zip_content},
                    Timeout=timeout,
                    MemorySize=memory_size,
                    Architectures=[LAMBDA_ARCHITECTURE]
                )
                print(f"✓ Created {function_name}")

            # Add CloudWatch Dashboard permissions
            for stmt_id, principal, extra in [
                ('AllowCloudWatchDashboardInternal', 'cloudwatch-dashboards.aws.internal', {}),
                ('AllowCloudWatchDashboard', 'cloudwatch.amazonaws.com', {'SourceAccount': account_id}),
            ]:
                try:
                    lambda_client.add_permission(
                        FunctionName=function_name,
                        StatementId=stmt_id,
                        Action='lambda:InvokeFunction',
                        Principal=principal,
                        **extra
                    )
                except lambda_client.exceptions.ResourceConflictException:
                    pass
                except Exception as e:
                    print(f"✗ Warning: Could not add {stmt_id} permission to {function_name}: {e}")

            success_count += 1
        except Exception as e:
            print(f"✗ Error with {function_name}: {e}")
            fail_count += 1

    print(f"\n✓ Deployment complete: {success_count} successful, {fail_count} failed")

def get_settings_value(key):
    """Read a value from settings.yaml by key path (e.g. 'aws.bucket')"""
    root_dir = get_root_dir()
    settings_path = os.path.join(root_dir, 'settings.yaml')
    if not os.path.exists(settings_path):
        return None
    with open(settings_path, 'r') as f:
        lines = f.readlines()
    keys = key.split('.')
    depth = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        indent = len(line) - len(line.lstrip())
        current_depth = indent // 2
        if current_depth == depth and depth < len(keys) - 1:
            if stripped.startswith(keys[depth] + ':'):
                depth += 1
        elif current_depth == depth and depth == len(keys) - 1:
            if stripped.startswith(keys[depth] + ':'):
                val = stripped.split(':', 1)[1].strip()
                return val if val else None
    return None

def setup_cloudwatch_dashboard(account_id, region):
    """Create CloudWatch management dashboard from template"""
    bucket_name = get_settings_value('aws.bucket')
    if not bucket_name:
        print("✗ S3 bucket not found in settings.yaml. Set up S3 bucket first.")
        return

    # Ask about input_location change
    current_input = get_settings_value('application.input_location')
    if current_input and current_input != 's3':
        if ask(f"\nThe management dashboard allows managing all input from CloudWatch. Would you like to change input_location in settings.yaml from '{current_input}' to 's3'? [y]/n: "):
            root_dir = get_root_dir()
            settings_path = os.path.join(root_dir, 'settings.yaml')
            with open(settings_path, 'r') as f:
                content = f.read()
            content = content.replace(f'input_location: {current_input}', 'input_location: s3', 1)
            with open(settings_path, 'w') as f:
                f.write(content)
            print(f"✓ Updated settings.yaml input_location to 's3'")

    region_code = REGION_CODES.get(region, region)
    default_name = f"Canary-Monitor-{region_code.upper()}"
    dashboard_name = input(f"\nDashboard name [{default_name}]: ").strip() or default_name

    _, _, instance_id = get_ec2_metadata()
    if not instance_id:
        instance_id = input("EC2 instance ID: ").strip()
    if not instance_id:
        print("✗ Instance ID is required for the dashboard")
        return

    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard-template.json')
    if not os.path.exists(template_path):
        print(f"✗ Template file not found: {template_path}")
        return

    with open(template_path, 'r') as f:
        body = f.read()

    body = body.replace('${REGION}', region)
    body = body.replace('${ACCOUNT_ID}', account_id)
    body = body.replace('${INSTANCE_ID}', instance_id)
    body = body.replace('${BUCKET}', bucket_name)
    body = body.replace('${REGION_CODE}', region_code)

    try:
        cw = get_client('cloudwatch', region_name=region)
        cw.put_dashboard(DashboardName=dashboard_name, DashboardBody=body)
        print(f"✓ Created CloudWatch dashboard: {dashboard_name}")
    except Exception as e:
        print(f"✗ Error creating dashboard: {e}")

def setup_cloudwatch_alarm(account_id, region, instance_id):
    """Create CloudWatch alarm for canary monitor process status"""
    cloudwatch_client = get_client('cloudwatch', region_name=region)
    alarm_name = f"canary-monitor-{REGION_CODES.get(region, region)}-status"

    try:
        cloudwatch_client.put_metric_alarm(
            AlarmName=alarm_name,
            ComparisonOperator='LessThanOrEqualToThreshold',
            EvaluationPeriods=1,
            MetricName='procstat_lookup_pid_count',
            Namespace='CWAgent',
            Period=60,
            Statistic='Average',
            Threshold=0.0,
            ActionsEnabled=False,
            AlarmDescription=f'Alert when canary-monitor process is not running',
            Dimensions=[
                {'Name': 'InstanceId', 'Value': instance_id},
                {'Name': 'pattern', 'Value': 'canarymonitor.py'},
                {'Name': 'pid_finder', 'Value': 'native'}
            ],
            TreatMissingData='breaching'
        )
        print(f"✓ Created CloudWatch alarm: {alarm_name}")
    except Exception as e:
        print(f"✗ Error creating CloudWatch alarm: {e}")

def ask_aws_account_and_region():
    """Prompt for AWS account ID and region with defaults from EC2 metadata or boto3 config"""
    default_account_id, default_region = get_aws_defaults()

    prompt_account = f"\nAWS account ID [{default_account_id}]: " if default_account_id else "\nAWS account ID: "
    account_id = input(prompt_account).strip() or default_account_id
    if not account_id:
        print("✗ AWS account ID is required")
        return None, None

    prompt_region = f"AWS region [{default_region}]: " if default_region else "AWS region: "
    region = input(prompt_region).strip() or default_region
    if not region:
        print("✗ AWS region is required")
        return None, None

    print()
    return account_id, region

def menu_aws_setup():
    """AWS resources setup"""
    account_id, region = ask_aws_account_and_region()
    if not account_id:
        return

    if ask("Would you like to set up AWS S3 bucket for storing Canary Monitor outputs? [y]/n: "):
        setup_s3_bucket(account_id, region)

    if ask("\nWould you like to create AWS Lambda functions for monitoring endpoint health and for management of monitored endpoints in AWS CloudWatch? [y]/n: "):
        deploy_lambda_functions(account_id, region)
        # Update Lambda ARNs in settings.yaml
        root_dir = get_root_dir()
        settings_path = os.path.join(root_dir, 'settings.yaml')
        if os.path.exists(settings_path):
            with open(settings_path, 'r') as f:
                content = f.read()
            report_arn = f"arn:aws:lambda:{region}:{account_id}:function:canary-monitor-report"
            logs_arn = f"arn:aws:lambda:{region}:{account_id}:function:canary-monitor-manage-logs"
            new_lines = []
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith('report:'):
                    new_lines.append(f"    report: {report_arn}")
                elif stripped.startswith('logs:'):
                    new_lines.append(f"    logs: {logs_arn}")
                else:
                    new_lines.append(line)
            with open(settings_path, 'w') as f:
                f.write('\n'.join(new_lines) + '\n')
            print(f"✓ Updated settings.yaml Lambda ARNs")

    if ask("\nWould you like to create AWS CloudWatch dashboard for management of endpoints in AWS CloudWatch? [y]/n: "):
        setup_cloudwatch_dashboard(account_id, region)

    if ask("\nWould you like to create AWS CloudWatch alarm for monitoring Canary Monitor status (used in the Canary Monitor management dashboard)? [y]/n: "):
        _, _, instance_id = get_ec2_metadata()
        if not instance_id:
            instance_id = input("EC2 instance ID (required for alarm): ").strip()
        if instance_id:
            setup_cloudwatch_alarm(account_id, region, instance_id)
        else:
            print("✗ Instance ID is required for CloudWatch alarm")

    restart_canary_monitor_if_running()


# --- Menu 3: Continuous Management ---

def menu_continuous_management():
    """Continuous management of Lambda functions and dashboards"""
    account_id, region = ask_aws_account_and_region()
    if not account_id:
        return

    if ask("Would you like to update AWS Lambda functions? [y]/n: "):
        deploy_lambda_functions(account_id, region)

    if ask("\nWould you like to update AWS CloudWatch management dashboard? [y]/n: "):
        setup_cloudwatch_dashboard(account_id, region)


# --- Menu 1: Check Permissions ---

def menu_check_permissions():
    """Check IAM permissions and display policy documents"""
    answer = input("\nThe EC2 instance role should contain handful of permissions for full functionality of the canary monitor. Would you like to print an example inline policy that can be attached to the EC2 instance role? [y]/n: ").strip().lower()
    if answer != 'n':
        print_policy_file('iam-policy-ec2.json', 'EC2 instance role policy (runtime permissions for Canary Monitor)')

    answer = input("\nIn order to use this script to set up AWS resources like AWS S3 and Lambda, the user running this script needs permissions to those services. Would you like to print an example inline policy with the required permissions? [y]/n: ").strip().lower()
    if answer != 'n':
        print_policy_file('iam-policy-setup.json', 'Setup user policy (permissions for deploying AWS resources)')

    if ask("\nWould you like to check some of the IAM permissions of the current caller? [y]/n: "):
        check_iam_permissions()


# --- Main ---

def check_iam_permissions():
    """Check IAM permissions of the current caller"""
    # Determine region for service clients
    region = _session.region_name
    if not region:
        _, region, _ = get_ec2_metadata()
    if not region:
        region = input("\nAWS region: ").strip()
    if not region:
        print("✗ AWS region is required for permission checks")
        return

    try:
        cw = get_client('cloudwatch', region_name=region)
        cw.list_dashboards(DashboardNamePrefix='canary-monitor-permission-check')
        print("\n✓ CloudWatch: cloudwatch:ListDashboards")
    except Exception:
        print("\n✗ CloudWatch: cloudwatch:ListDashboards")

    try:
        s3 = get_client('s3', region_name=region)
        s3.list_buckets()
        print("✓ S3: s3:ListAllMyBuckets")
    except Exception:
        print("✗ S3: s3:ListAllMyBuckets")

    try:
        lam = get_client('lambda', region_name=region)
        lam.list_functions(MaxItems=1)
        print("✓ Lambda: lambda:ListFunctions")
    except Exception:
        print("✗ Lambda: lambda:ListFunctions")

    try:
        iam = get_client('iam', region_name=region)
        iam.get_role(RoleName=LAMBDA_ROLE_NAME)
        print("✓ IAM: iam:GetRole")
    except iam.exceptions.NoSuchEntityException:
        print("✓ IAM: iam:GetRole")
    except Exception:
        print("✗ IAM: iam:GetRole")

def main():
    parser = argparse.ArgumentParser(description='Canary Monitor setup and management tool')
    parser.add_argument('--profile', help='AWS profile name to use from ~/.aws/credentials')
    args = parser.parse_args()

    try:
        import boto3
        print("\n✓ Boto3 available")
    except ImportError:
        print("\n✗ Boto3 is required. Run 'sudo dnf install python3-boto3' or 'sudo yum install python3-boto3' if you are on older Amazon Linux")
        sys.exit(1)

    init_session(profile_name=args.profile)

    try:
        sts = get_client('sts')
        identity = sts.get_caller_identity()
        print(f"✓ AWS identity: {identity.get('Arn', '')}")
    except Exception:
        print("⚠ Could not verify AWS identity")

    print("\nUse this script to set up the Canary Monitor host system, deploy AWS resources")
    print("and manage updates. You will be prompted for each action.\n")

    while True:
        print("=" * 60)
        print("\n1. Check Permissions")
        print("2. Setup Host System")
        print("3. Setup AWS Resources")
        print("4. Manage Updates")
        print("5. Exit")

        choice = input("\nSelect option (1-5): ").strip()

        if choice == '1':
            menu_check_permissions()
        elif choice == '2':
            menu_ec2_setup()
        elif choice == '3':
            menu_aws_setup()
        elif choice == '4':
            menu_continuous_management()
        elif choice == '5':
            print("✓ Exited")
            sys.exit(0)
        else:
            print("✗ Invalid option")
        print()

if __name__ == "__main__":
    main()
