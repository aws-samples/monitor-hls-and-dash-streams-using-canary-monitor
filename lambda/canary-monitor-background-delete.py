import boto3
import json
import time

def lambda_handler(event, context):
    """
    Background Lambda for deleting S3 data and CloudWatch dashboards.
    Invoked asynchronously by delete endpoint/endpoints Lambdas.
    Timeout: 15 minutes
    """
    print(f"Event: {json.dumps(event)}")
    
    # Wait 15 seconds before deleting to allow application to stop writing
    print("Waiting 15 seconds before deletion...")
    time.sleep(15)
    print("Starting deletion process")
    
    bucket = event.get('bucket', '')
    s3_prefix = event.get('s3_prefix', '')
    dashboard_name = event.get('dashboard_name', '')
    
    results = {
        's3_deleted': 0,
        'dashboard_deleted': False,
        'errors': []
    }
    
    # Delete S3 data if prefix provided
    if bucket and s3_prefix:
        try:
            s3_client = boto3.client('s3')
            print(f"Deleting S3 data: s3://{bucket}/{s3_prefix}")
            
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket, Prefix=s3_prefix)
            
            deleted_count = 0
            for page in pages:
                if 'Contents' in page:
                    objects_to_delete = [{'Key': obj['Key']} for obj in page['Contents']]
                    if objects_to_delete:
                        s3_client.delete_objects(
                            Bucket=bucket,
                            Delete={'Objects': objects_to_delete}
                        )
                        deleted_count += len(objects_to_delete)
                        print(f"Deleted {len(objects_to_delete)} objects (total: {deleted_count})")
            
            results['s3_deleted'] = deleted_count
            print(f"Completed S3 deletion: {deleted_count} objects")
        except Exception as e:
            error_msg = f"Error deleting S3 data: {str(e)}"
            print(f"{error_msg}")
            results['errors'].append(error_msg)
    
    # Delete CloudWatch dashboard if name provided
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
            print(f"{error_msg}")
            results['errors'].append(error_msg)
    
    print(f"Background deletion completed: {json.dumps(results)}")
    return results
