import json
import boto3


def lambda_handler(event, context):
  s3_path = event.get('s3_path', '')

  if not s3_path or not s3_path.startswith('s3://'):
    return '<pre>No valid S3 path provided</pre>'

  # Parse S3 path
  path_parts = s3_path.replace('s3://', '').split('/', 1)
  bucket = path_parts[0]
  key = path_parts[1] if len(path_parts) > 1 else ''

  try:
    s3 = boto3.client('s3')
    response = s3.get_object(Bucket=bucket, Key=key)
    content = response['Body'].read().decode('utf-8')

    # Format as JSON for display
    json_obj = json.loads(content)
    formatted_json = json.dumps(json_obj, indent=2)

    return f'<pre>{formatted_json}</pre>'

  except Exception as e:
    return f'<pre>Error fetching report: {str(e)}</pre>'