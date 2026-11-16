"""Private object storage. No bucket URL or signed bearer URLs are exposed."""
from pathlib import Path
import mimetypes
import shutil
import boto3
from botocore.exceptions import ClientError
from . import config
class LocalStorage:
    def path(self, key):
        root = Path(config.STORAGE_PATH).resolve()
        result = (root / key).resolve()
        if root not in result.parents:
            raise ValueError('Invalid object key')
        return result
    def ensure(self):
        Path(config.STORAGE_PATH).mkdir(parents=True, exist_ok=True)
    def put_file(self, key, path, content_type='application/octet-stream'):
        destination = self.path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
    def download(self, key, path):
        shutil.copyfile(self.path(key), path)
    def size(self, key):
        return self.path(key).stat().st_size
    def read(self, key, start=None, end=None):
        path = self.path(key)
        total = path.stat().st_size
        body = open(path, 'rb')
        body.seek(start or 0)
        return {'body': body, 'length': (end if end is not None else total-1) - (start or 0)+1, 'total': total, 'content_type': mimetypes.guess_type(key)[0] or 'application/octet-stream'}
    def delete(self, key):
        self.path(key).unlink(missing_ok=True)
class S3Storage:
    def __init__(self):
        self.client = boto3.client('s3', endpoint_url=config.S3_ENDPOINT, aws_access_key_id=config.S3_ACCESS_KEY, aws_secret_access_key=config.S3_SECRET_KEY, region_name='us-east-1')
    def ensure(self):
        try:
            self.client.head_bucket(Bucket=config.S3_BUCKET)
        except ClientError as error:
            if error.response['Error']['Code'] in ('404','NoSuchBucket'):
                self.client.create_bucket(Bucket=config.S3_BUCKET)
            else:
                raise
    def put_file(self,key,path,content_type='application/octet-stream'):
        self.client.upload_file(str(path),config.S3_BUCKET,key,ExtraArgs={'ContentType':content_type})
    def download(self,key,path):
        self.client.download_file(config.S3_BUCKET,key,str(path))
    def size(self,key):
        return self.client.head_object(Bucket=config.S3_BUCKET,Key=key)['ContentLength']
    def read(self,key,start=None,end=None):
        args = {'Bucket':config.S3_BUCKET,'Key':key}
        if start is not None:
            args['Range'] = f'bytes={start}-{end if end is not None else ""}'
        response = self.client.get_object(**args)
        return {'body':response['Body'],'length':response['ContentLength'],'content_type':response.get('ContentType','application/octet-stream')}
    def delete(self,key):
        self.client.delete_object(Bucket=config.S3_BUCKET,Key=key)
def get_storage():
    return LocalStorage() if config.STORAGE_BACKEND == 'local' else S3Storage()
