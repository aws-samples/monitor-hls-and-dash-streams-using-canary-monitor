import boto3
import json
import time

def lambda_handler(event, context):
    """
    Background Lambda for deleting S3 data and CloudWatch dashboards.
    Invoked asynchronously by delete endpoint/endpoints Lambdas.
    Re-invokes itself if approaching timeout during S3 deletion.
    Timeout: 15 minutes
    """
    print(f"Event: {json.dumps(event)}")

    if not event.get('skip_wait'):
        print("Waiting 15 seconds before deletion...")
        time.sleep(15)
    print("Starting deletion process")

    bucket = event.get('bucket', '')
    s3_prefix = event.get('s3_prefix', '')
    dashboard_name = event.get('dashboard_name', '')
    start_after = event.get('start_after', '')

    results = {
        's3_deleted': 0,
        'dashboard_deleted': False,
        'errors': []
    }

    # Delete CloudWatch dashboard first (quick single API call)
    if dashboard_name:
        try:
            cloudwatch_client = boto3.client('cloudwatch')
            print(f"Deleting CloudWatch dashboard: {dashboard_name}")
            cloudwatch_client.delete_dashboards(DashboardNames=[dashboard_name])
            results['dashboard_deleted'] = True
            print(f"Deleted dashboard: {dashboard_name}")
        except cloudwatch_client.exceptions.ResourceNotFoundException:
            print(f"Dashboard not found: {dashboard_name}")
        except Exception as e:
            error_msg = f"Error deleting dashboard: {str(e)}"
            print(error_msg)
            results['errors'].append(error_msg)

    # Delete S3 data if prefix provided
    if bucket and s3_prefix:
        try:
            s3_client = boto3.client('s3')
            print(f"Deleting S3 data: s3://{bucket}/{s3_prefix}")

            list_params = {'Bucket': bucket, 'Prefix': s3_prefix}
            if start_after:
                list_params['StartAfter'] = start_after

            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(**list_params)

            deleted_count = 0
            last_key = ''
            for page in pages:
                if 'Contents' in page:
                    objects_to_delete = [{'Key': obj['Key']} for obj in page['Contents']]
                    if objects_to_delete:
                        last_key = objects_to_delete[-1]['Key']
                        s3_client.delete_objects(
                            Bucket=bucket,
                            Delete={'Objects': objects_to_delete}
                        )
                        deleted_count += len(objects_to_delete)
                        print(f"Deleted {len(objects_to_delete)} objects (total: {deleted_count})")

                    # Re-invoke if less than 30 seconds remaining
                    if context.get_remaining_time_in_millis() < 30_000:
                        print(f"Approaching timeout after {deleted_count} objects, re-invoking to continue")
                        boto3.client('lambda').invoke(
                            FunctionName=context.function_name,
                            InvocationType='Event',
                            Payload=json.dumps({
                                'bucket': bucket,
                                's3_prefix': s3_prefix,
                                'start_after': last_key,
                                'skip_wait': True
                            })
                        )
                        results['s3_deleted'] = deleted_count
                        results['continued'] = True
                        print(f"Background deletion continued: {json.dumps(results)}")
                        return results

            results['s3_deleted'] = deleted_count
            print(f"Completed S3 deletion: {deleted_count} objects")
        except Exception as e:
            error_msg = f"Error deleting S3 data: {str(e)}"
            print(error_msg)
            results['errors'].append(error_msg)

    print(f"Background deletion completed: {json.dumps(results)}")
    return results
